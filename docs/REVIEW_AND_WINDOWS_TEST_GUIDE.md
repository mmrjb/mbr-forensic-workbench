# MBR Forensic Workbench 0.1.0 — Review and Windows Test Guide

Reviewed on October 28, 2026.

Input: `mbr-forensic-workbench-0.1.0-src.zip`, supplied by the owner. This review examined an extracted working copy. It did not modify the supplied ZIP or patch the application.

## Review outcome

This is a substantial, runnable Python/Qt application, with actual case storage, adapters, job handling, output import, timeline, reporting, and tests. It is suitable for controlled owner testing of the interface and basic workflows. It should not yet be treated as a validated forensic processing platform.

The ZIP contains source, documentation, a synthetic sample case, and screenshots. It contains no ready-made Windows EXE and no genuine Eric Zimmerman executables.

Independent checks completed for this review:

- Installed the project's pinned development dependencies into an isolated Python 3.12 environment.
- Ran the project's complete test suite: **118 passed, 3 skipped in 10.96 seconds**.
- The skipped checks require official tool archives at the developer's configured archive location, which was unavailable here.
- Ran the actual application `main()` entry under Qt's offscreen platform, with a timed normal close: **exit code 0**.
- Generated and inspected fresh screenshots from the running application using its synthetic demo case.
- Reproduced the packaging entry-point failure and five further integration/integrity defects using independent probes and synthetic fixtures.

Environment: Linux x86_64, Python 3.12.14, PySide6 6.10.1. No actual Windows desktop, Windows package, real EZ parser execution, real Authenticode trust verification, or real Windows process containment was tested here. An offscreen Qt test is not an interactive Windows usability test.

The developer's own claim of 121 passing tests is consistent with this suite having 121 cases in total; three archive-dependent cases were skipped in this review. Passing the supplied suite does not cover the additional defects below.

## Findings to fix before relying on real case results

Priorities below describe practical impact for this application. They are not formal vulnerability severity scores.

### R1 — High: the selected packaging entry point uses a package-relative import

Locations: `src/mbrwb/__main__.py:1`; `packaging/mbr_workbench.spec`, the `Analysis` script selection.

The packaging specification selects `src/mbrwb/__main__.py` as a script. That file begins with `from .app import main`. Executing it in script context produces:

```text
ImportError: attempted relative import with no known parent package
```

This was reproduced directly with `runpy.run_path(..., run_name='__main__')`. Running the source as `python -m mbrwb` succeeds because Python then supplies the package context. A genuine frozen Windows executable was not built during this review; its startup must be checked after correction.

Requested correction: use a dedicated absolute-import launcher, such as a script importing `main` from `mbrwb.app`, and point the packaging spec at it. Keep the package's `python -m mbrwb` entry working. Build and launch the actual Windows package from a clean user profile.

### R2 — High: same-size evidence changes are not detected before a job runs

Location: `src/mbrwb/jobs/manager.py:430`, `_run_preflight`.

The job can retain an input SHA-256 at submission, but the run-time check only verifies input existence and size. In an independent probe, a queued file was changed to different bytes with the same length. Its current hash differed from `sha256_at_submit`, yet `_run_preflight` returned no blocking problem.

Impact: a job can process different input content while its provenance still records the old digest. This does not mean the application changed the source itself; it means the change-detection guarantee is incomplete.

Requested correction: compare current input digests to the frozen evidence identity when a digest exists, or process an explicitly frozen working copy. Handle folder-based jobs using their constituent evidence identities. Make checking cancellable and show the phase honestly. Add tests for same-size file changes and folder-member changes while queued.

### R3 — High: executable checks do not cover the managed assembly or all processing dependencies

Location: `src/mbrwb/jobs/manager.py:430` and the installation/dependency snapshot code.

The run-time preflight rechecks the executable SHA-256, but not the separately recorded managed assembly. Replacing the installed synthetic `PECmd.dll` after queue submission, while retaining the EXE, was not blocked. Maps and plugin/batch dependencies also need an explicit frozen-identity comparison.

Impact: the recorded EXE identity does not completely identify the code and interpretation data used by a modern .NET tool.

Requested correction: snapshot and validate the executable, managed assembly, relevant runtime/dependency files, and interpretation assets. Prefer an immutable, content-identified installation and fail clearly when the queued installation has changed. Test DLL-only and dependency-only changes.

### R4 — High: an invalid signature status is not enforced by execution preflight

Locations: `src/mbrwb/jobs/manager.py:97`, `src/mbrwb/ui/pages/tools.py:369`, `src/mbrwb/tools/launcher.py:30`, and the help-verification path.

The inventory can display an invalid signature as needing setup, but the parser submission preflight does not enforce it. In the independent probe, setting the installation's signature state to `invalid` still produced a successful parser preflight. Launch/help paths also need a consistent execution policy rather than relying on a status badge.

This probe did not execute malicious code or test real Windows certificate validation. It demonstrated a missing application-level gate.

Requested correction: centralize execution trust assessment across parser runs, original GUI launches, help capture, and maintenance actions. Distinguish invalid/unexpected publisher, unavailable verification, and offline revocation status. Apply the actual configured policy consistently; do not silently turn an Invalid badge into permission to run.

### R5 — Medium: an update containing only changed Maps can be ignored

Location: `src/mbrwb/tools/store.py:274`.

Installation deduplication uses tool ID plus EXE hash and returns the existing installation early. In the independent probe, a new EvtxECmd archive had the same EXE but a changed `Maps/Security_4624.map`. Import reused the old installation ID and retained the old map content.

Impact: the operator can import an updated bundle while continuing to use stale interpretation assets.

Requested correction: identify an installation using the relevant complete bundle content, including its assembly and interpretation dependencies. A Maps-only change must create or select a distinct installation snapshot without changing active jobs.

### R6 — High: Windows cancellation has a failure branch that can leave a process running

Locations: `src/mbrwb/jobs/process.py:129` and `:162`.

If `AssignProcessToJobObject` fails after `_WinJob()` succeeds, `self._job` remains non-null but does not contain the child. Cancellation later terminates that empty job rather than falling back to terminating the launched process. The independent simulated Windows-branch probe showed the warning was logged, cancellation targeted the unassigned job, and the fallback process kill was never called.

This failure branch was reproduced with mocked Windows APIs/process objects, not on a real Windows host. Separately, the source documents a launch-to-assignment race because the process starts before Job Object assignment; real process-tree containment still requires Windows validation.

Requested correction: track successful assignment explicitly. On containment failure, close the failed Job Object and stop/fail the launched process safely, or use a documented fallback that actually terminates it. Avoid marking a job as safely cancellable when containment did not succeed. Test this branch and genuine parent/grandchild cancellation on Windows.

## Recommended order for owner testing

1. Start the source application and inspect the UI with synthetic demo results.
2. Run the provided automated tests locally.
3. Perform one real parser job on a copied lab artifact.
4. Compare the workbench's results against the same tool run directly.
5. Check reports, reopening, cancellation, and additional tools.
6. Build an EXE after the entry-point correction, then validate it separately.

Keep these first checks in a dedicated lab case and profile. The integrity defects above are relevant to trusting forensic conclusions; they do not prevent inspecting the interface.

## 1. Windows prerequisites

Use a Windows x64 test machine or VM.

- Install or use a compatible Python **3.12 x64**, as expected by the supplied run/build instructions. Check it with `py -3.12 --version`. The project metadata permits Python 3.12 and 3.13, but the supplied packaging path specifically targets 3.12.
- Install **.NET 10 Desktop Runtime x64** for the current .NET 10 EZ GUI tools. Microsoft's Desktop Runtime also includes the standard .NET Runtime, so a second separate runtime installation is unnecessary for those CLI tools.
- The workbench UI itself uses Python/Qt; it can be inspected before EZ Tools or their .NET runtime are installed.
- Download the original tools separately from the official catalogue. Start with EvtxECmd, PECmd, and Timeline Explorer rather than installing everything for the first test.

Official sources:

- [Python Windows downloads](https://www.python.org/downloads/windows/)
- [.NET 10 downloads](https://dotnet.microsoft.com/en-us/download/dotnet/10.0) — choose **.NET Desktop Runtime**, Windows, x64.
- [Eric Zimmerman's tools](https://ericzimmerman.github.io/)

Dependency installation needs access to a configured Python package index or an appropriately prepared wheelhouse. Keep the project's pins for the initial reproduction; record any installation failure rather than silently replacing every dependency with an unrelated version.

## 2. Extract and launch from source

Extract the supplied ZIP under a dedicated folder such as:

```text
%USERPROFILE%\DFIRLab\mbr-forensic-workbench
```

That project folder should contain `README.md`, `pyproject.toml`, `requirements`, `src`, and `tests`. Open PowerShell there. If using a different extraction location, change the first command accordingly.

```powershell
$labRoot = Join-Path $env:USERPROFILE 'DFIRLab'
Set-Location (Join-Path $labRoot 'mbr-forensic-workbench')

py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements\dev.txt

$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:MBRWB_HOME = Join-Path $labRoot 'WorkbenchProfile-010'
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe -m mbrwb
```

There is no need to activate the virtual environment or change PowerShell execution policy: these commands call its Python executable directly. The dedicated `MBRWB_HOME` keeps this test profile separate from the normal profile.

Expected: a desktop window with Overview, Cases, Evidence, Workflows, Tools, Jobs, Results, Timeline, Findings & Reports, and Settings. On first run, choosing “Continue and configure later” should work.

Use `python -m mbrwb`, not `python src\mbrwb\__main__.py`. The latter hits R1.

## 3. Run the project's tests on Windows

Close the application. In the same PowerShell/project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests set offscreen Qt mode inside their process and isolate their app-data root. Some archive/symlink checks may skip depending on available fixtures and Windows permissions. Do not assume the Linux result must match Windows exactly. Preserve the complete failure text if anything fails.

The passing tests use fake parser tools for processing workflows. This command is not proof that a real EZ parser has produced correct results.

## 4. Inspect a fresh synthetic demo case

The UI does not expose a “Create demo case” command. Generate a fresh demo using the included function instead of relying on absolute paths in a demo case generated on another machine.

With the application closed and the environment variables from section 2 still set:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; import os; from mbrwb.samples import create_demo_case; c=create_demo_case(Path(os.environ['MBRWB_HOME'])/'Demo cases'); print(c.root); c.close()"
```

Copy the printed case folder path. Start the app again, press **Ctrl+O**, and choose that folder:

```powershell
.\.venv\Scripts\python.exe -m mbrwb
```

Expected demo observations from the supplied sample generator:

- The top bar clearly says DEMO.
- Overview shows 15 registered evidence items, 4 imported datasets, 18 records, and 31 timeline events.
- Results contains synthetic event, Prefetch, and NTFS-shaped output.
- Filtering the synthetic event dataset by EventId 4624 should yield 2 records.
- A bookmarked record remains the same source record after sorting.
- Timeline opens a source record and displays the selected timezone.
- Findings & Reports can preview and save an HTML report.
- Reopening the case preserves records and notes.

These are sample-generator expectations, not guarantees about real parser output. The demo's artifact files are synthetic and are not the input for validating genuine parsers.

## 5. First genuine parser test: one exported EVTX

Create a new real-test case, separate from DEMO. Put evidence outside the case directory.

### Prepare a small local test artifact

If you already have a collected lab `Application.evtx`, use that copy. Otherwise the following exports the Application log into a new lab file:

```powershell
$labRoot = Join-Path $env:USERPROFILE 'DFIRLab'
$evidenceDir = Join-Path $labRoot 'Evidence\LAB-WIN-01\C\Windows\System32\winevt\Logs'
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
$evtxPath = Join-Path $evidenceDir ('Application-{0}.evtx' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
wevtutil epl Application $evtxPath
if ($LASTEXITCODE -ne 0) { throw 'The event log export failed; use an existing exported lab EVTX or resolve access to that channel.' }
Get-FileHash -Algorithm SHA256 -LiteralPath $evtxPath
```

Record the exported path and digest. If channel access is denied, use a permitted collected lab copy; the application does not need to run elevated merely to parse an accessible copy. `wevtutil epl` is the export operation.

### Import the original tool

1. Download the appropriate official EvtxECmd archive and extract its complete contents into a lab tools folder, retaining Maps and sidecars. Alternatively import the original ZIP directly.
2. In **Tools**, use **Import archive** or **Import folder**.
3. Select EvtxECmd and check the actual installed version, runtime information, and executable path.
4. Open **Options evidence → Verify this version (capture help output)**.
5. If the app reports unsupported version or missing runtime, retain that message. A badge is not a substitute for the actual job test.

### Create the case and run only EvtxECmd

1. Press **Ctrl+N**. Use a new title such as `LAB - EvtxECmd first test`. Place the case under `%USERPROFILE%\DFIRLab\Cases`, outside `%USERPROFILE%\DFIRLab\Evidence`.
2. In **Evidence → Add evidence**, select the exported EVTX, click **Inspect**, review the classification, assign host `LAB-WIN-01`, then **Register evidence**.
3. Prefer **Tools → EvtxECmd → Configure analysis** for the first run. Select the single EVTX file and keep basic/default CSV output. Use **Check** before submitting.
4. In **Jobs**, inspect Summary, Logs, Outputs, and Provenance.
5. The job should have a real parser exit status and output files. A failure or Partial result needs its precise reason; do not hide it by moving on.
6. In **Results**, inspect actual EventId, channel/provider, timestamp fields, and selected event values.
7. In **Timeline**, check mapped events, timezone interpretation, and **Open source record**. A timeline mapping problem can exist even when the raw CSV is valid.
8. Recompute the input file's SHA-256 and compare it with the recorded original digest.

The synthetic demo's record counts do not apply to this file. Do not expect the same number of timeline events and output rows in every artifact type.

## 6. Compare workbench processing with a direct run

Copy the exact installed EvtxECmd executable path from Tools. Use the same input and the workbench job's relevant options. The following illustrates a basic file-to-CSV run; replace the tool path with the actual installation path:

```powershell
$toolExe = 'PASTE_THE_INSTALLED_EvtxECmd.exe_PATH_HERE'
$comparisonOut = Join-Path $labRoot ('Direct-EvtxECmd-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $comparisonOut | Out-Null
& $toolExe -f $evtxPath --csv $comparisonOut
```

Compare parsed event identity, event counts where equivalent datasets are being compared, key field values, warning messages, and timestamps. Output filenames, tool-console timestamps, and row order alone are not sufficient equivalence tests. Use the same tool version and Maps. Keep raw CSV separately from spreadsheet-safe exports, which intentionally escape some values.

## 7. Extend testing after the first parser works

| Test | Input | What to verify |
| --- | --- | --- |
| PECmd | A genuine copied lab `.pf` or collected Prefetch folder | Parser output, file dataset, dedicated run-time dataset if emitted, matching timestamps and source references |
| MFTECmd | An already collected lab `$MFT` | CSV import, volume association, file/path fields, correctly labeled file timestamps |
| Timeline Explorer | Original installed GUI, optionally open a generated CSV inside it | Opens normally; app records a launch session rather than claiming a completed parser job |
| Bookmark/finding/report | A real parsed record | Stable source reference after filters/sorting; HTML report includes actual evidence/tool/version context |
| Reopen case | Completed lab case | Results, notes, findings, job logs, and provenance survive restart |
| Cancellation | A sufficiently long lab parse | Job state changes and parent/child processes actually stop; test R6's corrected code before assuming this is reliable |
| Workflow | The same already-validated artifacts | Planned jobs match available inputs; no cross-host mixing; omissions and partial results remain visible |

Use previously collected artifacts for MFT/Registry tests. This review does not assume that an ordinary file copy can acquire protected live NTFS metadata.

## 8. Information to send back after the first run

- Windows version and Python version.
- Whether dependency installation and the application window worked.
- Tool version and runtime status for the parser tested.
- The job's final state, parser outcome, import outcome, and warning/error text.
- Whether Results contains the expected actual CSV fields.
- Whether Timeline links back to the right source row.
- If there is a failure: `stdout.log`, `stderr.log`, `job.json`, and `workbench_error.log` when present, after reviewing them for sensitive data.

There is no need to send the original evidence file merely to diagnose a setup error.

## Corrective brief for Claude Code

Give Claude the source ZIP and this report. A focused request is:

> Review and fix R1–R6 in this report before adding new features. Preserve the existing design and working source launch. Add meaningful regression tests that fail on the current code and pass after each correction. Correct the frozen application entry point, enforce input/tool/dependency identity at run time, apply execution trust consistently, handle Maps-only updates as distinct installation content, and fix the failed Job Object assignment branch. Keep processing with real EZ tools separate from fake-tool tests. Build and launch the actual Windows EXE only if a Windows environment is available; otherwise state that limitation precisely. Update TEST_RESULTS.md, TOOL_COVERAGE.md, and IMPLEMENTATION_STATUS.md with evidence. Work locally and do not publish or push to GitHub.

## Official references for the test steps

- [Microsoft .NET 10 downloads](https://dotnet.microsoft.com/en-us/download/dotnet/10.0)
- [Microsoft wevtutil command reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/wevtutil)
- [Python downloads for Windows](https://www.python.org/downloads/windows/)
- [PyInstaller usage and entry scripts](https://pyinstaller.org/en/stable/usage.html)
- [Official EZ Tools catalogue](https://ericzimmerman.github.io/)

The review findings are based on the uploaded source and independent synthetic reproductions. The official references support installation/export/packaging instructions; they do not establish that the uploaded application was validated on Windows.
