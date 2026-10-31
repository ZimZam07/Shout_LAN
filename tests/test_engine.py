"""
Engine + Stop-and-Wait tests on the SIMULATED channel (real modulated audio
samples, software "air", sped up 5x). They prove the protocol logic; they do
NOT prove the acoustic link (see tools/arq_test.py for that).
"""

import threading
import time

import pytest

from shout_lan import frame as fr
from shout_lan.channel import SimulatedAir, SimulatedChannel
from shout_lan.engine import Engine

SCALE = 0.2


class Recorder:
    """Collects every event an engine emits, and lets a test wait for one."""

    def __init__(self, engine):
        self.events = []
        self.cond = threading.Condition()
        engine.add_listener(self.on_event)

    def on_event(self, e):
        with self.cond:
            self.events.append(e)
            self.cond.notify_all()

    def of(self, name, **match):
        with self.cond:
            return [e for e in self.events
                    if e["event"] == name and all(e.get(k) == v for k, v in match.items())]

    def wait_for(self, name, timeout=60, **match):
        end = time.time() + timeout
        with self.cond:
            while True:
                found = [e for e in self.events
                         if e["event"] == name and all(e.get(k) == v for k, v in match.items())]
                if found:
                    return found[0]
                left = end - time.time()
                if left <= 0:
                    raise AssertionError(f"timed out waiting for {name} {match}")
                self.cond.wait(left)


@pytest.fixture
def pair():
    air = SimulatedAir(time_scale=SCALE, seed=1)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    b = Engine(2, SimulatedChannel(air), time_scale=SCALE)
    ra, rb = Recorder(a), Recorder(b)
    a.start()
    b.start()
    yield a, b, ra, rb
    a.stop()
    b.stop()


def finished(rec, msg_id):
    rec.wait_for("message_status", msg_id=msg_id, status="delivered", timeout=90) \
        if not rec.of("message_status", msg_id=msg_id, status="failed") else None
    return [e["status"] for e in rec.of("message_status", msg_id=msg_id)][-1]


def test_normal_delivery(pair):
    a, b, ra, rb = pair
    mid = a.send_message(2, "HELLO WORLD")
    ra.wait_for("message_status", msg_id=mid, status="delivered")
    msg = rb.wait_for("message_rx")
    assert msg["message"] == "HELLO WORLD" and msg["origin"] == 1
    assert a.stats.snapshot()["counters"]["retransmissions"] == 0
    assert "SIMULATED" in a.stats.snapshot()["source"]


def test_three_fragment_message(pair):
    a, b, ra, rb = pair
    text = "HELLO FROM COMPUTER A - THIS MESSAGE NEEDS THREE FRAMES!"
    mid = a.send_message(2, text)
    ra.wait_for("message_status", msg_id=mid, status="delivered")
    assert rb.wait_for("message_rx")["message"] == text
    assert len(ra.of("message_progress", msg_id=mid)) == 3


def test_corrupted_data_frame_is_retransmitted(pair):
    a, b, ra, rb = pair
    a.set_injection("corrupt_next_data")
    mid = a.send_message(2, "CRC TEST")
    ra.wait_for("message_status", msg_id=mid, status="delivered")
    assert rb.of("frame_rx", ok=False)                       # receiver saw the CRC failure
    assert "CRC mismatch" in rb.of("frame_rx", ok=False)[0]["error"]
    assert ra.of("timeout")                                  # sender timed out
    assert a.stats.snapshot()["counters"]["retransmissions"] == 1
    assert [e["message"] for e in rb.of("message_rx")] == ["CRC TEST"]


def test_lost_ack_gives_exactly_once_delivery(pair):
    a, b, ra, rb = pair
    b.set_injection("drop_next_ack")
    mid = a.send_message(2, "ACK LOSS TEST")
    ra.wait_for("message_status", msg_id=mid, status="delivered")
    time.sleep(0.5)
    assert rb.of("ack_dropped")
    assert rb.of("duplicate")                                # the resend was recognised
    assert [e["message"] for e in rb.of("message_rx")] == ["ACK LOSS TEST"]   # exactly once
    assert a.stats.snapshot()["counters"]["retransmissions"] == 1


def test_max_retries_reports_failure():
    air = SimulatedAir(time_scale=SCALE)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    ra = Recorder(a)
    a.start()
    try:
        mid = a.send_message(9, "NOBODY HOME")              # node 9 does not exist
        ra.wait_for("message_status", msg_id=mid, status="failed", timeout=120)
        assert not ra.of("message_status", msg_id=mid, status="delivered")
        c = a.stats.snapshot()["counters"]
        assert c["retransmissions"] == 5 and c["timeouts"] == 6 and c["messages_failed"] == 1
    finally:
        a.stop()


def test_ping_measures_rtt(pair):
    a, b, ra, rb = pair
    a.ping(2, count=3)
    s = ra.wait_for("ping_summary", timeout=90)
    assert s["sent"] == 3 and s["received"] == 3 and s["loss"] == 0
    assert 0 < s["min"] <= s["avg"] <= s["max"]


def test_hello_discovery_lists_only_real_nodes(pair):
    a, b, ra, rb = pair
    assert a.node_table() == {}
    a.hello()
    ra.wait_for("frame_rx", ok=True)
    time.sleep(0.3)
    assert list(a.node_table()) == [2]                      # only the node that answered


def test_messages_to_other_nodes_are_ignored():
    air = SimulatedAir(time_scale=SCALE)
    a, b, c = (Engine(n, SimulatedChannel(air), time_scale=SCALE) for n in (1, 2, 3))
    rc = Recorder(c)
    for e in (a, b, c):
        e.start()
    try:
        ra = Recorder(a)
        mid = a.send_message(2, "ONLY FOR B")
        ra.wait_for("message_status", msg_id=mid, status="delivered")
        assert not rc.of("message_rx")
        assert rc.of("frame_rx", for_me=False)
    finally:
        for e in (a, b, c):
            e.stop()
