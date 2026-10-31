"""AES-256-GCM security layer tests (DoD E1, E1a, E1b, E4)."""

import inspect

import pytest

from shout_lan import security
from shout_lan.security import OVERHEAD, Security, SecurityError

KEY = bytes(range(32))


def test_round_trip_and_overhead():
    s = Security(KEY)
    blob = s.encrypt(b"HELLO", origin=1, final=2)
    assert len(blob) == 5 + OVERHEAD == 33
    assert b"HELLO" not in blob                     # ciphertext, not plaintext
    assert Security(KEY).decrypt(blob, 1, 2) == b"HELLO"


@pytest.mark.parametrize("where", ["nonce", "ciphertext", "tag"])
def test_any_modified_byte_is_rejected(where):
    blob = bytearray(Security(KEY).encrypt(b"TOP SECRET", 1, 2))
    index = {"nonce": 3, "ciphertext": 12 + 4, "tag": len(blob) - 1}[where]
    blob[index] ^= 0x01
    with pytest.raises(SecurityError, match="authentication failed"):
        Security(KEY).decrypt(bytes(blob), 1, 2)


def test_changed_origin_or_destination_is_rejected():
    blob = Security(KEY).encrypt(b"TOP SECRET", 1, 2)
    with pytest.raises(SecurityError):
        Security(KEY).decrypt(blob, 3, 2)          # someone claims node 3 sent it
    with pytest.raises(SecurityError):
        Security(KEY).decrypt(blob, 1, 4)          # re-addressed to node 4


def test_wrong_key_is_rejected():
    blob = Security(KEY).encrypt(b"TOP SECRET", 1, 2)
    with pytest.raises(SecurityError):
        Security(bytes(32)).decrypt(blob, 1, 2)


def test_nonces_are_unique_random_and_not_from_seq():
    s = Security(KEY)
    blobs = [s.encrypt(b"same text", 1, 2) for _ in range(10000)]
    assert len({b[:12] for b in blobs}) == 10000     # 10 000 distinct nonces
    assert len(set(blobs)) == 10000                   # 10 000 distinct ciphertexts
    src = inspect.getsource(security)
    assert src.count("os.urandom(NONCE_LEN)") == 1    # the only nonce source
    assert "seq" not in inspect.getsource(Security.encrypt)


def test_replay_is_rejected():
    blob = Security(KEY).encrypt(b"PAY 10 RUPEES", 1, 2)
    receiver = Security(KEY)
    assert receiver.decrypt(blob, 1, 2) == b"PAY 10 RUPEES"
    with pytest.raises(SecurityError, match="replay"):
        receiver.decrypt(blob, 1, 2)


def test_replay_cache_is_bounded():
    s, receiver = Security(KEY), Security(KEY)
    first = s.encrypt(b"x", 1, 2)
    receiver.decrypt(first, 1, 2)
    for _ in range(security.REPLAY_CACHE):
        receiver.decrypt(s.encrypt(b"y", 1, 2), 1, 2)
    # documented limitation: after 1000 newer messages the oldest nonce is forgotten
    assert receiver.decrypt(first, 1, 2) == b"x"
    assert len(receiver.seen) == security.REPLAY_CACHE


def test_key_file(tmp_path):
    path = tmp_path / "secret.key"
    key = security.generate_key(path)
    assert len(key) == 32 and security.load_key(path) == key
    (tmp_path / "bad.key").write_bytes(b"short")
    with pytest.raises(SecurityError, match="32 bytes"):
        security.load_key(tmp_path / "bad.key")


def test_secret_key_is_git_ignored():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore")) as f:
        assert "secret.key" in f.read().split()
