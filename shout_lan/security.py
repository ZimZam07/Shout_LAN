"""
security.py - APPLICATION-LAYER security: AES-256-GCM authenticated encryption.

Why: sound is a broadcast medium - anyone in the room with a microphone can
record every frame (eavesdropping) or play back / forge frames (tampering,
replay). AES-GCM gives:
  * confidentiality  - the payload is unreadable without the key
  * integrity + authenticity - any change to the ciphertext, the nonce or the
    authenticated header fields makes decryption FAIL instead of returning
    wrong data.

We use the standard AESGCM class from the `cryptography` library (the same
library as Assignment 9's Fernet) - no home-made cryptography.

Encrypted message layout (before it is split into frames):
    nonce (12 bytes) || ciphertext (same length as the text) || tag (16 bytes)
=> 28 extra bytes per MESSAGE (not per frame).

Nonce rule: every message gets a NEW random 96-bit nonce from os.urandom
(the operating system's cryptographically secure generator). A nonce is never
derived from the frame sequence number (that wraps at 256 and restarts).

Limitation: all nodes share ONE key. A valid tag proves the message came from
SOME holder of the key, not from WHICH node. See docs/THREAT_MODEL.md.
"""

import collections
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LEN = 12
TAG_LEN = 16
OVERHEAD = NONCE_LEN + TAG_LEN          # 28 bytes per message
KEY_LEN = 32                            # AES-256
REPLAY_CACHE = 1000                     # remember this many recent nonces
CONTEXT = b"SHOUT-LAN v1"               # binds ciphertexts to this protocol


class SecurityError(Exception):
    """Decryption failed: wrong key, tampered data, or a replayed message."""


def generate_key(path):
    """Create a new random 256-bit key file (run once, then copy the file to every laptop)."""
    key = AESGCM.generate_key(bit_length=256)
    with open(path, "wb") as f:
        f.write(key)
    return key


def load_key(path):
    with open(path, "rb") as f:
        key = f.read()
    if len(key) != KEY_LEN:
        raise SecurityError(f"{path} must contain exactly {KEY_LEN} bytes (found {len(key)})")
    return key


def _associated_data(origin, final):
    """
    Header fields that are NOT secret but must not be changed: who created the
    message and who it is for. They are authenticated (covered by the tag) but
    sent in clear in the network header.
    """
    return CONTEXT + bytes([origin, final])


class Security:
    def __init__(self, key):
        if len(key) != KEY_LEN:
            raise SecurityError("AES-256 needs a 32-byte key")
        self.aes = AESGCM(key)
        self.seen = collections.deque(maxlen=REPLAY_CACHE)    # order of accepted nonces
        self.seen_set = set()

    def encrypt(self, plaintext, origin, final):
        nonce = os.urandom(NONCE_LEN)                          # fresh random nonce every time
        sealed = self.aes.encrypt(nonce, plaintext, _associated_data(origin, final))
        return nonce + sealed                                  # nonce || ciphertext || tag

    def decrypt(self, blob, origin, final):
        """Returns the plaintext, or raises SecurityError. Never returns unverified data."""
        if len(blob) < OVERHEAD:
            raise SecurityError("encrypted message too short")
        nonce, sealed = blob[:NONCE_LEN], blob[NONCE_LEN:]
        try:
            plaintext = self.aes.decrypt(nonce, sealed, _associated_data(origin, final))
        except InvalidTag:
            raise SecurityError("authentication failed (tampered data or wrong key)") from None
        if nonce in self.seen_set:
            raise SecurityError("replay detected (this exact message was already accepted)")
        if len(self.seen) == self.seen.maxlen:
            self.seen_set.discard(self.seen[0])
        self.seen.append(nonce)
        self.seen_set.add(nonce)
        return plaintext
