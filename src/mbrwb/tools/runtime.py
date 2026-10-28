"""Detect installed .NET runtimes without running anything, and compare them with a tool's
requirement read from its runtimeconfig (see :mod:`mbrwb.catalogue.pe`).

Modern .NET: shared frameworks live in ``<dotnet root>/shared/<FrameworkName>/<version>``.
Roots checked: ``DOTNET_ROOT`` (and ``DOTNET_ROOT_X64``), ``%ProgramFiles%\\dotnet`` on
Windows, ``/usr/share/dotnet``, ``/usr/lib64/dotnet``, ``/usr/lib/dotnet`` elsewhere.
Roll-forward follows the default ``Minor`` policy: any installed version with the same major
and a version >= the requested one satisfies the requirement.

.NET Framework: the ``Release`` value under ``HKLM\\SOFTWARE\\Microsoft\\NET Framework
Setup\\NDP\\v4\\Full`` (461808 = 4.7.2, 528040 = 4.8).

A self-contained workbench build does not satisfy a child tool's runtime requirement; this
module only reports what the *machine* has.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..catalogue.catalogue import load_catalogue
from ..catalogue.pe import RuntimeRequirement

NETFX_RELEASE = {"4.7.2": 461808, "4.8": 528040, "4.8.1": 533320}


@dataclass
class RuntimeInventory:
    frameworks: dict[str, list[str]] = field(default_factory=dict)  # name -> versions
    roots: list[str] = field(default_factory=list)
    netfx_release: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RuntimeAssessment:
    ok: bool
    requirement: str
    missing: list[str]
    guidance: list[str]
    satisfied_by: list[str]


def _candidate_roots() -> list[Path]:
    roots = []
    for var in ("DOTNET_ROOT_X64", "DOTNET_ROOT"):
        v = os.environ.get(var)
        if v:
            roots.append(Path(v))
    if sys.platform == "win32":
        for var in ("ProgramW6432", "ProgramFiles"):
            v = os.environ.get(var)
            if v:
                roots.append(Path(v) / "dotnet")
    else:
        roots += [Path("/usr/share/dotnet"), Path("/usr/lib64/dotnet"), Path("/usr/lib/dotnet"), Path.home() / ".dotnet"]
    seen, out = set(), []
    for r in roots:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _parse_version(v: str) -> tuple[int, ...]:
    nums = []
    for part in v.split("-")[0].split("."):
        try:
            nums.append(int(part))
        except ValueError:
            break
    return tuple(nums)


def scan_runtimes() -> RuntimeInventory:
    inv = RuntimeInventory()
    for root in _candidate_roots():
        shared = root / "shared"
        if not shared.is_dir():
            continue
        inv.roots.append(str(root))
        for fw in shared.iterdir():
            if fw.is_dir():
                vers = [v.name for v in fw.iterdir() if v.is_dir() and _parse_version(v.name)]
                inv.frameworks.setdefault(fw.name, [])
                for v in vers:
                    if v not in inv.frameworks[fw.name]:
                        inv.frameworks[fw.name].append(v)
    for k in inv.frameworks:
        inv.frameworks[k].sort(key=_parse_version)
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
                inv.netfx_release = int(winreg.QueryValueEx(key, "Release")[0])
        except OSError:
            inv.notes.append(".NET Framework 4.x release key not found")
    else:
        inv.notes.append(".NET Framework detection is Windows-only")
    return inv


def assess(req: RuntimeRequirement, inv: RuntimeInventory, *, min_netfx: str = "4.7.2") -> RuntimeAssessment:
    links = load_catalogue().runtime_links
    if req.flavor == "dotnet":
        missing, ok_by, guidance = [], [], []
        for name, version in req.frameworks:
            want = _parse_version(version)
            have = [v for v in inv.frameworks.get(name, []) if _parse_version(v)[:1] == want[:1] and _parse_version(v) >= want]
            if have:
                ok_by.append(f"{name} {have[-1]}")
            else:
                missing.append(f"{name} {version} (x64)")
        if missing:
            desktop = any("WindowsDesktop" in m for m in missing)
            major = req.frameworks[0][1].split(".")[0] if req.frameworks else "10"
            guidance.append(f"Install the .NET {major} {'Desktop ' if desktop else ''}Runtime (x64) from {links.get('net10')}")
            guidance.append("Offline: copy the official runtime installer from a connected machine and run it with administrator approval.")
        return RuntimeAssessment(not missing, req.describe(), missing, guidance, ok_by)
    if req.flavor == "netfx":
        need = NETFX_RELEASE.get(min_netfx, 461808)
        if inv.netfx_release is None:
            return RuntimeAssessment(False, req.describe(), [f".NET Framework {min_netfx}+"], [f"Install .NET Framework {min_netfx} or newer: {links.get('netfx472')}"], [])
        if inv.netfx_release >= need:
            return RuntimeAssessment(True, req.describe(), [], [], [f".NET Framework release {inv.netfx_release}"])
        return RuntimeAssessment(False, req.describe(), [f".NET Framework {min_netfx}+ (found release {inv.netfx_release})"],
                                 [f"Install .NET Framework {min_netfx} or newer: {links.get('netfx472')}"], [])
    if req.flavor == "native":
        return RuntimeAssessment(True, req.describe(), [], [], ["native executable"])
    return RuntimeAssessment(False, req.describe(), ["unknown runtime requirement"], ["Re-import the complete tool archive including its .runtimeconfig.json."], [])
