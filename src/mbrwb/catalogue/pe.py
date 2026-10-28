"""Static, read-only inspection of PE/.NET executables.

Nothing here executes the inspected file. It reads headers, the version resource,
the Authenticode certificate table, .NET single-file bundle metadata and the
``*.runtimeconfig.json`` sidecar so the workbench can report version, architecture,
runtime requirements and signature presence for a tool before it is ever run.

Signature *validity* is a separate question answered by :mod:`mbrwb.tools.signature`
on Windows; this module only reports whether a certificate table exists and which
certificate common names it embeds.
"""
from __future__ import annotations

import json
import mmap
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

MACHINES = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x1C4: "arm"}

# The .NET apphost carries this placeholder; in a single-file bundle the 8 bytes in
# front of it hold the bundle header offset (0 for a plain apphost).
_BUNDLE_SIGNATURE = bytes.fromhex("8b1202b96a612038727b930214d7a03213f5b9e6efae3318ee3b2dce24b36aae")

_CLI_FLAGS_ILONLY = 0x1
_CLI_FLAGS_32BITREQUIRED = 0x2


class PEFormatError(ValueError):
    pass


@dataclass
class RuntimeRequirement:
    flavor: str  # "dotnet" (modern .NET), "netfx" (.NET Framework), "native", "unknown"
    frameworks: list[tuple[str, str]] = field(default_factory=list)  # (name, min version)
    tfm: str | None = None
    source: str = ""  # where the requirement was read from

    def describe(self) -> str:
        if self.flavor == "dotnet":
            fw = ", ".join(f"{n} {v}" for n, v in self.frameworks) or "unknown framework"
            return f"Modern .NET ({self.tfm or '?'}): {fw}"
        if self.flavor == "netfx":
            return f".NET Framework ({self.tfm or 'v4.x'})"
        if self.flavor == "native":
            return "Native executable (no .NET runtime detected)"
        return "Unknown runtime"


@dataclass
class PEInfo:
    path: str
    machine: str
    is_dotnet_assembly: bool
    il_only: bool
    requires_32bit: bool
    product_version: str | None
    file_version: str | None
    original_filename: str | None
    company_name: str | None
    target_framework: str | None
    is_single_file_bundle: bool
    has_certificate_table: bool
    certificate_common_names: list[str]
    runtime: RuntimeRequirement

    @property
    def architecture(self) -> str:
        if self.is_dotnet_assembly and self.il_only and not self.requires_32bit and self.machine == "x86":
            return "anycpu"
        return self.machine

    @property
    def version(self) -> str | None:
        """Display version without the git commit suffix (``2026.10.3+abc`` -> ``2026.10.3``)."""
        v = self.product_version or self.file_version
        return v.split("+", 1)[0].strip() if v else None


def _u16(b: bytes | mmap.mmap, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32(b: bytes | mmap.mmap, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


class _PE:
    def __init__(self, buf: bytes | mmap.mmap):
        self.b = buf
        if len(buf) < 0x40 or buf[0:2] != b"MZ":
            raise PEFormatError("not an MZ executable")
        pe = _u32(buf, 0x3C)
        if pe + 24 > len(buf) or buf[pe : pe + 4] != b"PE\0\0":
            raise PEFormatError("missing PE signature")
        self.machine = _u16(buf, pe + 4)
        nsec = _u16(buf, pe + 6)
        opt_size = _u16(buf, pe + 20)
        opt = pe + 24
        magic = _u16(buf, opt)
        if magic not in (0x10B, 0x20B):
            raise PEFormatError("unknown optional header magic")
        dd = opt + (96 if magic == 0x10B else 112)
        ndd = _u32(buf, dd - 4)
        self.dirs = []
        for i in range(min(ndd, 16)):
            self.dirs.append(struct.unpack_from("<II", buf, dd + 8 * i))
        self.sections = []
        sec = opt + opt_size
        for i in range(nsec):
            o = sec + 40 * i
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", buf, o + 8)
            self.sections.append((va, max(vsize, rsize), rptr, rsize))

    def dir(self, idx: int) -> tuple[int, int]:
        return self.dirs[idx] if idx < len(self.dirs) else (0, 0)

    def rva_to_off(self, rva: int) -> int | None:
        for va, size, rptr, rsize in self.sections:
            if va <= rva < va + size:
                off = rptr + (rva - va)
                return off if rva - va < rsize else None
        return None


def _read_version_resource(pe: _PE) -> dict[str, str]:
    rva, size = pe.dir(2)
    if not rva:
        return {}
    base = pe.rva_to_off(rva)
    if base is None:
        return {}
    b = pe.b

    def entries(dir_off: int):
        n_named = _u16(b, dir_off + 12)
        n_id = _u16(b, dir_off + 14)
        for i in range(n_named + n_id):
            e = dir_off + 16 + 8 * i
            name, target = struct.unpack_from("<II", b, e)
            yield name, target

    # Level 1: type == RT_VERSION (16)
    for name, target in entries(base):
        if name == 16 and target & 0x80000000:
            lvl2 = base + (target & 0x7FFFFFFF)
            for _, t2 in entries(lvl2):
                lvl3 = base + (t2 & 0x7FFFFFFF) if t2 & 0x80000000 else None
                if lvl3 is None:
                    continue
                for _, t3 in entries(lvl3):
                    data_entry = base + t3
                    drva, dsize = struct.unpack_from("<II", b, data_entry)
                    doff = pe.rva_to_off(drva)
                    if doff is None:
                        continue
                    return _parse_version_strings(bytes(b[doff : doff + dsize]))
    return {}


def _parse_version_strings(blob: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("ProductVersion", "FileVersion", "OriginalFilename", "CompanyName", "InternalName", "ProductName"):
        k = key.encode("utf-16le") + b"\0\0"
        i = blob.find(k)
        if i < 0:
            continue
        j = i + len(k)
        # Value is DWORD aligned relative to the blob start.
        while j % 4:
            j += 1
        end = j
        while end + 1 < len(blob) and blob[end : end + 2] != b"\0\0":
            end += 2
        try:
            out[key] = blob[j:end].decode("utf-16le").strip()
        except UnicodeDecodeError:
            continue
    return out


def _certificate_names(pe: _PE) -> tuple[bool, list[str]]:
    off, size = pe.dir(4)  # security directory uses a *file offset*, not an RVA
    if not off or not size or off + size > len(pe.b):
        return False, []
    blob = bytes(pe.b[off : off + size])
    names: list[str] = []
    for m in re.finditer(rb"\x06\x03\x55\x04\x03[\x0c\x13]([\x01-\x7f])", blob):
        n = m.group(1)[0]
        try:
            cn = blob[m.end() : m.end() + n].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if cn not in names:
            names.append(cn)
    return True, names


def _clr_info(pe: _PE) -> tuple[bool, bool, bool, str | None]:
    rva, size = pe.dir(14)
    if not rva:
        return False, False, False, None
    off = pe.rva_to_off(rva)
    if off is None:
        return False, False, False, None
    flags = _u32(pe.b, off + 16)
    # Target framework attribute strings live in the #Blob heap as UTF-8.
    meta_rva, meta_size = struct.unpack_from("<II", pe.b, off + 8)
    moff = pe.rva_to_off(meta_rva)
    tfm = None
    if moff is not None:
        meta = bytes(pe.b[moff : moff + meta_size])
        m = re.search(rb"\.(NETFramework|NETCoreApp|NETStandard),Version=v([0-9.]+)", meta)
        if m:
            tfm = f".{m.group(1).decode()},Version=v{m.group(2).decode()}"
    return True, bool(flags & _CLI_FLAGS_ILONLY), bool(flags & _CLI_FLAGS_32BITREQUIRED), tfm


def _bundle_runtimeconfig(buf: bytes | mmap.mmap) -> tuple[bool, dict | None]:
    idx = buf.find(_BUNDLE_SIGNATURE)
    if idx < 8:
        return False, None
    header_off = struct.unpack_from("<q", buf, idx - 8)[0]
    if header_off <= 0 or header_off >= len(buf):
        return False, None
    try:
        major, _minor, _count = struct.unpack_from("<IIi", buf, header_off)
        p = header_off + 12
        # 7-bit encoded length prefix of the bundle id
        length, shift = 0, 0
        while True:
            byte = buf[p]
            p += 1
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        p += length
        if major < 2:
            return True, None
        _deps_off, _deps_size, rc_off, rc_size = struct.unpack_from("<qqqq", buf, p)
        if rc_off and rc_size and rc_off + rc_size <= len(buf):
            return True, json.loads(bytes(buf[rc_off : rc_off + rc_size]).decode("utf-8-sig"))
        return True, None
    except (struct.error, IndexError, ValueError):
        return True, None


def runtime_from_config(cfg: dict, source: str) -> RuntimeRequirement:
    opts = cfg.get("runtimeOptions", {})
    fws: list[tuple[str, str]] = []
    if "framework" in opts:
        fws.append((opts["framework"].get("name", "?"), opts["framework"].get("version", "?")))
    for fw in opts.get("frameworks", []) or []:
        fws.append((fw.get("name", "?"), fw.get("version", "?")))
    if not fws and opts.get("includedFrameworks"):
        return RuntimeRequirement("dotnet", [], opts.get("tfm"), source + " (self-contained)")
    return RuntimeRequirement("dotnet", fws, opts.get("tfm"), source)


def inspect_pe(path: str | Path, sidecar_runtimeconfig: str | Path | None = None) -> PEInfo:
    """Inspect ``path`` without executing it.

    ``sidecar_runtimeconfig`` defaults to ``<stem>.runtimeconfig.json`` next to the file.
    For an apphost ``X.exe`` the managed assembly is ``X.dll``; callers may inspect both.
    """
    path = Path(path)
    with open(path, "rb") as fh:
        size = path.stat().st_size
        if size == 0:
            raise PEFormatError("empty file")
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            pe = _PE(buf)
            ver = _read_version_resource(pe)
            has_cert, cns = _certificate_names(pe)
            is_net, il_only, req32, tfm = _clr_info(pe)
            bundle, bundle_cfg = _bundle_runtimeconfig(buf)
            machine = MACHINES.get(pe.machine, hex(pe.machine))

    rc_path = Path(sidecar_runtimeconfig) if sidecar_runtimeconfig else path.with_suffix("").with_suffix(".runtimeconfig.json")
    if not rc_path.name.endswith(".runtimeconfig.json"):
        rc_path = path.parent / (path.stem + ".runtimeconfig.json")
    runtime: RuntimeRequirement
    if bundle_cfg:
        runtime = runtime_from_config(bundle_cfg, "embedded single-file bundle runtimeconfig")
    elif rc_path.exists():
        try:
            runtime = runtime_from_config(json.loads(rc_path.read_text("utf-8-sig")), rc_path.name)
        except (OSError, ValueError):
            runtime = RuntimeRequirement("unknown", source=f"unreadable {rc_path.name}")
    elif is_net and tfm and tfm.startswith(".NETFramework"):
        runtime = RuntimeRequirement("netfx", [(".NET Framework", tfm.split("=v", 1)[1])], tfm, "TargetFrameworkAttribute")
    elif is_net and tfm and tfm.startswith(".NETCoreApp"):
        runtime = RuntimeRequirement("dotnet", [("Microsoft.NETCore.App", tfm.split("=v", 1)[1])], tfm, "TargetFrameworkAttribute (no runtimeconfig found)")
    elif is_net:
        runtime = RuntimeRequirement("unknown", source="managed assembly without target framework attribute")
    else:
        runtime = RuntimeRequirement("native", source="no CLR header")

    return PEInfo(
        path=str(path),
        machine=machine,
        is_dotnet_assembly=is_net,
        il_only=il_only,
        requires_32bit=req32,
        product_version=ver.get("ProductVersion"),
        file_version=ver.get("FileVersion"),
        original_filename=ver.get("OriginalFilename"),
        company_name=ver.get("CompanyName"),
        target_framework=tfm,
        is_single_file_bundle=bundle,
        has_certificate_table=has_cert,
        certificate_common_names=cns,
        runtime=runtime,
    )


_OPTION_RE = re.compile(r"--?[A-Za-z][A-Za-z0-9]*")


def dotnet_user_strings(data: bytes, min_chars: int = 2) -> list[str]:
    """UTF-16LE printable runs; the .NET #US heap stores literal strings this way."""
    pat = rb"(?:[\x20-\x7e]\x00){%d,}" % min_chars
    return [m.decode("utf-16le") for m in re.findall(pat, data)]


def static_option_inventory(data: bytes) -> dict[str, str]:
    """Option names found as standalone literals in a managed assembly, with the literal that
    immediately follows (usually its help text). Static evidence only: it proves the string is
    present in this exact build, not that the option behaves as described."""
    strs = dotnet_user_strings(data)
    out: dict[str, str] = {}
    for i, s in enumerate(strs):
        if _OPTION_RE.fullmatch(s):
            nxt = strs[i + 1] if i + 1 < len(strs) else ""
            if s not in out or (not out[s] and nxt):
                out[s] = nxt
    return out
