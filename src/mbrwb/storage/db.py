"""SQLite access with an explicit threading policy.

Policy:
* one connection per (database file, thread), created lazily and kept in thread-local storage;
* WAL journal so readers on other threads never block on a writer;
* every write goes through :meth:`Database.write`, which serialises writers in-process with a
  lock and wraps the work in a single transaction (commit or rollback as a unit);
* schema changes only through numbered migrations recorded in ``schema_migrations``.

Evidence SQLite databases are never opened through this class; see
:func:`open_evidence_readonly` for read-only, immutable access to copies.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

Migration = tuple[int, str, Callable[[sqlite3.Connection], None] | str]


def utcnow() -> str:
    """UTC timestamp for application metadata (analysis-workstation clock)."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Database:
    def __init__(self, path: str | Path, migrations: Sequence[Migration], *, label: str = "db"):
        self.path = Path(path)
        self.label = label
        self._migrations = list(migrations)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # -- connections -----------------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
            with self._conns_lock:
                self._all_conns.append(c)
        return c

    def close(self) -> None:
        with self._conns_lock:
            for c in self._all_conns:
                with contextlib.suppress(sqlite3.Error):
                    c.close()
            self._all_conns.clear()
        self._local = threading.local()

    # -- reads -----------------------------------------------------------------------
    def query(self, sql: str, params: Sequence | dict = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence | dict = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence | dict = ()):
        row = self.conn().execute(sql, params).fetchone()
        return None if row is None else row[0]

    # -- writes ----------------------------------------------------------------------
    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction. Nested use on the same thread joins the outer one."""
        with self._write_lock:
            c = self.conn()
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                c.execute("BEGIN IMMEDIATE")
            self._local.depth = depth + 1
            try:
                yield c
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    c.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    c.execute("COMMIT")

    def execute(self, sql: str, params: Sequence | dict = ()) -> sqlite3.Cursor:
        with self.write() as c:
            return c.execute(sql, params)

    # -- schema ----------------------------------------------------------------------
    def schema_version(self) -> int:
        c = self.conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_utc TEXT NOT NULL)")
        v = c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        return int(v or 0)

    def migrate(self) -> None:
        current = self.schema_version()
        known = max((m[0] for m in self._migrations), default=0)
        if current > known:
            raise RuntimeError(
                f"{self.label} schema version {current} is newer than this application supports ({known}). "
                "Open it with a newer workbench build."
            )
        for version, name, step in sorted(self._migrations, key=lambda m: m[0]):
            if version <= current:
                continue
            with self.write() as c:
                if isinstance(step, str):
                    for stmt in _split_sql(step):
                        c.execute(stmt)
                else:
                    step(c)
                c.execute("INSERT INTO schema_migrations(version, name, applied_utc) VALUES (?,?,?)", (version, name, utcnow()))

    def checkpoint(self) -> None:
        """Fold the WAL into the main file (used before archival/backup)."""
        with self._write_lock:
            self.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def backup_to(self, dest: str | Path) -> None:
        """Consistent online copy through SQLite's backup API."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            out = sqlite3.connect(str(dest))
            try:
                self.conn().backup(out)
            finally:
                out.close()


def _split_sql(script: str) -> list[str]:
    parts, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        if line.rstrip().endswith(";") and sqlite3.complete_statement("\n".join(buf)):
            stmt = "\n".join(buf).strip()
            if stmt.strip(";").strip():
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def open_evidence_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an evidence SQLite file strictly read-only and immutable (no WAL/journal writes).

    The workbench itself does not parse evidence databases (SQLECmd and the other parsers do);
    this exists for safe inspection such as schema previews of a *working copy*.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def quote_ident(name: str) -> str:
    """Quote an identifier the application generated itself (table/column names)."""
    if not name or "\x00" in name:
        raise ValueError("invalid identifier")
    return '"' + name.replace('"', '""') + '"'
