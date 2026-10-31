"""
Packet Monitor & Logs: every frame this node sent or received, with all of its
fields per layer, plus a plain-English event log and the error-injection controls.
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget)

from ui import style
from ui.widgets import Card, page_header

COLUMNS = ["Time", "Dir", "Type", "Src", "Dst", "Seq", "Len", "CRC", "Notes"]


class MonitorPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        self.rows = []                      # event dicts, same order as the table rows
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Packet monitor & logs",
                                   "Every frame that really went out of the speaker or came in through "
                                   "the microphone. Click a row to see its layers."))

        inject = Card("Error injection (for demonstrations)",
                      "Press on the SENDING laptop to corrupt its next data frame; press on the "
                      "RECEIVING laptop to drop its next ACK. Each use is logged as 'injected'.")
        row = QHBoxLayout()
        self.corrupt_btn = QPushButton("Corrupt next DATA frame")
        self.corrupt_btn.setObjectName("warn")
        self.dropack_btn = QPushButton("Drop next ACK")
        self.dropack_btn.setObjectName("warn")
        self.clear_btn = QPushButton("Clear table")
        row.addWidget(self.corrupt_btn)
        row.addWidget(self.dropack_btn)
        row.addStretch()
        row.addWidget(self.clear_btn)
        inject.add(row)
        root.addWidget(inject)

        split = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)
        table_card = Card("Frames")
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for i in range(len(COLUMNS) - 1):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        table_card.add(self.table)
        top.addWidget(table_card)
        detail_card = Card("Frame layers")
        self.detail = QTextBrowser()
        self.detail.setStyleSheet("QTextBrowser{border:none;}")
        self.detail.setHtml(f"<p style='color:{style.MUTED}'>Select a frame.</p>")
        detail_card.add(self.detail)
        top.addWidget(detail_card)
        top.setSizes([640, 360])
        split.addWidget(top)
        log_card = Card("Event log")
        self.log = QTextBrowser()
        self.log.setStyleSheet("QTextBrowser{border:none; font-size:9.5pt;}")
        log_card.add(self.log)
        split.addWidget(log_card)
        split.setSizes([420, 200])
        root.addWidget(split, 1)

        self.corrupt_btn.clicked.connect(
            lambda: self.app.command({"cmd": "inject", "name": "corrupt_next_data", "value": True}))
        self.dropack_btn.clicked.connect(
            lambda: self.app.command({"cmd": "inject", "name": "drop_next_ack", "value": True}))
        self.clear_btn.clicked.connect(self.clear)
        self.table.itemSelectionChanged.connect(self.show_detail)

    def clear(self):
        self.rows = []
        self.table.setRowCount(0)

    # ---------------------------------------------------------------- events
    def on_event(self, e):
        ev = e["event"]
        if ev in ("frame_tx", "frame_rx"):
            self.add_row(e)
        text = e.get("text")
        if text and ev not in ("tx_start", "status"):
            color = {"timeout": style.BAD, "retransmit": style.WARN, "ack_dropped": style.WARN,
                     "injection_set": style.WARN, "error": style.BAD, "duplicate": style.WARN,
                     "message_rx": style.GOOD, "security_reject": style.BAD,
                     "encrypted": style.ACCENT}.get(ev, style.TEXT)
            if ev == "frame_rx" and not e.get("ok"):
                color = style.BAD
            if ev == "message_status" and e.get("status") == "delivered":
                color = style.GOOD
            if ev == "message_status" and e.get("status") == "failed":
                color = style.BAD
            t = time.strftime("%H:%M:%S", time.localtime(e.get("time", time.time())))
            self.log.append(f"<span style='color:{style.MUTED}'>{t}</span> "
                            f"<span style='color:{color}'>{text}</span>")

    def add_row(self, e):
        f = e.get("frame") or {}
        tx = e["event"] == "frame_tx"
        if tx:
            crc = "sent"
            notes = []
            if e.get("retry"):
                notes.append(f"retransmission #{e['retry']}")
            if e.get("injected"):
                notes.append(e["injected"])
            notes.append(f"{e.get('airtime', 0):.2f} s of sound")
        elif e.get("ok"):
            crc = "✓ OK"
            notes = [f"preamble {e.get('score', 0):.2f}", f"confidence {e.get('confidence', 0):.2f}"]
            if not e.get("for_me", True):
                notes.append("not for this node")
        else:
            crc = "✗ FAIL"
            notes = [e.get("error", "")]
        if "ENC" in f.get("flags", []):
            notes.insert(0, "🔒 ENC: payload is AES-GCM ciphertext")
        values = [time.strftime("%H:%M:%S", time.localtime(e["time"])), "TX →" if tx else "← RX",
                  f.get("type", "?"), f.get("src", "?"), f.get("dst", "?"), f.get("seq", "?"),
                  f.get("len", "?"), crc, " · ".join(notes)]
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, v in enumerate(values):
            item = QTableWidgetItem(str(v))
            if col == 7:
                item.setForeground(QColor(style.GOOD if "OK" in crc else
                                          style.BAD if "FAIL" in crc else style.MUTED))
            if col == 8 and (e.get("injected") or e.get("retry")):
                item.setForeground(QColor(style.WARN))
            self.table.setItem(row, col, item)
        self.rows.append(e)
        if row > 500:
            self.table.removeRow(0)
            self.rows.pop(0)
        self.table.scrollToBottom()

    def show_detail(self):
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return
        e = self.rows[sel[0].row()]
        f = e.get("frame")
        raw = e.get("raw", "")
        if f is None:
            self.detail.setHtml(
                f"<h3 style='color:{style.BAD}'>Rejected frame</h3><p>{e.get('error')}</p>"
                f"<p><b>Received bytes</b><br><code>{raw}</code></p>"
                "<p>The CRC (or the frame format) did not match, so this data was "
                "<b>not</b> delivered. The sender will notice the missing ACK and retransmit.</p>")
            return
        payload = bytes.fromhex(f["payload_hex"].replace(" ", "")) if f["payload_hex"] else b""
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "·" for b in payload)
        enc = "ENC" in f["flags"]
        def row(k, v):
            return f"<tr><td style='color:{style.MUTED}; padding-right:12px'>{k}</td><td>{v}</td></tr>"
        parts = [
            "<h4>Application data</h4><table>",
            row("Payload", ("<i>ciphertext (encrypted)</i> " if enc else "") + (ascii_ or "<i>none</i>")),
            row("Hex", f"<code>{f['payload_hex'] or '—'}</code>"), "</table>",
            "<h4>Network header</h4><table>",
            row("Origin", f["origin"]), row("Final destination", f["final"]), row("TTL", f["ttl"]),
            "</table><h4>Data-link header</h4><table>",
            row("Type", f["type"]), row("Flags", ", ".join(f["flags"]) or "—"),
            row("Link source", f["src"]), row("Link destination", f["dst"]),
            row("Sequence", f["seq"]), row("Length", f["len"]),
            row("CRC-16", "sent with frame" if e["event"] == "frame_tx" else "✓ verified"),
            "</table><h4>Physical layer</h4><table>",
        ]
        if e["event"] == "frame_tx":
            parts.append(row("Sound", f"{e.get('bytes')} bytes = {e.get('bytes', 0) * 8} bits, "
                                      f"{e.get('airtime')} s (BFSK + 0.1 s preamble)"))
        else:
            parts.append(row("Preamble match", e.get("score")))
            parts.append(row("Bit confidence", e.get("confidence")))
        parts.append("</table>")
        parts.append(f"<h4>All frame bytes</h4><code>{raw}</code>")
        self.detail.setHtml("".join(parts))

    def on_status(self, s):
        ok = s is not None
        self.corrupt_btn.setEnabled(ok)
        self.dropack_btn.setEnabled(ok)
