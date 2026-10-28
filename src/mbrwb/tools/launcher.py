"""Launching original GUI tools.

* The verified executable is started from its installation folder with no arguments unless the
  catalogue records a *verified* file-opening argument for that tool (none are verified in the
  2026.10 review, so subjects are offered as copy-path / open-folder instead).
* Each launch is a *launch session* (app DB, and the case DB when a case is open), separate from
  parser jobs. A watcher thread records when the process exits. Closing a GUI tool is never
  reported as a completed analysis.
* The workbench never embeds another application's window and never opens files taken from
  evidence with their registered shell handler.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from ..catalogue.catalogue import ToolEntry
from ..domain.models import InstallationRef
from ..storage.case_store import CaseStore, new_id
from ..storage.db import Database, utcnow


class LaunchError(Exception):
    pass


def launch_gui(app_db: Database, tool: ToolEntry, inst: InstallationRef, *, case: CaseStore | None = None, subject_path: str | None = None,
               purpose: str = "", classification: str = "gui_session") -> str:
    exe = Path(inst.executable)
    if not exe.is_file():
        raise LaunchError(f"{exe} does not exist; re-import {tool.name}.")
    if sys.platform != "win32":
        raise LaunchError(f"{tool.name} is a Windows program; it can only be opened on Windows.")
    args: list[str] = []
    if subject_path and tool.gui_open_argument:
        args = [a.replace("{path}", subject_path) for a in json.loads(tool.gui_open_argument)]
    flags = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    try:
        proc = subprocess.Popen([str(exe), *args], cwd=str(exe.parent), shell=False, close_fds=True, creationflags=flags,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise LaunchError(f"Could not open {tool.name}: {exc}") from exc
    sid = new_id("launch")
    now = utcnow()
    app_db.execute("""INSERT INTO launch_sessions(id, case_id, case_root, tool_id, installation_id, executable, exe_sha256, args_json, purpose, classification,
                      started_utc, pid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sid, case.id if case else None, str(case.root) if case else None, tool.id, inst.id, str(exe), inst.exe_sha256,
                    json.dumps(args), purpose or (f"subject: {subject_path}" if subject_path else ""), classification, now, proc.pid))
    if case is not None:
        case.db.execute("INSERT INTO launch_sessions(id, case_id, tool_id, executable, exe_sha256, args_json, subject_path, started_utc, note) VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, case.id, tool.id, str(exe), inst.exe_sha256, json.dumps(args), subject_path, now,
                         "Launch session only; files created in the tool must be imported explicitly."))
        case.audit("tool.launched", tool.id, {"session": sid, "subject": subject_path})

    def watch():
        code = proc.wait()
        end = utcnow()
        try:
            app_db.execute("UPDATE launch_sessions SET ended_utc=?, exit_code=? WHERE id=?", (end, code, sid))
            if case is not None:
                case.db.execute("UPDATE launch_sessions SET ended_utc=?, exit_code=? WHERE id=?", (end, code, sid))
        except Exception:
            pass
    threading.Thread(target=watch, name=f"watch-{tool.id}", daemon=True).start()
    return sid


def can_launch_here() -> bool:
    return sys.platform == "win32"


def reveal_folder_url(path: str | Path) -> str | None:
    """Folder to show for a path (never the file itself: evidence files are not opened with
    their shell association)."""
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    return str(folder) if folder.exists() else None

