"""
linebuffer.py - newline-delimited JSON over TCP.

TCP is a BYTE STREAM, not a message service: one recv() may return half a
message, or three messages glued together. So every receiver keeps a buffer,
cuts it at b"\\n", and keeps the unfinished tail for the next recv().
The server, the UI and monitor_client.py all use this same helper.
"""

import json


class LineBuffer:
    def __init__(self):
        self.pending = b""

    def feed(self, data):
        """Add bytes from recv(); return a list of complete decoded JSON objects."""
        self.pending += data
        messages = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                # decode only complete lines, so a UTF-8 character split
                # across two recv() calls is never decoded half-way
                messages.append(json.loads(line.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                messages.append({"event": "protocol_error", "raw": line[:200].decode("latin-1")})
        return messages


def encode(obj):
    """Python dict -> one line of JSON bytes ending with b"\\n" (ready for sendall)."""
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
