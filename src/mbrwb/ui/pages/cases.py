"""Cases: recent list, create/open, archive, forget (never deletes)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QMessageBox, QTableView

from ...storage.case_store import CaseError, CaseStore, peek_case
from ..widgets.common import button, label
from ..widgets.paged_model import RowsModel
from .base import Page


class CasesPage(Page):
    case_scoped = False

    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Cases", "Cases keep evidence references, jobs, results and findings together in one folder.")
        for b in (button("New case", icon_name="plus", primary=True, on_click=win.new_case),
                  button("Open case folder", icon_name="folder", on_click=win.open_case_dialog)):
            self.header.add_action(b)
        self.model = RowsModel(["Case", "Case ID", "Examiner", "Created (UTC)", "Last activity (UTC)", "Location", "Status"])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._open)
        self.body_layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button("Open", icon_name="folder", primary=True, on_click=self._open))
        row.addWidget(button("Show in file manager", icon_name="external", on_click=self._reveal))
        row.addWidget(button("Archive copy...", icon_name="download", on_click=self._archive,
                             tooltip="Copy the case to another folder after jobs have stopped (databases copied via SQLite backup)."))
        row.addWidget(button("Remove from list", icon_name="x", on_click=self._forget, tooltip="Forgets the entry only. The case folder and evidence are not deleted."))
        row.addStretch(1)
        self.note = label("Removing a case from this list never deletes its folder or evidence. Archiving makes a consistent copy; deletion is not offered here.", muted=True, wrap=True)
        self.body_layout.addLayout(row)
        self.body_layout.addWidget(self.note)

    def refresh(self) -> None:
        def load():
            rows, payload = [], []
            for r in self.ctx.app_db.query("SELECT * FROM recent_cases ORDER BY last_opened_utc DESC"):
                root = Path(r["case_root"])
                status, examiner, created, updated, cid = "missing", "", "", "", r["case_id"]
                if (root / "case.json").exists():
                    info = peek_case(root)
                    if info is None:
                        status = "unreadable"
                    else:
                        examiner, created, updated, cid = info["examiner"], info["created_utc"][:19], info["updated_utc"][:19], info["id"]
                        status = "open" if self.ctx.case and self.ctx.case.id == cid else ("demo" if info["is_demo"] else "available")
                        if cid in self.ctx.other_cases:
                            status = "jobs running in background"
                rows.append([r["title"], cid, examiner, created, updated, str(root), status])
                payload.append(str(root))
            return rows, payload
        self.ctx.run_bg(load, on_done=lambda res: self.model.set_rows(res[0], res[1]))

    def _selected(self) -> str | None:
        idx = self.table.currentIndex()
        return self.model.data(self.model.index(idx.row(), 0), Qt.ItemDataRole.UserRole) if idx.isValid() else None

    def _open(self, *_):
        p = self._selected()
        if p:
            self.win.open_case_path(p)

    def _reveal(self):
        p = self._selected()
        if p and Path(p).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def _forget(self):
        p = self._selected()
        if p:
            self.ctx.app_db.execute("DELETE FROM recent_cases WHERE case_root=?", (p,))
            self.refresh()

    def _archive(self):
        p = self._selected()
        if not p:
            return
        dest = QFileDialog.getExistingDirectory(self, "Empty folder for the archive copy")
        if not dest:
            return
        try:
            info = peek_case(Path(p))
            opened = self.ctx.case if self.ctx.case and info and self.ctx.case.id == info["id"] else (self.ctx.other_cases.get(info["id"]) if info else None)
            store = opened or CaseStore.open(Path(p))
            try:
                store.archive_to(Path(dest) / Path(p).name, active_jobs=self.ctx.jobs.active_count(store.id))
            finally:
                if opened is None:
                    store.close()
            QMessageBox.information(self, "Archive", f"Case copied to {Path(dest) / Path(p).name}.")
        except (CaseError, OSError) as exc:
            self.win.banner.show_message("error", str(exc))
