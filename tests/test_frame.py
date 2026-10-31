"""Unit tests for CRC-16, parity, frame build/parse, fragmentation and the streaming receiver."""

import itertools

import numpy as np
import pytest

from shout_lan import frame as fr
from shout_lan.crc import crc16, flip_bits, parity_bit, parity_ok
from shout_lan.modem import Modem, bytes_to_bits
from shout_lan.receiver import FrameReceiver


# ---------------------------------------------------------------- CRC
def test_crc_standard_check_value():
    assert crc16(b"123456789") == 0x29B1


def _sample_frame_bytes():
    return fr.build(fr.Frame(fr.DATA, src=1, dst=2, seq=7, payload=b"HELLO WORLD 12345",
                             flags=fr.F_FIRST | fr.F_LAST))


def test_crc_detects_every_single_bit_error():
    data = _sample_frame_bytes()
    for pos in range(8, len(data) * 8):                 # every bit after SYNC
        with pytest.raises(fr.FrameError):
            fr.parse(flip_bits(data, [pos]))


def test_crc_detects_every_double_bit_error():
    data = _sample_frame_bytes()
    n = len(data) * 8
    for a, b in itertools.combinations(range(8, n), 2):
        with pytest.raises(fr.FrameError):
            fr.parse(flip_bits(data, [a, b]))


def test_crc_detects_all_bursts_up_to_16_bits():
    data = _sample_frame_bytes()
    n = len(data) * 8
    rng = np.random.default_rng(0)
    for length in range(1, 17):
        for start in range(8, n - length):
            # burst: first and last bits flipped, random bits in between
            inner = [start + i for i in range(1, length - 1) if rng.integers(0, 2)]
            positions = sorted({start, start + length - 1, *inner})
            with pytest.raises(fr.FrameError):
                fr.parse(flip_bits(data, positions))


def test_parity_misses_two_bit_error_but_crc_catches_it():
    data = _sample_frame_bytes()
    p = parity_bit(data)
    damaged = flip_bits(data, [40, 41])
    assert parity_ok(damaged, p)                       # parity is fooled
    with pytest.raises(fr.FrameError):                 # CRC is not
        fr.parse(damaged)
    assert not parity_ok(flip_bits(data, [40]), p)     # parity does catch 1 bit


# ---------------------------------------------------------------- frames
def test_build_parse_round_trip_all_fields():
    f = fr.Frame(fr.DATA, src=3, dst=9, seq=255, payload=bytes(range(24)),
                 flags=fr.F_LAST | fr.F_ENC, origin=1, final=12, ttl=2)
    raw = fr.build(f)
    assert len(raw) == fr.OVERHEAD + 24 == 35
    g = fr.parse(raw)
    assert (g.ftype, g.src, g.dst, g.seq, g.payload, g.flags, g.origin, g.final, g.ttl) == \
           (f.ftype, f.src, f.dst, f.seq, f.payload, f.flags, f.origin, f.final, f.ttl)


def test_ack_frame_is_11_bytes():
    assert len(fr.build(fr.Frame(fr.ACK, src=2, dst=1, seq=4))) == 11


@pytest.mark.parametrize("raw", [b"", b"\x7e\x10", b"\x00" * 20,
                                 bytes([0x7E, 0x10, 1, 2, 0, 200]) + b"\x00" * 10])
def test_malformed_frames_are_rejected_not_crashing(raw):
    with pytest.raises(fr.FrameError):
        fr.parse(raw)


def test_truncated_frame_rejected():
    raw = _sample_frame_bytes()
    with pytest.raises(fr.FrameError):
        fr.parse(raw[:-3])


def test_build_rejects_oversized_payload():
    with pytest.raises(ValueError):
        fr.build(fr.Frame(fr.DATA, 1, 2, payload=b"x" * 25))


# ---------------------------------------------------------------- fragmentation
def test_fragment_and_reassemble_three_frames():
    msg = b"HELLO FROM COMPUTER A, THIS IS SHOUT LAN!!!!!!!!"    # 48 bytes -> 2 frames
    msg = msg + b"+3"                                          # 50 bytes -> 3 frames
    pieces = fr.fragment(msg)
    assert len(pieces) == 3
    assert pieces[0][1] & fr.F_FIRST and pieces[-1][1] & fr.F_LAST
    r = fr.Reassembler()
    outputs = [r.add(p, f) for p, f in pieces]
    assert outputs[:2] == [None, None] and outputs[2] == msg


def test_empty_message_is_one_fragment():
    pieces = fr.fragment(b"")
    assert pieces == [(b"", fr.F_FIRST | fr.F_LAST)]


def test_first_flag_discards_abandoned_partial_message():
    r = fr.Reassembler()
    r.add(b"OLD", fr.F_FIRST)                 # sender gave up after this
    assert r.add(b"NEW", fr.F_FIRST | fr.F_LAST) == b"NEW"


# ---------------------------------------------------------------- streaming receiver
def _audio_for(frames, modem, gain=0.3, noise=0.003, seed=0):
    rng = np.random.default_rng(seed)
    parts = [np.zeros(7000)]
    for f in frames:
        parts.append(modem.modulate_bytes(fr.build(f)) * gain)
        parts.append(np.zeros(3000))
    audio = np.concatenate(parts)
    return audio + rng.normal(0, noise, len(audio))


def test_receiver_decodes_frames_from_small_chunks():
    modem = Modem()
    frames = [fr.Frame(fr.DATA, 1, 2, seq=i, payload=f"part {i}".encode()) for i in range(3)]
    audio = _audio_for(frames, modem)
    rx = FrameReceiver(modem)
    results = []
    for i in range(0, len(audio), 2048):                 # same chunk size as the microphone
        results += rx.feed(audio[i:i + 2048])
    assert [r["ok"] for r in results] == [True, True, True]
    assert [r["frame"].payload for r in results] == [b"part 0", b"part 1", b"part 2"]


@pytest.mark.parametrize("chunk", [256, 333, 1000, 2048, 4799])
def test_receiver_works_when_chunks_cut_the_preamble(chunk):
    # Regression (TESTING.md M2): chunk boundaries inside the preamble used to
    # make the receiver pick an early correlation peak -> wrong bit timing.
    modem = Modem()
    frames = [fr.Frame(fr.DATA, 1, 2, seq=i, payload=b"HELLO") for i in range(4)]
    audio = _audio_for(frames, modem, noise=0.01, seed=chunk)
    rx = FrameReceiver(modem)
    results = []
    for i in range(0, len(audio), chunk):
        results += rx.feed(audio[i:i + chunk])
    assert [r["ok"] for r in results] == [True] * 4


def test_receiver_reports_crc_failure_for_corrupted_frame():
    modem = Modem()
    good = fr.build(fr.Frame(fr.DATA, 1, 2, seq=1, payload=b"CORRUPT ME"))
    bad = flip_bits(good, [100])
    audio = np.concatenate([np.zeros(5000), modem.modulate_bytes(bad) * 0.3, np.zeros(5000)])
    results = FrameReceiver(modem).feed(audio)
    assert len(results) == 1 and not results[0]["ok"]
    assert "CRC mismatch" in results[0]["error"]
