"""
SILENT tests of the acoustic code path (AcousticChannel, half-duplex muting,
carrier sensing, speaker-mode fallback, the hardware tools) using fake
speakers/microphones. No sound is played. These prove the code paths work;
they do NOT prove the real speaker->air->microphone link (see the hardware plan).
"""

import sys
import time

import pytest

from shout_lan import frame as fr
from shout_lan.engine import Engine
from tests.fake_audio import FakeAir, FakeMicrophone, FakeSpeaker, fake_acoustic_channel
from tests.test_engine import Recorder

SCALE = 0.25


@pytest.fixture
def acoustic_pair():
    air = FakeAir(time_scale=SCALE)
    a = Engine(1, fake_acoustic_channel(air, "A"), time_scale=SCALE)
    b = Engine(2, fake_acoustic_channel(air, "B"), time_scale=SCALE)
    ra, rb = Recorder(a), Recorder(b)
    a.start()
    b.start()
    yield a, b, ra, rb
    a.stop()
    b.stop()


def test_acoustic_path_delivers_and_labels_measurements(acoustic_pair):
    a, b, ra, rb = acoustic_pair
    mid = a.send_message(2, "ACOUSTIC PATH, SILENT")
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    assert rb.wait_for("message_rx")["message"] == "ACOUSTIC PATH, SILENT"
    assert a.stats.snapshot()["source"] == "Measured - acoustic"   # acoustic code path
    assert a.status()["speaker_mode"] == "exclusive"


def test_half_duplex_node_never_decodes_its_own_frames(acoustic_pair):
    # The fake microphone hears its own speaker (like a real laptop). Because of
    # the half-duplex mute, node 1 must never report receiving its own DATA.
    a, b, ra, rb = acoustic_pair
    mid = a.send_message(2, "HALF DUPLEX")
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=120)
    own = [e for e in ra.of("frame_rx") if e.get("frame", {}).get("src") == 1]
    assert own == []
    assert not ra.of("frame_rx", ok=False)          # and no CRC noise from its own echo


def test_acoustic_selective_repeat_with_lost_frame(acoustic_pair):
    a, b, ra, rb = acoustic_pair
    a.set_protocol("sr")
    a.set_injection("drop_frame", 2)
    text = "SELECTIVE REPEAT DEMO: this message is long enough to need four frames of 24 bytes!!"
    mid = a.send_message(2, text)
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=200)
    assert [e["message"] for e in rb.of("message_rx")] == [text]
    assert [e["seq"] for e in ra.of("retransmit")] == [e["seq"] for e in ra.of("frame_dropped")]


def test_carrier_sense_hears_real_band_energy():
    # CSMA on the ACOUSTIC path: node 1 measures the band energy of node 2's
    # (fake) sound through its microphone and defers.
    air = FakeAir(time_scale=SCALE)
    a = Engine(1, fake_acoustic_channel(air, "A"), time_scale=SCALE)
    b = Engine(2, fake_acoustic_channel(air, "B"), time_scale=SCALE)
    c = Engine(3, fake_acoustic_channel(air, "C"), time_scale=SCALE)
    ra, rb = Recorder(a), Recorder(b)
    for e in (a, b, c):
        e.start()
    try:
        time.sleep(1.5)                                  # let carrier sense learn the noise floor
        b.set_protocol("sr")
        m1 = b.send_message(3, "B IS TALKING FOR A WHILE - B IS TALKING FOR A WHILE - B IS TALKING")
        time.sleep(0.8)
        m2 = a.send_message(3, "WAIT")
        ra.wait_for("message_status", msg_id=m2, status="delivered", timeout=200)
        rb.wait_for("message_status", msg_id=m1, status="delivered", timeout=200)
        reasons = [e["reason"] for e in ra.of("channel_busy")]
        assert reasons, "node 1 never deferred"
    finally:
        for e in (a, b, c):
            e.stop()


def test_speaker_shared_fallback_is_reported():
    air = FakeAir(time_scale=SCALE)
    a = Engine(1, fake_acoustic_channel(air, "A", refuse_exclusive=True), time_scale=SCALE)
    ra = Recorder(a)
    a.start()
    try:
        assert a.status()["speaker_mode"] == "shared"
        assert any("exclusive mode refused" in e.get("text", "") for e in ra.of("error"))
    finally:
        a.stop()


def test_frame_test_tool_runs_silently(monkeypatch, capsys, tmp_path):
    """tools/frame_test.py with fake audio: all 13 frames valid, 3-fragment message reassembled."""
    import importlib
    sys.path.insert(0, "tools")
    import frame_test
    importlib.reload(frame_test)
    air = FakeAir(time_scale=0.05)
    mic, spk = FakeMicrophone(air, "M"), FakeSpeaker(air, "S")
    monkeypatch.setattr(frame_test, "Microphone", lambda *a, **k: mic)
    monkeypatch.setattr(frame_test, "Speaker", lambda *a, **k: spk)
    monkeypatch.setattr(frame_test, "default_devices", lambda: (None, None))
    monkeypatch.setattr(frame_test, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["frame_test.py"])
    frame_test.main()
    out = capsys.readouterr().out
    assert '"valid_frames": 13' in out and '"long_message_reassembled": true' in out, out[-800:]
