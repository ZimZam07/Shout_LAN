"""Overview: this node, the nodes really heard, a topology sketch and recent activity."""

import time

from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QHeaderView, QAbstractItemView)

from ui.widgets import Card, Chip, TopologyView, page_header


class OverviewPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Network overview",
                                   "What this node is doing and which other nodes it has actually heard."))

        grid = QGridLayout()
        grid.setSpacing(16)
        root.addLayout(grid, 1)

        # --- this node
        node = Card("This node")
        self.node_label = QLabel("Engine not connected")
        self.node_label.setObjectName("statValue")
        self.channel_chip = Chip("—")
        self.proto_label = QLabel("")
        self.proto_label.setObjectName("muted")
        node.add(self.node_label)
        node.add(self.channel_chip)
        node.add(self.proto_label)
        node.body.addStretch()
        grid.addWidget(node, 0, 0)

        # --- discovered nodes
        nodes = Card("Nodes heard", "Filled only from frames this laptop really decoded.")
        self.discover_btn = QPushButton("Discover (send HELLO)")
        self.discover_btn.setObjectName("primary")
        self.discover_btn.clicked.connect(lambda: self.app.command({"cmd": "hello"}))
        nodes.head.addWidget(self.discover_btn)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Node", "Last heard", "Preamble score"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(90)
        nodes.add(self.table)

        # --- routing table (network layer)
        rt_title = QLabel("Routing table (network layer)")
        rt_title.setObjectName("cardTitle")
        nodes.add(rt_title)
        hint = QLabel("To reach a node that is out of hearing range, send through a neighbour "
                      "(relay). Messages carry origin, final destination and a TTL.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        nodes.add(hint)
        self.routes = QTableWidget(0, 2)
        self.routes.setHorizontalHeaderLabels(["Destination", "Next hop (relay)"])
        self.routes.verticalHeader().setVisible(False)
        self.routes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.routes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routes.setMaximumHeight(110)
        nodes.add(self.routes)
        row = QHBoxLayout()
        row.addWidget(QLabel("Reach node"))
        self.route_dst = QSpinBox()
        self.route_dst.setRange(1, 254)
        self.route_dst.setValue(3)
        row.addWidget(self.route_dst)
        row.addWidget(QLabel("via node"))
        self.route_via = QSpinBox()
        self.route_via.setRange(1, 254)
        self.route_via.setValue(2)
        row.addWidget(self.route_via)
        self.route_add = QPushButton("Add route")
        self.route_del = QPushButton("Remove")
        row.addWidget(self.route_add)
        row.addWidget(self.route_del)
        row.addStretch()
        nodes.add(row)
        self.route_add.clicked.connect(lambda: self.app.command(
            {"cmd": "set_route", "dst": self.route_dst.value(), "via": self.route_via.value()}))
        self.route_del.clicked.connect(lambda: self.app.command(
            {"cmd": "set_route", "dst": self.route_dst.value(), "via": None}))
        grid.addWidget(nodes, 0, 1)

        # --- topology
        topo = Card("Topology", "This laptop in the centre; lines to every node it has heard.")
        self.topology = TopologyView()
        topo.add(self.topology)
        grid.addWidget(topo, 1, 0)

        # --- activity
        act = Card("Recent activity")
        self.activity = QListWidget()
        self.activity.setStyleSheet("QListWidget{border:none;} QListWidget::item{padding:4px;}")
        act.add(self.activity)
        grid.addWidget(act, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)

    def on_status(self, s):
        if s is None:
            self.node_label.setText("Engine not connected")
            self.channel_chip.set("—")
            self.proto_label.setText("")
            self.table.setRowCount(0)
            self.topology.set_data(None, {}, False)
            self.discover_btn.setEnabled(False)
            self.routes.setRowCount(0)
            for w in (self.route_add, self.route_del):
                w.setEnabled(False)
            return
        self.discover_btn.setEnabled(True)
        self.node_label.setText(f"Node {s['node_id']}")
        sim = s["channel"] != "acoustic"
        self.channel_chip.set(s["channel_label"], "warn" if sim else "good")
        self.proto_label.setText(f"Reliability: {s['protocol']}    •    "
                                 f"Tones {s['freq0']}/{s['freq1']} Hz    •    "
                                 f"{1 / s['bit_time']:.0f} bit/s")
        nodes = {int(k): v for k, v in s["nodes"].items()}
        self.table.setRowCount(len(nodes))
        now = time.time()
        for row, (nid, info) in enumerate(sorted(nodes.items())):
            self.table.setItem(row, 0, QTableWidgetItem(f"Node {nid}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{now - info['last_heard']:.0f} s ago"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{info['score']:.2f}"))
        routes = {int(k): v for k, v in s.get("routes", {}).items()}
        self.routes.setRowCount(len(routes))
        for row, (dst, via) in enumerate(sorted(routes.items())):
            self.routes.setItem(row, 0, QTableWidgetItem(f"Node {dst}"))
            self.routes.setItem(row, 1, QTableWidgetItem(f"Node {via}"))
        for w in (self.route_add, self.route_del):
            w.setEnabled(True)
        self.topology.set_data(s["node_id"], nodes, sim, routes)

    def on_event(self, e):
        text = e.get("text")
        if not text or e["event"] in ("tx_start", "status"):
            return
        self.activity.insertItem(0, f"{time.strftime('%H:%M:%S', time.localtime(e['time']))}   {text}"
                                 if "time" in e else text)
        while self.activity.count() > 60:
            self.activity.takeItem(self.activity.count() - 1)
