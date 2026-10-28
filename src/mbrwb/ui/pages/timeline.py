"""Timeline: histogram navigation, explicit time mode, unresolved list, context window."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QSpinBox,
    QSplitter,
    QTableView,
)

from ...findings.service import FindingsService, Reference
from ...results.query import TimelineQuery, TimelineView, export_timeline_csv, sanitize_filename
from ...storage.case_store import new_id
from ...storage.db import utcnow
from ...timeline.timestamps import format_ticks, parse_operator_bound
from ..theme import status_color
from ..widgets.common import EmptyState, KeyValueGrid, Panel, Pill, button, label
from ..widgets.histogram import Histogram
from ..widgets.paged_model import PagedTableModel
from .base import Page
from .results import add_reference_to_finding

STATUS_TEXT = {"utc": "explicit UTC", "offset": "explicit offset", "tool_utc_convention": "tool UTC convention", "operator_assumption": "operator timezone assumption",
               "unresolved": "unresolved (no offset, no verified rule)", "ambiguous": "ambiguous (DST)", "nonexistent": "non-existent local time (DST gap)",
               "invalid": "invalid", "zero": "zero / sentinel value"}


class TimelinePage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Timeline", "Events from reviewed output mappings only. Each event links to its exact source record and job.")
        top = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setToolTip("Display only. Changing it never reinterprets stored instants.")
        self.mode.currentIndexChanged.connect(self._reload)
        self.mode_pill = Pill("", "info")
        top.addWidget(label("Time display"))
        top.addWidget(self.mode)
        top.addWidget(self.mode_pill)
        top.addStretch(1)
        self.unresolved_btn = button("", icon_name="warning", on_click=self._toggle_unresolved)
        self.unresolved_btn.setCheckable(True)
        top.addWidget(self.unresolved_btn)
        self.body_layout.addLayout(top)
        frow = QHBoxLayout()
        self.start = QLineEdit()
        self.start.setPlaceholderText("From (YYYY-MM-DD HH:MM:SS)")
        self.end = QLineEdit()
        self.end.setPlaceholderText("To")
        self.host = QComboBox()
        self.tool = QComboBox()
        self.cat = QComboBox()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Text in path / description")
        for w in (self.start, self.end, self.text):
            w.returnPressed.connect(self._apply)
        for w in (self.host, self.tool, self.cat):
            w.currentIndexChanged.connect(self._apply)
        for w, t in ((self.start, None), (self.end, None), (self.host, "Host"), (self.tool, "Tool"), (self.cat, "Category")):
            if t:
                frow.addWidget(label(t))
            frow.addWidget(w, 1)
        frow.addWidget(self.text, 2)
        frow.addWidget(button("Apply", icon_name="filter", on_click=self._apply))
        frow.addWidget(button("Reset", icon_name="x", on_click=self._reset))
        self.body_layout.addLayout(frow)
        self.hist = Histogram(height=104)
        self.hist.rangeSelected.connect(self._range_from_hist)
        self.body_layout.addWidget(self.hist)
        crow = QHBoxLayout()
        self.count = label("", muted=True)
        crow.addWidget(self.count, 1)
        crow.addWidget(button("Save filter...", icon_name="bookmark", on_click=self._save_view))
        self.saved = QComboBox()
        self.saved.setMinimumWidth(180)
        self.saved.activated.connect(self._load_view)
        crow.addWidget(self.saved)
        crow.addWidget(button("Export CSV", icon_name="download", on_click=self._export, tooltip="Spreadsheet-safe CSV with UTC, display timezone, original values and source references"))
        self.body_layout.addLayout(crow)
        split = QSplitter(Qt.Orientation.Horizontal)
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
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._row())
        split.addWidget(self.table)
        self.detail = Panel()
        d = self.detail.lay
        self.d_title = label("Select an event", obj="SectionTitle")
        self.d_title.setWordWrap(True)
        d.addWidget(self.d_title)
        self.kv = KeyValueGrid()
        d.addWidget(self.kv)
        r1 = QHBoxLayout()
        r1.addWidget(button("Open source record", icon_name="results", on_click=self._source))
        r1.addWidget(button("Open job", icon_name="jobs", on_click=self._job))
        d.addLayout(r1)
        r2 = QHBoxLayout()
        self.ctx_minutes = QSpinBox()
        self.ctx_minutes.setRange(1, 24 * 60)
        self.ctx_minutes.setValue(5)
        self.ctx_minutes.setSuffix(" min")
        r2.addWidget(button("Show before/after", icon_name="timeline", on_click=self._context))
        r2.addWidget(self.ctx_minutes)
        d.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(button("Bookmark", icon_name="bookmark", on_click=self._bookmark))
        r3.addWidget(button("Add to finding", icon_name="findings", on_click=self._finding))
        d.addLayout(r3)
        d.addStretch(1)
        self.detail.setMinimumWidth(320)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.body_layout.addWidget(split, 1)
        self.empty = EmptyState("No timeline events", "Events are created only for outputs with reviewed mappings (Prefetch, $MFT, event logs, LNK, Jump Lists, ShellBags, Amcache, "
                                "ShimCache, Recycle Bin, Windows Timeline). An empty timeline does not mean nothing happened.",
                                [button("New analysis", icon_name="play", primary=True, on_click=win.start_analysis)], "timeline")
        self.body_layout.addWidget(self.empty)
        self.empty.hide()
        self.view = TimelineView()
        self._event: dict | None = None
        self.tq: TimelineQuery | None = None
        self.model.countReady.connect(self._counted)
        ctx.resultsChanged.connect(lambda: self.isVisible() and self.refresh())

    def focus_filter(self) -> bool:
        self.text.setFocus()
        return True

    def open_with(self, arg) -> None:
        if isinstance(arg, dict) and arg.get("range"):
            a, b = arg["range"]
            self.view = TimelineView(a, b)
            QTimer.singleShot(0, lambda: (self._sync_fields(), self._reload()))

    def tz(self) -> str:
        return self.mode.currentData() or "UTC"

    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        self.tq = TimelineQuery(case.results)
        info = case.info
        cur = self.mode.currentData()
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem("UTC", "UTC")
        if info.display_tz != "UTC":
            self.mode.addItem(f"Case timezone ({info.display_tz})", info.display_tz)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cur)))
        self.mode.blockSignals(False)
        tq = self.tq

        def load():
            return {"hosts": tq.distinct("host"), "tools": tq.distinct("tool_id"), "cats": tq.distinct("category"), "unresolved": tq.unresolved_count(),
                    "total": tq.count(TimelineView()) + tq.unresolved_count()}
        self.ctx.run_bg(load, on_done=self._filters_loaded)

    def _filters_loaded(self, d: dict) -> None:
        for combo, vals, allt in ((self.host, d["hosts"], "All hosts"), (self.tool, d["tools"], "All tools"), (self.cat, d["cats"], "All categories")):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(allt, None)
            for v in vals:
                combo.addItem(v, v)
            combo.setCurrentIndex(max(0, combo.findData(cur)))
            combo.blockSignals(False)
        self.unresolved_btn.setText(f"{d['unresolved']:,} unresolved timestamps")
        self.unresolved_btn.setToolTip("Events whose time could not be resolved (no offset without a verified rule, DST ambiguity, invalid or zero values). "
                                       "They are kept here instead of being placed at epoch zero.")
        self.empty.setVisible(d["total"] == 0)
        self.table.setVisible(d["total"] > 0)
        self.saved.clear()
        self.saved.addItem("Saved filters", None)
        for r in self.ctx.case.db.query("SELECT name, view_json FROM saved_views WHERE scope='timeline' ORDER BY created_utc DESC"):
            self.saved.addItem(r["name"], r["view_json"])
        self._reload()

    def _sync_fields(self) -> None:
        tz = self.tz()
        self.start.setText(format_ticks(self.view.start_ticks, tz, 0, with_zone=False) if self.view.start_ticks is not None else "")
        self.end.setText(format_ticks(self.view.end_ticks, tz, 0, with_zone=False) if self.view.end_ticks is not None else "")

    def _apply(self) -> None:
        tz = self.tz()
        s = parse_operator_bound(self.start.text(), tz) if self.start.text().strip() else None
        e = parse_operator_bound(self.end.text(), tz) if self.end.text().strip() else None
        if (self.start.text().strip() and s is None) or (self.end.text().strip() and e is None):
            self.win.banner.show_message("warn", f"Range values must look like 2024-03-01 10:00:00 and exist in {tz}.")
            return
        self.view = TimelineView(s, e, [self.host.currentData()] if self.host.currentData() else [], [], [self.tool.currentData()] if self.tool.currentData() else [],
                                 [self.cat.currentData()] if self.cat.currentData() else [], self.text.text(), self.unresolved_btn.isChecked())
        self._reload()

    def _reset(self) -> None:
        for w in (self.start, self.end, self.text):
            w.clear()
        for c in (self.host, self.tool, self.cat):
            c.setCurrentIndex(0)
        self.unresolved_btn.setChecked(False)
        self.view = TimelineView()
        self._reload()

    def _toggle_unresolved(self) -> None:
        self._apply()

    def _range_from_hist(self, a: int, b: int) -> None:
        self.view.start_ticks, self.view.end_ticks = a, b
        self._sync_fields()
        self._reload()

    def _reload(self) -> None:
        if self.tq is None:
            return
        tz = self.tz()
        self.mode_pill.set(f"Showing {tz}" + (" - stored instants are UTC" if tz != "UTC" else ""), "info")
        tq = self.tq
        v = TimelineView(**self.view.__dict__)
        unresolved = v.unresolved_only
        headers = (["Original value", "Status", "Meaning", "Object / path", "Description", "Host", "User", "Tool", "Source record"] if unresolved else
                   [f"Time ({tz})", "Meaning", "Object / path", "Description", "Host", "User", "Tool", "Source record", "Time basis"])

        def disp(row, col):
            d = dict(zip(TimelineQuery.COLUMNS, row))
            src = f"ds{d['dataset_id']}:rec{d['rec_id']}"
            if unresolved:
                vals = [d["raw_value"], STATUS_TEXT.get(d["ts_status"], d["ts_status"]), d["meaning"], d["object_path"], d["description"], d["host"], d["user_profile"], d["tool_id"], src]
            else:
                vals = [format_ticks(d["utc_ticks"], tz, d["precision_digits"] or 0), d["meaning"], d["object_path"], d["description"], d["host"], d["user_profile"],
                        d["tool_id"], src, STATUS_TEXT.get(d["ts_status"], d["ts_status"])]
            x = vals[col]
            return "" if x is None else str(x)
        self.model.display = disp
        self.model.row_tint = lambda row: status_color("warn") if row[3] not in ("utc", "offset", "tool_utc_convention") else None
        self.model.configure(headers, lambda off, lim: tq.page(v, off, lim), lambda: tq.count(v))
        for i, w in enumerate([230, 260, 300, 300, 110, 100, 110, 120] if not unresolved else [200, 200, 240, 260, 260, 110, 100, 100]):
            self.table.setColumnWidth(i, w)
        if not unresolved:
            self.ctx.run_bg(lambda: tq.histogram(v, 140), on_done=lambda h: self.hist.set_data(h[0], h[1], h[2], tz))
        self.hist.setVisible(not unresolved)

    def _counted(self, n: int) -> None:
        v = self.view
        scope = "in the selected range and filters" if (v.start_ticks or v.end_ticks or v.hosts or v.tools or v.categories or v.text) else "in this case"
        msg = f"{n:,} events {scope}" + (" (unresolved list)" if v.unresolved_only else "")
        if n == 0:
            msg += ". Nothing matched this filter; that does not mean no activity occurred."
        self.count.setText(msg)

    def _row(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid() or self.tq is None:
            return
        row = self.model.row_tuple(idx.row())
        if row is None:
            return
        e = self.tq.event(row[0])
        self._event = e
        tz = self.tz()
        self.d_title.setText(e["meaning"])
        self.kv.clear()
        self.kv.add("Time (UTC)", format_ticks(e["utc_ticks"], "UTC", e["precision_digits"] or 0) or "unresolved", mono=True)
        if tz != "UTC" and e["utc_ticks"] is not None:
            self.kv.add(f"Time ({tz})", format_ticks(e["utc_ticks"], tz, e["precision_digits"] or 0), mono=True)
        self.kv.add("Original value", e["raw_value"], mono=True)
        self.kv.add("Interpretation", STATUS_TEXT.get(e["ts_status"], e["ts_status"]) + (f" - {e['ts_assumption']}" if e["ts_assumption"] else ""))
        self.kv.add("Source field", e["field"])
        self.kv.add("Object", e["object_path"], mono=True)
        self.kv.add("Description", e["description"])
        self.kv.add("Host / user", f"{e['host'] or '-'} / {e['user_profile'] or '-'}")
        self.kv.add("Tool", f"{e['tool_id']} {e['tool_version'] or ''}")
        self.kv.add("Source record", f"ds{e['dataset_id']}:rec{e['rec_id']}", mono=True)
        self.kv.add("Source output", e["source_output"], mono=True)
        self.kv.add("Job", e["job_id"] or "- (externally generated output; no workbench job)", mono=bool(e["job_id"]))
        self.kv.add("Mapping", f"{e['mapping_id']} v{e['mapping_version']}")

    def _source(self) -> None:
        if self._event:
            self.win.go("results", {"dataset_id": self._event["dataset_id"], "rec_id": self._event["rec_id"]})

    def _job(self) -> None:
        if self._event and self._event["job_id"]:
            self.win.go("jobs", {"job_id": self._event["job_id"]})

    def _context(self) -> None:
        if not self._event or self._event["utc_ticks"] is None:
            self.win.banner.show_message("info", "This event has no resolved time, so no context window can be computed.")
            return
        v = self.tq.context(self._event["id"], self.ctx_minutes.value() * 60)
        self.view = v
        self.host.setCurrentIndex(max(0, self.host.findData(v.hosts[0]) if v.hosts else 0))
        self._sync_fields()
        self._reload()

    def _bookmark(self) -> None:
        if self._event:
            FindingsService(self.ctx.case).bookmark(Reference("event", event_id=self._event["id"]))
            self.win.banner.show_message("ok", f"Bookmarked event {self._event['id']}.")

    def _finding(self) -> None:
        if self._event:
            add_reference_to_finding(self, self.ctx, Reference("event", event_id=self._event["id"]))

    def _save_view(self) -> None:
        name, ok = QInputDialog.getText(self, "Save timeline filter", "Name")
        if ok and name.strip():
            self.ctx.case.db.execute("INSERT INTO saved_views(id, case_id, scope, name, view_json, created_utc) VALUES (?,?,?,?,?,?)",
                                     (new_id("view"), self.ctx.case.id, "timeline", name.strip(), self.view.to_json(), utcnow()))
            self.refresh()

    def _load_view(self, i: int) -> None:
        j = self.saved.itemData(i)
        if j:
            self.view = TimelineView.from_json(j)
            self._sync_fields()
            self._reload()

    def _export(self) -> None:
        case = self.ctx.case
        if case is None or self.tq is None:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Export timeline", str(case.root / "exports" / sanitize_filename(f"timeline_{utcnow()[:10]}.csv")))
        if not f:
            return
        tq, v, tz = self.tq, TimelineView(**self.view.__dict__), self.tz()
        self.ctx.run_bg(lambda: export_timeline_csv(tq, v, f, tz), on_done=lambda n: (case.audit("timeline.exported", f, {"events": n, "tz": tz}),
                                                                                     self.win.banner.show_message("ok", f"Exported {n:,} events with UTC and {tz} columns to {f}.")))

