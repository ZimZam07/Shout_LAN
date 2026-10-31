"""
modem.py - PHYSICAL LAYER: turns bits into sound and sound back into bits.

  modulate()        bits  -> audio samples   (BFSK, used by the sender)
  find_preamble()   audio -> where a frame starts (synchronisation)
  demodulate_bits() audio -> bits            (Goertzel tone detection)

BFSK (Binary Frequency Shift Keying): every bit is a short tone.
    bit 0 -> FREQ_0 Hz          bit 1 -> FREQ_1 Hz
This is the same "digital data over an analogue signal" idea as a modem.
"""

import numpy as np
from scipy.signal import fftconvolve, lfilter

from shout_lan import config


# ---------------------------------------------------------------- bit helpers
def bytes_to_bits(data):
    """b'A' -> [0,1,0,0,0,0,0,1]  (most significant bit first, like the ASCII tables)."""
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def bits_to_bytes(bits):
    """[0,1,0,0,0,0,0,1] -> b'A'. Extra bits that don't fill a whole byte are ignored."""
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


# ---------------------------------------------------------------- Goertzel
def goertzel_power(block, freq, sample_rate):
    """
    How strong is ONE frequency inside a short block of audio?

    The Goertzel algorithm is a cheap way to compute a single DFT/FFT bin.
    We only ever need two frequencies (FREQ_0 and FREQ_1), so it is much
    simpler than running a full FFT.

    Recurrence:  s[n] = x[n] + 2*cos(w)*s[n-1] - s[n-2]
    Power:       s[N-1]^2 + s[N-2]^2 - 2*cos(w)*s[N-1]*s[N-2]
    (lfilter runs the recurrence quickly in C instead of a slow Python loop.)
    """
    n = len(block)
    if n < 2:
        return 0.0
    w = 2.0 * np.pi * freq / sample_rate
    coeff = 2.0 * np.cos(w)
    s = lfilter([1.0], [1.0, -coeff, 1.0], block)
    s1, s2 = s[-1], s[-2]
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return float(power) / (n * n)          # normalise so block length doesn't matter


class Modem:
    """Holds the physical-layer settings and does modulation / demodulation."""

    def __init__(self, sample_rate=config.SAMPLE_RATE, freq0=config.FREQ_0,
                 freq1=config.FREQ_1, bit_time=config.BIT_TIME,
                 volume=config.TX_VOLUME, threshold=config.DETECT_THRESHOLD):
        self.sample_rate = sample_rate
        self.freq0 = freq0
        self.freq1 = freq1
        self.bit_time = bit_time
        self.volume = volume
        self.threshold = threshold

        self.samples_per_bit = int(round(sample_rate * bit_time))
        self.gap_samples = int(round(sample_rate * config.GAP_TIME))
        self.preamble_wave = self._make_preamble()

    # ------------------------------------------------------------ sender side
    def _make_preamble(self):
        """
        A linear chirp (pitch slides from PREAMBLE_F_START to PREAMBLE_F_END).
        A chirp is used because it looks very different from speech, noise
        and our own BFSK tones, so the receiver can find it reliably and very
        precisely in time.
        """
        n = int(round(self.sample_rate * config.PREAMBLE_TIME))
        t = np.arange(n) / self.sample_rate
        f0, f1 = config.PREAMBLE_F_START, config.PREAMBLE_F_END
        k = (f1 - f0) / config.PREAMBLE_TIME
        wave = np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))
        wave *= np.hanning(n) ** 0.25     # soft start/end so the speaker doesn't "click"
        return wave.astype(np.float32)

    def modulate(self, bits):
        """
        Bits -> audio samples ready for the speaker.

        Layout:  [ preamble chirp ][ short gap ][ bit 0 ][ bit 1 ] ... [ tail ]

        Continuous-phase FSK: the sine wave's phase carries on from one bit to
        the next, so switching between the two tones doesn't make clicks.
        """
        freqs = np.where(np.asarray(bits, dtype=np.int8) == 1, self.freq1, self.freq0)
        per_sample_freq = np.repeat(freqs, self.samples_per_bit).astype(np.float64)
        phase = 2 * np.pi * np.cumsum(per_sample_freq) / self.sample_rate
        tones = np.sin(phase)

        # short fade in / out of the whole bit stream (no clicks)
        ramp = min(240, len(tones) // 2)
        if ramp > 0:
            fade = np.linspace(0.0, 1.0, ramp)
            tones[:ramp] *= fade
            tones[-ramp:] *= fade[::-1]

        gap = np.zeros(self.gap_samples)
        tail = np.zeros(self.samples_per_bit * 2)
        signal = np.concatenate([self.preamble_wave, gap, tones, tail])
        return (signal * self.volume).astype(np.float32)

    def airtime(self, n_bits):
        """Seconds of sound needed to send n_bits (preamble + gap + bits)."""
        return config.PREAMBLE_TIME + config.GAP_TIME + n_bits * self.bit_time

    # ------------------------------------------------------------ receiver side
    def preamble_scores(self, audio):
        """
        Normalised cross-correlation between the audio and the known preamble.
        Output value k (0..1) says how much audio[k : k+len(preamble)] looks
        like the preamble. Normalising by the audio energy means a loud noise
        does NOT automatically look like a preamble.
        """
        tmpl = self.preamble_wave.astype(np.float64)
        n = len(tmpl)
        audio = np.asarray(audio, dtype=np.float64)
        if len(audio) < n:
            return np.zeros(0)
        corr = fftconvolve(audio, tmpl[::-1], mode="valid")
        energy = np.cumsum(np.concatenate([[0.0], audio * audio]))
        window_energy = energy[n:] - energy[:-n]
        denom = np.sqrt(np.maximum(window_energy, 1e-12)) * np.linalg.norm(tmpl)
        return np.abs(corr) / denom

    def find_preamble(self, audio, start=0):
        """
        Search for the next frame start at or after sample `start`.
        Returns (index_of_first_bit, score) or None if no preamble is found.
        """
        scores = self.preamble_scores(audio[start:])
        if len(scores) == 0:
            return None
        above = np.nonzero(scores > self.threshold)[0]
        if len(above) == 0:
            return None
        # The first crossing tells us roughly where; the best peak just after it
        # tells us exactly where (within one preamble length).
        first = above[0]
        region = scores[first:first + len(self.preamble_wave)]
        peak = first + int(np.argmax(region))
        first_bit = start + peak + len(self.preamble_wave) + self.gap_samples
        return first_bit, float(scores[peak])

    def demodulate_bits(self, audio, first_bit, n_bits):
        """
        Decide each bit by comparing tone strengths inside that bit's time slot.

        Only the middle part of each slot is measured (skipping the edges),
        because echoes of the previous bit and small timing errors live there.
        Returns (bits, confidences); confidence 0..1 shows how clear the decision was.
        """
        spb = self.samples_per_bit
        lo = int(spb * 0.15)
        hi = int(spb * 0.95)
        bits, conf = [], []
        for i in range(n_bits):
            begin = first_bit + i * spb
            block = np.asarray(audio[begin + lo: begin + hi], dtype=np.float64)
            if len(block) < (hi - lo):
                break                                   # not enough audio yet
            p0 = goertzel_power(block, self.freq0, self.sample_rate)
            p1 = goertzel_power(block, self.freq1, self.sample_rate)
            bits.append(1 if p1 > p0 else 0)
            conf.append(abs(p1 - p0) / (p1 + p0 + 1e-20))
        return bits, conf

    def samples_needed(self, n_bits):
        """Audio samples after the first-bit index needed to decode n_bits."""
        return n_bits * self.samples_per_bit
