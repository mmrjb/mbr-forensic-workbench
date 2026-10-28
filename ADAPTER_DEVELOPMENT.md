# Adapter development

How to add or update a tool adapter and its output schema without weakening the guarantees.

## 1. Get evidence for the exact build
1. Download the official archive(s) and keep them outside the repository (never commit EZ binaries).
2. Run the read-only inspector:
   ```
   python scripts/inspect_tool_archives.py <folder-with-zips> --out src/mbrwb/catalogue/data/static_evidence.json
   ```
   It records archive SHA-256, main binary SHA-256, `ProductVersion`, runtime requirement, embedded signer names, sidecar folders and every option literal with its help text from the .NET user-string heap. Nothing is executed.
3. On Windows, after importing the tool, use **Tools > Verify this version**. It runs the installed executable with `--help` (argv list, no shell, 30 s timeout), stores the exact output with the executable hash in `verification_records`, and compares the modelled flags. This is the only path to `verified_help`.

Never infer a flag from a tool's display name, a blog post or memory. Online documentation is data; it never overrides this process.

## 2. Write or update the profile (`src/mbrwb/adapters/ez_cli.py`)
- New version family: add a new `AdapterProfile` with its own `profile_id` (e.g. `pecmd.2027.01`) and `version_family` regex. Keep the old profile; jobs record which one they used.
- `inputs`: `InputRole(key, flag, label, is_dir, artifact_types, required, group)`. Roles sharing a `group` are mutually exclusive alternatives (e.g. `-f` / `-d`).
- `options`: `OptionSpec(kind=...)`
  - `SWITCH` (flag alone), `BOOL` (flag + `true`/`false`), `TEXT` (with `pattern` + `pattern_hint`), `INT` (`minimum`/`maximum`), `CHOICE`, `FILE` (analyst-supplied file, not evidence), `DEPENDENCY` (resolved inside the installation folder and hashed).
  - `depends_on` / `conflicts` express cross-field rules; anything more complex goes into `validate_specific`.
  - Mark rarely used options `advanced=True`.
- `managed_outputs`: destinations the workbench owns. A managed output with `option=<key>` is emitted only when that toggle option is enabled; the toggle itself never emits a bare flag.
- `output_patterns`: glob -> kind, role, schema hint, importable. Order matters (first match wins), most specific first.
- `not_offered` + `notes`: list excluded switches and the reason (`--vss`, `--sync`, `--dt`, live modes).
- Use the `_evidence(tool_id)` helper so the profile states exactly which build it was checked against.

## 3. Tests that must pass
- `tests/test_adapter_profiles.py`: every modelled flag exists in the static evidence for that tool (or is listed in `CONFIRMED_ELSEWHERE` with a recorded targeted string check), version family matches the inspected build, no network/live switches.
- Add cases to `tests/test_jobs.py` style tests when the adapter has non-trivial validation (mutually exclusive inputs, required companions, conflicting options).
- If the adapter writes derived artifacts (like RLA), add a journey test that proves originals keep their hashes.

## 4. Output schema and timeline mapping
1. Add the output glob with a `schema_hint` (e.g. `wxtcmd.activity`).
2. Create `src/mbrwb/timeline/mappings/<mapping_id>.json`:
   ```json
   {"mapping_id": "wxtcmd.activity", "version": 1, "tool_id": "wxtcmd", "schema_hints": ["wxtcmd.activity"],
    "required_columns": ["Executable", "StartTime"], "timestamp_convention": "utc", "convention_basis": "...",
    "object_columns": ["Executable"], "events": [{"field": "StartTime", "meaning": "...", "category": "...", "description": "{Executable}"}],
    "column_verification": "how the column names were confirmed", "notes": ""}
   ```
3. Meanings must be precise and must not overstate (presence is not execution; a ShellBag time is not a folder visit; $MFT times are file system metadata).
4. Confirm column names: statically (property identifiers in the DLL, e.g. via the check used for the 2026.10 mappings) and, before calling it verified, against a real CSV header produced on Windows.
5. Changing a mapping's semantics = new `version`. Existing events keep their `mapping_version`; re-map a dataset to apply the new version.
6. If required columns are missing at import time the dataset is marked `unsupported_schema` and no events are created - never guess.

## 5. Coverage and docs
- Regenerate `TOOL_COVERAGE.md`: `python scripts/coverage_matrix.py > TOOL_COVERAGE.md`.
- Record verification evidence (help capture, real-tool runs) in `TEST_RESULTS.md`.
- Note consequential choices in `DECISIONS.md`.

## 6. Things an adapter must never do
- Return or execute a command string; use `shell=True`, `cmd.exe /c`, PowerShell for a parser, `eval`/`exec`.
- Resolve a DLL, plugin, map or batch file from an evidence location.
- Write output into an input directory.
- Add a generic `--csv`/`-f`/`-d` to a tool that does not have it.
- Mark a profile verified on the strength of documentation alone.
