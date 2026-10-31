"""widgets.py - small reusable building blocks (cards, stat tiles, meters, charts, topology)."""

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui import style


class Card(QFrame):
    """White rounded panel with an optional title and subtitle."""

    def __init__(self, title=None, subtitle=None):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            head = QHBoxLayout()
            t = QLabel(title)
            t.setObjectName("cardTitle")
            head.addWidget(t)
            head.addStretch()
            self.head = head
            self.body.addLayout(head)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("muted")
            s.setWordWrap(True)
            self.body.addWidget(s)

    def add(self, widget_or_layout):
        if isinstance(widget_or_layout, QWidget):
            self.body.addWidget(widget_or_layout)
        else:
            self.body.addLayout(widget_or_layout)
        return widget_or_layout


class Chip(QLabel):
    def __init__(self, text="", kind="neutral"):
        super().__init__(text)
        self.set(text, kind)

    def set(self, text, kind="neutral"):
        self.setText(text)
        self.setStyleSheet(style.chip_style(kind))
        self.adjustSize()


class StatTile(QFrame):
    """Big number + small label. Shows '—' when there is no real value yet."""

    def __init__(self, label, hint=""):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(0)
        self.setMinimumHeight(72)
        self.value = QLabel("—")
        self.value.setObjectName("statValue")
        lab = QLabel(label)
        lab.setObjectName("statLabel")
        lay.addWidget(self.value)
        lay.addWidget(lab)
        if hint:
            self.setToolTip(hint)

    def set(self, text, color=None):
        self.value.setText(str(text))
        self.value.setStyleSheet(f"color:{color};" if color else "")


class LevelMeter(QWidget):
    """Horizontal microphone level bar (log scale), with the noise floor marked."""

    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.floor = None
        self.setMinimumHeight(14)
        self.setMaximumHeight(14)

    def set_level(self, level, floor=None):
        self.level = level or 0.0
        self.floor = floor
        self.update()

    @staticmethod
    def _pos(value):
        if value <= 0:
            return 0.0
        db = 20 * math.log10(max(value, 1e-6))          # -120 .. 0 dBFS
        return min(1.0, max(0.0, (db + 70) / 70))         # show -70 .. 0 dB

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 2, 0, -2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(style.BORDER))
        p.drawRoundedRect(r, 5, 5)
        w = r.width() * self._pos(self.level)
        p.setBrush(QColor(style.GOOD if self._pos(self.level) < 0.85 else style.WARN))
        p.drawRoundedRect(QRectF(r.x(), r.y(), w, r.height()), 5, 5)
        if self.floor:
            x = r.x() + r.width() * self._pos(self.floor)
            p.setPen(QPen(QColor(style.MUTED), 2))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))


class BarChart(QWidget):
    """Simple labelled bar chart (used for calibration SNR and RTT samples)."""

    def __init__(self, unit="", empty_text="No data yet"):
        super().__init__()
        self.items = []            # list of (label, value, highlight)
        self.unit = unit
        self.empty_text = empty_text
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_items(self, items):
        self.items = items
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(34, 10, -8, -24)
        if not self.items:
            p.setPen(QColor(style.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty_text)
            return
        top = max(max(v for _, v, _ in self.items), 1e-9)
        p.setPen(QPen(QColor(style.BORDER), 1))
        for frac in (0, 0.5, 1):
            y = r.bottom() - r.height() * frac
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(style.MUTED))
            p.setFont(QFont("Segoe UI", 7))
            p.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignRight | Qt.AlignVCenter,
                       f"{top * frac:.0f}{self.unit}")
            p.setPen(QPen(QColor(style.BORDER), 1))
        n = len(self.items)
        slot = r.width() / n
        step = max(1, n // 12)
        for i, (label, value, hl) in enumerate(self.items):
            h = r.height() * max(value, 0) / top
            x = r.left() + i * slot + slot * 0.15
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(style.ACCENT if hl else "#93C5FD"))
            p.drawRoundedRect(QRectF(x, r.bottom() - h, slot * 0.7, h), 2, 2)
            if i % step == 0:
                p.setPen(QColor(style.MUTED))
                p.setFont(QFont("Segoe UI", 7))
                p.drawText(QRectF(x - 10, r.bottom() + 4, slot * 0.7 + 20, 16), Qt.AlignCenter, label)


class LineChart(QWidget):
    """
    x/y chart with several series. Each series: (label, xs, ys, colour, dashed, points?).
    Used for the ALOHA simulation (dots = simulated, dashed line = theory).
    """

    def __init__(self, x_label="", y_label="", empty_text="No data yet"):
        super().__init__()
        self.series = []
        self.x_label, self.y_label, self.empty_text = x_label, y_label, empty_text
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_series(self, series):
        self.series = series
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.series:
            p.setPen(QColor(style.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty_text)
            return
        r = QRectF(self.rect()).adjusted(44, 26, -16, -34)
        xmax = max(max(s[1]) for s in self.series)
        ymax = max(max(s[2]) for s in self.series) * 1.1 or 1
        p.setFont(QFont("Segoe UI", 7))
        for frac in (0, 0.25, 0.5, 0.75, 1):
            y = r.bottom() - r.height() * frac
            p.setPen(QPen(QColor(style.BORDER), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(style.MUTED))
            p.drawText(QRectF(0, y - 7, 40, 14), Qt.AlignRight | Qt.AlignVCenter, f"{ymax * frac:.2f}")
        for frac in (0, 0.5, 1):
            x = r.left() + r.width() * frac
            p.drawText(QRectF(x - 20, r.bottom() + 4, 40, 14), Qt.AlignCenter, f"{xmax * frac:g}")
        p.drawText(QRectF(r.left(), r.bottom() + 16, r.width(), 16), Qt.AlignCenter, self.x_label)
        p.drawText(QRectF(r.left(), 4, r.width(), 16), Qt.AlignLeft, self.y_label)

        def pt(x, y):
            return QPointF(r.left() + r.width() * x / xmax, r.bottom() - r.height() * y / ymax)

        legend_x = r.right() - 210
        for n, (label, xs, ys, color, dashed, points) in enumerate(self.series):
            pen = QPen(QColor(color), 2, Qt.DashLine if dashed else Qt.SolidLine)
            p.setPen(pen)
            if points:
                p.setBrush(QColor(color))
                for x, y in zip(xs, ys):
                    p.drawEllipse(pt(x, y), 3, 3)
            else:
                for i in range(1, len(xs)):
                    p.drawLine(pt(xs[i - 1], ys[i - 1]), pt(xs[i], ys[i]))
            ly = r.top() + 4 + n * 16
            p.drawLine(QPointF(legend_x, ly + 6), QPointF(legend_x + 18, ly + 6))
            p.setPen(QColor(style.TEXT))
            p.drawText(QRectF(legend_x + 24, ly, 190, 14), Qt.AlignLeft | Qt.AlignVCenter, label)


class TopologyView(QWidget):
    """
    Draws THIS node, the nodes it has ACTUALLY heard, and nodes reached via a relay.
    Nothing is drawn for nodes that were never heard (except relays named in a route,
    which are marked "not heard yet").
    """

    def __init__(self):
        super().__init__()
        self.me = None
        self.nodes = {}
        self.simulated = False
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, me, nodes, simulated, routes=None):
        self.me, self.nodes, self.simulated = me, nodes, simulated
        self.routes = routes or {}
        self.update()

    def paintEvent(self, _):
        """
        Left-to-right picture: this laptop -> the neighbours it has really heard
        -> nodes reached through a relay (routing table, dashed orange).
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.me is None:
            p.setPen(QColor(style.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Engine not connected")
            return
        routes = getattr(self, "routes", {}) or {}
        others = sorted(self.nodes.items())
        relays = sorted({v for v in routes.values() if v not in self.nodes})
        column2 = [nid for nid, _ in others] + relays          # direct neighbours (+ unheard relays)
        w, h = self.width(), self.height()
        x_me, x_mid, x_far = w * 0.15, w * 0.5, w * 0.85

        def ys(n):
            return [h * (i + 1) / (n + 1) for i in range(n)]

        me = QPointF(x_me, h / 2)
        mid_pos = {nid: QPointF(x_mid, y) for nid, y in zip(column2, ys(len(column2)))}
        far_pos = {dst: QPointF(x_far, y) for (dst, _), y in zip(sorted(routes.items()),
                                                                  ys(len(routes)))}
        now = time.time()
        p.setFont(QFont("Segoe UI", 8))
        for nid, pt in mid_pos.items():
            info = self.nodes.get(nid)
            fresh = info is not None and now - info.get("last_heard", 0) < 120
            p.setPen(QPen(QColor(style.ACCENT if fresh else style.BORDER), 2,
                          Qt.DashLine if self.simulated or info is None else Qt.SolidLine))
            p.drawLine(me, pt)
        for dst, pt in far_pos.items():
            via = routes[dst]
            p.setPen(QPen(QColor(style.WARN), 2, Qt.DashLine))
            p.drawLine(mid_pos.get(via, me), pt)
        for nid, pt in mid_pos.items():
            info = self.nodes.get(nid)
            fresh = info is not None and now - info.get("last_heard", 0) < 120
            self._node(p, pt, str(nid), style.SURFACE, style.ACCENT if fresh else style.MUTED)
            label = (f"heard {now - info['last_heard']:.0f} s ago" if info else "not heard yet")
            p.setPen(QColor(style.MUTED))
            p.drawText(QRectF(pt.x() - 70, pt.y() + 20, 140, 16), Qt.AlignCenter, label)
        for dst, pt in far_pos.items():
            self._node(p, pt, str(dst), style.SURFACE, style.WARN)
            p.setPen(QColor(style.MUTED))
            p.drawText(QRectF(pt.x() - 70, pt.y() + 20, 140, 16), Qt.AlignCenter,
                       f"via node {routes[dst]}")
        self._node(p, me, str(self.me), style.ACCENT, "white", big=True)
        p.setPen(QColor(style.MUTED))
        p.drawText(QRectF(me.x() - 60, me.y() + 26, 120, 16), Qt.AlignCenter, "this laptop")
        if not others and not routes:
            p.drawText(QRectF(0, h - 22, w, 18), Qt.AlignCenter,
                       "No other node heard yet - press Discover")
        elif routes:
            p.drawText(QRectF(0, 4, w, 16), Qt.AlignCenter,
                       "blue = heard directly" + (" (dashed: SIMULATED)" if self.simulated else "")
                       + "        orange = reached through a relay")

    @staticmethod
    def _node(p, pt, text, fill, fg, big=False):
        r = 22 if big else 18
        p.setPen(QPen(QColor(style.ACCENT), 2))
        p.setBrush(QColor(fill))
        p.drawEllipse(pt, r, r)
        p.setPen(QColor(fg))
        p.setFont(QFont("Segoe UI", 10 if big else 9, QFont.Bold))
        p.drawText(QRectF(pt.x() - r, pt.y() - r, 2 * r, 2 * r), Qt.AlignCenter, text)


def page_header(title, subtitle):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    s = QLabel(subtitle)
    s.setObjectName("pageSub")
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w
