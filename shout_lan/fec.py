"""
fec.py - FORWARD ERROR CORRECTION for the Noise Shield: Hamming(7,4) + interleaving.

CRC-16 (crc.py) only DETECTS errors: a damaged frame is thrown away and the
sender has to retransmit it (ARQ). Forward error correction adds redundant
bits so the receiver can REPAIR a few damaged bits itself, with no
retransmission - the idea used by Wi-Fi, mobile networks and CDs.

Hamming(7,4): every 4 data bits (one nibble) are sent as a 7-bit codeword

    position:  1   2   3   4   5   6   7
    bit:       p1  p2  d1  p3  d2  d3  d4

    p1 = d1 ^ d2 ^ d4      (checks positions 1, 3, 5, 7)
    p2 = d1 ^ d3 ^ d4      (checks positions 2, 3, 6, 7)
    p3 = d2 ^ d3 ^ d4      (checks positions 4, 5, 6, 7)

The receiver recomputes the three checks; the failing checks, read as a
binary number (the "syndrome"), give the POSITION of a single wrong bit,
which is then flipped back. One error per codeword is always corrected.
Code rate 4/7: 1 byte -> 14 bits on air (1.75x the plain airtime).

Soft decision (used by default): the demodulator also tells us how SURE it
was about each bit. The decoder picks, out of the 16 valid codewords, the
one that best matches what was heard, counting confident bits more than
weak ones. On weak bits this repairs more than the textbook rule.

Interleaving: noise often damages several bits in a row (a burst: a clap,
a cough). If those bits were all in one codeword, Hamming could not fix
them. So the codewords of a block are sent "column by column": first bit 1
of every codeword, then bit 2 of every codeword, ... A burst of up to N
bits (N = codewords in the block) then hits each codeword at most once.

Frame layout on air (shield format):
    [ header block: bytes 0-5 (SYNC..LEN) = 12 codewords = 84 bits ]
    [ body block:   the remaining bytes, 2 codewords per byte      ]
The header is a block of its own, so the receiver can decode it (and learn
the frame length) before the rest of the frame has arrived.
"""

import numpy as np

HEADER_BYTES = 6
BITS_PER_BYTE = 14          # 2 nibbles x 7 bits
NAME = "Hamming(7,4)"
RATE = 4 / 7

# 16 valid codewords, index = data nibble (d1 d2 d3 d4, d1 = most significant)
CODEWORDS = []
for _n in range(16):
    _d1, _d2, _d3, _d4 = (_n >> 3) & 1, (_n >> 2) & 1, (_n >> 1) & 1, _n & 1
    CODEWORDS.append([_d1 ^ _d2 ^ _d4, _d1 ^ _d3 ^ _d4, _d1, _d2 ^ _d3 ^ _d4, _d2, _d3, _d4])
CODEWORDS = np.array(CODEWORDS, dtype=np.int8)          # shape (16, 7)
DATA_POS = (2, 4, 5, 6)                                  # 0-based positions of d1..d4
_SIGNS = 2.0 * CODEWORDS - 1.0                           # 0 -> -1, 1 -> +1


def encode_nibble(n):
    return [int(b) for b in CODEWORDS[n & 0x0F]]


def syndrome(word):
    """Position (1..7) of the single wrong bit, or 0 if all three checks pass."""
    b = [0] + list(word)                                  # 1-based
    s1 = b[1] ^ b[3] ^ b[5] ^ b[7]
    s2 = b[2] ^ b[3] ^ b[6] ^ b[7]
    s3 = b[4] ^ b[5] ^ b[6] ^ b[7]
    return s1 | (s2 << 1) | (s3 << 2)


def decode_word_hard(word):
    """Textbook decoding: fix the bit the syndrome points at. Returns (nibble, fixed_word)."""
    word = list(word)
    pos = syndrome(word)
    if pos:
        word[pos - 1] ^= 1
    n = 0
    for p in DATA_POS:
        n = (n << 1) | word[p]
    return n, word


def decode_word_soft(soft):
    """
    soft[i] in -1..+1: sign = which bit the demodulator heard, size = how sure it was.
    Pick the valid codeword with the highest agreement (maximum-likelihood).
    """
    n = int(np.argmax(_SIGNS @ np.asarray(soft, dtype=np.float64)))
    return n, [int(b) for b in CODEWORDS[n]]


def interleave(words):
    """List of 7-bit codewords -> bits in send order (column by column)."""
    return [int(w[j]) for j in range(7) for w in words]


def deinterleave(bits, n_words):
    """Inverse of interleave(): bits in send order -> list of n_words codewords."""
    return [[bits[j * n_words + i] for j in range(7)] for i in range(n_words)]


def encode_block(data):
    words = []
    for byte in data:
        words.append(encode_nibble(byte >> 4))
        words.append(encode_nibble(byte & 0x0F))
    return interleave(words)


def encode_frame(data):
    """Frame bytes -> coded bits: header block, then body block."""
    data = bytes(data)
    return encode_block(data[:HEADER_BYTES]) + encode_block(data[HEADER_BYTES:])


def coded_bits(n_bytes):
    """Bits on air for an n-byte frame in the shield format."""
    return n_bytes * BITS_PER_BYTE


def header_bits():
    return HEADER_BYTES * BITS_PER_BYTE


def decode_block(bits, conf=None, soft=True):
    """
    Coded bits of ONE block (send order) -> (bytes, info).
    conf: per-bit confidence 0..1 from the demodulator (enables soft decision).
    info = {"repaired": number of on-air bits the decoder changed,
            "repaired_data": data-bit positions (in the decoded bytes) that were changed,
            "conf": per decoded data bit, the confidence of that bit as received}
    """
    n_words = len(bits) // 7
    bits = list(bits[:n_words * 7])
    words = deinterleave(bits, n_words)
    confs = deinterleave(list(conf[:n_words * 7]), n_words) if conf is not None else None
    out = bytearray()
    repaired, repaired_data, data_conf = 0, [], []
    nibble_hi = None
    for i, word in enumerate(words):
        if soft and confs is not None:
            s = [(2 * b - 1) * max(c, 1e-3) for b, c in zip(word, confs[i])]
            n, fixed = decode_word_soft(s)
        else:
            n, fixed = decode_word_hard(word)
        for j in range(7):
            if fixed[j] != word[j]:
                repaired += 1
                if j in DATA_POS:
                    repaired_data.append(i * 4 + DATA_POS.index(j))
        data_conf.extend(confs[i][p] if confs is not None else 1.0 for p in DATA_POS)
        if nibble_hi is None:
            nibble_hi = n
        else:
            out.append((nibble_hi << 4) | n)
            nibble_hi = None
    return bytes(out), {"repaired": repaired, "repaired_data": repaired_data, "conf": data_conf}
