"""Generate TOOL_COVERAGE.md from the catalogue, adapters, mappings and static evidence.

Usage: python scripts/coverage_matrix.py > TOOL_COVERAGE.md
Values: Verified | Implemented, unverified | Static evidence only | Not applicable | Blocked: <reason>.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mbrwb.adapters.registry import ADAPTER_CLASSES  # noqa: E402
from mbrwb.catalogue.catalogue import load_catalogue  # noqa: E402
from mbrwb.domain.enums import ToolKind  # noqa: E402
from mbrwb.timeline.mapper import load_mappings  # noqa: E402

TESTED_FAKE_E2E = {"pecmd", "mftecmd", "evtxecmd", "rla"}
NO_WIN = "Blocked: no Windows host in this environment"
NO_EXEC = "Blocked: executing EZ binaries was not permitted in the build session"


def row(t, static, mappings) -> list[str]:
    ev = static.get(t.id)
    cat = f"Verified (official page {load_catalogue().reviewed_utc[:10]})"
    if t.id == "kape":
        imp = "Implemented, unverified (operator folder referenced in place; no KAPE binary available)"
    elif ev:
        imp = "Verified with the official 2026.10 archive (safe extract, layout + version check, promote; not executed)"
    else:
        imp = "Implemented, unverified"
    if t.kind == ToolKind.SCRIPT:
        ver = "Verified (script header 'Updated:' + SHA-256 allowlist)"
    elif ev:
        ver = f"Verified statically: {ev.get('version')} from PE version resource; runtime {', '.join(f[0] + ' ' + f[1] for f in ev['runtime']['frameworks'])}"
    else:
        ver = "Implemented, unverified"
    adapter = ADAPTER_CLASSES.get(t.adapter or "")
    if t.kind == ToolKind.GUI:
        form = "Launcher implemented (no file-open argument verified; copy path / open folder offered)"
        exe = f"Launch session recording tested with a stubbed process; real launch {NO_WIN}"
        index = "Not applicable (opening a GUI tool is not a parse)"
        tl = "Not applicable"
        tests = "test_gui_launch_is_a_session_not_a_job, test_install_every_official_archive"
    elif t.id == "get-zimmermantools":
        form = "Maintenance action (-Dest, -NetVersion, -NoUpdate; reviewed hash)"
        exe = f"Implemented, unverified ({NO_WIN})"
        index = "Not applicable (refreshes the tool inventory)"
        tl = "Not applicable"
        tests = "-"
    elif adapter is not None:
        prof = adapter().profiles[0]
        nflags = len(prof.options) + len(prof.inputs) + len(prof.managed_outputs)
        form = (f"Implemented: {nflags} modelled flags, profile {prof.profile_id}; every flag found in the official binary strings" if t.id != "kape"
                else "Implemented: switches checked against KapeDocs (no binary)")
        if t.id == "kape":
            exe = "Implemented, unverified (no KAPE binary available; KAPE is operator-supplied)"
        elif t.id in TESTED_FAKE_E2E:
            exe = f"Job pipeline tested end-to-end with a FAKE tool; real execution {NO_EXEC}"
        else:
            exe = f"Job pipeline shared and tested; this adapter's real execution {NO_EXEC}"
        if t.id == "vscmount":
            index = "Not applicable (console-only specialist operation)"
        elif t.id == "bstrings":
            index = "Text output (bounded viewer), not tabular"
        elif t.id == "rla":
            index = "Derived hives registered as evidence with parent links (tested with fake tool)"
        else:
            roles = [p for p in prof.output_patterns if p.importable]
            index = f"Implemented: {len(roles)} importable output pattern(s); CSV/JSONL importer tested; real output schema not observed"
        maps = [m for m in mappings.values() if m.tool_id == t.id]
        tl = ("Implemented, unverified against real output: " + ", ".join(f"{m.mapping_id} v{m.version}" for m in maps) + " (column names confirmed in DLL)") if maps else (
            "Not applicable" if t.id in ("vscmount", "bstrings", "rla", "kape") else "No reviewed mapping (raw rows searchable)")
        tests = ("test_misc::test_kape_definitions_are_read_as_data" if t.id == "kape" else
                 "test_adapter_profiles, test_install_every_official_archive" + (", test_jobs, test_journey (fake tool)" if t.id in TESTED_FAKE_E2E else ""))
    else:
        form = "Launch-only specialist entry"
        exe = NO_WIN
        index = "Not applicable"
        tl = "Not applicable"
        tests = "-"
    real = "Blocked: KAPE binary not available" if t.id == "kape" else NO_WIN + "; " + ("binary execution not permitted here" if t.kind != ToolKind.SCRIPT else "PowerShell not available")
    return [t.name, cat, imp, ver, form, exe, index, tl, tests, real]


def main() -> None:
    cat = load_catalogue()
    static = cat.static_evidence
    mappings = load_mappings()
    hdr = ["Tool", "Catalogue", "Import/install", "Version detection", "Form/launcher", "Execution", "Output indexing", "Timeline mapping", "Automated tests", "Real Windows validation"]
    print("# Tool coverage\n")
    print("_Generated by `scripts/coverage_matrix.py` from the reviewed catalogue, adapter registry, timeline mappings and static archive evidence. "
          "Regenerate after any adapter or catalogue change._\n")
    print("Status vocabulary: **Verified** (checked against the stated evidence), **Implemented, unverified** (code exists, not checked against the real tool), "
          "**Not applicable**, **Blocked: reason**. 'Statically verified' means option names and help text were read from the official 2026.10 binary "
          "without executing it. No row is a real Windows validation.\n")
    print("Environment of this build: AlmaLinux 10 (Linux), Python 3.12.14, PySide6 6.10.1. Official archives were downloaded from "
          "download.ericzimmermanstools.com on 2026-10-28 and inspected read-only. Executing them was refused by the build session's safety policy; "
          "there is no Windows host.\n")
    print("| " + " | ".join(hdr) + " |")
    print("|" + "---|" * len(hdr))
    for t in sorted(cat.tools.values(), key=lambda x: (x.kind.value, x.name.lower())):
        print("| " + " | ".join(c.replace("|", "/") for c in row(t, static, mappings)) + " |")
    print("\n## Version families and evidence\n")
    print("| Tool | Inspected build | Archive SHA-256 | Main binary SHA-256 | Signer (embedded) | Runtime |")
    print("|---|---|---|---|---|---|")
    for tid, r in sorted(static.items()):
        signer = "ZimTech LLC" if "ZimTech LLC" in (r.get("certificate_common_names") or []) else ("-" if not r.get("has_certificate_table") else "chain present")
        rt = ", ".join(f[0] + " " + f[1] for f in (r.get("runtime") or {}).get("frameworks") or []) or "-"
        print(f"| {cat.tools[tid].name} | {r.get('product_version') or 'script'} | `{r['archive_sha256'][:16]}...` | `{r['main_member_sha256'][:16]}...` | {signer} | {rt} |")
    print("\n## Dependencies and sidecars\n")
    for t in cat.tools.values():
        if t.sidecars:
            print(f"- **{t.name}**: {'; '.join(t.sidecars)}")
    print("\n## Deliberately not offered\n")
    for tid, cls in sorted(ADAPTER_CLASSES.items()):
        p = cls().profiles[0]
        if p.not_offered:
            print(f"- **{cat.tools[tid].name}**: {', '.join(p.not_offered)}")
    print("\nReasons: `--vss` and live-registry modes are live operations needing administrator rights; `--sync` downloads maps/batch files at run time "
          "and would change dependencies outside the reviewed installation; `--dt` would change the timestamp format the timeline mappings parse.")
    print("\n## What remains for real validation (Windows)\n")
    print("1. Import the official tools (Tools > Import folder or Install all eligible) on Windows 11 x64 with the .NET 10 runtime and Desktop runtime.\n"
          "2. For each CLI tool, run Tools > Verify this version: the workbench captures `--help` from the exact executable and compares every modelled flag; "
          "a match upgrades that hash to *Verified against captured help output*.\n"
          "3. Run `pytest -m real_tools` style checks with genuine artifacts (minimum: PECmd, MFTECmd, EvtxECmd) and confirm that the timeline mappings' "
          "required columns match the real CSV headers; record results in TEST_RESULTS.md.\n"
          "4. Open each GUI tool once from the Tools page and confirm the launch session is recorded.\n"
          "5. Confirm Authenticode shows 'Valid signature (ZimTech LLC)' for each installed binary.")


if __name__ == "__main__":
    main()
