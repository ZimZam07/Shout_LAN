"""
Milestone 7: discovery, routing table, multi-hop relay, TTL (DoD C14, C15).
SIMULATED channel with a range model: node 1 and node 3 cannot hear each
other, node 2 is in the middle and can hear both (A - B - C).
"""

import time

import pytest

from shout_lan import frame as fr
from shout_lan.channel import SimulatedAir, SimulatedChannel
from shout_lan.engine import Engine
from shout_lan.security import Security
from tests.test_engine import Recorder

SCALE = 0.2
LINE = {"n1": ["n2"], "n2": ["n1", "n3"], "n3": ["n2"]}      # who can hear whom


def line_of_three(security=None):
    air = SimulatedAir(time_scale=SCALE, seed=21, hears=LINE)
    nodes = [Engine(i, SimulatedChannel(air, f"n{i}"), time_scale=SCALE,
                    security=Security(security) if security else None) for i in (1, 2, 3)]
    recs = [Recorder(n) for n in nodes]
    for n in nodes:
        n.start()
    return nodes, recs


@pytest.fixture
def line():
    nodes, recs = line_of_three()
    yield nodes, recs
    for n in nodes:
        n.stop()


def test_out_of_range_without_route_fails(line):
    (a, b, c), (ra, rb, rc) = line
    mid = a.send_message(3, "DIRECT")
    ra.wait_for("message_status", msg_id=mid, status="failed", timeout=120)
    assert not rc.of("message_rx")


def test_relay_through_middle_node(line):                                   # C15
    (a, b, c), (ra, rb, rc) = line
    a.set_route(3, 2)
    mid = a.send_message(3, "HELLO COMPUTER C")
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    got = rc.wait_for("message_rx", timeout=120)
    assert got["message"] == "HELLO COMPUTER C" and got["origin"] == 1 and got["hops"] == 2
    assert rb.of("forwarding") and not rb.of("message_rx")          # B relays, B doesn't read it
    assert b.stats.snapshot()["counters"]["forwarded"] == 1
    # every frame C received came from B (link source), but says origin = A
    data = [e["frame"] for e in rc.of("frame_rx", ok=True) if e["frame"]["type"] == "DATA"]
    assert data and all(f["src"] == 2 and f["origin"] == 1 and f["final"] == 3 for f in data)
    assert all(f["ttl"] == fr.DEFAULT_TTL - 1 for f in data)        # TTL decremented by B


def test_relay_with_selective_repeat_multi_frame(line):
    (a, b, c), (ra, rb, rc) = line
    a.set_route(3, 2)
    a.set_protocol("sr")
    b.set_protocol("sr")
    text = "A LONGER MESSAGE RELAYED HOP BY HOP THROUGH NODE TWO, FOUR FRAMES LONG!!!!!!!!!!!"
    mid = a.send_message(3, text)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=200)
    assert rc.wait_for("message_rx", timeout=200)["message"] == text


def test_routing_loop_is_stopped_by_ttl(line):
    (a, b, c), (ra, rb, rc) = line
    a.set_route(9, 2)              # node 9 doesn't exist; A and B point at each other
    b.set_route(9, 1)
    a.send_message(9, "LOOP")
    expired = None
    end = time.time() + 200
    while time.time() < end and expired is None:
        expired = (ra.of("ttl_expired") or rb.of("ttl_expired") or [None])[0]
        time.sleep(0.2)
    assert expired is not None, "TTL never expired"
    hops = a.stats.snapshot()["counters"]["forwarded"] + b.stats.snapshot()["counters"]["forwarded"]
    assert hops == fr.DEFAULT_TTL - 1                     # 4 -> 1, then dropped


def test_encrypted_message_is_relayed_without_decryption():
    nodes, recs = line_of_three(security=bytes(range(32)))
    (a, b, c), (ra, rb, rc) = nodes, recs
    try:
        a.set_route(3, 2)
        mid = a.send_message(3, "RELAYED SECRET", encrypt=True)
        ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
        got = rc.wait_for("message_rx", timeout=120)
        assert got["message"] == "RELAYED SECRET" and got["encrypted"]
        assert not rb.of("message_rx") and not rb.of("security_reject")   # B never decrypted
        on_air = "".join(e["frame"]["payload_hex"] for e in rb.of("frame_rx", ok=True)).replace(" ", "")
        assert b"RELAYED SECRET".hex() not in on_air
    finally:
        for n in nodes:
            n.stop()


def test_discovery_only_lists_nodes_in_range(line):                          # C14
    (a, b, c), (ra, rb, rc) = line
    a.hello()
    ra.wait_for("frame_rx", ok=True, timeout=60)
    time.sleep(1.0)
    assert list(a.node_table()) == [2]                     # node 3 is out of range: not listed
    b.hello()
    time.sleep(3.0)
    assert sorted(b.node_table()) == [1, 3]
