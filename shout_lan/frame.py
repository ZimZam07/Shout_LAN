"""
frame.py - DATA LINK + NETWORK headers: building and parsing frames.

Byte layout of one frame (everything after the sound preamble):

  0      SYNC        0x7E, marks the start of the frame bytes
  1      TYPE|FLAGS  high 4 bits = frame type, low 4 bits = flags
  2      LINK SRC    node that sent this frame on THIS hop   (like a MAC address)
  3      LINK DST    node that should receive it on THIS hop (255 = everyone)
  4      SEQ         sequence number 0..255
  5      LEN         payload length 0..24
  6      ORIGIN      node that created the message            (network layer)
  7      FINAL DST   node the message is finally meant for    (network layer)
  8      TTL         hops left before the message is dropped  (network layer)
  9..    PAYLOAD     LEN bytes of data
  last 2 CRC-16      over bytes 1 .. end of payload

Overhead = 11 bytes. A full frame (24-byte payload) is 35 bytes = 280 bits.

Encapsulation: application data -> inside network header (ORIGIN/FINAL/TTL)
-> inside data-link header (SRC/DST/SEQ/LEN) + CRC -> sent as sound.
"""

import struct
from dataclasses import dataclass

from shout_lan.crc import crc16

SYNC = 0x7E
BROADCAST = 255
MAX_PAYLOAD = 24
HEADER_LEN = 9          # SYNC .. TTL
CRC_LEN = 2
OVERHEAD = HEADER_LEN + CRC_LEN   # 11 bytes
DEFAULT_TTL = 4

# Frame types (high nibble)
DATA, ACK, SACK, PING, PONG, HELLO, SRDATA = 1, 2, 3, 4, 5, 6, 7
TYPE_NAMES = {DATA: "DATA", ACK: "ACK", SACK: "SACK", PING: "PING", PONG: "PONG", HELLO: "HELLO",
              SRDATA: "DATA-SR"}
DATA_TYPES = (DATA, SRDATA)    # DATA = Stop-and-Wait data, DATA-SR = Selective Repeat data

# Flags (low nibble)
F_LAST = 0x1        # last fragment of a message
F_ENC = 0x2         # payload is AES-GCM encrypted
F_POLL = 0x4        # Selective Repeat: "end of my burst, please send a SACK"
F_FIRST = 0x8       # first fragment of a message
                    # (on a SACK frame F_FIRST means "cumulative": every seq before SEQ arrived)


class FrameError(Exception):
    """A received frame is malformed (bad sync, bad length, or CRC mismatch)."""


@dataclass
class Frame:
    ftype: int
    src: int
    dst: int
    seq: int = 0
    payload: bytes = b""
    flags: int = 0
    origin: int = None      # defaults to src
    final: int = None       # defaults to dst
    ttl: int = DEFAULT_TTL

    def __post_init__(self):
        if self.origin is None:
            self.origin = self.src
        if self.final is None:
            self.final = self.dst

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.ftype, f"TYPE{self.ftype}")

    def flag_names(self):
        names = []
        for bit, name in ((F_FIRST, "FIRST"), (F_LAST, "LAST"), (F_ENC, "ENC"), (F_POLL, "POLL")):
            if self.flags & bit:
                names.append(name)
        return names

    def describe(self):
        """Dictionary used by the packet monitor (every field of every layer)."""
        return {"type": self.type_name, "flags": self.flag_names(), "src": self.src,
                "dst": self.dst, "seq": self.seq, "len": len(self.payload),
                "origin": self.origin, "final": self.final, "ttl": self.ttl,
                "payload_hex": self.payload.hex(" ")}


def build(frame):
    """Frame object -> bytes ready for the modem (header + payload + CRC-16)."""
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too long ({len(frame.payload)} > {MAX_PAYLOAD})")
    for name in ("src", "dst", "seq", "origin", "final", "ttl"):
        value = getattr(frame, name)
        if not 0 <= value <= 255:
            raise ValueError(f"{name}={value} does not fit in one byte")
    type_flags = ((frame.ftype & 0x0F) << 4) | (frame.flags & 0x0F)
    body = struct.pack("BBBBBBBB", type_flags, frame.src, frame.dst, frame.seq,
                       len(frame.payload), frame.origin, frame.final, frame.ttl) + frame.payload
    return bytes([SYNC]) + body + struct.pack(">H", crc16(body))


def total_length(header_bytes):
    """
    Given the first 6 received bytes (SYNC..LEN), how long is the whole frame?
    Used by the receiver to know how many more bits to decode.
    Raises FrameError if the start of the frame is clearly invalid.
    """
    if len(header_bytes) < 6:
        raise FrameError("header too short")
    if header_bytes[0] != SYNC:
        raise FrameError(f"bad sync byte 0x{header_bytes[0]:02X}")
    length = header_bytes[5]
    if length > MAX_PAYLOAD:
        raise FrameError(f"impossible length {length}")
    return OVERHEAD + length


def parse(data):
    """
    Bytes from the modem -> Frame object.
    Raises FrameError for anything malformed. The CRC check is done here:
    a frame whose CRC does not match is NEVER passed up as valid data.
    """
    if len(data) < OVERHEAD:
        raise FrameError(f"frame too short ({len(data)} bytes)")
    expected = total_length(data[:6])
    if len(data) < expected:
        raise FrameError(f"frame truncated ({len(data)} of {expected} bytes)")
    data = data[:expected]
    body, received_crc = data[1:-2], struct.unpack(">H", data[-2:])[0]
    calc = crc16(body)
    if calc != received_crc:
        raise FrameError(f"CRC mismatch (received 0x{received_crc:04X}, calculated 0x{calc:04X})")
    type_flags, src, dst, seq, length, origin, final, ttl = struct.unpack("BBBBBBBB", body[:8])
    return Frame(ftype=type_flags >> 4, flags=type_flags & 0x0F, src=src, dst=dst, seq=seq,
                 payload=bytes(body[8:8 + length]), origin=origin, final=final, ttl=ttl)


# ---------------------------------------------------------------- fragmentation
def fragment(message, max_payload=MAX_PAYLOAD):
    """
    Split a long message into payload-sized pieces (fragmentation).
    Returns a list of (payload, flags) - FIRST on the first piece, LAST on the last.
    An empty message still produces one (empty) fragment.
    """
    pieces = [message[i:i + max_payload] for i in range(0, len(message), max_payload)] or [b""]
    out = []
    for i, piece in enumerate(pieces):
        flags = 0
        if i == 0:
            flags |= F_FIRST
        if i == len(pieces) - 1:
            flags |= F_LAST
        out.append((piece, flags))
    return out


class Reassembler:
    """
    Joins fragments back into the original message (reassembly).
    One instance per sender. The ARQ layer guarantees fragments arrive in
    order and without duplicates; FIRST resets any half-finished message
    (for example one whose sender gave up).
    """

    def __init__(self):
        self.parts = []

    def add(self, payload, flags):
        """Add one fragment; returns the complete message (bytes) when LAST arrives, else None."""
        if flags & F_FIRST:
            self.parts = []
        self.parts.append(payload)
        if flags & F_LAST:
            message = b"".join(self.parts)
            self.parts = []
            return message
        return None
