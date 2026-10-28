# Implementation status

_Last updated: 2026-10-28, end of session 1. Read this first when continuing._

## Current increment
All increments A-G are implemented. What remains is **environment-dependent verification on Windows** (see "Next concrete action").

## Implemented
- **A Foundation**: package layout (`src/mbrwb`), configurable product name, per-user paths and portable mode, typed settings (atomic, recoverable, export/import preview), SQLite with migrations (app, case, results), case folders with marker, lock and recovery, logging.
- **B Tool management**: reviewed catalogue of all 31 official entries, static evidence for all 30 official archives, safe zip extraction, staged installs with version retention/rollback and in-use protection, folder/archive/offline-bundle import, KAPE referenced in place, allowlisted HTTPS downloads, catalogue reconciliation, .NET runtime detection, Authenticode (WinVerifyTrust on Windows), readiness inventory, GUI launch sessions, help-capture verification.
- **C/D Processing**: adapter contract and 19 EZ CLI profiles (+ KAPE, Get-ZimmermanTools), Windows argument rules, job manager (frozen provenance, queue/concurrency/exclusive, preflight at run time, cancel/timeout of the whole tree, quarantine, retry, recovery), outputs with hashes.
- **E Workflows**: discovery/classification, host/user/volume inference, related inputs, 8 presets + validated custom workflows, planner (grouping, statuses, dry run, plan snapshots).
- **F Analysis**: streaming CSV/JSONL/JSON import with record locators, paged/filtered/sorted queries, case-wide search, timeline (13 versioned mappings, timestamp semantics, histogram, context window, unresolved list), bookmarks, notes, versioned findings.
- **G Delivery**: HTML/Markdown reports with redaction, case manifest, exports (safe/raw CSV, JSONL, timeline CSV), Qt UI (10 pages, dialogs, command palette, shortcuts, drag and drop, dark/light themes), PyInstaller spec + Windows build script, docs, sample DEMO case and reports, screenshots, million-row benchmark, coverage matrix.

## Verification evidence
- `pytest`: 121 passed (Linux, offscreen Qt). See TEST_RESULTS.md.
- Adapters: every flag checked against the official 2026.10 binaries (static, not executed). All 30 official archives import correctly.
- Benchmark: 1 M rows import in 22 s; timeline 3 M events in 109 s; largest UI event-loop gap 73 ms.

## Genuine blockers
- No Windows host: package build, Job Object, WinVerifyTrust, GUI launches and real tool runs are untested.
- Executing the downloaded EZ binaries was refused by the session's safety policy, so there is no real help capture or real parser output.
- PyPI/GitHub unreachable from the build host (PySide6 came from EPEL; PyInstaller not available here).

## Next concrete action
On a Windows 11 x64 machine: run `packaging\build_windows.ps1`, then work through the "Windows acceptance checklist" in TEST_RESULTS.md (Verify this version for PECmd/MFTECmd/EvtxECmd, run Quick Windows Triage on real collected artifacts, and compare real CSV headers against the timeline mappings). Record results in TEST_RESULTS.md and regenerate TOOL_COVERAGE.md.

## Known limitations / ideas not done
- No full-text (FTS) index; all-column search is a LIKE scan (about 3 s count on 1 M rows, first page under 1 s).
- KAPE runs use the generic configure form (Targets/Modules as comma-separated names, license acknowledgement required); a picker built from the parsed Targets/Modules lists is a candidate improvement.
- SRUM, SQLECmd, SumECmd and iisGeoLocate outputs are imported and searchable but have no timeline mapping (their schemas are table/map specific).
