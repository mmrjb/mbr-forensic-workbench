# Decisions

Consequential decisions only. Dates are UTC.

## 2026-10-28 Persistence: three SQLite files
`app.sqlite` (per user: installations, verification records, launch sessions, recent cases), and per case `case.sqlite` (metadata, evidence, jobs, outputs, findings, audit log) plus `results.sqlite` (dataset row tables and timeline events). A million-row import is one long write transaction; keeping it in a separate file means bookmarks, notes and job state updates are never blocked by it. WAL journal, one connection per thread, in-process write lock, numbered migrations, `schema_migrations` table.

## 2026-10-28 Datasets: one row table per dataset, raw strings only
Each import creates `ds_<id>(rec_id, line_start, line_end, byte_start, byte_end, rstatus, extra, c0..cN)`. Values are the exact original strings; nothing is coerced. `rec_id` is the logical record number in the source file and is the identity used by bookmarks, findings and timeline events. Typed interpretation lives only in timeline events (and their mapping version), never in the raw columns.

## 2026-10-28 Timestamps: integer 100 ns ticks since 1601 UTC
Python `datetime` truncates to microseconds; FILETIME-precision values (7 digits) are stored as integer ticks plus the original text and the number of fractional digits. Statuses distinguish explicit UTC/offset, *tool UTC convention*, operator assumption, unresolved, ambiguous/non-existent (DST), zero and invalid. EZ Tools CSV timestamps without an offset are mapped with the *tool UTC convention* status (evidence: UTC wording in the 2026.10 help strings), not as explicit UTC. Unresolved values are excluded from time-ordered views and listed separately.

## 2026-10-28 Adapter verification levels
`static_strings` = every modelled flag and its help text were found in the official 2026.10 binary of that tool, read without executing it; `verified_help` = the operator captured `--help` from the exact installed executable and all modelled flags matched (per executable hash); `unverified` otherwise. Unknown version families keep the catalogue entry and help evidence but get no form until verified. Reason: the build session could not execute the binaries, and a documentation URL alone is not verification.

## 2026-10-28 Managed outputs and excluded switches
Inputs and output destinations are owned by the workbench (`--csv`, `--json`, `--out`, ...); extra arguments may only use modelled, non-managed flags. `--vss`, live-registry modes, `--sync` (runtime map/batch download) and `--dt` (timestamp format) are not offered in collected-evidence workflows.

## 2026-10-28 Tool installations are copied and immutable
Imports (archive, folder, offline bundle) are extracted to `tools/.staging`, validated (expected executable layout, PE version, managed assembly present), and promoted with an atomic rename into `tools/<id>/<version>-<flavor>-<sha8>`. Previous versions are retained (default 2) and never removed while selected or in use by a queued/running job. KAPE is the exception: it is referenced in place (operator-owned, licensed separately), and its executable hash is re-checked before every job.

## 2026-10-28 Process lifecycle
`subprocess.Popen` with an argument list and `shell=False`; two reader threads drain stdout/stderr into raw log files; Windows Job Object with kill-on-close for whole-tree cancel/timeout and for cleanup if the workbench dies; POSIX process groups elsewhere. Job state, parser outcome and import outcome are stored separately and aggregated explicitly.

## 2026-10-28 Network
Only user-initiated requests (catalogue check, tool download). HTTPS only, reviewed origins (`download.ericzimmermanstools.com` for downloads, `ericzimmerman.github.io` for the catalogue page), redirects restricted to the same allowlist, size limits, timeouts, cancellation, default TLS verification. The fetched catalogue page is data: new entries are shown as "pending review" and never downloaded or executed.

## 2026-10-28 Get-ZimmermanTools
Optional maintenance job only. Runs a reviewed copy (SHA-256 allowlist; otherwise operator review + approval) with `-NoUpdate` always (the script otherwise replaces itself), never `-Sync`, never `-ExecutionPolicy Bypass`. The workbench's own download/import does not depend on it.

## 2026-10-28 Packaging
PyInstaller one-folder build on Windows (Python 3.12 x64, pinned requirements). The package never includes EZ Tools or KAPE. Portable mode only with `portable.flag` next to the executable and a writable root; otherwise per-user `%LOCALAPPDATA%\MBR Forensic Workbench`.

## 2026-10-28 UI threading
All database queries, imports, hashing, discovery, downloads and report generation run in a `QThreadPool`; results return through queued signals. Worker objects are kept referenced until their result is delivered (otherwise PySide may collect them and drop the signal). Large tables use a virtual model that materialises only fetched 200-row pages.

## 2026-10-28 One workbench process per case
`case.lock` (pid) prevents a second process from opening the same case and running recovery against jobs that are still running in the first; a lock from a dead process is taken over.
