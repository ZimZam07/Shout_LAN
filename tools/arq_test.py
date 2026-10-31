"""
arq_test.py - Milestone 3 hardware test: Stop-and-Wait ARQ over REAL sound.

Single laptop: two engines (node 1 and node 2) share this laptop's speaker
and microphone. Every DATA frame and every ACK really travels
speaker -> air -> microphone.

    python tools/arq_test.py              # all scenarios
    python tools/arq_test.py --quick      # only normal + corrupted + lost ACK
    python tools/arq_test.py --rest       # only 3-frame message + ping + max retries
    python tools/arq_test.py --security   # same text plain vs AES-GCM encrypted (overhead)

Results are saved in results/arq_*.json (all engine events included).
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shout_lan.audio_io import default_devices                      # noqa: E402
from shout_lan.channel import AcousticLoopbackHub, LoopbackAcousticChannel  # noqa: E402
from shout_lan.engine import Engine                                  # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


class Log:
    def __init__(self, *engines):
        self.events = []
        self.cond = threading.Condition()
        for e in engines:
            e.add_listener(self.add)

    def add(self, e):
        with self.cond:
            self.events.append(e)
            self.cond.notify_all()
        if e.get("text") and e["event"] not in ("tx_start",):
            print(f"  [node {e['node']}] {e['text']}")

    def wait(self, pred, timeout):
        end = time.time() + timeout
        with self.cond:
            while not any(pred(e) for e in self.events):
                left = end - time.time()
                if left <= 0:
                    return None
                self.cond.wait(left)
            return next(e for e in self.events if pred(e))

    def since(self, start):
        return self.events[start:]


def wait_message(log, msg_id, timeout=240):
    return log.wait(lambda e: e["event"] == "message_status" and e.get("msg_id") == msg_id
                    and e["status"] in ("delivered", "failed"), timeout)


def scenario(name, log, a, b, action, check):
    print(f"\n=== {name} ===")
    start = len(log.events)
    sa, sb = a.stats.snapshot()["counters"], b.stats.snapshot()["counters"]
    t0 = time.time()
    outcome = action()
    time.sleep(2.5)                                # let late ACKs/duplicates arrive
    events = log.since(start)
    da = {k: v - sa[k] for k, v in a.stats.snapshot()["counters"].items()}
    db = {k: v - sb[k] for k, v in b.stats.snapshot()["counters"].items()}
    passed, detail = check(outcome, events, da, db)
    print(f"--> {'PASS' if passed else 'FAIL'}: {detail}  ({time.time() - t0:.1f} s)")
    return {"scenario": name, "passed": passed, "detail": detail,
            "seconds": round(time.time() - t0, 1), "node1": da, "node2": db}


def delivered_texts(events):
    return [e["message"] for e in events if e["event"] == "message_rx"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--rest", action="store_true")
    p.add_argument("--security", action="store_true")
    args = p.parse_args()

    mic, spk = default_devices()
    hub = AcousticLoopbackHub(mic, spk)
    from shout_lan.security import Security
    import os as _os
    key = _os.urandom(32)                       # a fresh shared key just for this test
    a = Engine(1, LoopbackAcousticChannel(hub), security=Security(key))
    b = Engine(2, LoopbackAcousticChannel(hub), security=Security(key))
    log = Log(a, b)
    a.start()
    b.start()
    time.sleep(1)
    results = []
    try:
        if args.security:
            text = "SECRET HELLO"
            for enc in (False, True):
                def run(enc=enc):
                    return wait_message(log, a.send_message(2, text, encrypt=enc))
                results.append(scenario(f"'{text}' {'ENCRYPTED (AES-GCM)' if enc else 'plain'}",
                    log, a, b, run,
                    lambda o, ev, da, db, enc=enc: (
                        o and o["status"] == "delivered" and delivered_texts(ev) == [text]
                        and all(e.get("encrypted") == enc for e in ev if e["event"] == "message_rx"),
                        f"status={o and o['status']}, delivered={delivered_texts(ev)}, "
                        f"data frames sent={da['data_frames_tx']}, seconds={o and o.get('seconds')}")))
            args.quick = True
            args.rest = True                       # skip the other scenarios
        if args.rest:
            args.quick = False
        # 1. normal
        def normal():
            return wait_message(log, a.send_message(2, "HELLO WORLD"))
        if not args.rest:
            results.append(scenario("Normal transmission", log, a, b, normal,
            lambda o, ev, da, db: (o and o["status"] == "delivered"
                                   and delivered_texts(ev) == ["HELLO WORLD"]
                                   and da["retransmissions"] == 0,
                                   f"status={o and o['status']}, delivered={delivered_texts(ev)}, "
                                   f"retransmissions={da['retransmissions']}")))

        # 2. corrupted DATA frame
        def corrupted():
            a.set_injection("corrupt_next_data")
            return wait_message(log, a.send_message(2, "CRC TEST"))
        if not args.rest:
            results.append(scenario("Corrupted DATA frame", log, a, b, corrupted,
            lambda o, ev, da, db: (o and o["status"] == "delivered" and db["crc_failures"] >= 1
                                   and da["timeouts"] >= 1 and da["retransmissions"] >= 1
                                   and delivered_texts(ev) == ["CRC TEST"],
                                   f"receiver CRC failures={db['crc_failures']}, sender timeouts="
                                   f"{da['timeouts']}, retransmissions={da['retransmissions']}, "
                                   f"delivered={delivered_texts(ev)}")))

        # 3. lost ACK -> duplicate, delivered exactly once
        def lost_ack():
            b.set_injection("drop_next_ack")
            return wait_message(log, a.send_message(2, "ACK LOSS TEST"))
        if not args.rest:
            results.append(scenario("Lost ACK (exactly-once delivery)", log, a, b, lost_ack,
            lambda o, ev, da, db: (o and o["status"] == "delivered" and db["duplicates"] >= 1
                                   and delivered_texts(ev) == ["ACK LOSS TEST"],
                                   f"duplicates at receiver={db['duplicates']}, delivered "
                                   f"{len(delivered_texts(ev))} time(s): {delivered_texts(ev)}")))

        if not args.quick and not args.security:
            # 4. multi-frame message
            text = "HELLO FROM COMPUTER A - THIS MESSAGE NEEDS THREE FRAMES!"

            def multi():
                return wait_message(log, a.send_message(2, text))
            results.append(scenario("3-frame message", log, a, b, multi,
                lambda o, ev, da, db: (o and o["status"] == "delivered"
                                       and delivered_texts(ev) == [text],
                                       f"status={o and o['status']}, delivered={delivered_texts(ev)}")))

            # 5. ping
            def ping():
                a.ping(2, count=3)
                return log.wait(lambda e: e["event"] == "ping_summary", 120)
            results.append(scenario("Acoustic ping x3", log, a, b, ping,
                lambda o, ev, da, db: (o is not None and o["received"] >= 2,
                                       f"{o and o.get('text')}")))

            # 6. max retries
            def nobody():
                return wait_message(log, a.send_message(9, "NOBODY HOME"), timeout=400)
            results.append(scenario("Maximum retries exhausted (node 9 absent)", log, a, b, nobody,
                lambda o, ev, da, db: (o and o["status"] == "failed" and da["retransmissions"] == 5,
                                       f"status={o and o['status']}, retransmissions="
                                       f"{da['retransmissions']}, timeouts={da['timeouts']}")))
    finally:
        a.stop()
        b.stop()

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    print("RTO after test:", round(a.timer.timeout(), 2), "s;  frame RTT:", a.stats.snapshot()["rtt"])
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"arq_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w") as f:
        json.dump({"kind": "Measured - acoustic (single-laptop loopback, 2 engines)",
                   "results": results, "stats_node1": a.stats.snapshot(),
                   "stats_node2": b.stats.snapshot(), "events": log.events}, f, indent=1)
    print("saved:", path)


if __name__ == "__main__":
    main()
