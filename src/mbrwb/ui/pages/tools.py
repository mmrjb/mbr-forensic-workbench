"""Tools: catalogue grid/list, filters, favourites, details, import/download/update, versions,
verified-option evidence, GUI launch, maintenance and KAPE."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...adapters.registry import get_adapter
from ...catalogue.catalogue import parse_catalogue_page, reconcile
from ...domain.enums import IntegrationLevel, ToolKind
from ...storage.db import utcnow
from ...tools.download import DownloadError, fetch
from ...tools.inventory import ToolStatus, all_statuses
from ...tools.launcher import LaunchError, can_launch_here, launch_gui
from ..dialogs import ConfigureToolDialog, Dialog
from ..theme import icon, mono_font, status_color, tokens
from ..widgets.common import KeyValueGrid, Panel, PathLabel, Pill, button, label, tool_button
from .base import Page

ROLE_STATUS = Qt.ItemDataRole.UserRole + 1


class TileDelegate(QStyledItemDelegate):
    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def sizeHint(self, option, index):
        return QSize(236, 132) if self.page.grid_mode else QSize(600, 44)

    def paint(self, p: QPainter, option, index):
        st: ToolStatus = index.data(ROLE_STATUS)
        if st is None:
            return
        t = tokens()
        r = option.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = option.state & QStyle.StateFlag.State_Selected
        hov = option.state & QStyle.StateFlag.State_MouseOver
        p.setPen(QPen(QColor(t.accent if sel else (t.text3 if hov else t.border)), 1.2))
        p.setBrush(QColor(t.selection if sel else t.card))
        p.drawRoundedRect(r, 10, 10)
        col = status_color(st.level)
        name_font = QFont(option.font)
        name_font.setBold(True)
        fav = st.tool.id in self.page.favorites
        if self.page.grid_mode:
            p.setFont(name_font)
            p.setPen(QColor(t.text))
            p.drawText(r.adjusted(12, 10, -30, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, st.tool.name)
            if fav:
                icon("star", t.warning, 14).paint(p, QRect(r.right() - 24, r.top() + 10, 14, 14))
            p.setFont(option.font)
            p.setPen(QColor(t.text2))
            fm = p.fontMetrics()
            desc = fm.elidedText(st.tool.description, Qt.TextElideMode.ElideRight, r.width() * 2 - 60)
            p.drawText(r.adjusted(12, 32, -12, -52), Qt.TextFlag.TextWordWrap, desc)
            ver = st.installation.version if st.installation else "not installed"
            p.setPen(QColor(t.text3))
            p.drawText(r.adjusted(12, 0, -12, -32), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       f"{st.tool.kind.value.upper()}  \u00b7  {ver}")
            icon({"ok": "check", "warn": "warning", "error": "error"}.get(st.level, "info"), col, 14).paint(p, QRect(r.left() + 12, r.bottom() - 25, 14, 14))
            p.setPen(QColor(col))
            p.drawText(r.adjusted(32, 0, -12, -10), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       fm.elidedText(st.label, Qt.TextElideMode.ElideRight, r.width() - 46))
        else:
            p.setFont(name_font)
            p.setPen(QColor(t.text))
            p.drawText(r.adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, ("★ " if fav else "") + st.tool.name)
            p.setFont(option.font)
            p.setPen(QColor(t.text2))
            p.drawText(r.adjusted(220, 0, -380, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(st.tool.description, Qt.TextElideMode.ElideRight, max(50, r.width() - 610)))
            p.setPen(QColor(t.text3))
            p.drawText(QRect(r.right() - 370, r.top(), 140, r.height()), Qt.AlignmentFlag.AlignVCenter, st.installation.version if st.installation else "not installed")
            p.setPen(QColor(col))
            p.drawText(QRect(r.right() - 225, r.top(), 215, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, st.label)
        p.restore()


class ToolsPage(Page):
    case_scoped = False

    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Tools", "The Eric Zimmerman tool collection. Catalogue, installation, execution and output normalisation are tracked separately.")
        self.grid_mode = True
        self.statuses: list[ToolStatus] = []
        self.favorites: set[str] = {r[0] for r in ctx.app_db.query("SELECT tool_id FROM favorites")}
        self._cancel = threading.Event()
        for b in (button("Import folder", icon_name="folder", on_click=self.import_folder, tooltip="Import an existing EZ Tools folder (copied into the tool store)"),
                  button("Import archive", icon_name="import", on_click=self.import_archive, tooltip="Import an official tool .zip or an offline bundle"),
                  button("Install all eligible", icon_name="download", on_click=self.install_all_eligible),
                  button("Check for updates", icon_name="refresh", on_click=self.check_updates, tooltip="Fetches the official catalogue page (user-initiated network request)")):
            self.header.add_action(b)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools")
        self.search.textChanged.connect(self._filter)
        self.cat = QComboBox()
        self.cat.addItem("All categories", None)
        for c in sorted({t.category for t in ctx.catalogue.tools.values()}):
            self.cat.addItem(c, c)
        self.cat.currentIndexChanged.connect(self._filter)
        self.stat = QComboBox()
        for text, val in (("Any status", None), ("Ready", "ready"), ("Needs setup", "setup"), ("Installed", "installed"), ("Favorites", "fav"),
                          ("Command line", "cli"), ("GUI", "gui")):
            self.stat.addItem(text, val)
        self.stat.currentIndexChanged.connect(self._filter)
        self.grid_btn = tool_button("grid", "Grid view", lambda: self._set_mode(True), checkable=True)
        self.list_btn = tool_button("list", "List view", lambda: self._set_mode(False), checkable=True)
        self.grid_btn.setChecked(True)
        bar.addWidget(self.search, 2)
        bar.addWidget(self.cat, 1)
        bar.addWidget(self.stat, 1)
        bar.addWidget(self.grid_btn)
        bar.addWidget(self.list_btn)
        self.body_layout.addLayout(bar)
        self.coverage = label("", muted=True, wrap=True)
        self.body_layout.addWidget(self.coverage)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setItemDelegate(TileDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(2)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(lambda *_: self._details())
        self._set_mode(True)
        split.addWidget(self.list)
        # details
        self.detail = Panel()
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setWidget(self.detail)
        dscroll.setMinimumWidth(400)
        d = self.detail.lay
        hrow = QHBoxLayout()
        self.d_name = label("Select a tool", obj="PageTitle")
        self.fav_btn = tool_button("star", "Favorite", self._toggle_fav, checkable=True)
        hrow.addWidget(self.d_name, 1)
        hrow.addWidget(self.fav_btn)
        d.addLayout(hrow)
        self.d_desc = label("", muted=True, wrap=True)
        d.addWidget(self.d_desc)
        self.d_pill = Pill("", "muted")
        d.addWidget(self.d_pill)
        self.primary = button("", primary=True, on_click=self._primary)
        d.addWidget(self.primary, 0, Qt.AlignmentFlag.AlignLeft)
        self.d_problems = label("", wrap=True)
        d.addWidget(self.d_problems)
        self.tabs = QTabWidget()
        self.kv = KeyValueGrid()
        w1 = QWidget()
        QVBoxLayout(w1).addWidget(self.kv)
        self.tabs.addTab(w1, "Details")
        vw = QWidget()
        vl = QVBoxLayout(vw)
        self.versions = QListWidget()
        vl.addWidget(self.versions, 1)
        vr = QHBoxLayout()
        vr.addWidget(button("Use selected version", icon_name="check", on_click=self._select_version))
        vr.addWidget(button("Roll back", icon_name="refresh", on_click=self._rollback))
        vr.addWidget(button("Re-check signature", icon_name="shield", on_click=self._recheck_sig))
        vl.addLayout(vr)
        vl.addWidget(label("Versions in use by queued or running jobs are never removed or changed.", muted=True, wrap=True))
        self.tabs.addTab(vw, "Versions")
        self.help_text = QPlainTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setFont(mono_font(9))
        hw = QWidget()
        hl = QVBoxLayout(hw)
        hl.addWidget(self.help_text, 1)
        self.verify_btn = button("Verify this version (capture help output)", icon_name="terminal", on_click=self._verify_version,
                                 tooltip="Runs the selected trusted executable with --help (Windows) and compares its options with the adapter profile.")
        hl.addWidget(self.verify_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(hw, "Options evidence")
        self.diag = QPlainTextEdit()
        self.diag.setReadOnly(True)
        self.diag.setFont(mono_font(9))
        self.tabs.addTab(self.diag, "Diagnostics")
        d.addWidget(self.tabs, 1)
        lrow = QHBoxLayout()
        lrow.addWidget(button("Documentation", icon_name="external", on_click=self._docs))
        lrow.addWidget(button("Open tool folder", icon_name="folder", on_click=self._tool_folder))
        lrow.addStretch(1)
        d.addLayout(lrow)
        split.addWidget(dscroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.body_layout.addWidget(split, 1)
        self.progress = label("", muted=True)
        self.body_layout.addWidget(self.progress)
        ctx.toolsChanged.connect(self.refresh)

    # -- data ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        ctx = self.ctx
        self.ctx.run_bg(lambda: all_statuses(ctx.catalogue, ctx.tools, ctx.runtimes(refresh=True)), on_done=self._fill)

    def _fill(self, statuses: list[ToolStatus]) -> None:
        cur = self._current().tool.id if self._current() else None
        self.statuses = statuses
        self.list.clear()
        for st in sorted(statuses, key=lambda s: (s.tool.id not in self.favorites, s.tool.category, s.tool.name.lower())):
            it = QListWidgetItem()
            it.setData(ROLE_STATUS, st)
            it.setData(Qt.ItemDataRole.AccessibleTextRole, f"{st.tool.name}: {st.label}")
            it.setToolTip(f"{st.tool.name} - {st.tool.description}\n{st.label}")
            self.list.addItem(it)
            if st.tool.id == cur:
                self.list.setCurrentItem(it)
        cli = [s for s in statuses if s.tool.kind == ToolKind.CLI]
        gui = [s for s in statuses if s.tool.kind == ToolKind.GUI]
        self.coverage.setText(
            f"Catalogue: {len(statuses)} entries ({len(cli)} command line, {len(gui)} GUI, {len(statuses) - len(cli) - len(gui)} other). "
            f"Installed: {sum(1 for s in statuses if s.installation)}. Ready here: {sum(1 for s in statuses if s.readiness in ('ready', 'launch_only'))}. "
            f"Adapters: options statically verified against the 2026.10 builds; real Windows execution is recorded per job." +
            ("" if sys.platform == "win32" else " This machine is not Windows: tools can be managed but not run."))
        self._filter()
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)

    def _filter(self) -> None:
        text = self.search.text().lower().strip()
        cat = self.cat.currentData()
        stf = self.stat.currentData()
        for i in range(self.list.count()):
            it = self.list.item(i)
            st: ToolStatus = it.data(ROLE_STATUS)
            show = (not text or text in (st.tool.name + st.tool.description + st.tool.category).lower()) and (cat is None or st.tool.category == cat)
            if stf == "ready":
                show &= st.readiness in ("ready", "launch_only")
            elif stf == "setup":
                show &= st.readiness in ("needs_setup", "unsupported_version", "manual_import")
            elif stf == "installed":
                show &= st.installation is not None
            elif stf == "fav":
                show &= st.tool.id in self.favorites
            elif stf == "cli":
                show &= st.tool.kind == ToolKind.CLI
            elif stf == "gui":
                show &= st.tool.kind == ToolKind.GUI
            it.setHidden(not show)

    def _set_mode(self, grid: bool) -> None:
        self.grid_mode = grid
        self.grid_btn.setChecked(grid)
        self.list_btn.setChecked(not grid)
        self.list.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWrapping(grid)
        self.list.setGridSize(QSize(244, 140) if grid else QSize())
        self.list.doItemsLayout()

    def _current(self) -> ToolStatus | None:
        it = self.list.currentItem()
        return it.data(ROLE_STATUS) if it else None

    def open_with(self, arg) -> None:
        if isinstance(arg, dict) and arg.get("tool_id"):
            for i in range(self.list.count()):
                if self.list.item(i).data(ROLE_STATUS).tool.id == arg["tool_id"]:
                    self.list.setCurrentRow(i)

    # -- details ------------------------------------------------------------------------------
    def _details(self) -> None:
        st = self._current()
        if st is None:
            return
        t, inst = st.tool, st.installation
        self.d_name.setText(t.name)
        self.d_desc.setText(t.description)
        self.d_pill.set(st.label, st.level)
        self.fav_btn.setChecked(t.id in self.favorites)
        self.d_problems.setText("\n".join(st.details))
        action = {"configure": "Configure analysis", "open": "Open original tool", "set_up": "Set up", "manage": "Manage"}[st.primary_action]
        if t.id == "get-zimmermantools" and inst:
            action = "Run maintenance update"
        if t.id == "kape":
            action = "Select KAPE folder" if not inst else "Plan KAPE run"
        self.primary.setText(action)
        self.primary.setIcon(icon({"configure": "play", "open": "window", "set_up": "download", "manage": "settings"}[st.primary_action], tokens().accent_text))
        self.kv.clear()
        self.kv.add("Category", t.category)
        self.kv.add("Type", {"cli": "Command-line parser", "gui": "Original GUI program (opens in its own window)", "script": "PowerShell maintenance script",
                             "bundle": "Collection/processing framework"}[t.kind.value])
        self.kv.add("Integration", {"cli_adapter": "Form + job adapter + output import", "gui_launch": "Launch only (no file-open argument verified)",
                                    "maintenance_script": "Separate maintenance operation", "specialist": "Specialist utility (never automatic triage)",
                                    "restricted_optional": "Optional, operator-supplied", "pending_review": "Pending review"}[t.integration.value])
        self.kv.add("Installed version", f"{inst.version} ({inst.flavor})" if inst else "not installed")
        self.kv.add("Catalogue version", ", ".join(f"{k}: {v.catalogue_version}" for k, v in t.downloads.items()) or "-")
        if inst:
            self.kv.add("Executable", "", widget=PathLabel(inst.executable))
            self.kv.add("SHA-256", inst.exe_sha256, mono=True, tooltip="Identifies content; does not by itself prove authenticity.")
            self.kv.add("Signature", f"{inst.signature_state}")
            self.kv.add("Runtime", st.runtime_message or "-")
        if st.verification:
            self.kv.add("Adapter", st.verification)
        self.kv.add("Inputs", ", ".join(t.input_types) or "-")
        self.kv.add("Outputs", ", ".join(t.output_formats) or "-")
        if t.sidecars:
            self.kv.add("Sidecars", "; ".join(t.sidecars))
        self.kv.add("Privileges", t.privileges)
        self.kv.add("Networking", t.network or "None")
        self.kv.add("License", t.license_source)
        self.kv.add("Redistribution", t.redistribution)
        if t.notes:
            self.kv.add("Notes", t.notes)
        self.versions.clear()
        sel = inst.id if inst else None
        for v in self.ctx.tools.installations(t.id):
            row = self.ctx.tools.installation_row(v.id)
            it = QListWidgetItem(f"{'● ' if v.id == sel else '   '}{v.version} ({v.flavor}) - {row['source']} - installed {row['installed_utc'][:19]}"
                                 + ("  [in use by a job]" if self.ctx.tools.in_use(v.id) else ""))
            it.setData(Qt.ItemDataRole.UserRole, v.id)
            self.versions.addItem(it)
        self._options_evidence(st)
        diag = {"tool_id": t.id, "adapter": t.adapter, "repository": t.repository, "repository_status": t.repository_status,
                "downloads": {k: v.url for k, v in t.downloads.items()}, "installation": inst.as_dict() if inst else None, "details": st.details}
        self.diag.setPlainText(json.dumps(diag, indent=2, ensure_ascii=False))

    def _options_evidence(self, st: ToolStatus) -> None:
        t = st.tool
        lines = []
        rec = self.ctx.catalogue.static_evidence.get(t.id)
        adapter = get_adapter(t.adapter or t.id) if t.kind == ToolKind.CLI or t.id == "kape" else None
        verified = self.ctx.app_db.query("SELECT * FROM verification_records WHERE tool_id=? ORDER BY checked_utc DESC LIMIT 1", (t.id,))
        if verified:
            v = verified[0]
            lines += [f"Help output captured {v['checked_utc']} from executable {v['exe_sha256'][:16]}... (result: {v['result']})", "", v["help_text"] or "", ""]
        if rec:
            lines += [f"Static evidence: {rec['main_member']} {rec.get('product_version')}", f"  SHA-256 {rec['main_member_sha256']}", f"  archive {rec['archive_name']} SHA-256 {rec['archive_sha256']}",
                      "  Method: read-only inspection; the binary was not executed.", ""]
            for k, v in (rec.get("static_options") or {}).items():
                lines.append(f"  {k:<10} {v[:150]}")
        if adapter:
            for p in adapter.profiles:
                lines += ["", f"Adapter profile {p.profile_id} (version family {p.version_family}): {p.verification.label}", f"  {p.evidence}",
                          f"  Not offered: {', '.join(p.not_offered) or '-'}"] + [f"  Note: {n}" for n in p.notes]
        self.help_text.setPlainText("\n".join(lines) or "No option evidence recorded for this entry.")
        self.verify_btn.setEnabled(st.installation is not None and t.kind == ToolKind.CLI)

    # -- actions --------------------------------------------------------------------------------
    def _primary(self) -> None:
        st = self._current()
        if st is None:
            return
        t = st.tool
        if t.id == "kape":
            return self._kape()
        if t.id == "get-zimmermantools" and st.installation:
            return self._run_gzt(st)
        if st.primary_action == "set_up":
            return self._setup(st)
        if st.primary_action == "configure":
            d = ConfigureToolDialog(self.ctx, t.id, parent=self)
            if d.exec() and d.submitted_job:
                self.win.go("jobs", {"job_id": d.submitted_job})
            return
        if st.primary_action == "open":
            if not can_launch_here():
                self.win.banner.show_message("warn", f"{t.name} is a Windows program and cannot be opened on this machine.")
                return
            try:
                sid = launch_gui(self.ctx.app_db, t, st.installation, case=self.ctx.case, purpose="opened from Tools")
                self.win.banner.show_message("info", f"{t.name} opened in its own window (launch session {sid}). Opening a tool is not an analysis; import any files you export from it.")
            except LaunchError as exc:
                self.win.banner.show_message("error", str(exc))

    def _setup(self, st: ToolStatus) -> None:
        t = st.tool
        if t.downloadable and not self.ctx.settings.network.offline_mode:
            r = QMessageBox.question(self, f"Set up {t.name}", f"Download {t.name} from {next(iter(t.downloads.values())).url}?\n\n"
                                     "Choose No to import a local archive or folder instead.")
            if r == QMessageBox.StandardButton.Yes:
                return self.download([t.id])
        self.import_archive()

    def _toggle_fav(self) -> None:
        st = self._current()
        if not st:
            return
        if st.tool.id in self.favorites:
            self.favorites.discard(st.tool.id)
            self.ctx.app_db.execute("DELETE FROM favorites WHERE tool_id=?", (st.tool.id,))
        else:
            self.favorites.add(st.tool.id)
            self.ctx.app_db.execute("INSERT OR IGNORE INTO favorites(tool_id) VALUES (?)", (st.tool.id,))
        self.list.viewport().update()

    def _docs(self) -> None:
        st = self._current()
        if st:
            QDesktopServices.openUrl(QUrl(st.tool.documentation))

    def _tool_folder(self) -> None:
        st = self._current()
        if st and st.installation:
            QDesktopServices.openUrl(QUrl.fromLocalFile(st.installation.install_dir))

    def _select_version(self) -> None:
        it = self.versions.currentItem()
        if it:
            try:
                self.ctx.tools.select(it.data(Qt.ItemDataRole.UserRole))
                self.ctx.toolsChanged.emit()
            except Exception as exc:
                self.win.banner.show_message("error", str(exc))

    def _rollback(self) -> None:
        st = self._current()
        if st:
            inst = self.ctx.tools.rollback(st.tool.id)
            self.win.banner.show_message("ok" if inst else "warn", f"Now using {inst.version}." if inst else "No earlier retained version is available.")
            self.ctx.toolsChanged.emit()

    def _recheck_sig(self) -> None:
        st = self._current()
        if st and st.installation:
            iid = st.installation.id
            self.ctx.run_bg(lambda: self.ctx.tools.recheck_signature(iid), on_done=lambda _: self.ctx.toolsChanged.emit())

    # -- imports / downloads -------------------------------------------------------------------
    def import_paths(self, paths: list[str]) -> None:
        for p in paths:
            if p.lower().endswith(".zip"):
                self._run_import(lambda p=p: self.ctx.tools.install_archive(Path(p)), f"Importing {Path(p).name}")
            else:
                self._run_import(lambda p=p: self.ctx.tools.import_folder(Path(p)), f"Importing tools from {p}")

    def import_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Existing EZ Tools folder", self.ctx.settings.recent_paths.get("tools_import", ""))
        if d:
            self.ctx.settings_store.remember_path("tools_import", d)
            self._run_import(lambda: self.ctx.tools.import_folder(Path(d)), f"Importing tools from {d}")

    def import_archive(self) -> None:
        fs, _ = QFileDialog.getOpenFileNames(self, "Tool archives or offline bundle", self.ctx.settings.recent_paths.get("tools_import", ""), "Zip archives (*.zip)")
        for f in fs:
            self._run_import(lambda f=f: self.ctx.tools.install_archive(Path(f)), f"Importing {Path(f).name}")

    def _run_import(self, fn, what: str) -> None:
        self.progress.setText(what + " (background)...")

        def done(res):
            self.progress.setText("")
            ok = ", ".join(f"{i.tool_id} {i.version}" for i in res.installed)
            msg = (f"Installed: {ok}." if ok else "Nothing installed.") + (f" Problems: {'; '.join(res.errors)}" if res.errors else "")
            self.win.banner.show_message("ok" if res.installed and not res.errors else ("warn" if res.installed else "error"), msg)
            self.ctx.toolsChanged.emit()
        self.ctx.run_bg(fn, on_done=done, on_error=lambda m, tb: (self.progress.setText(""), self.win.banner.show_message("error", "Import failed.", "", tb)))

    def install_all_eligible(self) -> None:
        cat = self.ctx.catalogue
        flavor = self.ctx.settings.tool_storage.preferred_flavor
        downloads, kept, manual = [], [], []
        for t in cat.tools.values():
            if t.integration == IntegrationLevel.RESTRICTED_OPTIONAL:
                manual.append(f"{t.name} (operator-supplied copy required; not downloaded)")
                continue
            src = t.downloads.get(flavor) or t.downloads.get("net10") or t.downloads.get("script")
            if src is None:
                manual.append(f"{t.name} (no reviewed download source)")
                continue
            inst = self.ctx.tools.selected(t.id)
            if inst and inst.version == src.catalogue_version:
                kept.append(f"{t.name} {inst.version}")
            else:
                downloads.append((t.id, src.url, src.catalogue_version))
        d = Dialog("Install all eligible tools", self, 640)
        d.lay.addWidget(label(f"Downloads ({len(downloads)}) from {', '.join(cat.allowed_download_origins)}:", obj="SectionTitle"))
        d.lay.addWidget(label(", ".join(cat.tools[x[0]].name + " " + x[2] for x in downloads) or "none", wrap=True))
        d.lay.addWidget(label(f"Existing installations retained ({len(kept)}):", obj="SectionTitle"))
        d.lay.addWidget(label(", ".join(kept) or "none", wrap=True, muted=True))
        d.lay.addWidget(label(f"Manual import / unavailable ({len(manual)}):", obj="SectionTitle"))
        d.lay.addWidget(label("; ".join(manual) or "none", wrap=True, muted=True))
        d.lay.addWidget(label("Restricted optional binaries such as KAPE are not included. Archives are verified after download (layout, version, Authenticode on Windows) "
                              "and installed through staging; current versions stay available.", wrap=True, muted=True))
        if self.ctx.settings.network.offline_mode:
            d.lay.addWidget(label("Offline mode is on: downloads are disabled. Use Import folder or Import archive.", wrap=True))
        row = QHBoxLayout()
        go = button("Download and install", icon_name="download", primary=True)
        go.setEnabled(bool(downloads) and not self.ctx.settings.network.offline_mode)
        go.clicked.connect(d.accept)
        cancel = button("Cancel", on_click=d.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(go)
        d.lay.addLayout(row)
        if d.exec():
            self.download([x[0] for x in downloads])

    def download(self, tool_ids: list[str]) -> None:
        if self.ctx.settings.network.offline_mode:
            self.win.banner.show_message("warn", "Offline mode is on; downloads are disabled.", "Turn it off in Settings > Network, or import a local archive.")
            return
        cat = self.ctx.catalogue
        flavor = self.ctx.settings.tool_storage.preferred_flavor
        net = self.ctx.settings.network
        self._cancel = threading.Event()
        dl_dir = self.ctx.paths.downloads

        def work():
            results = []
            for tid in tool_ids:
                if self._cancel.is_set():
                    results.append((tid, "cancelled"))
                    break
                t = cat.tools[tid]
                src = t.downloads.get(flavor) or t.downloads.get("net10") or t.downloads.get("script")
                if not cat.is_allowed_download(src.url):
                    results.append((tid, f"refused: {src.url} is not an allowlisted origin"))
                    continue
                try:
                    r = fetch(src.url, dl_dir / f"{tid}-{utcnow()[:10]}-{Path(src.url).name}", allowed_origins=cat.allowed_download_origins,
                              timeout=net.download_timeout_s, max_bytes=net.max_download_mb * 1024 * 1024, cancel=self._cancel)
                    res = self.ctx.tools.install_archive(r.path, tool_id=tid, source="download", source_detail=f"{src.url} (sha256 {r.sha256})")
                    results.append((tid, "installed" if res.installed else "; ".join(res.errors)))
                except DownloadError as exc:
                    results.append((tid, f"download failed: {exc}"))
            return results
        self.progress.setText(f"Downloading {len(tool_ids)} tool(s) in the background...")
        cancel_btn = button("Cancel downloads", icon_name="stop", on_click=self._cancel.set)
        self.header.add_action(cancel_btn)

        def done(results):
            cancel_btn.deleteLater()
            self.progress.setText("")
            bad = [f"{cat.tools[t].name}: {s}" for t, s in results if s != "installed"]
            good = [cat.tools[t].name for t, s in results if s == "installed"]
            self.win.banner.show_message("ok" if not bad else "warn", f"Installed {len(good)} tool(s)." + (f" Problems: {'; '.join(bad)}" if bad else ""))
            self.ctx.toolsChanged.emit()
        self.ctx.run_bg(work, on_done=done, on_error=lambda m, tb: (cancel_btn.deleteLater(), self.win.banner.show_message("error", m, "", tb)))

    def check_updates(self) -> None:
        if self.ctx.settings.network.offline_mode:
            self.win.banner.show_message("warn", "Offline mode is on; the catalogue check is disabled.")
            return
        cat = self.ctx.catalogue
        dest = self.ctx.paths.downloads / "catalogue.html"

        def work():
            r = fetch("https://ericzimmerman.github.io/", dest, allowed_origins=cat.allowed_catalogue_origins, timeout=self.ctx.settings.network.download_timeout_s, max_bytes=5 * 1024 * 1024)
            page = r.path.read_text("utf-8", errors="replace")
            items = reconcile(cat, parse_catalogue_page(page))
            from ...storage.case_store import new_id
            self.ctx.app_db.execute("INSERT INTO upstream_checks(id, checked_utc, source_url, page_sha256, entries_json) VALUES (?,?,?,?,?)",
                                    (new_id("chk"), utcnow(), r.final_url, r.sha256, json.dumps([i.__dict__ for i in items])))
            return items
        self.progress.setText("Checking the official catalogue...")

        def done(items):
            self.progress.setText("")
            changed = [i for i in items if i.status != "known"]
            d = Dialog("Catalogue check", self, 680)
            d.lay.addWidget(label(f"{len(items)} entries compared with the reviewed manifest ({cat.reviewed_utc[:10]}). The page is treated as data only.", wrap=True))
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setPlainText("\n".join(f"[{i.status}] {i.name}: {i.detail}" for i in changed) or "No differences: every entry matches the reviewed manifest.")
            d.lay.addWidget(txt, 1)
            d.lay.addWidget(label("New upstream entries are shown as 'integration pending review' and are never downloaded or executed automatically. "
                                  "Use Install all eligible to fetch newer builds of reviewed tools; a new version family needs adapter review before it is marked compatible.",
                                  wrap=True, muted=True))
            d.lay.addWidget(button("Close", on_click=d.accept), 0, Qt.AlignmentFlag.AlignRight)
            d.exec()
        self.ctx.run_bg(work, on_done=done, on_error=lambda m, tb: (self.progress.setText(""), self.win.banner.show_message("error", f"Catalogue check failed: {m}", "Check network access or use offline import.")))

    # -- maintenance / KAPE / verification -------------------------------------------------------
    def _run_gzt(self, st: ToolStatus) -> None:
        from ...adapters.maintenance import build_get_zimmermantools
        if self.ctx.case is None:
            self.win.banner.show_message("info", "Maintenance runs are recorded as jobs; open or create a case to keep the record (any case works).")
            return
        approved = {r[0] for r in self.ctx.app_db.query("SELECT exe_sha256 FROM verification_records WHERE tool_id='get-zimmermantools' AND method='script_review' AND result='compatible'")}
        dest = self.ctx.settings_store.tools_dir / ".gzt-download" / utcnow()[:19].replace(":", "")
        inv, pf = build_get_zimmermantools(Path(st.installation.executable), dest, "10", approved)
        if inv is None or not pf.ok:
            p = pf.blockers[0]
            self.win.banner.show_message("error", p.message, p.next_action, p.diagnostics)
            return
        jid = self.ctx.jobs.submit_invocation(self.ctx.case, kind="maintenance", tool_id="get-zimmermantools", inv=inv, title="Get-ZimmermanTools update (-NoUpdate)",
                                              installation_id=st.installation.id, exe_sha256=st.installation.exe_sha256)
        self.win.banner.show_message("info", f"Maintenance job {jid} queued. When it completes, import the destination folder to refresh the inventory: {dest}")

    def _kape(self) -> None:
        from ...adapters.kape import KAPE_LICENSE_NOTICE, KapeInstallation
        inst = self.ctx.tools.selected("kape")
        if inst is None:
            QMessageBox.information(self, "KAPE", KAPE_LICENSE_NOTICE + "\n\nSelect the folder of your own KAPE installation (kape.exe). It is referenced in place, not copied.")
            d = QFileDialog.getExistingDirectory(self, "KAPE folder")
            if not d:
                return
            try:
                self.ctx.tools.register_external("kape", Path(d))
                self.ctx.toolsChanged.emit()
            except Exception as exc:
                self.win.banner.show_message("error", str(exc))
            return
        k = KapeInstallation.inspect(Path(inst.install_dir))
        d = Dialog("KAPE", self, 760)
        d.lay.addWidget(label(KAPE_LICENSE_NOTICE, wrap=True))
        d.lay.addWidget(label(f"Targets (collection definitions): {len(k.targets)}  |  Modules (processing definitions): {len(k.modules)}. Definitions are read as data.", wrap=True))
        names = QLineEdit()
        names.setPlaceholderText("Module names to check, comma separated (e.g. EZParser)")
        out = QPlainTextEdit()
        out.setReadOnly(True)
        out.setFont(mono_font(9))

        def check():
            rows = k.module_dependencies([n.strip() for n in names.text().split(",") if n.strip()])
            out.setPlainText("\n".join(f"{r['module']}: {r['executable']} -> {r['status']}  {r.get('path') or ''}\n    {r.get('command_line', '')}" for r in rows))
        d.lay.addWidget(names)
        d.lay.addWidget(button("Check module dependencies", icon_name="check", on_click=check), 0, Qt.AlignmentFlag.AlignLeft)
        d.lay.addWidget(out, 1)
        d.lay.addWidget(label("Running KAPE from the workbench creates a case job with the reviewed switches only; review each module's command line above before running it. "
                              "A Targets collection is registered as a new evidence root with the job's provenance.", wrap=True, muted=True))
        from PySide6.QtWidgets import QCheckBox
        ack = QCheckBox("I confirm my use of KAPE is permitted by its license (not commercial use on third-party networks or paid engagements).")
        d.lay.addWidget(ack)
        row = QHBoxLayout()
        run = button("Configure KAPE run...", icon_name="play", primary=True)
        run.setEnabled(False)
        ack.toggled.connect(run.setEnabled)

        def configure():
            d.accept()
            if self.ctx.case is None:
                self.win.banner.show_message("info", "KAPE runs are recorded as case jobs; open or create a case first.")
                return
            cd = ConfigureToolDialog(self.ctx, "kape", parent=self)
            if cd.exec() and cd.submitted_job:
                self.ctx.case.audit("kape.license_acknowledged", cd.submitted_job, {"text": ack.text()})
                self.win.go("jobs", {"job_id": cd.submitted_job})
        run.clicked.connect(configure)
        row.addStretch(1)
        row.addWidget(button("Close", on_click=d.accept))
        row.addWidget(run)
        d.lay.addLayout(row)
        d.exec()

    def _verify_version(self) -> None:
        st = self._current()
        if not st or not st.installation:
            return
        if sys.platform != "win32":
            self.win.banner.show_message("warn", "Help capture runs the tool and therefore needs Windows. The static option evidence above is what is available here.")
            return
        from ...tools.verify import capture_and_compare
        inst = st.installation
        self.ctx.run_bg(lambda: capture_and_compare(self.ctx.app_db, inst), on_done=lambda r: (self.win.banner.show_message("ok" if r["result"] == "compatible" else "warn",
                        f"Help capture: {r['result']}. {r['detail']}"), self.ctx.toolsChanged.emit()))

