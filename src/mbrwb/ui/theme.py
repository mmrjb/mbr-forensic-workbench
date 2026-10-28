"""Design tokens, Qt stylesheet and themed icons.

Dark tokens follow the product brief; the light theme is a complete counterpart. Spacing is on
an 8 px grid, radii 8-12 px. Status is always communicated by text + icon, never colour alone.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    sidebar: str
    card: str
    card_alt: str
    border: str
    hover: str
    selection: str
    text: str
    text2: str
    text3: str
    accent: str
    accent_text: str
    success: str
    warning: str
    error: str
    disabled: str


DARK = Tokens("dark", bg="#0B1220", sidebar="#0F172A", card="#172235", card_alt="#1B2840", border="#26354F", hover="#1F2D46",
              selection="#1E4060", text="#E7EDF6", text2="#AAB8CC", text3="#7C8BA3", accent="#38BDF8", accent_text="#06111E",
              success="#34D399", warning="#FBBF24", error="#F87171", disabled="#56657D")
LIGHT = Tokens("light", bg="#F4F6FA", sidebar="#FFFFFF", card="#FFFFFF", card_alt="#F7F9FC", border="#D6DDE8", hover="#EEF2F8",
               selection="#D5EAFB", text="#0F172A", text2="#475569", text3="#64748B", accent="#0369A1", accent_text="#FFFFFF",
               success="#047857", warning="#B45309", error="#B91C1C", disabled="#94A3B8")

_current = DARK


def tokens() -> Tokens:
    return _current


def status_color(kind: str) -> str:
    t = _current
    return {"ok": t.success, "warn": t.warning, "error": t.error, "info": t.accent, "muted": t.text3}.get(kind, t.text2)


def ui_font_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Inter", "Red Hat Text", "Ubuntu", "DejaVu Sans"):
        if f in fams:
            return f
    return QApplication.font().family()


def mono_font_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Cascadia Mono", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono"):
        if f in fams:
            return f
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def mono_font(size: int = 0) -> QFont:
    f = QFont(mono_font_family())
    f.setStyleHint(QFont.StyleHint.Monospace)
    if size:
        f.setPointSize(size)
    return f


_CHECK_URL = ""


def _check_image(color: str) -> str:
    """Write a small check-mark SVG for checkbox indicators to a per-user cache file (QSS needs a URL)."""
    import os
    import tempfile
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{color}" '
           'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    try:
        d = os.path.join(tempfile.gettempdir(), f"mbrwb-ui-{os.getpid()}")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"check_{color.lstrip('#')}.svg")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg)
        return path.replace("\\", "/")
    except OSError:
        return ""  # the filled indicator still shows the checked state


def stylesheet(t: Tokens) -> str:
    global _CHECK_URL
    _CHECK_URL = _check_image(t.accent_text)
    mono = mono_font_family()
    return f"""
* {{ outline: 0; }}
QWidget {{ background: {t.bg}; color: {t.text}; font-size: 10pt; }}
QWidget:disabled {{ color: {t.disabled}; }}
QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 6px 8px; border-radius: 6px; }}
QFrame#Sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
QFrame#Sidebar QLabel#Brand {{ background: transparent; color: {t.text}; font-size: 12pt; font-weight: 600; padding: 4px 8px; }}
QFrame#Sidebar QLabel#BrandSub {{ background: transparent; color: {t.text3}; font-size: 8.5pt; padding: 0 8px 8px 8px; }}
QListWidget#Nav {{ background: transparent; border: none; }}
QListWidget#Nav::item {{ padding: 8px 10px; margin: 1px 6px; border-radius: 8px; color: {t.text2}; }}
QListWidget#Nav::item:hover {{ background: {t.hover}; color: {t.text}; }}
QListWidget#Nav::item:selected {{ background: {t.selection}; color: {t.text}; }}
QFrame#TopBar {{ background: {t.bg}; border-bottom: 1px solid {t.border}; }}
QFrame#Card, QFrame#Panel {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#Card QLabel, QFrame#Panel QLabel, QFrame#Card QCheckBox, QFrame#Panel QCheckBox, QFrame#Panel QRadioButton {{ background: transparent; }}
QFrame#Card:hover[clickable="true"] {{ border-color: {t.accent}; }}
QLabel#PageTitle {{ font-size: 17pt; font-weight: 600; background: transparent; }}
QLabel#PageSubtitle, QLabel[muted="true"] {{ color: {t.text2}; background: transparent; }}
QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; background: transparent; }}
QLabel#Metric {{ font-size: 20pt; font-weight: 600; background: transparent; }}
QLabel#MetricUnit {{ color: {t.text2}; background: transparent; }}
QLabel[mono="true"], QLineEdit[mono="true"], QPlainTextEdit[mono="true"] {{ font-family: "{mono}"; }}
QPushButton {{ background: {t.card_alt}; color: {t.text}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.text3}; }}
QPushButton:pressed {{ background: {t.selection}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton:disabled {{ color: {t.disabled}; border-color: {t.border}; background: {t.card}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t.accent}; border-color: {t.text}; }}
QPushButton[primary="true"]:disabled {{ background: {t.border}; color: {t.disabled}; border-color: {t.border}; }}
QPushButton[danger="true"] {{ color: {t.error}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[flat="true"]:hover {{ background: {t.hover}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 4px; }}
QToolButton:hover {{ background: {t.hover}; border-color: {t.border}; }}
QToolButton:checked {{ background: {t.selection}; border-color: {t.accent}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox, QDateTimeEdit {{
    background: {t.card_alt}; color: {t.text}; border: 1px solid {t.border}; border-radius: 8px; padding: 5px 8px; selection-background-color: {t.selection}; selection-color: {t.text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QLineEdit[invalid="true"] {{ border-color: {t.error}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t.card}; border: 1px solid {t.border}; selection-background-color: {t.selection}; }}
QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator, QGroupBox::indicator {{ width: 15px; height: 15px; border: 1.5px solid {t.text3}; background: {t.card_alt}; }}
QCheckBox::indicator, QGroupBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({_CHECK_URL}); }}
QRadioButton::indicator:checked {{ background: {t.card_alt}; border: 4.5px solid {t.accent}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {t.disabled}; background: {t.card}; }}
QListView::indicator, QTableView::indicator, QTreeView::indicator {{ width: 14px; height: 14px; border: 1.5px solid {t.text3}; border-radius: 3px; background: {t.card_alt}; }}
QListView::indicator:checked, QTableView::indicator:checked, QTreeView::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({_CHECK_URL}); }}
QGroupBox {{ border: 1px solid {t.border}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 10px 10px; background: {t.card}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {t.text2}; }}
QTableView, QTreeView, QListView, QTreeWidget, QListWidget, QTableWidget {{
    background: {t.card}; alternate-background-color: {t.card_alt}; border: 1px solid {t.border}; border-radius: 8px;
    gridline-color: {t.border}; selection-background-color: {t.selection}; selection-color: {t.text}; }}
QTableView::item, QTreeView::item {{ padding: 2px 4px; }}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{ background: {t.hover}; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {t.selection}; color: {t.text}; }}
QHeaderView::section {{ background: {t.card_alt}; color: {t.text2}; border: none; border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; padding: 5px 8px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 8px; top: -1px; background: {t.card}; }}
QTabBar::tab {{ background: transparent; color: {t.text2}; padding: 7px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text3}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ background: {t.card_alt}; border: 1px solid {t.border}; border-radius: 6px; text-align: center; height: 14px; color: {t.text2}; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 5px; }}
QSplitter::handle {{ background: {t.bg}; }}
QSplitter::handle:hover {{ background: {t.border}; }}
QMenu {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.selection}; }}
QStatusBar {{ background: {t.sidebar}; color: {t.text2}; border-top: 1px solid {t.border}; }}
QLabel#Pill {{ border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel#Chip {{ background: {t.selection}; border-radius: 10px; padding: 2px 8px; }}
QFrame#Banner {{ border-radius: 8px; border: 1px solid {t.border}; background: {t.card_alt}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply_theme(app: QApplication, name: str) -> Tokens:
    global _current
    _current = LIGHT if name == "light" else DARK
    t = _current
    pal = QPalette()
    for role, col in ((QPalette.ColorRole.Window, t.bg), (QPalette.ColorRole.Base, t.card), (QPalette.ColorRole.AlternateBase, t.card_alt),
                      (QPalette.ColorRole.Text, t.text), (QPalette.ColorRole.WindowText, t.text), (QPalette.ColorRole.Button, t.card_alt),
                      (QPalette.ColorRole.ButtonText, t.text), (QPalette.ColorRole.Highlight, t.selection), (QPalette.ColorRole.HighlightedText, t.text),
                      (QPalette.ColorRole.ToolTipBase, t.card), (QPalette.ColorRole.ToolTipText, t.text), (QPalette.ColorRole.PlaceholderText, t.text3),
                      (QPalette.ColorRole.Link, t.accent)):
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t.disabled))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t.disabled))
    app.setPalette(pal)
    f = QFont(ui_font_family())
    f.setPointSizeF(10 if sys.platform == "win32" else 10)
    app.setFont(f)
    app.setStyleSheet(stylesheet(t))
    icon.cache_clear()
    return t


@lru_cache(maxsize=512)
def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    try:
        raw = resources.files("mbrwb.ui").joinpath("icons", f"{name}.svg").read_text("utf-8")
    except FileNotFoundError:
        raw = resources.files("mbrwb.ui").joinpath("icons", "info.svg").read_text("utf-8")
    svg = raw.replace("currentColor", color or _current.text2)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for scale in (1, 2, 3):
        pm = QPixmap(QSize(size * scale, size * scale))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic
