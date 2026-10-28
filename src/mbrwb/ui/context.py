"""Application context shared by all pages: services, current case, background execution and
cross-page navigation. Every long operation runs in the thread pool; results come back to the
GUI thread through queued signals."""
from __future__ import annotations

import collections
import logging
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..catalogue.catalogue import load_catalogue
from ..config.paths import AppPaths
from ..config.settings import SettingsStore
from ..jobs.manager import JobManager
from ..storage.case_store import CaseStore
from ..storage.db import Database, utcnow
from ..storage.schema import APP_MIGRATIONS
from ..tools.runtime import RuntimeInventory, scan_runtimes
from ..tools.store import ToolStore

log = logging.getLogger("mbrwb.ui")


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str, str)  # message, traceback
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        self.cancel = threading.Event()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            res = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # reported to the UI, never swallowed
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
            return
        self.signals.finished.emit(res)


class AppContext(QObject):
    caseChanged = Signal()
    jobChanged = Signal(str, str)  # case_id, job_id
    toolsChanged = Signal()
    themeChanged = Signal(str)
    evidenceChanged = Signal()
    resultsChanged = Signal()
    findingsChanged = Signal()
    navigate = Signal(str, object)  # page key, argument
    notice = Signal(str, str)  # level, text (non-modal banner)

    def __init__(self, paths: AppPaths):
        super().__init__()
        self.paths = paths
        paths.ensure()
        self.settings_store = SettingsStore(paths)
        self.app_db = Database(paths.app_db, APP_MIGRATIONS, label="app")
        s = self.settings_store.settings
        self.catalogue = load_catalogue()
        self.tools = ToolStore(self.app_db, self.settings_store.tools_dir, s.tool_storage.retained_versions, self.catalogue)
        self.jobs = JobManager(self.tools, max_parallel=s.execution.max_parallel_jobs, default_timeout_s=s.execution.default_timeout_minutes * 60,
                               preview_lines=s.execution.log_preview_lines)
        self.case: CaseStore | None = None
        self.other_cases: dict[str, CaseStore] = {}  # cases with jobs still running after switching
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(4, self.pool.maxThreadCount()))
        self._runtimes: RuntimeInventory | None = None
        self._workers: set[Worker] = set()
        self.lines: dict[str, collections.deque] = {}
        self._lines_lock = threading.Lock()
        self.jobs.add_listener(lambda case_id, job_id: self.jobChanged.emit(case_id, job_id))
        self.jobs.add_line_listener(self._on_line)

    @property
    def settings(self):
        return self.settings_store.settings

    # -- background ----------------------------------------------------------------------
    def run_bg(self, fn: Callable, *args, on_done: Callable[[Any], None] | None = None, on_error: Callable[[str, str], None] | None = None,
               **kwargs) -> Worker:
        w = Worker(fn, *args, **kwargs)
        w.setAutoDelete(False)
        # Keep the worker (and its signal object) alive until its result has been delivered on
        # the GUI thread; otherwise Python may collect it first and the queued signal is lost.
        self._workers.add(w)
        if on_done:
            w.signals.finished.connect(on_done)
        w.signals.failed.connect(on_error or (lambda m, tb: (log.error("background task failed: %s\n%s", m, tb), self.notice.emit("error", m))))
        w.signals.finished.connect(lambda *_: self._workers.discard(w))
        w.signals.failed.connect(lambda *_: self._workers.discard(w))
        self.pool.start(w)
        return w

    def _on_line(self, job_id: str, stream: str, line: str) -> None:
        with self._lines_lock:
            dq = self.lines.setdefault(job_id, collections.deque(maxlen=self.settings.execution.log_preview_lines))
            dq.append(("! " if stream == "stderr" else "") + line)

    def job_lines(self, job_id: str) -> list[str]:
        with self._lines_lock:
            return list(self.lines.get(job_id, ()))

    # -- runtimes ------------------------------------------------------------------------
    def runtimes(self, refresh: bool = False) -> RuntimeInventory:
        if self._runtimes is None or refresh:
            self._runtimes = scan_runtimes()
        return self._runtimes

    # -- cases ---------------------------------------------------------------------------
    def open_case(self, store: CaseStore) -> None:
        old = self.case
        if old is not None and old.id != store.id:
            if self.jobs.active_count(old.id):
                self.other_cases[old.id] = old  # keep it open: its jobs continue into their own case
            else:
                old.close()
        self.case = store
        self.other_cases.pop(store.id, None)
        self.app_db.execute("INSERT INTO recent_cases(case_root, case_id, title, last_opened_utc) VALUES (?,?,?,?) "
                            "ON CONFLICT(case_root) DO UPDATE SET case_id=excluded.case_id, title=excluded.title, last_opened_utc=excluded.last_opened_utc",
                            (str(store.root), store.id, store.info.title, utcnow()))
        self.caseChanged.emit()

    def close_case(self) -> None:
        if self.case is not None:
            if self.jobs.active_count(self.case.id):
                self.other_cases[self.case.id] = self.case
            else:
                self.case.close()
        self.case = None
        self.caseChanged.emit()

    def case_for(self, case_id: str) -> CaseStore | None:
        if self.case is not None and self.case.id == case_id:
            return self.case
        return self.other_cases.get(case_id)

    def shutdown(self) -> None:
        self.jobs.shutdown()
        for c in list(self.other_cases.values()):
            c.close()
        if self.case is not None:
            self.case.close()
        self.app_db.close()
