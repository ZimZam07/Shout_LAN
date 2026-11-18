"""
receiver.py - turns a continuous stream of microphone audio into frames.

Steps for every frame (PHYSICAL -> DATA LINK):
  1. search the audio for a preamble chirp            (synchronisation)
     up-chirp = plain frame, down-chirp = Noise Shield frame (format signalling)
  2. decode the first 6 bytes (SYNC..LEN)             (framing: how long is it?)
  3. wait for enough audio, decode the rest of the bytes
     (shield frames: Hamming(7,4) repairs damaged bits first, fec.py)
  4. frame.parse() checks the CRC                      (error detection)

Audio arrives in small chunks, so the receiver keeps a buffer and remembers
where it is in the middle of a frame. A band-pass filtered copy of the
buffer (the receive filter, modem.detect_filter) is kept alongside it and
used only to FIND preambles; the bits are decoded from the unfiltered audio.
"""

import time

import numpy as np

from shout_lan import fec, frame as fr
from shout_lan.modem import Modem, bits_to_bytes, whiten

HEADER_BITS = 6 * 8      # SYNC .. LEN (plain format)


class FrameReceiver:
    def __init__(self, modem=None, on_preamble=None):
        self.modem = modem or Modem()
        self.on_preamble = on_preamble   # optional callback(score, shield, detected_at)
        self.buffer = np.zeros(0, dtype=np.float32)
        self.fbuffer = np.zeros(0, dtype=np.float64)   # band-pass filtered copy (detection only)
        self.zi = self.modem.new_filter_state()
        self.first_bit = None       # sample index of bit 0 of the frame being decoded
        self.score = 0.0
        self.shield = False         # format of the frame being decoded
        self.total_bytes = None     # known after the header is decoded
        self.gains = None           # per-tone channel gains of the current frame
        self.detected_at = 0.0

    def reset(self):
        self.buffer = np.zeros(0, dtype=np.float32)
        self.fbuffer = np.zeros(0, dtype=np.float64)
        self.zi = self.modem.new_filter_state()
        self.first_bit = None
        self.total_bytes = None

    def feed(self, audio):
        """
        Add new microphone samples. Returns a list of results, each a dict:
          {"ok": True,  "frame": Frame, "raw": bytes, "score": float, "confidence": float, ...}
          {"ok": False, "error": "CRC mismatch ...", "raw": bytes, "score": float, ...}
        Both carry "shield", "bits_repaired", "repaired_at", "bit_conf", "snr_db" and,
        when a whole frame was decoded, "crc_rx"/"crc_calc" (see _decode_full).
        """
        if len(audio):
            audio = np.asarray(audio, dtype=np.float32)
            self.buffer = np.concatenate([self.buffer, audio])
            filtered, self.zi = self.modem.detect_filter(audio, self.zi)
            self.fbuffer = np.concatenate([self.fbuffer, filtered])
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
        self.fbuffer = self.fbuffer[n:]
        if self.first_bit is not None:
            self.first_bit -= n

    def _header_bits(self):
        return fec.header_bits() if self.shield else HEADER_BITS

    def _frame_bits(self, n_bytes):
        return fec.coded_bits(n_bytes) if self.shield else n_bytes * 8

    def _step(self):
        m = self.modem
        spb = m.samples_per_bit

        # 1. Looking for the start of a new frame (either format)
        if self.first_bit is None:
            found = m.find_frame_start(self.fbuffer, filtered=True)
            if found is None:
                # Keep only the tail: a preamble may be partly recorded at the
                # end, and the search needs up to 2 preamble lengths after
                # its first threshold crossing to locate the exact peak.
                keep = 2 * len(m.preamble_wave) + m.gap_samples
                self._drop(len(self.buffer) - keep)
                return None
            self.first_bit, self.score, self.shield = found
            self.detected_at = time.time()            # when this frame's preamble was found
            self.total_bytes = None
            self.gains = m.estimate_gains(self.buffer, self.first_bit)
            if self.on_preamble is not None:
                try:
                    self.on_preamble(self.score, self.shield, self.detected_at)
                except Exception:
                    pass

        # 2. Decode the header to learn the frame length
        margin = m.TRACK_LIMIT_BITS * spb if m.TRACKING_DEFAULT else 0   # tracker looks further
        if self.total_bytes is None:
            n_head = self._header_bits()
            if len(self.buffer) < self.first_bit + n_head * spb + margin:
                return None                         # wait for more audio
            header, error = None, None
            for gains in self._gain_options():
                bits, conf = m.demodulate_bits(self.buffer, self.first_bit, n_head, gains)
                if self.shield:
                    header = whiten(fec.decode_block(bits, conf)[0])
                else:
                    header = whiten(bits_to_bytes(bits))
                try:
                    self.total_bytes = fr.total_length(header)
                    break
                except fr.FrameError as exc:
                    error = str(exc)
            if self.total_bytes is None:
                return self._finish_bad(error, header, {})

        # 3. Decode the whole frame once enough audio has arrived
        n_bits = self._frame_bits(self.total_bytes)
        if len(self.buffer) < self.first_bit + n_bits * spb + margin:
            return None
        for gains in self._gain_options():
            bits, conf = m.demodulate_bits(self.buffer, self.first_bit, n_bits, gains)
            raw, info = self._decode_full(bits, conf)
            try:
                frame = fr.parse(raw)
                break
            except fr.FrameError as exc:
                frame, error = None, str(exc)
        snr = m.tone_snr_db(self.buffer, self.first_bit, n_bits)
        info["snr_db"] = round(snr, 1) if snr is not None else None
        crc = fr.crc_fields(raw)
        if crc is not None:
            info["crc_rx"], info["crc_calc"] = f"0x{crc[0]:04X}", f"0x{crc[1]:04X}"
        if frame is None:
            info["damaged_frame"] = fr.parse_unchecked(raw)
            return self._finish_bad(error, raw, info)
        result = {"ok": True, "frame": frame, "raw": raw, "score": self.score,
                  "detected_at": self.detected_at, "shield": self.shield,
                  "confidence": float(np.mean(conf)) if conf else 0.0}
        result.update(info)
        self._finish(n_bits)
        return result

    def _gain_options(self):
        """
        Decode without the tone equaliser first; only if that fails AND the
        preamble said one tone is much weaker, try again with it (modem.py,
        EQUALISE_MIN_DB explains why).
        """
        yield None
        if self.gains is not None and self.modem.gains_significant(self.gains):
            yield self.gains

    def _decode_full(self, bits, conf):
        """Demodulated bits of a whole frame -> (frame bytes, details for the monitor)."""
        if not self.shield:
            raw = whiten(bits_to_bytes(bits))
            return raw, {"bits_repaired": 0, "repaired_at": [],
                         "bit_conf": [round(c, 2) for c in conf]}
        nh = fec.header_bits()
        head, hi = fec.decode_block(bits[:nh], conf[:nh])
        body, bi = fec.decode_block(bits[nh:], conf[nh:])
        raw = whiten(head + body)
        repaired_at = hi["repaired_data"] + [p + 8 * fec.HEADER_BYTES for p in bi["repaired_data"]]
        return raw, {"bits_repaired": hi["repaired"] + bi["repaired"],
                     "repaired_at": repaired_at[:64],
                     "bit_conf": [round(c, 2) for c in hi["conf"] + bi["conf"]]}

    def _finish(self, n_bits):
        end = self.first_bit + n_bits * self.modem.samples_per_bit
        self.first_bit = None
        self.total_bytes = None
        self._drop(end)

    def _finish_bad(self, error, raw, info):
        result = {"ok": False, "error": error, "raw": raw, "score": self.score,
                  "detected_at": self.detected_at, "shield": self.shield,
                  "bits_repaired": 0, "repaired_at": []}
        result.update(info)
        # Skip past this preamble only (not the whole guessed length): if the
        # header was garbage, the length is garbage too.
        end_pre = self.first_bit
        self.first_bit = None
        self.total_bytes = None
        self._drop(end_pre)
        return result
