"""
tone_trial.py - find which pair of BFSK tones works between two particular laptops.

Why: a laptop speaker, its case and the desk resonate at some frequencies, and
reflections cancel others. A tone that keeps ringing (or sits in a dip) makes
every frame fail no matter how good the decoder is. The right pair depends on
the two laptops and where they stand, so it has to be measured (docs/TESTING.md).

How: run this on BOTH laptops at the same time. Neither needs to know when the
other started: the pair on trial is chosen from the wall clock, one pair per
minute (pair = PAIRS[minute % len(PAIRS)]). In each minute the node with
--role a sends three HELLO probes at seconds 8, 16, 24 and the node with
--role b at seconds 34, 42, 50. Every probe is answered by the other node, so
each probe tests both directions. A few seconds of clock difference is fine.

    python tools/tone_trial.py --port 8081 --role b --minutes 7      (Mac, node 1)
    python tools/tone_trial.py --port 8082 --role a --minutes 7      (Windows, node 2)

Writes results/tone_trial_<role>_<time>.json with, per pair, how many frames
this node decoded and how many it rejected. Standard library only.
"""
import argparse
import json
import os
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = [(2750, 4000), (3250, 4000), (1750, 2750), (1750, 3250), (3000, 5000), (2500, 3500)]
SEND_AT = {"a": (8, 16, 24), "b": (34, 42, 50)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--role", choices=("a", "b"), required=True)
    p.add_argument("--minutes", type=int, default=len(PAIRS) + 1)
    args = p.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    events, lock = [], threading.Lock()

    def cmd(body):
        req = urllib.request.Request(base + "/api/cmd", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def listen():
        while True:
            try:
                with urllib.request.urlopen(base + "/api/events", timeout=3600) as r:
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:"):
                            e = json.loads(line[5:])
                            if e.get("event") in ("frame_rx", "frame_tx", "preamble") and not e.get("replay"):
                                with lock:
                                    events.append(e)
            except Exception:
                time.sleep(1)

    threading.Thread(target=listen, daemon=True).start()
    cmd({"cmd": "set_shield", "value": False})
    try:
        cmd({"cmd": "set_noise", "on": False})
    except Exception:
        pass
    cmd({"cmd": "set_mac", "value": "none"})          # probes are sent on a fixed timetable
    slots = []
    # wait for the next whole minute, then run `minutes` slots
    time.sleep(60 - time.time() % 60)
    for _ in range(args.minutes):
        minute = int(time.time() // 60)
        pair = PAIRS[minute % len(PAIRS)]
        start = minute * 60
        cmd({"cmd": "set_tones", "freq0": pair[0], "freq1": pair[1]})
        with lock:
            mark = len(events)
        print(time.strftime("%H:%M:%S"), "pair", pair, flush=True)
        for second in SEND_AT[args.role]:
            wait = start + second - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                cmd({"cmd": "hello"})
            except Exception as exc:
                print("hello failed:", exc)
        wait = start + 59.0 - time.time()
        if wait > 0:
            time.sleep(wait)
        with lock:
            mine = events[mark:]
        rx = [e for e in mine if e["event"] == "frame_rx"]
        ok = [e for e in rx if e.get("ok")]
        slot = {"pair": pair, "minute": time.strftime("%H:%M", time.localtime(start)),
                "sent": sum(1 for e in mine if e["event"] == "frame_tx"),
                "preambles": sum(1 for e in mine if e["event"] == "preamble"),
                "rx_ok": len(ok), "rx_bad": len(rx) - len(ok),
                "snr_ok": [e.get("snr_db") for e in ok],
                "errors": [str(e.get("error"))[:40] for e in rx if not e.get("ok")]}
        slots.append(slot)
        print("   ", {k: slot[k] for k in ("sent", "preambles", "rx_ok", "rx_bad", "snr_ok")}, flush=True)
        wait = start + 60.0 - time.time()
        if wait > 0:
            time.sleep(wait)
    cmd({"cmd": "set_mac", "value": "csma"})
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    path = os.path.join(ROOT, "results", f"tone_trial_{args.role}_{time.strftime('%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"role": args.role, "port": args.port, "slots": slots}, f, indent=1)
    print("saved", path)


if __name__ == "__main__":
    main()
