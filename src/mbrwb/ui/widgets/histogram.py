"""Event-density histogram used for navigation (click a bar or drag a range)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...timeline.timestamps import format_ticks
from ..theme import tokens


class Histogram(QWidget):
    rangeSelected = Signal(int, int)  # start ticks, end ticks

    def __init__(self, parent=None, height: int = 96):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height + 40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Event density histogram")
        self.lo, self.width_ticks, self.counts = 0, 1, []
        self.tz = "UTC"
        self._drag: tuple[int, int] | None = None
        self._hover: int | None = None
        self.unit = "events"

    def set_data(self, lo: int, width: int, counts: list[int], tz: str = "UTC") -> None:
        self.lo, self.width_ticks, self.counts, self.tz = lo, max(1, width), counts, tz
        self._drag = None
        total = sum(counts)
        self.setToolTip(f"{total} {self.unit} in view. Click a bar or drag across bars to set the time range.")
        self.update()

    def _bucket_at(self, x: float) -> int | None:
        if not self.counts:
            return None
        bw = (self.width() - 16) / len(self.counts)
        b = int((x - 8) // bw)
        return b if 0 <= b < len(self.counts) else None

    def paintEvent(self, _):
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(8, 6, -8, -20)
        p.fillRect(self.rect(), QColor(t.card))
        if not self.counts or not any(self.counts):
            p.setPen(QColor(t.text3))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No timestamped events in this view")
            return
        mx = max(self.counts)
        bw = r.width() / len(self.counts)
        sel = sorted(self._drag) if self._drag else None
        for i, c in enumerate(self.counts):
            if not c:
                continue
            h = max(2.0, r.height() * c / mx)
            rect = QRectF(r.left() + i * bw + 1, r.bottom() - h, max(1.0, bw - 2), h)
            col = QColor(t.accent)
            if sel and not (sel[0] <= i <= sel[1]):
                col.setAlpha(90)
            if self._hover == i:
                col = QColor(t.text)
            p.fillRect(rect, col)
        p.setPen(QPen(QColor(t.border)))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.setPen(QColor(t.text3))
        f = p.font()
        f.setPointSizeF(8)
        p.setFont(f)
        end = self.lo + self.width_ticks * len(self.counts)
        p.drawText(8, self.height() - 4, format_ticks(self.lo, self.tz, 0, with_zone=False))
        txt = format_ticks(end, self.tz, 0, with_zone=False) + f"  ({self.tz})"
        p.drawText(self.width() - 8 - p.fontMetrics().horizontalAdvance(txt), self.height() - 4, txt)
        if self._hover is not None and self._hover < len(self.counts):
            s = self.lo + self._hover * self.width_ticks
            tip = f"{self.counts[self._hover]} {self.unit} from {format_ticks(s, self.tz, 0, with_zone=False)}"
            w = p.fontMetrics().horizontalAdvance(tip) + 12
            x = min(max(8, int(r.left() + self._hover * bw) - w // 2), self.width() - w - 8)
            p.fillRect(x, 2, w, 16, QColor(t.card_alt))
            p.setPen(QColor(t.text))
            p.drawText(x + 6, 14, tip)

    def mousePressEvent(self, e):
        b = self._bucket_at(e.position().x())
        if b is not None:
            self._drag = (b, b)
            self.update()

    def mouseMoveEvent(self, e):
        b = self._bucket_at(e.position().x())
        self._hover = b
        if self._drag and b is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self._drag = (self._drag[0], b)
        self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            a, b = sorted(self._drag)
            self.rangeSelected.emit(self.lo + a * self.width_ticks, self.lo + (b + 1) * self.width_ticks - 1)
