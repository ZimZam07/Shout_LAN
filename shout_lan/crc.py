"""
crc.py - DATA LINK LAYER error detection: CRC-16 and parity.

CRC (Cyclic Redundancy Check): the sender divides the frame bits by a fixed
"generator polynomial" (binary long division using XOR) and sends the
remainder with the frame. The receiver repeats the division; a different
remainder means some bits were changed on the way.

We use CRC-16-CCITT (also called CRC-16/CCITT-FALSE):
    generator polynomial  x^16 + x^12 + x^5 + 1   -> 0x1021
    initial value         0xFFFF
Standard check value:  crc16(b"123456789") == 0x29B1
"""

POLY = 0x1021
INIT = 0xFFFF


def crc16(data):
    """CRC-16-CCITT of `data` (bytes), computed bit by bit exactly like the textbook division."""
    crc = INIT
    for byte in data:
        crc ^= byte << 8                      # bring the next 8 message bits into the register
        for _ in range(8):
            if crc & 0x8000:                  # leading bit is 1 -> "subtract" (XOR) the generator
                crc = ((crc << 1) ^ POLY) & 0xFFFF
            else:                             # leading bit is 0 -> just shift
                crc = (crc << 1) & 0xFFFF
    return crc


def parity_bit(data):
    """
    Even parity over all bits of `data`: returns 1 if the number of 1-bits is
    odd (so that data + parity bit has an even number of 1s), otherwise 0.
    Parity detects any ODD number of flipped bits but misses an EVEN number,
    which is why real networks use CRC instead.
    """
    ones = sum(bin(b).count("1") for b in data)
    return ones % 2


def parity_ok(data, parity):
    return parity_bit(data) == parity


def flip_bits(data, positions):
    """Return a copy of `data` with the given bit positions inverted (error injection helper)."""
    out = bytearray(data)
    for pos in positions:
        out[pos // 8] ^= 0x80 >> (pos % 8)
    return bytes(out)
