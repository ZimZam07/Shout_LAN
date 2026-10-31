"""
Connect & Audio: start/stop the local engine, connect the UI to it over the
local TCP control link, choose audio devices, volume, tones and calibrate.
"""

import os
import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QRadioButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from shout_lan import config
from ui.widgets import BarChart, Card, Chip, LevelMeter, page_header

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ConnectPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        self.process = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Connect & audio",
                                   "Start this laptop's engine, connect to it, and set up the speaker "
                                   "and microphone."))
        grid = QGridLayout()
        grid.setSpacing(16)
        root.addLayout(grid, 1)

        # ---------------------------------------------------------------- engine
        eng = Card("1. Engine (this laptop)",
                   "The engine does all networking and drives the speaker and microphone.")
        form = QFormLayout()
        form.setSpacing(8)
        self.node_spin = QSpinBox()
        self.node_spin.setRange(1, 254)
        self.node_spin.setValue(1)
        self.node_spin.setMaximumWidth(140)
        form.addRow("Node ID", self.node_spin)
        mode = QHBoxLayout()
        self.acoustic_radio = QRadioButton("Acoustic link (sound)")
        self.sim_radio = QRadioButton("SIMULATED channel (backup)")
        self.acoustic_radio.setChecked(True)
        mode.addWidget(self.acoustic_radio)
        mode.addWidget(self.sim_radio)
        form.addRow("Channel", mode)
        self.mic_combo = QComboBox()
        self.spk_combo = QComboBox()
        for combo in (self.mic_combo, self.spk_combo):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(24)
        form.addRow("Microphone", self.mic_combo)
        form.addRow("Speaker", self.spk_combo)
        self.exclusive_check = QCheckBox("Exclusive mic mode (bypasses Windows noise suppression)")
        self.exclusive_check.setChecked(True)
        form.addRow("", self.exclusive_check)
        eng.add(form)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start engine")
        self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop engine")
        self.stop_btn.setObjectName("danger")
        self.refresh_btn = QPushButton("Refresh devices")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addStretch()
        row.addWidget(self.refresh_btn)
        eng.add(row)
        self.engine_chip = Chip("Engine not running", "neutral")
        eng.add(self.engine_chip)
        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(500)
        self.console.setMinimumHeight(120)
        eng.add(self.console)
        grid.addWidget(eng, 0, 0, 2, 1)

        # ---------------------------------------------------------------- control link
        link = Card("2. Local control link (TCP)",
                    "The app is a TCP client of the engine on 127.0.0.1. This link never "
                    "leaves this laptop - laptops talk to each other only through sound.")
        lf = QHBoxLayout()
        lf.addWidget(QLabel("127.0.0.1 :"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(config.CONTROL_PORT)
        self.port_spin.setMaximumWidth(120)
        lf.addWidget(self.port_spin)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.disconnect_btn = QPushButton("Disconnect")
        lf.addWidget(self.connect_btn)
        lf.addWidget(self.disconnect_btn)
        lf.addStretch()
        link.add(lf)
        self.link_chip = Chip("Not connected", "bad")
        link.add(self.link_chip)
        link.body.addStretch()
        grid.addWidget(link, 0, 1)

        # ---------------------------------------------------------------- audio
        audio = Card("3. Audio", "Volume and tones apply to the running engine. Both laptops "
                                 "must use the SAME tones.")
        af = QFormLayout()
        vol = QHBoxLayout()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(2, 100)
        self.volume.setValue(int(config.TX_VOLUME * 100))
        self.volume_label = QLabel(f"{config.TX_VOLUME:.2f}")
        vol.addWidget(self.volume, 1)
        vol.addWidget(self.volume_label)
        af.addRow("Transmit volume", vol)
        tones = QHBoxLayout()
        self.f0 = QSpinBox()
        self.f1 = QSpinBox()
        for sb, v in ((self.f0, config.FREQ_0), (self.f1, config.FREQ_1)):
            sb.setRange(300, 8000)
            sb.setSingleStep(250)
            sb.setSuffix(" Hz")
            sb.setValue(v)
        self.tones_btn = QPushButton("Apply tones")
        tones.addWidget(QLabel("bit 0"))
        tones.addWidget(self.f0)
        tones.addWidget(QLabel("bit 1"))
        tones.addWidget(self.f1)
        tones.addWidget(self.tones_btn)
        af.addRow("BFSK tones", tones)
        self.meter = LevelMeter()
        self.meter_note = QLabel("")
        self.meter_note.setObjectName("muted")
        meter_row = QHBoxLayout()
        meter_row.addWidget(self.meter, 1)
        meter_row.addWidget(self.meter_note)
        af.addRow("Microphone level", meter_row)
        audio.add(af)
        cal_row = QHBoxLayout()
        self.cal_btn = QPushButton("Calibrate (noise + tone sweep)")
        self.cal_label = QLabel("Not calibrated yet")
        self.cal_label.setObjectName("muted")
        self.cal_label.setWordWrap(True)
        cal_row.addWidget(self.cal_btn)
        cal_row.addWidget(self.cal_label, 1)
        audio.add(cal_row)
        self.snr_chart = BarChart(unit=" dB", empty_text="Run calibration to see the SNR of each tone")
        audio.add(self.snr_chart)
        snr_note = QLabel("SNR measured inside the detector's narrow (~18 Hz) frequency bin; "
                          "dark bars are the two BFSK tones in use.")
        snr_note.setObjectName("muted")
        snr_note.setWordWrap(True)
        audio.add(snr_note)
        grid.addWidget(audio, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # ---------------------------------------------------------------- wiring
        self.start_btn.clicked.connect(self.start_engine)
        self.stop_btn.clicked.connect(self.stop_engine)
        self.refresh_btn.clicked.connect(self.load_devices)
        self.connect_btn.clicked.connect(self.connect_engine)
        self.disconnect_btn.clicked.connect(self.app.client.close)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v / 100:.2f}"))
        self.volume.sliderReleased.connect(
            lambda: self.app.command({"cmd": "set_volume", "value": self.volume.value() / 100}))
        self.tones_btn.clicked.connect(
            lambda: self.app.command({"cmd": "set_tones", "freq0": self.f0.value(),
                                      "freq1": self.f1.value()}))
        self.cal_btn.clicked.connect(self.calibrate)
        self.sim_radio.toggled.connect(self._mode_changed)
        self.load_devices()
        self.set_connected(False)

    # ---------------------------------------------------------------- devices
    def load_devices(self):
        """List the REAL audio devices Windows reports (WASAPI ones first)."""
        self.mic_combo.clear()
        self.spk_combo.clear()
        try:
            from shout_lan.audio_io import default_devices, list_devices
            devs = list_devices()
            mic, spk = default_devices()
        except Exception as exc:
            self.log_console(f"Could not list audio devices: {exc}")
            return
        for combo, items, default in ((self.mic_combo, devs["inputs"], mic),
                                      (self.spk_combo, devs["outputs"], spk)):
            items = sorted(items, key=lambda d: d["api"] != "Windows WASAPI")
            for d in items:
                combo.addItem(f"[{d['index']}] {d['name']}  ({d['api']})", d["index"])
            idx = combo.findData(default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        if self.mic_combo.count() == 0:
            self.log_console("No microphone found - only the SIMULATED channel can be used.")

    def _mode_changed(self, sim):
        for w in (self.mic_combo, self.spk_combo, self.exclusive_check):
            w.setEnabled(not sim)

    # ---------------------------------------------------------------- engine process
    def start_engine(self):
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.log_console("Engine is already running.")
            return
        args = ["-u", os.path.join(ROOT, "run_engine.py"), "--node", str(self.node_spin.value()),
                "--port", str(self.port_spin.value()),
                "--volume", f"{self.volume.value() / 100:.2f}",
                "--f0", str(self.f0.value()), "--f1", str(self.f1.value())]
        if self.sim_radio.isChecked():
            args.append("--simulated")
        else:
            if self.mic_combo.currentData() is None:
                self.log_console("Select a microphone first.")
                return
            args += ["--mic", str(self.mic_combo.currentData()),
                     "--speaker", str(self.spk_combo.currentData())]
            if not self.exclusive_check.isChecked():
                args.append("--shared")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(ROOT)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.log_console("$ python " + " ".join(args[1:]))
        self.process.start(sys.executable, args)
        self.engine_chip.set("Engine starting…", "warn")

    def _read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            self.log_console(line)
            if line.startswith("READY"):
                self.engine_chip.set("Engine running", "good")
                self.connect_engine()
            elif line.startswith("ERROR"):
                self.engine_chip.set("Engine failed to start - see console", "bad")

    def _finished(self, code, _status):
        self.log_console(f"Engine process exited (code {code}).")
        if code != 0:
            self.engine_chip.set(f"Engine stopped with error (code {code})", "bad")
        else:
            self.engine_chip.set("Engine not running", "neutral")

    def stop_engine(self):
        if self.app.client.is_connected:
            self.app.command({"cmd": "shutdown"})
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            if not self.process.waitForFinished(8000):
                self.process.kill()

    def connect_engine(self):
        error = self.app.client.connect_to("127.0.0.1", self.port_spin.value())
        if error:
            self.link_chip.set(f"Connection failed: {error}", "bad")
            self.log_console(f"connect() failed: {error}")

    # ---------------------------------------------------------------- calibration
    def calibrate(self):
        if self.app.command({"cmd": "calibrate"}) is None:
            self.cal_btn.setEnabled(False)
            self.cal_label.setText("Calibrating… keep the room quiet (about 6 s).")

    # ---------------------------------------------------------------- updates
    def log_console(self, line):
        self.console.appendPlainText(line)

    def set_connected(self, ok, address=""):
        self.connect_btn.setEnabled(not ok)
        self.disconnect_btn.setEnabled(ok)
        for w in (self.cal_btn, self.tones_btn, self.volume):
            w.setEnabled(ok)
        if ok:
            self.link_chip.set(f"Connected to engine at {address}", "good")
        else:
            self.link_chip.set("Not connected", "bad")
            self.meter.set_level(0)

    def on_status(self, s):
        if s is None:
            self.meter_note.setText("")
            return
        acoustic = s["channel"] == "acoustic"
        if acoustic:
            self.meter.set_level(s.get("mic_level", 0), s.get("noise_floor"))
            self.meter_note.setText(f"overflows {s.get('mic_overflows', 0)}")
        else:
            self.meter.set_level(0)
            self.meter_note.setText("no microphone (SIMULATED)")
        self.cal_btn.setEnabled(acoustic and self.cal_label.text() != "Calibrating… keep the room quiet (about 6 s).")
        self.cal_btn.setToolTip("" if acoustic else "Calibration measures real sound - not available "
                                                    "on the SIMULATED channel")
        if not self.volume.isSliderDown():
            self.volume.blockSignals(True)
            self.volume.setValue(int(round(s["volume"] * 100)))
            self.volume.blockSignals(False)
            self.volume_label.setText(f"{s['volume']:.2f}")
        if s["channel"] == "acoustic":
            self.engine_chip.set(f"Engine running - node {s['node_id']} on the acoustic link", "good")
        else:
            self.engine_chip.set(f"Engine running - node {s['node_id']} on the SIMULATED channel", "warn")

    def on_event(self, e):
        if e["event"] == "calibration":
            self.cal_btn.setEnabled(True)
            self.cal_label.setText(e["text"])
            if e.get("ok"):
                f0, f1 = e["freq0"], e["freq1"]
                items = [(f"{int(f) / 1000:g}k", max(v, 0), int(f) in (f0, f1))
                         for f, v in sorted(e["snr_db"].items(), key=lambda kv: int(kv[0]))]
                self.snr_chart.set_items(items)
        elif e["event"] == "setting" and e.get("name") == "tones":
            self.f0.setValue(e["value"][0])
            self.f1.setValue(e["value"][1])
