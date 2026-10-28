"""Safe extraction of tool archives into a staging directory.

Rejected: absolute paths, drive letters, UNC prefixes, ``..`` components, NTFS alternate
stream syntax, reserved device names, symlink entries, more than ``max_members`` entries,
total uncompressed size above ``max_total_bytes``, per-member compression ratios above
``max_ratio`` (zip bombs), and any pre-existing link/reparse point inside staging.
Extraction never overwrites outside the staging root; the caller validates the expected
executable layout and then promotes the staging folder atomically.
"""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event

RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class UnsafeArchive(ValueError):
    pass


class ExtractionCancelled(Exception):
    pass


@dataclass
class ExtractionReport:
    members: int
    total_bytes: int
    files: list[str]


def _safe_member_path(name: str) -> PurePosixPath:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or raw.startswith("//"):
        raise UnsafeArchive(f"absolute path in archive: {name!r}")
    p = PurePosixPath(raw)
    parts = [x for x in p.parts if x not in ("", ".")]
    if not parts:
        raise UnsafeArchive(f"empty member name: {name!r}")
    for part in parts:
        if part == "..":
            raise UnsafeArchive(f"path traversal in archive: {name!r}")
        if ":" in part:
            raise UnsafeArchive(f"drive letter or alternate data stream in archive path: {name!r}")
        if part.split(".")[0].lower() in RESERVED:
            raise UnsafeArchive(f"reserved device name in archive path: {name!r}")
        if part.endswith((" ", ".")) and part not in (".",):
            raise UnsafeArchive(f"path component with trailing dot/space: {name!r}")
        if any(ord(c) < 32 for c in part):
            raise UnsafeArchive(f"control character in archive path: {name!r}")
    return PurePosixPath(*parts)


def _is_link_entry(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def is_reparse_or_link(path: Path) -> bool:
    """True for symlinks, junctions and other reparse points (Windows) or symlinks (POSIX)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def safe_extract_zip(
    archive: str | Path,
    staging: str | Path,
    *,
    max_members: int = 20000,
    max_total_bytes: int = 4 * 1024**3,
    max_ratio: int = 200,
    cancel: Event | None = None,
) -> ExtractionReport:
    archive, staging = Path(archive), Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    root = staging.resolve()
    if any(staging.iterdir()):
        raise UnsafeArchive("staging directory is not empty")
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > max_members:
            raise UnsafeArchive(f"archive has {len(infos)} members (limit {max_members})")
        total = 0
        planned: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        seen: set[str] = set()
        for info in infos:
            rel = _safe_member_path(info.filename)
            if _is_link_entry(info):
                raise UnsafeArchive(f"link entry in archive: {info.filename!r}")
            key = rel.as_posix().lower()
            if key in seen and not info.is_dir():
                raise UnsafeArchive(f"duplicate member (case-insensitive): {info.filename!r}")
            seen.add(key)
            total += info.file_size
            if total > max_total_bytes:
                raise UnsafeArchive("archive expands beyond the size limit")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > max_ratio and info.file_size > 10 * 1024**2:
                raise UnsafeArchive(f"suspicious compression ratio for {info.filename!r}")
            planned.append((info, rel))
        written: list[str] = []
        extracted = 0
        for info, rel in planned:
            if cancel is not None and cancel.is_set():
                raise ExtractionCancelled()
            dest = root.joinpath(*rel.parts)
            # every existing ancestor must be a real directory inside staging
            anc = dest.parent
            while True:
                if anc != root and is_reparse_or_link(anc):
                    raise UnsafeArchive(f"link/reparse point inside staging at {anc}")
                if anc == root or anc == anc.parent:
                    break
                anc = anc.parent
            if not str(dest.resolve() if dest.exists() else dest).startswith(str(root)):
                raise UnsafeArchive(f"member escapes staging: {info.filename!r}")
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or is_reparse_or_link(dest):
                raise UnsafeArchive(f"refusing to overwrite existing path {dest}")
            # stream with an actual-size guard (headers can lie)
            remaining = info.file_size
            with z.open(info) as src, open(dest, "xb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    extracted += len(chunk)
                    if remaining < 0 or extracted > max_total_bytes:
                        raise UnsafeArchive(f"member {info.filename!r} expands beyond its declared size")
                    out.write(chunk)
            written.append(rel.as_posix())
    return ExtractionReport(members=len(planned), total_bytes=extracted, files=written)


def remove_tree(path: Path) -> None:
    """Remove a staging/install directory without following links out of it."""
    if not path.exists():
        return
    if is_reparse_or_link(path):
        path.unlink()
        return
    shutil.rmtree(path)
