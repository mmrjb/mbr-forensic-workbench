"""Workflows: 1 Select evidence -> 2 Review analysis plan -> 3 Run."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain.enums import PlanStepStatus
from ...evidence.artifacts import label as type_label
from ...workflows.planner import Planner
from ...workflows.presets import CUSTOM_TEMPLATE, PRESETS, WorkflowSpec, load_custom
from ..dialogs import ConfigureToolDialog
from ..theme import icon, status_color
from ..widgets.common import Card, Panel, button, label
from .base import Page

STATUS_LEVEL = {PlanStepStatus.READY: "ok", PlanStepStatus.NEEDS_SETUP: "warn", PlanStepStatus.BLOCKED: "error", PlanStepStatus.NOT_PRESENT: "muted",
                PlanStepStatus.NOT_SELECTED: "muted", PlanStepStatus.NOT_APPLICABLE: "muted"}
STATUS_ICON = {"ok": "check", "warn": "warning", "error": "error", "muted": "info"}


class WorkflowsPage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Workflows", "Choose the investigation question, review exactly what will run on which evidence, then run it.")
        self.custom: list[WorkflowSpec] = []
        self.plan = None
        self.selected_wf: WorkflowSpec | None = None
        # step 1
        s1 = Panel()
        s1.lay.addWidget(label("1  Select evidence", obj="SectionTitle"))
        r = QHBoxLayout()
        r.addWidget(label("Hosts"))
        self.hosts = QListWidget()
        self.hosts.setMaximumHeight(34)
        self.hosts.setFlow(QListWidget.Flow.LeftToRight)
        self.hosts.setWrapping(True)
        r.addWidget(self.hosts, 1)
        self.ev_summary = label("", muted=True, wrap=True)
        r.addWidget(self.ev_summary, 1)
        r.addWidget(button("Add evidence", icon_name="plus", on_click=lambda: win.add_evidence()))
        s1.lay.addLayout(r)
        self.body_layout.addWidget(s1)
        split = QSplitter(Qt.Orientation.Vertical)
        # step 2: presets
        s2 = QWidget()
        l2 = QVBoxLayout(s2)
        l2.setContentsMargins(0, 0, 0, 0)
        hdr = QHBoxLayout()
        hdr.addWidget(label("2  Choose a workflow and review the plan", obj="SectionTitle"))
        hdr.addStretch(1)
        hdr.addWidget(button("Import custom workflow...", icon_name="import", on_click=self._import_custom,
                             tooltip="Declarative JSON (schema_version 1). Validated as data; scripts are not supported."))
        hdr.addWidget(button("Save template...", icon_name="download", on_click=self._save_template))
        l2.addLayout(hdr)
        self.cards = QGridLayout()
        self.cards.setSpacing(10)
        l2.addLayout(self.cards)
        split.addWidget(s2)
        # plan table
        s3 = QWidget()
        l3 = QVBoxLayout(s3)
        l3.setContentsMargins(0, 0, 0, 0)
        self.plan_title = label("Select a workflow above.", muted=True, wrap=True)
        l3.addWidget(self.plan_title)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Run", "Step", "Tool", "Evidence group", "Prerequisites / reason", "Status"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 330)
        self.table.setColumnWidth(4, 360)
        self.table.itemChanged.connect(self._toggled)
        self.table.cellDoubleClicked.connect(self._configure_row)
        l3.addWidget(self.table, 1)
        tz = QHBoxLayout()
        tz.addWidget(label("Source timezone assumption for values without an offset"))
        self.source_tz = QComboBox()
        self.source_tz.setEditable(True)
        self.source_tz.addItem("None (keep unresolved unless the tool's UTC convention applies)", None)
        for z in ("UTC", "Asia/Tehran", "Europe/London", "America/New_York"):
            self.source_tz.addItem(z, z)
        self.source_tz.setToolTip("Only used for outputs whose mapping has no verified timezone rule. The assumption is recorded on every affected event.")
        tz.addWidget(self.source_tz, 1)
        l3.addLayout(tz)
        run = QHBoxLayout()
        self.counts = label("", wrap=True)
        run.addWidget(self.counts, 1)
        run.addWidget(button("Configure selected step...", icon_name="settings", on_click=lambda: self._configure_row(self.table.currentRow(), 0)))
        self.dry_btn = button("Dry run (save plan only)", icon_name="file", on_click=self._dry_run)
        self.run_btn = button("Run analysis", icon_name="play", primary=True, on_click=self._run)
        run.addWidget(self.dry_btn)
        run.addWidget(label("3", obj="SectionTitle"))
        run.addWidget(self.run_btn)
        l3.addLayout(run)
        split.addWidget(s3)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 520])
        self.body_layout.addWidget(split, 1)
        self.hosts.itemChanged.connect(lambda *_: self.selected_wf and self._build_plan(self.selected_wf))
        ctx.toolsChanged.connect(lambda: self.selected_wf and self.isVisible() and self._build_plan(self.selected_wf))

    def _workflows(self) -> list[WorkflowSpec]:
        return PRESETS + self.custom

    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, wf in enumerate(self._workflows()):
            c = Card(wf.title, clickable=True)
            c.lay.setContentsMargins(14, 10, 14, 10)
            c.lay.setSpacing(4)
            q = label(wf.question, muted=True, wrap=True)
            c.lay.addWidget(q)
            c.lay.addStretch(1)
            c.setToolTip(f"{wf.title} ({wf.id} v{wf.version})\nSteps: " + ", ".join(dict.fromkeys(s.title for s in wf.steps)))
            c.setAccessibleName(f"Workflow {wf.title}")
            c.clicked.connect(lambda wf=wf: self._build_plan(wf))
            c.setFixedHeight(96)
            c.setProperty("selected", self.selected_wf is not None and self.selected_wf.id == wf.id)
            if self.selected_wf is not None and self.selected_wf.id == wf.id:
                c.setStyleSheet("QFrame#Card { border: 2px solid palette(link); }")
            self.cards.addWidget(c, i // 4, i % 4)
        hosts = [r[0] or "(host not set)" for r in case.db.query("SELECT DISTINCT host FROM evidence ORDER BY host")]
        prev = {self.hosts.item(i).text(): self.hosts.item(i).checkState() for i in range(self.hosts.count())}
        self.hosts.blockSignals(True)
        self.hosts.clear()
        for h in hosts:
            it = QListWidgetItem(h)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(prev.get(h, Qt.CheckState.Checked))
            self.hosts.addItem(it)
        self.hosts.blockSignals(False)
        n = case.db.scalar("SELECT COUNT(*) FROM evidence WHERE artifact_type IS NOT NULL")
        types = [f"{type_label(r[0])} ({r[1]})" for r in case.db.query("SELECT artifact_type, COUNT(*) FROM evidence WHERE artifact_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8")]
        self.ev_summary.setText(f"{n} classified item(s): " + ", ".join(types) if n else "No evidence registered yet - add evidence first.")
        if self.selected_wf:
            self._build_plan(self.selected_wf)

    def _selected_hosts(self) -> set[str] | None:
        hs = {self.hosts.item(i).text() for i in range(self.hosts.count()) if self.hosts.item(i).checkState() == Qt.CheckState.Checked}
        if len(hs) == self.hosts.count():
            return None
        return {None if h == "(host not set)" else h for h in hs}

    def _build_plan(self, wf: WorkflowSpec) -> None:
        changed = self.selected_wf is None or self.selected_wf.id != wf.id
        self.selected_wf = wf
        if changed:
            for i in range(self.cards.count()):
                w = self.cards.itemAt(i).widget()
                if w is not None:
                    w.setStyleSheet("QFrame#Card { border: 2px solid palette(link); }" if w.toolTip().startswith(wf.title + " (") else "")
        case = self.ctx.case
        hosts = self._selected_hosts()
        self.plan_title.setText(f"Planning {wf.title}...")
        self.ctx.run_bg(lambda: Planner(case, self.ctx.jobs).plan(wf, hosts=hosts), on_done=self._show_plan)

    def _show_plan(self, plan) -> None:
        self.plan = plan
        self.plan_title.setText(f"<b>{plan.workflow.title}</b> - {plan.workflow.question}  <span style='color:gray'>(double-click a step to adjust its options)</span>")
        self.table.blockSignals(True)
        self.table.setRowCount(len(plan.steps))
        for i, s in enumerate(plan.steps):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if s.selected else Qt.CheckState.Unchecked)
            if s.status != PlanStepStatus.READY:
                chk.setFlags(Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(s.title + (f" [{s.host}]" if s.host else "")))
            self.table.item(i, 1).setToolTip(s.why)
            self.table.setItem(i, 2, QTableWidgetItem(self.ctx.catalogue.tools[s.tool_id].name))
            ev = QTableWidgetItem(s.evidence_label)
            ev.setToolTip(s.evidence_label)
            self.table.setItem(i, 3, ev)
            reason = s.reason + (("  |  " + "; ".join(dict.fromkeys(s.prerequisites))) if s.prerequisites and s.status != PlanStepStatus.READY else "")
            ri = QTableWidgetItem(reason)
            ri.setToolTip(reason)
            self.table.setItem(i, 4, ri)
            lvl = STATUS_LEVEL.get(s.status, "muted")
            st = QTableWidgetItem(icon(STATUS_ICON[lvl], status_color(lvl), 14), s.status.value)
            st.setForeground(_brush(status_color(lvl)))
            self.table.setItem(i, 5, st)
        self.table.blockSignals(False)
        self._update_counts()

    def _toggled(self, item) -> None:
        if item.column() != 0 or self.plan is None:
            return
        self.plan.steps[item.row()].selected = item.checkState() == Qt.CheckState.Checked
        self._update_counts()

    def _update_counts(self) -> None:
        if not self.plan:
            return
        ready = sum(1 for s in self.plan.steps if s.selected and s.status == PlanStepStatus.READY)
        self.counts.setText(f"Plan: {self.plan.summary()}. {ready} step(s) will run. Steps that are not run are recorded with the plan.")
        self.run_btn.setEnabled(ready > 0)

    def _configure_row(self, row: int, _col: int) -> None:
        if self.plan is None or row < 0 or row >= len(self.plan.steps):
            return
        s = self.plan.steps[row]
        if self.ctx.tools.selected(s.tool_id) is None:
            self.win.go("tools", {"tool_id": s.tool_id})
            return
        d = ConfigureToolDialog(self.ctx, s.tool_id, preset_inputs=s.inputs, preset_options=s.options, parent=self)
        d.run_btn.setText("Use these options")
        d.run_btn.clicked.disconnect()
        d.run_btn.clicked.connect(lambda: (setattr(s, "options", d.collect().options), d.accept()))
        d.exec()

    def _dry_run(self) -> None:
        if not self.plan:
            return
        pid = Planner(self.ctx.case, self.ctx.jobs).save(self.plan, dry_run=True)
        self.win.banner.show_message("ok", f"Dry-run plan {pid} saved with the case. No parser was executed.")

    def _run(self) -> None:
        if not self.plan:
            return
        tz = self.source_tz.currentData() or (self.source_tz.currentText() if "/" in self.source_tz.currentText() or self.source_tz.currentText() == "UTC" else None)
        plan_id, jobs, problems = Planner(self.ctx.case, self.ctx.jobs).run(self.plan, import_options={"source_tz": tz} if tz else None)
        msg = f"Plan {plan_id}: {len(jobs)} job(s) queued."
        if problems:
            msg += " Not submitted: " + "; ".join(f"{k}: {v}" for k, v in problems)
        self.win.banner.show_message("ok" if not problems else "warn", msg, "Follow progress on the Jobs page.")
        self.win.go("jobs", {"filter": "active"})

    def _import_custom(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Custom workflow (JSON)", "", "JSON (*.json)")
        if not f:
            return
        try:
            spec = load_custom(json.loads(open(f, encoding="utf-8").read()))
        except Exception as exc:
            self.win.banner.show_message("error", "The workflow file failed validation; nothing was imported.", "Fix the listed problems.", str(exc))
            return
        self.custom = [c for c in self.custom if c.id != spec.id] + [spec]
        self.refresh()
        self.win.banner.show_message("ok", f"Custom workflow '{spec.title}' v{spec.version} loaded (validated data, registered tools only).")

    def _save_template(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, "Save custom workflow template", "custom_workflow.json", "JSON (*.json)")
        if f:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(CUSTOM_TEMPLATE, fh, indent=2)


def _brush(c: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(c))

