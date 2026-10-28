"""Case folders.

    CaseRoot/
      case.json            identity + schema marker (written atomically)
      case.sqlite          metadata (evidence, jobs, outputs, findings, ...)
      results.sqlite       imported datasets and timeline events
      evidence_manifest/   per-registration manifests (JSON, never rewritten)
      working_copies/      operator-chosen working copies of evidence
      jobs/<job-id>/       job.json, stdout.log, stderr.log, outputs/, temp/
      reports/  exports/

Opening a folder checks ``case.json`` and the database tables before adopting it, so an
unrelated folder is never treated as a case.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config.settings import atomic_write_text
from ..domain.enums import JobState
from .db import Database, quote_ident, utcnow
from .schema import CASE_MIGRATIONS, RESULTS_MIGRATIONS

CASE_MARKER = "mbr-forensic-workbench-case"
SUBDIRS = ("evidence_manifest", "working_copies", "jobs", "reports", "exports")


class CaseError(Exception):
    pass


@dataclass
class CaseInfo:
    id: str
    title: str
    examiner: str
    description: str
    reference: str
    display_tz: str
    tags: str
    is_demo: bool
    created_utc: str
    updated_utc: str


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def path_within(child: str | Path, parent: str | Path) -> bool:
    """Case-insensitive on Windows; resolves symlinks/junctions where the path exists."""
    try:
        c = Path(os.path.realpath(child))
        p = Path(os.path.realpath(parent))
    except OSError:
        c, p = Path(child).absolute(), Path(parent).absolute()
    cs, ps = str(c), str(p)
    if os.name == "nt":
        cs, ps = cs.lower(), ps.lower()
    return cs == ps or cs.startswith(ps.rstrip("\\/") + os.sep)


def peek_case(root: Path) -> dict | None:
    """Read identity and evidence roots of a case *without* opening it for work: no migrations,
    no recovery (which would mark another session's running jobs as interrupted)."""
    import sqlite3

    root = Path(root)
    try:
        meta = json.loads((root / "case.json").read_text("utf-8"))
        if meta.get("marker") != CASE_MARKER:
            return None
        con = sqlite3.connect((root / "case.sqlite").resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            con.row_factory = sqlite3.Row
            r = con.execute("SELECT * FROM cases").fetchone()
            roots = [x[0] for x in con.execute("SELECT path FROM evidence_roots")]
        finally:
            con.close()
        return {"id": r["id"], "title": r["title"], "examiner": r["examiner"] or "", "created_utc": r["created_utc"],
                "updated_utc": r["updated_utc"], "is_demo": bool(r["is_demo"]), "evidence_roots": roots}
    except (OSError, ValueError, sqlite3.Error, TypeError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _take_lock(root: Path) -> None:
    """One workbench process per case: a second instance would otherwise run recovery and mark
    the first instance's running jobs as interrupted. A lock left by a dead process is taken over."""
    lock = root / "case.lock"
    if lock.exists():
        try:
            pid = int(json.loads(lock.read_text("utf-8")).get("pid", 0))
        except (OSError, ValueError):
            pid = 0
        if pid != os.getpid() and _pid_alive(pid):
            raise CaseError(f"This case is already open in another workbench process (pid {pid}). Close it there first.")
    atomic_write_text(lock, json.dumps({"pid": os.getpid(), "since_utc": utcnow()}))


class CaseStore:
    def __init__(self, root: Path, db: Database, results: Database):
        self.root = Path(root)
        self.db = db
        self.results = results

    # -- lifecycle ------------------------------------------------------------------------
    @classmethod
    def create(cls, root: Path, *, title: str, examiner: str = "", display_tz: str = "UTC", description: str = "",
               reference: str = "", tags: str = "", is_demo: bool = False, forbidden_parents: list[str] | None = None) -> "CaseStore":
        title = title.strip()
        if not title:
            raise CaseError("A case title is required.")
        root = Path(root)
        for parent in forbidden_parents or []:
            if path_within(root, parent):
                raise CaseError(f"The case folder cannot be inside registered evidence ({parent}). Choose another location.")
        if root.exists() and any(root.iterdir()):
            raise CaseError(f"{root} is not empty. Choose an empty or new folder for the case.")
        root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (root / d).mkdir(exist_ok=True)
        _take_lock(root)
        case_id = new_id("case")
        now = utcnow()
        atomic_write_text(root / "case.json", json.dumps({"marker": CASE_MARKER, "case_id": case_id, "title": title, "created_utc": now,
                                                          "is_demo": is_demo, "layout_version": 1}, indent=2, ensure_ascii=False))
        db = Database(root / "case.sqlite", CASE_MIGRATIONS, label="case")
        results = Database(root / "results.sqlite", RESULTS_MIGRATIONS, label="results")
        with db.write() as c:
            c.execute("INSERT INTO cases(id,title,examiner,description,reference,display_tz,tags,is_demo,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (case_id, title, examiner, description, reference, display_tz, tags, 1 if is_demo else 0, now, now))
        store = cls(root, db, results)
        store.audit("case.created", case_id, {"title": title, "examiner": examiner, "display_tz": display_tz, "is_demo": is_demo})
        return store

    @classmethod
    def open(cls, root: Path) -> "CaseStore":
        root = Path(root)
        marker = root / "case.json"
        if not marker.is_file() or not (root / "case.sqlite").is_file():
            raise CaseError(f"{root} is not a workbench case (case.json / case.sqlite missing).")
        try:
            meta = json.loads(marker.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise CaseError(f"case.json is unreadable: {exc}") from exc
        if meta.get("marker") != CASE_MARKER:
            raise CaseError(f"{root} does not carry the workbench case marker.")
        _take_lock(root)
        db = Database(root / "case.sqlite", CASE_MIGRATIONS, label="case")
        results = Database(root / "results.sqlite", RESULTS_MIGRATIONS, label="results")
        row = db.query_one("SELECT id FROM cases")
        if row is None or row["id"] != meta.get("case_id"):
            db.close()
            results.close()
            raise CaseError("case.json and case.sqlite disagree about the case identity.")
        for d in SUBDIRS:
            (root / d).mkdir(exist_ok=True)
        store = cls(root, db, results)
        store.recover()
        return store

    def close(self) -> None:
        self.db.close()
        self.results.close()
        lock = self.root / "case.lock"
        try:
            if lock.exists() and int(json.loads(lock.read_text("utf-8")).get("pid", 0)) == os.getpid():
                lock.unlink()
        except (OSError, ValueError):
            pass

    # -- identity -------------------------------------------------------------------------
    @property
    def info(self) -> CaseInfo:
        r = self.db.query_one("SELECT * FROM cases")
        return CaseInfo(r["id"], r["title"], r["examiner"] or "", r["description"] or "", r["reference"] or "", r["display_tz"],
                        r["tags"] or "", bool(r["is_demo"]), r["created_utc"], r["updated_utc"])

    @property
    def id(self) -> str:
        return self.info.id

    def update_info(self, **fields) -> None:
        allowed = {"title", "examiner", "description", "reference", "display_tz", "tags"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        with self.db.write() as c:
            c.execute(f"UPDATE cases SET {', '.join(f'{k}=?' for k in sets)}, updated_utc=?", (*sets.values(), utcnow()))
        self.audit("case.updated", self.id, sets)

    def touch(self) -> None:
        self.db.execute("UPDATE cases SET updated_utc=?", (utcnow(),))

    def audit(self, action: str, target: str | None = None, detail: dict | None = None, actor: str | None = None) -> None:
        self.db.execute("INSERT INTO audit_log(ts_utc, actor, action, target, detail_json) VALUES (?,?,?,?,?)",
                        (utcnow(), actor or os.environ.get("USERNAME") or os.environ.get("USER") or "", action, target,
                         json.dumps(detail or {}, ensure_ascii=False, default=str)))

    # -- recovery -------------------------------------------------------------------------
    def recover(self) -> dict:
        """Mark work that was active when the application stopped as Interrupted. Nothing is
        re-run. Datasets left mid-import lose their uncommitted rows (the import transaction
        never committed) and are marked interrupted."""
        active = [s.value for s in JobState if s.is_active]
        rows = self.db.query(f"SELECT id FROM jobs WHERE state IN ({','.join('?' * len(active))})", active)
        now = utcnow()
        with self.db.write() as c:
            for r in rows:
                c.execute("UPDATE jobs SET state=?, phase=?, ended_utc=COALESCE(ended_utc, ?), error_category=COALESCE(error_category, 'Process failure'),"
                          " error_message=COALESCE(error_message, 'The application stopped while this job was active. Logs and any files written so far were kept; partial outputs are quarantined.'),"
                          " next_action=COALESCE(next_action, 'Inspect the logs and use Retry to run it again in a fresh job folder.') WHERE id=?",
                          (JobState.INTERRUPTED.value, "interrupted", now, r["id"]))
                c.execute("UPDATE outputs SET completion='quarantined', import_state=CASE WHEN import_state IN ('pending','importing') THEN 'cancelled' ELSE import_state END WHERE job_id=? AND completion!='complete'", (r["id"],))
        ds = self.results.query("SELECT id, row_table FROM datasets WHERE state='importing'")
        with self.results.write() as c:
            for d in ds:
                if d["row_table"]:
                    c.execute(f"DROP TABLE IF EXISTS {quote_ident(d['row_table'])}")
                c.execute("DELETE FROM timeline_events WHERE dataset_id=?", (d["id"],))
                c.execute("UPDATE datasets SET state='interrupted', error='Import interrupted by application exit; re-import from the preserved output.' WHERE id=?", (d["id"],))
        if rows or ds:
            self.audit("case.recovered", self.id, {"interrupted_jobs": [r["id"] for r in rows], "interrupted_datasets": [d["id"] for d in ds]})
        return {"jobs": [r["id"] for r in rows], "datasets": [d["id"] for d in ds]}

    # -- paths ----------------------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id

    def evidence_roots(self) -> list[str]:
        return [r["path"] for r in self.db.query("SELECT path FROM evidence_roots")]

    # -- archival -------------------------------------------------------------------------
    def archive_to(self, dest: Path, *, active_jobs: int) -> Path:
        """Copy a stable case. Refuses while jobs are active; databases are copied through
        SQLite's backup API so the copy is consistent."""
        if active_jobs:
            raise CaseError(f"{active_jobs} job(s) are still active in this case. Wait for them or cancel them first.")
        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()):
            raise CaseError("Archive destination must be empty.")
        if path_within(dest, self.root):
            raise CaseError("Archive destination cannot be inside the case folder.")
        dest.mkdir(parents=True, exist_ok=True)
        self.db.checkpoint()
        self.results.checkpoint()
        for item in self.root.iterdir():
            if item.name in ("case.sqlite", "results.sqlite", "case.lock") or item.name.endswith(("-wal", "-shm")):
                continue
            if item.is_dir():
                shutil.copytree(item, dest / item.name, symlinks=True)
            else:
                shutil.copy2(item, dest / item.name)
        self.db.backup_to(dest / "case.sqlite")
        self.results.backup_to(dest / "results.sqlite")
        self.audit("case.archived", str(dest))
        return dest
