"""
node.py - glue between the engine and the local control server: it turns
JSON commands from the UI into engine actions.

Commands (UI -> engine), one JSON object per line:
    {"cmd": "status"}
    {"cmd": "send", "dst": 2, "text": "HELLO"}
    {"cmd": "ping", "dst": 2, "count": 4}
    {"cmd": "hello"}                                   discover neighbours
    {"cmd": "inject", "name": "drop_next_ack", "value": true}
    {"cmd": "set_volume", "value": 0.4}
    {"cmd": "set_tones", "freq0": 2000, "freq1": 3000}
    {"cmd": "set_protocol", "value": "sr"}            sw = Stop-and-Wait, sr = Selective Repeat
    {"cmd": "set_mac", "value": "csma"}               csma / aloha / none
    {"cmd": "calibrate"}
    {"cmd": "reset_stats"}
    {"cmd": "shutdown"}
Replies and engine events come back as {"event": ...} lines.
"""

import threading


def make_command_handler(engine, on_shutdown):
    def handle(server, client, cmd):
        name = cmd.get("cmd")
        if name == "status":
            status = engine.status()
            status["control_clients"] = server.client_count()
            status["control_link"] = f"Local control link (TCP) {server.host}:{server.port}"
            client.send({"event": "status", **status})
        elif name == "send":
            text = str(cmd.get("text", ""))
            if not text:
                raise ValueError("empty message")
            engine.send_message(int(cmd["dst"]), text, encrypt=bool(cmd.get("encrypt", False)))
        elif name == "ping":
            engine.ping(int(cmd["dst"]), int(cmd.get("count", 4)))
        elif name == "hello":
            engine.hello()
        elif name == "inject":
            value = cmd.get("value", True)
            engine.set_injection(cmd["name"], int(value) if cmd["name"] == "drop_frame" else bool(value))
        elif name == "set_protocol":
            engine.set_protocol(cmd["value"])
        elif name == "set_mac":
            engine.set_mac(cmd["value"])
        elif name == "set_volume":
            engine.set_volume(float(cmd["value"]))
        elif name == "set_tones":
            engine.set_tones(cmd["freq0"], cmd["freq1"])
        elif name == "calibrate":
            engine.calibrate()
        elif name == "reset_stats":
            engine.reset_stats()
        elif name == "shutdown":
            client.send({"event": "log", "text": "Engine shutting down"})
            threading.Thread(target=on_shutdown, daemon=True).start()
        else:
            raise ValueError(f"unknown command {name!r}")
    return handle
