"""Tests for the local TCP control link (DoD A3, A3a-A3d). No audio involved."""

import json
import socket
import threading
import time

from shout_lan.control_server import ControlServer
from shout_lan.linebuffer import LineBuffer, encode


# ---------------------------------------------------------------- A3a line reassembly
def test_message_split_across_many_recv_calls():
    buf = LineBuffer()
    data = encode({"event": "frame_tx", "seq": 7, "text": "hello"})
    out = []
    for i in range(len(data)):                      # worst case: one byte per recv()
        out += buf.feed(data[i:i + 1])
    assert out == [{"event": "frame_tx", "seq": 7, "text": "hello"}]


def test_several_messages_in_one_recv():
    buf = LineBuffer()
    data = encode({"a": 1}) + encode({"b": 2}) + encode({"c": 3})[:5]
    assert buf.feed(data) == [{"a": 1}, {"b": 2}]
    assert buf.feed(encode({"c": 3})[5:]) == [{"c": 3}]


def test_utf8_character_split_between_recv_calls():
    buf = LineBuffer()
    data = encode({"message": "नमस्ते ✓"})            # multi-byte characters
    cut = data.index("✓".encode("utf-8")) + 1      # cut in the middle of a 3-byte character
    assert buf.feed(data[:cut]) == []
    assert buf.feed(data[cut:]) == [{"message": "नमस्ते ✓"}]


# ---------------------------------------------------------------- helpers
def connect(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    s.settimeout(10)
    return s


def read_until(sock, buf, count, timeout=20):
    """Read until `count` messages have arrived (or timeout)."""
    out, end = [], time.time() + timeout
    while len(out) < count and time.time() < end:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            break
        if not data:
            break
        out += buf.feed(data)
    return out


# ---------------------------------------------------------------- A3 / A3b / A3c
def test_two_clients_concurrent_broadcasts_never_interleave():
    server = ControlServer(port=0)
    server.start()
    try:
        c1, c2 = connect(server.port), connect(server.port)
        b1, b2 = LineBuffer(), LineBuffer()
        assert read_until(c1, b1, 1)[0]["event"] == "hello"
        assert read_until(c2, b2, 1)[0]["event"] == "hello"
        while server.client_count() < 2:
            time.sleep(0.01)

        threads_n, per_thread = 4, 1000

        def blast(tid):
            for i in range(per_thread):
                server.broadcast({"event": "test", "thread": tid, "i": i, "pad": "x" * 50})

        workers = [threading.Thread(target=blast, args=(t,)) for t in range(threads_n)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        for sock, buf in ((c1, b1), (c2, b2)):
            msgs = read_until(sock, buf, threads_n * per_thread)
            assert len(msgs) == threads_n * per_thread             # all arrived
            assert all(m.get("event") == "test" for m in msgs)     # every line valid JSON
            for t in range(threads_n):                             # per-thread order kept
                seq = [m["i"] for m in msgs if m["thread"] == t]
                assert seq == list(range(per_thread))
        c1.close()
        c2.close()
    finally:
        server.stop()


def test_only_writer_thread_writes_each_socket():
    # Code-level check for A3c: the only sendall() in the server is in the writer.
    import inspect
    from shout_lan import control_server
    src = inspect.getsource(control_server)
    assert src.count(".sendall(") == 1
    assert ".sendall(" in inspect.getsource(control_server.ClientHandler.writer)


# ---------------------------------------------------------------- A3d disconnects
def test_client_disconnect_does_not_affect_other_client():
    server = ControlServer(port=0)
    server.start()
    try:
        c1, c2 = connect(server.port), connect(server.port)
        b2 = LineBuffer()
        read_until(c2, b2, 1)
        while server.client_count() < 2:
            time.sleep(0.01)
        c1.close()                                   # first client leaves mid-stream
        for i in range(200):
            server.broadcast({"event": "after", "i": i})
        msgs = read_until(c2, b2, 200)
        assert [m["i"] for m in msgs] == list(range(200))
        end = time.time() + 5
        while server.client_count() != 1 and time.time() < end:
            server.broadcast({"event": "ping"})
            time.sleep(0.05)
        assert server.client_count() == 1
        c2.close()
    finally:
        server.stop()


def test_commands_reach_handler_and_reply_goes_to_that_client():
    seen = []

    def handler(srv, client, cmd):
        seen.append(cmd)
        client.send({"event": "reply", "echo": cmd["value"]})

    server = ControlServer(port=0, command_handler=handler)
    server.start()
    try:
        c = connect(server.port)
        b = LineBuffer()
        read_until(c, b, 1)
        c.sendall(encode({"cmd": "echo", "value": 42}))
        assert read_until(c, b, 1)[0] == {"event": "reply", "echo": 42}
        assert seen == [{"cmd": "echo", "value": 42}]
        c.close()
    finally:
        server.stop()


def test_connection_refused_is_a_real_error():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                         # nobody listens here now
    try:
        connect(port)
        assert False, "should not connect"
    except OSError as exc:
        assert isinstance(exc, (ConnectionRefusedError, OSError))
