"""
Tests of the browser bridge (shout_lan/web_bridge.py) and the launcher
(shout.py). Engines run on the SIMULATED air or on SILENT fake audio
(tests/fake_audio.py): no sound is played and no audio device is opened.
Every server binds port 0 (a free port chosen by the OS), so these tests
never collide with other test files or other worktrees.
"""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

import shout
from shout_lan.audio_io import AudioError
from shout_lan.channel import (AcousticLoopbackHub, LoopbackAcousticChannel, SimulatedAir,
                               SimulatedChannel)
from shout_lan.engine import Engine
from shout_lan.web_bridge import CLIENT_QUEUE_SIZE, REPLAY_SIZE, ChannelTap, EventHub, WebBridge
from tests.fake_audio import FakeAir, FakeMicrophone, FakeSpeaker, fake_acoustic_channel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCALE = 0.25


# ----------------------------------------------------------------- helpers
def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


def get_json(port, path):
    status, _, body = get(port, path)
    assert status == 200
    return json.loads(body)


def post(port, body, raw=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/cmd", body=body if raw else json.dumps(body),
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data)


class SseReader:
    """A minimal EventSource: raw socket, collects every `data:` JSON object."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                          b"Accept: text/event-stream\r\n\r\n")
        self.events, self.comments = [], []
        self.headers = None
        self.cond = threading.Condition()
        self.closed = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        buf = b""
        while True:
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                break
            buf += data
            if self.headers is None and b"\r\n\r\n" in buf:
                self.headers, buf = buf.split(b"\r\n\r\n", 1)
            if self.headers is None:
                continue
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                for line in block.split(b"\n"):
                    if line.startswith(b"data: "):
                        with self.cond:
                            self.events.append(json.loads(line[6:]))
                            self.cond.notify_all()
                    elif line.startswith(b":"):
                        self.comments.append(line)
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def wait(self, pred, timeout=30):
        end = time.time() + timeout
        with self.cond:
            while True:
                for e in self.events:
                    if pred(e):
                        return e
                left = end - time.time()
                if left <= 0 or self.closed:
                    raise AssertionError(f"event not received; got "
                                         f"{[e['event'] for e in self.events if e['event'] != 'spectrum'][-20:]}")
                self.cond.wait(left)

    def named(self, name):
        with self.cond:
            return [e for e in self.events if e["event"] == name]

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def ev(_event, **kw):
    return lambda e: e.get("event") == _event and all(e.get(k) == v for k, v in kw.items())


def make_sim_bridges(web_dir, spectrum=False):
    air = SimulatedAir(time_scale=0.2, seed=1)
    nodes = []
    for node_id, name in ((1, "A"), (2, "B")):
        tap = ChannelTap(SimulatedChannel(air, name=name))
        engine = Engine(node_id, tap, time_scale=0.2)
        bridge = WebBridge(engine, 0, mode="simulated", peer=3 - node_id, tap=tap,
                           web_dir=str(web_dir), spectrum=spectrum)
        bridge.start()
        engine.start()
        nodes.append((engine, bridge))
    for (_, b), (_, other) in zip(nodes, reversed(nodes)):
        b.other_dashboards = [{"node": other.engine.node_id, "url": other.url}]
    return nodes


@pytest.fixture
def sim(tmp_path):
    nodes = make_sim_bridges(tmp_path / "dist")
    yield nodes
    for engine, bridge in nodes:
        bridge.stop()
        engine.stop()


# ----------------------------------------------------------------- status + commands
def test_status_shape(sim):
    (e1, b1), (e2, b2) = sim
    st = get_json(b1.port, "/api/status")
    for key in ("node_id", "channel", "channel_label", "running", "transmitting", "protocol",
                "nodes", "stats", "freq0", "freq1", "bit_time", "volume", "mic_level",
                "speaker_mode", "shield", "shield_info", "noise", "mode", "peer", "http_port",
                "other_dashboards"):
        assert key in st, key
    for key in ("counters", "delivery_ratio", "frame_loss", "goodput_bps", "rtt"):
        assert key in st["stats"], key
    assert st["node_id"] == 1 and st["running"] is True
    assert st["mode"] == "simulated" and st["channel"] == "simulated"
    assert st["peer"] == 2 and st["http_port"] == b1.port
    assert st["other_dashboards"] == [{"node": 2, "url": b2.url}]
    assert isinstance(st["shield"], bool) and isinstance(st["shield_info"], dict)
    assert set(st["noise"]) >= {"on", "level"}
    json.dumps(st)                                          # fully JSON


def test_command_round_trip(sim):
    (e1, b1), (e2, b2) = sim
    r1, r2 = SseReader(b1.port), SseReader(b2.port)
    try:
        r1.wait(ev("replay_done"))
        r2.wait(ev("replay_done"))
        status, reply = post(b1.port, {"cmd": "send", "dst": 2, "text": "OVER HTTP"})
        assert status == 200 and reply == {"ok": True}
        r1.wait(ev("message_status", status="queued", message="OVER HTTP"))
        r1.wait(ev("message_status", status="delivered"), timeout=60)
        got = r2.wait(ev("message_rx"), timeout=60)
        assert got["message"] == "OVER HTTP" and got["origin"] == 1
        assert got["simulated"] is True and got["text"].startswith("[SIMULATED]")
        # same handler as TCP: its validation errors come back as ok:false
        assert post(b1.port, {"cmd": "send", "dst": 999, "text": "x"}) == \
            (200, {"ok": False, "error": "destination must be a node id 1..254"})
        status, reply = post(b1.port, {"cmd": "nope"})
        assert status == 200 and reply["ok"] is False and "unknown command" in reply["error"]
        status, reply = post(b1.port, {"cmd": "set_volume", "value": 0.3})
        assert reply == {"ok": True} and e1.modem.volume == 0.3
        status, reply = post(b1.port, {"cmd": "status"})
        assert reply["ok"] and reply["status"]["node_id"] == 1
        assert post(b1.port, "{not json", raw=True)[0] == 400
        assert post(b1.port, [1, 2])[1]["ok"] is False
    finally:
        r1.close()
        r2.close()


def test_shield_and_noise_commands_fall_back_cleanly(sim):
    (e1, b1), _ = sim
    for cmd in ({"cmd": "set_shield", "value": True}, {"cmd": "set_noise", "on": True, "level": 0.5}):
        status, reply = post(b1.port, cmd)
        assert status == 200
        method = "set_shield" if cmd["cmd"] == "set_shield" else "set_noise"
        if callable(getattr(e1, method, None)):
            assert reply["ok"] is True                    # link engineer's engine is merged
        else:
            assert reply["ok"] is False and "not available in this build" in reply["error"]


# ----------------------------------------------------------------- SSE
def test_sse_replay_then_live_order(sim):
    (e1, b1), _ = sim
    for i in range(REPLAY_SIZE + 50):
        e1.emit("setting", name="t", value=i, text=f"pre {i}")
    b1.hub.publish({"event": "spectrum", "node": 1, "bins": []}, replay=False)
    r = SseReader(b1.port)
    try:
        r.wait(ev("replay_done"))
        e1.emit("setting", name="t", value="live", text="live one")
        r.wait(ev("setting", value="live"))
        names = [e["event"] for e in r.events if e["event"] != "spectrum"]
        k = names.index("replay_done")
        replay, live = r.events[:k], r.events[k + 1:]
        assert len(replay) == REPLAY_SIZE and all(e["replay"] is True for e in replay)
        values = [e["value"] for e in replay if e["event"] == "setting" and e.get("name") == "t"]
        assert values == list(range(50, REPLAY_SIZE + 50))        # oldest dropped, order kept
        assert not any(e["event"] == "spectrum" for e in replay)
        assert [e for e in live if e["event"] == "setting"][-1]["value"] == "live"
        assert all("replay" not in e for e in live)
        assert b"text/event-stream" in r.headers
    finally:
        r.close()


def test_two_sse_clients_at_once(sim):
    (e1, b1), _ = sim
    a, b = SseReader(b1.port), SseReader(b1.port)
    try:
        a.wait(ev("replay_done"))
        b.wait(ev("replay_done"))
        assert b1.hub.client_count() == 2
        for i in range(20):
            e1.emit("setting", name="both", value=i)
        for r in (a, b):
            r.wait(ev("setting", name="both", value=19))
            assert [e["value"] for e in r.named("setting") if e["name"] == "both"] == list(range(20))
    finally:
        a.close()
        b.close()


def test_client_disconnect_mid_stream_does_not_disturb_others(sim):
    (e1, b1), _ = sim
    keeper, leaver = SseReader(b1.port), SseReader(b1.port)
    keeper.wait(ev("replay_done"))
    leaver.wait(ev("replay_done"))
    e1.emit("setting", name="x", value=1)
    leaver.wait(ev("setting", name="x", value=1))
    leaver.close()                                          # browser tab closed mid-stream
    try:
        end = time.time() + 10
        i = 2
        while b1.hub.client_count() > 1 and time.time() < end:
            e1.emit("setting", name="x", value=i)           # writes to the dead socket fail -> dropped
            i += 1
            time.sleep(0.05)
        assert b1.hub.client_count() == 1
        e1.emit("setting", name="x", value="after")
        keeper.wait(ev("setting", name="x", value="after"))
        assert get_json(b1.port, "/api/status")["running"] is True
    finally:
        keeper.close()


def test_slow_client_is_dropped_without_blocking_the_engine():
    hub = EventHub()
    stuck = hub.subscribe()                 # never read: like a frozen browser tab
    fine = hub.subscribe()
    t0 = time.time()
    for i in range(CLIENT_QUEUE_SIZE + 10):
        hub.publish({"event": "setting", "value": i})
        if i % 500 == 0:                    # the healthy client keeps reading
            while not fine.queue.empty():
                fine.queue.get_nowait()
    assert time.time() - t0 < 5             # publish never waited
    assert stuck.alive is False and stuck not in hub.clients
    assert fine.alive is True and fine in hub.clients
    assert hub.dropped_clients == 1


def test_clear_forgets_replay_and_resets_stats(sim):
    (e1, b1), _ = sim
    e1.stats.add("frames_tx")
    e1.emit("setting", name="old", value=1, text="before clear")
    status, reply = post(b1.port, {"cmd": "clear"})
    assert reply == {"ok": True}
    assert e1.stats.snapshot()["counters"]["frames_tx"] == 0
    r = SseReader(b1.port)
    try:
        r.wait(ev("replay_done"))
        replay = [e for e in r.events if e.get("replay")]
        assert not any(e.get("name") == "old" for e in replay)
        assert not any(e["event"] == "engine_started" for e in replay)
        assert [e["event"] for e in replay] == ["setting"]           # only "Statistics reset"
    finally:
        r.close()


# ----------------------------------------------------------------- static files
def test_placeholder_page_when_web_app_not_built(sim):
    (e1, b1), _ = sim
    status, ctype, body = get(b1.port, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b"has not been built" in body and b"/api/events" in body
    status, _, body2 = get(b1.port, "/some/route")
    assert status == 200 and b"has not been built" in body2


def test_static_files_and_index_fallback(sim, tmp_path):
    (e1, b1), _ = sim
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>APP</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "assets" / "app.css").write_text("body{}")
    (dist / "logo.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("SECRET")
    cases = {"/": ("text/html", b"APP"), "/index.html": ("text/html", b"APP"),
             "/assets/app.js": ("text/javascript", b"console.log(1)"),
             "/assets/app.css": ("text/css", b"body{}"), "/logo.svg": ("image/svg+xml", b"<svg/>"),
             "/messages/42": ("text/html", b"APP"), "/assets/missing.js": ("text/html", b"APP"),
             "/../secret.txt": ("text/html", b"APP"), "/%2e%2e/secret.txt": ("text/html", b"APP")}
    for path, (ctype, body) in cases.items():
        status, got_type, got = get(b1.port, path)
        assert status == 200 and got_type.startswith(ctype) and body in got, path
    status, _, body = get(b1.port, "/api/nothing")
    assert status == 404 and json.loads(body)["ok"] is False


# ----------------------------------------------------------------- channel proxy + spectrum
def _fake_hub(air):
    hub = AcousticLoopbackHub.__new__(AcousticLoopbackHub)
    hub.mic, hub.speaker = FakeMicrophone(air, "M"), FakeSpeaker(air, "S")
    hub.members, hub.recording = [], None
    hub.lock, hub.thread, hub.running = threading.Lock(), None, False
    return hub


def test_channel_tap_forwards_everything_the_engine_uses():
    air = FakeAir(time_scale=SCALE)
    acoustic = ChannelTap(fake_acoustic_channel(air, "A"))
    assert acoustic.kind == "acoustic" and acoustic.label == "Acoustic link (sound)"
    assert acoustic.speaker is acoustic.wrapped.speaker
    assert getattr(acoustic, "half_duplex", True) is True          # AcousticChannel has none
    assert getattr(acoustic, "hub", None) is None
    assert acoustic.overflows == 0 and acoustic.underflows == 0
    assert getattr(acoustic, "last_lost", False) is False
    acoustic.wrapped.mic.level = 0.123
    assert acoustic.level() == 0.123
    acoustic.some_new_flag = True                                  # writes go to the real channel
    assert acoustic.wrapped.some_new_flag is True

    hub = _fake_hub(air)
    loop = ChannelTap(LoopbackAcousticChannel(hub))
    assert loop.hub is hub and loop.half_duplex is False and getattr(loop, "speaker", None) is None
    assert loop.label == "Acoustic link (single-laptop loopback)"

    sim = ChannelTap(SimulatedChannel(SimulatedAir()))
    assert sim.kind == "simulated" and sim.overflows == 0 and sim.last_lost is False
    assert sim.air.noise == 0.002 and sim.level() == 0.002

    # read() returns exactly what the channel returned, and keeps a copy
    sim.wrapped._hear(np.ones(100, dtype=np.float32))
    audio = sim.read()
    assert len(audio) == 4900
    assert len(sim.take(10000)) == 4900 and len(sim.take(10)) == 0


def test_engine_status_works_through_the_tap():
    air = FakeAir(time_scale=SCALE)
    tap = ChannelTap(fake_acoustic_channel(air, "A", refuse_exclusive=True))
    e = Engine(1, tap, time_scale=SCALE)
    e.start()
    try:
        st = e.status()
        assert st["speaker_mode"] == "shared" and st["channel"] == "acoustic"
        assert e.stats.snapshot()["source"] == "Measured - acoustic"
    finally:
        e.stop()


def test_spectrum_events_on_the_silent_acoustic_path(tmp_path):
    air = FakeAir(time_scale=SCALE)
    nodes = []
    for node_id, name in ((1, "A"), (2, "B")):
        tap = ChannelTap(fake_acoustic_channel(air, name))
        engine = Engine(node_id, tap, time_scale=SCALE)
        bridge = WebBridge(engine, 0, mode="acoustic", tap=tap, web_dir=str(tmp_path))
        bridge.start()
        engine.start()
        nodes.append((engine, bridge))
    (e1, b1), (e2, b2) = nodes
    r2 = SseReader(b2.port)
    try:
        first = r2.wait(ev("spectrum"), timeout=10)
        assert len(first["bins"]) == 48 and first["f_lo"] == 500 and first["f_hi"] == 6000
        assert all(-90 <= b <= 0 for b in first["bins"])
        assert {"level", "tone0", "tone1", "noise_db", "transmitting", "node", "time"} <= set(first)
        assert "simulated" not in first
        t0 = time.time()
        time.sleep(2)
        n = len([e for e in r2.named("spectrum") if e["time"] >= t0])
        assert 12 <= n <= 36, n                                      # about 12 per second
        post(b1.port, {"cmd": "send", "dst": 2, "text": "TONES"})
        # node 2's microphone hears node 1's tones: the tone bins rise far above the noise
        loud = r2.wait(lambda e: e["event"] == "spectrum"
                       and max(e["tone0"], e["tone1"]) > e["noise_db"] + 30, timeout=60)
        assert loud["level"] > 0.01
        r2.wait(ev("message_rx"), timeout=120)
        assert get_json(b2.port, "/api/status")["peer"] == 1        # last node heard
        assert get_json(b1.port, "/api/status")["mode"] == "acoustic"
    finally:
        r2.close()
        for engine, bridge in nodes:
            bridge.stop()
            engine.stop()


def test_simulated_spectrum_shows_own_and_received_frames(tmp_path):
    nodes = make_sim_bridges(tmp_path, spectrum=True)
    (e1, b1), (e2, b2) = nodes
    r1, r2 = SseReader(b1.port), SseReader(b2.port)
    try:
        idle = r2.wait(ev("spectrum"), timeout=10)
        assert idle["simulated"] is True and idle["noise_db"] < -60
        post(b1.port, {"cmd": "send", "dst": 2, "text": "SIM"})
        own = r1.wait(lambda e: e["event"] == "spectrum" and e["tone1"] > -40, timeout=30)
        assert own["simulated"] is True
        r2.wait(lambda e: e["event"] == "spectrum" and e["tone1"] > -40, timeout=30)
    finally:
        r1.close()
        r2.close()
        for engine, bridge in nodes:
            bridge.stop()
            engine.stop()


# ----------------------------------------------------------------- launcher (in-process, fake audio)
def test_launch_pair_mode_with_silent_fake_audio(monkeypatch):
    air = FakeAir(time_scale=SCALE)
    monkeypatch.setattr(shout, "default_devices", lambda: (None, None))
    monkeypatch.setattr(shout, "AcousticLoopbackHub", lambda *a, **k: _fake_hub(air))
    args = shout.parse_args(["--node", "1", "--pair", "--http", "0", "--port", "0", "--no-browser"])
    run = shout.launch(args, time_scale=SCALE)
    try:
        (n1, n2) = run.nodes
        assert [n1["id"], n2["id"]] == [1, 2]
        assert n1["engine"].channel.hub is n2["engine"].channel.hub
        st1 = get_json(n1["bridge"].port, "/api/status")
        assert st1["mode"] == "pair" and st1["peer"] == 2 and st1["channel"] == "acoustic"
        assert st1["other_dashboards"] == [{"node": 2, "url": n2["bridge"].url}]
        assert st1["speaker_mode"] == "exclusive"
        r2 = SseReader(n2["bridge"].port)
        try:
            assert post(n1["bridge"].port, {"cmd": "send", "dst": 2, "text": "PAIR"})[1]["ok"]
            got = r2.wait(ev("message_rx"), timeout=120)
            assert got["message"] == "PAIR" and "simulated" not in got
            assert r2.wait(ev("spectrum"))["node"] == 2
        finally:
            r2.close()
        # the TCP control link runs next to the dashboard
        sock = socket.create_connection(("127.0.0.1", n1["control"].port), timeout=5)
        sock.close()
    finally:
        run.stop()


def test_launch_single_acoustic_node_with_silent_fake_audio(monkeypatch):
    air = FakeAir(time_scale=SCALE)
    monkeypatch.setattr(shout, "default_devices", lambda: (None, None))
    monkeypatch.setattr(shout, "AcousticChannel",
                        lambda mic, spk, exclusive=True: fake_acoustic_channel(air, "A"))
    args = shout.parse_args(["--node", "3", "--http", "0", "--port", "0", "--no-browser"])
    run = shout.launch(args, time_scale=SCALE)
    try:
        assert [n["id"] for n in run.nodes] == [3]
        st = get_json(run.nodes[0]["bridge"].port, "/api/status")
        assert st["mode"] == "acoustic" and st["node_id"] == 3
        assert st["peer"] is None and st["other_dashboards"] == []
    finally:
        run.stop()


def test_launch_reports_missing_microphone(monkeypatch):
    def no_mic():
        raise AudioError("No microphone found")
    monkeypatch.setattr(shout, "default_devices", no_mic)
    with pytest.raises(shout.LaunchError) as info:
        shout.launch(shout.parse_args(["--node", "1", "--no-browser"]))
    assert info.value.code == 2 and "microphone" in str(info.value)


def test_default_ports_follow_the_contract():
    args = shout.parse_args(["--node", "1", "--simulated"])
    assert args.http is None and args.port == 5000 and shout.HTTP_BASE + 1 == 8081


# ----------------------------------------------------------------- launcher (real subprocess)
def _start_shout(*extra):
    proc = subprocess.Popen([sys.executable, "-u", "shout.py", *extra], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc


def test_port_in_use_prints_one_error_line_and_exits_nonzero():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        proc = _start_shout("--simulated", "--no-browser", "--http", str(port), "--port", "0")
        out, _ = proc.communicate(timeout=60)
        assert proc.returncode == 3, out
        errors = [line for line in out.splitlines() if line.startswith("ERROR:")]
        assert len(errors) == 1 and str(port) in errors[0], out
        assert "Traceback" not in out
    finally:
        blocker.close()


def test_end_to_end_simulated_subprocess():
    proc = _start_shout("--simulated", "--no-browser", "--http", "0", "--port", "0")
    lines = []
    try:
        while True:
            line = proc.stdout.readline()
            assert line, "".join(lines)
            lines.append(line)
            if line.startswith("READY"):
                break
        ready = line.split()
        assert ready[1] == "mode=simulated"
        urls = dict(part.split("=", 1) for part in ready[2:])
        port1 = int(urls["node1"].rsplit(":", 1)[1])
        port2 = int(urls["node2"].rsplit(":", 1)[1])
        assert get_json(port2, "/api/status")["other_dashboards"] == [{"node": 1, "url": urls["node1"]}]
        r2 = SseReader(port2)
        try:
            r2.wait(ev("replay_done"))
            assert r2.named("engine_started")[0]["replay"] is True
            assert post(port1, {"cmd": "send", "dst": 2, "text": "END TO END"}) == (200, {"ok": True})
            got = r2.wait(ev("message_rx"), timeout=90)
            assert got["message"] == "END TO END" and got["origin"] == 1
            assert "SIMULATED" in got["text"]
            spectra = r2.named("spectrum")
            assert len(spectra) > 12 and all(len(s["bins"]) == 48 for s in spectra)
        finally:
            r2.close()
    finally:
        if os.name == "posix":
            proc.send_signal(signal.SIGINT)          # what Ctrl+C does
        else:
            proc.terminate()
        out, _ = proc.communicate(timeout=30)
    if os.name == "posix":
        assert proc.returncode == 0 and "Stopped." in out, out
