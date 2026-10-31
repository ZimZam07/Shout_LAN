"""
app.py - the SHOUT LAN desktop window: sidebar navigation, a status bar that
always shows both communication paths, and the five screens.
"""

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ui import style
from ui.control_client import ControlClient
from ui.pages.connect import ConnectPage
from ui.pages.messages import MessagesPage
from ui.pages.monitor import MonitorPage
from ui.pages.overview import OverviewPage
from ui.pages.performance import PerformancePage
from ui.widgets import Chip

PAGES = [("overview", "Overview"), ("connect", "Connect & Audio"), ("messages", "Messages"),
         ("monitor", "Packet Monitor & Logs"), ("performance", "Protocols & Performance")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SHOUT LAN - Acoustic Network")
        self.resize(1320, 840)
        self.setMinimumSize(1100, 720)
        self.client = ControlClient()
        self.status = None
        self.transmitting = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------------------------------------------------------- sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(256)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 20, 14, 16)
        sl.setSpacing(4)
        brand = QLabel("SHOUT LAN")
        brand.setObjectName("brand")
        sub = QLabel("Acoustic computer network")
        sub.setObjectName("brandSub")
        sl.addWidget(brand)
        sl.addWidget(sub)
        sl.addSpacing(22)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        for i, (key, title) in enumerate(PAGES):
            b = QPushButton("  " + title.replace("&", "&&"))     # "&" would mean a shortcut key
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(style.icon(key))
            self.nav.addButton(b, i)
            sl.addWidget(b)
        sl.addStretch()
        foot = QLabel("Messages between laptops travel\nonly through sound.")
        foot.setObjectName("brandSub")
        sl.addWidget(foot)
        outer.addWidget(side)

        # ---------------------------------------------------------------- main area
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(24, 10, 24, 10)
        tl.setSpacing(10)
        self.node_chip = Chip("No engine", "neutral")
        self.link_chip = Chip("Local control link (TCP): not connected", "bad")
        self.channel_chip = Chip("Acoustic link (sound): —", "neutral")
        self.tx_chip = Chip("", "accent")
        self.tx_chip.setVisible(False)
        tl.addWidget(self.node_chip)
        tl.addWidget(self.link_chip)
        tl.addWidget(self.channel_chip)
        tl.addStretch()
        tl.addWidget(self.tx_chip)
        ml.addWidget(top)

        self.stack = QStackedWidget()
        self.pages = [OverviewPage(self), ConnectPage(self), MessagesPage(self),
                      MonitorPage(self), PerformancePage(self)]
        for p in self.pages:
            self.stack.addWidget(p)
        ml.addWidget(self.stack, 1)
        outer.addWidget(main, 1)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.nav.button(1).setChecked(True)
        self.stack.setCurrentIndex(1)          # start on Connect & Audio

        # ---------------------------------------------------------------- signals
        self.client.event.connect(self.on_event)
        self.client.connected.connect(self.on_connected)
        self.client.disconnected.connect(self.on_disconnected)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.request_status)
        self.poll.start(1000)
        self.broadcast_status(None)

    # ---------------------------------------------------------------- commands
    def command(self, obj):
        """Send a command to the engine. Returns None, or an error text that is also shown."""
        error = self.client.send(obj)
        if error:
            self.pages[3].on_event({"event": "error", "text": f"Command not sent: {error}"})
            self.pages[1].log_console(f"Command not sent: {error}")
        return error

    def request_status(self):
        if self.client.is_connected:
            self.client.send({"cmd": "status"})

    # ---------------------------------------------------------------- engine events
    def on_connected(self, address):
        self.link_chip.set(f"Local control link (TCP): connected {address}", "good")
        self.pages[1].set_connected(True, address)
        self.request_status()

    def on_disconnected(self, reason):
        self.link_chip.set(f"Local control link (TCP): not connected", "bad")
        self.pages[1].set_connected(False)
        self.status = None
        self.broadcast_status(None)

    def on_event(self, e):
        ev = e.get("event")
        if ev == "status":
            self.status = e
            self.broadcast_status(e)
            return
        if ev == "tx_start":
            self.set_tx(True, f"● Transmitting {e.get('type')} ({e.get('airtime')} s of sound)")
        elif ev == "frame_tx":
            self.set_tx(False)
        for p in self.pages:
            p.on_event(e)

    def set_tx(self, on, text=""):
        self.tx_chip.setVisible(on)
        if on:
            self.tx_chip.set(text, "accent")

    def broadcast_status(self, s):
        if s is None:
            self.node_chip.set("No engine", "neutral")
            self.channel_chip.set("Acoustic link (sound): —", "neutral")
            self.set_tx(False)
        else:
            self.node_chip.set(f"Node {s['node_id']}", "accent")
            if s["channel"] == "acoustic":
                self.channel_chip.set(s["channel_label"], "good")
            else:
                self.channel_chip.set("SIMULATED channel - not sound", "warn")
        for p in self.pages:
            p.on_status(s)

    def closeEvent(self, event):
        self.client.close()
        page = self.pages[1]
        if page.process is not None and page.process.state() != QProcess.NotRunning:
            page.stop_engine()
        super().closeEvent(event)
