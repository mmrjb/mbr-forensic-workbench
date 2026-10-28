"""Main window: sidebar navigation, top bar, pages, shortcuts, drag-and-drop, command palette."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.product import PRODUCT
from ..storage.case_store import CaseError, CaseStore, peek_case
from .context import AppContext
from .dialogs import AddEvidenceDialog, CommandPalette, FirstRunDialog, NewCaseDialog
from .theme import apply_theme, icon, tokens
from .widgets.common import Banner, button, label

PAGES = [
    ("overview", "Overview", "overview"),
    ("cases", "Cases", "cases"),
    ("evidence", "Evidence", "evidence"),
    ("workflows", "Workflows", "workflows"),
    ("tools", "Tools", "tools"),
    ("jobs", "Jobs", "jobs"),
    ("results", "Results", "results"),
    ("timeline", "Timeline", "timeline"),
    ("findings", "Findings & Reports", "findings"),
    ("settings", "Settings", "settings"),
]

HELP = {
    "overview": "Overview summarises the open case: evidence, processing coverage, jobs needing attention, datasets, timeline density and findings. Counts are records and events, never incidents or scores.",
    "cases": "Create, open, archive and forget cases. Removing a case from the list never deletes its folder or evidence.",
    "evidence": "Register files or collection folders, review the suggested artifact types (signature, structure or file name), correct host/user/volume, hash, and verify availability.",
    "workflows": "Pick a preset that answers an investigation question, review the plan (ready / needs setup / not present) and run it. Dry run saves the plan without running anything.",
    "tools": "Manage the Eric Zimmerman tool collection: import, download from the official source, select versions, view verified option evidence, configure CLI analyses and open GUI tools.",
    "jobs": "Every parser run with its state, logs, outputs and frozen provenance (executable hash, arguments, dependencies). Cancel stops the whole process tree.",
    "results": "Explore imported parser outputs. Search scope is explicit (this table or the whole case). Bookmarks refer to the original logical record, independent of sorting.",
    "timeline": "Events created only from reviewed output mappings. The time mode is always visible; unresolved timestamps are listed separately, never placed at epoch zero.",
    "findings": "Write findings that link immutable references to records and events, then generate a self-contained HTML or Markdown report with optional redaction.",
    "settings": "Appearance, tool storage, case defaults, execution, network/offline mode and diagnostics. Changes affect future work only.",
}


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(PRODUCT.name)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 660)
        self.setAcceptDrops(True)
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.setCentralWidget(root)
        # sidebar
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(224)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 16, 8, 12)
        sl.setSpacing(4)
        brand = QLabel(PRODUCT.short_name)
        brand.setObjectName("Brand")
        sub = QLabel("EZ Tools investigation workbench")
        sub.setObjectName("BrandSub")
        sl.addWidget(brand)
        sl.addWidget(sub)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setAccessibleName("Navigation")
        for key, text, ic in PAGES:
            it = QListWidgetItem(icon(ic), text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setToolTip(HELP[key])
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        self.side_jobs = label("", muted=True, wrap=True)
        sl.addWidget(self.side_jobs)
        h.addWidget(side)
        # main column
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        top = QFrame()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 10, 16, 10)
        tl.setSpacing(12)
        self.case_label = label("No case open", obj="SectionTitle")
        self.case_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tl.addWidget(self.case_label)
        self.demo_label = label("DEMO", muted=False)
        self.demo_label.setStyleSheet(f"color: {tokens().warning}; border: 1px solid {tokens().warning}; border-radius: 8px; padding: 1px 8px;")
        self.demo_label.hide()
        tl.addWidget(self.demo_label)
        tl.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this case (all imported results)  Ctrl+F")
        self.search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMinimumWidth(320)
        self.search.setAccessibleName("Case-wide search")
        self.search.returnPressed.connect(self._case_search)
        tl.addWidget(self.search)
        self.jobs_status = button("No jobs running", icon_name="jobs", flat=True, on_click=lambda: self.go("jobs"))
        tl.addWidget(self.jobs_status)
        self.new_analysis = button("New analysis", icon_name="play", primary=True, on_click=self.start_analysis,
                                   tooltip="Choose evidence and a workflow (Workflows page)")
        tl.addWidget(self.new_analysis)
        cl.addWidget(top)
        self.banner = Banner()
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.setContentsMargins(20, 8, 20, 0)
        bl.addWidget(self.banner)
        cl.addWidget(bw)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack, 1)
        h.addWidget(col, 1)
        # pages
        from .pages.cases import CasesPage
        from .pages.evidence import EvidencePage
        from .pages.findings import FindingsPage
        from .pages.jobs import JobsPage
        from .pages.overview import OverviewPage
        from .pages.results import ResultsPage
        from .pages.settings import SettingsPage
        from .pages.timeline import TimelinePage
        from .pages.tools import ToolsPage
        from .pages.workflows import WorkflowsPage
        self.pages = {
            "overview": OverviewPage(ctx, self), "cases": CasesPage(ctx, self), "evidence": EvidencePage(ctx, self),
            "workflows": WorkflowsPage(ctx, self), "tools": ToolsPage(ctx, self), "jobs": JobsPage(ctx, self), "results": ResultsPage(ctx, self),
            "timeline": TimelinePage(ctx, self), "findings": FindingsPage(ctx, self), "settings": SettingsPage(ctx, self),
        }
        for key, _, _ in PAGES:
            self.stack.addWidget(self.pages[key])
        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)
        # signals
        ctx.caseChanged.connect(self._case_changed)
        ctx.jobChanged.connect(lambda *_: self._jobs_timer.start(250))
        ctx.navigate.connect(self.go)
        ctx.notice.connect(lambda level, text: self.banner.show_message(level, text))
        self._jobs_timer = QTimer(self, singleShot=True, timeout=self._update_jobs_status)
        # shortcuts
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_case)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_case_dialog)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.command_palette)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("F1"), self, activated=self.context_help)
        for i in range(min(9, len(PAGES))):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.nav.setCurrentRow(i))
        if ctx.settings_store.notice:
            self.banner.show_message("warn", ctx.settings_store.notice)
        if ctx.paths.portable_fallback_reason:
            self.banner.show_message("warn", ctx.paths.portable_fallback_reason)
        self._case_changed()
        self._update_jobs_status()

    # -- navigation ----------------------------------------------------------------------------
    def go(self, key: str, arg=None) -> None:
        for i, (k, _, _) in enumerate(PAGES):
            if k == key:
                self.nav.setCurrentRow(i)
                page = self.pages[key]
                if arg is not None and hasattr(page, "open_with"):
                    page.open_with(arg)
                return

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        if hasattr(page, "on_show"):
            page.on_show()

    def _focus_search(self) -> None:
        page = self.stack.currentWidget()
        if hasattr(page, "focus_filter") and page.focus_filter():
            return
        self.search.setFocus()
        self.search.selectAll()

    def context_help(self) -> None:
        key = PAGES[self.nav.currentRow()][0]
        QMessageBox.information(self, f"Help - {PAGES[self.nav.currentRow()][1]}", HELP[key] + "\n\nSee USER_GUIDE.md for the full guide.")

    # -- case handling ---------------------------------------------------------------------------
    def _case_changed(self) -> None:
        c = self.ctx.case
        if c is None:
            self.case_label.setText("No case open")
            self.demo_label.hide()
            self.search.setEnabled(False)
        else:
            info = c.info
            self.case_label.setText(info.title)
            self.case_label.setToolTip(f"{info.id}\n{c.root}")
            self.demo_label.setVisible(info.is_demo)
            self.search.setEnabled(True)
        others = [o for o in self.ctx.other_cases.values()]
        if others:
            self.banner.show_message("info", "Jobs are still running in another case: " + ", ".join(o.info.title for o in others) +
                                     ". They keep writing to their own case.")
        for p in self.pages.values():
            if hasattr(p, "on_case_changed"):
                p.on_case_changed()

    def new_case(self) -> None:
        d = NewCaseDialog(self.ctx, self)
        if d.exec() and d.store is not None:
            self.ctx.open_case(d.store)
            self.go("evidence")

    def open_case_dialog(self) -> None:
        start = self.ctx.settings.recent_paths.get("cases_dir", str(self.ctx.settings_store.cases_dir))
        d = QFileDialog.getExistingDirectory(self, "Open case folder", start)
        if d:
            self.open_case_path(d)

    def open_case_path(self, path: str) -> None:
        info = peek_case(Path(path))
        if info and self.ctx.case is not None and info["id"] == self.ctx.case.id:
            self.go("overview")
            return
        if info and info["id"] in self.ctx.other_cases:  # jobs still running there: reuse, never re-run recovery
            self.ctx.open_case(self.ctx.other_cases[info["id"]])
            self.go("overview")
            return
        try:
            store = CaseStore.open(Path(path))
        except (CaseError, Exception) as exc:
            self.banner.show_message("error", f"Could not open the case: {exc}", "Choose a folder that contains case.json.")
            return
        self.ctx.open_case(store)
        interrupted = store.db.scalar("SELECT COUNT(*) FROM jobs WHERE state='Interrupted'")
        if interrupted:
            self.banner.show_message("warn", f"{interrupted} job(s) were interrupted when the application last stopped. Their logs and files were kept; nothing was re-run.",
                                     "Open Jobs to inspect or retry them.")
        missing = store.db.scalar("SELECT COUNT(*) FROM evidence WHERE availability!='available'")
        if missing:
            self.banner.show_message("warn", f"{missing} evidence item(s) are unavailable or changed on this machine.", "Open Evidence to remap or verify them.")
        self.go("overview")

    def add_evidence(self, paths: list[str] | None = None) -> None:
        if self.ctx.case is None:
            self.banner.show_message("info", "Create or open a case before adding evidence.", "Press Ctrl+N to create a case.")
            return
        d = AddEvidenceDialog(self.ctx, paths, self)
        if d.exec():
            self.go("evidence")

    def start_analysis(self) -> None:
        if self.ctx.case is None:
            self.banner.show_message("info", "Analyses run inside a case.", "Create a case (Ctrl+N) or open one (Ctrl+O).")
            self.go("overview")
            return
        self.go("workflows")

    def _case_search(self) -> None:
        if self.ctx.case is None or not self.search.text().strip():
            return
        self.go("results", {"case_search": self.search.text().strip()})

    def _update_jobs_status(self) -> None:
        n = self.ctx.jobs.active_count()
        self.jobs_status.setText(f"{n} job(s) active" if n else "No jobs running")
        self.jobs_status.setIcon(icon("jobs", tokens().accent if n else None))
        self.side_jobs.setText(f"{n} active job(s)" if n else "")

    # -- palette / dnd -----------------------------------------------------------------------------
    def command_palette(self) -> None:
        acts = [(f"Go to {text}", f"Ctrl+{i + 1}" if i < 9 else "", (lambda k=key: self.go(k))) for i, (key, text, _) in enumerate(PAGES)]
        acts += [
            ("New case", "Ctrl+N", self.new_case),
            ("Open case", "Ctrl+O", self.open_case_dialog),
            ("Add evidence", "", self.add_evidence),
            ("New analysis (workflow)", "", self.start_analysis),
            ("Import tools from folder", "", lambda: self.pages["tools"].import_folder()),
            ("Import tool archive", "", lambda: self.pages["tools"].import_archive()),
            ("Check catalogue for updates", "network", lambda: self.pages["tools"].check_updates()),
            ("Hash pending evidence", "", lambda: self.pages["evidence"].hash_pending()),
            ("Verify evidence availability", "", lambda: self.pages["evidence"].verify()),
            ("New finding", "", lambda: (self.go("findings"), self.pages["findings"].new_finding())),
            ("Generate HTML report", "", lambda: (self.go("findings"), self.pages["findings"].save_report("html"))),
            ("Toggle light/dark theme", "", self.toggle_theme),
            ("Open case folder", "", self.open_case_folder),
            ("Help for this page", "F1", self.context_help),
        ]
        CommandPalette(acts, self).exec()

    def toggle_theme(self) -> None:
        new = "light" if self.ctx.settings.appearance.theme == "dark" else "dark"
        self.ctx.settings_store.update(appearance={"theme": new})
        apply_theme(QApplication.instance(), new)
        self.ctx.themeChanged.emit(new)
        for i, (key, text, ic) in enumerate(PAGES):
            self.nav.item(i).setIcon(icon(ic))

    def open_case_folder(self) -> None:
        if self.ctx.case:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.case.root)))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        p = paths[0]
        # tool archives / tool folders go to Tools; anything else is evidence
        if p.lower().endswith(".zip") or (os.path.isdir(p) and self.ctx.tools.locate_tools(Path(p))) or (os.path.isdir(p) and self.ctx.tools.locate_tools(Path(p) / "net")):
            self.go("tools")
            self.pages["tools"].import_paths(paths)
        else:
            self.add_evidence(paths)

    # -- lifecycle ----------------------------------------------------------------------------------
    def first_run(self) -> None:
        if self.ctx.settings.first_run_completed:
            return
        d = FirstRunDialog(self.ctx, self)
        d.exec()
        self.ctx.settings_store.update(first_run_completed=True)
        if d.choice == "import":
            self.go("tools")
            self.pages["tools"].import_folder()
        elif d.choice == "download":
            self.go("tools")
            self.pages["tools"].install_all_eligible()

    def closeEvent(self, e):
        n = self.ctx.jobs.active_count()
        if n:
            r = QMessageBox.question(self, "Jobs still running",
                                     f"{n} job(s) are queued or running. Closing stops their process trees; their logs are kept and they will be marked Interrupted/Cancelled. Close anyway?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self.ctx.shutdown()
        e.accept()
