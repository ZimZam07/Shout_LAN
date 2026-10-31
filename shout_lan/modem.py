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


# ---------------------------------------------------------------- scrambler
def _scrambler_sequence(n_bytes, seed=0x5D):
    """
    Pseudo-random bytes from a 7-bit LFSR with polynomial x^7 + x^4 + 1
    (the same scrambler polynomial as IEEE 802.11 Wi-Fi).
    """
    state = seed & 0x7F
    out = bytearray()
    for _ in range(n_bytes):
        byte = 0
        for _ in range(8):
            bit = ((state >> 6) ^ (state >> 3)) & 1      # taps for x^7 and x^4
            state = ((state << 1) | bit) & 0x7F
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def whiten(data):
    """
    Scramble (or unscramble - XOR is its own inverse) bytes before sending.

    Why: frames contain many 0x00/0x01 bytes, i.e. long runs of the same bit
    = the same tone for hundreds of milliseconds. In a real room a long tone
    builds up echo that spills into the following bits (inter-symbol
    interference). XOR-ing with a pseudo-random sequence breaks those runs.
    It does not hide anything - it is not encryption.
    """
    seq = _scrambler_sequence(len(data))
    return bytes(a ^ b for a, b in zip(data, seq))


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

    def modulate_bytes(self, data):
        """Frame bytes -> scrambled -> bits -> sound. Used for every transmission."""
        return self.modulate(bytes_to_bits(whiten(data)))

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
        if first + len(self.preamble_wave) > len(scores):
            # The preamble is only partly recorded so far. Picking a peak now
            # would give the wrong start time, so wait for more audio.
            return None
        region = scores[first:first + len(self.preamble_wave)]
        peak = first + int(np.argmax(region))
        first_bit = start + peak + len(self.preamble_wave) + self.gap_samples
        return first_bit, float(scores[peak])

    def estimate_gains(self, audio, first_bit):
        """
        Channel estimation: the preamble chirp contains both tone frequencies
        at a known level. Comparing how strongly each one ARRIVED tells us how
        much the speaker/room/microphone weakens each tone (attenuation).
        Returns (gain0, gain1) used to equalise the two tones.
        """
        end = first_bit - self.gap_samples
        start = end - len(self.preamble_wave)
        received = np.asarray(audio[max(start, 0):end], dtype=np.float64)
        sent = self.preamble_wave.astype(np.float64) * self.volume
        if len(received) < len(sent) // 2:
            return 1.0, 1.0
        gains = []
        for f in (self.freq0, self.freq1):
            ref = goertzel_power(sent, f, self.sample_rate)
            got = goertzel_power(received, f, self.sample_rate)
            gains.append(max(got / ref, 1e-12) if ref > 0 else 1.0)
        return gains[0], gains[1]

    # Symbol-timing recovery settings (see demodulate_bits)
    TRACK_GROUP = 4          # re-check the bit timing after every 4 bits
    TRACK_RANGE = 480        # look up to half a bit (480 samples) earlier/later
    TRACK_STEP = 12          # in steps of 12 samples (0.25 ms)
    TRACK_GAIN = 0.03        # move only if the alignment score improves by more than this
    TRACK_LIMIT_BITS = 2     # never drift more than 2 bits away from the preamble timing
    TRACK_WIN_LO = 0.30      # alignment window = 30% .. 100% of the slot (skips most room echo;
                             # chosen by re-decoding 89 real recorded frames, TESTING.md M6)

    # Symbol-timing recovery is EXPERIMENTAL and OFF by default: it helped on two recorded
    # runs (+10 / +2 frames) but failed badly on another (3/44 vs 25/44 valid frames,
    # TESTING.md M6), so it is not reliable enough to enable.
    TRACKING_DEFAULT = False

    def demodulate_bits(self, audio, first_bit, n_bits, gains=None, track=None, trace=None):
        """
        Decide each bit by comparing tone strengths inside that bit's time slot.

        - Only the LATER part of each slot is measured (40% .. 95%): the start
          of a slot still contains the echo of the previous bit.
        - Each tone's power is divided by its channel gain (from the preamble),
          so a tone that the speaker plays weakly is not always out-voted.
        - Symbol-timing recovery (track=True): Windows' audio path sometimes
          inserts or drops a few milliseconds of sound in the middle of a
          frame (TESTING.md M6), which shifts every later bit. After every 4
          bits the receiver tries slightly earlier/later timings and keeps the
          slot boundaries centred - like a real modem's clock recovery.
          Alignment is judged with a window reaching to the very END of the
          slot (30%..100%): it scores highest only when it ends exactly at the
          bit boundary (a late window mixes in the next bit's tone), so small
          shifts are corrected before they add up. The first 30% is skipped
          because it is full of the previous bit's room echo.
        Tone strength for ANY window is computed in O(1) from running sums of
        x[n]·e^(-jωn) (a "sliding DFT" - the same value Goertzel gives).
        Returns (bits, confidences); confidence 0..1 shows how clear the decision was.
        """
        if track is None:
            track = self.TRACKING_DEFAULT
        spb = self.samples_per_bit
        lo = int(spb * 0.40)
        hi = int(spb * 0.95)
        length = hi - lo
        g0, g1 = gains if gains else (1.0, 1.0)
        limit = self.TRACK_LIMIT_BITS * spb if track else 0
        start = max(0, first_bit - limit)
        x = np.asarray(audio[start:first_bit + n_bits * spb + limit], dtype=np.float64)
        n = np.arange(len(x))
        # running sums: S[k] = sum of x[m]*e^(-jωm) for m < k
        s0 = np.concatenate([[0], np.cumsum(x * np.exp(-2j * np.pi * self.freq0 / self.sample_rate * n))])
        s1 = np.concatenate([[0], np.cumsum(x * np.exp(-2j * np.pi * self.freq1 / self.sample_rate * n))])
        base = first_bit - start                       # index of bit 0 inside x

        def measure(begins, w_lo=lo, w_len=length):
            """(p0, p1) tone powers for windows starting at the given bit-slot starts."""
            a = np.asarray(begins) + w_lo
            b = a + w_len
            p0 = np.abs(s0[b] - s0[a]) ** 2 / (w_len * w_len) / g0
            p1 = np.abs(s1[b] - s1[a]) ** 2 / (w_len * w_len) / g1
            return p0, p1

        def confidence(p0, p1):
            return np.abs(p1 - p0) / (p1 + p0 + 1e-20)

        offset = 0
        bits, conf = [], []
        for i in range(n_bits):
            begin = base + i * spb + offset
            if begin + hi > len(x) or begin + lo < 0:
                break                                   # not enough audio yet
            p0, p1 = measure([begin])
            bits.append(1 if p1[0] > p0[0] else 0)
            conf.append(float(confidence(p0, p1)[0]))
            if track and (i + 1) % self.TRACK_GROUP == 0:
                group = np.arange(i + 1 - self.TRACK_GROUP, i + 1)
                shifts = np.arange(-self.TRACK_RANGE, self.TRACK_RANGE + 1, self.TRACK_STEP)
                shifts = shifts[np.abs(offset + shifts) <= limit]
                begins = base + group[None, :] * spb + offset + shifts[:, None]
                t_lo = int(spb * self.TRACK_WIN_LO)
                ok = (begins + t_lo >= 0).all(axis=1) & (begins + spb <= len(x)).all(axis=1)
                shifts, begins = shifts[ok], begins[ok]
                if len(shifts) == 0:
                    continue
                c = confidence(*measure(begins, t_lo, spb - t_lo))   # shape (shifts, 4 bits)
                score = c.mean(axis=1)
                current = score[shifts == 0][0] if (shifts == 0).any() else 0.0
                best = int(np.argmax(score))
                if shifts[best] != 0 and score[best] > current + self.TRACK_GAIN:
                    offset += int(shifts[best])
                    if trace is not None:
                        trace.append((i, offset, round(float(current), 2), round(float(score[best]), 2)))
                    # re-decide the bits of this group with the corrected timing where
                    # that gives a clearer decision (the jump happened somewhere inside it)
                    p0, p1 = measure(base + group * spb + offset)
                    newc = confidence(p0, p1)
                    for k, bit_index in enumerate(group):
                        if newc[k] > conf[bit_index]:
                            bits[bit_index] = 1 if p1[k] > p0[k] else 0
                            conf[bit_index] = float(newc[k])
        return bits, conf

    def samples_needed(self, n_bits):
        """Audio samples after the first-bit index needed to decode n_bits."""
        return n_bits * self.samples_per_bit
