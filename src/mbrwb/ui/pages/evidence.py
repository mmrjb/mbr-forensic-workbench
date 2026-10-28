"""Evidence: tree grouped by registration root, host and artifact type; editable details."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
)

from ...evidence.artifacts import ARTIFACT_TYPES, label as type_label, tooltip as type_tip
from ...evidence.service import EvidenceService
from ..theme import icon, status_color
from ..widgets.common import EmptyState, Panel, PathLabel, Pill, button, label
from .base import Page

BASIS_TEXT = {"signature": "identified by file signature", "structure": "identified by file structure", "filename": "suggested by file name only",
              "operator": "set by the analyst", "derived": "derived by a workbench job"}


class EvidencePage(Page):
    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Evidence", "Registered artifacts with host, user and volume context. The workbench reads evidence; it never writes to it.")
        for b in (button("Add evidence", icon_name="plus", primary=True, on_click=lambda: win.add_evidence()),
                  button("Hash pending", icon_name="shield", on_click=self.hash_pending),
                  button("Verify availability", icon_name="refresh", on_click=self.verify)):
            self.header.add_action(b)
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by path, type, host or user")
        self.filter.textChanged.connect(self._apply_filter)
        self.show_unclassified = QCheckBox("Show unclassified files")
        self.show_unclassified.toggled.connect(self.refresh)
        self.summary = label("", muted=True)
        top.addWidget(self.filter, 1)
        top.addWidget(self.show_unclassified)
        top.addWidget(self.summary)
        self.body_layout.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Evidence", "Type", "Host", "User", "Size", "Hash", "Availability"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.setColumnWidth(0, 360)
        split.addWidget(self.tree)
        self.detail = Panel()
        d = self.detail.lay
        self.d_title = label("Select an item", obj="SectionTitle")
        d.addWidget(self.d_title)
        self.d_path = PathLabel()
        d.addWidget(self.d_path)
        self.d_basis = Pill("", "muted")
        d.addWidget(self.d_basis)
        form = QFormLayout()
        self.e_type = QComboBox()
        for t in sorted(ARTIFACT_TYPES.values(), key=lambda x: x.label):
            self.e_type.addItem(t.label, t.id)
            self.e_type.setItemData(self.e_type.count() - 1, t.tooltip, Qt.ItemDataRole.ToolTipRole)
        self.e_host = QLineEdit()
        self.e_user = QLineEdit()
        self.e_vol = QLineEdit()
        form.addRow("Artifact type", self.e_type)
        form.addRow("Source host", self.e_host)
        form.addRow("User profile", self.e_user)
        form.addRow("Volume", self.e_vol)
        d.addLayout(form)
        self.save_btn = button("Save corrections", icon_name="check", on_click=self._save)
        d.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.d_facts = label("", wrap=True, selectable=True)
        d.addWidget(self.d_facts)
        d.addWidget(label("Related files", obj="SectionTitle"))
        self.related = QListWidget()
        self.related.setMaximumHeight(120)
        d.addWidget(self.related)
        row = QHBoxLayout()
        row.addWidget(button("Register new version", icon_name="refresh", on_click=self._new_version,
                             tooltip="After a source changed: keep the old record and register the current file as a new version."))
        row.addWidget(button("Remap root...", icon_name="folder", on_click=self._remap, tooltip="Point this registration at a new location (for example on another machine)."))
        d.addLayout(row)
        d.addStretch(1)
        self.detail.setMinimumWidth(360)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.body_layout.addWidget(split, 1)
        self.empty = EmptyState("No evidence registered", "Add artifact files, an evidence folder or a collected KAPE-style tree. Discovery runs in the background and shows a preview first.",
                                [button("Add evidence", icon_name="plus", primary=True, on_click=lambda: win.add_evidence())], "evidence")
        self.body_layout.addWidget(self.empty)
        self.empty.hide()
        self._current: str | None = None
        self._cancel = threading.Event()
        ctx.evidenceChanged.connect(lambda: self.isVisible() and self.refresh())

    def refresh(self) -> None:
        case = self.ctx.case
        if case is None:
            return
        unclassified = self.show_unclassified.isChecked()

        def load():
            roots = [dict(r) for r in case.db.query("SELECT * FROM evidence_roots ORDER BY registered_utc")]
            q = "SELECT * FROM evidence" + ("" if unclassified else " WHERE artifact_type IS NOT NULL") + " ORDER BY host, artifact_type, original_path"
            items = [dict(r) for r in case.db.query(q)]
            return roots, items
        self.ctx.run_bg(load, on_done=self._fill)

    def _fill(self, res) -> None:
        roots, items = res
        self.tree.clear()
        self.empty.setVisible(not items)
        self.tree.setVisible(bool(items))
        by_root: dict = {}
        for it in items:
            by_root.setdefault(it["root_id"] or "derived", []).append(it)
        root_items = {}
        for r in roots + [{"id": "derived", "path": "Derived by workbench jobs", "mode": "", "host": None, "label": "Derived artifacts"}]:
            if r["id"] not in by_root:
                continue
            ri = QTreeWidgetItem([f"{r.get('label') or r['path']}", r.get("mode") or "", r.get("host") or "", "", "", "", ""])
            ri.setToolTip(0, r["path"])
            ri.setIcon(0, icon("folder"))
            self.tree.addTopLevelItem(ri)
            root_items[r["id"]] = ri
            hosts: dict = {}
            for it in by_root[r["id"]]:
                hk = it["host"] or "(host not set)"
                if hk not in hosts:
                    hosts[hk] = QTreeWidgetItem([hk, "", "", "", "", "", ""])
                    hosts[hk].setIcon(0, icon("window"))
                    ri.addChild(hosts[hk])
                types = {}
                h = hosts[hk]
                key = it["artifact_type"] or "unclassified"
                for i in range(h.childCount()):
                    types[h.child(i).data(0, Qt.ItemDataRole.UserRole + 1)] = h.child(i)
                if key not in types:
                    t = QTreeWidgetItem([type_label(it["artifact_type"]), "", "", "", "", "", ""])
                    t.setData(0, Qt.ItemDataRole.UserRole + 1, key)
                    t.setToolTip(0, type_tip(it["artifact_type"]))
                    h.addChild(t)
                    types[key] = t
                avail = it["availability"]
                leaf = QTreeWidgetItem([it["rel_path"] or it["original_path"], type_label(it["artifact_type"]), it["host"] or "", it["user_profile"] or "",
                                        f"{it['size'] or 0:,}", it["hash_state"] if it["hash_state"] != "hashed" else "SHA-256 ok", avail])
                leaf.setData(0, Qt.ItemDataRole.UserRole, it["id"])
                leaf.setToolTip(0, it["original_path"])
                if avail != "available":
                    leaf.setForeground(6, _brush(status_color("error")))
                    leaf.setIcon(6, icon("warning", status_color("error"), 14))
                types[key].addChild(leaf)
            ri.setExpanded(True)
            for i in range(ri.childCount()):
                ri.child(i).setExpanded(True)
        n = len(items)
        hashed = sum(1 for i in items if i["hash_state"] == "hashed")
        bad = sum(1 for i in items if i["availability"] != "available")
        self.summary.setText(f"{n:,} items, {hashed:,} hashed, {bad:,} unavailable/changed")
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.filter.text().lower().strip()

        def walk(item) -> bool:
            visible_child = False
            for i in range(item.childCount()):
                visible_child |= walk(item.child(i))
            mine = not text or any(text in item.text(c).lower() for c in range(item.columnCount())) or text in (item.toolTip(0) or "").lower()
            show = mine or visible_child
            item.setHidden(not show)
            return show
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _selected(self) -> None:
        items = self.tree.selectedItems()
        eid = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        self._current = eid
        if not eid or self.ctx.case is None:
            return
        r = dict(self.ctx.case.db.query_one("SELECT * FROM evidence WHERE id=?", (eid,)))
        self.d_title.setText(type_label(r["artifact_type"]))
        self.d_path.set(r["original_path"])
        self.d_basis.set(BASIS_TEXT.get(r["artifact_basis"] or "", "unclassified"), "warn" if r["artifact_basis"] == "filename" else "info")
        i = self.e_type.findData(r["artifact_type"])
        self.e_type.setCurrentIndex(max(0, i))
        self.e_host.setText(r["host"] or "")
        self.e_user.setText(r["user_profile"] or "")
        self.e_vol.setText(r["volume"] or "")
        self.d_facts.setText(
            f"Evidence ID: {r['id']} (version {r['version']}{', supersedes ' + r['supersedes'] if r['supersedes'] else ''})\n"
            f"Working copy: {r['working_path'] or 'none (referenced in place)'}\n"
            f"Size: {r['size']:,} bytes\nObserved file times (file system metadata, not activity): modified {r['mtime_utc']}, changed/created {r['ctime_utc']}\n"
            f"Registered: {r['imported_utc']}\nHash: {r['hash_state']} {r['sha256'] or ''} {('- ' + r['hash_note']) if r['hash_note'] else ''}\n"
            f"Availability: {r['availability']} (checked {r['availability_checked_utc'] or 'at registration'})")
        self.related.clear()
        for rel in EvidenceService(self.ctx.case).related(eid):
            self.related.addItem(f"{rel['relation']}: {rel['other_path']} ({rel['basis']})")
        if not self.related.count():
            self.related.addItem("No related files linked.")

    def _save(self) -> None:
        if not self._current:
            return
        EvidenceService(self.ctx.case).correct(self._current, artifact_type=self.e_type.currentData(), host=self.e_host.text().strip() or None,
                                               user_profile=self.e_user.text().strip() or None, volume=self.e_vol.text().strip() or None)
        self.win.banner.show_message("ok", "Corrections saved; they apply to future plans. Past jobs keep what they ran with.")
        self.refresh()

    def hash_pending(self) -> None:
        if self.ctx.case is None:
            return
        case = self.ctx.case
        self._cancel = threading.Event()
        limit = self.ctx.settings.case_defaults.hash_size_limit_mb * 1024 * 1024
        self.win.banner.show_message("info", "Hashing pending evidence in the background...")
        self.ctx.run_bg(lambda: EvidenceService(case).hash_pending(cancel=self._cancel, size_limit=limit),
                        on_done=lambda c: (self.win.banner.show_message("ok", f"Hashing finished: {c['hashed']} hashed, {c['unavailable']} unavailable, {c['excluded']} excluded."),
                                           self.refresh()))

    def verify(self) -> None:
        if self.ctx.case is None:
            return
        case = self.ctx.case
        self.ctx.run_bg(lambda: EvidenceService(case).verify_availability(),
                        on_done=lambda c: (self.win.banner.show_message("ok" if not (c["changed"] or c["unavailable"]) else "warn",
                                                                        f"Availability: {c['available']} available, {c['changed']} changed, {c['unavailable']} unavailable."
                                                                        + (" Changed sources keep their previous manifest; register a new version explicitly." if c["changed"] else "")),
                                           self.refresh()))

    def _new_version(self) -> None:
        if not self._current:
            return
        try:
            nid = EvidenceService(self.ctx.case).register_new_version(self._current)
            self.win.banner.show_message("ok", f"Registered new version {nid}; hash it with 'Hash pending'.")
            self.refresh()
        except Exception as exc:
            self.win.banner.show_message("error", str(exc))

    def _remap(self) -> None:
        if not self._current:
            return
        root_id = self.ctx.case.db.scalar("SELECT root_id FROM evidence WHERE id=?", (self._current,))
        if not root_id:
            return
        d = QFileDialog.getExistingDirectory(self, "New location of this evidence root")
        if d:
            n = EvidenceService(self.ctx.case).remap_root(root_id, d)
            self.win.banner.show_message("ok", f"Remapped {n} item(s). Run 'Verify availability' to confirm.")
            self.refresh()


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))

