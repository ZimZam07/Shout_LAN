"""
mac.py - MAC SUBLAYER: who may use the shared acoustic channel, and when.

Sound is a shared broadcast medium: if two laptops beep at the same time the
frames overlap and both are destroyed (a collision).

  CSMA  (Carrier Sense Multiple Access) - "listen before talk":
        before sending, measure the sound energy in our frequency band.
        Busy -> wait a random time and listen again (non-persistent CSMA).
  ALOHA - "just talk": send immediately without listening. If no ACK comes
        back (maybe a collision), wait a RANDOM backoff before trying again,
        so two colliding senders are unlikely to collide again.
  NONE  - no access control (for comparison).

Responses (ACK, SACK, PONG) are sent without carrier sensing, like the short
gap before an ACK in Wi-Fi: the medium was just reserved by the frame they
answer.
"""

import random
import time

CSMA, ALOHA, NONE = "csma", "aloha", "none"
NAMES = {CSMA: "CSMA (listen before talk)", ALOHA: "Pure ALOHA (random backoff)",
         NONE: "No access control"}
EXPLAIN = {
    CSMA: "Before every new frame the node measures the sound level in its frequency band. "
          "If someone else is transmitting (channel busy) it waits a random time and listens again. "
          "Acoustic sensing is imperfect: a node that cannot hear another one can still collide.",
    ALOHA: "The node transmits as soon as it has data, without listening. If the ACK does not come "
           "back it assumes a collision and waits a random 'backoff' time (doubling each attempt) "
           "before retransmitting.",
    NONE: "Frames are sent immediately and retransmitted immediately after a timeout. "
          "Only for comparison.",
}

BUSY_FACTOR = 100.0         # band energy 100x (20 dB) above the noise floor = busy
                            # (a quiet room varies by up to ~10 dB; real frames measured 28-53 dB)
CSMA_MAX_DEFERS = 25        # give up waiting after this many busy checks
SENSE_WINDOW = 0.25         # seconds of recent audio looked at by carrier sense


def csma_backoff():
    """Random wait after sensing a busy channel (seconds)."""
    return random.uniform(0.3, 1.2)


def aloha_backoff(attempt, frame_time):
    """
    Binary exponential backoff for ALOHA retransmissions:
    wait a random number (0 .. 2^attempt - 1) of frame times (capped at 2^4).
    """
    k = min(attempt, 4)
    slots = random.randint(0, 2 ** k - 1)
    return slots * frame_time


class CarrierSense:
    """
    Keeps a short history of sound energy in our tone band, so the MAC can
    decide "busy" or "idle". The noise floor is estimated as a low percentile
    of the recent history (the quiet moments).
    """

    def __init__(self):
        self.history = []          # (time, energy)

    def add(self, energy):
        now = time.time()
        self.history.append((now, energy))
        cutoff = now - 15
        while self.history and self.history[0][0] < cutoff:
            self.history.pop(0)

    def noise_floor(self):
        values = sorted(e for _, e in self.history)
        if len(values) < 10:
            return None
        return max(values[len(values) // 10], 1e-12)

    def recent(self):
        now = time.time()
        values = [e for t, e in self.history if t >= now - SENSE_WINDOW]
        return max(values) if values else 0.0

    def busy(self):
        floor = self.noise_floor()
        if floor is None:
            return False, None
        ratio = self.recent() / floor
        return ratio > BUSY_FACTOR, ratio
