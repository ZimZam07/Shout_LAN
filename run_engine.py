"""
run_engine.py - start one SHOUT LAN node (the engine + its local TCP control server).

    python run_engine.py --node 1                    # real acoustic link, default mic/speaker
    python run_engine.py --node 2 --mic 9 --speaker 8
    python run_engine.py --node 1 --simulated --port 5001   # SIMULATED backup mode

Then start the UI (python run_ui.py) or the monitor (python monitor_client.py).
The terminal shows every engine event as it happens. Stop with Ctrl+C.
"""

import argparse
import os
import sys
import threading
import time

from shout_lan import config
from shout_lan.audio_io import AudioError, default_devices
from shout_lan.channel import AcousticChannel, UdpSimulatedChannel
from shout_lan.control_server import ControlServer
from shout_lan.engine import Engine
from shout_lan.modem import Modem
from shout_lan.node import make_command_handler
from shout_lan.security import Security, SecurityError, load_key


def main():
    p = argparse.ArgumentParser(description="SHOUT LAN engine")
    p.add_argument("--node", type=int, required=True, help="this laptop's node id (1-254)")
    p.add_argument("--mic", type=int, help="microphone device index")
    p.add_argument("--speaker", type=int, help="speaker device index")
    p.add_argument("--shared", action="store_true",
                   help="use Windows shared (processed) mic mode instead of exclusive")
    p.add_argument("--simulated", action="store_true", help="SIMULATED channel (not sound)")
    p.add_argument("--port", type=int, default=config.CONTROL_PORT)
    p.add_argument("--volume", type=float, default=config.TX_VOLUME)
    p.add_argument("--f0", type=int, default=config.FREQ_0)
    p.add_argument("--f1", type=int, default=config.FREQ_1)
    p.add_argument("--key", default="secret.key",
                   help="AES-256 key file (made by tools/gen_key.py); encryption is off without it")
    args = p.parse_args()

    modem = Modem(freq0=args.f0, freq1=args.f1, volume=args.volume)
    try:
        if args.simulated:
            channel = UdpSimulatedChannel(args.node)
        else:
            mic, spk = default_devices()
            channel = AcousticChannel(args.mic if args.mic is not None else mic,
                                      args.speaker if args.speaker is not None else spk,
                                      exclusive=not args.shared)
    except (AudioError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(2)
    security = None
    if os.path.exists(args.key):
        try:
            security = Security(load_key(args.key))
        except SecurityError as exc:
            print(f"ERROR: {exc}", file=sys.stderr, flush=True)
            sys.exit(2)
    engine = Engine(args.node, channel, modem, security=security)
    stop = threading.Event()

    def print_event(e):
        if e.get("text") and e["event"] not in ("tx_start",):
            print(time.strftime("%H:%M:%S"), f"[{e['event']}]", e["text"], flush=True)

    engine.add_listener(print_event)
    server = ControlServer(engine, port=args.port,
                           command_handler=make_command_handler(engine, stop.set))
    try:
        server.start()
    except OSError as exc:
        print(f"ERROR: cannot open control port {args.port}: {exc}", file=sys.stderr, flush=True)
        sys.exit(3)
    try:
        engine.start()
    except (AudioError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        server.stop()
        sys.exit(2)
    print("Encryption: AES-256-GCM key loaded from " + args.key if security
          else f"Encryption: OFF (no {args.key}; create one with tools/gen_key.py)", flush=True)
    print(f"READY node={args.node} channel={channel.label} "
          f"control=127.0.0.1:{server.port}", flush=True)
    try:
        while not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    engine.stop()
    server.stop()
    print("Engine stopped.", flush=True)


if __name__ == "__main__":
    main()
