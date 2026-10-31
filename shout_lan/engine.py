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

from shout_lan import arq, calibration, config, frame as fr, mac
from shout_lan.crc import flip_bits
from shout_lan.modem import Modem
from shout_lan.receiver import FrameReceiver
from shout_lan.security import SecurityError
from shout_lan.stats import Stats

OWN_ECHO_GUARD = 0.3       # seconds after our own transmission whose band energy CSMA ignores
# Half-duplex handling: the microphone is NEVER switched off (a reply that starts while
# our speaker is still finishing must not lose its preamble - found in silent tests).
# Instead: our own frames are ignored by source address, and a corrupted frame whose
# preamble was detected while we were transmitting is treated as our own echo.


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
        self.sr_receivers = {}                  # neighbour id -> SelectiveRepeatReceiver
        self.reassemblers = {}                  # origin id   -> Reassembler
        self.nodes = {}                         # neighbour id -> {"last_heard": t, "score": s}
        self.routes = {}                        # NETWORK LAYER routing table: final dst -> next hop
        self.nodes_lock = threading.Lock()
        ack_air = self.modem.airtime(fr.OVERHEAD * 8)
        self.timer = arq.RetransmissionTimer(initial=ack_air + 2.5, minimum=ack_air + 0.8)
        self.sw_sender = arq.StopAndWaitSender(self)
        self.sr_sender = arq.SelectiveRepeatSender(self)
        self.protocol = "sw"                    # "sw" Stop-and-Wait or "sr" Selective Repeat
        self.mac_mode = mac.CSMA                # medium access: csma / aloha / none
        self.carrier = mac.CarrierSense()
        self.msg_ids = itertools.count(1)
        self.ping_ids = itertools.count(1)

        # error injection switches (for demonstrations; every use is logged as "injected")
        self.inject = {"corrupt_next_data": False, "drop_next_ack": False,
                       "drop_frame": 0, "drop_next_sack": False}
        self.inject_lock = threading.Lock()

        self.listeners = []
        self.listeners_lock = threading.Lock()
        self.transmitting = False
        self.tx_started = 0.0                   # time our current/last transmission started
        self.tx_ended = 0.0                     # time our last transmission ended
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
        spk = getattr(self.channel, "speaker", None) or getattr(getattr(self.channel, "hub", None), "speaker", None)
        if spk is not None and spk.warning:
            self.emit("error", text=spk.warning)

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
        if wait and frame.ftype not in (fr.ACK, fr.SACK, fr.PONG):
            self._carrier_sense(frame)        # MAC: may we use the channel now?
        if frame.ftype in fr.DATA_TYPES and self._take_injection("corrupt_next_data"):
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
                self.tx_started = time.time()
                self.transmitting = True
                try:
                    self.channel.transmit(item["samples"])
                except Exception as exc:
                    self.emit("error", text=f"Transmission failed: {exc}")
                finally:
                    self.transmitting = False
                    self.tx_ended = time.time()
                item["done"].set()
                continue
            frame, raw = item["frame"], item["raw"]
            samples = self.modem.modulate_bytes(raw)
            airtime = len(samples) / config.SAMPLE_RATE
            self.emit("tx_start", seq=frame.seq, type=frame.type_name, airtime=round(airtime, 2))
            self.tx_started = time.time()
            self.transmitting = True
            started = time.time()
            error = None
            try:
                self.channel.transmit(samples)
            except Exception as exc:                 # e.g. speaker unplugged
                error = str(exc)
            finally:
                self.transmitting = False
                self.tx_ended = time.time()
            if error:
                self.emit("error", text=f"Transmission failed: {error}")
                item["done"].set()
                continue
            self.stats.add("frames_tx")
            if frame.ftype in fr.DATA_TYPES:
                self.stats.add("data_frames_tx")
            if frame.ftype in (fr.ACK, fr.SACK):
                self.stats.add("acks_tx")
            info = frame.describe()
            self.emit("frame_tx", frame=info, raw=raw.hex(" "), bytes=len(raw),
                      airtime=round(airtime, 2), duration=round(time.time() - started, 2),
                      retry=item["retry"], msg_id=item["msg_id"], injected=item["injected"],
                      lost_in_simulation=getattr(self.channel, "last_lost", False),
                      mic_overflows=int(getattr(self.channel, "overflows", 0) or 0),
                      speaker_underflows=int(getattr(self.channel, "underflows", 0) or 0),
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
            continue_sleep = len(audio) == 0
            own_sound = self.transmitting or time.time() < self.tx_ended + OWN_ECHO_GUARD * self.time_scale
            if len(audio) and not own_sound:
                # carrier sense must measure OTHER nodes, not our own speaker
                self.carrier.add(calibration.band_energy(
                    audio, (self.modem.freq0, self.modem.freq1, 1500, 3500)))
            for result in self.receiver.feed(audio):
                try:
                    self._handle(result)
                except Exception as exc:
                    self.emit("error", text=f"Error handling frame: {exc}")
            if continue_sleep:
                time.sleep(0.03)

    def _own_echo(self, result):
        """True if this (corrupted) frame started while our own speaker was playing."""
        half_duplex = self.channel.kind == "acoustic" and getattr(self.channel, "half_duplex", True)
        t = result.get("detected_at", 0.0)
        return half_duplex and (self.transmitting or self.tx_started <= t <= self.tx_ended + 0.05)

    def _handle(self, result):
        if not result["ok"] and self._own_echo(result):
            return                                    # our own transmission, heard by our mic
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
        elif f.ftype == fr.SRDATA:
            self._on_sr_data(f)
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
        reasm = self.reassemblers.setdefault((f.origin, f.final), fr.Reassembler())
        message = reasm.add(f.payload, f.flags)
        if message is not None:
            self._message_complete(f, message)

    # ------------------------------------------------------------ network layer
    def _message_complete(self, last_frame, data):
        """
        A whole message has arrived over one hop. NETWORK LAYER decision:
          final destination == this node -> deliver it to the application
          otherwise                      -> forward it (store-and-forward relay)
        """
        f = last_frame
        if f.final in (self.node_id, fr.BROADCAST):
            self._deliver(f.origin, data, f.flags, ttl=f.ttl)
        else:
            self._forward(f.origin, f.final, f.ttl, data, f.flags)

    def _forward(self, origin, final, ttl, data, flags):
        """
        Relay a message towards `final`. TTL (time to live) is decremented at
        every hop; at 0 the message is dropped, so a routing loop cannot make a
        message circle forever (same idea as the IP TTL field).
        Encrypted messages are forwarded untouched - the relay never decrypts.
        """
        if ttl <= 1:
            self.stats.add("ttl_expired")
            self.emit("ttl_expired", origin=origin, final=final,
                      text=f"Message from node {origin} to node {final} DROPPED: TTL expired")
            return
        next_hop = self.routes.get(final, final)
        self.stats.add("forwarded")
        self.emit("forwarding", origin=origin, final=final, next_hop=next_hop, ttl=ttl - 1,
                  text=f"Relaying message from node {origin} to node {final} via next hop "
                       f"{next_hop} (TTL {ttl} -> {ttl - 1})")
        self.outbox.put({"kind": "forward", "msg_id": next(self.msg_ids), "dst": final,
                         "data": data, "origin": origin, "ttl": ttl - 1,
                         "flags": flags & fr.F_ENC})

    def set_route(self, dst, via):
        """Static routing table entry: reach node `dst` through neighbour `via` (None = remove)."""
        dst = int(dst)
        if via in (None, "", dst):
            self.routes.pop(dst, None)
            self.emit("setting", name="route", value=[dst, None],
                      text=f"Route to node {dst} removed (send directly)")
        else:
            self.routes[dst] = int(via)
            self.emit("setting", name="route", value=[dst, int(via)],
                      text=f"Route added: node {dst} via node {via}")

    def _deliver(self, origin, data, flags, ttl=fr.DEFAULT_TTL):
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
                  encrypted=encrypted, hops=fr.DEFAULT_TTL - ttl + 1,
                  text=f"Message from node {origin} delivered"
                       + (" (AES-GCM verified + decrypted)" if encrypted else "") + f": {text!r}")

    def _on_sr_data(self, f):
        rcv = self.sr_receivers.setdefault(f.src, arq.SelectiveRepeatReceiver())
        dups = rcv.duplicates
        ready = rcv.accept(f)
        if rcv.duplicates > dups:
            self.stats.add("duplicates")
            self.emit("duplicate", seq=f.seq, src=f.src,
                      text=f"Duplicate seq {f.seq} from node {f.src}: reported in SACK, not delivered twice")
        elif not ready and rcv.rcv_base is not None:
            self.emit("sr_buffered", seq=f.seq, waiting_for=rcv.rcv_base,
                      text=f"Seq {f.seq} buffered out of order (waiting for seq {rcv.rcv_base})")
        for g in ready:
            reasm = self.reassemblers.setdefault((g.origin, g.final), fr.Reassembler())
            message = reasm.add(g.payload, g.flags)
            if message is not None:
                self._message_complete(g, message)
        if f.flags & fr.F_POLL:
            ref, bitmap, cumulative = rcv.sack()
            if self._take_injection("drop_next_sack"):
                self.stats.add("injected_events")
                self.emit("sack_dropped", text="SACK NOT sent (injected SACK loss)")
                return
            self.transmit(fr.Frame(fr.SACK, self.node_id, f.src, seq=ref, payload=bytes([bitmap]),
                                   flags=fr.F_FIRST if cumulative else 0), wait=False, priority=0)
            got = [(ref + i) % 256 for i in range(8) if bitmap >> i & 1]
            self.emit("sack_tx", ref=ref, bitmap=bitmap,
                      text=f"SACK to node {f.src}: everything before seq {ref} delivered"
                           + (f", also holding seq {got}" if got else ""))

    def wait_for_sack(self, peer, timeout):
        """Wait up to `timeout` seconds for a SACK from `peer`."""
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                f = self.ack_queue.get(timeout=left)
            except queue.Empty:
                return None
            if f.ftype == fr.SACK and f.src == peer:
                return f

    # ------------------------------------------------------------ MAC sublayer
    def channel_busy(self):
        """Carrier sense: (busy?, reason). Uses only real measurements of this node's channel."""
        if self.receiver.first_bit is not None:
            return True, "a frame is being received"
        if self.channel.kind == "acoustic":
            busy, ratio = self.carrier.busy()
            if busy:
                return True, f"sound in our band {10 * np.log10(ratio):.0f} dB above the noise floor"
            return False, None
        level = self.channel.level()
        return (level > 0.05, "simulated channel in use") if level > 0.05 else (False, None)

    def _carrier_sense(self, frame):
        """CSMA (non-persistent): listen; if busy, wait a random time and listen again."""
        if self.mac_mode != mac.CSMA:
            return
        for attempt in range(mac.CSMA_MAX_DEFERS):
            busy, reason = self.channel_busy()
            if not busy:
                if attempt:
                    self.emit("channel_idle", text="Channel idle - transmitting now")
                return
            wait = mac.csma_backoff()
            self.stats.add("channel_busy_deferrals")
            self.emit("channel_busy", reason=reason, wait=round(wait, 2),
                      text=f"CSMA: channel BUSY ({reason}) - deferring {frame.type_name} "
                           f"seq {frame.seq} for {wait:.1f} s")
            time.sleep(wait * self.time_scale)
        self.emit("channel_busy", reason="gave up",
                  text=f"CSMA: channel still busy after {mac.CSMA_MAX_DEFERS} checks - "
                       f"transmitting anyway (a collision is possible)")

    def before_retransmit(self, attempt):
        """ALOHA: after a missing ACK wait a random backoff before retransmitting."""
        if self.mac_mode != mac.ALOHA:
            return
        frame_time = self.modem.airtime((fr.OVERHEAD + fr.MAX_PAYLOAD) * 8)
        wait = mac.aloha_backoff(attempt, frame_time)
        self.stats.add("backoffs")
        self.emit("backoff", wait=round(wait, 2), attempt=attempt,
                  text=f"ALOHA: random backoff {wait:.1f} s before retransmission "
                       f"(attempt {attempt}, up to {2 ** min(attempt, 4) - 1} frame times)")
        time.sleep(wait * self.time_scale)

    def set_protocol(self, name):
        if name not in ("sw", "sr"):
            raise ValueError("protocol must be 'sw' or 'sr'")
        self.protocol = name
        self.emit("setting", name="protocol", value=name,
                  text=f"Reliability protocol set to {self._sender().name}")

    def set_mac(self, mode):
        if mode not in mac.NAMES:
            raise ValueError(f"MAC must be one of {list(mac.NAMES)}")
        self.mac_mode = mode
        self.emit("setting", name="mac", value=mode, text=f"Medium access set to {mac.NAMES[mode]}")

    def _sender(self):
        return self.sr_sender if self.protocol == "sr" else self.sw_sender

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
                  via=self.routes.get(dst, dst),
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
                if job["kind"] in ("message", "forward"):
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
        relayed = job["kind"] == "forward"
        origin = job.get("origin", self.node_id)
        ttl = job.get("ttl", fr.DEFAULT_TTL)
        base_flags = job.get("flags", 0)
        link_dst = self.routes.get(dst, dst)      # next hop from the routing table
        if job.get("encrypt"):
            plain_len = len(data)
            data = self.security.encrypt(data, self.node_id, dst)
            base_flags = fr.F_ENC
            self.emit("encrypted", msg_id=msg_id, plain=plain_len, sealed=len(data),
                      text=f"Message #{msg_id} encrypted with AES-256-GCM: {plain_len} -> "
                           f"{len(data)} bytes (+{len(data) - plain_len} nonce+tag)")
        pieces = fr.fragment(data)
        arq.drain(self.ack_queue)
        via = f" via node {link_dst}" if link_dst != dst else ""
        if not relayed:
            self.stats.add("messages_sent")
            self.emit("message_status", msg_id=msg_id, dst=dst, status="sending", frames=len(pieces),
                      via=link_dst, text=f"Sending message #{msg_id}{via} with {self._sender().name} "
                                         f"+ {mac.NAMES[self.mac_mode]}")
        started = time.time()
        ok = self._sender().send(msg_id, link_dst, pieces, base_flags=base_flags, final=dst,
                                 origin=origin, ttl=ttl)
        if relayed:
            self.emit("forwarded", ok=ok, final=dst, next_hop=link_dst,
                      text=f"Relay to node {dst} via {link_dst}: "
                           + ("next hop acknowledged every frame" if ok else "FAILED (no ACK)"))
            return
        elapsed = time.time() - started
        self.stats.add_busy_time(elapsed)
        if ok:
            self.stats.add("messages_delivered")
            self.stats.add("payload_bytes_acked", len(data))
            self.emit("message_status", msg_id=msg_id, dst=dst, status="delivered",
                      seconds=round(elapsed, 1),
                      text=f"Message #{msg_id} DELIVERED to node {dst}{via} in {elapsed:.1f} s "
                           + ("(next hop acknowledged every frame)" if via else "(every frame acknowledged)"))
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
                "transmitting": self.transmitting, "protocol": self._sender().name,
                "protocol_id": self.protocol, "mac": self.mac_mode, "mac_name": mac.NAMES[self.mac_mode],
                "channel_busy": self.channel_busy()[0],
                "rto": round(self.timer.timeout(), 2), "inject": dict(self.inject),
                "nodes": self.node_table(), "stats": self.stats.snapshot(),
                "freq0": self.modem.freq0, "freq1": self.modem.freq1,
                "bit_time": self.modem.bit_time, "volume": self.modem.volume,
                "mic_level": round(float(self.channel.level()), 4),
                "mic_overflows": int(getattr(self.channel, "overflows", 0) or 0),
                "noise_floor": getattr(self, "noise_floor", None),
                "encryption_available": getattr(self, "security", None) is not None,
                "speaker_mode": self._speaker_mode(),
                "routes": {str(k): v for k, v in self.routes.items()}}

    def _speaker_mode(self):
        spk = getattr(self.channel, "speaker", None) or getattr(getattr(self.channel, "hub", None), "speaker", None)
        return spk.mode if spk is not None else "simulated"
