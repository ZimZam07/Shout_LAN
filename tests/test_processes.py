"""
End-to-end test of the real programs: two engine PROCESSES in SIMULATED
mode (UDP software air, not sound) + TCP control clients, like the UI uses.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

from shout_lan.linebuffer import LineBuffer, encode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_engine(node, port):
    proc = subprocess.Popen([sys.executable, "-u", "run_engine.py", "--node", str(node),
                             "--simulated", "--port", str(port)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen = ""
    while True:
        line = proc.stdout.readline()
        if line.startswith("READY"):
            return proc
        seen += line
        if not line or "ERROR" in line:
            proc.terminate()
            raise AssertionError(seen + proc.stdout.read())


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = LineBuffer()
        self.events = []

    def send(self, obj):
        self.sock.sendall(encode(obj))

    def wait(self, pred, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.events:
                if pred(e):
                    return e
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            self.events += self.buf.feed(data)
        raise AssertionError("event not received")


@pytest.fixture
def two_engines():
    procs = []
    try:
        procs.append(start_engine(11, 5111))
        procs.append(start_engine(12, 5112))
        yield
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def test_message_between_two_engine_processes(two_engines):
    ui1, ui2 = Client(5111), Client(5112)
    monitor = Client(5111)                      # second client on the same engine
    ui1.send({"cmd": "send", "dst": 12, "text": "PROCESS TEST"})
    ui1.wait(lambda e: e.get("event") == "message_status" and e.get("status") == "delivered")
    got = ui2.wait(lambda e: e.get("event") == "message_rx")
    assert got["message"] == "PROCESS TEST" and got["origin"] == 11
    monitor.wait(lambda e: e.get("event") == "message_status" and e.get("status") == "delivered")
    ui1.send({"cmd": "status"})
    st = ui1.wait(lambda e: e.get("event") == "status")
    assert st["channel"] == "simulated" and "SIMULATED" in st["stats"]["source"]
    assert st["control_clients"] == 2


def test_bad_command_returns_error_not_crash(two_engines):
    ui = Client(5111)
    ui.send({"cmd": "send", "dst": 999, "text": "x"})
    err = ui.wait(lambda e: e.get("event") == "error")
    assert "destination" in err["text"]
    ui.send({"cmd": "status"})
    assert ui.wait(lambda e: e.get("event") == "status")["running"] is True
