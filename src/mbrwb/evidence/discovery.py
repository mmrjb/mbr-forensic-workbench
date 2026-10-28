"""Artifact discovery inside a selected evidence root.

Classification uses file signatures/structure first and names second. A name-only match is
reported with ``basis="filename"`` and is a suggestion the operator can correct.
Traversal never follows symlinks/junctions/reparse points out of the selected root, skips
workbench case folders (so the workbench never rediscovers its own outputs), and is
cancellable.
"""
from __future__ import annotations

import os
import re
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable

from ..storage.case_store import CASE_MARKER

HIVE_NAMES = {"system": "system_hive", "software": "software_hive", "sam": "sam_hive", "security": "security_hive",
              "ntuser.dat": "ntuser_hive", "usrclass.dat": "usrclass_hive", "amcache.hve": "amcache_hive", "default": "registry_hive"}
LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")


@dataclass
class Candidate:
    path: str
    rel_path: str
    artifact_type: str | None
    basis: str  # signature | structure | filename | none
    size: int
    mtime: float
    ctime: float
    atime: float
    host: str | None = None
    user_profile: str | None = None
    volume: str | None = None
    note: str = ""


@dataclass
class DiscoveryReport:
    root: str
    files_inspected: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    unclassified: list[Candidate] = field(default_factory=list)
    unavailable: list[tuple[str, str]] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & 0x400) or (hasattr(entry, "is_junction") and entry.is_junction())
    except OSError:
        return True


def classify(path: Path, head: bytes) -> tuple[str | None, str]:
    name = path.name.lower()
    # signatures / structure
    if head[:8] == b"ElfFile\x00":
        return "evtx_file", "signature"
    if head[4:8] == b"SCCA" or head[:3] == b"MAM":
        return ("prefetch_file", "signature") if name.endswith(".pf") or head[4:8] == b"SCCA" else ("prefetch_file", "structure")
    if head[:4] == b"regf":
        if re.search(r"\.log[12]?$", name):
            return "registry_transaction_log", "signature"
        return HIVE_NAMES.get(name, "registry_hive"), "signature" if name in HIVE_NAMES else "structure"
    if head[:4] in (b"HvLE",) or (re.search(r"\.log[12]$", name) and head[:4] == b"\0\0\0\0"):
        return "registry_transaction_log", "structure"
    if head[:4] == b"FILE" and (name in ("$mft",) or name.endswith("$mft")):
        return "ntfs_mft", "signature"
    if head[:4] == b"FILE":
        return "ntfs_mft", "structure"
    if head[:4] == b"RSTR" or head[:4] == b"RCRD":
        return "ntfs_logfile", "signature"
    if head[3:11] == b"NTFS    ":
        return "ntfs_boot", "signature"
    if head[:4] == b"INDX":
        return "ntfs_i30", "signature"
    if head[:4] == bytes.fromhex("4c000000") and head[4:20] == LNK_CLSID:
        return "lnk_file", "signature"
    if head[:16] == b"SQLite format 3\x00":
        if name == "activitiescache.db":
            return "activitiescache_db", "signature"
        return "sqlite_db", "signature"
    if head[4:8] == bytes.fromhex("efcdab89") and name.endswith((".dat", ".mdb", ".edb")):
        if name == "srudb.dat":
            return "srum_db", "signature"
        if name.endswith(".mdb"):
            return "sum_db", "signature"
        return None, "none"
    if head[:8] == bytes.fromhex("d0cf11e0a1b11ae1") and name.endswith(".automaticdestinations-ms"):
        return "jumplist_file", "signature"
    if name.endswith(".customdestinations-ms"):
        return "jumplist_file", "filename"
    if name.startswith("$i") and len(head) >= 8 and head[:8] in (bytes.fromhex("0100000000000000"), bytes.fromhex("0200000000000000")):
        return "recyclebin_i_file", "signature"
    if name == "info2":
        return "recyclebin_info2", "filename"
    if name == "recentfilecache.bcf":
        return "recentfilecache_bcf", "filename"
    if name.endswith(".sdb") and head[8:12] == b"sdbf":
        return "shim_sdb", "signature"
    # names only (suggestions)
    if name in ("$j", "$usnjrnl_$j", "$usnjrnl:$j") or name.endswith("$j"):
        return "ntfs_usnjrnl_j", "filename"
    if name in ("$secure_$sds", "$sds") or name.endswith("$sds"):
        return "ntfs_sds", "filename"
    if name in ("$logfile",):
        return "ntfs_logfile", "filename"
    if name.endswith(".evtx"):
        return "evtx_file", "filename"
    if name.endswith(".pf"):
        return "prefetch_file", "filename"
    if name.endswith(".lnk"):
        return "lnk_file", "filename"
    if name.endswith("-wal") or name.endswith("-journal") or name.endswith("-shm"):
        return "sqlite_sidecar", "filename"
    if name.endswith(".log") and head.startswith(b"#Software: Microsoft Internet Information Services"):
        return "iis_log", "signature"
    return None, "none"


def infer_context(rel: str, default_host: str | None) -> tuple[str | None, str | None, str | None]:
    """Host/user/volume suggestions from a KAPE-style relative path such as
    ``HOST1/C/Users/alice/NTUSER.DAT`` or ``C/Windows/Prefetch/X.pf``."""
    parts = [p for p in PureWindowsPath(rel.replace("/", "\\")).parts if p not in ("\\",)]
    host, user, volume = default_host, None, None
    for i, p in enumerate(parts[:-1]):
        if re.fullmatch(r"[A-Za-z]:?|[A-Za-z]_?drive", p) or re.fullmatch(r"vss\d+", p, re.I):
            volume = p.rstrip(":").upper() if not p.lower().startswith("vss") else p.upper()
            if i >= 1 and not default_host:
                host = parts[i - 1]
            continue
        if p.lower() == "users" and i + 1 < len(parts) - 1:
            cand = parts[i + 1]
            if cand.lower() not in ("public", "default", "all users", "default user"):
                user = cand
    return host, user, volume


def discover(root: str | Path, *, default_host: str | None = None, cancel: threading.Event | None = None,
             progress: Callable[[int, int], None] | None = None, skip_dirs: list[str] | None = None,
             max_files: int = 2_000_000) -> DiscoveryReport:
    root = Path(root)
    rep = DiscoveryReport(str(root))
    skip = [os.path.normcase(os.path.realpath(s)) for s in (skip_dirs or [])]

    def handle_file(path: Path, rel: str) -> None:
        rep.files_inspected += 1
        try:
            st = path.stat()
            with open(path, "rb") as fh:
                head = fh.read(64)
        except PermissionError as exc:
            rep.unavailable.append((str(path), f"access denied: {exc.strerror}"))
            return
        except OSError as exc:
            rep.unavailable.append((str(path), f"{type(exc).__name__}: {exc.strerror or exc}"))
            return
        atype, basis = classify(path, head)
        host, user, volume = infer_context(rel, default_host)
        c = Candidate(str(path), rel, atype, basis, st.st_size, st.st_mtime, st.st_ctime, st.st_atime, host, user, volume)
        (rep.candidates if atype else rep.unclassified).append(c)

    if root.is_file():
        handle_file(root, root.name)
        return rep
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            rep.cancelled = True
            break
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError as exc:
            rep.unavailable.append((str(d), f"{type(exc).__name__}: {exc.strerror or exc}"))
            continue
        if any(e.name == "case.json" for e in entries) and _is_case_folder(Path(d)):
            rep.excluded.append((str(d), "workbench case folder (not evidence)"))
            continue
        for e in entries:
            p = Path(e.path)
            if _is_link(e):
                rep.excluded.append((e.path, "link/junction/reparse point not followed"))
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                rep.unavailable.append((e.path, "unreadable directory entry"))
                continue
            if is_dir:
                if os.path.normcase(os.path.realpath(e.path)) in skip:
                    rep.excluded.append((e.path, "workbench-owned folder"))
                    continue
                stack.append(p)
            elif e.is_file(follow_symlinks=False):
                if rep.files_inspected >= max_files:
                    rep.excluded.append((e.path, f"file limit {max_files} reached"))
                    continue
                handle_file(p, os.path.relpath(e.path, root))
                if progress and rep.files_inspected % 500 == 0:
                    progress(rep.files_inspected, len(rep.candidates))
            else:
                rep.excluded.append((e.path, "not a regular file"))
    if progress:
        progress(rep.files_inspected, len(rep.candidates))
    return rep


def _is_case_folder(d: Path) -> bool:
    try:
        return CASE_MARKER in (d / "case.json").read_text("utf-8", errors="ignore")[:500]
    except OSError:
        return False


def file_mode_is_regular(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False
