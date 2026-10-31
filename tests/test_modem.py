"""Unit tests for the BFSK modem using synthetic audio (no sound hardware)."""

import numpy as np

from shout_lan.modem import Modem, bytes_to_bits, bits_to_bytes, goertzel_power


def test_bits_bytes_round_trip():
    data = b"HELLO\x00\xff"
    assert bytes_to_bits(b"H") == [0, 1, 0, 0, 1, 0, 0, 0]   # ASCII 'H' = 01001000
    assert bits_to_bytes(bytes_to_bits(data)) == data


def test_goertzel_picks_the_right_tone():
    fs = 48000
    t = np.arange(800) / fs
    tone = np.sin(2 * np.pi * 3000 * t)
    assert goertzel_power(tone, 3000, fs) > 100 * goertzel_power(tone, 2000, fs)


def _channel(signal, delay, noise, gain=0.3, seed=0):
    """Fake 'air': silence before the signal, quieter signal, background noise."""
    rng = np.random.default_rng(seed)
    audio = np.concatenate([np.zeros(delay), signal * gain, np.zeros(4800)])
    return audio + rng.normal(0, noise, len(audio))


def test_clean_round_trip_with_unknown_delay():
    modem = Modem()
    rng = np.random.default_rng(1)
    bits = list(rng.integers(0, 2, 256))
    audio = _channel(modem.modulate(bits), delay=12345, noise=0.001)
    found = modem.find_preamble(audio)
    assert found is not None
    got, _ = modem.demodulate_bits(audio, found[0], len(bits))
    assert got == bits


def test_noisy_round_trip():
    modem = Modem()
    rng = np.random.default_rng(2)
    bits = list(rng.integers(0, 2, 256))
    # noise std 0.05 vs tone amplitude 0.15 -> roughly 6 dB SNR per sample
    audio = _channel(modem.modulate(bits), delay=5000, noise=0.05, seed=3)
    found = modem.find_preamble(audio)
    assert found is not None
    got, _ = modem.demodulate_bits(audio, found[0], len(bits))
    errors = sum(a != b for a, b in zip(got, bits))
    assert errors <= 2


def test_no_false_preamble_in_noise():
    modem = Modem()
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 0.1, 48000 * 5)
    assert modem.find_preamble(noise) is None


def test_no_false_preamble_in_plain_bfsk_tones():
    # Our own data tones must not be mistaken for a frame start.
    modem = Modem()
    rng = np.random.default_rng(5)
    sig = modem.modulate(list(rng.integers(0, 2, 400)))
    body = sig[len(modem.preamble_wave) + modem.gap_samples:]
    assert modem.find_preamble(body) is None


def test_whitening_is_reversible_and_breaks_runs():
    from shout_lan.modem import whiten
    data = bytes(24)                                   # 192 zero bits in a row
    w = whiten(data)
    assert whiten(w) == data
    bits = bytes_to_bits(w)
    longest = max(len(r) for r in "".join(map(str, bits)).replace("1", " 1 ").split())
    assert longest <= 8                                 # no long single-tone runs


def test_equaliser_handles_a_weak_tone():
    # Tone 1 arrives 15 dB weaker than tone 0 (like the real laptop speaker).
    from scipy.signal import filtfilt, firwin
    modem = Modem()
    rng = np.random.default_rng(9)
    bits = [0] * 40 + [1] + [0] * 40 + [int(b) for b in rng.integers(0, 2, 100)]
    sig = modem.modulate(bits).astype(np.float64)
    band = firwin(301, [2600, 3400], fs=48000, pass_zero=False)
    weak = sig - (1 - 10 ** (-15 / 20)) * filtfilt(band, [1.0], sig)   # attenuate ~3 kHz (zero phase)
    audio = np.concatenate([np.zeros(3000), weak * 0.3, np.zeros(3000)])
    audio += rng.normal(0, 0.002, len(audio))
    found = modem.find_preamble(audio)
    gains = modem.estimate_gains(audio, found[0])
    assert gains[1] < gains[0]                          # it noticed tone 1 is weaker
    got, _ = modem.demodulate_bits(audio, found[0], len(bits), gains)
    assert got == bits


def test_snr_estimate_is_correct_for_known_signal():
    # A tone with power A^2/2 = 0.005 in white noise of variance 1e-4 per sample.
    # Within one 4320-sample Goertzel bin the noise power is sigma^2 / n * (normalisation),
    # so we just check that a 20 dB stronger tone reads ~20 dB higher than the noise.
    from shout_lan import calibration
    rng = np.random.default_rng(0)
    fs = 48000
    noise = rng.normal(0, 0.01, fs)
    nb = calibration.noise_powers(noise)
    n = calibration.tone_block_length()
    t = np.arange(n) / fs
    for amp_db in (0, 20):
        amp = 10 ** (amp_db / 20) * 0.01 * np.sqrt(2) / np.sqrt(n / 2)   # tone power = noise power in bin * 10^(dB/10)
        block = amp * np.sin(2 * np.pi * 2000 * t) + rng.normal(0, 0.01, n)
        snr = 10 * np.log10(goertzel_power(block, 2000, fs) / nb[2000])
        assert abs(snr - 10 * np.log10(1 + 10 ** (amp_db / 10))) < 3.5
