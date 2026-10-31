"""
control_client.py - the UI side of the LOCAL CONTROL LINK (TCP client).

Lab pattern:  socket.socket(AF_INET, SOCK_STREAM) -> connect() -> sendall()/recv()

A background thread does the blocking recv() loop and hands every complete
JSON event to the UI through a Qt signal, so the window never freezes.
Only the UI thread calls send(), so the socket has exactly one writer.
"""

import socket
import threading

from PySide6.QtCore import QObject, Signal

from shout_lan.linebuffer import LineBuffer, encode


class ControlClient(QObject):
    event = Signal(dict)               # one engine event
    connected = Signal(str)            # "127.0.0.1:5000"
    disconnected = Signal(str)         # reason

    def __init__(self):
        super().__init__()
        self.sock = None
        self.address = None

    @property
    def is_connected(self):
        return self.sock is not None

    def connect_to(self, host, port):
        """Returns None on success or the real error text (e.g. connection refused)."""
        self.close()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((host, port))
            sock.settimeout(None)
        except OSError as exc:
            return f"{exc.__class__.__name__}: {exc}"
        self.sock = sock
        self.address = f"{host}:{port}"
        threading.Thread(target=self._reader, args=(sock,), daemon=True).start()
        self.connected.emit(self.address)
        return None

    def _reader(self, sock):
        buf = LineBuffer()
        reason = "Engine closed the connection"
        while True:
            try:
                data = sock.recv(65536)
            except OSError as exc:
                reason = str(exc)
                break
            if not data:
                break
            for ev in buf.feed(data):
                self.event.emit(ev)
        if self.sock is sock:
            self.sock = None
            self.disconnected.emit(reason)

    def send(self, obj):
        if self.sock is None:
            return "Not connected to an engine"
        try:
            self.sock.sendall(encode(obj))
        except OSError as exc:
            return str(exc)
        return None

    def close(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
            self.disconnected.emit("Disconnected")
