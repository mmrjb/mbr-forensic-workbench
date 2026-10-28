"""Controlled verification of an installed CLI tool version: run the trusted executable with
``--help`` (argument list, no shell, from its installation folder, short timeout), store the
exact output with the executable hash, and compare the options it lists with the adapter
profile. A match upgrades that exact executable hash to ``verified_help``; a mismatch is
recorded and the profile stays unverified for it.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from ..adapters.registry import get_adapter
from ..domain.models import InstallationRef
from ..storage.case_store import new_id
from ..storage.db import Database, utcnow

_FLAG = re.compile(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9]*)")


def help_flags(text: str) -> set[str]:
    return {m.group(1).lower() for m in _FLAG.finditer(text)}


def capture_and_compare(app_db: Database, inst: InstallationRef, timeout: int = 30) -> dict:
    exe = Path(inst.executable)
    if not exe.is_file():
        return {"result": "error", "detail": "executable missing"}
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run([str(exe), "--help"], cwd=str(exe.parent), capture_output=True, timeout=timeout, shell=False,
                           stdin=subprocess.DEVNULL, creationflags=flags)
        text = (p.stdout + b"\n" + p.stderr).decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"result": "error", "detail": f"{type(exc).__name__}: {exc}"}
    adapter = get_adapter(inst.tool_id)
    found = help_flags(text)
    missing: list[str] = []
    profile_id = None
    if adapter is not None and adapter.profiles:
        prof = adapter.select_profile(inst.version) or adapter.profiles[0]
        profile_id = prof.profile_id
        modelled = {o.flag.lower() for o in prof.options} | {i.flag.lower() for i in prof.inputs} | {m.flag.lower() for m in prof.managed_outputs}
        missing = sorted(f for f in modelled if f not in found)
    result = "compatible" if adapter is not None and not missing and found else "incompatible"
    detail = "all modelled options are listed by the tool's help" if result == "compatible" else f"options not listed by help: {', '.join(missing) or 'help output unrecognised'}"
    app_db.execute("""INSERT INTO verification_records(id, tool_id, installation_id, exe_sha256, version, method, profile_id, result, detail_json, help_text, help_sha256, checked_utc)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (new_id("ver"), inst.tool_id, inst.id, inst.exe_sha256, inst.version, "help_capture", profile_id, result,
                    json.dumps({"missing": missing, "exit_code": p.returncode, "flags_found": sorted(found)}), text[:200000],
                    hashlib.sha256(text.encode()).hexdigest(), utcnow()))
    return {"result": result, "detail": detail, "missing": missing}
