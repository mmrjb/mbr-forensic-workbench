# User guide

MBR Forensic Workbench runs and opens Eric Zimmerman's tools for you, keeps every run reproducible, and lets you explore results, build a timeline and write findings. It never replaces the tools' own parsing, and it never writes to evidence.

Shortcuts: **Ctrl+N** new case, **Ctrl+O** open case, **Ctrl+K** command palette, **Ctrl+F** search/filter the current view, **F1** help for the current page, **Ctrl+1..9** pages.

---

## 1. Tools

**Tools** lists all 31 entries of the official catalogue (reviewed 2026-10-28). Each tile shows the official name, purpose, installed version and a status written in words (never colour alone).

### Getting the tools
- **Import folder** - point at an existing EZ Tools folder (for example a Get-ZimmermanTools destination with its `net` subfolder, or a folder you copied from a USB stick). Recognised tools are *copied* into the workbench tool store; your folder is not changed.
- **Import archive** - one or more official `.zip` files, or an *offline bundle* (`mbrwb-offline-bundle.json` inside). Archives are extracted into a staging folder with traversal, link and zip-bomb checks, the expected executable layout and version are checked, and only then is the new version promoted.
- **Install all eligible** - shows a summary first: what will be downloaded from `download.ericzimmermanstools.com`, which installations are kept, and which entries need a manual import (KAPE). Downloads are HTTPS only, from the reviewed origin, with timeouts and cancel.
- **Check for updates** - fetches the official catalogue page on request (never automatically) and compares it with the reviewed manifest. New upstream entries appear as *integration pending review*; they are not downloaded or run.
- **Drag and drop** a tool archive or tools folder onto the window to import it.

Offline machines: use Import folder/archive. *Settings > Network* has an Offline mode that disables every workbench network request.

### Status and details
- *Ready (options statically verified)* - installed, runtime present, and the adapter's options were checked against the official build of this version (the binary was not executed during that check).
- *Ready* with *Verified against captured help output* - you ran **Verify this version** on this exact executable.
- *Runtime missing* - the details tell you which .NET runtime (and whether the Desktop runtime is needed) and where to get it. GUI tools need the **.NET 10 Desktop Runtime**; CLI .NET 10 builds need the .NET 10 runtime; .NET 4 builds need .NET Framework 4.7.2 (TimeApp 4.8).
- *Version not reviewed* - the installed version does not match a reviewed adapter profile. The tool stays listed and its help is available; use **Verify this version** to compare its help with the closest profile.
- *Installed - runs on Windows* - shown on non-Windows machines.

The details panel has: **Versions** (select a version for future jobs, roll back, re-check the signature), **Options evidence** (the exact option list and help text found in the official binary, plus any captured help output), **Diagnostics** (internal identifiers), documentation link and tool folder.

### Command-line tools
**Configure analysis** opens a form generated from the installed version's profile: evidence inputs (choose registered evidence or browse), Basic and Advanced options, the output destination (always a new job folder in the case), a live **command preview** with **Copy command**, and **Check** (preflight). The preview is for reading; jobs run as an argument list without any shell.

### GUI tools
**Open original tool** starts the program in its own window from its verified installation folder. No file-opening argument has been verified for the 2026.10 GUI tools, so the workbench does not invent one; use *Copy path* / *Show folder* to load a file inside the tool. Each launch is recorded as a *launch session* - it is never counted as an analysis. If you export something from a GUI tool, import it via **Results > Import external CSV/JSON**.

### Maintenance and specialist entries
- **Get-ZimmermanTools** - optional maintenance job using a reviewed copy of the script, always with `-NoUpdate`. Afterwards import the destination folder to refresh the inventory.
- **KAPE** - optional; point the workbench at your own licensed KAPE folder (referenced in place, never copied or downloaded). KAPE's FAQ: free for government, educational, research and internal company use; not available for commercial use since 1 January 2026. The KAPE dialog lists Targets (collection definitions) and Modules (processing definitions) and checks which module executables are present.
- **VSCMount** is a specialist live operation that needs administrator rights; it is never part of a workflow.
- **XWFIM** and **TimeApp** are launch-only utilities.

---

## 2. Cases

**New case** (Ctrl+N) needs only a title; the examiner and display timezone have defaults. The case folder cannot be inside registered evidence. **Open** (Ctrl+O) accepts only folders that carry the workbench case marker.

On opening a case, jobs that were running when the application stopped are marked **Interrupted** - their logs and files are kept and nothing is re-run. If evidence paths are missing on this machine, Evidence shows them as unavailable and you can **Remap root**.

**Cases** lists recent cases with status. *Remove from list* never deletes anything. *Archive copy* makes a consistent copy (databases via SQLite backup) once no jobs are running.

Demo cases are labelled **DEMO** in the top bar and live in a separate *Demo cases* folder.

---

## 3. Evidence

**Add evidence** (or drag a folder onto the window) -> choose a file or folder -> **Inspect**. Discovery runs in the background (cancellable) and shows a preview: files inspected, supported candidates, how many were identified only by file name, unavailable and excluded paths (links/junctions are not followed; workbench case folders are skipped), estimated size and the host/user/volume grouping it inferred from KAPE-style paths.

Choose **Reference in place** (default) or **Make working copies inside the case** (relative structure preserved, copies verified by hash). Optionally hash in the background. Each registration writes an evidence manifest that lists exactly which files were hashed, unavailable or excluded - a folder is never called "verified" as a whole.

In the evidence tree you can correct the **artifact type**, **host**, **user profile** and **volume** (corrections are audited). *Related files* shows automatic links (hive <-> .LOG1/.LOG2, SQLite <-> -wal/-journal, $J/$LogFile <-> $MFT of the same volume, SRUDB.dat <-> SOFTWARE of the same host). **Verify availability** re-checks size (and optionally hash): a changed source becomes *changed*, its old manifest is kept, and **Register new version** records the current file as a new evidence version.

---

## 4. Workflows ("New analysis")

1. **Select evidence** - tick the hosts to include.
2. **Choose a workflow** - each preset states its question: Quick Windows Triage; Program Presence & Execution Evidence; File and Folder Activity; User Activity; Registry Analysis; Windows Event Logs; SRUM and Application Resource Usage; SQLite Application Artifacts; or import a **Custom workflow** (validated JSON; registered tools and options only, no scripts).
3. **Review the plan** - one row per job: step, tool, evidence group (one job per host/folder, not one per file), prerequisites and status (*Ready*, *Needs setup*, *Not present*, *Not selected*, *Blocked*). Untick steps you do not want; double-click a step to adjust its options. Optionally set a **source timezone assumption** for outputs without a verified timezone rule.
4. **Run analysis** (or **Dry run** to save the plan without executing anything). The plan snapshot, including what was not run and why, is stored with the case.

Presence artifacts are labelled as presence: Amcache, ShimCache and RecentFileCache can show a program existed; Prefetch run times are execution evidence (when Prefetch is enabled).

---

## 5. Jobs

Every run, with state (Queued, Preflight, Running, Importing, Completed, Partial, Failed, Cancelled, Timed out, Interrupted, Skipped), elapsed time, warnings and outcome. Select a job for:
- **Summary** - parser outcome and import outcome separately, the reason, warnings, the next action.
- **Logs** - live preview while running; *Open full stdout/stderr log* for the complete files (searchable, bounded viewer).
- **Outputs** - each file with type, size, SHA-256, completion (complete / quarantined) and import state; open in Results, show folder, or **Re-import** a preserved output.
- **Provenance** - executable path + SHA-256 + version, profile, argument array, dependency snapshot (maps, batch files, plugins with hashes), inputs with their size/hash at submission and run time.

**Cancel job** stops that job's whole process tree; **Cancel rest of workflow** affects only that workflow. **Retry** runs again in a fresh folder linked to the original (the original tool version must still be selected).

What the states mean: exit code 0 with "damaged/skipping" messages -> *Partial* with warnings; output that does not match the reviewed schema -> *Partial* with "unsupported output schema" (raw output still searchable); a tool that reports nothing to process -> *Completed* with zero records. None of these mean the host is clean.

---

## 6. Results

Left: datasets grouped by host, tool, job or user. Centre: a paged table (fast with millions of rows), with:
- **Filters** - column, operator, value; shown as chips; **Reset** clears them.
- **Search** with an explicit **Scope**: *This table* (all columns of the dataset) or *Whole case* (every imported dataset).
- Sort by clicking a header (an index is built in the background the first time).
- Columns menu, saved views, export (**Spreadsheet-safe CSV** neutralises leading `= + - @`; **Raw CSV** keeps exact values; **JSON lines**).

Right: **Record** (original fields, source record `dsN:recM`, line/byte range, import status), **Provenance** (job, source output and its hash, availability, import counts, rejected samples, timeline mapping) and **Field meaning** (the reviewed mapping, or a note that columns are shown uninterpreted). Actions: copy value/row, bookmark, add to finding, notes. Bookmarks follow the record, not the row position.

"No records match" describes the current filter and dataset only.

---

## 7. Timeline

Events exist only for outputs with reviewed mappings (Prefetch, $MFT, $J, event logs, LNK, Jump Lists, ShellBags, Amcache, ShimCache, Recycle Bin, Windows Timeline). One record can produce several events with different meanings (for example $SI created vs $FN created).

- **Time display** is always visible: UTC or the case timezone. Changing it never reinterprets stored instants.
- **Histogram** - drag across bars to set the range.
- Filters: range, host, tool, category, text; save filters.
- **Unresolved timestamps** - values without an offset and without a verified rule, DST-ambiguous or non-existent local times, zero and invalid values. They are listed separately, never placed at 1601/1970.
- Event details: UTC and display time, original value, interpretation (explicit UTC, tool UTC convention, operator assumption ...), source field, object, source record, source output, job and mapping version. **Open source record** jumps to the exact row; **Show before/after** shows N minutes around the event on the same host.
- **Export CSV** includes UTC, the display timezone, original values, status and source references.

---

## 8. Findings & Reports

A finding has a title, status (draft/open/confirmed/refuted/closed), optional **analyst-assigned** severity, tags, narrative and supporting references. Use **Add to finding** from Results or Timeline; each reference stores an immutable identity plus a snapshot of what you saw. Every save keeps the previous version.

**Report** tab: choose findings, write the scope, pick the timezone, choose redactions (examiner, user names, host names, Windows path directories, custom terms) and **Preview** the actual content. Save as self-contained **HTML** (no external assets, print-ready), **Markdown**, or a **Case manifest (JSON)** with tool versions and output hashes. Redaction affects the report only and is stated in it. Report options are saved as a draft automatically.

---

## 9. Settings

Appearance (dark/light), Tool storage (folder, versions kept, preferred .NET flavor), Case defaults (cases folder, examiner, timezone, evidence mode, hashing), Execution (parallel jobs, timeout, log preview), Network/offline mode, Diagnostics (log level, diagnostics bundle - its contents are listed before saving and exclude evidence and case data; settings export/import with preview - folder paths from another machine stay unresolved).

Changes apply to future work only.

---

## 10. What the workbench does not claim
- It does not decide whether a host is compromised and computes no scores.
- An empty result is not proof of absence.
- Hashes and logs support reproducibility; they do not establish legal admissibility or a complete chain of custody, and the case database is not tamper-proof.
- Running tools on a live machine can create operating-system traces; the workbench "does not intentionally modify input artifacts", it is not "zero footprint".
