"""Writable per-user locations.

Default (Windows): ``%LOCALAPPDATA%\\MBR Forensic Workbench`` for settings, the app
database, logs and managed tool installations; cases default to a ``Cases`` folder in the
same root. Nothing mutable is written next to the executable unless portable mode is
explicitly enabled (``portable.flag`` next to the executable) and that root is writable.

``MBRWB_HOME`` overrides the root (tests, demos, multiple profiles).
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .product import PRODUCT


@dataclass(frozen=True)
class AppPaths:
    root: Path
    portable: bool = False
    portable_fallback_reason: str | None = None

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def app_db(self) -> Path:
        return self.root / "app.sqlite"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def default_tools_dir(self) -> Path:
        return self.root / "tools"

    @property
    def default_cases_dir(self) -> Path:
        return self.root / "Cases"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def demo_cases_dir(self) -> Path:
        return self.root / "Demo cases"

    def ensure(self) -> None:
        for p in (self.root, self.logs, self.staging, self.downloads):
            p.mkdir(parents=True, exist_ok=True)


def is_writable_dir(path: Path) -> tuple[bool, str]:
    """Create-and-delete probe; returns (ok, reason)."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write-probe-", dir=path)
        os.close(fd)
        os.unlink(probe)
        return True, ""
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _default_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / PRODUCT.data_dir_name
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "mbr-forensic-workbench"


def resolve_paths() -> AppPaths:
    env = os.environ.get("MBRWB_HOME")
    if env:
        return AppPaths(Path(env))
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "portable.flag").exists():
            root = exe_dir / "workbench-data"
            ok, why = is_writable_dir(root)
            if ok:
                return AppPaths(root, portable=True)
            return AppPaths(_default_root(), portable=False, portable_fallback_reason=f"Portable root {root} is not writable ({why}); using the per-user location instead.")
    return AppPaths(_default_root())
