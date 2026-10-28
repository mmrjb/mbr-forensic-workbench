# Architecture

## Modules (`src/mbrwb`)

| Package | Responsibility |
|---|---|
| `domain/` | Enums (job states, error categories, timestamp statuses, verification levels), structured `Problem`/`Preflight`, value objects (`InstallationRef`, `EvidenceRef`, `Invocation`, `ProcessResult`, `CompletionClassification`). |
| `config/` | Product identity (configurable name), per-user paths / portable mode, typed settings with atomic writes, recovery and export/import preview. |
| `storage/` | `Database` (thread-local connections, WAL, serialised writes, migrations, backup API), schemas for `app`, `case`, `results`; `CaseStore` (case folder layout, lock, recovery, archival). |
| `catalogue/` | Reviewed tool manifest (`data/catalogue.json`), static archive evidence (`data/static_evidence.json`), read-only PE/.NET inspector (`pe.py`), upstream page reconciliation. |
| `tools/` | Safe archive extraction, installation store (staging, promotion, retention, rollback, bundles, KAPE in place), allowlisted downloads, .NET runtime detection, Authenticode, GUI launcher, readiness inventory, help-capture verification. |
| `adapters/` | Adapter contract (`base.py`), Windows argument rules (`winargs.py`), EZ CLI profiles (`ez_cli.py`), KAPE (`kape.py`), Get-ZimmermanTools (`maintenance.py`), registry. |
| `jobs/` | Process runner with whole-tree control (`process.py`) and the job manager (`manager.py`): submission, preflight, queue, outcome aggregation, outputs, import, recovery, retry. |
| `evidence/` | Artifact vocabulary, discovery/classification, registration, hashing, relationships, availability, derived artifacts. |
| `workflows/` | Declarative presets + custom workflow schema (`presets.py`), planner (`planner.py`). |
| `results/` | Streaming importer (CSV / JSONL / JSON array), paged queries, case search, exports. |
| `timeline/` | Timestamp semantics (`timestamps.py`), versioned mappings (`mappings/*.json`), mapper. |
| `findings/` | Bookmarks, notes, versioned findings with reference snapshots. |
| `reports/` | HTML / Markdown reports, manifest, redaction, drafts. |
| `ui/` | Theme tokens and QSS, icons, context (services + thread pool), main window, pages, dialogs, widgets (paged model, histogram). |
| `samples.py` | Synthetic DEMO case. |

Layering: `ui` → services (`jobs`, `evidence`, `workflows`, `results`, `timeline`, `findings`, `reports`, `tools`) → `adapters`/`catalogue` → `storage`/`domain`. Nothing below `ui` imports Qt.

## Case folder

```
CaseRoot/
  case.json            marker, case id, title, layout version (atomic write)
  case.lock            pid of the workbench process that has the case open
  case.sqlite          metadata
  results.sqlite       dataset row tables + timeline events
  evidence_manifest/   <root-id>_<timestamp>.json per registration (append-only)
  working_copies/      optional working copies, relative structure preserved
  jobs/<job-id>/       job.json, stdout.log, stderr.log, outputs/, temp/, workbench_error.log
  reports/  exports/
```

## Case schema (`case.sqlite`, migration 1)

- `cases(id, title, examiner, description, reference, display_tz, tags, is_demo, created_utc, updated_utc)`
- `evidence_roots(id, case_id, path, kind, mode, working_path, label, host, registered_utc, files_inspected, candidates, unavailable, excluded, discovery_state, manifest_path)`
- `evidence(id, case_id, root_id, version, supersedes, original_path, working_path, rel_path, artifact_type, artifact_basis, host, user_profile, volume, size, mtime_utc, ctime_utc, atime_utc, imported_utc, hash_state, sha256, hash_note, availability, availability_checked_utc, note)`
- `evidence_relations(id, parent_id, child_id, relation, basis, created_utc)` - relations: `transaction_log`, `sqlite_sidecar`, `same_volume_mft`, `software_hive`, `derived_hive`, operator-defined.
- `workflow_plans(id, case_id, preset_id, preset_version, created_utc, dry_run, snapshot_json, omissions_json, summary)` - immutable snapshot of every step, its inputs, options and status.
- `jobs(...)` - frozen identity: `installation_id, profile_id, verification, executable, exe_sha256, tool_version, launcher_json, args_json, display_command, cwd, env_json, dependency_json, options_json, output_root, display_tz`; lifecycle: `state, phase, created/started/ended_utc, exit_code, parser_outcome, import_outcome, error_category, error_message, next_action, warnings_json, classification_json, pid, retry_of, plan_id, step_key`.
- `job_inputs(id, job_id, evidence_id, role, path, size_at_submit, sha256_at_submit, size_at_run, checked_at_run_utc)`
- `outputs(id, case_id, job_id, origin, rel_path, abs_path, kind, role, size, sha256, completion, import_state, import_detail, dataset_ids_json, declared_tool, declared_version, parent_evidence_json, created_utc)` - `origin` is `job`, `external` (imported output with declared metadata only) or `gui_session`.
- `bookmarks`, `notes` (amendments reference earlier notes), `findings`, `finding_versions` (snapshot per save), `finding_links` (immutable reference + display snapshot), `reports` (path, sha256, options, finding versions, redaction policy), `report_drafts`, `saved_views`, `launch_sessions`, `audit_log`.

## Results schema (`results.sqlite`)

- `datasets(id, case_id, output_id, job_id, origin, tool_id, tool_version, name, source_path, source_sha256, source_size, source_format, schema_profile, importer_version, encoding, columns_json, host, user_profile, evidence_ids_json, state, rows_total, rows_ok, rows_adjusted, rows_rejected, rejected_samples_json, started_utc, completed_utc, error, row_table, search_index, timeline_state, timeline_mapping, timeline_events, timeline_unresolved, superseded_by)`
- `ds_<id>(rec_id INTEGER PRIMARY KEY, line_start, line_end, byte_start, byte_end, rstatus, extra, c0 ... cN)` - raw strings, one table per dataset; sort indexes are created on demand.
- `timeline_events(id, case_id, dataset_id, rec_id, host, user_profile, job_id, tool_id, tool_version, source_output, field, raw_value, utc_ticks, precision_digits, ts_status, ts_assumption, meaning, category, object_path, description, mapping_id, mapping_version)`

Import atomicity: the dataset row is committed first with `state='importing'`; all rows plus the final state are committed in one transaction. On restart, datasets still `importing` have their table dropped and are marked `interrupted`; the original output file is untouched and can be re-imported.

## Process lifecycle

1. **Submit** (`JobManager.submit_parser`): adapter preflight (inputs, mutually exclusive roles, option types/patterns/dependencies/conflicts, extra-argument whitelist), tool present, version profile, runtime, dependency resolution from the installation folder with hashes, case/evidence overlap. The invocation (argv list) and every identity are frozen into `jobs` and `job.json`. State `Queued`.
2. **Dispatch**: at most `max_parallel` jobs; an `exclusive` job (VSCMount, KAPE) runs alone.
3. **Preflight at run time**: executable exists and its SHA-256 still matches; inputs exist and their size is unchanged; elevation check for specialist jobs. The installation is marked in use (never removed by retention).
4. **Running**: `RunningProcess` starts the argv with `shell=False`, stdin closed, no console window; stdout/stderr are drained by threads into raw log files and a bounded preview. Windows: Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; cancel/timeout use `TerminateJobObject`; if the workbench dies the OS kills the tree. Limitation: `subprocess` cannot start the child suspended, so a grandchild spawned in the microseconds before assignment could escape; the EZ tools do not spawn children at start-up. POSIX: new session + `killpg` (SIGTERM, then SIGKILL).
5. **Classify** (`ToolAdapter.classify_completion`): launch error, cancelled, timed out, non-zero exit (failed; files quarantined), zero exit with console warnings (success with warnings), zero exit without expected outputs (legitimately empty if the tool says so, else *Output missing*), console-only operations.
6. **Register outputs** with SHA-256; completion `complete` only when the parser succeeded.
7. **Import** each importable output (separate outcome per output), then **timeline mapping** (separate state per dataset).
8. **Aggregate**: Completed / Partial (warnings, rejected rows, unsupported schema, import failure) / Failed / Cancelled / Timed out. RLA derived hives are registered as evidence with parent links.
9. **Recovery**: on case open, jobs still `Queued/Preflight/Running/Importing` become `Interrupted`; partial outputs are quarantined; nothing is re-run. **Retry** creates a new job folder linked by `retry_of` and requires the originally frozen tool version to still be selected.

## Adapter boundaries

`ToolAdapter` (see `adapters/base.py`):
- `describe_capabilities(installation)` - version profile match and verification level.
- `validate_inputs(profile, request)` - generic + `validate_specific`.
- `resolve_dependencies(installation, profile, options)` - maps / batch files / plugins only from inside the installation folder, hashed.
- `build_invocation(validated_request, create_dirs=True)` - returns an `Invocation` (absolute executable, argv list, trusted cwd = installation folder, env overrides `TMP/TEMP` = job temp, input refs, output root, dependency snapshot). No string is ever executed.
- `classify_completion(profile, result, outputs)`, `discover_outputs(profile, output_root)`.
- Output importers: `results/importer.py` (`inspect` = JSON layout sniffing, `import_output`); timeline: `timeline/mapper.py:map_dataset` with a `MappingProfile`.

## UI threading
`AppContext.run_bg` runs callables in `QThreadPool` and delivers results through queued signals on the GUI thread. `PagedTableModel` asks the pool for the row count and for 200-row pages on demand (LRU of 60 pages) and ignores results from an outdated filter generation. Job events from worker threads are emitted as Qt signals; job log lines go to a bounded per-job deque polled by the Jobs page.
