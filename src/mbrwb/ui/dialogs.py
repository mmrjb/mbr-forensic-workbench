"""Dialogs: new case, add evidence, configure analysis, command palette, first run, text viewer,
add-to-finding."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from zoneinfo import available_timezones

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..adapters.base import AdapterProfile, OptionKind, Request, ValidatedRequest
from ..adapters.registry import get_adapter
from ..config.paths import is_writable_dir
from ..domain.models import EvidenceRef
from ..evidence.artifacts import ARTIFACT_TYPES, DIRECTORY_OF, label as type_label
from ..evidence.discovery import discover
from ..evidence.service import EvidenceService
from ..results.query import stream_text_search, text_preview
from ..storage.case_store import CaseError, CaseStore, peek_case
from .theme import mono_font
from .widgets.common import Banner, Pill, button, label


class Dialog(QDialog):
    def __init__(self, title: str, parent=None, width: int = 640):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 16)
        self.lay.setSpacing(12)
        head = label(title, obj="SectionTitle")
        self.lay.addWidget(head)


# -- new case ------------------------------------------------------------------------------

class NewCaseDialog(Dialog):
    def __init__(self, ctx, parent=None):
        super().__init__("New case", parent, 620)
        self.ctx = ctx
        self.store: CaseStore | None = None
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Required, e.g. IR-2026-114 LAB-WIN-01")
        self.examiner = QLineEdit(ctx.settings.case_defaults.examiner)
        self.tz = QComboBox()
        self.tz.setEditable(True)
        zones = sorted(z for z in available_timezones() if "/" in z or z == "UTC")
        self.tz.addItems(["UTC"] + [z for z in zones if z != "UTC"])
        self.tz.setCurrentText(ctx.settings.case_defaults.display_timezone)
        self.folder = QLineEdit(str(ctx.settings_store.cases_dir))
        browse = button("Browse", icon_name="folder", on_click=self._browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Optional ticket / organisation reference")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Optional description")
        self.desc.setMaximumHeight(80)
        form.addRow("Title *", self.title)
        form.addRow("Examiner", self.examiner)
        form.addRow("Display timezone", self.tz)
        form.addRow("Store cases in", frow)
        form.addRow("Reference", self.reference)
        form.addRow("Description", self.desc)
        self.lay.addLayout(form)
        self.preview = label("", muted=True, wrap=True)
        self.lay.addWidget(self.preview)
        self.banner = Banner()
        self.lay.addWidget(self.banner)
        bb = QDialogButtonBox()
        self.ok = button("Create case", primary=True)
        bb.addButton(self.ok, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.rejected.connect(self.reject)
        self.ok.clicked.connect(self._create)
        self.lay.addWidget(bb)
        self.title.textChanged.connect(self._update)
        self.folder.textChanged.connect(self._update)
        self._update()

    def _target(self) -> Path:
        from ..results.query import sanitize_filename
        return Path(self.folder.text()) / sanitize_filename(self.title.text().strip(), "case")

    def _update(self) -> None:
        ok = bool(self.title.text().strip())
        self.ok.setEnabled(ok)
        self.preview.setText(f"Case folder: {self._target()}" if ok else "Enter a title to continue.")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder for cases", self.folder.text())
        if d:
            self.folder.setText(d)

    def _create(self) -> None:
        base = Path(self.folder.text())
        ok, why = is_writable_dir(base)
        if not ok:
            self.banner.show_message("error", f"The case folder location is not writable ({why}).", "Choose a writable folder.")
            return
        forbidden = []
        for r in self.ctx.app_db.query("SELECT case_root FROM recent_cases"):
            info = peek_case(Path(r["case_root"]))
            if info:
                forbidden += info["evidence_roots"]
        try:
            self.store = CaseStore.create(self._target(), title=self.title.text(), examiner=self.examiner.text(), display_tz=self.tz.currentText() or "UTC",
                                          description=self.desc.toPlainText(), reference=self.reference.text(), forbidden_parents=forbidden)
        except (CaseError, ValueError, OSError) as exc:
            self.banner.show_message("error", str(exc), "Change the title or the folder.")
            return
        self.ctx.settings_store.remember_path("cases_dir", base)
        self.accept()


# -- add evidence --------------------------------------------------------------------------

class AddEvidenceDialog(Dialog):
    """Pick -> background discovery (cancellable) -> preview -> register -> background hashing."""

    def __init__(self, ctx, paths: list[str] | None = None, parent=None):
        super().__init__("Add evidence", parent, 720)
        self.ctx = ctx
        self.report = None
        self.worker = None
        self.root_id = None
        top = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setProperty("mono", True)
        self.path.setPlaceholderText("Evidence file or collection folder (for example a KAPE target output)")
        top.addWidget(self.path, 1)
        top.addWidget(button("File", icon_name="file", on_click=self._pick_file))
        top.addWidget(button("Folder", icon_name="folder", on_click=self._pick_folder))
        self.lay.addLayout(top)
        hrow = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("Source host (optional; inferred from folder names when possible)")
        hrow.addWidget(label("Host"))
        hrow.addWidget(self.host, 1)
        self.lay.addLayout(hrow)
        self.scan_btn = button("Inspect", icon_name="search", primary=True, on_click=self._scan)
        self.lay.addWidget(self.scan_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = label("", muted=True, wrap=True)
        self.lay.addWidget(self.progress)
        self.lay.addWidget(self.status)
        self.preview = QGroupBox("Preview of what will be registered")
        pv = QVBoxLayout(self.preview)
        self.summary = label("", wrap=True, selectable=True)
        pv.addWidget(self.summary)
        mrow = QHBoxLayout()
        self.in_place = QRadioButton("Reference in place (no copy)")
        self.copy = QRadioButton("Make working copies inside the case")
        g = QButtonGroup(self)
        g.addButton(self.in_place)
        g.addButton(self.copy)
        (self.copy if ctx.settings.case_defaults.evidence_mode == "working_copy" else self.in_place).setChecked(True)
        mrow.addWidget(self.in_place)
        mrow.addWidget(self.copy)
        pv.addLayout(mrow)
        self.include_unclassified = QCheckBox("Also register unclassified files (for strings search or viewing)")
        self.hash_now = QCheckBox("Hash registered files in the background (SHA-256)")
        self.hash_now.setChecked(ctx.settings.case_defaults.hash_on_register)
        pv.addWidget(self.include_unclassified)
        pv.addWidget(self.hash_now)
        self.types = QListWidget()
        self.types.setMaximumHeight(150)
        pv.addWidget(self.types)
        self.preview.hide()
        self.lay.addWidget(self.preview)
        self.banner = Banner()
        self.lay.addWidget(self.banner)
        bb = QDialogButtonBox()
        self.reg = button("Register evidence", primary=True)
        self.reg.setEnabled(False)
        self.cancel_btn = button("Cancel")
        bb.addButton(self.reg, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.reg.clicked.connect(self._register)
        self.cancel_btn.clicked.connect(self._cancel)
        self.lay.addWidget(bb)
        if paths:
            self.path.setText(paths[0])
            QTimer.singleShot(0, self._scan)

    def _pick_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Evidence file", self.ctx.settings.recent_paths.get("evidence", ""))
        if f:
            self.path.setText(f)

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Evidence folder", self.ctx.settings.recent_paths.get("evidence", ""))
        if d:
            self.path.setText(d)

    def _scan(self) -> None:
        p = self.path.text().strip()
        if not p or not os.path.exists(p):
            self.banner.show_message("error", "Select an existing file or folder.", "Use File or Folder.")
            return
        self.ctx.settings_store.remember_path("evidence", str(Path(p).parent if os.path.isfile(p) else p))
        self.progress.show()
        self.scan_btn.setEnabled(False)
        self.status.setText("Inspecting files in the background...")
        case = self.ctx.case
        skip = [str(case.root)] if case else []

        def job(w_cancel):
            return discover(p, default_host=self.host.text().strip() or None, cancel=w_cancel, skip_dirs=skip)
        self._cancel_ev = threading.Event()
        self.worker = self.ctx.run_bg(job, self._cancel_ev, on_done=self._scanned, on_error=self._failed)

    def _cancel(self) -> None:
        if getattr(self, "_cancel_ev", None) is not None and self.progress.isVisible():
            self._cancel_ev.set()
            self.status.setText("Cancelling...")
            return
        self.reject()

    def _failed(self, msg: str, tb: str) -> None:
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.banner.show_message("error", "Inspection failed.", "Check that the location is readable.", tb)

    def _scanned(self, rep) -> None:
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.report = rep
        by_type: dict[str, int] = {}
        for c in rep.candidates:
            by_type[c.artifact_type] = by_type.get(c.artifact_type, 0) + 1
        self.types.clear()
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            it = QListWidgetItem(f"{type_label(t)}: {n}")
            it.setToolTip(ARTIFACT_TYPES[t].tooltip if t in ARTIFACT_TYPES else "")
            self.types.addItem(it)
        filename_only = sum(1 for c in rep.candidates if c.basis == "filename")
        ev = EvidenceService(self.ctx.case)
        plan = ev.plan(rep, mode="working_copy" if self.copy.isChecked() else "in_place", host=self.host.text().strip() or None)
        size_mb = plan.total_bytes / 1024 / 1024
        self.summary.setText(
            f"{rep.files_inspected} files inspected; {len(rep.candidates)} supported candidates "
            f"({filename_only} identified by file name only - suggestions you can correct); {len(rep.unclassified)} unclassified; "
            f"{len(rep.unavailable)} unavailable; {len(rep.excluded)} excluded (links/junctions not followed, workbench folders).\n"
            f"Estimated volume: {size_mb:,.1f} MB. {plan.grouping_note}\n"
            + ("Cancelled: results are partial. " if rep.cancelled else "")
            + (f"Working copies go to: {plan.destination}" if plan.destination else "Files stay where they are; the workbench only reads them."))
        self.preview.show()
        self.reg.setEnabled(bool(rep.candidates) or bool(rep.unclassified))
        self.status.setText("Review the preview, then register.")

    def _register(self) -> None:
        if self.report is None:
            return
        ev = EvidenceService(self.ctx.case)
        try:
            self.root_id = ev.register(self.report, mode="working_copy" if self.copy.isChecked() else "in_place", host=self.host.text().strip() or None,
                                       include_unclassified=self.include_unclassified.isChecked())
        except Exception as exc:
            self.banner.show_message("error", str(exc), "Choose another location.")
            return
        if self.hash_now.isChecked():
            limit = self.ctx.settings.case_defaults.hash_size_limit_mb * 1024 * 1024
            rid = self.root_id
            self.ctx.run_bg(lambda: EvidenceService(self.ctx.case).hash_pending(root_id=rid, size_limit=limit),
                            on_done=lambda counts: (self.ctx.evidenceChanged.emit(), self.ctx.notice.emit("ok", f"Hashing finished: {counts}")))
        self.ctx.evidenceChanged.emit()
        self.accept()


# -- configure analysis ----------------------------------------------------------------------

class ConfigureToolDialog(Dialog):
    """Form generated from the selected tool version's adapter profile."""

    def __init__(self, ctx, tool_id: str, *, preset_inputs: dict | None = None, preset_options: dict | None = None, parent=None):
        tool = ctx.catalogue.tools[tool_id]
        super().__init__(f"Configure analysis - {tool.name}", parent, 820)
        self.ctx, self.tool_id, self.tool = ctx, tool_id, tool
        self.adapter = get_adapter(tool_id)
        self.inst = ctx.tools.selected(tool_id)
        self.caps = self.adapter.describe_capabilities(self.inst, ctx.tools.verified_hashes(tool_id) if self.inst else None)
        self.profile: AdapterProfile | None = self.caps.profile
        self.widgets: dict[str, QWidget] = {}
        self.inputs: dict[str, QComboBox] = {}
        self.submitted_job: str | None = None
        info = QHBoxLayout()
        info.addWidget(label(tool.description, muted=True, wrap=True), 1)
        info.addWidget(Pill(self.caps.verification.label, "ok" if self.caps.compatible else "warn"))
        self.lay.addLayout(info)
        if self.inst:
            self.lay.addWidget(label(f"Version {self.inst.version} ({self.inst.flavor}) - SHA-256 {self.inst.exe_sha256[:16]}... - {self.caps.message}", muted=True, wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(12)
        scroll.setWidget(body)
        self.lay.addWidget(scroll, 1)
        self.setMinimumHeight(640)
        if self.profile is None:
            bl.addWidget(label("No reviewed adapter profile matches this installation, so no form can be offered. "
                               "Use 'Verify this version' on the Tools page or select a supported version.", wrap=True))
            self._finish_buttons(enabled=False)
            return
        # inputs
        g_in = QGroupBox("Evidence")
        f_in = QFormLayout(g_in)
        case = ctx.case
        ev = EvidenceService(case) if case else None
        for role in self.profile.inputs:
            cb = QComboBox()
            cb.setMinimumWidth(420)
            cb.addItem("(none)", None)
            if ev is not None:
                if role.is_dir:
                    for t in role.artifact_types:
                        if t in ("prefetch_directory", "evtx_directory", "lnk_directory", "jumplist_directory", "recyclebin_directory", "sum_directory",
                                 "iis_log_directory", "sqlite_directory"):
                            ft = {v: k for k, v in DIRECTORY_OF.items()}.get(t)
                            for g in ev.directory_groups(ft) if ft else []:
                                cb.addItem(f"{g.path}  [{g.host or 'host?'}]", g)
                    roots = case.db.query("SELECT path, host FROM evidence_roots WHERE kind='folder'")
                    for r in roots:
                        cb.addItem(f"{r['path']}  [collection root]", EvidenceRef("path", r["path"], r["path"], "collection_root", r["host"], is_dir=True))
                else:
                    types = None if "*" in role.artifact_types else tuple(role.artifact_types)
                    for ref in ev.items(artifact_types=types)[:5000]:
                        cb.addItem(f"{ref.original_path}  [{type_label(ref.artifact_type)} | {ref.host or 'host?'}]", ref)
            cb.addItem("Browse for an unregistered path...", "browse")
            cb.activated.connect(lambda i, c=cb, r=role: self._maybe_browse(c, r))
            cb.currentIndexChanged.connect(self._refresh_preview)
            cb.setToolTip(role.help)
            f_in.addRow(role.label + (" *" if role.required and not role.group else ""), cb)
            self.inputs[role.key] = cb
            if preset_inputs and role.key in preset_inputs:
                for ref in preset_inputs[role.key][:1]:
                    cb.insertItem(1, f"{ref.path}  [{ref.host or 'host?'}]", ref)
                    cb.setCurrentIndex(1)
        groups = {r.group for r in self.profile.inputs if r.group}
        if groups:
            f_in.addRow("", label("Choose one of the alternatives marked as the same input (file or folder).", muted=True))
        bl.addWidget(g_in)
        g_basic = QGroupBox("Basic options")
        f_basic = QFormLayout(g_basic)
        g_adv = QGroupBox("Advanced options")
        g_adv.setCheckable(True)
        g_adv.setChecked(False)
        adv_body = QWidget()
        f_adv = QFormLayout(adv_body)
        QVBoxLayout(g_adv).addWidget(adv_body)
        g_adv.toggled.connect(adv_body.setVisible)
        adv_body.setVisible(False)
        for opt in self.profile.options:
            w = self._option_widget(opt, (preset_options or {}).get(opt.key, opt.default))
            self.widgets[opt.key] = w
            lbl = QLabel(opt.label)
            lbl.setToolTip(f"{opt.flag}: {opt.help}")
            w.setToolTip(f"{opt.flag}: {opt.help}")
            (f_adv if opt.advanced else f_basic).addRow(lbl, w)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Additional options of this tool version (validated; inputs/outputs are managed)")
        self.extra.setProperty("mono", True)
        self.extra.textChanged.connect(self._refresh_preview)
        f_adv.addRow("Extra arguments", self.extra)
        bl.addWidget(g_basic)
        bl.addWidget(g_adv)
        g_out = QGroupBox("Output")
        f_out = QFormLayout(g_out)
        dest = "a new job folder in this case: jobs/<job id>/outputs" if case else "(open a case to run)"
        f_out.addRow("Destination", label(dest, mono=True, wrap=True))
        f_out.addRow("Managed by the workbench", label(", ".join(f"{m.flag} ({m.label})" for m in self.profile.managed_outputs), muted=True, wrap=True))
        bl.addWidget(g_out)
        g_cmd = QGroupBox("Command preview (display only; the job runs as an argument list without a shell)")
        vl = QVBoxLayout(g_cmd)
        self.cmd = QPlainTextEdit()
        self.cmd.setReadOnly(True)
        self.cmd.setFont(mono_font(9))
        self.cmd.setMaximumHeight(90)
        vl.addWidget(self.cmd)
        vl.addWidget(button("Copy command", icon_name="copy", on_click=lambda: QGuiApplication.clipboard().setText(self.cmd.toPlainText())), 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g_cmd)
        if self.profile.notes:
            bl.addWidget(label("Notes: " + " ".join(self.profile.notes), muted=True, wrap=True))
        bl.addStretch(1)
        self.problems = label("", wrap=True)
        self.lay.addWidget(self.problems)
        self.banner = Banner()
        self.lay.addWidget(self.banner)
        self._finish_buttons(enabled=case is not None)
        self._refresh_preview()

    def _finish_buttons(self, enabled: bool) -> None:
        bb = QDialogButtonBox()
        self.run_btn = button("Run analysis", icon_name="play", primary=True)
        self.run_btn.setEnabled(enabled)
        if not enabled and self.ctx.case is None:
            self.run_btn.setToolTip("Evidence processing needs an open case.")
        check = button("Check", icon_name="check", on_click=self._preflight)
        bb.addButton(check, QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton(self.run_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        self.run_btn.clicked.connect(self._run)
        self.lay.addWidget(bb)

    def _option_widget(self, opt, value) -> QWidget:
        if opt.kind == OptionKind.SWITCH or (opt.kind == OptionKind.DEPENDENCY and value is True):
            w = QCheckBox("Enabled" if opt.kind == OptionKind.SWITCH else f"Use the installation's {opt.dependency_glob} folder")
            w.setChecked(bool(value))
            w.toggled.connect(self._refresh_preview)
            return w
        if opt.kind in (OptionKind.BOOL, OptionKind.CHOICE):
            w = QComboBox()
            w.addItem("Tool default", None)
            for c in opt.choices:
                w.addItem(c, c)
            if value is not None:
                w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(self._refresh_preview)
            return w
        if opt.kind == OptionKind.INT:
            w = QSpinBox()
            w.setRange((opt.minimum or 0) - 1, opt.maximum or 2_000_000_000)
            w.setSpecialValueText("Tool default")
            w.setValue(int(value) if value not in (None, "") else w.minimum())
            w.valueChanged.connect(self._refresh_preview)
            return w
        if opt.kind == OptionKind.DEPENDENCY:
            w = QComboBox()
            if self.inst:
                base = Path(self.inst.install_dir)
                folder = base / (opt.dependency_glob or "")
                if folder.is_dir():
                    for p in sorted(folder.glob("*.reb")) or [folder]:
                        w.addItem(str(p.relative_to(base)).replace("\\", "/"), str(p.relative_to(base)).replace("\\", "/"))
            if value:
                i = w.findData(value)
                w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(self._refresh_preview)
            return w
        w = QLineEdit("" if value in (None, True) else str(value))
        if opt.kind == OptionKind.FILE:
            w.setPlaceholderText("Path to an analyst-supplied file")
        if opt.pattern_hint:
            w.setPlaceholderText(opt.pattern_hint)
        w.textChanged.connect(self._refresh_preview)
        return w

    def _maybe_browse(self, cb: QComboBox, role) -> None:
        if cb.currentData() != "browse":
            return
        if role.is_dir:
            p = QFileDialog.getExistingDirectory(self, role.label)
        else:
            p, _ = QFileDialog.getOpenFileName(self, role.label)
        if p:
            ref = EvidenceRef("path", p, p, None, is_dir=role.is_dir)
            cb.insertItem(1, f"{p}  [not registered: no hash recorded]", ref)
            cb.setCurrentIndex(1)
        else:
            cb.setCurrentIndex(0)

    def collect(self) -> Request:
        inputs = {}
        for k, cb in self.inputs.items():
            d = cb.currentData()
            if isinstance(d, EvidenceRef):
                inputs[k] = [d]
        opts = {}
        for opt in self.profile.options:
            w = self.widgets[opt.key]
            if isinstance(w, QCheckBox):
                v = w.isChecked()
                opts[opt.key] = True if v else (False if opt.kind == OptionKind.SWITCH else None)
            elif isinstance(w, QComboBox):
                opts[opt.key] = w.currentData()
            elif isinstance(w, QSpinBox):
                opts[opt.key] = None if w.value() == w.minimum() else w.value()
            else:
                opts[opt.key] = w.text().strip() or None
        return Request(inputs, {k: v for k, v in opts.items() if v is not None}, self.extra.text())

    def _refresh_preview(self) -> None:
        if self.profile is None or self.inst is None:
            return
        req = self.collect()
        from ..domain.errors import Preflight
        try:
            extra = self.adapter.parse_extra_args(self.profile, req.extra_args_text, Preflight()) if req.extra_args_text.strip() else []
            deps, _snap, _pf = self.adapter.resolve_dependencies(self.inst, self.profile, req.options)
            job = (self.ctx.case.root if self.ctx.case else Path("<case>")) / "jobs" / "<job-id>"
            v = ValidatedRequest(self.profile, self.inst, req.inputs, req.options, extra, job, job / "outputs", job / "temp", deps)
            self.cmd.setPlainText(self.adapter.build_invocation(v, create_dirs=False).display())
        except Exception as exc:
            self.cmd.setPlainText(f"(preview unavailable: {exc})")

    def _preflight(self) -> bool:
        pf, _ = self.ctx.jobs.preflight_parser(self.ctx.case, self.tool_id, self.collect())
        if not pf.problems:
            self.problems.setText("Preflight passed: inputs, options, tool and runtime look correct.")
            return True
        lines = [("Blocking: " if p.blocking else "Warning: ") + p.message + (f" - {p.next_action}" if p.next_action else "") for p in pf.problems]
        self.problems.setText("\n".join(lines))
        return pf.ok

    def _run(self) -> None:
        if not self._preflight():
            return
        jid, pf = self.ctx.jobs.submit_parser(self.ctx.case, self.tool_id, self.collect(), title=f"{self.tool.name} (configured)")
        if jid is None:
            self.problems.setText("\n".join(p.message for p in pf.blockers))
            return
        self.submitted_job = jid
        self.accept()


# -- command palette -------------------------------------------------------------------------

class CommandPalette(QDialog):
    def __init__(self, actions: list[tuple[str, str, callable]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setMinimumSize(560, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type a command (for example: new case, add evidence, timeline)")
        self.list = QListWidget()
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        self.actions = actions
        for name, hint, fn in actions:
            it = QListWidgetItem(f"{name}    {hint}")
            it.setData(Qt.ItemDataRole.UserRole, fn)
            self.list.addItem(it)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda it: self._run(it))
        self.list.setCurrentRow(0)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self.edit, activated=lambda: self._move(1))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self.edit, activated=lambda: self._move(-1))

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = not all(w in it.text().lower() for w in words)
            it.setHidden(hide)
            if not hide and first is None:
                first = i
        if first is not None:
            self.list.setCurrentRow(first)

    def _move(self, d: int) -> None:
        r = self.list.currentRow()
        while 0 <= r + d < self.list.count():
            r += d
            if not self.list.item(r).isHidden():
                self.list.setCurrentRow(r)
                return

    def _run_current(self) -> None:
        it = self.list.currentItem()
        if it and not it.isHidden():
            self._run(it)

    def _run(self, it) -> None:
        fn = it.data(Qt.ItemDataRole.UserRole)
        self.accept()
        QTimer.singleShot(0, fn)


# -- first run -------------------------------------------------------------------------------

class FirstRunDialog(Dialog):
    def __init__(self, ctx, parent=None):
        super().__init__("Welcome", parent, 640)
        self.choice = "later"
        self.lay.addWidget(label("Set up the Eric Zimmerman tools. Nothing is downloaded unless you choose it; you can change this later on the Tools page.", wrap=True))
        for key, title, text, ic in (
            ("import", "Import an existing EZ Tools folder", "Use a folder you already have (for example from Get-ZimmermanTools or an offline bundle). Files are copied into the workbench's tool store.", "import"),
            ("download", "Download supported tools", "Fetch the official .NET 10 builds from download.ericzimmermanstools.com (HTTPS, reviewed origin). KAPE is never downloaded.", "download"),
            ("later", "Continue and configure tools later", "Cases, evidence and imported outputs work without tools; parsers show 'Needs setup' until installed.", "chevron-right"),
        ):
            b = button(title, icon_name=ic, primary=key == "import")
            b.setToolTip(text)
            b.setMinimumHeight(44)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.lay.addWidget(b)
            self.lay.addWidget(label(text, muted=True, wrap=True))
        self.lay.addWidget(label(f"Data folder: {ctx.paths.root}" + (f"\n{ctx.paths.portable_fallback_reason}" if ctx.paths.portable_fallback_reason else ""), muted=True, wrap=True))

    def _pick(self, k: str) -> None:
        self.choice = k
        self.accept()


# -- bounded text viewer ---------------------------------------------------------------------

class TextViewer(Dialog):
    def __init__(self, path: str, title: str = "Text viewer", parent=None):
        super().__init__(title, parent, 900)
        self.path = Path(path)
        self.setMinimumHeight(600)
        row = QHBoxLayout()
        self.find = QLineEdit()
        self.find.setPlaceholderText("Search the whole file (streamed)")
        row.addWidget(self.find, 1)
        row.addWidget(button("Search", icon_name="search", on_click=self._search))
        self.lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(mono_font(9))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.lay.addWidget(self.text, 1)
        self.info = label("", muted=True, wrap=True)
        self.lay.addWidget(self.info)
        try:
            t, trunc = text_preview(self.path)
            self.text.setPlainText(t)
            self.info.setText(f"{self.path} - {self.path.stat().st_size:,} bytes" + (" - showing the first 2 MB; use Search for the rest." if trunc else ""))
        except OSError as exc:
            self.info.setText(f"Cannot read {self.path}: {exc}")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)

    def _search(self) -> None:
        if not self.find.text().strip():
            return
        hits = stream_text_search(self.path, self.find.text().strip())
        self.text.setPlainText("\n".join(f"{n:>9}: {line}" for n, line in hits) or "No matches.")
        self.info.setText(f"{len(hits)} matching lines" + (" (first 500 shown)" if len(hits) >= 500 else "") + " - line numbers refer to the original file.")


# -- add to finding --------------------------------------------------------------------------

class AddToFindingDialog(Dialog):
    def __init__(self, findings: list[dict], parent=None):
        super().__init__("Add to finding", parent, 520)
        self.choice: str | None = None
        self.new_title = QLineEdit()
        self.new_title.setPlaceholderText("Title for a new finding")
        self.list = QListWidget()
        for f in findings:
            it = QListWidgetItem(f"{f['title']}  ({f['status']})")
            it.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.list.addItem(it)
        self.lay.addWidget(label("Existing findings", muted=True))
        self.lay.addWidget(self.list, 1)
        self.lay.addWidget(label("or create a new one", muted=True))
        self.lay.addWidget(self.new_title)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)

    def _ok(self) -> None:
        it = self.list.currentItem()
        if self.new_title.text().strip():
            self.choice = "new:" + self.new_title.text().strip()
        elif it:
            self.choice = it.data(Qt.ItemDataRole.UserRole)
        if self.choice:
            self.accept()

