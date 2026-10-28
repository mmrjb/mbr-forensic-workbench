"""Child-process execution with whole-tree lifecycle control.

* argv list, ``shell=False``, stdin closed, no console window on Windows.
* stdout/stderr are drained continuously by two threads into raw log files (exact bytes)
  and a bounded decoded preview, so a chatty tool can never deadlock on a full pipe.
* Windows: the child is assigned to a Job Object created with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; cancel/timeout call ``TerminateJobObject`` (the whole
  tree), and if the workbench itself dies the OS closes the handle and kills the tree.
  ``subprocess`` cannot create the child suspended, so there is a short window between
  creation and assignment in which a grandchild could escape; the EZ tools do not spawn
  children at startup. This limitation is documented in ARCHITECTURE.md.
* POSIX: the child starts a new session; cancel/timeout signal the whole process group
  (SIGTERM, then SIGKILL after a grace period).
"""
from __future__ import annotations

import collections
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ..domain.models import Invocation, ProcessResult
from ..storage.db import utcnow

LineCallback = Callable[[str, str], None]  # (stream, line)


class _WinJob:  # pragma: no cover - exercised on Windows only
    def __init__(self):
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32 = k32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.EXTENDED = EXTENDED
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def assign(self, process_handle: int) -> None:
        from ctypes import wintypes

        self.k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if not self.k32.AssignProcessToJobObject(self.handle, int(process_handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def peak_memory(self) -> int | None:
        info = self.EXTENDED()
        self.k32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        if self.k32.QueryInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            return int(info.PeakJobMemoryUsed)
        return None

    def terminate(self, code: int = 1) -> None:
        self.k32.TerminateJobObject(self.handle, code)

    def close(self) -> None:
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class RunningProcess:
    """One launched child process tree."""

    def __init__(self, inv: Invocation, stdout_path: Path, stderr_path: Path, *, on_line: LineCallback | None = None,
                 preview_lines: int = 2000):
        self.inv = inv
        self.stdout_path = Path(stdout_path)
        self.stderr_path = Path(stderr_path)
        self.on_line = on_line
        self._tails = {"stdout": collections.deque(maxlen=preview_lines), "stderr": collections.deque(maxlen=preview_lines)}
        self._popen: subprocess.Popen | None = None
        self._job: _WinJob | None = None
        self._threads: list[threading.Thread] = []
        self._cancel = threading.Event()
        self._timed_out = False
        self.launch_error: str | None = None
        self.started_utc = utcnow()
        self.peak_memory: int | None = None

    @property
    def pid(self) -> int | None:
        return self._popen.pid if self._popen else None

    def start(self) -> None:
        env = os.environ.copy()
        env.update(self.inv.env_overrides)
        kwargs: dict = dict(cwd=self.inv.cwd or None, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False, close_fds=True)
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._popen = subprocess.Popen(self.inv.argv, **kwargs)
        except OSError as exc:
            self.launch_error = f"{type(exc).__name__}: {exc}"
            self.stdout_path.write_bytes(b"")
            self.stderr_path.write_text(f"[workbench] could not start process: {self.launch_error}\n", encoding="utf-8")
            return
        if sys.platform == "win32":  # pragma: no cover
            try:
                self._job = _WinJob()
                self._job.assign(self._popen._handle)  # type: ignore[attr-defined]
            except OSError as exc:
                self._tails["stderr"].append(f"[workbench] warning: process tree containment unavailable ({exc})")
        for name, stream, path in (("stdout", self._popen.stdout, self.stdout_path), ("stderr", self._popen.stderr, self.stderr_path)):
            t = threading.Thread(target=self._pump, args=(name, stream, path), name=f"pump-{name}-{self._popen.pid}", daemon=True)
            t.start()
            self._threads.append(t)

    def _pump(self, name: str, stream, path: Path) -> None:
        with open(path, "ab") as out:
            for raw in iter(stream.readline, b""):
                out.write(raw)
                out.flush()
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._tails[name].append(line)
                if self.on_line:
                    try:
                        self.on_line(name, line)
                    except Exception:  # never let a UI callback break the pump
                        pass
        stream.close()

    def cancel(self) -> None:
        self._cancel.set()
        self._kill_tree()

    def _kill_tree(self) -> None:
        p = self._popen
        if p is None or p.poll() is not None and self._job is None:
            return
        if sys.platform == "win32":  # pragma: no cover
            if self._job is not None:
                self._job.terminate(1)
            else:
                p.kill()
            return
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if p.poll() is not None:
                break
            time.sleep(0.05)
        try:
            os.killpg(p.pid, signal.SIGKILL)  # also reaps grandchildren that ignored SIGTERM
        except (ProcessLookupError, PermissionError):
            pass

    def wait(self, timeout_s: float | None = None) -> ProcessResult:
        if self._popen is None:
            return ProcessResult(None, self.started_utc, utcnow(), launch_error=self.launch_error or "not started",
                                 stdout_path=str(self.stdout_path), stderr_path=str(self.stderr_path))
        deadline = time.monotonic() + timeout_s if timeout_s else None
        while True:
            try:
                self._popen.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if self._cancel.is_set():
                    self._kill_tree()
                if deadline and time.monotonic() > deadline:
                    self._timed_out = True
                    self._kill_tree()
        if self._cancel.is_set() or self._timed_out:
            self._kill_tree()  # the parent may have exited while descendants live on
        for t in self._threads:
            t.join(timeout=10)
        if self._job is not None:  # pragma: no cover
            self.peak_memory = self._job.peak_memory()
            self._job.close()
        return ProcessResult(
            exit_code=self._popen.returncode,
            started_utc=self.started_utc,
            ended_utc=utcnow(),
            cancelled=self._cancel.is_set(),
            timed_out=self._timed_out,
            stdout_path=str(self.stdout_path),
            stderr_path=str(self.stderr_path),
            stdout_tail=list(self._tails["stdout"])[-400:],
            stderr_tail=list(self._tails["stderr"])[-400:],
            pid=self._popen.pid,
        )

    def preview(self, stream: str) -> list[str]:
        return list(self._tails[stream])


def run(inv: Invocation, stdout_path: Path, stderr_path: Path, *, timeout_s: float | None = None, on_line: LineCallback | None = None,
        cancel_event: threading.Event | None = None) -> ProcessResult:
    rp = RunningProcess(inv, stdout_path, stderr_path, on_line=on_line)
    rp.start()
    if cancel_event is not None:
        def watch():
            while rp._popen is not None and rp._popen.poll() is None:
                if cancel_event.wait(0.1):
                    rp.cancel()
                    return
        threading.Thread(target=watch, daemon=True).start()
    return rp.wait(timeout_s)
