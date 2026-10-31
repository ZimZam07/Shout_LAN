"""
Protocols & Performance: choose the reliability protocol and the medium-access
rule, read what each one does, see what the engine has really measured, and
run the (clearly labelled) many-node ALOHA simulation.
"""

from PySide6.QtWidgets import (QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from shout_lan import aloha_sim, mac
from ui import style
from ui.widgets import BarChart, Card, Chip, LineChart, StatTile, page_header

ARQ_EXPLAIN = {
    "sw": "Stop-and-Wait: send one frame, wait for its ACK, resend after a timeout. "
          "Sequence numbers let the receiver recognise duplicates so each frame is delivered once. "
          "Simple, but the channel is idle while waiting.",
    "sr": "Selective Repeat (sliding window of 4): send up to 4 frames back-to-back, then the "
          "receiver answers with ONE selective ACK listing exactly which frames arrived. Only the "
          "missing frames are resent; out-of-order frames wait in the receiver's buffer.",
}


class PerformancePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setObjectName("page")
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Protocols & performance",
                                   "Choose how the node sends reliably and how it shares the channel, "
                                   "then see what was really measured."))

        # ---------------------------------------------------------------- protocol choice
        top = QHBoxLayout()
        top.setSpacing(16)
        arq_card = Card("Reliability (data link layer)")
        self.arq_group = QButtonGroup(self)
        row = QHBoxLayout()
        for i, (key, label) in enumerate((("sw", "Stop-and-Wait"), ("sr", "Selective Repeat (window 4)"))):
            b = QRadioButton(label)
            b.setProperty("key", key)
            self.arq_group.addButton(b, i)
            row.addWidget(b)
        row.addStretch()
        arq_card.add(row)
        self.arq_text = QLabel(ARQ_EXPLAIN["sw"])
        self.arq_text.setWordWrap(True)
        arq_card.add(self.arq_text)
        self.timer_label = QLabel("")
        self.timer_label.setObjectName("muted")
        arq_card.add(self.timer_label)
        top.addWidget(arq_card, 1)

        mac_card = Card("Medium access (MAC sublayer)")
        self.mac_group = QButtonGroup(self)
        row = QHBoxLayout()
        for i, key in enumerate((mac.CSMA, mac.ALOHA, mac.NONE)):
            b = QRadioButton({"csma": "CSMA", "aloha": "Pure ALOHA", "none": "None"}[key])
            b.setProperty("key", key)
            self.mac_group.addButton(b, i)
            row.addWidget(b)
        row.addStretch()
        self.busy_chip = Chip("Channel: —", "neutral")
        row.addWidget(self.busy_chip)
        mac_card.add(row)
        self.mac_text = QLabel(mac.EXPLAIN[mac.CSMA])
        self.mac_text.setWordWrap(True)
        mac_card.add(self.mac_text)
        top.addWidget(mac_card, 1)
        root.addLayout(top)

        # ---------------------------------------------------------------- metrics
        metrics = Card("Measured metrics")
        self.source_chip = Chip("—")
        metrics.head.addWidget(self.source_chip)
        self.reset_btn = QPushButton("Reset")
        metrics.head.addWidget(self.reset_btn)
        grid = QGridLayout()
        grid.setSpacing(10)
        names = [("frames_tx", "Frames sent"), ("frames_rx_ok", "Frames received (CRC OK)"),
                 ("crc_failures", "CRC failures"), ("retransmissions", "Retransmissions"),
                 ("timeouts", "Timeouts"), ("duplicates", "Duplicates suppressed"),
                 ("deferrals", "CSMA deferrals (busy)"), ("backoffs", "ALOHA backoffs"),
                 ("delivery", "Message delivery ratio"), ("loss", "Frame loss (needed resend)"),
                 ("goodput", "Goodput (useful bit/s)"), ("rtt", "Round trip avg (frame/burst → ACK)"),
                 ("ping", "Ping RTT min/avg/max (s)"), ("pingloss", "Ping loss"),
                 ("security", "Rejected by security"), ("received", "Messages received")]
        self.tiles = {}
        for i, (key, label) in enumerate(names):
            tile = StatTile(label)
            self.tiles[key] = tile
            grid.addWidget(tile, i // 4, i % 4)
        metrics.add(grid)
        root.addWidget(metrics)

        charts = QHBoxLayout()
        charts.setSpacing(16)
        rtt_card = Card("Round-trip times", "Most recent 30, in seconds.")
        self.rtt_chart = BarChart(unit=" s", empty_text="No acknowledged frames yet")
        rtt_card.add(self.rtt_chart)
        charts.addWidget(rtt_card, 1)

        sim_card = Card("ALOHA with many nodes", "SIMULATION - not acoustic. With only two laptops "
                        "we can't create heavy traffic, so this simulates thousands of frames and "
                        "compares the result with the textbook formulas.")
        self.sim_chip = Chip("Simulation", "warn")
        sim_card.head.addWidget(self.sim_chip)
        self.sim_btn = QPushButton("Run simulation")
        sim_card.head.addWidget(self.sim_btn)
        self.sim_chart = LineChart("offered load G (frames per frame-time)", "throughput S",
                                   "Press 'Run simulation'")
        sim_card.add(self.sim_chart)
        self.sim_label = QLabel("")
        self.sim_label.setObjectName("muted")
        self.sim_label.setWordWrap(True)
        sim_card.add(self.sim_label)
        charts.addWidget(sim_card, 1)
        root.addLayout(charts)

        self.reset_btn.clicked.connect(lambda: self.app.command({"cmd": "reset_stats"}))
        self.arq_group.buttonClicked.connect(
            lambda b: self.app.command({"cmd": "set_protocol", "value": b.property("key")}))
        self.mac_group.buttonClicked.connect(
            lambda b: self.app.command({"cmd": "set_mac", "value": b.property("key")}))
        self.sim_btn.clicked.connect(self.run_simulation)

    def run_simulation(self):
        """Pure computation in the app (no engine, no sound) - labelled 'Simulation'."""
        r = aloha_sim.sweep(frame_times=20000)
        g = r["loads"]
        self.sim_chart.set_series([
            ("Pure ALOHA - simulated", g, r["pure"], style.ACCENT, False, True),
            ("Pure ALOHA - theory G·e^(-2G)", g, r["pure_theory"], style.ACCENT, True, False),
            ("Slotted ALOHA - simulated", g, r["slotted"], style.GOOD, False, True),
            ("Slotted ALOHA - theory G·e^(-G)", g, r["slotted_theory"], style.GOOD, True, False),
        ])
        bp = max(r["pure"])
        bs = max(r["slotted"])
        self.sim_label.setText(
            f"Simulated maximum: pure {bp:.3f} at G={g[r['pure'].index(bp)]}, slotted {bs:.3f} at "
            f"G={g[r['slotted'].index(bs)]} (theory 0.184 at G=0.5 and 0.368 at G=1.0). "
            f"20 000 frame-times per point.")

    def on_status(self, s):
        if s is None:
            self.source_chip.set("Not connected", "neutral")
            self.busy_chip.set("Channel: —", "neutral")
            for g in (self.arq_group, self.mac_group):
                for b in g.buttons():
                    b.setEnabled(False)
            return
        for g in (self.arq_group, self.mac_group):
            for b in g.buttons():
                b.setEnabled(True)
                if b.property("key") in (s.get("protocol_id"), s.get("mac")):
                    b.setChecked(True)
        self.arq_text.setText(ARQ_EXPLAIN.get(s.get("protocol_id"), ""))
        self.mac_text.setText(mac.EXPLAIN.get(s.get("mac"), ""))
        self.busy_chip.set("Channel BUSY" if s.get("channel_busy") else "Channel idle",
                           "warn" if s.get("channel_busy") else "good")
        st = s["stats"]
        c = st["counters"]
        self.timer_label.setText(f"Retransmission timeout (adapts to measured round trips): {s['rto']:.2f} s")
        self.source_chip.set(st["source"], "good" if "acoustic" in st["source"] else "warn")
        for key in ("frames_tx", "frames_rx_ok", "crc_failures", "retransmissions",
                    "timeouts", "duplicates"):
            self.tiles[key].set(c[key])
        self.tiles["deferrals"].set(c["channel_busy_deferrals"])
        self.tiles["backoffs"].set(c["backoffs"])
        self.tiles["security"].set(c.get("security_rejects", 0))
        self.tiles["received"].set(c["messages_received"])
        dr = st["delivery_ratio"]
        self.tiles["delivery"].set("—" if dr is None else f"{dr:.0%}",
                                   None if dr is None else style.GOOD if dr >= 0.9 else style.WARN)
        fl = st["frame_loss"]
        self.tiles["loss"].set("—" if fl is None else f"{fl:.0%}")
        gp = st["goodput_bps"]
        self.tiles["goodput"].set("—" if gp is None else f"{gp:.1f}")
        rtt = st["rtt"]
        self.tiles["rtt"].set("—" if rtt is None else f"{rtt['avg']:.2f} s")
        ping = st["ping_rtt"]
        self.tiles["ping"].set("—" if ping is None else
                               f"{ping['min']:.1f}/{ping['avg']:.1f}/{ping['max']:.1f}")
        pl = st["ping_loss"]
        self.tiles["pingloss"].set("—" if pl is None else f"{pl:.0%}")
        recent = st.get("recent_rtts", [])
        self.rtt_chart.set_items([(str(i + 1), v, False) for i, v in enumerate(recent)])

    def on_event(self, e):
        pass
