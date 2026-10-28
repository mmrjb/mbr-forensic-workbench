# Test results

_Session 1, 2026-10-28. Everything below was actually run in this session unless it is listed under "Not tested"._

## Platform actually used
| Item | Value |
|---|---|
| OS | AlmaLinux 10 (Linux 6.12.0-211.40.1.el10_2.x86_64), **not Windows** |
| CPU / RAM | Intel Xeon E5-2699 v4 @ 2.20 GHz, 6 vCPU / 16 GB |
| Python | 3.12.14 (system), isolated venv |
| Qt / PySide6 | 6.10.1 / 6.10.1 (EPEL/EL10 builds extracted into a private sysroot; `QT_QPA_PLATFORM=offscreen`) |
| pydantic / PyYAML / pytest / pytest-qt | 2.14.1 / 6.0.3 / 9.1.1 / 4.5.0 |
| Network | ericzimmerman.github.io and download.ericzimmermanstools.com reachable; PyPI and GitHub not reachable |
| .NET | .NET 10.0.13 runtime extracted locally (used only for runtime *detection* tests; no EZ binary was run with it) |

## Commands run and outcomes
```
mkdir -p .pytest_tmp && python -m pytest -p no:cacheprovider --basetemp=.pytest_tmp/t -q   -> 121 passed in 65.53 s
python scripts/inspect_tool_archives.py <official zips>                 -> 30 archives inspected (static_evidence.json)
python scripts/benchmark_million.py --dir <scratch> --rows 1000000      -> see Performance (two runs)
python scripts/screenshots.py ...                                       -> 36 screenshots (dark, light, 1280x720 @150 %)
python scripts/make_sample.py                                           -> samples/ (demo case + example reports)
python scripts/coverage_matrix.py > TOOL_COVERAGE.md
python -m compileall src scripts tests; import of every mbrwb module     -> no errors
```
Full pytest output: `docs/benchmarks/pytest_full_run.txt`.

## Coverage of the required test areas
| # | Required area | Tests | Result | Kind |
|---|---|---|---|---|
| 1 | Argument arrays with spaces, Persian, `$`, `&`, `()`, UNC; no shell | `test_winargs` (round-trip incl. UNC/Persian/quotes), `test_jobs::test_hostile_paths_reach_the_tool_unchanged` (`Prefetch (copy) & $MFT %PATH% \`x\` پرونده` reaches the child byte-for-byte; frozen argv == executed argv) | pass | real process, **fake tool** |
| 2 | Per-tool required inputs, invalid combinations, version profiles | `test_adapter_profiles` (19 EZ adapters: every flag present in the official 2026.10 binary strings; profile selection; no `--vss/--sync/--dt`), `test_jobs::test_missing_tool_and_wrong_inputs...` (missing tool, missing input, -f and -d together, managed `--csv` in extra args, MFTECmd `--body` without `--bdl`) | pass | static + unit |
| 3 | Safe archive extraction | `test_tool_store` (`../`, absolute, drive letter, ADS, CON/NUL, backslash traversal, symlink member, pre-existing link in staging, zip bomb ratio/total) | pass | unit |
| 4 | Failed/interrupted install keeps prior version | `test_failed_update_keeps_prior_version_selected`, `test_install_update_rollback_and_retention` (in-use version never removed) | pass | unit |
| 5 | Missing runtime/tool/dependency -> actionable preflight | `test_runtime_missing_blocks_with_guidance`, `test_missing_tool...`, RLA/SRUM/MFTECmd dependency warnings | pass | unit |
| 6 | Run, cancel, timeout, tree cleanup, interrupted recovery | `test_cancel_kills_whole_tree_and_quarantines` (grandchild pid verified dead), `test_timeout_is_its_own_state`, `test_interrupted_jobs_are_marked_on_reopen_and_not_rerun`, `test_queued_job_keeps_frozen_tool_identity_after_update`, `test_retry_runs_in_fresh_folder_linked_to_original` | pass | real processes (POSIX process groups), **fake tool** |
| 7 | Non-zero exit vs exit 0 + warnings vs legitimate empty vs import failure | `test_outcomes_stay_distinct` (CRASH -> Failed/quarantined; WARN -> Partial; EMPTY -> Completed 0 records; BADSCHEMA -> Partial/unsupported_schema) | pass | **fake tool** |
| 8 | CSV quoting, BOM, multiline, malformed, duplicate headers, JSONL | `test_import_and_timeline` (byte range reproduces the exact original record; padded/extra rows; latin-1 refused then accepted with explicit cp1252; UTF-16 BOM with Persian; JSONL + JSON array; empty CSV) | pass | unit |
| 9 | Timestamp precision, invalid/zero, UTC, offsets, unknown tz, DST | `test_timestamp_precision_and_statuses` (100 ns resolution survives), `test_dst_ambiguity_and_gap_with_operator_timezone` (New York fold/gap, Tehran) + fast-path equivalence check on 20 000 values | pass | unit |
| 10 | Source row references survive filtering, normalisation, timeline, report | `test_full_journey` (bookmark after re-sort resolves to the same record; event -> dataset/rec -> original value; report links) | pass | **fake tool** |
| 11 | Input hashes unchanged, incl. registry log replay on derived copies | `test_full_journey` (all evidence hashes identical after the workflow), `test_rla_derived_hive_keeps_originals` (derived hive registered with parent link) | pass | **fake tool** |
| 12 | SQL parameterisation, HTML escaping, spreadsheet-safe export | `test_filters_are_parameterised`, `test_spreadsheet_safe`, journey report escaping (`<script>` text escaped, no external assets), safe vs raw CSV | pass | unit |
| 13 | GUI launches separate from parser jobs | `test_gui_launch_is_a_session_not_a_job` (stubbed process; session rows in app and case DB; zero jobs; no unverified file-open argument) | pass | stubbed (no real GUI tool launched) |
| 14 | Million-row import/query/paging with responsive UI | `scripts/benchmark_million.py` (below) | measured | synthetic data |
| 15 | User journey create -> evidence -> plan -> run -> results -> bookmark -> finding -> report -> reopen | `test_full_journey` (core), `test_ui.py` (offscreen UI: all pages with and without a case, demo case results/timeline/report preview, theme switch, command palette, side-effect-free command preview) | pass | **fake tool** + offscreen UI |

Other tests: settings recovery and import preview, case lock across processes, case-inside-evidence refusal, foreign folder refusal, download allowlist/https-only, catalogue completeness (all 31 official entries, every download URL on the allowlisted origin, KAPE non-commercial note), signature assessment off-Windows (UNAVAILABLE, not VALID; unsigned PE detected), KAPE definitions read with `safe_load` (malicious YAML tag rejected), upstream page reconciliation (new entry -> pending review), KAPE run with a FAKE kape.exe stand-in (argv contains `--target/--tsource/--tdest`, never `--tflush`; the collected tree is registered as evidence carrying the job id), switch-vs-value toggle emission, **import of all 30 official 2026.10 archives** into the tool store (copy, safe extract, layout + version check; nothing executed).

## Real versus simulated
- **Real (static, read-only):** all 30 official archives downloaded from download.ericzimmermanstools.com on 2026-10-28; PE headers, version resources, runtimeconfig, embedded certificate names, option literals and output-name literals read; every adapter flag cross-checked; mapping column names confirmed as property identifiers in the DLLs; all 30 archives imported through the real installation code path.
- **Real (processes):** process start/stream/cancel/timeout/tree-kill on Linux with real child and grandchild processes.
- **Simulated:** every parser run used `tests/fake_tools/fake_ez.py`. Its CSV output is synthetic and **proves nothing about the EZ tools**. The UI ran on Qt's offscreen platform.
- **Not executed at all:** any EZ binary (the build session's safety policy refused it), KAPE (no binary), Get-ZimmermanTools (no PowerShell).

## Performance (synthetic 1,000,000-row MFTECmd-shaped CSV, 188.5 MB)
Machine as above. Numbers are this workbench's storage/UI paths, not EZ tool speed. Full JSON: `docs/benchmarks/million_rows_linux.json` (final) and `..._before_optimisation.json` (first run).

| Metric | First run | After optimisation |
|---|---|---|
| Import 1 M records (single transaction) | 21.2 s | 22.1 s |
| Timeline mapping (3 M events) | **193.5 s** | **108.8 s** |
| First page (200 rows) | 3.2 ms | 1.5 ms |
| Deep page (offset 900 000) | 230 ms | 271 ms |
| Count all / filter equals count | 237 / 459 ms | 387 / 459 ms |
| All-column search: first page / count | 685 ms / 3.7 s | 819 ms / 2.9 s |
| Sort index build (on demand) / sorted first page | 2.2 s / 3.8 ms | 1.5 s / 4.1 ms |
| Timeline first page / count / 140-bucket histogram | 3.4 / 231 / 2 917 ms | 3.3 / 173 / 2 617 ms |
| UI (paged table, search filter): rows visible / scroll to last row | 3.3 s / 3.8 s | 3.2 s / 3.5 s |
| **Largest GUI event-loop gap during those queries** | 112 ms | **73 ms** |
| Peak RSS of the workbench process | 45 MB | 366 MB |
| results.sqlite size | 1.41 GB | 1.32 GB |

Bottleneck found: timeline mapping. Fixes: exact-format fast path for EZ timestamps (verified equivalent to the general parser), templates compiled once per dataset, per-row constants hoisted, a larger SQLite page cache during bulk writes (the reason for the higher peak RSS), and the low-selectivity `ts_status` index dropped (migration 2). Child-process memory is not included in these numbers; on Windows the Job Object reports peak job memory separately.

## Visual QA (actual running UI, offscreen)
`docs/screenshots/`: every page in dark and light theme at 1440x900, plus `docs/screenshots/scaled/` at 1280x720 with `QT_SCALE_FACTOR=1.5`. Issues found during this review and fixed: Tools tile text overlap, truncated Results action buttons, cramped Workflow cards and too-small plan table, sidebar subtitle clipping, Results controls at small widths, an empty Job field on externally imported events. Reviewed visually: typography, alignment, empty states, long paths (middle-elided with tooltip), dense tables, light theme. Keyboard: shortcuts and tab order were exercised only through code paths, not by hand on a desktop.

## Not tested (and why)
| Item | Reason / what to do |
|---|---|
| Windows package build and start from a clean profile | No Windows host. Run `packaging\build_windows.ps1` then start the exe from a fresh user profile. |
| Any real EZ tool execution (PECmd, MFTECmd, EvtxECmd minimum) and real output headers vs mappings | Binary execution refused in this session; no Windows. Run Quick Windows Triage on a real collection; check for "unsupported output schema". |
| `Verify this version` help capture | Requires running the binaries (Windows). |
| Windows Job Object containment, WinVerifyTrust, .NET Framework registry detection, `IsUserAnAdmin`, `case.lock` liveness via OpenProcess | Windows-only code paths; written against documented APIs, not executed. |
| Real GUI tool launches, Get-ZimmermanTools, KAPE runs, VSCMount (admin) | No Windows / no KAPE binary. |
| Downloads through the UI against the live origin | The download code was not pointed at the live server from tests (unit tests cover the allowlist and https rules); the official archives used for static checks were fetched with curl. |
| 125 %/200 % scaling, real high-DPI monitors, screen readers | Only 100 % and 150 % (QT_SCALE_FACTOR) offscreen captures were reviewed. |
| Long-path (>260) behaviour on Windows | Warning path implemented; untested on Windows. |

## Windows acceptance checklist (for the owner)
1. Build: `powershell -NoProfile -File packaging\build_windows.ps1` (tests must pass on Windows too).
2. Start `dist\MBR Forensic Workbench\MBR Forensic Workbench.exe` from a clean profile without Python installed.
3. First run -> Import folder (or Install all eligible) -> confirm versions, SHA-256 and "Valid signature (ZimTech LLC)".
4. Remove the .NET 10 Desktop runtime on a test VM (or use one without it) -> GUI tools show *Runtime missing* with the download link.
5. Verify this version for PECmd, MFTECmd, EvtxECmd.
6. Run Quick Windows Triage on a collected test image's KAPE output; cancel one job mid-run and confirm no PECmd/EvtxECmd process remains (Task Manager).
7. Kill the workbench during a job; reopen the case -> job *Interrupted*, no orphan processes.
8. Results/Timeline: confirm mapped events, *tool UTC convention*, source-record navigation; export safe CSV and open it in Excel.
9. Open Timeline Explorer from Tools; confirm a launch session (not a job) is recorded.
10. Generate the HTML report, print preview it, reopen the case.
