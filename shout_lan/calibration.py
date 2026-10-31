"""
calibration.py - measures the real acoustic channel before we use it.

1. Noise floor: how loud is the room when nobody transmits? (also used later
   by CSMA to decide "channel busy" vs "channel idle").
2. Tone sweep: play test tones one after another and measure how strongly
   each frequency arrives at the microphone (speakers and microphones do
   not hear all pitches equally well - this is attenuation in the syllabus).
3. Suggest the two BFSK frequencies with the best signal-to-noise ratio.
"""

import numpy as np

from shout_lan import config
from shout_lan.audio_io import Microphone, record_while_playing
from shout_lan.modem import goertzel_power

SWEEP_FREQS = list(range(1000, 6001, 250))     # Hz
SWEEP_TONE_TIME = 0.15                         # seconds per test tone
SWEEP_GAP_TIME = 0.05                          # silence between test tones


def tone_block_length():
    """Samples in one measured tone block (same length must be used for noise)."""
    n = int(config.SAMPLE_RATE * SWEEP_TONE_TIME)
    return (n * 3) // 5


def noise_powers(audio):
    """
    Noise power at every sweep frequency, measured in blocks of the SAME length
    as the tone measurements and averaged. (Goertzel noise power depends on the
    block length, so comparing a 1 s noise block with a 90 ms tone block would
    make the SNR look ~10 dB better than it really is.)
    """
    n = tone_block_length()
    blocks = [audio[i:i + n] for i in range(0, len(audio) - n + 1, n)] or [audio]
    return {f: float(np.mean([goertzel_power(b, f, config.SAMPLE_RATE) for b in blocks]))
            for f in SWEEP_FREQS}


def measure_noise(mic_device=None, exclusive=True, seconds=1.0):
    """Record the quiet room and return (rms_level, power_per_frequency dict)."""
    import time
    mic = Microphone(mic_device, exclusive=exclusive)
    mic.start()
    try:
        time.sleep(seconds)
    finally:
        mic.stop()
    audio = mic.read_available()[int(0.2 * config.SAMPLE_RATE):]   # skip the start-up transient
    if len(audio) == 0:
        raise RuntimeError("Microphone returned no audio")
    rms = float(np.sqrt(np.mean(audio * audio)))
    return rms, noise_powers(audio), audio


def band_energy(audio, freqs):
    """Average tone power at the given frequencies - used for CSMA carrier sensing."""
    if len(audio) == 0:
        return 0.0
    return float(np.mean([goertzel_power(audio, f, config.SAMPLE_RATE) for f in freqs]))


def make_sweep(volume=config.TX_VOLUME):
    """One test tone per frequency in SWEEP_FREQS, separated by short silences."""
    fs = config.SAMPLE_RATE
    n = int(fs * SWEEP_TONE_TIME)
    t = np.arange(n) / fs
    fade = np.hanning(n) ** 0.25
    gap = np.zeros(int(fs * SWEEP_GAP_TIME))
    parts = []
    for f in SWEEP_FREQS:
        parts.append(np.sin(2 * np.pi * f * t) * fade)
        parts.append(gap)
    return (np.concatenate(parts) * volume).astype(np.float32)


def run_sweep(mic_device=None, speaker_device=None, exclusive=True, volume=config.TX_VOLUME):
    """
    Play the sweep, find where it starts in the recording (using the strongest
    onset of the first tone) and measure each tone's received power.
    Returns {freq: power}.
    """
    sweep = make_sweep(volume)
    audio, _ = record_while_playing(sweep, mic_device, speaker_device, exclusive, lead=0.3)
    return analyse_sweep(audio)


def analyse_sweep(audio):
    """Find the recorded sweep and measure every test tone's received power -> {freq: power}."""
    fs = config.SAMPLE_RATE
    n = int(fs * SWEEP_TONE_TIME)
    step = n + int(fs * SWEEP_GAP_TIME)

    # Locate the sweep: slide over the first second and pick the offset where
    # the first tone's frequency is strongest.
    first = SWEEP_FREQS[0]
    best_off, best_p = 0, -1.0
    for off in range(0, int(fs * 1.0), 240):
        p = goertzel_power(audio[off:off + n // 2], first, fs)
        if p > best_p:
            best_off, best_p = off, p
    results = {}
    for i, f in enumerate(SWEEP_FREQS):
        start = best_off + i * step + n // 5
        block = audio[start:start + tone_block_length()]
        results[f] = goertzel_power(block, f, fs) if len(block) else 0.0
    return results


def snr_table(tone_powers, noise_powers):
    """Signal-to-noise ratio in dB for every swept frequency."""
    table = {}
    for f, p in tone_powers.items():
        noise = max(noise_powers.get(f, 1e-15), 1e-15)
        table[f] = 10 * np.log10(max(p, 1e-15) / noise)
    return table


def ber_test(modem, mic_device=None, speaker_device=None, exclusive=True, trials=3, n_bits=128, seed=0):
    """
    Send known random bits through the real speaker -> mic and count errors.
    Returns (bit_errors, total_bits, preambles_detected). This is the most
    honest link-quality measurement: it uses exactly the real modem.
    """
    rng = np.random.default_rng(seed)
    errors = total = detected = 0
    for _ in range(trials):
        bits = [int(b) for b in rng.integers(0, 2, n_bits)]
        audio, _ = record_while_playing(modem.modulate(bits), mic_device, speaker_device,
                                        exclusive, lead=0.2, tail=0.3)
        total += n_bits
        found = modem.find_preamble(audio)
        if found is None:
            errors += n_bits
            continue
        detected += 1
        got, _ = modem.demodulate_bits(audio, found[0], n_bits)
        errors += sum(a != b for a, b in zip(got, bits)) + (n_bits - len(got))
    return errors, total, detected


def candidate_pairs(snr_db, count=3):
    """
    The sweep alone is NOT reliable for choosing tones (room echoes create
    notches that change from moment to moment - see TESTING.md M1), so we use
    it only to shortlist candidates; ber_test() makes the final choice.
    """
    pairs = [(config.FREQ_0, config.FREQ_1)]
    best = suggest_pair(snr_db)
    if best and best not in pairs:
        pairs.append(best)
    for f0, f1 in [(1750, 2750), (2500, 3500), (3000, 4000)]:
        if len(pairs) >= count + 1:
            break
        if (f0, f1) not in pairs:
            pairs.append((f0, f1))
    return pairs


def suggest_pair(snr_db, min_spacing=500):
    """
    Choose (freq0, freq1) for BFSK: both must have a good SNR, they must be at
    least `min_spacing` Hz apart, and their received levels should be similar
    (so neither tone dominates when echoes mix them).
    """
    freqs = sorted(snr_db)
    best, best_score = None, -1e9
    for i, a in enumerate(freqs):
        for b in freqs[i + 1:]:
            if b - a < min_spacing:
                continue
            weaker = min(snr_db[a], snr_db[b])
            imbalance = abs(snr_db[a] - snr_db[b])
            score = weaker - 0.5 * imbalance
            if score > best_score:
                best, best_score = (a, b), score
    return best
