"""
engine.py - one SHOUT LAN node: all the network layers working together.

Threads (producer -> queue -> consumer):

    rx thread      channel audio -> FrameReceiver -> handle_frame()
                   (sends ACKs / PONGs by putting frames in tx_queue)
    tx thread      tx_queue -> modem -> channel.transmit()   (speaker)
    sender thread  outbox (messages, pings, hellos) -> ARQ protocol
                   -> transmit() + wait_for_ack()

Everything that happens is reported with emit(event, ...). The local TCP
control server forwards these events to the UI (Milestone 4).
"""

import itertools
import queue
import random
import threading
import time

import numpy as np

from shout_lan import arq, calibration, config, frame as fr
from shout_lan.crc import flip_bits
from shout_lan.modem import Modem
from shout_lan.receiver import FrameReceiver
from shout_lan.security import SecurityError
from shout_lan.stats import Stats

MUTE_AFTER_TX = 0.15       # seconds the acoustic receiver stays deaf after we stop talking


class Engine:
    def __init__(self, node_id, channel, modem=None, time_scale=1.0, security=None):
        if not 1 <= node_id <= 254:
            raise ValueError("node id must be 1..254")
        self.node_id = node_id
        self.channel = channel
        self.modem = modem or Modem()
        self.time_scale = time_scale            # < 1 only for fast automated tests
        self.security = security                # shout_lan.security.Security, or None
        self.receiver = FrameReceiver(self.modem)
        self.stats = Stats("Measured - acoustic" if channel.kind == "acoustic"
                           else "Measured - SIMULATED channel")

        # queues between threads
        self.tx_queue = queue.PriorityQueue()   # (priority, order, item) - ACKs first
        self.outbox = queue.Queue()             # work for the sender thread
        self.ack_queue = queue.Queue()          # ACK/SACK frames for the waiting sender
        self.pong_queue = queue.Queue()
        self._order = itertools.count()

        # protocol state
        self.seq = random.randint(0, 255)       # random start, like TCP's initial sequence number
        self.seq_lock = threading.Lock()
        self.sw_receivers = {}                  # neighbour id -> StopAndWaitReceiver
        self.reassemblers = {}                  # origin id   -> Reassembler
        self.nodes = {}                         # neighbour id -> {"last_heard": t, "score": s}
        self.nodes_lock = threading.Lock()
        ack_air = self.modem.airtime(fr.OVERHEAD * 8)
        self.timer = arq.RetransmissionTimer(initial=ack_air + 2.5, minimum=ack_air + 0.8)
        self.sw_sender = arq.StopAndWaitSender(self)
        self.msg_ids = itertools.count(1)
        self.ping_ids = itertools.count(1)

        # error injection switches (for demonstrations; every use is logged as "injected")
        self.inject = {"corrupt_next_data": False, "drop_next_ack": False}
        self.inject_lock = threading.Lock()

        self.listeners = []
        self.listeners_lock = threading.Lock()
        self.transmitting = False
        self.mute_until = 0.0
        self.running = False
        self.threads = []
        self.capture = None                     # list while calibration records raw audio

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.channel.start()
        self.running = True
        for target, name in ((self._rx_loop, "rx"), (self._tx_loop, "tx"),
                             (self._sender_loop, "sender")):
            t = threading.Thread(target=target, name=f"node{self.node_id}-{name}", daemon=True)
            t.start()
            self.threads.append(t)
        self.emit("engine_started", channel=self.channel.kind, label=self.channel.label,
                  text=f"Node {self.node_id} started on the {self.channel.label}")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.outbox.put(None)
        self.tx_queue.put((-1, -1, None))
        for t in self.threads:
            t.join(timeout=5)
        self.threads = []
        self.channel.stop()
        self.emit("engine_stopped", text=f"Node {self.node_id} stopped")

    # ------------------------------------------------------------ events
    def add_listener(self, fn):
        with self.listeners_lock:
            self.listeners.append(fn)

    def remove_listener(self, fn):
        with self.listeners_lock:
            if fn in self.listeners:
                self.listeners.remove(fn)

    def emit(self, event, **fields):
        """Report something that really happened. Listeners must be quick (they only queue)."""
        data = {"event": event, "time": round(time.time(), 3), "node": self.node_id}
        data.update(fields)
        with self.listeners_lock:
            listeners = list(self.listeners)
        for fn in listeners:
            try:
                fn(data)
            except Exception:
                pass

    # ------------------------------------------------------------ helpers
    def next_seq(self):
        with self.seq_lock:
            self.seq = (self.seq + 1) % 256
            return self.seq

    def set_injection(self, name, value=True):
        with self.inject_lock:
            if name not in self.inject:
                raise ValueError(f"unknown injection {name}")
            self.inject[name] = value
        self.emit("injection_set", name=name, value=value,
                  text=f"Error injection '{name}' {'ARMED' if value else 'cleared'}")

    def _take_injection(self, name):
        """Returns True once if the injection is armed, then disarms it."""
        with self.inject_lock:
            armed = self.inject.get(name)
            if armed:
                self.inject[name] = False
            return armed

    def _heard(self, node, score):
        with self.nodes_lock:
            self.nodes[node] = {"last_heard": time.time(), "score": round(score, 2)}

    def node_table(self):
        with self.nodes_lock:
            return {n: dict(v) for n, v in self.nodes.items()}

    # ------------------------------------------------------------ sending frames
    def transmit(self, frame, retry=0, msg_id=None, wait=True, priority=1):
        """
        Queue a frame for the speaker. With wait=True this returns only when the
        sound has finished playing (the ARQ timer must start AFTER sending).
        """
        raw = fr.build(frame)
        injected = None
        if frame.ftype == fr.DATA and self._take_injection("corrupt_next_data"):
            raw = flip_bits(raw, [len(raw) * 8 - 30])      # one bit inside the payload/CRC area
            injected = "corrupted (injected bit error)"
            self.stats.add("injected_events")
        done = threading.Event()
        item = {"frame": frame, "raw": raw, "retry": retry, "msg_id": msg_id,
                "injected": injected, "done": done}
        self.tx_queue.put((priority, next(self._order), item))
        if wait:
            limit = self.modem.airtime(len(raw) * 8) * 3 + 10
            done.wait(timeout=limit)

    def _tx_loop(self):
        while self.running:
            _, _, item = self.tx_queue.get()
            if item is None:
                break
            if "samples" in item:                      # calibration sweep (not a frame)
                self.transmitting = True
                try:
                    self.channel.transmit(item["samples"])
                except Exception as exc:
                    self.emit("error", text=f"Transmission failed: {exc}")
                finally:
                    self.transmitting = False
                    self.mute_until = time.time() + MUTE_AFTER_TX
                item["done"].set()
                continue
            frame, raw = item["frame"], item["raw"]
            samples = self.modem.modulate_bytes(raw)
            airtime = len(samples) / config.SAMPLE_RATE
            self.emit("tx_start", seq=frame.seq, type=frame.type_name, airtime=round(airtime, 2))
            self.transmitting = True
            started = time.time()
            error = None
            try:
                self.channel.transmit(samples)
            except Exception as exc:                 # e.g. speaker unplugged
                error = str(exc)
            finally:
                self.transmitting = False
                self.mute_until = time.time() + MUTE_AFTER_TX
            if error:
                self.emit("error", text=f"Transmission failed: {error}")
                item["done"].set()
                continue
            self.stats.add("frames_tx")
            if frame.ftype == fr.DATA:
                self.stats.add("data_frames_tx")
            if frame.ftype in (fr.ACK, fr.SACK):
                self.stats.add("acks_tx")
            info = frame.describe()
            self.emit("frame_tx", frame=info, raw=raw.hex(" "), bytes=len(raw),
                      airtime=round(airtime, 2), duration=round(time.time() - started, 2),
                      retry=item["retry"], msg_id=item["msg_id"], injected=item["injected"],
                      lost_in_simulation=getattr(self.channel, "last_lost", False),
                      text=f"Sent {frame.type_name} seq {frame.seq} to node {frame.dst}"
                           + (f" (retry {item['retry']})" if item["retry"] else "")
                           + (f" - {item['injected']}" if item["injected"] else ""))
            item["done"].set()

    # ------------------------------------------------------------ receiving frames
    def _rx_loop(self):
        while self.running:
            audio = self.channel.read()
            if self.capture is not None:               # calibration is recording raw audio
                if len(audio):
                    self.capture.append(audio)
                else:
                    time.sleep(0.03)
                continue
            half_duplex = self.channel.kind == "acoustic" and getattr(self.channel, "half_duplex", True)
            if half_duplex and (self.transmitting or time.time() < self.mute_until):
                # Half-duplex: we can't listen while our own speaker is talking.
                self.receiver.reset()
                continue_sleep = True
            else:
                continue_sleep = len(audio) == 0
                for result in self.receiver.feed(audio):
                    try:
                        self._handle(result)
                    except Exception as exc:
                        self.emit("error", text=f"Error handling frame: {exc}")
            if continue_sleep:
                time.sleep(0.03)

    def _handle(self, result):
        if not result["ok"]:
            self.stats.add("crc_failures")
            self.emit("frame_rx", ok=False, error=result["error"], raw=result["raw"].hex(" "),
                      score=round(result["score"], 2),
                      text=f"Frame rejected: {result['error']}")
            return
        f = result["frame"]
        if f.src == self.node_id:
            return                                    # our own echo
        self.stats.add("frames_rx_ok")
        self._heard(f.src, result["score"])
        for_me = f.dst in (self.node_id, fr.BROADCAST)
        self.emit("frame_rx", ok=True, frame=f.describe(), raw=result["raw"].hex(" "),
                  score=round(result["score"], 2), confidence=round(result["confidence"], 2),
                  for_me=for_me,
                  text=f"Received {f.type_name} seq {f.seq} from node {f.src} - CRC OK"
                       + ("" if for_me else f" (for node {f.dst}, ignored)"))
        if not for_me:
            return
        if f.ftype in (fr.ACK, fr.SACK):
            self.stats.add("acks_rx")
            self.ack_queue.put(f)
        elif f.ftype == fr.DATA:
            self._on_data(f)
        elif f.ftype == fr.PING:
            self.transmit(fr.Frame(fr.PONG, self.node_id, f.src, seq=f.seq, payload=f.payload),
                          wait=False, priority=0)
        elif f.ftype == fr.PONG:
            self.pong_queue.put(f)
        elif f.ftype == fr.HELLO and f.flags & fr.F_POLL:
            self.transmit(fr.Frame(fr.HELLO, self.node_id, f.src, seq=self.next_seq()),
                          wait=False, priority=0)

    def _send_ack(self, f):
        if self._take_injection("drop_next_ack"):
            self.stats.add("injected_events")
            self.emit("ack_dropped", seq=f.seq,
                      text=f"ACK for seq {f.seq} NOT sent (injected ACK loss)")
            return
        self.transmit(fr.Frame(fr.ACK, self.node_id, f.src, seq=f.seq), wait=False, priority=0)

    def _on_data(self, f):
        rcv = self.sw_receivers.setdefault(f.src, arq.StopAndWaitReceiver())
        is_new = rcv.accept(f.seq)
        self._send_ack(f)                     # ACK new frames AND duplicates
        if not is_new:
            self.stats.add("duplicates")
            self.emit("duplicate", seq=f.seq, src=f.src,
                      text=f"Duplicate seq {f.seq} from node {f.src}: ACKed again, not delivered twice")
            return
        reasm = self.reassemblers.setdefault(f.origin, fr.Reassembler())
        message = reasm.add(f.payload, f.flags)
        if message is not None:
            self._deliver(f.origin, message, f.flags)

    def _deliver(self, origin, data, flags):
        encrypted = bool(flags & fr.F_ENC)
        if encrypted:
            # The data link already verified every frame's CRC; now the security
            # layer verifies the AES-GCM tag. Only verified plaintext is shown.
            if self.security is None:
                self.stats.add("security_rejects")
                self.emit("security_reject", origin=origin, reason="no key",
                          text=f"Encrypted message from node {origin} discarded: "
                               f"this node has no secret.key")
                return
            try:
                data = self.security.decrypt(data, origin, self.node_id)
            except SecurityError as exc:
                self.stats.add("security_rejects")
                self.emit("security_reject", origin=origin, reason=str(exc),
                          text=f"Encrypted message from node {origin} REJECTED: {exc}")
                return
        self.stats.add("messages_received")
        text = data.decode("utf-8", errors="replace")
        self.emit("message_rx", origin=origin, message=text, length=len(data),
                  encrypted=encrypted,
                  text=f"Message from node {origin} delivered"
                       + (" (AES-GCM verified + decrypted)" if encrypted else "") + f": {text!r}")

    def wait_for_ack(self, peer, seq, timeout):
        """Wait up to `timeout` seconds for ACK `seq` from `peer`. Stale ACKs are ignored."""
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                f = self.ack_queue.get(timeout=left)
            except queue.Empty:
                return None
            if f.ftype == fr.ACK and f.src == peer and f.seq == seq:
                return f
            self.emit("stale_ack", seq=f.seq, text=f"Ignored old/unexpected ACK seq {f.seq}")

    # ------------------------------------------------------------ application requests
    def send_message(self, dst, text, encrypt=False):
        """Queue a text message. Returns its message id (progress is reported by events)."""
        if not 1 <= int(dst) <= 254:
            raise ValueError("destination must be a node id 1..254")
        if encrypt and getattr(self, "security", None) is None:
            raise ValueError("encryption is not available (no secret.key loaded)")
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        msg_id = next(self.msg_ids)
        self.outbox.put({"kind": "message", "msg_id": msg_id, "dst": dst, "data": data,
                         "encrypt": encrypt})
        on_air = len(data) + (28 if encrypt else 0)          # AES-GCM adds nonce + tag
        frames = len(fr.fragment(bytes(on_air)))
        self.emit("message_status", msg_id=msg_id, dst=dst, status="queued", frames=frames,
                  message=data.decode("utf-8", errors="replace"), encrypted=bool(encrypt),
                  text=f"Message #{msg_id} to node {dst} queued ({len(data)} bytes"
                       + (f", {on_air} after encryption" if encrypt else "") + f", {frames} frame(s))")
        return msg_id

    def ping(self, dst, count=4):
        self.outbox.put({"kind": "ping", "dst": dst, "count": count})

    def hello(self):
        self.outbox.put({"kind": "hello"})

    def _sender_loop(self):
        while self.running:
            job = self.outbox.get()
            if job is None:
                break
            try:
                if job["kind"] == "message":
                    self._send_message_job(job)
                elif job["kind"] == "ping":
                    self._ping_job(job)
                elif job["kind"] == "calibrate":
                    self._calibrate_job()
                elif job["kind"] == "hello":
                    self.transmit(fr.Frame(fr.HELLO, self.node_id, fr.BROADCAST,
                                           seq=self.next_seq(), flags=fr.F_POLL))
            except Exception as exc:
                self.emit("error", text=f"Sender error: {exc}")

    def _send_message_job(self, job):
        msg_id, dst, data = job["msg_id"], job["dst"], job["data"]
        base_flags = 0
        if job.get("encrypt"):
            plain_len = len(data)
            data = self.security.encrypt(data, self.node_id, dst)
            base_flags = fr.F_ENC
            self.emit("encrypted", msg_id=msg_id, plain=plain_len, sealed=len(data),
                      text=f"Message #{msg_id} encrypted with AES-256-GCM: {plain_len} -> "
                           f"{len(data)} bytes (+{len(data) - plain_len} nonce+tag)")
        pieces = fr.fragment(data)
        arq.drain(self.ack_queue)
        self.stats.add("messages_sent")
        self.emit("message_status", msg_id=msg_id, dst=dst, status="sending", frames=len(pieces),
                  text=f"Sending message #{msg_id} with {self.sw_sender.name}")
        started = time.time()
        ok = self.sw_sender.send(msg_id, dst, pieces, base_flags=base_flags)
        elapsed = time.time() - started
        self.stats.add_busy_time(elapsed)
        if ok:
            self.stats.add("messages_delivered")
            self.stats.add("payload_bytes_acked", len(data))
            self.emit("message_status", msg_id=msg_id, dst=dst, status="delivered",
                      seconds=round(elapsed, 1),
                      text=f"Message #{msg_id} DELIVERED to node {dst} in {elapsed:.1f} s "
                           f"(every frame acknowledged)")
        else:
            self.stats.add("messages_failed")
            self.emit("message_status", msg_id=msg_id, dst=dst, status="failed",
                      seconds=round(elapsed, 1),
                      text=f"Message #{msg_id} FAILED: no ACK after {arq.MAX_RETRIES} retries")

    def _ping_job(self, job):
        """
        Acoustic ping, like the ICMP Pinger lab: send Echo Request (PING),
        wait for Echo Reply (PONG), measure round-trip time, report loss.
        """
        dst, count = job["dst"], job["count"]
        ping_id = next(self.ping_ids) % 256
        air = self.modem.airtime((fr.OVERHEAD + 2) * 8)
        timeout = (2 * air + 3.0) * self.time_scale
        rtts = []
        self.emit("ping_start", dst=dst, count=count,
                  text=f"PING node {dst}: {count} echo requests over the acoustic link")
        for n in range(count):
            arq.drain(self.pong_queue)
            payload = bytes([ping_id, n])
            started = time.time()
            self.stats.add("pings_sent")
            self.transmit(fr.Frame(fr.PING, self.node_id, dst, seq=n, payload=payload))
            reply = None
            deadline = started + air * self.time_scale + timeout
            while reply is None and time.time() < deadline:
                try:
                    f = self.pong_queue.get(timeout=max(0.01, deadline - time.time()))
                except queue.Empty:
                    break
                if f.src == dst and f.payload == payload:
                    reply = f
            if reply is not None:
                rtt = time.time() - started
                rtts.append(rtt)
                self.stats.add("pongs_received")
                self.stats.add_ping_rtt(rtt)
                self.emit("ping_reply", dst=dst, n=n, rtt=round(rtt, 3),
                          text=f"Reply from node {dst}: seq={n} time={rtt * 1000:.0f} ms")
            else:
                self.emit("ping_timeout", dst=dst, n=n, text=f"Request seq={n} timed out")
        loss = 1 - len(rtts) / count
        summary = {"sent": count, "received": len(rtts), "loss": round(loss, 3)}
        if rtts:
            summary.update(min=round(min(rtts), 3), avg=round(sum(rtts) / len(rtts), 3),
                           max=round(max(rtts), 3))
        self.emit("ping_summary", dst=dst, **summary,
                  text=f"Ping statistics for node {dst}: {count} sent, {len(rtts)} received, "
                       f"{loss:.0%} loss" + (f", RTT min/avg/max = {summary['min']:.2f}/"
                                             f"{summary['avg']:.2f}/{summary['max']:.2f} s"
                                             if rtts else ""))

    def calibrate(self):
        self.outbox.put({"kind": "calibrate"})

    def _calibrate_job(self):
        """
        Measure the real channel through this engine's own microphone stream:
        1 s of room noise, then a tone sweep through the speaker.
        Reports the noise floor and the SNR of every test frequency.
        """
        if self.channel.kind != "acoustic":
            self.emit("calibration", ok=False,
                      text="Calibration needs the acoustic channel (it measures real sound)")
            return
        self.emit("calibration_start", text="Calibrating: measuring room noise for 1 s, then playing a tone sweep")
        self.capture = []
        time.sleep(1.0)
        noise = np.concatenate(self.capture) if self.capture else np.zeros(0)
        self.capture = []
        done = threading.Event()
        self.tx_queue.put((1, next(self._order),
                           {"samples": calibration.make_sweep(self.modem.volume), "done": done}))
        done.wait(timeout=30)
        time.sleep(0.4)
        heard = np.concatenate(self.capture) if self.capture else np.zeros(0)
        self.capture = None
        self.receiver.reset()
        if len(noise) == 0 or len(heard) == 0:
            self.emit("calibration", ok=False, text="Calibration failed: no audio from the microphone")
            return
        noise_rms = float(np.sqrt(np.mean(noise * noise)))
        noise_p = calibration.noise_powers(noise)
        snr = calibration.snr_table(calibration.analyse_sweep(heard), noise_p)
        self.noise_floor = noise_rms
        current = [round(snr.get(self.modem.freq0, 0), 1), round(snr.get(self.modem.freq1, 0), 1)]
        self.emit("calibration", ok=True, noise_rms=round(noise_rms, 5),
                  snr_db={str(k): round(v, 1) for k, v in snr.items()},
                  freq0=self.modem.freq0, freq1=self.modem.freq1, current_snr=current,
                  source=self.stats.source,
                  text=f"Calibration done: noise floor RMS {noise_rms:.4f}; detector-bin SNR at "
                       f"{self.modem.freq0} Hz = {current[0]} dB, {self.modem.freq1} Hz = {current[1]} dB")

    def set_volume(self, volume):
        self.modem.volume = max(0.02, min(1.0, float(volume)))
        self.emit("setting", name="volume", value=self.modem.volume,
                  text=f"Transmit volume set to {self.modem.volume:.2f}")

    def set_tones(self, freq0, freq1):
        """Both laptops MUST use the same tones, otherwise they cannot understand each other."""
        freq0, freq1 = int(freq0), int(freq1)
        if not (300 <= freq0 < freq1 <= 8000) or freq1 - freq0 < 300:
            raise ValueError("tones must be 300..8000 Hz, freq0 < freq1, at least 300 Hz apart")
        self.modem.freq0, self.modem.freq1 = freq0, freq1
        self.emit("setting", name="tones", value=[freq0, freq1],
                  text=f"BFSK tones set to {freq0}/{freq1} Hz (set the SAME values on every laptop)")

    def reset_stats(self):
        self.stats.reset()
        self.emit("setting", name="stats", value="reset", text="Statistics reset")

    # ------------------------------------------------------------ status for the UI
    def status(self):
        return {"node_id": self.node_id, "channel": self.channel.kind,
                "channel_label": self.channel.label, "running": self.running,
                "transmitting": self.transmitting, "protocol": self.sw_sender.name,
                "rto": round(self.timer.timeout(), 2), "inject": dict(self.inject),
                "nodes": self.node_table(), "stats": self.stats.snapshot(),
                "freq0": self.modem.freq0, "freq1": self.modem.freq1,
                "bit_time": self.modem.bit_time, "volume": self.modem.volume,
                "mic_level": round(float(self.channel.level()), 4),
                "mic_overflows": int(getattr(self.channel, "overflows", 0) or 0),
                "noise_floor": getattr(self, "noise_floor", None),
                "encryption_available": getattr(self, "security", None) is not None}
