"""Virtualised table model: the row count and pages come from a background fetcher; only the
visible pages are materialised, and stale results from an earlier filter are discarded."""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from ..theme import tokens

PAGE = 200
MAX_PAGES = 60


class PagedTableModel(QAbstractTableModel):
    countReady = Signal(int)
    loadingChanged = Signal(bool)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.headers: list[str] = []
        self.header_tips: list[str] = []
        self._count = 0
        self._pages: OrderedDict[int, list[tuple]] = OrderedDict()
        self._pending: set[int] = set()
        self._gen = 0
        self._fetch: Callable[[int, int], list[tuple]] | None = None
        self._counter: Callable[[], int] | None = None
        self.display: Callable[[tuple, int], str] = lambda row, col: "" if row[col] is None else str(row[col])
        self.row_tint: Callable[[tuple], str | None] = lambda row: None
        self.first_data_col = 0

    # -- configuration ---------------------------------------------------------------------
    def configure(self, headers: list[str], fetch: Callable[[int, int], list[tuple]], counter: Callable[[], int], *, first_data_col: int = 0,
                  header_tips: list[str] | None = None) -> None:
        self.beginResetModel()
        self._gen += 1
        self.headers = headers
        self.header_tips = header_tips or [""] * len(headers)
        self._fetch, self._counter = fetch, counter
        self._pages.clear()
        self._pending.clear()
        self._count = 0
        self.first_data_col = first_data_col
        self.endResetModel()
        gen = self._gen
        self.loadingChanged.emit(True)
        self.ctx.run_bg(counter, on_done=lambda n, g=gen: self._on_count(g, n), on_error=lambda m, tb: self.loadingChanged.emit(False))

    def clear(self) -> None:
        self.beginResetModel()
        self._gen += 1
        self.headers, self._count = [], 0
        self._pages.clear()
        self.endResetModel()

    def _on_count(self, gen: int, n: int) -> None:
        if gen != self._gen:
            return
        self.beginResetModel()
        self._count = int(n)
        self.endResetModel()
        self.loadingChanged.emit(False)
        self.countReady.emit(self._count)
        if self._count:
            self._request(0)

    def _request(self, page: int) -> None:
        if page in self._pages or page in self._pending or self._fetch is None:
            return
        self._pending.add(page)
        gen = self._gen
        fetch = self._fetch
        self.ctx.run_bg(fetch, page * PAGE, PAGE, on_done=lambda rows, g=gen, p=page: self._on_page(g, p, rows),
                        on_error=lambda m, tb, p=page: self._pending.discard(p))

    def _on_page(self, gen: int, page: int, rows: list[tuple]) -> None:
        if gen != self._gen:
            return
        self._pending.discard(page)
        self._pages[page] = rows
        while len(self._pages) > MAX_PAGES:
            self._pages.popitem(last=False)
        top = page * PAGE
        bottom = min(self._count, top + len(rows)) - 1
        if bottom >= top:
            self.dataChanged.emit(self.index(top, 0), self.index(bottom, max(0, len(self.headers) - 1)))

    # -- access ------------------------------------------------------------------------------
    def row_tuple(self, row: int) -> tuple | None:
        page, off = divmod(row, PAGE)
        rows = self._pages.get(page)
        if rows is None or off >= len(rows):
            self._request(page)
            return None
        self._pages.move_to_end(page)
        return rows[off]

    def total(self) -> int:
        return self._count

    # -- Qt interface ---------------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_tuple(index.row())
        if role == Qt.ItemDataRole.DisplayRole:
            if row is None:
                return "…"
            v = self.display(row, index.column() + self.first_data_col)
            return v if len(v) < 500 else v[:500] + "…"
        if role == Qt.ItemDataRole.ToolTipRole and row is not None:
            v = self.display(row, index.column() + self.first_data_col)
            return v if 40 < len(v) < 4000 else None
        if role == Qt.ItemDataRole.ForegroundRole and row is not None:
            tint = self.row_tint(row)
            if tint:
                return QColor(tint)
        if role == Qt.ItemDataRole.ForegroundRole and row is None:
            return QColor(tokens().text3)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole and section < len(self.headers):
                return self.headers[section]
            if role == Qt.ItemDataRole.ToolTipRole and section < len(self.header_tips):
                return self.header_tips[section] or self.headers[section]
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None


class RowsModel(QAbstractTableModel):
    """Small in-memory model for short lists (jobs, cases, outputs)."""

    def __init__(self, headers: list[str], parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows: list[list] = []
        self.payload: list = []
        self.tints: list[str | None] = []
        self.icons: list = []

    def set_rows(self, rows: list[list], payload: list | None = None, tints: list | None = None, icons: list | None = None) -> None:
        self.beginResetModel()
        self.rows = rows
        self.payload = payload or [None] * len(rows)
        self.tints = tints or [None] * len(rows)
        self.icons = icons or [None] * len(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return "" if v is None else str(v)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0 and self.tints[index.row()]:
            return QColor(self.tints[index.row()])
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and self.icons[index.row()] is not None:
            return self.icons[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return self.payload[index.row()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        idx = sorted(range(len(self.rows)), key=lambda i: (self.rows[i][column] is None, str(self.rows[i][column] or "").lower()),
                     reverse=order == Qt.SortOrder.DescendingOrder)
        self.beginResetModel()
        self.rows = [self.rows[i] for i in idx]
        self.payload = [self.payload[i] for i in idx]
        self.tints = [self.tints[i] for i in idx]
        self.icons = [self.icons[i] for i in idx]
        self.endResetModel()
