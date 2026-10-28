"""Jobs: list + Summary / Logs / Outputs / Provenance tabs from persisted data."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.enums import JobState
from ...results.query import text_preview
from ...storage.db import utcnow
from ..dialogs import TextViewer
from ..theme import icon, mono_font, status_color
from ..widgets.common import KeyValueGrid, Pill, button, label
from ..widgets.paged_model import RowsModel
from .base import Page

STATE_LEVEL = {"Completed": "ok", "Partial": "warn", "Failed": "error", "Timed out": "error", "Interrupted": "error", "Cancelled": "muted",
               "Skipped": "muted", "Queued": "info", "Preflight": "info", "Running": "info", "Importing": "info"}
STATE_ICON = {"ok": "check", "warn": "warning", "error": "error", "muted": "stop", "info": "jobs"}


def _elapsed(start: str | None, end: str | None) -> str:
    import datetime as dt
    if not start:
        return ""
    try:
        a = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat((end or utcnow()).replace("Z", "+00:00"))
        s = int((b - a).total_seconds())
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    except ValueError:
        return ""


class JobsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Jobs", "Each parser run with its frozen tool identity, arguments, logs and outputs.")
        top = QHBoxLayout()
        self.state_filter = QComboBox()
        self.state_filter.addItem("All jobs", None)
        self.state_filter.addItem("Needs attention (failed, timed out, interrupted, partial)", "attention")
        self.state_filter.addItem("Active", "active")
        for s in JobState:
            self.state_filter.addItem(s.value, s.value)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(label("Show"))
        top.addWidget(self.state_filter)
        top.addStretch(1)
        self.cancel_btn = button("Cancel job", icon_name="stop", danger=True, on_click=self._cancel, tooltip="Stops this job's whole process tree.")
        self.cancel_plan_btn = button("Cancel rest of workflow", icon_name="stop", on_click=self._cancel_plan, tooltip="Cancels the queued/running jobs of this job's workflow only.")
        self.retry_btn = button("Retry", icon_name="refresh", on_click=self._retry, tooltip="Runs again in a fresh job folder, linked to this job.")
        self.folder_btn = button("Open job folder", icon_name="folder", on_click=self._folder)
        for b in (self.cancel_btn, self.cancel_plan_btn, self.retry_btn, self.folder_btn):
            top.addWidget(b)
        self.body_layout.addLayout(top)
        split = QSplitter(Qt.Orientation.Vertical)
        self.model = RowsModel(["State", "Tool", "Job", "Evidence group", "Host", "Started (UTC)", "Elapsed", "Warnings", "Outcome"])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._show_selected())
        split.addWidget(self.table)
        self.tabs = QTabWidget()
        # summary
        sw = QWidget()
        sl = QVBoxLayout(sw)
        hr = QHBoxLayout()
        self.pill = Pill("", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(220)
        self.progress.setToolTip("This tool does not report progress; the bar only shows that the job is active.")
        hr.addWidget(self.pill)
        hr.addWidget(self.progress)
        hr.addStretch(1)
        sl.addLayout(hr)
        self.kv = KeyValueGrid()
        sl.addWidget(self.kv)
        sl.addStretch(1)
        self.tabs.addTab(sw, "Summary")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono_font(9))
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lw = QWidget()
        ll = QVBoxLayout(lw)
        ll.addWidget(self.log, 1)
        lrow = QHBoxLayout()
        self.log_note = label("", muted=True)
        lrow.addWidget(self.log_note, 1)
        lrow.addWidget(button("Open full stdout log", icon_name="file", on_click=lambda: self._view_log("stdout.log")))
        lrow.addWidget(button("Open full stderr log", icon_name="file", on_click=lambda: self._view_log("stderr.log")))
        ll.addLayout(lrow)
        self.tabs.addTab(lw, "Logs")
        ow = QWidget()
        ol = QVBoxLayout(ow)
        self.out_model = RowsModel(["File", "Type", "Bytes", "SHA-256", "Completion", "Import", "Details"])
        self.out_table = QTableView()
        self.out_table.setModel(self.out_model)
        self.out_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.out_table.verticalHeader().hide()
        self.out_table.horizontalHeader().setStretchLastSection(True)
        self.out_table.doubleClicked.connect(self._open_output)
        ol.addWidget(self.out_table, 1)
        orow = QHBoxLayout()
        orow.addWidget(button("Open in Results / viewer", icon_name="results", on_click=self._open_output))
        orow.addWidget(button("Show folder", icon_name="folder", on_click=self._output_folder))
        orow.addWidget(button("Re-import", icon_name="import", on_click=self._reimport, tooltip="Imports this preserved output again into a fresh dataset."))
        orow.addStretch(1)
        ol.addLayout(orow)
        self.tabs.addTab(ow, "Outputs")
        self.prov = QPlainTextEdit()
        self.prov.setReadOnly(True)
        self.prov.setFont(mono_font(9))
        self.tabs.addTab(self.prov, "Provenance")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.body_layout.addWidget(split, 1)
        self._job: dict | None = None
        self._timer = QTimer(self, interval=500, timeout=self._tick)
        ctx.jobChanged.connect(self._job_changed)

    def open_with(self, arg) -> None:
        if isinstance(arg, dict) and arg.get("filter"):
            i = self.state_filter.findData(arg["filter"])
            self.state_filter.setCurrentIndex(max(0, i))
        if isinstance(arg, dict) and arg.get("job_id"):
            self._select_after = arg["job_id"]
            self.refresh()

    def _job_changed(self, case_id: str, job_id: str) -> None:
        if self.isVisible() and self.ctx.case and case_id == self.ctx.case.id:
            self._refresh_timer = getattr(self, "_refresh_timer", None) or QTimer(self, singleShot=True, timeout=self.refresh)
            self._refresh_timer.start(300)

    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        f = self.state_filter.currentData()

        def load():
            q, a = "SELECT * FROM jobs", []
            if f == "attention":
                q += " WHERE state IN ('Failed','Timed out','Interrupted','Partial')"
            elif f == "active":
                q += " WHERE state IN ('Queued','Preflight','Running','Importing')"
            elif f:
                q += " WHERE state=?"
                a = [f]
            return [dict(r) for r in case.db.query(q + " ORDER BY created_utc DESC LIMIT 2000", a)]
        self.ctx.run_bg(load, on_done=self._fill)

    def _fill(self, jobs: list[dict]) -> None:
        sel = self._job["id"] if self._job else getattr(self, "_select_after", None)
        rows, payload, tints, icons = [], [], [], []
        for j in jobs:
            lvl = STATE_LEVEL.get(j["state"], "muted")
            warns = len(json.loads(j["warnings_json"] or "[]"))
            outcome = f"parser {j['parser_outcome'] or '-'}, import {j['import_outcome'] or '-'}"
            rows.append([j["state"], j["tool_id"], j["title"], j["evidence_group"], j["host"], (j["started_utc"] or "")[:19], _elapsed(j["started_utc"], j["ended_utc"]),
                         warns or "", outcome])
            payload.append(j)
            tints.append(status_color(lvl))
            icons.append(icon(STATE_ICON[lvl], status_color(lvl), 14))
        self.model.set_rows(rows, payload, tints, icons)
        if sel:
            for i, j in enumerate(payload):
                if j["id"] == sel:
                    self.table.selectRow(i)
                    break
        if not jobs:
            self.kv.clear()
            self.kv.add("Jobs", "No jobs match this view. Start one with New analysis or Tools > Configure analysis.")

    def _show_selected(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        self._job = self.model.payload[idx.row()]
        self._render()

    def _render(self) -> None:
        j = self._job
        case = self.ctx.case
        if not j or case is None:
            return
        j = dict(case.db.query_one("SELECT * FROM jobs WHERE id=?", (j["id"],)))
        self._job = j
        lvl = STATE_LEVEL.get(j["state"], "muted")
        self.pill.set(j["state"] + (f" - {j['phase']}" if j["phase"] and JobState(j["state"]).is_active else ""), lvl)
        active = JobState(j["state"]).is_active
        self.progress.setVisible(active)
        self.cancel_btn.setEnabled(active)
        self.cancel_plan_btn.setEnabled(bool(j["plan_id"]))
        self.retry_btn.setEnabled(not active and j["kind"] == "parser")
        self.kv.clear()
        cls = json.loads(j["classification_json"] or "{}")
        self.kv.add("Job", j["title"])
        self.kv.add("Tool", f"{j['tool_id']} {j['tool_version'] or ''} ({j['verification'] or 'n/a'})")
        self.kv.add("Evidence", j["evidence_group"] or "-")
        self.kv.add("Host / user", f"{j['host'] or '-'} / {j['user_profile'] or '-'}")
        self.kv.add("Started / ended (UTC)", f"{j['started_utc'] or '-'} / {j['ended_utc'] or '-'} ({_elapsed(j['started_utc'], j['ended_utc'])})")
        self.kv.add("Exit code", j["exit_code"])
        self.kv.add("Parser outcome", f"{j['parser_outcome'] or '-'}: {cls.get('reason', '')}")
        self.kv.add("Import outcome", j["import_outcome"] or "-")
        if j["error_message"] and j["state"] != "Completed":
            self.kv.add("Problem", f"{j['error_category'] or ''}: {j['error_message']}")
        if j["next_action"]:
            self.kv.add("Next action", j["next_action"])
        warns = json.loads(j["warnings_json"] or "[]")
        if warns:
            self.kv.add("Warnings", "\n".join(warns[:20]) + (f"\n... {len(warns) - 20} more in the log" if len(warns) > 20 else ""))
        if j["retry_of"]:
            self.kv.add("Retry of", j["retry_of"])
        self._render_logs()
        outs = [dict(r) for r in case.db.query("SELECT * FROM outputs WHERE job_id=? ORDER BY rel_path", (j["id"],))]
        self.out_model.set_rows([[o["rel_path"], o["kind"], f"{o['size'] or 0:,}", (o["sha256"] or "")[:16] + "...", o["completion"], o["import_state"], o["import_detail"] or ""]
                                 for o in outs], outs)
        inputs = [dict(r) for r in case.db.query("SELECT role, path, evidence_id, size_at_submit, sha256_at_submit, size_at_run, checked_at_run_utc FROM job_inputs WHERE job_id=?", (j["id"],))]
        prov = {k: j[k] for k in ("id", "case_id", "plan_id", "step_key", "retry_of", "kind", "installation_id", "profile_id", "verification", "executable",
                                  "exe_sha256", "tool_version", "cwd", "output_root", "display_tz", "created_utc")}
        prov["launcher"] = json.loads(j["launcher_json"] or "[]")
        prov["arguments"] = json.loads(j["args_json"])
        prov["display_command (not executed)"] = j["display_command"]
        prov["dependencies"] = json.loads(j["dependency_json"] or "{}")
        prov["options"] = json.loads(j["options_json"] or "{}")
        prov["inputs"] = inputs
        self.prov.setPlainText(json.dumps(prov, indent=2, ensure_ascii=False))
        if active:
            self._timer.start()
        else:
            self._timer.stop()

    def _render_logs(self) -> None:
        j = self._job
        if not j:
            return
        lines = self.ctx.job_lines(j["id"])
        if lines:
            self.log.setPlainText("\n".join(lines))
            self.log_note.setText(f"Live preview (last {len(lines)} lines). Full logs are kept on disk.")
        else:
            parts = []
            for name in ("stdout.log", "stderr.log"):
                p = Path(j["job_dir"]) / name
                if p.exists():
                    t, trunc = text_preview(p, max_bytes=256 * 1024)
                    parts.append(f"--- {name}{' (first 256 KB)' if trunc else ''} ---\n{t}")
            self.log.setPlainText("\n".join(parts) or "No log output recorded.")
            self.log_note.setText("Bounded preview of the saved logs.")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _tick(self) -> None:
        if self._job and self.isVisible():
            self._render_logs()
            r = self.ctx.case.db.query_one("SELECT state, started_utc FROM jobs WHERE id=?", (self._job["id"],)) if self.ctx.case else None
            if r is None or not JobState(r["state"]).is_active:
                self._timer.stop()
                self._render()

    def _cancel(self) -> None:
        if self._job:
            self.ctx.jobs.cancel(self._job["id"])
            self.win.banner.show_message("info", f"Cancellation requested for {self._job['title']}. Cleanup of its process tree is shown in the job state.")

    def _cancel_plan(self) -> None:
        if self._job and self._job["plan_id"]:
            n = self.ctx.jobs.cancel_plan(self._job["plan_id"])
            self.win.banner.show_message("info", f"Cancelled {n} remaining job(s) of this workflow. Other work was not affected.")

    def _retry(self) -> None:
        if not self._job:
            return
        jid, pf = self.ctx.jobs.retry(self.ctx.case, self._job["id"])
        if jid:
            self.win.banner.show_message("ok", f"Retry submitted as {jid}.")
            self._select_after = jid
            self._job = None
            self.refresh()
        else:
            p = pf.blockers[0]
            self.win.banner.show_message("error", p.message, p.next_action)

    def _folder(self) -> None:
        if self._job:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._job["job_dir"]))

    def _view_log(self, name: str) -> None:
        if self._job:
            p = Path(self._job["job_dir"]) / name
            if p.exists():
                TextViewer(str(p), f"{name} - {self._job['title']}", self).exec()

    def _selected_output(self) -> dict | None:
        idx = self.out_table.currentIndex()
        return self.out_model.payload[idx.row()] if idx.isValid() else None

    def _open_output(self, *_) -> None:
        o = self._selected_output()
        if not o:
            return
        ids = json.loads(o["dataset_ids_json"] or "[]")
        if ids:
            self.win.go("results", {"dataset_id": ids[0]})
        elif o["kind"] in ("text", "log", "csv", "json", "xml", "body", "html"):
            TextViewer(o["abs_path"], o["rel_path"], self).exec()
        else:
            self._output_folder()

    def _output_folder(self) -> None:
        o = self._selected_output()
        if o:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(o["abs_path"]).parent)))

    def _reimport(self) -> None:
        o = self._selected_output()
        if not o or o["kind"] not in ("csv", "json", "jsonl"):
            return
        from ...results.importer import DatasetContext, import_output
        from ...timeline.mapper import map_dataset
        case, j = self.ctx.case, self._job

        def work():
            ctx = DatasetContext(case_id=case.id, name=o["rel_path"], source_path=Path(o["abs_path"]), source_format="csv" if o["kind"] == "csv" else "json",
                                 output_id=o["id"], job_id=j["id"], tool_id=j["tool_id"], tool_version=j["tool_version"], host=j["host"], user_profile=j["user_profile"],
                                 schema_hint=_schema_hint(j["tool_id"], o["role"]), source_sha256=o["sha256"])
            res = import_output(case.results, ctx)
            if res.state in ("complete", "partial"):
                map_dataset(case.results, res.dataset_id, schema_hint=ctx.schema_hint)
                old = json.loads(o["dataset_ids_json"] or "[]")
                case.db.execute("UPDATE outputs SET import_state=?, dataset_ids_json=? WHERE id=?", ("imported" if res.state == "complete" else "partial",
                                                                                                    json.dumps(old + [res.dataset_id]), o["id"]))
            return res
        self.ctx.run_bg(work, on_done=lambda r: (self.win.banner.show_message("ok" if r.state == "complete" else "warn", f"Re-import: {r.state}, {r.stats.total} records."),
                                                 self._render(), self.ctx.resultsChanged.emit()))


def _schema_hint(tool_id: str, role: str | None) -> str | None:
    from ...adapters.registry import get_adapter
    a = get_adapter(tool_id)
    if a is None or not role:
        return None
    for p in a.profiles:
        for pat in p.output_patterns:
            if pat.role == role:
                return pat.schema_hint
    return None
