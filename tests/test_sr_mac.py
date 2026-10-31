"""
Milestone 6 tests: Selective Repeat (DoD C9-C9d), CSMA (C10), ALOHA (C11),
ALOHA simulation vs theory (C12). Engine tests use the SIMULATED channel.
"""

import time

import pytest

from shout_lan import aloha_sim, arq, frame as fr, mac
from shout_lan.channel import SimulatedAir, SimulatedChannel
from shout_lan.engine import Engine
from tests.test_engine import Recorder

SCALE = 0.2
LONG = "SELECTIVE REPEAT DEMO: this message is long enough to need four frames of 24 bytes!!"  # 84 B = 4 frames


def sr_frame(seq, flags=0, payload=b"x"):
    return fr.Frame(fr.SRDATA, 1, 2, seq=seq, payload=payload, flags=flags)


# ---------------------------------------------------------------- receiver logic (unit)
def test_out_of_order_frames_are_buffered_and_delivered_in_order():           # C9a
    r = arq.SelectiveRepeatReceiver()
    out = []
    out += r.accept(sr_frame(10, fr.F_FIRST, b"0"))
    out += r.accept(sr_frame(12, 0, b"2"))
    out += r.accept(sr_frame(13, 0, b"3"))
    assert [f.payload for f in out] == [b"0"]                 # 2 and 3 wait in the buffer
    assert r.sack() == (11, 0b110, True)                      # missing 11, holding 12 and 13
    out += r.accept(sr_frame(11, 0, b"1"))
    assert [f.payload for f in out] == [b"0", b"1", b"2", b"3"]
    assert r.rcv_base == 14 and r.buffer == {}


def test_duplicates_are_never_delivered_twice():                              # C9d
    r = arq.SelectiveRepeatReceiver()
    assert len(r.accept(sr_frame(5, fr.F_FIRST))) == 1
    assert r.accept(sr_frame(5, fr.F_FIRST)) == []            # resent copy of a delivered frame
    r.accept(sr_frame(7))
    assert r.accept(sr_frame(7)) == []                        # resent copy of a buffered frame
    assert r.duplicates == 2


def test_sequence_numbers_wrap_around():                                      # C9d
    r = arq.SelectiveRepeatReceiver()
    out = []
    for seq, flags in ((254, fr.F_FIRST), (0, 0), (1, 0), (255, 0)):
        out += r.accept(sr_frame(seq, flags, bytes([seq])))
    assert [f.seq for f in out] == [254, 255, 0, 1] and r.rcv_base == 2


def test_new_message_after_abandoned_one_is_not_stuck():
    r = arq.SelectiveRepeatReceiver()
    r.accept(sr_frame(20, fr.F_FIRST))
    r.accept(sr_frame(22))                                    # 21 never comes (sender gave up)
    out = r.accept(sr_frame(30, fr.F_FIRST | fr.F_LAST, b"new"))
    assert [f.payload for f in out] == [b"new"]


def test_sender_reads_sack_bitmap():
    frames = [sr_frame(s) for s in (100, 101, 102, 103)]
    acked = [False] * 4
    sack = fr.Frame(fr.SACK, 2, 1, seq=101, payload=bytes([0b110]), flags=fr.F_FIRST)
    newly = arq.SelectiveRepeatSender.apply_sack(sack, frames, acked, 0)
    assert newly == [100, 102, 103] and acked == [True, False, True, True]   # only 101 missing


# ---------------------------------------------------------------- engine scenarios (SIMULATED)
@pytest.fixture
def sr_pair():
    air = SimulatedAir(time_scale=SCALE, seed=11)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    b = Engine(2, SimulatedChannel(air), time_scale=SCALE)
    a.set_protocol("sr")
    ra, rb = Recorder(a), Recorder(b)
    a.start()
    b.start()
    yield a, b, ra, rb
    a.stop()
    b.stop()


def delivered(rec):
    return [e["message"] for e in rec.of("message_rx")]


def test_sr_normal_four_frames(sr_pair):
    a, b, ra, rb = sr_pair
    mid = a.send_message(2, LONG)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    assert delivered(rb) == [LONG]
    c = a.stats.snapshot()["counters"]
    assert c["data_frames_tx"] == 4                          # 84 B = 4 frames, all in one window
    assert c["retransmissions"] == 0
    assert len(rb.of("sack_tx")) == 1                         # ONE SACK for the whole window


def test_sr_lost_frame_only_that_frame_is_resent(sr_pair):                    # C9
    a, b, ra, rb = sr_pair
    a.set_injection("drop_frame", 2)
    mid = a.send_message(2, LONG)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    assert delivered(rb) == [LONG]
    resent = [e["seq"] for e in ra.of("retransmit")]
    dropped = [e["seq"] for e in ra.of("frame_dropped")]
    assert resent == dropped and len(resent) == 1             # exactly the lost frame
    assert rb.of("sr_buffered")                               # frames 3, 4 waited in the buffer


def test_sr_lost_sack_recovers_exactly_once(sr_pair):                         # C9b
    a, b, ra, rb = sr_pair
    b.set_injection("drop_next_sack")
    mid = a.send_message(2, LONG)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    time.sleep(0.5)
    assert rb.of("sack_dropped") and ra.of("timeout")
    assert a.stats.snapshot()["counters"]["retransmissions"] == 1    # only the oldest frame
    assert rb.of("duplicate")                                 # receiver already had it
    assert delivered(rb) == [LONG]                            # exactly once


def test_sr_lost_poll_frame_recovers(sr_pair):                                # C9c
    a, b, ra, rb = sr_pair
    a.set_injection("drop_frame", 4)                          # the POLL frame of the first burst
    mid = a.send_message(2, LONG)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    assert ra.of("timeout")
    assert delivered(rb) == [LONG]


def test_sr_is_faster_than_stop_and_wait_for_long_messages():
    air = SimulatedAir(time_scale=SCALE, seed=12)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    b = Engine(2, SimulatedChannel(air), time_scale=SCALE)
    ra = Recorder(a)
    a.start()
    b.start()
    try:
        times = {}
        for proto in ("sw", "sr"):
            a.set_protocol(proto)
            mid = a.send_message(2, LONG)
            times[proto] = ra.wait_for("message_status", msg_id=mid, status="delivered",
                                       timeout=120)["seconds"]
        assert times["sr"] < times["sw"]
    finally:
        a.stop()
        b.stop()


# ---------------------------------------------------------------- MAC
def test_csma_defers_while_another_node_transmits():                          # C10 (SIMULATED)
    air = SimulatedAir(time_scale=SCALE, seed=13)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    b = Engine(2, SimulatedChannel(air), time_scale=SCALE)
    c = Engine(3, SimulatedChannel(air), time_scale=SCALE)
    ra, rb = Recorder(a), Recorder(b)
    for e in (a, b, c):
        e.start()
    try:
        m1 = b.send_message(3, LONG)                          # b occupies the channel
        time.sleep(0.3)
        m2 = a.send_message(3, "WAIT FOR YOUR TURN")          # a must listen first
        ra.wait_for("message_status", msg_id=m2, status="delivered", timeout=120)
        rb.wait_for("message_status", msg_id=m1, status="delivered", timeout=120)
        assert ra.of("channel_busy")
        assert a.stats.snapshot()["counters"]["channel_busy_deferrals"] >= 1
    finally:
        for e in (a, b, c):
            e.stop()


def test_aloha_uses_random_backoff_after_missing_ack():                      # C11 (SIMULATED)
    air = SimulatedAir(time_scale=SCALE)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    a.set_mac(mac.ALOHA)
    ra = Recorder(a)
    a.start()
    try:
        mid = a.send_message(9, "NOBODY")                     # no ACK will ever come
        ra.wait_for("message_status", msg_id=mid, status="failed", timeout=200)
        backoffs = ra.of("backoff")
        assert len(backoffs) == 5 == a.stats.snapshot()["counters"]["retransmissions"]
        assert not ra.of("channel_busy")                      # ALOHA never listens first
    finally:
        a.stop()


def test_aloha_backoff_range():
    for attempt in range(1, 8):
        for _ in range(50):
            w = mac.aloha_backoff(attempt, frame_time=2.0)
            assert 0 <= w <= (2 ** min(attempt, 4) - 1) * 2.0


def test_aloha_simulation_matches_theory():                                   # C12
    assert abs(aloha_sim.simulate_pure(0.5, 20000) - aloha_sim.theory_pure(0.5)) < 0.03
    assert abs(aloha_sim.simulate_slotted(1.0, 20000) - aloha_sim.theory_slotted(1.0)) < 0.03
    sweep = aloha_sim.sweep()
    assert sweep["kind"] == "Simulation"
    best_pure = sweep["loads"][sweep["pure"].index(max(sweep["pure"]))]
    best_slot = sweep["loads"][sweep["slotted"].index(max(sweep["slotted"]))]
    assert 0.3 <= best_pure <= 0.7 and 0.7 <= best_slot <= 1.3
