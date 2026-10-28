"""Evidence registration, hashing, relationships and availability checks.

Registration writes an immutable JSON manifest per evidence root (what was inspected, hashed,
unavailable or excluded) under ``evidence_manifest/``. Working copies preserve the relative
directory structure (host/user/volume associations stay visible) and are hashed against the
source. Evidence is never modified by the workbench; derived artifacts (for example RLA
replayed hives) are registered as *new* evidence linked to their parents.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config.settings import atomic_write_text
from ..domain.enums import Availability, HashState
from ..domain.models import EvidenceRef
from ..storage.case_store import CaseStore, new_id, path_within
from ..storage.db import utcnow
from .artifacts import DIRECTORY_OF
from .discovery import Candidate, DiscoveryReport

HASH_CHUNK = 1 << 20


class EvidenceError(Exception):
    pass


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_file(path: str | Path, cancel: threading.Event | None = None, progress: Callable[[int], None] | None = None) -> str | None:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            if cancel is not None and cancel.is_set():
                return None
            b = fh.read(HASH_CHUNK)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


@dataclass
class RegistrationPlan:
    """Preview shown before anything is registered."""

    root: Path
    mode: str  # in_place | working_copy
    destination: Path | None
    host: str | None
    candidates: int
    unclassified: int
    total_bytes: int
    grouping_note: str


class EvidenceService:
    def __init__(self, case: CaseStore):
        self.case = case

    # -- registration ------------------------------------------------------------------
    def plan(self, report: DiscoveryReport, *, mode: str, host: str | None, include_unclassified: bool = False) -> RegistrationPlan:
        items = report.candidates + (report.unclassified if include_unclassified else [])
        dest = self.case.root / "working_copies" / _safe_name(Path(report.root).name) if mode == "working_copy" else None
        hosts = sorted({c.host for c in report.candidates if c.host} | ({host} if host else set()))
        return RegistrationPlan(Path(report.root), mode, dest, host, len(report.candidates), len(report.unclassified),
                                sum(c.size for c in items),
                                "Hosts: " + (", ".join(hosts) if hosts else "not determined (set one below)") +
                                f"; users: {len({c.user_profile for c in report.candidates if c.user_profile})}; "
                                f"volumes: {len({c.volume for c in report.candidates if c.volume})}.")

    def register(self, report: DiscoveryReport, *, mode: str = "in_place", host: str | None = None, label: str | None = None,
                 include_unclassified: bool = False, cancel: threading.Event | None = None,
                 progress: Callable[[str, int, int], None] | None = None, collected_by_job: str | None = None) -> str:
        """``collected_by_job`` is set only for collections a workbench job produced (KAPE Targets);
        such a tree lives inside the case's job folder and is registered with that provenance."""
        root = Path(report.root)
        if path_within(self.case.root, root):
            raise EvidenceError("The case folder is inside this evidence location; outputs could mix with evidence. Move the case first.")
        if path_within(root, self.case.root) and not (collected_by_job and path_within(root, self.case.job_dir(collected_by_job))):
            raise EvidenceError("This location is inside the case folder (workbench outputs are not evidence).")
        root_id = new_id("evr")
        now = utcnow()
        items: list[Candidate] = report.candidates + (report.unclassified if include_unclassified else [])
        working_root = None
        if mode == "working_copy":
            working_root = self.case.root / "working_copies" / f"{root_id}_{_safe_name(root.name)}"
            working_root.mkdir(parents=True, exist_ok=False)
        with self.case.db.write() as c:
            c.execute("""INSERT INTO evidence_roots(id, case_id, path, kind, mode, working_path, label, host, registered_utc, files_inspected,
                         candidates, unavailable, excluded, discovery_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (root_id, self.case.id, str(root), "folder" if root.is_dir() else "file", mode, str(working_root) if working_root else None,
                       label or (f"Collected by job {collected_by_job}" if collected_by_job else root.name), host, now, report.files_inspected,
                       len(report.candidates), len(report.unavailable), len(report.excluded),
                       "cancelled" if report.cancelled else "complete"))
            for cand in items:
                c.execute("""INSERT INTO evidence(id, case_id, root_id, original_path, rel_path, artifact_type, artifact_basis, host, user_profile, volume,
                             size, mtime_utc, ctime_utc, atime_utc, imported_utc, hash_state, availability) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (new_id("ev"), self.case.id, root_id, cand.path, cand.rel_path, cand.artifact_type, cand.basis if cand.artifact_type else None,
                           host or cand.host, cand.user_profile, cand.volume, cand.size, _iso(cand.mtime), _iso(cand.ctime), _iso(cand.atime), now,
                           HashState.PENDING.value, Availability.AVAILABLE.value))
        if working_root:
            self._make_working_copies(root_id, root, working_root, cancel, progress)
        self.link_related(root_id)
        self.write_manifest(root_id, report)
        self.case.audit("evidence.registered", root_id, {"path": str(root), "mode": mode, "items": len(items), "host": host})
        return root_id

    def _make_working_copies(self, root_id: str, src_root: Path, dst_root: Path, cancel, progress) -> None:
        rows = self.case.db.query("SELECT id, original_path, rel_path FROM evidence WHERE root_id=?", (root_id,))
        for i, r in enumerate(rows):
            if cancel is not None and cancel.is_set():
                break
            dst = dst_root / r["rel_path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(r["original_path"], dst)
                self.case.db.execute("UPDATE evidence SET working_path=? WHERE id=?", (str(dst), r["id"]))
            except OSError as exc:
                self.case.db.execute("UPDATE evidence SET hash_note=? WHERE id=?", (f"working copy failed: {exc}", r["id"]))
            if progress:
                progress("copy", i + 1, len(rows))

    def write_manifest(self, root_id: str, report: DiscoveryReport | None = None) -> Path:
        rows = [dict(r) for r in self.case.db.query("SELECT * FROM evidence WHERE root_id=? ORDER BY rel_path", (root_id,))]
        root = dict(self.case.db.query_one("SELECT * FROM evidence_roots WHERE id=?", (root_id,)))
        stamp = utcnow().replace(":", "").replace("-", "")[:15]
        path = self.case.root / "evidence_manifest" / f"{root_id}_{stamp}.json"
        doc = {
            "root": root,
            "generated_utc": utcnow(),
            "statement": "Lists exactly which files were registered and their hash state. Files marked pending/unavailable/excluded were NOT hashed; "
                         "the folder as a whole is not 'verified' unless every listed file is hashed.",
            "items": [{k: r[k] for k in ("id", "original_path", "working_path", "rel_path", "artifact_type", "artifact_basis", "host", "user_profile",
                                         "volume", "size", "mtime_utc", "hash_state", "sha256", "hash_note", "availability")} for r in rows],
            "unavailable": [{"path": p, "reason": why} for p, why in (report.unavailable if report else [])],
            "excluded": [{"path": p, "reason": why} for p, why in (report.excluded if report else [])],
        }
        atomic_write_text(path, json.dumps(doc, indent=1, ensure_ascii=False))
        self.case.db.execute("UPDATE evidence_roots SET manifest_path=? WHERE id=?", (str(path), root_id))
        return path

    # -- hashing -----------------------------------------------------------------------
    def hash_pending(self, *, root_id: str | None = None, cancel: threading.Event | None = None,
                     progress: Callable[[int, int, int], None] | None = None, size_limit: int = 0) -> dict:
        q = "SELECT id, original_path, working_path, size FROM evidence WHERE hash_state IN ('pending','cancelled','failed')"
        args: tuple = ()
        if root_id:
            q += " AND root_id=?"
            args = (root_id,)
        rows = self.case.db.query(q, args)
        total_bytes = sum(r["size"] or 0 for r in rows)
        done_bytes = 0
        counts = {"hashed": 0, "unavailable": 0, "excluded": 0, "cancelled": 0}
        for i, r in enumerate(rows):
            if cancel is not None and cancel.is_set():
                counts["cancelled"] = len(rows) - i
                break
            if size_limit and (r["size"] or 0) > size_limit:
                self.case.db.execute("UPDATE evidence SET hash_state=?, hash_note=? WHERE id=?",
                                     (HashState.EXCLUDED.value, f"larger than the {size_limit} byte hashing limit", r["id"]))
                counts["excluded"] += 1
                continue
            self.case.db.execute("UPDATE evidence SET hash_state=? WHERE id=?", (HashState.HASHING.value, r["id"]))

            def prog(n, _i=i):
                nonlocal done_bytes
                done_bytes += n
                if progress:
                    progress(_i, len(rows), done_bytes * 100 // max(total_bytes, 1))
            try:
                digest = sha256_file(r["original_path"], cancel, prog)
            except OSError as exc:
                self.case.db.execute("UPDATE evidence SET hash_state=?, hash_note=? WHERE id=?",
                                     (HashState.UNAVAILABLE.value, f"{type(exc).__name__}: {exc.strerror or exc}", r["id"]))
                counts["unavailable"] += 1
                continue
            if digest is None:
                self.case.db.execute("UPDATE evidence SET hash_state=? WHERE id=?", (HashState.CANCELLED.value, r["id"]))
                counts["cancelled"] = len(rows) - i
                break
            note = None
            if r["working_path"]:
                try:
                    wd = sha256_file(r["working_path"])
                    note = "working copy hash matches source" if wd == digest else f"WORKING COPY DIFFERS (copy {wd})"
                except OSError as exc:
                    note = f"working copy unreadable: {exc}"
            self.case.db.execute("UPDATE evidence SET hash_state=?, sha256=?, hash_note=? WHERE id=?", (HashState.HASHED.value, digest, note, r["id"]))
            counts["hashed"] += 1
        for rid in {r["root_id"] for r in self.case.db.query("SELECT DISTINCT root_id FROM evidence WHERE root_id IS NOT NULL" + (" AND root_id=?" if root_id else ""), args)}:
            self.write_manifest(rid)
        self.case.audit("evidence.hashed", root_id, counts)
        return counts

    # -- relationships -------------------------------------------------------------------
    def link_related(self, root_id: str) -> int:
        """Automatic relations within one registration: hive<->transaction logs (same folder),
        SQLite db<->sidecars (same folder), $J/$LogFile<->$MFT (same host+volume, only when
        exactly one $MFT matches), SRUDB.dat<->SOFTWARE hive (same host, only one match)."""
        rows = [dict(r) for r in self.case.db.query("SELECT * FROM evidence WHERE root_id=?", (root_id,))]
        by_dir: dict[str, list[dict]] = {}
        for r in rows:
            by_dir.setdefault(os.path.dirname(r["original_path"]).lower(), []).append(r)
        made = 0

        def link(parent, child, rel):
            nonlocal made
            with self.case.db.write() as c:
                cur = c.execute("INSERT OR IGNORE INTO evidence_relations(id,parent_id,child_id,relation,basis,created_utc) VALUES (?,?,?,?,?,?)",
                                (new_id("rel"), parent["id"], child["id"], rel, "auto", utcnow()))
                made += cur.rowcount
        for files in by_dir.values():
            names = {os.path.basename(f["original_path"]).lower(): f for f in files}
            for f in files:
                n = os.path.basename(f["original_path"]).lower()
                if f["artifact_type"] in ("system_hive", "software_hive", "sam_hive", "security_hive", "ntuser_hive", "usrclass_hive", "amcache_hive", "registry_hive"):
                    for suf in (".log1", ".log2", ".log"):
                        if n + suf in names:
                            link(f, names[n + suf], "transaction_log")
                if f["artifact_type"] in ("sqlite_db", "activitiescache_db"):
                    for suf in ("-wal", "-journal", "-shm"):
                        if n + suf in names:
                            link(f, names[n + suf], "sqlite_sidecar")
        mfts = [r for r in rows if r["artifact_type"] == "ntfs_mft"]
        for j in (r for r in rows if r["artifact_type"] in ("ntfs_usnjrnl_j", "ntfs_logfile")):
            same = [m for m in mfts if (m["host"], m["volume"]) == (j["host"], j["volume"]) and m["volume"]]
            if len(same) == 1:
                link(same[0], j, "same_volume_mft")
        softs = [r for r in rows if r["artifact_type"] == "software_hive"]
        for s in (r for r in rows if r["artifact_type"] == "srum_db"):
            same = [h for h in softs if h["host"] == s["host"]]
            if len(same) == 1:
                link(s, same[0], "software_hive")
        return made

    def add_relation(self, parent_id: str, child_id: str, relation: str) -> None:
        with self.case.db.write() as c:
            c.execute("INSERT OR IGNORE INTO evidence_relations(id,parent_id,child_id,relation,basis,created_utc) VALUES (?,?,?,?,?,?)",
                      (new_id("rel"), parent_id, child_id, relation, "operator", utcnow()))
        self.case.audit("evidence.relation_added", parent_id, {"child": child_id, "relation": relation})

    def related(self, evidence_id: str) -> list[dict]:
        return [dict(r) for r in self.case.db.query(
            "SELECT r.*, e.original_path AS other_path, e.artifact_type AS other_type FROM evidence_relations r JOIN evidence e ON e.id = CASE WHEN r.parent_id=? THEN r.child_id ELSE r.parent_id END "
            "WHERE r.parent_id=? OR r.child_id=?", (evidence_id, evidence_id, evidence_id))]

    # -- corrections -----------------------------------------------------------------------
    def correct(self, evidence_id: str, **fields) -> None:
        allowed = {"artifact_type", "host", "user_profile", "volume", "note"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        old = dict(self.case.db.query_one("SELECT * FROM evidence WHERE id=?", (evidence_id,)))
        extra = ", artifact_basis='operator'" if "artifact_type" in sets else ""
        with self.case.db.write() as c:
            c.execute(f"UPDATE evidence SET {', '.join(f'{k}=?' for k in sets)}{extra} WHERE id=?", (*sets.values(), evidence_id))
        self.case.audit("evidence.corrected", evidence_id, {"before": {k: old[k] for k in sets}, "after": sets})

    # -- availability ----------------------------------------------------------------------
    def verify_availability(self, *, rehash: bool = False, cancel: threading.Event | None = None) -> dict:
        """Re-stat (optionally re-hash) registered files. A changed or missing source becomes
        'changed'/'unavailable'; its registered manifest is kept, and a new evidence version must
        be registered explicitly."""
        out = {"available": 0, "changed": 0, "unavailable": 0}
        for r in self.case.db.query("SELECT id, original_path, size, sha256, availability FROM evidence WHERE supersedes IS NULL OR 1"):
            if cancel is not None and cancel.is_set():
                break
            p = r["original_path"]
            state = Availability.AVAILABLE
            if not os.path.exists(p):
                state = Availability.UNAVAILABLE
            else:
                try:
                    if r["size"] is not None and os.path.getsize(p) != r["size"]:
                        state = Availability.CHANGED
                    elif rehash and r["sha256"] and sha256_file(p, cancel) not in (r["sha256"], None):
                        state = Availability.CHANGED
                except OSError:
                    state = Availability.UNAVAILABLE
            out[state.value] += 1
            if state.value != r["availability"]:
                self.case.db.execute("UPDATE evidence SET availability=?, availability_checked_utc=? WHERE id=?", (state.value, utcnow(), r["id"]))
                self.case.audit("evidence.availability", r["id"], {"from": r["availability"], "to": state.value})
            else:
                self.case.db.execute("UPDATE evidence SET availability_checked_utc=? WHERE id=?", (utcnow(), r["id"]))
        return out

    def register_new_version(self, evidence_id: str) -> str:
        old = dict(self.case.db.query_one("SELECT * FROM evidence WHERE id=?", (evidence_id,)))
        p = Path(old["original_path"])
        if not p.exists():
            raise EvidenceError("The source is unavailable; reconnect it or remap the path first.")
        st = p.stat()
        nid = new_id("ev")
        with self.case.db.write() as c:
            c.execute("""INSERT INTO evidence(id, case_id, root_id, version, supersedes, original_path, rel_path, artifact_type, artifact_basis, host, user_profile,
                         volume, size, mtime_utc, ctime_utc, atime_utc, imported_utc, hash_state, availability, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (nid, old["case_id"], old["root_id"], old["version"] + 1, evidence_id, old["original_path"], old["rel_path"], old["artifact_type"],
                       old["artifact_basis"], old["host"], old["user_profile"], old["volume"], st.st_size, _iso(st.st_mtime), _iso(st.st_ctime), _iso(st.st_atime),
                       utcnow(), "pending", "available", f"new version of {evidence_id}"))
        self.case.audit("evidence.new_version", nid, {"supersedes": evidence_id})
        return nid

    def remap_root(self, root_id: str, new_path: str) -> int:
        """Point a root (and its items) at a new location, e.g. after reopening the case on another
        machine. Past job records keep the paths they were run with."""
        old = self.case.db.query_one("SELECT path FROM evidence_roots WHERE id=?", (root_id,))["path"]
        n = 0
        with self.case.db.write() as c:
            for r in c.execute("SELECT id, original_path FROM evidence WHERE root_id=?", (root_id,)).fetchall():
                if r["original_path"].startswith(old):
                    c.execute("UPDATE evidence SET original_path=?, availability='available' WHERE id=?", (new_path + r["original_path"][len(old):], r["id"]))
                    n += 1
            c.execute("UPDATE evidence_roots SET path=? WHERE id=?", (new_path, root_id))
        self.case.audit("evidence.remapped", root_id, {"from": old, "to": new_path, "items": n})
        return n

    # -- derived artifacts -----------------------------------------------------------------
    def register_derived(self, path: Path, *, parents: list[str], relation: str, artifact_type: str | None, host: str | None,
                         user_profile: str | None, note: str) -> str:
        st = path.stat()
        nid = new_id("ev")
        with self.case.db.write() as c:
            c.execute("""INSERT INTO evidence(id, case_id, original_path, rel_path, artifact_type, artifact_basis, host, user_profile, size, mtime_utc,
                         ctime_utc, atime_utc, imported_utc, hash_state, sha256, availability, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (nid, self.case.id, str(path), path.name, artifact_type, "derived", host, user_profile, st.st_size, _iso(st.st_mtime), _iso(st.st_ctime),
                       _iso(st.st_atime), utcnow(), "hashed", sha256_file(path), "available", note))
            for pid in parents:
                c.execute("INSERT OR IGNORE INTO evidence_relations(id,parent_id,child_id,relation,basis,created_utc) VALUES (?,?,?,?,?,?)",
                          (new_id("rel"), pid, nid, relation, "auto", utcnow()))
        self.case.audit("evidence.derived", nid, {"parents": parents, "relation": relation, "path": str(path)})
        return nid

    # -- lookups ----------------------------------------------------------------------------
    def ref(self, evidence_id: str) -> EvidenceRef:
        r = self.case.db.query_one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        if r is None:
            raise EvidenceError(f"unknown evidence {evidence_id}")
        return _to_ref(r)

    def items(self, *, artifact_types: tuple[str, ...] | None = None, available_only: bool = True) -> list[EvidenceRef]:
        q = "SELECT * FROM evidence e WHERE NOT EXISTS (SELECT 1 FROM evidence n WHERE n.supersedes = e.id)"
        args: list = []
        if artifact_types:
            q += f" AND artifact_type IN ({','.join('?' * len(artifact_types))})"
            args += list(artifact_types)
        if available_only:
            q += " AND availability='available'"
        return [_to_ref(r) for r in self.case.db.query(q + " ORDER BY host, original_path", args)]

    def directory_groups(self, file_type: str) -> list[EvidenceRef]:
        """Group registered files of ``file_type`` by (host, parent folder) into folder inputs
        for directory-mode tools, so one job covers a folder instead of one job per file."""
        groups: dict[tuple, list[EvidenceRef]] = {}
        for ref in self.items(artifact_types=(file_type,)):
            groups.setdefault((ref.host, ref.user_profile, os.path.dirname(ref.path)), []).append(ref)
        out = []
        for (host, user, folder), members in groups.items():
            out.append(EvidenceRef(id="grp:" + ",".join(m.id for m in members), path=folder, original_path=folder,
                                   artifact_type=DIRECTORY_OF.get(file_type, "any_directory"), host=host, user_profile=user,
                                   volume=members[0].volume, size=sum(m.size or 0 for m in members), is_dir=True))
        return out


def _to_ref(r) -> EvidenceRef:
    return EvidenceRef(id=r["id"], path=r["working_path"] or r["original_path"], original_path=r["original_path"], artifact_type=r["artifact_type"],
                       host=r["host"], user_profile=r["user_profile"], volume=r["volume"], size=r["size"], sha256=r["sha256"], is_dir=False)


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    return out[:80] or "evidence"
