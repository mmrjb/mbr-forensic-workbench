"""Build static verification evidence from official EZ Tools archives.

Usage:
    python scripts/inspect_tool_archives.py <dir-with-official-zips> [--out src/mbrwb/catalogue/data/static_evidence.json]

The script only *reads* the archives (zip directory, PE headers, version resources,
runtimeconfig and .NET user strings). It never extracts executables to disk and never
runs them. Its output records exactly which build (archive SHA-256, binary SHA-256,
ProductVersion) each adapter option inventory was taken from, so the catalogue can
say "options statically verified against build X" rather than "verified".
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mbrwb.catalogue.pe import inspect_pe, static_option_inventory  # noqa: E402

# archive file name -> (tool id, managed assembly or exe path inside the archive)
ARCHIVES = {
    "AmcacheParser.zip": ("amcacheparser", "AmcacheParser.dll"),
    "AppCompatCacheParser.zip": ("appcompatcacheparser", "AppCompatCacheParser.dll"),
    "bstrings.zip": ("bstrings", "bstrings.dll"),
    "EvtxECmd.zip": ("evtxecmd", "EvtxeCmd/EvtxECmd.dll"),
    "JLECmd.zip": ("jlecmd", "JLECmd.dll"),
    "LECmd.zip": ("lecmd", "LECmd.dll"),
    "MFTECmd.zip": ("mftecmd", "MFTECmd.dll"),
    "PECmd.zip": ("pecmd", "PECmd.dll"),
    "RBCmd.zip": ("rbcmd", "RBCmd.dll"),
    "RecentFileCacheParser.zip": ("recentfilecacheparser", "RecentFileCacheParser.dll"),
    "RECmd.zip": ("recmd", "RECmd/RECmd.dll"),
    "rla.zip": ("rla", "rla.dll"),
    "SBECmd.zip": ("sbecmd", "SBECmd.dll"),
    "SQLECmd.zip": ("sqlecmd", "SQLECmd/SQLECmd.dll"),
    "SrumECmd.zip": ("srumecmd", "SrumECmd.dll"),
    "SumECmd.zip": ("sumecmd", "SumECmd.dll"),
    "VSCMount.zip": ("vscmount", "VSCMount.dll"),
    "WxTCmd.zip": ("wxtcmd", "WxTCmd.dll"),
    "iisGeolocate.zip": ("iisgeolocate", "iisGeolocate/iisGeolocate.dll"),
    "EZViewer.zip": ("ezviewer", "EZViewer/EZViewer.exe"),
    "hasher.zip": ("hasher", "Hasher/Hasher.exe"),
    "JumpListExplorer.zip": ("jumplistexplorer", "JumpListExplorer/JumpListExplorer.exe"),
    "MFTExplorer.zip": ("mftexplorer", "MFTExplorer/MFTExplorer.exe"),
    "RegistryExplorer.zip": ("registryexplorer", "RegistryExplorer/RegistryExplorer.exe"),
    "SDBExplorer.zip": ("sdbexplorer", "SDBExplorer/SDBExplorer.exe"),
    "ShellBagsExplorer.zip": ("shellbagsexplorer", "ShellBagsExplorer/ShellBagsExplorer.exe"),
    "TimelineExplorer.zip": ("timelineexplorer", "TimelineExplorer/TimelineExplorer.exe"),
    "TimeApp.zip": ("timeapp", "TimeApp.exe"),
    "XWFIM.zip": ("xwfim", "XWFIM/XWFIM.exe"),
    "Get-ZimmermanTools.zip": ("get-zimmermantools", "Get-ZimmermanTools.ps1"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_archive(path: Path, flavor: str) -> dict:
    tool_id, main = ARCHIVES[path.name]
    raw = path.read_bytes()
    z = zipfile.ZipFile(io.BytesIO(raw))
    entries = [{"name": i.filename, "size": i.file_size} for i in z.infolist() if not i.is_dir()]
    rec: dict = {
        "tool_id": tool_id,
        "flavor": flavor,
        "archive_name": path.name,
        "archive_sha256": sha256(raw),
        "archive_size": len(raw),
        "entry_count": len(entries),
        "top_level": sorted({e["name"].split("/")[0] for e in entries}),
        "main_member": main,
    }
    data = z.read(main)
    rec["main_member_sha256"] = sha256(data)
    if main.endswith(".ps1"):
        text = data.decode("utf-8-sig", "replace")
        rec["script_has_signature_block"] = "SIG # Begin signature block" in text
        rec["script_lines"] = text.count("\n") + 1
        return rec
    # inspect_pe needs a real file for mmap; write the member to a private temp file
    # (never executed, deleted immediately). The runtimeconfig sidecar comes along.
    with tempfile.TemporaryDirectory() as td:
        member = Path(td) / Path(main).name
        member.write_bytes(data)
        rc_name = main.rsplit(".", 1)[0] + ".runtimeconfig.json"
        if rc_name in z.namelist():
            (Path(td) / Path(rc_name).name).write_bytes(z.read(rc_name))
        info = inspect_pe(member)
    rec.update(
        product_version=info.product_version,
        version=info.version,
        machine=info.machine,
        architecture=info.architecture,
        single_file_bundle=info.is_single_file_bundle,
        runtime={"flavor": info.runtime.flavor, "tfm": info.runtime.tfm, "frameworks": info.runtime.frameworks, "source": info.runtime.source},
        certificate_common_names=info.certificate_common_names,
        has_certificate_table=info.has_certificate_table,
    )
    exe = main.rsplit(".", 1)[0] + ".exe"
    if exe != main and exe in z.namelist():
        rec["apphost_member"] = exe
        rec["apphost_sha256"] = sha256(z.read(exe))
    if main.endswith(".dll"):
        rec["static_options"] = static_option_inventory(data)
    sidecar_dirs = sorted({e["name"].split("/")[1] for e in entries if e["name"].count("/") >= 2})
    if sidecar_dirs:
        rec["sidecar_directories"] = {d: sum(1 for e in entries if e["name"].split("/")[1:2] == [d]) for d in sidecar_dirs}
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("archive_dir", type=Path)
    ap.add_argument("--flavor", default="net10")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "src/mbrwb/catalogue/data/static_evidence.json")
    a = ap.parse_args()
    records = []
    for name in sorted(ARCHIVES, key=str.lower):
        p = a.archive_dir / name
        if not p.exists():
            print(f"missing: {name}", file=sys.stderr)
            continue
        records.append(inspect_archive(p, a.flavor))
        print(f"inspected {name}: {records[-1].get('version')}")
    doc = {
        "schema_version": 1,
        "kind": "static_archive_evidence",
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "Read-only inspection of official archives (zip directory, PE headers, version resource, runtimeconfig, .NET user-string heap). Binaries were NOT executed.",
        "source": "https://download.ericzimmermanstools.com/ (net/ = .NET 10 flavor)",
        "records": records,
    }
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
