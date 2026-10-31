"""Messages: send text to another node over sound and watch its real delivery status."""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ui import style
from ui.widgets import Card, page_header

STATUS_TEXT = {"queued": ("Queued", style.MUTED), "sending": ("Sending…", style.ACCENT),
               "delivered": ("✓ Delivered (ACK received)", style.GOOD),
               "failed": ("✗ Failed - no acknowledgement", style.BAD)}


class Bubble(QWidget):
    """One chat message: text in a rounded box, and a status line under it."""

    def __init__(self, outgoing):
        super().__init__()
        self.outgoing = outgoing
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.box = QFrame()
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(12, 8, 12, 8)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(self.text)
        bg, fg = (style.ACCENT, "white") if outgoing else ("#EEF2F7", style.TEXT)
        self.box.setStyleSheet(f"QFrame{{background:{bg}; border-radius:14px;}}"
                               f"QLabel{{color:{fg}; font-size:10.5pt; background:transparent;}}")
        self.meta = QLabel()
        self.meta.setStyleSheet("font-size:8.5pt;")
        col.addWidget(self.box, 0, Qt.AlignRight if outgoing else Qt.AlignLeft)
        col.addWidget(self.meta, 0, Qt.AlignRight if outgoing else Qt.AlignLeft)
        if outgoing:
            row.addStretch(1)
            row.addLayout(col)
        else:
            row.addLayout(col)
            row.addStretch(1)

    def set(self, text, meta, meta_color):
        self.text.setText(text)
        # Give the wrapped label an explicit width and the matching height, otherwise Qt
        # may size it for one line and cut off the rest of a long message.
        fm = self.text.fontMetrics()
        width = min(fm.horizontalAdvance(text) + 8, 480)
        self.text.setFixedWidth(max(width, 40))
        self.text.setFixedHeight(self.text.heightForWidth(self.text.width()))
        self.meta.setText(meta)
        self.meta.setStyleSheet(f"font-size:8.5pt; color:{meta_color};")
        self.meta.setMinimumWidth(self.meta.fontMetrics().horizontalAdvance(meta) + 12)


class MessagesPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        self.items = []                 # chat entries in display order
        self.by_id = {}                 # msg_id -> entry (messages this node sent)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Messages",
                                   "Messages travel through the speaker and microphone. "
                                   "'Delivered' appears only after the receiver's ACK is heard."))

        chat = Card()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self.chat_layout = QVBoxLayout(holder)
        self.chat_layout.setContentsMargins(4, 4, 12, 4)
        self.chat_layout.addStretch(1)
        self.placeholder = QLabel("No messages yet. Start the engine, then send a message to another node.")
        self.placeholder.setObjectName("muted")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.chat_layout.addWidget(self.placeholder)
        self.scroll.setWidget(holder)
        chat.add(self.scroll)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        chat.add(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("muted")
        chat.add(self.progress_label)
        root.addWidget(chat, 1)

        compose = Card()
        row = QHBoxLayout()
        row.addWidget(QLabel("To node"))
        self.dst = QSpinBox()
        self.dst.setRange(1, 254)
        self.dst.setValue(2)
        row.addWidget(self.dst)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a short message (about 1 second of sound per 6 letters)…")
        self.input.setMaxLength(240)
        row.addWidget(self.input, 1)
        self.encrypt = QCheckBox("Encrypt (AES-GCM)")
        self.encrypt.setEnabled(False)
        self.encrypt.setToolTip("Needs secret.key on both laptops (tools/gen_key.py)")
        row.addWidget(self.encrypt)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primary")
        row.addWidget(self.send_btn)
        compose.add(row)
        row2 = QHBoxLayout()
        self.estimate = QLabel("")
        self.estimate.setObjectName("muted")
        row2.addWidget(self.estimate, 1)
        self.ping_btn = QPushButton("Ping this node ×4")
        row2.addWidget(self.ping_btn)
        compose.add(row2)
        root.addWidget(compose)

        self.send_btn.clicked.connect(self.send)
        self.input.returnPressed.connect(self.send)
        self.input.textChanged.connect(self._estimate)
        self.ping_btn.clicked.connect(
            lambda: self.app.command({"cmd": "ping", "dst": self.dst.value(), "count": 4}))
        self.render()

    def _estimate(self, text):
        n = len(text.encode("utf-8"))
        if not n:
            self.estimate.setText("")
            return
        frames = (n + 23) // 24
        air = (frames * 11 + n) * 8 / 50 + frames * 0.11
        self.estimate.setText(f"{n} bytes → {frames} frame(s), about {air:.1f} s of sound "
                              f"(+ about 2 s per ACK)")

    def send(self):
        text = self.input.text().strip()
        if not text:
            return
        cmd = {"cmd": "send", "dst": self.dst.value(), "text": text,
               "encrypt": self.encrypt.isChecked()}
        if self.app.command(cmd) is None:
            self.input.clear()

    # ---------------------------------------------------------------- rendering
    def render(self):
        """Create bubbles for new entries and refresh the status line of every bubble."""
        self.placeholder.setVisible(not self.items)
        added = False
        for it in self.items:
            if "widget" not in it:
                it["widget"] = Bubble(it["dir"] == "out")
                self.chat_layout.addWidget(it["widget"])
                added = True
            t = time.strftime("%H:%M:%S", time.localtime(it["time"]))
            lock = "  🔒 encrypted" if it.get("encrypted") else ""
            if it["dir"] == "out":
                label, color = STATUS_TEXT.get(it["status"], (it["status"], style.MUTED))
                if it.get("via") not in (None, it["peer"]):
                    if it["status"] == "delivered":
                        label = f"✓ Handed to relay node {it['via']} (hop ACK; end-to-end not confirmed)"
                    elif it["status"] == "sending":
                        label = f"Sending via relay node {it['via']}…"
                extra = (f" · frame {it['done']}/{it['frames']}"
                         if it["status"] == "sending" and it.get("frames") else "")
                extra += f" · {it['seconds']} s" if it.get("seconds") is not None else ""
                meta = f"to node {it['peer']} · {t} · {label}{extra}{lock}"
            elif it.get("rejected"):
                color = style.BAD
                meta = f"from node {it['peer']} · {t} · {it['reason']}"
            else:
                color = style.MUTED
                hops = f" · {it['hops']} hops" if it.get("hops", 1) > 1 else ""
                meta = f"from node {it['peer']} · {t} · CRC verified{hops}{lock}"
            it["widget"].set(it["text"], meta, color)
        if added:
            bar = self.scroll.verticalScrollBar()
            QTimer.singleShot(50, lambda: bar.setValue(bar.maximum()))

    # ---------------------------------------------------------------- engine events
    def on_event(self, e):
        ev = e["event"]
        if ev == "message_status":
            mid = e["msg_id"]
            if e["status"] == "queued":
                entry = {"dir": "out", "peer": e["dst"], "text": e.get("message", ""),
                         "status": "queued", "time": e["time"], "frames": e.get("frames"),
                         "done": 0, "encrypted": e.get("encrypted", False),
                         "via": e.get("via", e["dst"])}
                self.by_id[mid] = entry
                self.items.append(entry)
            elif mid in self.by_id:
                entry = self.by_id[mid]
                entry["status"] = e["status"]
                if "seconds" in e:
                    entry["seconds"] = e["seconds"]
            self._progress(e)
            self.render()
        elif ev == "message_progress" and e.get("msg_id") in self.by_id:
            entry = self.by_id[e["msg_id"]]
            entry["done"] = e["done"]
            self.progress.setVisible(True)
            self.progress.setMaximum(e["total"])
            self.progress.setValue(e["done"])
            self.progress_label.setText(e["text"])
            self.render()
        elif ev == "message_rx":
            self.items.append({"dir": "in", "peer": e["origin"], "text": e["message"],
                               "time": e["time"], "encrypted": e.get("encrypted"),
                               "hops": e.get("hops", 1)})
            self.render()
        elif ev == "security_reject":
            self.items.append({"dir": "in", "peer": e["origin"], "rejected": True,
                               "text": "⚠ Encrypted message rejected - not shown",
                               "reason": e.get("reason", ""), "time": e["time"]})
            self.render()
        elif ev in ("retransmit", "timeout"):
            self.progress_label.setText(e["text"])
        elif ev in ("ping_reply", "ping_timeout", "ping_summary", "ping_start"):
            self.progress_label.setText(e["text"])

    def _progress(self, e):
        if e["status"] == "sending":
            self.progress.setVisible(True)
            self.progress.setMaximum(max(1, e.get("frames", 1)))
            self.progress.setValue(0)
            self.progress_label.setText(e["text"])
        elif e["status"] in ("delivered", "failed"):
            self.progress.setVisible(False)
            self.progress_label.setText(e["text"])

    def on_status(self, s):
        connected = s is not None
        self.send_btn.setEnabled(connected)
        self.ping_btn.setEnabled(connected)
        enc = bool(s and s.get("encryption_available"))
        self.encrypt.setEnabled(enc)
        self.encrypt.setToolTip("AES-256-GCM with the pre-shared secret.key" if enc
                                else "Encryption needs secret.key on both laptops")
        if not enc:
            self.encrypt.setChecked(False)
