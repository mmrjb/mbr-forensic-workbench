"""Results explorer: dataset navigator, paged table, filters, explicit search scope, details."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...findings.service import FindingsService, Reference
from ...results.query import (
    OPERATOR_LABELS,
    DatasetQuery,
    DatasetView,
    Filter,
    export_dataset_csv,
    export_dataset_json,
    sanitize_filename,
    search_case,
)
from ...storage.case_store import new_id
from ...storage.db import utcnow
from ..dialogs import AddToFindingDialog
from ..theme import status_color
from ..widgets.common import EmptyState, KeyValueGrid, button, label, tool_button
from ..widgets.paged_model import PagedTableModel
from .base import Page


class ResultsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Results", "Imported parser outputs. Original files are never modified; every row keeps its original record identity.")
        split = QSplitter(Qt.Orientation.Horizontal)
        # navigator
        nav = QWidget()
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(0, 0, 0, 0)
        self.group_by = QComboBox()
        for t, v in (("Group by host", "host"), ("Group by tool", "tool"), ("Group by job", "job"), ("Group by user", "user")):
            self.group_by.addItem(t, v)
        self.group_by.currentIndexChanged.connect(self.refresh)
        nl.addWidget(self.group_by)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dataset", "Records"])
        self.tree.setColumnWidth(0, 210)
        self.tree.setHeaderHidden(False)
        self.tree.itemSelectionChanged.connect(self._dataset_selected)
        nl.addWidget(self.tree, 1)
        nav.setMinimumWidth(170)
        split.addWidget(nav)
        # centre
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(0, 0, 0, 0)
        srow = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.returnPressed.connect(self._search)
        self.scope = QComboBox()
        self.scope.addItem("This table", "table")
        self.scope.addItem("Whole case", "case")
        self.scope.setToolTip("Search scope: 'This table' searches every column of the selected dataset; 'Whole case' searches every imported dataset.")
        self.scope.setAccessibleName("Search scope")
        srow.addWidget(self.search, 3)
        srow.addWidget(label("Scope", muted=True))
        srow.addWidget(self.scope)
        srow.addWidget(button("Search", icon_name="search", on_click=self._search))
        cl.addLayout(srow)
        frow = QHBoxLayout()
        self.f_col = QComboBox()
        self.f_col.setMinimumWidth(120)
        self.f_op = QComboBox()
        self.f_op.setMinimumWidth(110)
        for k, v in OPERATOR_LABELS.items():
            self.f_op.addItem(v, k)
        self.f_val = QLineEdit()
        self.f_val.setPlaceholderText("Value")
        self.f_val.returnPressed.connect(self._add_filter)
        frow.addWidget(label("Filter"))
        frow.addWidget(self.f_col)
        frow.addWidget(self.f_op)
        frow.addWidget(self.f_val, 1)
        frow.addWidget(button("Add", icon_name="filter", on_click=self._add_filter))
        frow.addWidget(button("Reset", icon_name="x", on_click=self._reset, tooltip="Reset filters and search"))
        cl.addLayout(frow)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        chipw = QWidget()
        chipw.setLayout(self.chips)
        cl.addWidget(chipw)
        info = QHBoxLayout()
        self.count = label("", muted=True)
        info.addWidget(self.count, 1)
        self.cols_btn = tool_button("list", "Choose columns", self._columns_menu)
        self.views_btn = tool_button("bookmark", "Saved views", self._views_menu)
        self.export_btn = tool_button("download", "Export current filtered results", self._export_menu)
        for b in (self.cols_btn, self.views_btn, self.export_btn):
            info.addWidget(b)
        cl.addLayout(info)
        self.model = PagedTableModel(ctx)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(150)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self._sort)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._save_layout_later())
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._row_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        cl.addWidget(self.table, 1)
        self.empty = EmptyState("No imported results", "Run an analysis or import an existing CSV output. Nothing matched does not mean no activity occurred.",
                                [button("New analysis", icon_name="play", primary=True, on_click=win.start_analysis),
                                 button("Import external CSV/JSON...", icon_name="import", on_click=self._import_external)], "results")
        cl.addWidget(self.empty)
        self.empty.hide()
        split.addWidget(centre)
        # details
        self.details = QTabWidget()
        rec = QWidget()
        rl = QVBoxLayout(rec)
        self.rec_table = QTableWidget(0, 2)
        self.rec_table.setHorizontalHeaderLabels(["Field", "Original value"])
        self.rec_table.horizontalHeaderItem(0).setToolTip("Field names are the original output headers")
        self.rec_table.horizontalHeader().setStretchLastSection(True)
        self.rec_table.setColumnWidth(0, 150)
        self.rec_table.verticalHeader().hide()
        self.rec_table.setWordWrap(True)
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rl.addWidget(self.rec_table, 1)
        act = QHBoxLayout()
        act.addWidget(button("Copy value", icon_name="copy", on_click=self._copy_value))
        act.addWidget(button("Copy row", icon_name="copy", on_click=self._copy_row))
        rl.addLayout(act)
        act1 = QHBoxLayout()
        act1.addWidget(button("Bookmark", icon_name="bookmark", on_click=self._bookmark))
        act1.addWidget(button("Add to finding", icon_name="findings", on_click=self._add_to_finding))
        rl.addLayout(act1)
        act2 = QHBoxLayout()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Analyst note or tags for this record")
        act2.addWidget(self.note_edit, 1)
        act2.addWidget(button("Save note", icon_name="check", on_click=self._save_note))
        rl.addLayout(act2)
        self.details.addTab(rec, "Record")
        pw = QWidget()
        pl = QVBoxLayout(pw)
        self.prov = KeyValueGrid()
        pl.addWidget(self.prov)
        prow = QHBoxLayout()
        prow.addWidget(button("Show source output folder", icon_name="folder", on_click=self._open_source))
        prow.addWidget(button("Open job", icon_name="jobs", on_click=self._open_job))
        prow.addStretch(1)
        pl.addLayout(prow)
        pl.addStretch(1)
        self.details.addTab(pw, "Provenance")
        self.meaning = QPlainTextEdit()
        self.meaning.setReadOnly(True)
        self.details.addTab(self.meaning, "Field meaning")
        self.details.setMinimumWidth(300)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 2)
        split.setSizes([220, 760, 380])
        self.body_layout.addWidget(split, 1)
        self.q: DatasetQuery | None = None
        self.view: DatasetView | None = None
        self._rec: dict | None = None
        self._pending_select: int | None = None
        self.model.countReady.connect(self._count_ready)
        self._layout_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_layout)
        ctx.resultsChanged.connect(lambda: self.isVisible() and self.refresh())
        ctx.jobChanged.connect(lambda *_: self.isVisible() and QTimer.singleShot(1500, self.refresh))

    def focus_filter(self) -> bool:
        self.search.setFocus()
        self.search.selectAll()
        return True

    def open_with(self, arg) -> None:
        if not isinstance(arg, dict):
            return
        if arg.get("case_search"):
            self.search.setText(arg["case_search"])
            self.scope.setCurrentIndex(1)
            QTimer.singleShot(0, self._search)
        if arg.get("dataset_id"):
            self._select_dataset(int(arg["dataset_id"]), arg.get("rec_id"))

    # -- datasets --------------------------------------------------------------------------------
    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        g = self.group_by.currentData()

        def load():
            return [dict(r) for r in case.results.query("SELECT id, name, tool_id, tool_version, host, user_profile, job_id, state, rows_total, rows_rejected, "
                                                         "rows_adjusted, origin, timeline_state FROM datasets WHERE row_table IS NOT NULL ORDER BY id")]
        self.ctx.run_bg(load, on_done=lambda rows: self._fill_tree(rows, g))

    def _fill_tree(self, rows: list[dict], g: str) -> None:
        cur = self.q.dataset_id if self.q else None
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        key = {"host": "host", "tool": "tool_id", "job": "job_id", "user": "user_profile"}[g]
        for r in rows:
            k = r[key] or "(not set)"
            if k not in groups:
                groups[k] = QTreeWidgetItem([k, ""])
                self.tree.addTopLevelItem(groups[k])
                groups[k].setExpanded(True)
            name = Path(r["name"]).name
            it = QTreeWidgetItem([name, f"{r['rows_total']:,}"])
            it.setToolTip(0, f"{r['name']}\nstate: {r['state']}; {r['rows_rejected']} rejected, {r['rows_adjusted']} adjusted; timeline: {r['timeline_state']}"
                             + ("\nExternally generated output (declared metadata only)" if r["origin"] == "external" else ""))
            it.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            if r["state"] != "complete":
                it.setForeground(1, _brush(status_color("warn")))
            groups[k].addChild(it)
            if r["id"] == cur:
                it.setSelected(True)
        self.tree.blockSignals(False)
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))

    def _dataset_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        ds = items[0].data(0, Qt.ItemDataRole.UserRole)
        if ds is not None and (self.q is None or self.q.dataset_id != ds):
            self._select_dataset(ds)

    def _select_dataset(self, ds_id: int, rec_id: int | None = None) -> None:
        try:
            self.q = DatasetQuery(self.ctx.case.results, ds_id)
        except ValueError as exc:
            self.win.banner.show_message("warn", str(exc))
            return
        self.view = DatasetView(ds_id)
        self.f_col.clear()
        self.f_col.addItem("#", "#")
        for c in self.q.columns:
            self.f_col.addItem(c["name"], c["name"])
        self._pending_select = rec_id
        self._reload()
        self._render_provenance()
        self.scope.setCurrentIndex(0)

    def _reload(self) -> None:
        if not self.q or not self.view:
            return
        q, v = self.q, DatasetView(self.view.dataset_id, list(self.view.filters), self.view.search, self.view.sort_column, self.view.sort_desc)
        headers = ["#"] + [c["name"] for c in q.columns]
        tips = ["Original logical record number in the source output (stable across sorting and filtering)"] + \
               [f"Original header: {c['original']}" for c in q.columns]
        # page rows are (rec_id, rstatus, c0..cN): column 0 shows rec_id, rstatus only tints adjusted rows
        self.model.display = lambda row, col: str(row[0]) if col == 0 else ("" if row[col + 1] is None else str(row[col + 1]))
        self.model.row_tint = lambda row: status_color("warn") if row[1] else None
        self.model.configure(headers, lambda off, lim: q.page(v, off, lim), lambda: q.count(v), header_tips=tips)
        self._apply_layout()
        self._render_chips()

    def _count_ready(self, n: int) -> None:
        if not self.q:
            return
        total = self.q.meta["rows_total"]
        filt = bool(self.view.filters or self.view.search)
        note = f"{n:,} records" + (f" match (of {total:,} in this dataset)" if filt else " in this dataset")
        if filt and n == 0:
            note += ". No records match the current filter in this dataset; that does not mean no activity occurred."
        if self.q.meta["rows_rejected"]:
            note += f"  |  {self.q.meta['rows_rejected']} source record(s) could not be imported (see Provenance)."
        self.count.setText(note)
        if self._pending_select is not None:
            pos = self.q.position_of(self.view, int(self._pending_select))
            self._pending_select = None
            if pos is not None:
                QTimer.singleShot(150, lambda: (self.table.selectRow(pos), self.table.scrollTo(self.model.index(pos, 0))))

    # -- filters / search / sort ------------------------------------------------------------------
    def _add_filter(self) -> None:
        if not self.view:
            return
        op = self.f_op.currentData()
        if op not in ("is_empty", "not_empty") and not self.f_val.text():
            return
        self.view.filters.append(Filter(self.f_col.currentData(), op, self.f_val.text()))
        self.f_val.clear()
        self._reload_keep_selection()

    def _reset(self) -> None:
        if self.view:
            self.view.filters.clear()
            self.view.search = ""
            self.search.clear()
            self._reload_keep_selection()

    def _render_chips(self) -> None:
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.view:
            return
        items = [(f.chip(), i) for i, f in enumerate(self.view.filters)]
        if self.view.search:
            items.append((f"search: {self.view.search}", -1))
        for text, i in items:
            b = button(text + "  ✕", flat=True, tooltip="Remove this filter")
            b.setObjectName("Chip")
            b.clicked.connect(lambda _=False, i=i: self._remove_filter(i))
            self.chips.addWidget(b)
        self.chips.addStretch(1)

    def _remove_filter(self, i: int) -> None:
        if i == -1:
            self.view.search = ""
            self.search.clear()
        else:
            self.view.filters.pop(i)
        self._reload_keep_selection()

    def _reload_keep_selection(self) -> None:
        self._pending_select = self._rec["rec_id"] if self._rec else None
        self._reload()

    def _search(self) -> None:
        text = self.search.text().strip()
        if self.scope.currentData() == "case":
            if not text or self.ctx.case is None:
                return
            case = self.ctx.case
            self.count.setText("Searching all datasets in this case...")
            self.ctx.run_bg(lambda: search_case(case.results, text), on_done=self._case_hits)
            return
        if self.view:
            self.view.search = text
            self._reload_keep_selection()

    def _case_hits(self, hits: list[dict]) -> None:
        from ..dialogs import Dialog
        d = Dialog("Whole-case search", self, 620)
        d.lay.addWidget(label(f"'{self.search.text()}' - matches in {len(hits)} dataset(s). Scope: every imported dataset in this case (raw values).", wrap=True))
        t = QTreeWidget()
        t.setHeaderLabels(["Dataset", "Matches shown"])
        for h in hits:
            it = QTreeWidgetItem([h["name"], f"{h['hits_shown']}{'+' if h['capped'] else ''}"])
            it.setData(0, Qt.ItemDataRole.UserRole, (h["dataset_id"], h["rec_ids"][0]))
            t.addTopLevelItem(it)
        if not hits:
            t.addTopLevelItem(QTreeWidgetItem(["No imported record contains this text. This says nothing about data that was not processed.", ""]))

        def go(it):
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data:
                d.accept()
                self._select_dataset(data[0], data[1])
                self.view.search = self.search.text().strip()
                self._reload_keep_selection()
        t.itemActivated.connect(go)
        t.itemDoubleClicked.connect(go)
        d.lay.addWidget(t, 1)
        d.exec()

    def _sort(self, section: int) -> None:
        if not self.q or not self.view:
            return
        col = "#" if section == 0 else self.q.columns[section - 1]["name"]
        if self.view.sort_column == col:
            self.view.sort_desc = not self.view.sort_desc
        else:
            self.view.sort_column, self.view.sort_desc = (None if col == "#" else col), False
        self.table.horizontalHeader().setSortIndicator(section, Qt.SortOrder.DescendingOrder if self.view.sort_desc else Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        q = self.q
        if self.view.sort_column:
            self.count.setText("Preparing sort index...")
            self.ctx.run_bg(lambda: q.ensure_sort_index(col), on_done=lambda _: self._reload_keep_selection())
        else:
            self._reload_keep_selection()

    # -- record details ---------------------------------------------------------------------------
    def _row_selected(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid() or not self.q:
            return
        row = self.model.row_tuple(idx.row())
        if row is None:
            return
        rec = self.q.record(row[0])
        self._rec = rec
        self.rec_table.setRowCount(len(rec["fields"]) + 3)
        self.rec_table.setItem(0, 0, QTableWidgetItem("Source record"))
        self.rec_table.setItem(0, 1, QTableWidgetItem(f"ds{self.q.dataset_id}:rec{rec['rec_id']} (lines {rec['line_start']}-{rec['line_end']}"
                                                      + (f", bytes {rec['byte_start']}-{rec['byte_end']})" if rec["byte_start"] is not None else ")")))
        self.rec_table.setItem(1, 0, QTableWidgetItem("Import status"))
        self.rec_table.setItem(1, 1, QTableWidgetItem({0: "as in source", 1: "short row padded with empty values", 2: "extra values kept separately"}[rec["rstatus"]]))
        self.rec_table.setItem(2, 0, QTableWidgetItem("Extra values"))
        self.rec_table.setItem(2, 1, QTableWidgetItem(json.dumps(rec["extra"], ensure_ascii=False) if rec["extra"] else ""))
        for i, (k, v) in enumerate(rec["fields"], start=3):
            self.rec_table.setItem(i, 0, QTableWidgetItem(k))
            vi = QTableWidgetItem(v or "")
            vi.setToolTip(v or "")
            self.rec_table.setItem(i, 1, vi)
        self.rec_table.resizeRowsToContents()
        fs = FindingsService(self.ctx.case)
        notes = fs.notes("record", f"ds{self.q.dataset_id}:rec{rec['rec_id']}")
        self.note_edit.setPlaceholderText(f"{len(notes)} note(s) on this record - add another" if notes else "Analyst note or tags for this record")

    def _render_provenance(self) -> None:
        self.prov.clear()
        m = self.q.meta
        self.prov.add("Dataset", f"#{m['id']} {m['name']}")
        self.prov.add("Origin", "workbench job" if m["origin"] == "job" else "external output (declared metadata only)")
        self.prov.add("Tool", f"{m['tool_id'] or '-'} {m['tool_version'] or ''}")
        self.prov.add("Job", m["job_id"] or "-", mono=True)
        self.prov.add("Source output", m["source_path"], mono=True)
        exists = Path(m["source_path"]).exists()
        self.prov.add("Source available", "yes" if exists else "NO - imported rows remain viewable; the original file is missing (no substitute is used)")
        self.prov.add("Source SHA-256", m["source_sha256"] or "-", mono=True)
        self.prov.add("Encoding", m["encoding"])
        self.prov.add("Schema profile", m["schema_profile"] or "not recognised (raw import only)")
        self.prov.add("Import", f"{m['state']}: {m['rows_total']:,} records, {m['rows_adjusted']} adjusted, {m['rows_rejected']} rejected (importer v{m['importer_version']})")
        if m["rows_rejected"]:
            self.prov.add("Rejected samples", m["rejected_samples_json"][:2000], mono=True)
        self.prov.add("Timeline", f"{m['timeline_state']} {m['timeline_mapping'] or ''} ({m['timeline_events']} events, {m['timeline_unresolved']} unresolved)")
        self.prov.add("Host / user", f"{m['host'] or '-'} / {m['user_profile'] or '-'}")
        from ...timeline.mapper import mapping_for
        mp = mapping_for(m["schema_profile"])
        if mp:
            self.meaning.setPlainText("\n".join([f"Mapping {mp.mapping_id} v{mp.version}", f"Timestamp convention: {mp.convention} - {mp.convention_basis}",
                                                 f"Column check: {mp.column_verification}", ""] +
                                                [f"{e['field']}: {e['meaning']} [{e['category']}]" for e in mp.events] + (["", mp.notes] if mp.notes else [])))
        else:
            self.meaning.setPlainText("No reviewed mapping for this output: columns are shown exactly as the tool wrote them. "
                                      "Column names are not interpreted (a column called Path or Timestamp can mean different things in different tools).")

    def _copy_value(self) -> None:
        r = self.rec_table.currentRow()
        if r >= 0 and self.rec_table.item(r, 1):
            QGuiApplication.clipboard().setText(self.rec_table.item(r, 1).text())

    def _copy_row(self) -> None:
        if self._rec:
            QGuiApplication.clipboard().setText("\t".join(v or "" for _, v in self._rec["fields"]))

    def _ref(self) -> Reference | None:
        return Reference("record", dataset_id=self.q.dataset_id, rec_id=self._rec["rec_id"]) if self._rec and self.q else None

    def _bookmark(self) -> None:
        ref = self._ref()
        if ref:
            FindingsService(self.ctx.case).bookmark(ref, note=self.note_edit.text())
            self.win.banner.show_message("ok", f"Bookmarked {ref.label}. The bookmark follows the record, not its position on screen.")
            self.ctx.findingsChanged.emit()

    def _save_note(self) -> None:
        ref = self._ref()
        if ref and self.note_edit.text().strip():
            FindingsService(self.ctx.case).add_note("record", ref.label, self.note_edit.text().strip())
            self.note_edit.clear()
            self._row_selected()

    def _add_to_finding(self) -> None:
        ref = self._ref()
        if ref:
            add_reference_to_finding(self, self.ctx, ref)

    def _open_source(self) -> None:
        if self.q:
            p = Path(self.q.meta["source_path"]).parent
            if p.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _open_job(self) -> None:
        if self.q and self.q.meta["job_id"]:
            self.win.go("jobs", {"job_id": self.q.meta["job_id"]})

    def _context_menu(self, pos) -> None:
        m = QMenu(self)
        for text, fn in (("Copy value", self._copy_cell), ("Copy row", self._copy_row), ("Bookmark", self._bookmark), ("Add to finding...", self._add_to_finding),
                         ("Filter: equals this value", lambda: self._quick_filter("equals")), ("Filter: exclude this value", lambda: self._quick_filter("not_equals"))):
            a = QAction(text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_cell(self) -> None:
        idx = self.table.currentIndex()
        if idx.isValid():
            QGuiApplication.clipboard().setText(str(self.model.data(idx) or ""))

    def _quick_filter(self, op: str) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid() or idx.column() == 0 or not self.view:
            return
        row = self.model.row_tuple(idx.row())
        col = self.q.columns[idx.column() - 1]["name"]
        self.view.filters.append(Filter(col, op, row[idx.column() + 1] or ""))
        self._reload_keep_selection()

    # -- columns / layouts / views ---------------------------------------------------------------
    def _columns_menu(self) -> None:
        if not self.q:
            return
        m = QMenu(self)
        for i in range(self.model.columnCount()):
            a = QAction(self.model.headers[i], m)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: (self.table.setColumnHidden(i, not on), self._save_layout_later()))
            m.addAction(a)
        m.exec(self.cols_btn.mapToGlobal(self.cols_btn.rect().bottomLeft()))

    def _layout_key(self) -> str | None:
        return f"{self.q.meta['tool_id']}:{self.q.meta['schema_profile'] or Path(self.q.meta['name']).stem}" if self.q else None

    def _save_layout_later(self) -> None:
        self._layout_timer.start()

    def _save_layout(self) -> None:
        key = self._layout_key()
        if not key:
            return
        hdr = self.table.horizontalHeader()
        layout = {"hidden": [i for i in range(self.model.columnCount()) if self.table.isColumnHidden(i)], "widths": [hdr.sectionSize(i) for i in range(self.model.columnCount())]}
        self.ctx.settings.column_layouts[key] = layout
        self.ctx.settings_store.save()

    def _apply_layout(self) -> None:
        key = self._layout_key()
        lay = self.ctx.settings.column_layouts.get(key or "", {})
        for i in range(self.model.columnCount()):
            self.table.setColumnHidden(i, i in lay.get("hidden", []))
            ws = lay.get("widths", [])
            self.table.setColumnWidth(i, ws[i] if i < len(ws) else (70 if i == 0 else 160))

    def _views_menu(self) -> None:
        if not self.view or self.ctx.case is None:
            return
        m = QMenu(self)
        save = QAction("Save current view...", m)
        save.triggered.connect(self._save_view)
        m.addAction(save)
        m.addSeparator()
        for r in self.ctx.case.db.query("SELECT * FROM saved_views WHERE scope='results' ORDER BY created_utc DESC"):
            a = QAction(r["name"], m)
            a.triggered.connect(lambda _=False, j=r["view_json"]: self._load_view(j))
            m.addAction(a)
        m.exec(self.views_btn.mapToGlobal(self.views_btn.rect().bottomLeft()))

    def _save_view(self) -> None:
        name, ok = QInputDialog.getText(self, "Save view", "Name")
        if ok and name.strip():
            self.ctx.case.db.execute("INSERT INTO saved_views(id, case_id, scope, name, view_json, created_utc) VALUES (?,?,?,?,?,?)",
                                     (new_id("view"), self.ctx.case.id, "results", name.strip(), self.view.to_json(), utcnow()))

    def _load_view(self, j: str) -> None:
        v = DatasetView.from_json(j)
        self._select_dataset(v.dataset_id)
        self.view = v
        self._reload()

    def _export_menu(self) -> None:
        if not self.q:
            return
        m = QMenu(self)
        for text, kind in (("Spreadsheet-safe CSV (recommended)", "safe"), ("Raw CSV (exact original values)", "raw"), ("JSON lines", "json")):
            a = QAction(text, m)
            a.triggered.connect(lambda _=False, k=kind: self._export(k))
            m.addAction(a)
        m.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def _export(self, kind: str) -> None:
        case = self.ctx.case
        base = sanitize_filename(f"ds{self.q.dataset_id}_{Path(self.q.meta['name']).stem}_{kind}") + (".jsonl" if kind == "json" else ".csv")
        f, _ = QFileDialog.getSaveFileName(self, "Export filtered results", str(case.root / "exports" / base))
        if not f:
            return
        q, v = self.q, DatasetView(self.view.dataset_id, list(self.view.filters), self.view.search, self.view.sort_column, self.view.sort_desc)

        def work():
            n = export_dataset_json(q, v, Path(f)) if kind == "json" else export_dataset_csv(q, v, Path(f), safe=kind == "safe")
            case.audit("results.exported", f, {"dataset": q.dataset_id, "kind": kind, "records": n, "view": v.to_json()})
            return n
        self.ctx.run_bg(work, on_done=lambda n: self.win.banner.show_message("ok", f"Exported {n:,} records to {f}" +
                                                                             (" (formula-like values prefixed with an apostrophe; originals unchanged)." if kind == "safe" else ".")))

    def _import_external(self) -> None:
        if self.ctx.case is None:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Existing CSV or JSON output", "", "Outputs (*.csv *.json *.jsonl)")
        if not f:
            return
        tool, ok = QInputDialog.getText(self, "Declared metadata", "Producing tool and version, if known (declared only, e.g. 'PECmd 2026.10.3'):")
        from ...adapters.base import file_sha256
        from ...results.importer import DatasetContext, import_output
        case = self.ctx.case

        def work():
            p = Path(f)
            digest = file_sha256(p)
            oid = new_id("out")
            case.db.execute("""INSERT INTO outputs(id, case_id, job_id, origin, rel_path, abs_path, kind, role, size, sha256, completion, import_state, declared_tool, created_utc)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (oid, case.id, None, "external", p.name, str(p), "csv" if p.suffix.lower() == ".csv" else "json", "external", p.stat().st_size, digest,
                             "complete", "importing", tool if ok else None, utcnow()))
            res = import_output(case.results, DatasetContext(case_id=case.id, name=p.name, source_path=p, source_format="csv" if p.suffix.lower() == ".csv" else "json",
                                                             origin="external", output_id=oid, tool_id=None, tool_version=(tool or None) if ok else None, source_sha256=digest))
            case.db.execute("UPDATE outputs SET import_state=?, dataset_ids_json=? WHERE id=?", (res.state, json.dumps([res.dataset_id]), oid))
            case.audit("output.external_imported", oid, {"path": str(p), "declared": tool, "sha256": digest})
            return res
        self.ctx.run_bg(work, on_done=lambda r: (self.win.banner.show_message("ok" if r.state == "complete" else "warn",
                                                                              f"Imported as external output: {r.state}, {r.stats.total:,} records. No parser job was fabricated; unknown provenance stays unknown."),
                                                 self.refresh()))


def add_reference_to_finding(parent, ctx, ref: Reference) -> None:
    fs = FindingsService(ctx.case)
    d = AddToFindingDialog(fs.findings(), parent)
    if not d.exec() or not d.choice:
        return
    fid = fs.create(d.choice[4:]) if d.choice.startswith("new:") else d.choice
    fs.link(fid, ref)
    ctx.findingsChanged.emit()
    parent.win.banner.show_message("ok", f"Linked {ref.label} to the finding (immutable reference plus a display snapshot).")


def _brush(c: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(c))

