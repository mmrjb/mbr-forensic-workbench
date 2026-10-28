"""Build tiny synthetic PE files for tests (never real tools, never executed).

Produces a PE32 image with one ``.rsrc`` section containing an RT_VERSION resource whose
string table carries ProductVersion/FileVersion. Enough for the static inspector to report a
version; nothing else about it is meaningful.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path


def _u16z(s: str) -> bytes:
    return s.encode("utf-16le") + b"\0\0"


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * ((-len(b)) % 4)


def _version_blob(version: str) -> bytes:
    body = b""
    for key, val in (("ProductVersion", version), ("FileVersion", version.split("+")[0]), ("OriginalFilename", "synthetic.exe")):
        k = _pad4(struct.pack("<HHH", 0, len(val) + 1, 1) + _u16z(key))
        body += _pad4(k + _u16z(val))
    return _pad4(struct.pack("<HHH", 0, 0, 1) + _u16z("VS_VERSION_INFO")) + body


def build_pe(version: str = "2026.10.3+synthetic", machine: int = 0x14C) -> bytes:
    sect_rva, sect_raw = 0x1000, 0x200
    # resource tree: root -> type 16 -> id 1 -> lang 0x409 -> data entry
    ver = _version_blob(version)
    root = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 16, 0x80000000 | 0x18)
    lvl2 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | 0x30)
    lvl3 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x409, 0x48)
    data_off = 0x58
    data_entry = struct.pack("<IIII", sect_rva + data_off, len(ver), 0, 0)
    rsrc = root + lvl2 + lvl3 + data_entry
    rsrc = rsrc.ljust(data_off, b"\0") + ver
    raw_size = (len(rsrc) + 0x1FF) & ~0x1FF
    rsrc = rsrc.ljust(raw_size, b"\0")

    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, 224, 0x0102)
    opt = bytearray(224)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 16, 0x1000)  # entry point (never used)
    struct.pack_into("<I", opt, 28, 0x400000)
    struct.pack_into("<II", opt, 32, 0x1000, 0x200)
    struct.pack_into("<I", opt, 56, sect_rva + raw_size)
    struct.pack_into("<I", opt, 60, sect_raw)
    struct.pack_into("<H", opt, 68, 3)
    struct.pack_into("<I", opt, 92, 16)
    struct.pack_into("<II", opt, 96 + 8 * 2, sect_rva, len(rsrc))
    section = struct.pack("<8sIIIIIIHHI", b".rsrc", raw_size, sect_rva, raw_size, sect_raw, 0, 0, 0, 0, 0x40000040)
    headers = bytes(dos) + b"PE\0\0" + coff + bytes(opt) + section
    return headers.ljust(sect_raw, b"\0") + rsrc


RUNTIMECONFIG = b'{"runtimeOptions":{"tfm":"net10.0","framework":{"name":"Microsoft.NETCore.App","version":"10.0.0"}}}'


def write_cli_tool(folder: Path, stem: str, version: str, *, extra: dict[str, bytes] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.exe").write_bytes(build_pe(version, 0x8664))
    (folder / f"{stem}.dll").write_bytes(build_pe(version))
    (folder / f"{stem}.runtimeconfig.json").write_bytes(RUNTIMECONFIG)
    for name, data in (extra or {}).items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return folder / f"{stem}.exe"


def write_tool_zip(path: Path, stem: str, version: str, *, subdir: str | None = None, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        prefix = f"{subdir}/" if subdir else ""
        z.writestr(f"{prefix}{stem}.exe", build_pe(version, 0x8664))
        z.writestr(f"{prefix}{stem}.dll", build_pe(version))
        z.writestr(f"{prefix}{stem}.runtimeconfig.json", RUNTIMECONFIG)
        for name, data in (extra or {}).items():
            z.writestr(prefix + name, data)
    return path
