"""Settings: grouped, validated on change, affecting future work only."""
from __future__ import annotations

import json
import platform
import sys
import zipfile
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...config.paths import is_writable_dir
from ...storage.db import utcnow
from ..dialogs import Dialog
from ..widgets.common import button, label
from .base import Page


class SettingsPage(Page):
    case_scoped = False

    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Settings", "Changes apply to future work only. Jobs and cases keep the settings they were created with.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(12)
        scroll.setWidget(inner)
        self.body_layout.addWidget(scroll, 1)
        s = ctx.settings
        # appearance
        g = QGroupBox("Appearance")
        f = QFormLayout(g)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(s.appearance.theme)
        self.theme.currentTextChanged.connect(lambda v: v != ctx.settings.appearance.theme and win.toggle_theme())
        f.addRow("Theme", self.theme)
        lay.addWidget(g)
        # tool storage
        g = QGroupBox("Tool storage")
        f = QFormLayout(g)
        self.tools_dir = QLineEdit(str(ctx.settings_store.tools_dir))
        self.tools_dir.setProperty("mono", True)
        r = QHBoxLayout()
        r.addWidget(self.tools_dir, 1)
        r.addWidget(button("Browse", icon_name="folder", on_click=lambda: self._pick(self.tools_dir)))
        f.addRow("Tools folder", r)
        self.retained = QSpinBox()
        self.retained.setRange(1, 10)
        self.retained.setValue(s.tool_storage.retained_versions)
        f.addRow("Previous versions kept", self.retained)
        self.flavor = QComboBox()
        self.flavor.addItems(["net10", "net4"])
        self.flavor.setCurrentText(s.tool_storage.preferred_flavor)
        self.flavor.setToolTip(".NET 10 builds are recommended by the catalogue; GUI tools and four CLI tools exist only as .NET 10.")
        f.addRow("Preferred build", self.flavor)
        lay.addWidget(g)
        # case defaults
        g = QGroupBox("Case defaults")
        f = QFormLayout(g)
        self.cases_dir = QLineEdit(str(ctx.settings_store.cases_dir))
        r = QHBoxLayout()
        r.addWidget(self.cases_dir, 1)
        r.addWidget(button("Browse", icon_name="folder", on_click=lambda: self._pick(self.cases_dir)))
        f.addRow("Cases folder", r)
        self.examiner = QLineEdit(s.case_defaults.examiner)
        f.addRow("Examiner", self.examiner)
        self.tz = QLineEdit(s.case_defaults.display_timezone)
        f.addRow("Display timezone (IANA)", self.tz)
        self.ev_mode = QComboBox()
        self.ev_mode.addItems(["in_place", "working_copy"])
        self.ev_mode.setCurrentText(s.case_defaults.evidence_mode)
        f.addRow("Evidence default", self.ev_mode)
        self.hash_reg = QCheckBox("Hash evidence after registration")
        self.hash_reg.setChecked(s.case_defaults.hash_on_register)
        f.addRow("", self.hash_reg)
        self.hash_limit = QSpinBox()
        self.hash_limit.setRange(0, 10_000_000)
        self.hash_limit.setSuffix(" MB (0 = no limit)")
        self.hash_limit.setValue(s.case_defaults.hash_size_limit_mb)
        f.addRow("Hash size limit", self.hash_limit)
        lay.addWidget(g)
        # execution
        g = QGroupBox("Execution")
        f = QFormLayout(g)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(s.execution.max_parallel_jobs)
        f.addRow("Parallel parser jobs", self.parallel)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 7 * 24 * 60)
        self.timeout.setSuffix(" min")
        self.timeout.setValue(s.execution.default_timeout_minutes)
        f.addRow("Job timeout", self.timeout)
        self.preview = QSpinBox()
        self.preview.setRange(100, 50000)
        self.preview.setValue(s.execution.log_preview_lines)
        f.addRow("Log preview lines", self.preview)
        lay.addWidget(g)
        # network
        g = QGroupBox("Network / offline mode")
        f = QFormLayout(g)
        self.offline = QCheckBox("Offline mode: disable all workbench-initiated network requests")
        self.offline.setChecked(s.network.offline_mode)
        f.addRow("", self.offline)
        f.addRow("", label("The workbench never checks for updates automatically. Offline mode does not and cannot sandbox separately launched original tools "
                           "(for example TimeApp's optional public IP lookup).", muted=True, wrap=True))
        self.dl_timeout = QSpinBox()
        self.dl_timeout.setRange(5, 600)
        self.dl_timeout.setSuffix(" s")
        self.dl_timeout.setValue(s.network.download_timeout_s)
        f.addRow("Download timeout", self.dl_timeout)
        lay.addWidget(g)
        # diagnostics
        g = QGroupBox("Diagnostics")
        f = QFormLayout(g)
        self.loglevel = QComboBox()
        self.loglevel.addItems(["INFO", "DEBUG", "WARNING"])
        self.loglevel.setCurrentText(s.diagnostics.log_level)
        f.addRow("Log level", self.loglevel)
        r = QHBoxLayout()
        r.addWidget(button("Export diagnostics bundle...", icon_name="download", on_click=self._diagnostics))
        r.addWidget(button("Export settings...", icon_name="download", on_click=self._export))
        r.addWidget(button("Import settings...", icon_name="import", on_click=self._import))
        r.addStretch(1)
        f.addRow("", r)
        f.addRow("Data folder", label(str(ctx.paths.root), mono=True, selectable=True))
        lay.addWidget(g)
        lay.addStretch(1)
        row = QHBoxLayout()
        self.msg = label("", wrap=True)
        row.addWidget(self.msg, 1)
        row.addWidget(button("Save settings", icon_name="check", primary=True, on_click=self._save))
        self.body_layout.addLayout(row)

    def _pick(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if d:
            ok, why = is_writable_dir(Path(d))
            if not ok:
                self.msg.setText(f"{d} is not writable ({why}). Choose another folder.")
                return
            edit.setText(d)

    def _save(self) -> None:
        for e, name in ((self.tools_dir, "Tools folder"), (self.cases_dir, "Cases folder")):
            ok, why = is_writable_dir(Path(e.text()))
            if not ok:
                self.msg.setText(f"{name} is not writable: {why}")
                return
        try:
            self.ctx.settings_store.update(
                tool_storage={"tools_dir": self.tools_dir.text(), "retained_versions": self.retained.value(), "preferred_flavor": self.flavor.currentText()},
                case_defaults={"cases_dir": self.cases_dir.text(), "examiner": self.examiner.text(), "display_timezone": self.tz.text().strip() or "UTC",
                               "evidence_mode": self.ev_mode.currentText(), "hash_on_register": self.hash_reg.isChecked(), "hash_size_limit_mb": self.hash_limit.value()},
                execution={"max_parallel_jobs": self.parallel.value(), "default_timeout_minutes": self.timeout.value(), "log_preview_lines": self.preview.value()},
                network={"offline_mode": self.offline.isChecked(), "download_timeout_s": self.dl_timeout.value()},
                diagnostics={"log_level": self.loglevel.currentText()},
            )
        except Exception as exc:
            self.msg.setText(f"Not saved: {exc}")
            return
        self.ctx.jobs.set_max_parallel(self.parallel.value())
        self.ctx.jobs.default_timeout_s = self.timeout.value() * 60
        self.ctx.tools.retained = self.retained.value()
        self.msg.setText("Saved. New settings apply to work started from now on; a changed tools folder applies after restart.")

    def _export(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, "Export settings", "workbench-settings.json", "JSON (*.json)")
        if f:
            Path(f).write_text(json.dumps(self.ctx.settings_store.export_dict(), indent=2), encoding="utf-8")
            self.msg.setText("Settings exported (no secrets; folder paths are marked as machine-specific).")

    def _import(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not f:
            return
        try:
            merged, notes = self.ctx.settings_store.preview_import(json.loads(Path(f).read_text("utf-8")))
        except Exception as exc:
            self.msg.setText(f"Settings file rejected: {exc}")
            return
        d = Dialog("Import settings - preview", self, 620)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(json.dumps(merged.model_dump(exclude={"recent_paths", "column_layouts"}), indent=2) + "\n\n" + "\n".join(notes))
        d.lay.addWidget(txt, 1)
        r = QHBoxLayout()
        r.addStretch(1)
        r.addWidget(button("Cancel", on_click=d.reject))
        r.addWidget(button("Apply", primary=True, on_click=d.accept))
        d.lay.addLayout(r)
        if d.exec():
            self.ctx.settings_store.apply_import(merged)
            self.msg.setText("Imported. Paths from the other machine were left unresolved.")

    def _diagnostics(self) -> None:
        logs = sorted(self.ctx.paths.logs.glob("*.log"))[-5:]
        contents = ["environment.json (OS, Python, Qt, workbench version, settings without paths)", "tool_inventory.json (tool ids, versions, hashes, signature states)"] + \
                   [f"logs/{p.name}" for p in logs]
        r = QMessageBox.question(self, "Diagnostics bundle",
                                 "The bundle will contain:\n- " + "\n- ".join(contents) +
                                 "\n\nIt does not contain evidence, case data, results or findings. Continue?")
        if r != QMessageBox.StandardButton.Yes:
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save diagnostics bundle", f"workbench-diagnostics-{utcnow()[:10]}.zip", "Zip (*.zip)")
        if not f:
            return
        from PySide6 import __version__ as pyside_version
        env = {"workbench": __version__, "python": sys.version, "platform": platform.platform(), "pyside6": pyside_version,
               "settings": {k: v for k, v in self.ctx.settings_store.export_dict().items() if k not in ("tool_storage", "case_defaults")}}
        inv = [{"tool_id": i.tool_id, "version": i.version, "flavor": i.flavor, "exe_sha256": i.exe_sha256, "signature": i.signature_state} for i in self.ctx.tools.installations()]
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("environment.json", json.dumps(env, indent=2))
            z.writestr("tool_inventory.json", json.dumps(inv, indent=2))
            for p in logs:
                z.write(p, f"logs/{p.name}")
        self.msg.setText(f"Diagnostics bundle saved to {f}.")
