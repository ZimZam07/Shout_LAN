"""
shout.py - the one command to run SHOUT LAN with its browser dashboard.

    python shout.py --node 1                # real sound: this laptop's speaker + microphone
    python shout.py --node 1 --pair         # ONE laptop: nodes 1 and 2 share its speaker + mic
    python shout.py --node 1 --simulated    # no sound: nodes 1 and 2 on a SIMULATED software air

Node N's dashboard is served at http://127.0.0.1:(8080 + N) and opened in the
browser. --pair and --simulated run nodes N and N+1 in this one process (1 and 2
by default, dashboards on 8081 and 8082). The TCP control link (JSON lines,
127.0.0.1:5000, used by monitor_client.py) keeps running next to the dashboard.
Stop with Ctrl+C.

Everything here stays on 127.0.0.1: messages between laptops travel only
through sound (or, in --simulated mode, through the clearly labelled software air).
"""

import argparse
import os
import signal
import sys
import threading
import time
import webbrowser

from shout_lan import config
from shout_lan.audio_io import AudioError, default_devices
from shout_lan.channel import (AcousticChannel, AcousticLoopbackHub, LoopbackAcousticChannel,
                               SimulatedAir, SimulatedChannel)
from shout_lan.control_server import ControlServer
from shout_lan.engine import Engine
from shout_lan.modem import Modem
from shout_lan.security import Security, SecurityError, load_key
from shout_lan.web_bridge import ChannelTap, WebBridge

HTTP_BASE = 8080


class LaunchError(Exception):
    """A clear one-line problem (no microphone, port in use, ...) with an exit code."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="SHOUT LAN: engine + browser dashboard")
    p.add_argument("--node", type=int, default=1,
                   help="this laptop's node id 1-254 (pair/simulated: nodes N and N+1; default 1)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--pair", action="store_true",
                      help="one laptop, two nodes sharing its speaker and microphone (real sound)")
    mode.add_argument("--simulated", action="store_true",
                      help="two nodes on a SIMULATED software air (no sound)")
    p.add_argument("--http", type=int, default=None,
                   help="dashboard port (default 8080+node; pair/simulated: PORT and PORT+1; 0 = any free)")
    p.add_argument("--port", type=int, default=config.CONTROL_PORT,
                   help="TCP control port (pair/simulated: PORT and PORT+1; 0 = any free)")
    p.add_argument("--mic", type=int, help="microphone device index")
    p.add_argument("--speaker", type=int, help="speaker device index")
    p.add_argument("--shared", action="store_true",
                   help="use Windows shared (processed) mic mode instead of exclusive")
    p.add_argument("--volume", type=float, default=config.TX_VOLUME)
    p.add_argument("--f0", type=int, default=config.FREQ_0)
    p.add_argument("--f1", type=int, default=config.FREQ_1)
    p.add_argument("--key", default="secret.key",
                   help="AES-256 key file (made by tools/gen_key.py); encryption is off without it")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    p.add_argument("--peer", type=int, default=None,
                   help="the other laptop's node id: the dashboard's default destination")
    args = p.parse_args(argv)
    two = args.pair or args.simulated
    if not 1 <= args.node <= (253 if two else 254):
        p.error("--node must be 1..254 (1..253 with --pair/--simulated)")
    return args


def mode_of(args):
    return "pair" if args.pair else "simulated" if args.simulated else "acoustic"


def make_channels(args):
    """Returns [(node_id, channel)] for the chosen mode. Raises AudioError/OSError."""
    if args.simulated:
        air = SimulatedAir(time_scale=1.0)            # real-time speed: looks like the real thing
        return [(args.node, SimulatedChannel(air, name="A")),
                (args.node + 1, SimulatedChannel(air, name="B"))]
    mic, spk = default_devices()
    mic = args.mic if args.mic is not None else mic
    spk = args.speaker if args.speaker is not None else spk
    if args.pair:
        hub = AcousticLoopbackHub(mic, spk, exclusive=not args.shared)
        return [(args.node, LoopbackAcousticChannel(hub)),
                (args.node + 1, LoopbackAcousticChannel(hub))]
    return [(args.node, AcousticChannel(mic, spk, exclusive=not args.shared))]


class Running:
    """Everything one shout.py process runs: engines, dashboards, control servers."""

    def __init__(self, mode):
        self.mode = mode
        self.nodes = []             # dicts: id, engine, bridge, control
        self.stop_event = threading.Event()
        self.security_note = ""

    def urls(self):
        return {n["id"]: n["bridge"].url for n in self.nodes}

    def stop(self):
        for n in self.nodes:
            if n.get("bridge") is not None:
                n["bridge"].stop()
        for n in self.nodes:
            if n.get("engine") is not None:
                n["engine"].stop()
        for n in self.nodes:
            if n.get("control") is not None:
                n["control"].stop()


def launch(args, time_scale=1.0, log=None):
    """
    Build and start everything. Returns a Running object; raises LaunchError
    (after undoing whatever was started) on any problem.
    """
    mode = mode_of(args)
    run = Running(mode)
    try:
        channels = make_channels(args)
    except (AudioError, OSError) as exc:
        raise LaunchError(str(exc), 2) from exc

    security = None
    if os.path.exists(args.key):
        try:
            security = Security(load_key(args.key))
        except SecurityError as exc:
            raise LaunchError(str(exc), 2) from exc
    run.security_note = ("Encryption: AES-256-GCM key loaded from " + args.key if security
                         else f"Encryption: OFF (no {args.key}; create one with tools/gen_key.py)")

    two = len(channels) == 2
    for i, (node_id, channel) in enumerate(channels):
        tap = ChannelTap(channel)                      # copies the mic audio for the spectrum
        modem = Modem(freq0=args.f0, freq1=args.f1, volume=args.volume)
        engine = Engine(node_id, tap, modem, time_scale=time_scale, security=security)
        if args.http is None:
            http_port = HTTP_BASE + node_id
        else:
            http_port = args.http + i if args.http else 0
        control_port = args.port + i if args.port else 0
        peer = channels[1 - i][0] if two else args.peer
        bridge = WebBridge(engine, http_port, mode=mode, peer=peer, tap=tap,
                           on_shutdown=run.stop_event.set)
        control = ControlServer(engine, port=control_port, command_handler=bridge.command_handler)
        if log is not None:
            engine.add_listener(log)
        run.nodes.append({"id": node_id, "engine": engine, "bridge": None, "control": None,
                          "_bridge": bridge, "_control": control})

    try:
        for n in run.nodes:
            try:
                n["_bridge"].start()
            except OSError as exc:
                raise LaunchError(f"cannot open dashboard port {n['_bridge'].port} for node "
                                  f"{n['id']}: {exc}", 3) from exc
            n["bridge"] = n["_bridge"]
            try:
                n["_control"].start()
            except OSError as exc:
                raise LaunchError(f"cannot open control port {n['_control'].port} for node "
                                  f"{n['id']}: {exc}", 3) from exc
            n["control"] = n["_control"]
        for n in run.nodes:
            n["bridge"].other_dashboards = [{"node": m["id"], "url": m["bridge"].url}
                                            for m in run.nodes if m is not n]
        for n in run.nodes:
            try:
                n["engine"].start()
            except (AudioError, OSError) as exc:
                raise LaunchError(str(exc), 2) from exc
    except LaunchError:
        run.stop()
        raise
    return run


def main(argv=None):
    args = parse_args(argv)
    two = args.pair or args.simulated

    def print_event(e):
        if e.get("text") and e["event"] not in ("tx_start",):
            prefix = f"[node {e['node']}] " if two else ""
            print(time.strftime("%H:%M:%S"), f"{prefix}[{e['event']}]", e["text"], flush=True)

    try:
        run = launch(args, log=print_event)
    except LaunchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(exc.code)

    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, lambda *_: run.stop_event.set())
        except ValueError:
            pass
    print(run.security_note, flush=True)
    for n in run.nodes:
        print(f"  node {n['id']}: dashboard {n['bridge'].url}  "
              f"control 127.0.0.1:{n['control'].port}  ({n['engine'].channel.label})", flush=True)
    print(f"READY mode={run.mode} "
          + " ".join(f"node{n['id']}={n['bridge'].url}" for n in run.nodes), flush=True)
    if run.mode == "simulated":
        print("NOTE: SIMULATED mode - the nodes talk through software, NOT through sound.", flush=True)
    if not args.no_browser:
        def open_tabs():
            for url in run.urls().values():
                try:
                    webbrowser.open_new_tab(url)
                except Exception:
                    pass
        threading.Thread(target=open_tabs, daemon=True).start()
    try:
        while not run.stop_event.is_set():
            run.stop_event.wait(0.5)
    except KeyboardInterrupt:
        pass
    print("Stopping...", flush=True)
    run.stop()
    print("Stopped.", flush=True)


if __name__ == "__main__":
    main()
