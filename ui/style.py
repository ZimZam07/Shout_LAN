"""style.py - colours, the Qt style sheet and small line icons for the SHOUT LAN app."""

import os

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# A restrained palette: one accent colour plus status colours.
BG = "#F4F6FA"
SURFACE = "#FFFFFF"
BORDER = "#E3E8EF"
TEXT = "#1E293B"
MUTED = "#64748B"
SIDEBAR = "#111827"
SIDEBAR_TEXT = "#CBD5E1"
ACCENT = "#2563EB"
ACCENT_SOFT = "#DBEAFE"
GOOD = "#16A34A"
GOOD_SOFT = "#DCFCE7"
WARN = "#B45309"
WARN_SOFT = "#FEF3C7"
BAD = "#DC2626"
BAD_SOFT = "#FEE2E2"

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets").replace("\\", "/")

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#page {{ background: {BG}; }}
QWidget#sidebar {{ background: {SIDEBAR}; }}
QLabel#brand {{ color: white; font-size: 15pt; font-weight: 700; }}
QLabel#brandSub {{ color: #94A3B8; font-size: 8.5pt; }}
QPushButton#nav {{
    color: {SIDEBAR_TEXT}; background: transparent; border: none; text-align: left;
    padding: 10px 14px; border-radius: 8px; font-size: 10.5pt;
}}
QPushButton#nav:hover {{ background: #1F2937; color: white; }}
QPushButton#nav:checked {{ background: {ACCENT}; color: white; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#pageTitle {{ font-size: 17pt; font-weight: 700; }}
QLabel#pageSub {{ color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#statValue {{ font-size: 17pt; font-weight: 700; padding-bottom: 2px; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 9pt; }}
QWidget#topbar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #A0AEC0; background: #F1F5F9; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #1D4ED8; }}
QPushButton#primary:disabled {{ background: #93C5FD; color: white; }}
QPushButton#danger {{ background: {BAD_SOFT}; color: {BAD}; border: 1px solid #FECACA; }}
QPushButton#warn {{ background: {WARN_SOFT}; color: {WARN}; border: 1px solid #FDE68A; }}
QLineEdit, QSpinBox, QComboBox, QDoubleSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; border-left: 1px solid {BORDER};
    background: #F8FAFC;
}}
QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 8px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {ACCENT_SOFT}; }}
QSpinBox::up-arrow {{ image: url({ASSETS}/up.svg); width: 9px; height: 6px; }}
QSpinBox::down-arrow {{ image: url({ASSETS}/down.svg); width: 9px; height: 6px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({ASSETS}/down.svg); width: 10px; height: 7px; }}
QComboBox QAbstractItemView {{ border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT}; background: {SURFACE}; }}
QTableWidget {{
    background: {SURFACE}; border: none; gridline-color: {BORDER};
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #F8FAFC; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: 600; color: {MUTED};
}}
QTextBrowser, QPlainTextEdit, QTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
}}
QPlainTextEdit#console {{ font-family: Consolas, monospace; font-size: 9pt; background: #0F172A; color: #CBD5E1; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -5px 0; border-radius: 8px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QToolTip {{ background: {SIDEBAR}; color: white; border: none; padding: 6px; }}
"""

# Feather-style line icons (MIT licensed shapes, drawn as simple SVG paths).
_ICONS = {
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
                '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "connect": '<path d="M12 2v6"/><path d="M5 12a7 7 0 0 0 14 0V8H5z"/><path d="M12 19v3"/>',
    "messages": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "monitor": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><circle cx="4" cy="6" r="1"/>'
               '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "performance": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "wave": '<path d="M2 12c2-6 4-6 6 0s4 6 6 0 4-6 6 0"/>',
}


def icon(name, color="#CBD5E1", size=20):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
           f'{_ICONS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def chip_style(kind):
    """Rounded status 'chip' colours: good / warn / bad / neutral / accent."""
    colors = {"good": (GOOD_SOFT, GOOD), "warn": (WARN_SOFT, WARN), "bad": (BAD_SOFT, BAD),
              "neutral": ("#F1F5F9", MUTED), "accent": (ACCENT_SOFT, ACCENT)}
    bg, fg = colors[kind]
    return (f"background:{bg}; color:{fg}; border-radius:11px; padding:3px 10px; "
            f"font-weight:600; font-size:9pt;")
