"""Noise Shield (Hamming(7,4) + interleaving, down-chirp signalling) and the interference generator.
Synthetic audio, fake audio and the SIMULATED channel only - no sound is played."""

import time

import numpy as np
import pytest

from shout_lan import fec, frame as fr
from shout_lan.channel import SimulatedAir, SimulatedChannel
from shout_lan.engine import Engine
from shout_lan.modem import Modem, whiten
from shout_lan.receiver import FrameReceiver


def _frame(payload=b"HELLO", seq=7):
    return fr.Frame(fr.DATA, 1, 2, seq=seq, payload=payload, flags=fr.F_FIRST | fr.F_LAST)


def _air(sig, noise=0.002, seed=0, lead=6000):
    rng = np.random.default_rng(seed)
    audio = np.concatenate([np.zeros(lead), sig * 0.3, np.zeros(48000)])
    return (audio + rng.normal(0, noise, len(audio))).astype(np.float32)


# ---------------------------------------------------------------- the code itself
def test_hamming_corrects_every_single_bit_error():
    for n in range(16):
        word = fec.encode_nibble(n)
        assert fec.syndrome(word) == 0
        for pos in range(7):
            bad = list(word)
            bad[pos] ^= 1
            assert fec.syndrome(bad) == pos + 1             # the syndrome points at the wrong bit
            assert fec.decode_word_hard(bad)[0] == n


def test_coded_frame_round_trip_and_size():
    raw = fr.build(_frame(b"SIXTEEN"))
    bits = fec.encode_frame(raw)
    assert len(bits) == fec.coded_bits(len(raw)) == 14 * len(raw)
    nh = fec.header_bits()
    head, _ = fec.decode_block(bits[:nh])
    body, _ = fec.decode_block(bits[nh:])
    assert head + body == raw
    assert fr.total_length(head) == len(raw)              # header decodable on its own


def test_interleaving_turns_a_burst_into_single_errors():
    raw = fr.build(_frame(b"BURST TEST PAYLOAD"))
    bits = fec.encode_frame(raw)
    nh = fec.header_bits()
    body = list(bits[nh:])
    n_words = len(body) // 7
    for k in range(40, 40 + n_words):                     # a burst as long as the block width
        body[k] ^= 1
    decoded, info = fec.decode_block(body, soft=False)
    assert decoded == raw[fec.HEADER_BYTES:]
    assert info["repaired"] == n_words


def test_modem_round_trip_with_injected_bit_errors():
    m = Modem()
    raw = fr.build(_frame(b"REPAIR ME"))
    bits = m.frame_bits(raw, shield=True)
    rng = np.random.default_rng(3)
    flips = rng.choice(len(bits), 6, replace=False)       # 6 random on-air errors
    for k in flips:
        bits[k] ^= 1
    res = FrameReceiver(m).feed(_air(m.modulate(bits, shield=True)))
    assert len(res) == 1 and res[0]["ok"] and res[0]["shield"]
    assert res[0]["raw"] == raw
    assert res[0]["bits_repaired"] >= 6
    assert all(0 <= p < len(raw) * 8 for p in res[0]["repaired_at"])
    assert len(res[0]["bit_conf"]) == len(raw) * 8


def test_plain_frame_with_the_same_errors_fails_the_crc():
    m = Modem()
    raw = fr.build(_frame(b"REPAIR ME"))
    bits = m.frame_bits(raw)
    for k in (60, 80):                                    # payload area
        bits[k] ^= 1
    res = FrameReceiver(m).feed(_air(m.modulate(bits)))
    assert len(res) == 1 and not res[0]["ok"] and not res[0]["shield"]
    assert "CRC mismatch" in res[0]["error"]
    assert res[0]["crc_rx"] != res[0]["crc_calc"]
    assert res[0]["damaged_frame"].src == 1               # header still readable for the monitor


def test_both_formats_back_to_back_are_told_apart():
    m = Modem()
    a = fr.build(_frame(b"PLAIN", seq=1))
    b = fr.build(_frame(b"SHIELDED", seq=2))
    c = fr.build(_frame(b"PLAIN AGAIN", seq=3))
    sig = np.concatenate([m.modulate_bytes(a), m.modulate_bytes(b, shield=True),
                          m.modulate_bytes(c)])
    rx = FrameReceiver(m)
    res = []
    audio = _air(sig)
    for i in range(0, len(audio), 2048):                   # arrives in microphone-sized chunks
        res += rx.feed(audio[i:i + 2048])
    assert [r["ok"] for r in res] == [True, True, True]
    assert [r["shield"] for r in res] == [False, True, False]
    assert [r["raw"] for r in res] == [a, b, c]


def test_up_and_down_chirps_do_not_trigger_each_other():
    m = Modem()
    down = _air(m.shield_preamble_wave.astype(np.float64))
    up = _air(m.preamble_wave.astype(np.float64))
    assert m.find_preamble(down) is None and m.find_preamble(up, shield=True) is None
    assert m.find_frame_start(down)[2] is True and m.find_frame_start(up)[2] is False


def test_detection_ignores_low_frequency_rumble():
    # the MacBook finding: idle mic noise is almost all below 300 Hz and as loud as the frame
    m = Modem()
    rng = np.random.default_rng(1)
    raw = fr.build(_frame())
    sig = m.modulate_bytes(raw).astype(np.float64)
    t = np.arange(len(sig) + 60000) / 48000
    rumble = 0.08 * np.sin(2 * np.pi * 60 * t) + 0.05 * np.sin(2 * np.pi * 150 * t + 1)
    audio = rumble.copy()
    audio[6000:6000 + len(sig)] += sig * 0.1
    audio += rng.normal(0, 0.001, len(audio))
    res = FrameReceiver(m).feed(audio.astype(np.float32))
    assert len(res) == 1 and res[0]["ok"] and res[0]["raw"] == raw


def test_no_false_detections_in_generated_noise():
    from shout_lan.audio_io import NoiseGenerator
    m = Modem()
    noise = NoiseGenerator(seed=5).block(48000 * 10, 1.0)
    assert m.find_frame_start(noise) is None


# ---------------------------------------------------------------- engine, SIMULATED channel
class Rec:
    def __init__(self, engine):
        self.events = []
        engine.add_listener(self.events.append)

    def of(self, name, **match):
        return [e for e in list(self.events) if e["event"] == name
                and all(e.get(k) == v for k, v in match.items())]

    def wait_for(self, name, timeout=60, **match):
        end = time.time() + timeout
        while time.time() < end:
            found = self.of(name, **match)
            if found:
                return found[0]
            time.sleep(0.05)
        raise AssertionError(f"{name} {match} not seen")


@pytest.fixture
def pair():
    air = SimulatedAir(noise=0.002, time_scale=0.25, seed=1)
    a = Engine(1, SimulatedChannel(air), time_scale=0.25)
    b = Engine(2, SimulatedChannel(air), time_scale=0.25)
    ra, rb = Rec(a), Rec(b)
    a.start()
    b.start()
    yield air, a, b, ra, rb
    a.stop()
    b.stop()


def test_shield_switch_and_reply_in_kind(pair):
    air, a, b, ra, rb = pair
    assert a.shield is False and b.shield is False
    a.set_shield(True)
    assert ra.of("shield_set", value=True)
    mid = a.send_message(2, "SHIELDED HI")
    done = ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=90)
    assert done
    data_tx = ra.of("frame_tx")[0]
    assert data_tx["shield"] is True and data_tx["bits_on_air"] == 14 * data_tx["bytes"]
    assert rb.of("frame_rx", ok=True)[0]["shield"] is True
    assert rb.of("preamble", shield=True)
    ack_tx = [e for e in rb.of("frame_tx") if e["frame"]["type"] == "ACK"][0]
    assert ack_tx["shield"] is True                      # B answered in kind; B's own switch is OFF
    assert b.shield is False
    ack_rx = [e for e in ra.of("frame_rx", ok=True) if e["frame"]["type"] == "ACK"][0]
    assert ack_rx["shield"] is True
    c = b.stats.snapshot()["counters"]
    assert c["shield_frames_rx"] >= 1 and c["shield_frames_tx"] >= 1
    st = a.status()
    assert st["shield"] is True and st["shield_info"]["code"] == "Hamming(7,4)"
    assert st["noise"] == {"on": False, "level": 0.0}


def test_plain_exchange_stays_plain(pair):
    air, a, b, ra, rb = pair
    mid = a.send_message(2, "PLAIN HI")
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=60)
    assert all(e["shield"] is False for e in ra.of("frame_tx") + rb.of("frame_tx"))
    rx = rb.of("frame_rx", ok=True)[0]
    assert rx["bits_repaired"] == 0 and rx["crc_rx"] == rx["crc_calc"]
    assert isinstance(rx["snr_db"], float) and len(rx["bit_conf"]) == 8 * len(rx["raw"].split())


def test_simulated_noise_generator_hurts_plain_and_shield_survives(pair):
    air, a, b, ra, rb = pair
    a.set_noise(True, 0.35)
    ev = ra.of("noise_set")[0]
    assert ev["on"] is True and "SIMULATED" in ev["text"] and "generated" in ev["text"].lower()
    assert a.noise == {"on": True, "level": 0.35} and b.noise["on"] is True
    # plain frames through heavy simulated noise: count CRC rejections at B
    m = Modem()
    noisy = SimulatedChannel(air)
    noisy.np_rng = np.random.default_rng(0)
    raw = fr.build(_frame(b"NOISY 16B", seq=5))
    plain_ok = shield_ok = 0
    for i in range(6):
        for shield in (False, True):
            noisy._hear(m.modulate_bytes(raw, shield) * air.gain)
            res = FrameReceiver(m).feed(np.concatenate([np.zeros(4800, np.float32), noisy.read()]))
            ok = any(r["ok"] and r["raw"] == raw for r in res)
            plain_ok += ok and not shield
            shield_ok += ok and shield
    assert shield_ok > plain_ok
    a.set_noise(False)
    assert a.noise["on"] is False


def test_timeout_and_retransmit_events_have_contract_fields(pair):
    air, a, b, ra, rb = pair
    b.set_injection("drop_next_ack")
    mid = a.send_message(2, "ACK LOSS")
    ra.wait_for("message_status", msg_id=mid, status="delivered", timeout=90)
    t = ra.of("timeout")[0]
    r = ra.of("retransmit")[0]
    assert {"seq", "msg_id", "attempt", "waited"} <= set(t)
    assert {"seq", "msg_id", "attempt"} <= set(r)
    assert t["attempt"] == 1 and r["attempt"] == 2


# ---------------------------------------------------------------- fake audio (real acoustic code path)
def test_noise_generator_on_the_fake_acoustic_path():
    from tests.fake_audio import FakeAir, fake_acoustic_channel
    air = FakeAir(time_scale=0.25)
    ch = fake_acoustic_channel(air, "laptop")
    e = Engine(1, ch, time_scale=0.25)
    rec = Rec(e)
    e.start()
    try:
        ch.mic.read_available()
        e.set_noise(True, 0.5)
        assert e.noise == {"on": True, "level": 0.5}
        ev = rec.of("noise_set")[0]
        assert "generated" in ev["text"].lower() and "SIMULATED" not in ev["text"]
        time.sleep(0.3)
        heard = ch.mic.read_available()
        assert len(heard) and np.sqrt(np.mean(heard ** 2)) > 0.01   # noise really reaches the mic
        e.set_noise(False)
        assert e.noise["on"] is False
    finally:
        e.stop()


def test_speaker_mixer_keeps_frame_timing_exact():
    """The real Speaker mixer with a fake output stream: the frame's samples come out
    contiguous and unchanged on top of the noise (no gaps, no jumps)."""
    from shout_lan import audio_io

    class FakeStream:
        latency = 0.0

        def __init__(self):
            self.out = []

        def write(self, data):
            self.out.append(np.array(data[:, 0]))
            time.sleep(len(data) / 48000 / 20)       # 20x faster than real time
            return False

    real_check = audio_io.check_device
    audio_io.check_device = lambda device, kind: None   # no sound card is touched
    try:
        spk = audio_io.Speaker(None)
    finally:
        audio_io.check_device = real_check
    spk.stream = FakeStream()
    spk.noise_gen = audio_io.NoiseGenerator(seed=1)
    spk.set_noise(True, 0.0001)                       # tiny noise so the frame is easy to find
    time.sleep(0.05)
    frame = (0.5 + 0.2 * np.sin(np.arange(30000) * 0.3)).astype(np.float32)   # never near 0
    spk.play(frame)
    spk.set_noise(False, 0)
    time.sleep(0.1)
    out = np.concatenate(spk.stream.out)
    start = int(np.argmax(out > 0.25))
    assert np.max(np.abs(out[start:start + len(frame)] - frame)) < 0.01   # every sample in place
    assert np.all(np.abs(out[start + len(frame):start + len(frame) + 100]) < 0.01)
    assert start > 0                                   # noise blocks were written before it
