"""Reusable widgets with consistent spacing (8 px grid) and accessible labelling."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import icon, mono_font, status_color, tokens

LEVEL_ICON = {"ok": "check", "warn": "warning", "error": "error", "info": "info", "muted": "info"}


def button(text: str, *, icon_name: str | None = None, primary: bool = False, danger: bool = False, flat: bool = False,
           tooltip: str = "", on_click: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, tokens().accent_text if primary else None))
        b.setIconSize(QSize(16, 16))
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if flat:
        b.setProperty("flat", True)
    if tooltip:
        b.setToolTip(tooltip)
    b.setAccessibleName(text or tooltip)
    if on_click:
        b.clicked.connect(on_click)
    return b


def tool_button(icon_name: str, tooltip: str, on_click: Callable | None = None, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def label(text: str = "", *, muted: bool = False, mono: bool = False, wrap: bool = False, selectable: bool = False, obj: str | None = None) -> QLabel:
    lb = QLabel(text)
    if muted:
        lb.setProperty("muted", True)
    if mono:
        lb.setProperty("mono", True)
        lb.setFont(mono_font())
    if wrap:
        lb.setWordWrap(True)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    if obj:
        lb.setObjectName(obj)
    return lb


class Card(QFrame):
    clicked = Signal()

    def __init__(self, title: str | None = None, *, clickable: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("clickable", clickable)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            t = label(title, obj="SectionTitle")
            self.lay.addWidget(t)

    def mouseReleaseEvent(self, e):
        if self.property("clickable") and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if self.property("clickable") and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class Panel(QFrame):
    def __init__(self, parent: QWidget | None = None, margins: int = 12):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(8)


class Pill(QWidget):
    """Status shown as icon + text + colour (never colour alone)."""

    def __init__(self, text: str = "", level: str = "muted", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.ic = QLabel()
        self.ic.setFixedSize(14, 14)
        self.text = QLabel()
        self.text.setObjectName("Pill")
        lay.addWidget(self.ic)
        lay.addWidget(self.text)
        lay.addStretch(0)
        self.set(text, level)

    def set(self, text: str, level: str) -> None:
        col = status_color(level)
        self.ic.setPixmap(icon(LEVEL_ICON.get(level, "info"), col, 14).pixmap(14, 14))
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {col}; background: transparent; border: 1px solid {col}; border-radius: 9px; padding: 1px 8px;")
        self.setAccessibleName(f"Status: {text}")
        self.setToolTip(text)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title, obj="PageTitle")
        self.subtitle = label(subtitle, obj="PageSubtitle", wrap=True)
        col.addWidget(self.title)
        if subtitle:
            col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)

    def add_action(self, w: QWidget) -> None:
        self.actions.addWidget(w)


class EmptyState(QWidget):
    def __init__(self, title: str, text: str, actions: list[QPushButton] | None = None, icon_name: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, tokens().text3, 40).pixmap(40, 40))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        t = label(title, obj="SectionTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        d = label(text, muted=True, wrap=True)
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setMaximumWidth(520)
        lay.addWidget(d, 0, Qt.AlignmentFlag.AlignHCenter)
        if actions:
            row = QHBoxLayout()
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            for a in actions:
                row.addWidget(a)
            lay.addLayout(row)


class PathLabel(QWidget):
    """Shows a path elided in the middle (keeps the filename), full path in the tooltip, with a
    copy button. Paths are always laid out left-to-right."""

    def __init__(self, path: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.lb = label("", mono=True, selectable=True)
        self.lb.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.copy = tool_button("copy", "Copy full path", self._copy)
        lay.addWidget(self.lb, 1)
        lay.addWidget(self.copy)
        self._path = ""
        self.set(path)

    def set(self, path: str | None) -> None:
        self._path = path or ""
        self.lb.setToolTip(self._path)
        self.copy.setEnabled(bool(self._path))
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        fm = QFontMetrics(self.lb.font())
        w = max(60, self.lb.width())
        self.lb.setText("⁦" + fm.elidedText(self._path, Qt.TextElideMode.ElideMiddle, w) + "⁩" if self._path else "-")

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._path)


class Banner(QFrame):
    """Non-modal message with a next action and optional expandable diagnostics."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        row = QHBoxLayout()
        self.ic = QLabel()
        self.ic.setFixedSize(18, 18)
        self.msg = label("", wrap=True, selectable=True)
        self.more = button("Details", flat=True)
        self.more.setCheckable(True)
        self.close_btn = tool_button("x", "Dismiss", self.hide)
        row.addWidget(self.ic, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.msg, 1)
        row.addWidget(self.more)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.diag = QPlainTextEdit()
        self.diag.setReadOnly(True)
        self.diag.setFont(mono_font(9))
        self.diag.setMaximumHeight(140)
        self.diag.hide()
        lay.addWidget(self.diag)
        self.more.toggled.connect(self.diag.setVisible)
        self.hide()

    def show_message(self, level: str, text: str, next_action: str = "", diagnostics: str = "") -> None:
        col = status_color(level)
        self.ic.setPixmap(icon(LEVEL_ICON.get(level, "info"), col, 18).pixmap(18, 18))
        self.setStyleSheet(f"QFrame#Banner {{ border-left: 4px solid {col}; }}")
        self.msg.setText(text + (f"\nNext: {next_action}" if next_action else ""))
        self.diag.setPlainText(diagnostics)
        self.more.setVisible(bool(diagnostics))
        self.more.setChecked(False)
        self.show()


class KeyValueGrid(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self._row = 0

    def clear(self) -> None:
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._row = 0

    def add(self, key: str, value, *, mono: bool = False, widget: QWidget | None = None, tooltip: str = "") -> None:
        k = label(key, muted=True)
        k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        if tooltip:
            k.setToolTip(tooltip)
        self.grid.addWidget(k, self._row, 0)
        if widget is None:
            widget = label("" if value is None else str(value), mono=mono, wrap=not mono, selectable=True)
            if mono:
                widget.setWordWrap(True)
        self.grid.addWidget(widget, self._row, 1)
        self._row += 1


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {tokens().border};")
    return f
