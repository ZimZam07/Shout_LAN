"""Protocols & Performance: which protocol is running, what it does, and measured metrics."""

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import style
from ui.widgets import BarChart, Card, Chip, StatTile, page_header

EXPLAIN = {
    "Stop-and-Wait": "The sender transmits one frame and waits for its acknowledgement (ACK). "
                     "If no ACK arrives before the timer expires, it sends the same frame again. "
                     "Sequence numbers let the receiver recognise duplicates so each frame is delivered once.",
}


class PerformancePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Protocols & performance",
                                   "The reliability protocol in use and what the engine has really measured."))

        proto = Card("Reliability protocol")
        self.proto_name = QLabel("—")
        self.proto_name.setObjectName("statValue")
        self.proto_text = QLabel("")
        self.proto_text.setWordWrap(True)
        self.timer_label = QLabel("")
        self.timer_label.setObjectName("muted")
        proto.add(self.proto_name)
        proto.add(self.proto_text)
        proto.add(self.timer_label)
        root.addWidget(proto)

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
                 ("delivery", "Message delivery ratio"), ("loss", "Frame loss (needed resend)"),
                 ("goodput", "Goodput (useful bit/s)"), ("rtt", "Frame RTT avg"),
                 ("ping", "Ping RTT min/avg/max"), ("pingloss", "Ping loss")]
        self.tiles = {}
        for i, (key, label) in enumerate(names):
            tile = StatTile(label)
            self.tiles[key] = tile
            grid.addWidget(tile, i // 4, i % 4)
        metrics.add(grid)
        root.addWidget(metrics)

        chart = Card("Frame round-trip times", "Time from the start of each data frame until its ACK "
                                               "was heard (most recent 30).")
        self.rtt_chart = BarChart(unit=" s", empty_text="No acknowledged frames yet")
        chart.add(self.rtt_chart)
        root.addWidget(chart, 1)
        self.reset_btn.clicked.connect(lambda: self.app.command({"cmd": "reset_stats"}))

    def on_status(self, s):
        if s is None:
            self.source_chip.set("Not connected", "neutral")
            return
        st = s["stats"]
        c = st["counters"]
        self.proto_name.setText(s["protocol"])
        self.proto_text.setText(EXPLAIN.get(s["protocol"], ""))
        self.timer_label.setText(f"Current retransmission timeout (adapts to measured round trips): "
                                 f"{s['rto']:.2f} s")
        self.source_chip.set(st["source"], "good" if "acoustic" in st["source"] else "warn")
        for key in ("frames_tx", "frames_rx_ok", "crc_failures", "retransmissions",
                    "timeouts", "duplicates"):
            self.tiles[key].set(c[key])
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
