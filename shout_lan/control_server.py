"""
control_server.py - the LOCAL CONTROL LINK (TCP) between the UI and the engine.

This is the socket-programming part of the course, written in the same style
as the lab assignments:

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(5)
    conn, addr = server.accept()      -> one handler per client

IMPORTANT: this TCP link only carries commands and status between programs
on the SAME laptop (127.0.0.1). Messages between laptops travel ONLY through
sound (engine.py -> modem -> speaker).

Multithreading: for every client there are two threads.
  reader thread : recv() commands, cut them into lines, execute them
  writer thread : the ONLY thread allowed to sendall() on that socket. It
                  takes ready-made JSON lines from the client's own queue.
broadcast() puts each event into every client's queue (under a lock), so two
messages can never be mixed together on one socket and a slow client can
never block the engine.
"""

import queue
import socket
import threading

from shout_lan import config
from shout_lan.linebuffer import LineBuffer, encode


class ClientHandler:
    def __init__(self, server, conn, addr):
        self.server = server
        self.conn = conn
        self.addr = addr
        self.outgoing = queue.Queue()        # JSON lines waiting to be sent to this client
        self.alive = True

    def start(self):
        threading.Thread(target=self.reader, daemon=True, name=f"ctl-read-{self.addr[1]}").start()
        threading.Thread(target=self.writer, daemon=True, name=f"ctl-write-{self.addr[1]}").start()

    def send(self, obj):
        """Queue a message for this client (never writes to the socket directly)."""
        if self.alive:
            self.outgoing.put(encode(obj))

    def writer(self):
        while self.alive:
            line = self.outgoing.get()
            if line is None:
                break
            try:
                self.conn.sendall(line)       # sendall: keep sending until every byte is out
            except OSError:
                break
        self.close()

    def reader(self):
        buf = LineBuffer()
        while self.alive:
            try:
                data = self.conn.recv(4096)
            except OSError:
                break
            if not data:                       # b"" = the client closed the connection
                break
            for command in buf.feed(data):
                self.server.handle_command(self, command)
        self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self.outgoing.put(None)                # wake the writer so it can finish
        try:
            self.conn.close()
        except OSError:
            pass
        self.server.remove_client(self)


class ControlServer:
    def __init__(self, engine=None, host=config.CONTROL_HOST, port=config.CONTROL_PORT,
                 command_handler=None):
        self.engine = engine
        self.host = host
        self.port = port
        self.command_handler = command_handler   # function(server, client, command)
        self.clients = []
        self.clients_lock = threading.Lock()
        self.server = None
        self.running = False

    # ---------------------------------------------------------- lab-style server
    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]     # real port (if 0 was requested)
        self.running = True
        if self.engine is not None:
            self.engine.add_listener(self.broadcast)
        threading.Thread(target=self.accept_loop, daemon=True, name="ctl-accept").start()

    def accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server.accept()
            except OSError:
                break                                   # server socket closed
            client = ClientHandler(self, conn, addr)
            with self.clients_lock:
                self.clients.append(client)
            client.start()
            client.send({"event": "hello", "text": "Connected to SHOUT LAN engine",
                         "link": "Local control link (TCP)", "clients": self.client_count()})
            if self.engine is not None:
                self.broadcast({"event": "log", "node": self.engine.node_id,
                                "text": f"Control client connected from {addr[0]}:{addr[1]} "
                                        f"({self.client_count()} connected)"})

    def stop(self):
        self.running = False
        if self.engine is not None:
            self.engine.remove_listener(self.broadcast)
        try:
            self.server.close()
        except (OSError, AttributeError):
            pass
        with self.clients_lock:
            clients = list(self.clients)
        for c in clients:
            c.close()

    # ---------------------------------------------------------- clients
    def remove_client(self, client):
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)

    def client_count(self):
        with self.clients_lock:
            return len(self.clients)

    def broadcast(self, event):
        """Give an event to every connected client (each client's writer sends it)."""
        with self.clients_lock:
            for c in self.clients:
                c.send(event)

    def handle_command(self, client, command):
        if self.command_handler is not None:
            try:
                self.command_handler(self, client, command)
            except Exception as exc:
                client.send({"event": "error", "text": f"Command failed: {exc}",
                             "cmd": command.get("cmd")})
