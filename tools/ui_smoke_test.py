"""
ui_smoke_test.py - drives the REAL desktop window without a screen (Qt offscreen):
clicks "Start engine", sends a message, discovers nodes, pings, injects an
error, and saves a screenshot of every page in results/screenshots/.

Uses the SIMULATED channel only (no sound), so it is safe to run anywhere:
    python tools/ui_smoke_test.py
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication   # noqa: E402

from ui import style                          # noqa: E402
from ui.app import MainWindow                 # noqa: E402

SHOTS = os.path.join(ROOT, "results", "screenshots")


MAX_STALL = [0.0]
SLOW = []                  # (milliseconds, handler) for UI handlers slower than 50 ms          # longest single processEvents() call = how long the UI was frozen


def pump(app, seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        t0 = time.perf_counter()
        app.processEvents()
        MAX_STALL[0] = max(MAX_STALL[0], time.perf_counter() - t0)
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def main():
    os.makedirs(SHOTS, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(style.QSS)
    w = MainWindow()
    # --- timing instrumentation: which UI handler is slow?
    SLOW.clear()
    orig_event, orig_status = w.on_event, w.broadcast_status

    def timed_event(e):
        t0 = time.perf_counter()
        orig_event(e)
        dt = time.perf_counter() - t0
        if dt > 0.05:
            SLOW.append((round(dt * 1000), "event:" + str(e.get("event"))))

    def timed_status(st):
        t0 = time.perf_counter()
        orig_status(st)
        dt = time.perf_counter() - t0
        if dt > 0.05:
            SLOW.append((round(dt * 1000), "status"))

    w.client.event.disconnect(w.on_event)
    w.client.event.connect(timed_event)
    w.on_event = timed_event
    w.broadcast_status = timed_status
    w.resize(1400, 900)
    w.show()
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))
        print(("PASS " if ok else "FAIL ") + name, flush=True)

    pump(app, 0.5)
    w.grab().save(os.path.join(SHOTS, "0_connect_not_running.png"))

    # second node, started from the command line (like the other laptop)
    other = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "run_engine.py"),
                              "--node", "2", "--simulated", "--port", "5202"], cwd=ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = w.pages[1]
        page.node_spin.setValue(1)
        page.sim_radio.setChecked(True)
        page.port_spin.setValue(5201)
        page.start_btn.click()
        check("engine starts and UI connects over TCP",
              pump(app, 20, lambda: w.status is not None))
        check("top bar shows SIMULATED label", "SIMULATED" in w.channel_chip.text())
        w.grab().save(os.path.join(SHOTS, "1_connect_running.png"))

        # discovery
        w.nav.button(0).click()
        w.pages[0].discover_btn.click()
        check("HELLO discovery finds node 2",
              pump(app, 30, lambda: w.status and "2" in w.status["nodes"]))
        pump(app, 1.5)
        w.grab().save(os.path.join(SHOTS, "2_overview.png"))

        # message
        w.nav.button(2).click()
        msgs = w.pages[2]
        msgs.dst.setValue(2)
        msgs.input.setText("HELLO FROM THE SHOUT LAN APP")
        msgs.send_btn.click()
        check("message shows Delivered only after ACK",
              pump(app, 60, lambda: any(i.get("status") == "delivered" for i in msgs.items)))
        w.grab().save(os.path.join(SHOTS, "3_messages.png"))

        # responsiveness during a long (3-frame) transmission
        MAX_STALL[0] = 0.0
        msgs.input.setText("A LONGER MESSAGE THAT NEEDS THREE FRAMES TO SHOW THE UI KEEPS RUNNING")
        msgs.send_btn.click()
        done = pump(app, 120, lambda: sum(i.get("status") == "delivered" for i in msgs.items) >= 2)
        print(f"     longest UI stall during transmission: {MAX_STALL[0] * 1000:.0f} ms", flush=True)
        print(f"     slow UI handlers so far: {SLOW}", flush=True)
        check("3-frame message delivered while UI stays responsive (stall < 200 ms)",
              done and MAX_STALL[0] < 0.2)

        # error injection + retransmission
        w.nav.button(3).click()
        w.pages[3].corrupt_btn.click()
        pump(app, 0.5)
        msgs.input.setText("CRC DEMO")
        msgs.send_btn.click()
        check("injected corruption causes retransmission and delivery",
              pump(app, 90, lambda: sum(i.get("status") == "delivered" for i in msgs.items) >= 3))
        check("monitor table has rows", w.pages[3].table.rowCount() >= 4)
        w.pages[3].table.selectRow(0)
        pump(app, 0.5)
        w.grab().save(os.path.join(SHOTS, "4_monitor.png"))

        # ping + performance
        w.command({"cmd": "ping", "dst": 2, "count": 2})
        pump(app, 40, lambda: w.status and w.status["stats"]["ping_rtt"]
             and w.status["stats"]["ping_rtt"]["count"] >= 2)
        pump(app, 1.5)
        w.nav.button(4).click()
        pump(app, 1.5)
        st = w.status["stats"]
        check("performance shows measured retransmission", st["counters"]["retransmissions"] >= 1)
        check("metrics labelled SIMULATED", "SIMULATED" in w.pages[4].source_chip.text())
        w.grab().save(os.path.join(SHOTS, "5_performance.png"))

        # ---- encrypted message (needs secret.key, shared by both simulated engines)
        w.nav.button(2).click()
        check("encryption checkbox enabled when secret.key is loaded", msgs.encrypt.isEnabled())
        msgs.encrypt.setChecked(True)
        msgs.input.setText("ENCRYPTED HELLO")
        msgs.send_btn.click()
        check("encrypted message delivered and marked encrypted",
              pump(app, 90, lambda: any(i.get("status") == "delivered" and i.get("encrypted")
                                        for i in msgs.items)))
        msgs.encrypt.setChecked(False)
        w.nav.button(3).click()
        enc_rows = [r for r in w.pages[3].rows if r.get("frame") and "ENC" in r["frame"]["flags"]]
        check("monitor shows ENC frames carrying ciphertext (not the plaintext)",
              enc_rows and all(b"ENCRYPTED HELLO".hex() not in r["frame"]["payload_hex"].replace(" ", "")
                               for r in enc_rows))
        notes = [w.pages[3].table.item(r, 8).text() for r in range(w.pages[3].table.rowCount())]
        check("encrypted frames clearly labelled in the monitor",
              sum("ENC: payload is AES-GCM ciphertext" in n for n in notes) >= 2)
        w.pages[3].table.selectRow(len(notes) - 1)
        pump(app, 0.3)
        w.grab().save(os.path.join(SHOTS, "6_monitor_encrypted.png"))

        # ---- M6: Selective Repeat with an injected lost frame, MAC choice, ALOHA simulation
        w.nav.button(4).click()
        perf = w.pages[4]
        [b for b in perf.arq_group.buttons() if b.property("key") == "sr"][0].click()
        check("Selective Repeat selected in the engine",
              pump(app, 5, lambda: w.status and w.status["protocol_id"] == "sr"))
        w.nav.button(3).click()
        w.pages[3].dropframe_spin.setValue(2)
        w.pages[3].dropframe_btn.click()
        pump(app, 0.5)
        retx_before = w.status["stats"]["counters"]["retransmissions"]
        msgs.input.setText("SELECTIVE REPEAT DEMO: this message is long enough to need four frames of 24 bytes!!")
        msgs.send_btn.click()
        check("SR message with a lost frame is delivered",
              pump(app, 120, lambda: sum(i.get("status") == "delivered" for i in msgs.items) >= 5))
        pump(app, 1.5)
        check("SR resent exactly one frame",
              w.status["stats"]["counters"]["retransmissions"] - retx_before == 1)
        w.grab().save(os.path.join(SHOTS, "7_monitor_selective_repeat.png"))
        w.nav.button(4).click()
        [b for b in perf.mac_group.buttons() if b.property("key") == "aloha"][0].click()
        check("Pure ALOHA selected in the engine",
              pump(app, 5, lambda: w.status and w.status["mac"] == "aloha"))
        perf.sim_btn.click()
        check("ALOHA simulation drawn and labelled Simulation",
              len(perf.sim_chart.series) == 4 and "Simulation" in perf.sim_chip.text())
        pump(app, 1)
        w.grab().save(os.path.join(SHOTS, "8_protocols.png"))
        [b for b in perf.mac_group.buttons() if b.property("key") == "csma"][0].click()
        [b for b in perf.arq_group.buttons() if b.property("key") == "sw"][0].click()
        pump(app, 2)

        # ---- M7: relay A -> B -> C. Node 3 can only hear node 2 (SIMULATED range).
        third = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "run_engine.py"),
                                  "--node", "3", "--simulated", "--port", "5203", "--hears", "2"],
                                 cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import socket as _socket
            from shout_lan.linebuffer import LineBuffer
            time.sleep(2.5)
            watch = _socket.create_connection(("127.0.0.1", 5203), timeout=5)
            watch.settimeout(0.05)
            wbuf, wev = LineBuffer(), []
            w.nav.button(0).click()
            ov = w.pages[0]
            ov.route_dst.setValue(3)
            ov.route_via.setValue(2)
            ov.route_add.click()
            check("route 3 via 2 shown in the routing table",
                  pump(app, 5, lambda: w.status and w.status["routes"].get("3") == 2
                       and ov.routes.rowCount() == 1))
            pump(app, 0.5)
            w.grab().save(os.path.join(SHOTS, "9_overview_routes.png"))
            w.nav.button(2).click()
            msgs.dst.setValue(3)
            msgs.input.setText("HELLO COMPUTER C VIA B")
            msgs.send_btn.click()

            def node3_got_it():
                try:
                    data = watch.recv(65536)
                    if data:
                        wev.extend(wbuf.feed(data))
                except OSError:
                    pass
                return any(e.get("event") == "message_rx" and e.get("origin") == 1 and
                           e.get("hops") == 2 for e in wev)
            check("node 3 (out of range) receives the message relayed by node 2 (2 hops)",
                  pump(app, 180, node3_got_it))
            check("sender shows the honest relay status",
                  pump(app, 30, lambda: any(i.get("via") == 2 and i.get("status") == "delivered"
                                            for i in msgs.items)))
            pump(app, 1)
            w.grab().save(os.path.join(SHOTS, "10_messages_relay.png"))
            ov.route_del.click()
            pump(app, 2)
            msgs.dst.setValue(2)
            watch.close()
        finally:
            third.terminate()
            third.wait(timeout=10)

        # ---- every remaining control does something real (DoD A4)
        w.nav.button(1).click()
        page.volume.setValue(30)
        page.volume.sliderReleased.emit()
        check("volume slider changes the engine's volume",
              pump(app, 5, lambda: w.status and abs(w.status["volume"] - 0.30) < 1e-6))
        page.f0.setValue(2000)
        page.f1.setValue(3000)
        page.tones_btn.click()
        check("Apply tones reaches the engine",
              pump(app, 5, lambda: any("BFSK tones set" in it.text()
                                       for it in [w.pages[0].activity.item(i)
                                                  for i in range(w.pages[0].activity.count())])))
        check("calibration disabled on SIMULATED channel", not page.cal_btn.isEnabled())
        n_before = page.mic_combo.count()
        page.refresh_btn.click()
        check("Refresh devices re-lists real devices", page.mic_combo.count() == n_before > 0)
        w.nav.button(2).click()
        msgs.ping_btn.click()
        check("Ping button runs an acoustic-style ping (summary event)",
              pump(app, 90, lambda: "Ping statistics" in msgs.progress_label.text()))
        w.nav.button(3).click()
        w.pages[3].dropack_btn.click()
        check("Drop next ACK button arms the injection",
              pump(app, 5, lambda: w.status and w.status["inject"]["drop_next_ack"]))
        w.pages[3].clear_btn.click()
        check("Clear table empties the monitor", w.pages[3].table.rowCount() == 0)
        w.nav.button(4).click()
        w.pages[4].reset_btn.click()
        check("Reset sets measured counters to zero",
              pump(app, 5, lambda: w.status and w.status["stats"]["counters"]["frames_tx"] == 0))
        w.nav.button(1).click()
        page.disconnect_btn.click()
        check("Disconnect closes the control link", pump(app, 5, lambda: w.status is None))
        page.connect_btn.click()
        check("Connect re-opens the control link", pump(app, 10, lambda: w.status is not None))

        page.stop_engine()
        check("engine stops and UI shows disconnected",
              pump(app, 15, lambda: w.status is None))
    finally:
        other.terminate()
        other.wait(timeout=10)
        w.close()
    passed = sum(ok for _, ok in checks)
    print(f"\n{passed}/{len(checks)} UI checks passed; screenshots in {SHOTS}")
    import json
    path = os.path.join(ROOT, "results", f"ui_smoke_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w") as f:
        json.dump({"kind": "UI automation on the SIMULATED channel (offscreen Qt)",
                   "checks": [{"name": n, "passed": ok} for n, ok in checks],
                   "max_ui_stall_ms": round(MAX_STALL[0] * 1000, 1),
                   "slow_handlers": SLOW}, f, indent=1)
    print("saved:", path)
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
