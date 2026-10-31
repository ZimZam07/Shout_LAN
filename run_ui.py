"""
run_ui.py - start the SHOUT LAN desktop app.

    python run_ui.py

In the app: Connect & Audio -> choose node id and devices -> Start engine.
(Or start the engine yourself with run_engine.py and press Connect.)
"""

import sys

from PySide6.QtWidgets import QApplication

from ui import style
from ui.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SHOUT LAN")
    app.setStyleSheet(style.QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
