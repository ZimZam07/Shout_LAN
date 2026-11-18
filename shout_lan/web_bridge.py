"""
web_bridge.py - the HTTP bridge between one engine and the browser dashboard
(docs/DASHBOARD_API.md, sections 2-5).

    GET  /api/status   one JSON object (engine.status() + dashboard fields)
    GET  /api/events   Server-Sent Events: replay of the last 300 events, then live
    POST /api/cmd      {"cmd": ...} -> the SAME command handler the TCP control link uses
    GET  /...          the pre-built web app from web/dist/ (index.html fallback)

Like the TCP control link, this server only listens on 127.0.0.1: it carries
commands and status between programs on the SAME laptop. Messages between
laptops still travel only through sound.

Networking concepts used here:
  * Multithreaded server: ThreadingHTTPServer runs every HTTP connection in its
    own thread, so a long-lived event stream never blocks a status request.
  * Producer-consumer queue per client: the engine (producer) only puts events
    into each browser's own bounded queue and never waits. That browser's
    connection thread (consumer) is the only writer on its socket. A client
    whose queue overflows (slow or dead tab) is dropped, so it can never slow
    down the engine or the other clients.
  * Server-Sent Events: one HTTP response that never ends; the server pushes
    "data: <json>\\n\\n" messages down it, and a ": ping" comment every 10 s keeps
    idle connections (and proxies) from timing it out.

It also hosts the spectrum monitor (contract section 4): ChannelTap wraps the
engine's channel object and copies the microphone audio the engine reads, and
SpectrumMonitor turns that copy into ~12 "spectrum" events per second on its
own thread, never on the audio path.
"""

import collections
import json
import mimetypes
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

import numpy as np

from shout_lan import config
from shout_lan.node import make_command_handler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIST = os.path.join(ROOT, "web", "dist")

REPLAY_SIZE = 300            # events kept for a browser that connects late (not spectrum)
CLIENT_QUEUE_SIZE = 2000     # per-browser backlog; overflow = the client is too slow -> dropped
KEEPALIVE = 10.0             # seconds between ": ping" comments
WRITE_TIMEOUT = 15.0         # a socket write blocked this long means the browser is gone

# Explicit types: Windows can map .js to text/plain through the registry, which
# makes browsers refuse ES modules.
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".ico": "image/x-icon", ".webp": "image/webp", ".woff": "font/woff",
        ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf", ".txt": "text/plain; charset=utf-8",
        ".wasm": "application/wasm", ".webmanifest": "application/manifest+json"}


def _json_bytes(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=_jsonable).encode("utf-8")


def _jsonable(x):
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (bytes, bytearray)):
        return x.hex(" ")
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    return str(x)


# =========================================================== event fan-out
class SseClient:
    """One connected browser: its own bounded queue (producer-consumer)."""

    def __init__(self):
        self.queue = queue.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self.alive = True

    def offer(self, data):
        """Called by the producer (engine thread). Never blocks. False = queue full."""
        if not self.alive:
            return False
        try:
            self.queue.put_nowait(data)
            return True
        except queue.Full:
            return False

    def close(self):
        """Stop this client; wake its writer even if the queue is full."""
        self.alive = False
        while True:
            try:
                self.queue.put_nowait(None)
                return
            except queue.Full:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass


class EventHub:
    """
    Receives every event of one engine (as an engine listener), keeps the
    replay buffer and hands each event to every connected browser's queue.
    """

    def __init__(self, simulated=False):
        self.simulated = simulated
        self.replay = collections.deque(maxlen=REPLAY_SIZE)
        self.clients = []
        self.lock = threading.Lock()
        self.dropped_clients = 0

    def _label(self, event):
        if not self.simulated:
            return event
        event = dict(event)                    # never change the dict other listeners see
        event["simulated"] = True
        text = event.get("text")
        if text and "SIMULATED" not in text:
            event["text"] = "[SIMULATED] " + text
        return event

    def publish(self, event, replay=True):
        """Engine listener: must be quick and must never block (it only queues)."""
        event = self._label(event)
        data = _json_bytes(event)
        slow = []
        with self.lock:
            if replay and event.get("event") != "spectrum":
                self.replay.append(event)
            for c in self.clients:
                if not c.offer(data):
                    slow.append(c)
            for c in slow:
                self.clients.remove(c)
                self.dropped_clients += 1
        for c in slow:
            c.close()

    def subscribe(self):
        """
        New browser. The replay and the registration happen under ONE lock, so
        the client sees every event exactly once: replay, replay_done, then live.
        """
        client = SseClient()
        with self.lock:
            for e in self.replay:
                client.offer(_json_bytes({**e, "replay": True}))
            client.offer(_json_bytes({"event": "replay_done", "time": round(time.time(), 3),
                                      "count": len(self.replay)}))
            self.clients.append(client)
        return client

    def unsubscribe(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
        client.close()

    def clear(self):
        with self.lock:
            self.replay.clear()

    def client_count(self):
        with self.lock:
            return len(self.clients)

    def close(self):
        with self.lock:
            clients, self.clients = self.clients, []
        for c in clients:
            c.close()


# =========================================================== spectrum
class ChannelTap:
    """
    Transparent proxy around the engine's channel. Every attribute and method
    is forwarded to the real channel (kind, label, speaker, hub, half_duplex,
    overflows, underflows, last_lost, level(), start(), transmit(), ...), so the
    engine cannot tell the difference. read() additionally keeps a reference to
    the audio it returns (one deque append: the audio path is not slowed down).
    On channels that are not real sound, our own transmissions are copied too
    (a real microphone hears its own speaker; the software air does not).
    """

    _own = ("_inner", "_lock", "_fifo", "_fifo_len", "_max_backlog")

    def __init__(self, channel, max_backlog_s=None):
        object.__setattr__(self, "_inner", channel)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_fifo", collections.deque())
        object.__setattr__(self, "_fifo_len", 0)
        if max_backlog_s is None:
            # sound arrives in real time; the SIMULATED air delivers a whole frame at once
            max_backlog_s = 0.5 if getattr(channel, "kind", "") == "acoustic" else 30.0
        object.__setattr__(self, "_max_backlog", int(max_backlog_s * config.SAMPLE_RATE))

    # ---- forwarding
    def __getattr__(self, name):                       # only called for names not found here
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value):
        if name in ChannelTap._own:
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def __delattr__(self, name):
        delattr(self._inner, name)

    def __repr__(self):
        return f"ChannelTap({self._inner!r})"

    @property
    def wrapped(self):
        return self._inner

    # ---- tapped methods
    def read(self):
        audio = self._inner.read()
        if len(audio):
            self._push(audio)
        return audio

    def transmit(self, samples):
        if getattr(self._inner, "kind", "") != "acoustic":
            air = getattr(self._inner, "air", None)
            gain = getattr(air, "gain", None) or getattr(self._inner, "gain", 1.0)
            self._push(np.asarray(samples, dtype=np.float32) * gain)
        return self._inner.transmit(samples)

    def _push(self, audio):
        with self._lock:
            self._fifo.append(audio)
            self._fifo_len += len(audio)
            while self._fifo_len > self._max_backlog and len(self._fifo) > 1:
                self._fifo_len -= len(self._fifo.popleft())

    def take(self, n):
        """Spectrum thread: remove up to n of the oldest tapped samples."""
        out, got = [], 0
        with self._lock:
            while self._fifo and got < n:
                part = self._fifo[0]
                need = n - got
                if len(part) <= need:
                    self._fifo.popleft()
                    out.append(part)
                    got += len(part)
                else:
                    out.append(part[:need])
                    self._fifo[0] = part[need:]
                    got += need
            self._fifo_len -= got
        return np.concatenate(out).astype(np.float32, copy=False) if out else np.zeros(0, np.float32)


class SpectrumMonitor:
    """
    ~12 times a second: FFT of the newest audio the node heard -> one
    "spectrum" event (48 bins, 500-6000 Hz, dB re full-scale sine, -90..0).
    Runs on its own thread, so the engine's receive loop is never delayed.
    Tapped audio is consumed at real-time speed (one hop per tick): on the
    SIMULATED channel, where a whole frame arrives at once, the display still
    shows it for as long as it lasts on the (software) air.
    """

    RATE = 12                       # events per second
    N_BINS = 48
    F_LO, F_HI = 500.0, 6000.0
    FFT_N = 4096
    FLOOR_DB = -90.0
    TONE_GUARD_HZ = 150.0           # bins this close to a tone are not "noise"

    def __init__(self, engine, tap, publish):
        self.engine = engine
        self.tap = tap
        self.publish = publish
        self.hop = config.SAMPLE_RATE // self.RATE
        self.window = np.hanning(self.FFT_N).astype(np.float32)
        self.scale = 2.0 / float(self.window.sum())      # |X| -> sine amplitude
        self.freqs = np.fft.rfftfreq(self.FFT_N, 1.0 / config.SAMPLE_RATE)
        edges = np.linspace(self.F_LO, self.F_HI, self.N_BINS + 1)
        self.bin_index = [np.where((self.freqs >= lo) & (self.freqs < hi))[0]
                          for lo, hi in zip(edges[:-1], edges[1:])]
        self.bin_centres = (edges[:-1] + edges[1:]) / 2
        self.buf = np.zeros(self.FFT_N, dtype=np.float32)
        self.have = 0                                   # real samples in buf so far
        self.rng = np.random.default_rng()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name=f"spectrum-{self.engine.node_id}")
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _simulated_noise_std(self):
        ch = self.tap.wrapped
        air = getattr(ch, "air", None)
        std = getattr(air, "noise", None) if air is not None else getattr(ch, "noise", None)
        return float(std) if isinstance(std, (int, float)) else None

    def _loop(self):
        period = 1.0 / self.RATE
        next_tick = time.time()
        while self.running:
            next_tick += period
            try:
                if self.engine.running:
                    event = self.compute()
                    if event is not None:
                        self.publish(event)
            except Exception:
                pass                                    # never let the display kill anything
            delay = next_tick - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.time()                 # fell behind: skip, don't burst

    def compute(self):
        new = self.tap.take(self.hop)
        simulated = getattr(self.tap, "kind", "") != "acoustic"
        if len(new) < self.hop and simulated:
            # Nothing (more) on the SIMULATED air: the simulation's own background
            # noise model (the same std the SimulatedChannel adds to what it delivers).
            std = self._simulated_noise_std()
            if std:
                fill = self.rng.normal(0, std, self.hop - len(new)).astype(np.float32)
                new = np.concatenate([new, fill])
        if len(new):
            n = min(len(new), self.FFT_N)
            self.buf = np.roll(self.buf, -n)
            self.buf[-n:] = new[-n:]
            self.have = min(self.FFT_N, self.have + n)
        if self.have == 0:
            return None                                 # no audio heard yet: nothing to show
        spec = np.abs(np.fft.rfft(self.buf * self.window)) * self.scale
        db = 20 * np.log10(np.maximum(spec, 1e-12))
        bins = np.array([db[idx].max() if len(idx) else self.FLOOR_DB for idx in self.bin_index])
        bins = np.clip(bins, self.FLOOR_DB, 0.0)
        f0, f1 = self.engine.modem.freq0, self.engine.modem.freq1
        tone0, tone1 = self._tone_db(db, f0), self._tone_db(db, f1)
        away = [b for b, c in zip(bins, self.bin_centres)
                if abs(c - f0) > self.TONE_GUARD_HZ and abs(c - f1) > self.TONE_GUARD_HZ]
        noise_db = float(np.median(away)) if away else self.FLOOR_DB
        level = float(np.sqrt(np.mean(new * new))) if len(new) else 0.0
        event = {"event": "spectrum", "time": round(time.time(), 3), "node": self.engine.node_id,
                 "bins": [round(float(b), 1) for b in bins], "f_lo": self.F_LO, "f_hi": self.F_HI,
                 "level": round(min(1.0, level), 4), "tone0": tone0, "tone1": tone1,
                 "noise_db": round(noise_db, 1), "transmitting": bool(self.engine.transmitting)}
        return event

    def _tone_db(self, db, freq):
        k = int(round(freq * self.FFT_N / config.SAMPLE_RATE))
        lo, hi = max(0, k - 2), min(len(db), k + 3)
        return round(float(np.clip(db[lo:hi].max(), self.FLOOR_DB, 0.0)), 1)


# =========================================================== HTTP server
class _CmdServerShim:
    """What make_command_handler expects from the TCP server (host, port, client_count)."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.host = bridge.host
        self.port = bridge.port

    def client_count(self):
        return self.bridge.hub.client_count()


class _CmdClientShim:
    """Collects the replies the command handler would send to a TCP client."""

    def __init__(self):
        self.replies = []

    def send(self, obj):
        self.replies.append(obj)


class _Server(ThreadingHTTPServer):
    daemon_threads = True            # one thread per connection; never keeps the program alive
    block_on_close = False
    # Windows: SO_REUSEADDR would let a SECOND program bind the same port silently.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class WebBridge:
    """
    One HTTP server for one engine. `info` supplies the dashboard fields of
    /api/status: mode, peer (int or None, or a function), other_dashboards.
    """

    def __init__(self, engine, port, mode="acoustic", peer=None, other_dashboards=None,
                 on_shutdown=None, host="127.0.0.1", web_dir=None, tap=None, spectrum=True):
        self.engine = engine
        self.host = host
        self.port = port
        self.mode = mode
        self.peer = peer
        self.other_dashboards = other_dashboards or []
        self.web_dir = web_dir or WEB_DIST
        self.on_shutdown = on_shutdown or (lambda: None)
        self.hub = EventHub(simulated=(mode == "simulated"))
        self.tap = tap
        self.spectrum = None
        if spectrum and tap is not None:
            self.spectrum = SpectrumMonitor(engine, tap, lambda e: self.hub.publish(e, replay=False))
        self.httpd = None
        self.command_handler = make_command_handler(engine, self.on_shutdown)

    # ---- lifecycle
    def start(self):
        """Bind the port (raises OSError if it is in use) and start serving."""
        bridge = self

        class Handler(_Handler):
            pass
        Handler.bridge = bridge
        self.httpd = _Server((self.host, self.port), Handler)
        self.port = self.httpd.server_address[1]           # real port (if 0 was requested)
        self.engine.add_listener(self.hub.publish)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.25},
                         daemon=True, name=f"http-{self.port}").start()
        if self.spectrum is not None:
            self.spectrum.start()

    def stop(self):
        if self.spectrum is not None:
            self.spectrum.stop()
        self.engine.remove_listener(self.hub.publish)
        self.hub.close()                                   # ends every event stream
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    # ---- API
    def status(self):
        e = self.engine
        st = e.status()
        st["shield"] = bool(getattr(e, "shield", False))
        st["shield_info"] = dict(getattr(e, "shield_info", None) or {})
        noise = getattr(e, "noise", None) or {"on": False, "level": 0.0}
        st["noise"] = dict(noise)
        st["shield_available"] = callable(getattr(e, "set_shield", None))
        st["noise_available"] = callable(getattr(e, "set_noise", None))
        st["mode"] = self.mode
        st["peer"] = self._peer()
        st["http_port"] = self.port
        st["other_dashboards"] = list(self.other_dashboards)
        st["sse_clients"] = self.hub.client_count()
        return st

    def _peer(self):
        if callable(self.peer):
            return self.peer()
        if self.peer is not None:
            return self.peer
        nodes = self.engine.node_table()                   # last node heard
        if not nodes:
            return None
        return int(max(nodes.items(), key=lambda kv: kv[1].get("last_heard", 0))[0])

    def command(self, cmd):
        """Run one command; returns the reply object of section 2."""
        if not isinstance(cmd, dict) or not isinstance(cmd.get("cmd"), str):
            return {"ok": False, "error": 'expected a JSON object like {"cmd": "status"}'}
        name = cmd["cmd"]
        try:
            if name == "clear":
                self.hub.clear()                           # forget the replay buffer ...
                self.engine.reset_stats()                  # ... and the statistics
                self.hub.publish({"event": "cleared", "time": round(time.time(), 3),
                                  "node": self.engine.node_id,
                                  "text": "Event log and statistics cleared"}, replay=False)
                return {"ok": True}
            if name == "set_shield" and not callable(getattr(self.engine, "set_shield", None)):
                return {"ok": False, "error": "set_shield is not available in this build "
                                              "(the Noise Shield is not in this engine yet)"}
            if name == "set_noise" and not callable(getattr(self.engine, "set_noise", None)):
                return {"ok": False, "error": "set_noise is not available in this build "
                                              "(the interference generator is not in this engine yet)"}
            client = _CmdClientShim()
            self.command_handler(_CmdServerShim(self), client, cmd)   # same handler as TCP
        except Exception as exc:
            return {"ok": False, "error": str(exc) or exc.__class__.__name__}
        reply = {"ok": True}
        for r in client.replies:
            if r.get("event") == "status":
                reply["status"] = {k: v for k, v in r.items() if k != "event"}
        return reply


class _Handler(BaseHTTPRequestHandler):
    bridge = None                     # set per server in WebBridge.start()
    protocol_version = "HTTP/1.1"
    server_version = "ShoutLAN"

    def log_message(self, fmt, *args):    # keep the presenter's terminal clean
        pass

    # ---- helpers
    def _send(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, _json_bytes(obj))

    # ---- routes
    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/status":
                self._json(self.bridge.status())
            elif path == "/api/events":
                self._events()
            elif path.startswith("/api/"):
                self._json({"ok": False, "error": f"unknown API path {path}"}, 404)
            else:
                self._static(path)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/api/cmd":
            self._json({"ok": False, "error": f"unknown API path {path}"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            cmd = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "body is not valid JSON"}, 400)
            return
        if cmd is None:
            self._json({"ok": False, "error": "empty body"}, 400)
            return
        self._json(self.bridge.command(cmd))

    # ---- Server-Sent Events: this thread is the consumer of the client's queue
    def _events(self):
        hub = self.bridge.hub
        client = hub.subscribe()
        self.close_connection = True
        try:
            self.connection.settimeout(WRITE_TIMEOUT)      # a dead browser cannot hang this thread forever
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while client.alive:
                try:
                    data = client.queue.get(timeout=KEEPALIVE)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")         # keep-alive comment
                    self.wfile.flush()
                    continue
                if data is None:
                    break
                chunks = [data]
                while len(chunks) < 64:                     # write a burst in one go
                    try:
                        more = client.queue.get_nowait()
                    except queue.Empty:
                        break
                    if more is None:
                        client.alive = False
                        break
                    chunks.append(more)
                self.wfile.write(b"".join(b"data: " + c + b"\n\n" for c in chunks))
                self.wfile.flush()
        except OSError:
            pass                                            # browser closed the tab
        finally:
            hub.unsubscribe(client)

    # ---- static web app
    def _static(self, path):
        web = self.bridge.web_dir
        index = os.path.join(web, "index.html")
        if not os.path.isfile(index):
            self._send(200, _placeholder_page(self.bridge).encode("utf-8"), MIME[".html"])
            return
        rel = unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(web, rel)) if rel else index
        base = os.path.normpath(web)
        if not (full == base or full.startswith(base + os.sep)) or not os.path.isfile(full):
            full = index                                    # SPA fallback (and no path escapes)
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, ctype)


def _placeholder_page(bridge):
    e = bridge.engine
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>SHOUT LAN node {e.node_id}</title>
<style>body{{font:14px/1.4 system-ui,sans-serif;margin:16px;background:#111;color:#ddd}}
pre{{background:#000;padding:8px;height:60vh;overflow:auto;white-space:pre-wrap}}
input{{width:60%}} code{{color:#9cf}}</style></head>
<body>
<h2>SHOUT LAN node {e.node_id} ({bridge.mode})</h2>
<p>The web app has not been built yet (no <code>web/dist/index.html</code>).
This placeholder shows the live event stream from <code>/api/events</code> as text.</p>
<form id="f"><input id="c" value='{{"cmd": "status"}}'> <button>POST /api/cmd</button></form>
<p id="reply"></p>
<p>spectrum events: <span id="sp">0</span></p>
<pre id="log"></pre>
<script>
const log = document.getElementById('log'), sp = document.getElementById('sp');
let n = 0;
const es = new EventSource('/api/events');
es.onopen = () => {{ log.textContent = ''; }};
es.onmessage = (m) => {{
  const e = JSON.parse(m.data);
  if (e.event === 'spectrum') {{ sp.textContent = ++n; return; }}
  const t = new Date(e.time * 1000).toLocaleTimeString();
  log.textContent += t + ' [' + e.event + (e.replay ? ', replay' : '') + '] ' + (e.text || '') + '\\n';
  log.scrollTop = log.scrollHeight;
}};
document.getElementById('f').onsubmit = async (ev) => {{
  ev.preventDefault();
  const r = await fetch('/api/cmd', {{method: 'POST', body: document.getElementById('c').value}});
  document.getElementById('reply').textContent = await r.text();
}};
</script>
</body></html>
"""
