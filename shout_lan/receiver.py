"""
receiver.py - turns a continuous stream of microphone audio into frames.

Steps for every frame (PHYSICAL -> DATA LINK):
  1. search the audio for the preamble chirp          (synchronisation)
  2. decode the first 6 bytes (SYNC..LEN)             (framing: how long is it?)
  3. wait for enough audio, decode the rest of the bytes
  4. frame.parse() checks the CRC                      (error detection)

Audio arrives in small chunks, so the receiver keeps a buffer and remembers
where it is in the middle of a frame.
"""

import numpy as np

from shout_lan import frame as fr
from shout_lan.modem import Modem, bits_to_bytes, whiten

HEADER_BITS = 6 * 8      # SYNC .. LEN


class FrameReceiver:
    def __init__(self, modem=None):
        self.modem = modem or Modem()
        self.buffer = np.zeros(0, dtype=np.float32)
        self.first_bit = None       # sample index of bit 0 of the frame being decoded
        self.score = 0.0
        self.total_bytes = None     # known after the header is decoded
        self.gains = None           # per-tone channel gains of the current frame

    def reset(self):
        self.buffer = np.zeros(0, dtype=np.float32)
        self.first_bit = None
        self.total_bytes = None

    def feed(self, audio):
        """
        Add new microphone samples. Returns a list of results, each a dict:
          {"ok": True,  "frame": Frame, "raw": bytes, "score": float, "confidence": float}
          {"ok": False, "error": "CRC mismatch ...", "raw": bytes, "score": float}
        """
        if len(audio):
            self.buffer = np.concatenate([self.buffer, np.asarray(audio, dtype=np.float32)])
        results = []
        while True:
            result = self._step()
            if result is None:
                break
            results.append(result)
        return results

    def _drop(self, n):
        """Forget the first n samples of the buffer (already processed)."""
        n = max(0, min(n, len(self.buffer)))
        self.buffer = self.buffer[n:]
        if self.first_bit is not None:
            self.first_bit -= n

    def _step(self):
        m = self.modem
        spb = m.samples_per_bit

        # 1. Looking for the start of a new frame
        if self.first_bit is None:
            found = m.find_preamble(self.buffer)
            if found is None:
                # Keep only the tail: a preamble may be partly recorded at the
                # end, and find_preamble needs up to 2 preamble lengths after
                # its first threshold crossing to locate the exact peak.
                keep = 2 * len(m.preamble_wave) + m.gap_samples
                self._drop(len(self.buffer) - keep)
                return None
            self.first_bit, self.score = found
            self.total_bytes = None
            self.gains = m.estimate_gains(self.buffer, self.first_bit)

        # 2. Decode the header to learn the frame length
        margin = m.TRACK_LIMIT_BITS * spb if m.TRACKING_DEFAULT else 0   # tracker looks further
        if self.total_bytes is None:
            if len(self.buffer) < self.first_bit + HEADER_BITS * spb + margin:
                return None                         # wait for more audio
            bits, _ = m.demodulate_bits(self.buffer, self.first_bit, HEADER_BITS, self.gains)
            header = whiten(bits_to_bytes(bits))
            try:
                self.total_bytes = fr.total_length(header)
            except fr.FrameError as exc:
                return self._finish_bad(str(exc), header, HEADER_BITS)

        # 3. Decode the whole frame once enough audio has arrived
        n_bits = self.total_bytes * 8
        if len(self.buffer) < self.first_bit + n_bits * spb + margin:
            return None
        bits, conf = m.demodulate_bits(self.buffer, self.first_bit, n_bits, self.gains)
        raw = whiten(bits_to_bytes(bits))
        try:
            frame = fr.parse(raw)
        except fr.FrameError as exc:
            return self._finish_bad(str(exc), raw, n_bits)
        result = {"ok": True, "frame": frame, "raw": raw, "score": self.score,
                  "confidence": float(np.mean(conf)) if conf else 0.0}
        self._finish(n_bits)
        return result

    def _finish(self, n_bits):
        end = self.first_bit + n_bits * self.modem.samples_per_bit
        self.first_bit = None
        self.total_bytes = None
        self._drop(end)

    def _finish_bad(self, error, raw, n_bits):
        result = {"ok": False, "error": error, "raw": raw, "score": self.score}
        # Skip past this preamble only (not the whole guessed length): if the
        # header was garbage, the length is garbage too.
        end_pre = self.first_bit
        self.first_bit = None
        self.total_bytes = None
        self._drop(end_pre)
        return result
