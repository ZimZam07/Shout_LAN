"""
monitor_client.py - a small TCP client (lab style) that prints the live
events of a running SHOUT LAN engine. Run it next to the UI to show that the
engine's server handles several clients at the same time (multithreading).

    python monitor_client.py            # connects to 127.0.0.1:5000
    python monitor_client.py 5001       # another port
"""

import socket
import sys

from shout_lan.linebuffer import LineBuffer

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    client.connect((HOST, PORT))
except OSError as exc:
    print(f"Could not connect to the engine at {HOST}:{PORT}: {exc}")
    sys.exit(1)
print(f"Connected to {HOST}:{PORT} (local control link). Press Ctrl+C to quit.")

buffer = LineBuffer()            # TCP may split or join messages - rebuild whole lines
try:
    while True:
        data = client.recv(4096)
        if not data:
            print("Engine closed the connection.")
            break
        for event in buffer.feed(data):
            if event.get("event") == "status":
                continue
            print(f"[{event.get('event')}] {event.get('text', '')}")
except KeyboardInterrupt:
    pass
client.close()
