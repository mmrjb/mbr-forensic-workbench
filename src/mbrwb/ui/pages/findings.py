"""Findings & Reports: finding editor with evidence links, report options, preview and export."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QWidget,
)

from ...findings.service import FINDING_STATUSES, SEVERITIES, FindingsService
from ...reports.report import ReportOptions, generate, load_draft, save_draft
from ..widgets.common import Panel, button, label
from .base import Page


class FindingsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Findings & Reports", "Analyst judgments with links to the exact records and events that support them.")
        self.header.add_action(button("New finding", icon_name="plus", primary=True, on_click=self.new_finding))
        tabs = QTabWidget()
        # findings tab
        fw = QWidget()
        fl = QHBoxLayout(fw)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._load())
        split.addWidget(self.list)
        ed = Panel()
        form = QFormLayout()
        self.title = QLineEdit()
        self.status = QComboBox()
        self.status.addItems(FINDING_STATUSES)
        self.severity = QComboBox()
        for s in SEVERITIES:
            self.severity.addItem(s or "(not assessed)", s)
        self.severity.setToolTip("Analyst-assigned. The workbench never computes a severity or compromise score.")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("comma separated")
        form.addRow("Title", self.title)
        form.addRow("Status", self.status)
        form.addRow("Severity (analyst)", self.severity)
        form.addRow("Tags", self.tags)
        ed.lay.addLayout(form)
        ed.lay.addWidget(label("Narrative", obj="SectionTitle"))
        self.narrative = QPlainTextEdit()
        self.narrative.setPlaceholderText("What the evidence shows, how it was established, and its limits.")
        ed.lay.addWidget(self.narrative, 2)
        ed.lay.addWidget(label("Supporting references (snapshot taken when linked)", obj="SectionTitle"))
        self.links = QListWidget()
        self.links.itemDoubleClicked.connect(self._open_link)
        ed.lay.addWidget(self.links, 1)
        row = QHBoxLayout()
        self.meta = label("", muted=True)
        row.addWidget(self.meta, 1)
        row.addWidget(button("Remove reference", icon_name="x", on_click=self._unlink))
        row.addWidget(button("Save finding", icon_name="check", primary=True, on_click=self._save))
        ed.lay.addLayout(row)
        split.addWidget(ed)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        fl.addWidget(split)
        tabs.addTab(fw, "Findings")
        # report tab
        rw = QWidget()
        rl = QHBoxLayout(rw)
        opts = Panel()
        opts.setMaximumWidth(420)
        o = opts.lay
        o.addWidget(label("Report contents", obj="SectionTitle"))
        self.rep_findings = QListWidget()
        self.rep_findings.setMaximumHeight(160)
        o.addWidget(label("Findings to include", muted=True))
        o.addWidget(self.rep_findings)
        self.scope_note = QPlainTextEdit()
        self.scope_note.setPlaceholderText("Scope: what was examined, what was not, and why")
        self.scope_note.setMaximumHeight(100)
        o.addWidget(self.scope_note)
        self.rep_tz = QComboBox()
        self.rep_tz.setEditable(True)
        o.addWidget(label("Timezone shown in the report (UTC is always included)", muted=True))
        o.addWidget(self.rep_tz)
        o.addWidget(label("Redaction (report only; the case is unchanged)", obj="SectionTitle"))
        self.r_exam = QCheckBox("Examiner name")
        self.r_users = QCheckBox("User profile names")
        self.r_hosts = QCheckBox("Host names")
        self.r_paths = QCheckBox("Directory parts of Windows paths")
        for c in (self.r_exam, self.r_users, self.r_hosts, self.r_paths):
            o.addWidget(c)
        self.r_terms = QLineEdit()
        self.r_terms.setPlaceholderText("Additional terms to redact, comma separated")
        o.addWidget(self.r_terms)
        self.draft_note = label("", muted=True, wrap=True)
        o.addWidget(self.draft_note)
        brow = QHBoxLayout()
        brow.addWidget(button("Preview", icon_name="search", on_click=self.preview))
        brow.addWidget(button("Save HTML", icon_name="download", primary=True, on_click=lambda: self.save_report("html")))
        o.addLayout(brow)
        brow2 = QHBoxLayout()
        brow2.addWidget(button("Save Markdown", icon_name="file", on_click=lambda: self.save_report("md")))
        brow2.addWidget(button("Case manifest (JSON)", icon_name="file", on_click=lambda: self.save_report("manifest")))
        o.addLayout(brow2)
        o.addStretch(1)
        rl.addWidget(opts)
        self.preview_view = QTextBrowser()
        self.preview_view.setOpenLinks(False)
        self.preview_view.setPlaceholderText("Press Preview to render the actual report content.")
        rl.addWidget(self.preview_view, 1)
        tabs.addTab(rw, "Report")
        self.body_layout.addWidget(tabs, 1)
        self._fid: str | None = None
        self._draft_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._save_draft)
        for w in (self.scope_note,):
            w.textChanged.connect(lambda: self._draft_timer.start())
        for c in (self.r_exam, self.r_users, self.r_hosts, self.r_paths):
            c.toggled.connect(lambda *_: self._draft_timer.start())
        self.r_terms.textChanged.connect(lambda *_: self._draft_timer.start())
        ctx.findingsChanged.connect(lambda: self.isVisible() and self.refresh())

    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        fs = FindingsService(case)
        cur = self._fid
        self.list.blockSignals(True)
        self.list.clear()
        self.rep_findings.clear()
        for f in fs.findings():
            it = QListWidgetItem(f"{f['title']}\n{f['status']}{(' · ' + f['severity']) if f['severity'] else ''} · v{f['version']}")
            it.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.list.addItem(it)
            ri = QListWidgetItem(f["title"])
            ri.setData(Qt.ItemDataRole.UserRole, f["id"])
            ri.setFlags(ri.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ri.setCheckState(Qt.CheckState.Checked)
            self.rep_findings.addItem(ri)
            if f["id"] == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        info = case.info
        self.rep_tz.clear()
        self.rep_tz.addItems(["UTC"] + ([info.display_tz] if info.display_tz != "UTC" else []))
        d = load_draft(case)
        if d:
            opts, mod = d
            self.scope_note.blockSignals(True)
            self.scope_note.setPlainText(opts.scope_note)
            self.scope_note.blockSignals(False)
            self.r_exam.setChecked(opts.redact_examiner)
            self.r_users.setChecked(opts.redact_users)
            self.r_hosts.setChecked(opts.redact_hosts)
            self.r_paths.setChecked(opts.redact_paths)
            self.r_terms.setText(", ".join(opts.redact_terms))
            self.draft_note.setText(f"Draft options saved automatically (modified {mod[:19]}Z).")
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        if not self.list.count():
            self._clear_editor()

    def _clear_editor(self) -> None:
        self._fid = None
        for w in (self.title, self.tags):
            w.clear()
        self.narrative.clear()
        self.links.clear()
        self.meta.setText("No finding selected. Use New finding, or 'Add to finding' from Results or Timeline.")

    def new_finding(self) -> None:
        if self.ctx.case is None:
            return
        fid = FindingsService(self.ctx.case).create("New finding", status="draft")
        self._fid = fid
        self.refresh()
        self.title.setFocus()
        self.title.selectAll()

    def _load(self) -> None:
        it = self.list.currentItem()
        if not it or self.ctx.case is None:
            return
        fs = FindingsService(self.ctx.case)
        f = fs.finding(it.data(Qt.ItemDataRole.UserRole))
        self._fid = f["id"]
        self.title.setText(f["title"])
        self.status.setCurrentText(f["status"])
        self.severity.setCurrentIndex(max(0, self.severity.findData(f["severity"] or "")))
        self.tags.setText(f["tags"] or "")
        self.narrative.setPlainText(f["narrative"] or "")
        self.meta.setText(f"Version {f['version']} by {f['author'] or '-'}, modified {f['modified_utc'][:19]}Z. Every save keeps the previous version.")
        self.links.clear()
        for l in fs.links(f["id"]):
            s = json.loads(l["snapshot_json"])
            if l["target_kind"] == "event":
                text = f"event {l['event_id']}: {s.get('meaning')} at {s.get('utc')} ({s.get('ts_status')}) - {s.get('object') or ''}"
            elif l["target_kind"] == "record":
                text = f"record ds{l['dataset_id']}:rec{l['rec_id']} from {s.get('tool')} {s.get('tool_version') or ''} ({s.get('dataset')})"
            else:
                text = f"{l['target_kind']}: {json.dumps(s)[:120]}"
            li = QListWidgetItem(text)
            li.setData(Qt.ItemDataRole.UserRole, l)
            li.setToolTip(json.dumps(s, indent=1, ensure_ascii=False)[:3000])
            self.links.addItem(li)

    def _save(self) -> None:
        if not self._fid:
            return
        try:
            FindingsService(self.ctx.case).update(self._fid, title=self.title.text().strip() or "Untitled finding", narrative=self.narrative.toPlainText(),
                                                  status=self.status.currentText(), severity=self.severity.currentData() or "", tags=self.tags.text())
        except ValueError as exc:
            self.win.banner.show_message("error", str(exc))
            return
        self.win.banner.show_message("ok", "Finding saved (new version recorded).")
        self.refresh()

    def _unlink(self) -> None:
        it = self.links.currentItem()
        if it:
            FindingsService(self.ctx.case).unlink(it.data(Qt.ItemDataRole.UserRole)["id"])
            self._load()

    def _open_link(self, it) -> None:
        l = it.data(Qt.ItemDataRole.UserRole)
        if l["target_kind"] == "record":
            self.win.go("results", {"dataset_id": l["dataset_id"], "rec_id": l["rec_id"]})
        elif l["target_kind"] == "event":
            ev = self.ctx.case.results.query_one("SELECT dataset_id, rec_id FROM timeline_events WHERE id=?", (l["event_id"],))
            if ev:
                self.win.go("results", {"dataset_id": ev["dataset_id"], "rec_id": ev["rec_id"]})

    # -- reports -----------------------------------------------------------------------------
    def _options(self) -> ReportOptions:
        chosen = [self.rep_findings.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.rep_findings.count())
                  if self.rep_findings.item(i).checkState() == Qt.CheckState.Checked]
        return ReportOptions(finding_ids=chosen, scope_note=self.scope_note.toPlainText(), timezone=self.rep_tz.currentText() or "UTC",
                             redact_examiner=self.r_exam.isChecked(), redact_users=self.r_users.isChecked(), redact_hosts=self.r_hosts.isChecked(),
                             redact_paths=self.r_paths.isChecked(), redact_terms=[t.strip() for t in self.r_terms.text().split(",") if t.strip()])

    def _save_draft(self) -> None:
        if self.ctx.case:
            mod = save_draft(self.ctx.case, self._options())
            self.draft_note.setText(f"Draft options saved automatically (modified {mod[:19]}Z).")

    def preview(self) -> None:
        case, opts = self.ctx.case, self._options()
        if case is None:
            return
        self.ctx.run_bg(lambda: generate(case, opts, "html", preview_only=True)[0], on_done=self.preview_view.setHtml,
                        on_error=lambda m, tb: self.win.banner.show_message("error", f"Report preview failed: {m}", "Your finding edits are saved; fix the problem and retry.", tb))

    def save_report(self, fmt: str) -> None:
        case = self.ctx.case
        if case is None:
            return
        self._save_draft()
        opts = self._options()
        self.ctx.run_bg(lambda: generate(case, opts, fmt), on_done=lambda r: (self.preview_view.setHtml(r[0]) if fmt == "html" else self.preview_view.setPlainText(r[0]),
                                                                            self.win.banner.show_message("ok", f"Saved {r[1]} (SHA-256 recorded with the case).")),
                        on_error=lambda m, tb: self.win.banner.show_message("error", f"Report generation failed: {m}", "Finding edits were not affected.", tb))

