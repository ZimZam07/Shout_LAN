"""
engine_acoustic_check.py - checks the REAL engine process on the acoustic link,
controlled through the local TCP control link exactly like the UI:
start run_engine.py, connect, calibrate, send a HELLO, read status, shut down.
About 10 seconds of sound.
"""

import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from shout_lan.linebuffer import LineBuffer, encode   # noqa: E402

PORT = 5302


def main():
    proc = subprocess.Popen([sys.executable, "-u", "run_engine.py", "--node", "1", "--port", str(PORT)],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        while True:
            line = proc.stdout.readline()
            print("engine:", line.rstrip(), flush=True)
            if line.startswith("READY"):
                break
            if not line or "ERROR" in line:
                sys.exit(1)
        sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        buf, events = LineBuffer(), []

        def wait(pred, timeout):
            end = time.time() + timeout
            while time.time() < end:
                for e in events:
                    if pred(e):
                        return e
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                events.extend(buf.feed(data))
            return None

        print("-> calibrate", flush=True)
        sock.sendall(encode({"cmd": "calibrate"}))
        cal = wait(lambda e: e.get("event") == "calibration", 40)
        print("calibration:", cal and cal["text"], flush=True)
        print("-> hello", flush=True)
        sock.sendall(encode({"cmd": "hello"}))
        tx = wait(lambda e: e.get("event") == "frame_tx", 20)
        print("frame_tx:", tx and tx["text"], f"({tx and tx['airtime']} s of sound)", flush=True)
        sock.sendall(encode({"cmd": "status"}))
        st = wait(lambda e: e.get("event") == "status", 5)
        summary = {"kind": "Measured - acoustic (single engine process, single laptop)",
                   "calibration_ok": bool(cal and cal.get("ok")),
                   "calibration": cal, "hello_sent": tx is not None,
                   "status_channel": st and st["channel"], "stats_source": st and st["stats"]["source"],
                   "mic_level": st and st["mic_level"], "mic_overflows": st and st["mic_overflows"],
                   "frames_tx": st and st["stats"]["counters"]["frames_tx"]}
        print(json.dumps({k: v for k, v in summary.items() if k != "calibration"}, indent=1))
        sock.sendall(encode({"cmd": "shutdown"}))
        proc.wait(timeout=15)
        print("engine exit code:", proc.returncode)
        os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
        path = os.path.join(ROOT, "results", f"engine_acoustic_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump(summary, f, indent=1)
        print("saved:", path)
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
