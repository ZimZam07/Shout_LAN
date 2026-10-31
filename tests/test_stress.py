"""
Thread-safety stress test (DoD D3) and clean shutdown (D4).
SIMULATED channel (in-process, sped up): two engines, each with its own TCP
control server; two clients hammer both servers with status requests while
messages are sent in BOTH directions at the same time.
"""

import socket
import threading
import time

from shout_lan.channel import SimulatedAir, SimulatedChannel
from shout_lan.control_server import ControlServer
from shout_lan.engine import Engine
from shout_lan.linebuffer import LineBuffer, encode
from shout_lan.node import make_command_handler

SCALE = 0.2


def client_loop(port, stop, results):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    buf = LineBuffer()
    sock.settimeout(0.05)
    events = []
    while not stop.is_set():
        sock.sendall(encode({"cmd": "status"}))
        try:
            data = sock.recv(65536)
            if data:
                events += buf.feed(data)
        except socket.timeout:
            pass
    sock.close()
    results.append(events)


def test_concurrent_traffic_status_polling_and_clean_shutdown():
    air = SimulatedAir(time_scale=SCALE, seed=3)
    a = Engine(1, SimulatedChannel(air), time_scale=SCALE)
    b = Engine(2, SimulatedChannel(air), time_scale=SCALE)
    servers = []
    for e in (a, b):
        srv = ControlServer(e, port=0, command_handler=make_command_handler(e, lambda: None))
        srv.start()
        servers.append(srv)
    a.start()
    b.start()
    stop = threading.Event()
    results = []
    clients = [threading.Thread(target=client_loop, args=(srv.port, stop, results))
               for srv in servers for _ in range(2)]                 # 2 clients per engine
    for c in clients:
        c.start()

    # messages in both directions at the same time (half-duplex channel!)
    for i in range(3):
        a.send_message(2, f"A->B message {i}")
        b.send_message(1, f"B->A message {i}")

    deadline = time.time() + 240
    while time.time() < deadline:
        sa, sb = a.stats.snapshot()["counters"], b.stats.snapshot()["counters"]
        if sa["messages_delivered"] + sa["messages_failed"] == 3 and \
           sb["messages_delivered"] + sb["messages_failed"] == 3:
            break
        time.sleep(0.2)
    time.sleep(1)
    stop.set()
    for c in clients:
        c.join(timeout=10)

    sa, sb = a.stats.snapshot()["counters"], b.stats.snapshot()["counters"]
    assert sa["messages_delivered"] == 3 and sb["messages_delivered"] == 3
    assert sa["messages_received"] == 3 and sb["messages_received"] == 3
    # every client got valid JSON only, many status replies, and no error events
    assert len(results) == 4
    for events in results:
        assert all("event" in e for e in events)
        assert not [e for e in events if e["event"] in ("error", "protocol_error")]
        assert sum(e["event"] == "status" for e in events) > 50

    # ---- D4 clean shutdown
    engine_threads = a.threads + b.threads
    assert len(engine_threads) == 6 and all(t.is_alive() for t in engine_threads)
    for srv in servers:
        srv.stop()
    a.stop()
    b.stop()
    assert all(not t.is_alive() for t in engine_threads)
    for srv in servers:                      # the port can be bound again = it was released
        s = socket.socket()
        s.bind(("127.0.0.1", srv.port))
        s.close()
