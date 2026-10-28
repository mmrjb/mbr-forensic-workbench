"""Job queue, execution and provenance.

Lifecycle: Queued -> Preflight -> Running -> Importing -> {Completed | Partial | Failed |
Cancelled | Timed out} (or Skipped / Interrupted). The parser outcome and the import
outcome are stored separately; the job state aggregates them without hiding either.

At submission everything that defines the run is frozen into the job row and ``job.json``:
executable path + SHA-256 + version, profile, argument array, dependency snapshot (maps,
batch files, plugins with hashes), inputs with their size/hash at submission, output root and
display timezone. Preflight re-checks the executable hash and input sizes before starting, so
a tool updated or evidence changed after submission is detected, never silently used.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..adapters.base import AdapterProfile, Request, ToolAdapter, ValidatedRequest, file_sha256
from ..adapters.registry import get_adapter
from ..catalogue.pe import RuntimeRequirement
from ..config.settings import atomic_write_text
from ..domain.enums import ErrorCategory, ImportOutcome, JobState, ParserOutcome
from ..domain.errors import Preflight
from ..domain.models import CompletionClassification, Invocation, OutputFile
from ..evidence.service import EvidenceService
from ..results.importer import DatasetContext, import_output
from ..storage.case_store import CaseStore, new_id, path_within
from ..storage.db import utcnow
from ..timeline.mapper import map_dataset
from ..tools.runtime import assess, scan_runtimes
from ..tools.store import ToolStore
from .process import RunningProcess

RuntimeChecker = Callable[[RuntimeRequirement], tuple[bool, list[str], list[str]]]


def default_runtime_checker(req: RuntimeRequirement) -> tuple[bool, list[str], list[str]]:
    a = assess(req, scan_runtimes())
    return a.ok, a.missing, a.guidance


@dataclass
class JobHandle:
    job_id: str
    case: CaseStore
    kind: str
    tool_id: str
    installation_id: str | None
    invocation: Invocation
    adapter: ToolAdapter | None
    profile: AdapterProfile | None
    exclusive: bool
    timeout_s: int | None
    plan_id: str | None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    process: RunningProcess | None = None
    import_options: dict = field(default_factory=dict)


class JobManager:
    def __init__(self, tools: ToolStore, *, max_parallel: int = 2, default_timeout_s: int = 4 * 3600,
                 runtime_checker: RuntimeChecker = default_runtime_checker, preview_lines: int = 2000):
        self.tools = tools
        self.max_parallel = max_parallel
        self.default_timeout_s = default_timeout_s
        self.runtime_checker = runtime_checker
        self.preview_lines = preview_lines
        self._queue: list[JobHandle] = []
        self._running: dict[str, JobHandle] = {}
        self._cond = threading.Condition()
        self._stop = False
        self._listeners: list[Callable[[str, str], None]] = []  # (case_id, job_id)
        self._line_listeners: list[Callable[[str, str, str], None]] = []  # (job_id, stream, line)
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="job-dispatcher", daemon=True)
        self._dispatcher.start()

    # -- events -------------------------------------------------------------------------
    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        self._listeners.append(fn)

    def add_line_listener(self, fn: Callable[[str, str, str], None]) -> None:
        self._line_listeners.append(fn)

    def _emit(self, case: CaseStore, job_id: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(case.id, job_id)
            except Exception:
                pass

    # -- submission ---------------------------------------------------------------------
    def preflight_parser(self, case: CaseStore | None, tool_id: str, request: Request) -> tuple[Preflight, dict]:
        """Validate without creating anything. Returns (preflight, context for submission)."""
        pf = Preflight()
        ctx: dict = {}
        adapter = get_adapter(tool_id)
        if adapter is None:
            pf.add(ErrorCategory.TOOL_MISSING, f"No reviewed adapter exists for {tool_id}.", "Use 'Open original tool' or another tool.")
            return pf, ctx
        inst = self.tools.selected(tool_id)
        caps = adapter.describe_capabilities(inst, self.tools.verified_hashes(tool_id) if inst else None)
        ctx.update(adapter=adapter, inst=inst, caps=caps)
        if inst is None:
            pf.add(ErrorCategory.TOOL_MISSING, f"{tool_id} is not installed.", "Open Tools and import or download it.")
            return pf, ctx
        if not Path(inst.executable).is_file():
            pf.add(ErrorCategory.TOOL_MISSING, f"The selected {tool_id} executable is missing: {inst.executable}", "Re-import the tool or select another version.")
            return pf, ctx
        if not caps.compatible or caps.profile is None:
            pf.add(ErrorCategory.UNSUPPORTED_VERSION, caps.message, "Use 'Verify this version' on the Tools page, or select a supported version.")
            return pf, ctx
        if inst.runtime_json:
            rj = json.loads(inst.runtime_json)
            req = RuntimeRequirement(rj.get("flavor", "unknown"), [tuple(x) for x in rj.get("frameworks", [])], rj.get("tfm"), rj.get("source", ""))
            ok, missing, guidance = self.runtime_checker(req)
            if not ok:
                pf.add(ErrorCategory.RUNTIME_MISSING, f"Required runtime not found: {', '.join(missing)}.", " ".join(guidance) or "Install the required .NET runtime.")
        if case is None:
            pf.add(ErrorCategory.INPUT_MISMATCH, "Evidence processing needs an open case.", "Create or open a case first.")
        prof = caps.profile
        pf.problems += adapter.validate_inputs(prof, request).problems
        extra = adapter.parse_extra_args(prof, request.extra_args_text, Preflight()) if request.extra_args_text.strip() else []
        deps, dep_snapshot, dpf = adapter.resolve_dependencies(inst, prof, request.options)
        pf.problems += dpf.problems
        if case is not None:
            for refs in request.inputs.values():
                for ref in refs:
                    if path_within(case.root, ref.path):
                        pf.add(ErrorCategory.INPUT_MISMATCH, f"The case folder is inside the input {ref.path}; outputs would be written into evidence.",
                               "Move the case or choose a narrower input.")
                    if path_within(ref.path, case.root / "jobs"):
                        pf.add(ErrorCategory.INPUT_MISMATCH, f"{ref.path} is a workbench output, not evidence.", "Import it as an external output instead.", blocking=False)
        ctx.update(profile=prof, deps=deps, dep_snapshot=dep_snapshot, extra=extra)
        return pf, ctx

    def submit_parser(self, case: CaseStore, tool_id: str, request: Request, *, plan_id: str | None = None, step_key: str | None = None,
                      title: str | None = None, retry_of: str | None = None, display_tz: str | None = None,
                      import_options: dict | None = None) -> tuple[str | None, Preflight]:
        pf, ctx = self.preflight_parser(case, tool_id, request)
        if not pf.ok:
            return None, pf
        adapter: ToolAdapter = ctx["adapter"]
        inst = ctx["inst"]
        prof: AdapterProfile = ctx["profile"]
        job_id = new_id("job")
        job_dir = case.job_dir(job_id)
        out_root = job_dir / "outputs"
        temp = job_dir / "temp"
        for d in (out_root, temp):
            d.mkdir(parents=True, exist_ok=True)
        vreq = ValidatedRequest(prof, inst, request.inputs, request.options, ctx["extra"], job_dir, out_root, temp, ctx["deps"])
        inv = adapter.build_invocation(vreq)
        inv.dependency_snapshot = {**inv.dependency_snapshot, **ctx["dep_snapshot"]}
        hosts = sorted({r.host for refs in request.inputs.values() for r in refs if r.host})
        users = sorted({r.user_profile for refs in request.inputs.values() for r in refs if r.user_profile})
        group = "; ".join(f"{k}: {os.path.basename(r.path.rstrip(os.sep)) or r.path}" for k, refs in request.inputs.items() for r in refs)
        self._insert_job(case, job_id, kind="parser", tool_id=tool_id, title=title or f"{tool_id} - {group}"[:200], inst=inst, profile=prof,
                         verification=str(ctx["caps"].verification), inv=inv, options=request.options, plan_id=plan_id, step_key=step_key,
                         retry_of=retry_of, group=group, host=", ".join(hosts) or None, user=", ".join(users) or None,
                         display_tz=display_tz or case.info.display_tz, job_dir=job_dir, out_root=out_root)
        for role, refs in request.inputs.items():
            for ref in refs:
                ev_ids = ref.id[4:].split(",") if ref.id.startswith("grp:") else [ref.id]
                with case.db.write() as c:
                    for eid in ev_ids:
                        exists = c.execute("SELECT size, sha256 FROM evidence WHERE id=?", (eid,)).fetchone()
                        c.execute("INSERT INTO job_inputs(id, job_id, evidence_id, role, path, size_at_submit, sha256_at_submit) VALUES (?,?,?,?,?,?,?)",
                                  (new_id("ji"), job_id, eid if exists else None, role, ref.path if not ref.id.startswith("grp:") else ref.path,
                                   _size(ref.path), exists["sha256"] if exists else ref.sha256))
        handle = JobHandle(job_id, case, "parser", tool_id, inst.id, inv, adapter, prof, prof.exclusive, self.default_timeout_s, plan_id,
                           import_options=import_options or {})
        self._write_job_json(case, job_id)
        case.audit("job.submitted", job_id, {"tool": tool_id, "plan": plan_id, "retry_of": retry_of})
        self._enqueue(handle)
        return job_id, pf

    def submit_invocation(self, case: CaseStore, *, kind: str, tool_id: str, inv: Invocation, title: str, installation_id: str | None = None,
                          exe_sha256: str | None = None, tool_version: str | None = None, exclusive: bool = False, timeout_s: int | None = None,
                          adapter: ToolAdapter | None = None, profile: AdapterProfile | None = None) -> str:
        """Maintenance / KAPE / specialist jobs that do not come from a parser form."""
        job_id = new_id("job")
        job_dir = case.job_dir(job_id)
        (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
        inst = self.tools.installation(installation_id) if installation_id else None
        self._insert_job(case, job_id, kind=kind, tool_id=tool_id, title=title, inst=inst, profile=profile, verification=None, inv=inv,
                         options={}, plan_id=None, step_key=None, retry_of=None, group=None, host=None, user=None, display_tz=case.info.display_tz,
                         job_dir=job_dir, out_root=Path(inv.output_root or job_dir / "outputs"), exe_sha256=exe_sha256, tool_version=tool_version)
        self._write_job_json(case, job_id)
        self._enqueue(JobHandle(job_id, case, kind, tool_id, installation_id, inv, adapter, profile, exclusive, timeout_s or self.default_timeout_s, None))
        return job_id

    def _insert_job(self, case, job_id, *, kind, tool_id, title, inst, profile, verification, inv: Invocation, options, plan_id, step_key, retry_of,
                    group, host, user, display_tz, job_dir, out_root, exe_sha256=None, tool_version=None) -> None:
        with case.db.write() as c:
            c.execute(
                """INSERT INTO jobs(id, case_id, plan_id, step_key, retry_of, kind, tool_id, title, installation_id, adapter_id, profile_id, verification,
                   executable, exe_sha256, tool_version, launcher_json, args_json, display_command, cwd, env_json, dependency_json, options_json,
                   evidence_group, host, user_profile, job_dir, output_root, display_tz, exclusive, timeout_s, requires_elevation, state, phase, created_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, case.id, plan_id, step_key, retry_of, kind, tool_id, title, inst.id if inst else None, tool_id if profile else None,
                 profile.profile_id if profile else None, verification, inv.executable, exe_sha256 or (inst.exe_sha256 if inst else None),
                 tool_version or (inst.product_version if inst else None), json.dumps(inv.launcher), json.dumps(inv.args, ensure_ascii=False), inv.display(),
                 inv.cwd, json.dumps(inv.env_overrides), json.dumps(inv.dependency_snapshot, ensure_ascii=False, default=str),
                 json.dumps(options, ensure_ascii=False, default=str), group, host, user, str(job_dir), str(out_root), display_tz,
                 1 if (profile and profile.exclusive) else 0, self.default_timeout_s, 1 if (profile and profile.requires_elevation) else 0,
                 JobState.QUEUED.value, "queued", utcnow()),
            )

    def _write_job_json(self, case: CaseStore, job_id: str) -> None:
        row = dict(case.db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,)))
        inputs = [dict(r) for r in case.db.query("SELECT * FROM job_inputs WHERE job_id=?", (job_id,))]
        outputs = [dict(r) for r in case.db.query("SELECT * FROM outputs WHERE job_id=?", (job_id,))]
        for k in ("args_json", "launcher_json", "env_json", "dependency_json", "options_json", "warnings_json", "classification_json"):
            if row.get(k):
                row[k[:-5]] = json.loads(row.pop(k))
            else:
                row.pop(k, None)
        doc = {"job": row, "inputs": inputs, "outputs": outputs,
               "note": "argv = launcher + [executable] + args. display_command is for reading only; the job was not run through a shell."}
        atomic_write_text(Path(row["job_dir"]) / "job.json", json.dumps(doc, indent=1, ensure_ascii=False, default=str))

    # -- queue ----------------------------------------------------------------------------
    def _enqueue(self, h: JobHandle) -> None:
        with self._cond:
            self._queue.append(h)
            self._cond.notify_all()
        self._emit(h.case, h.job_id)

    def _dispatch_loop(self) -> None:
        while True:
            with self._cond:
                while not self._stop:
                    nxt = self._next_eligible()
                    if nxt is not None:
                        break
                    self._cond.wait(0.5)
                if self._stop:
                    return
                self._queue.remove(nxt)
                self._running[nxt.job_id] = nxt
            threading.Thread(target=self._run_job, args=(nxt,), name=f"job-{nxt.job_id}", daemon=True).start()

    def _next_eligible(self) -> JobHandle | None:
        if any(h.exclusive for h in self._running.values()):
            return None
        for h in self._queue:
            if h.cancel_event.is_set():
                return h  # finish it as Cancelled promptly
            if h.exclusive and not self._running:
                return h
            if not h.exclusive and len(self._running) < self.max_parallel:
                return h
        return None

    def set_max_parallel(self, n: int) -> None:
        with self._cond:
            self.max_parallel = max(1, n)
            self._cond.notify_all()

    # -- control --------------------------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        with self._cond:
            for h in self._queue + list(self._running.values()):
                if h.job_id == job_id:
                    h.cancel_event.set()
                    if h.process is not None:
                        h.process.cancel()
                    self._cond.notify_all()
                    return True
        return False

    def cancel_plan(self, plan_id: str) -> int:
        with self._cond:
            ids = [h.job_id for h in self._queue + list(self._running.values()) if h.plan_id == plan_id]
        for j in ids:
            self.cancel(j)
        return len(ids)

    def active_count(self, case_id: str | None = None) -> int:
        with self._cond:
            return sum(1 for h in self._queue + list(self._running.values()) if case_id is None or h.case.id == case_id)

    def active_jobs(self) -> list[tuple[str, str]]:
        with self._cond:
            return [(h.case.id, h.job_id) for h in self._queue + list(self._running.values())]

    def shutdown(self, timeout: float = 15) -> None:
        """Cancel everything (whole process trees) and wait briefly. Called on application exit."""
        with self._cond:
            handles = self._queue + list(self._running.values())
            self._stop = True
            self._cond.notify_all()
        for h in handles:
            h.cancel_event.set()
            if h.process is not None:
                h.process.cancel()
        deadline = threading.Event()
        for _ in range(int(timeout * 10)):
            with self._cond:
                if not self._running:
                    break
            deadline.wait(0.1)

    def retry(self, case: CaseStore, job_id: str) -> tuple[str | None, Preflight]:
        """Re-run a parser job into a fresh job folder, linked to the original. The originally
        frozen installation must still exist; otherwise the operator reconfigures explicitly."""
        row = case.db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        pf = Preflight()
        if row is None or row["kind"] != "parser":
            pf.add(ErrorCategory.INPUT_MISMATCH, "Only parser jobs can be retried from here.", "Start a new analysis instead.")
            return None, pf
        sel = self.tools.selected(row["tool_id"])
        if sel is None or sel.exe_sha256 != row["exe_sha256"]:
            pf.add(ErrorCategory.UNSUPPORTED_VERSION, "The tool version used by the original job is not the selected version anymore.",
                   "Select the original version on the Tools page, or configure a new analysis with the current version.")
            return None, pf
        ev = EvidenceService(case)
        inputs: dict[str, list] = {}
        for r in case.db.query("SELECT * FROM job_inputs WHERE job_id=?", (job_id,)):
            if r["evidence_id"]:
                ref = ev.ref(r["evidence_id"])
                if any(x.path == r["path"] for x in inputs.get(r["role"], [])):
                    continue
                if os.path.isdir(r["path"]):
                    from ..domain.models import EvidenceRef
                    ref = EvidenceRef(id=ref.id, path=r["path"], original_path=r["path"], artifact_type=None, host=ref.host, user_profile=ref.user_profile, is_dir=True)
                inputs.setdefault(r["role"], []).append(ref)
            else:
                from ..domain.models import EvidenceRef
                inputs.setdefault(r["role"], []).append(EvidenceRef(id="path", path=r["path"], original_path=r["path"], artifact_type=None, is_dir=os.path.isdir(r["path"])))
        options = json.loads(row["options_json"] or "{}")
        return self.submit_parser(case, row["tool_id"], Request(inputs, options), plan_id=row["plan_id"], step_key=row["step_key"],
                                  title=f"Retry of {row['title']}", retry_of=job_id, display_tz=row["display_tz"])

    # -- execution --------------------------------------------------------------------------
    def _set(self, case: CaseStore, job_id: str, **fields) -> None:
        with case.db.write() as c:
            c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))
        self._emit(case, job_id)

    def _run_job(self, h: JobHandle) -> None:
        case = h.case
        acquired = False
        try:
            if h.cancel_event.is_set():
                self._finish(h, JobState.CANCELLED, ParserOutcome.NOT_RUN, ImportOutcome.NOT_APPLICABLE, ErrorCategory.CANCELLED,
                             "Cancelled before it started.", "")
                return
            self._set(case, h.job_id, state=JobState.PREFLIGHT.value, phase="checking tool and inputs")
            problem = self._run_preflight(h)
            if problem:
                cat, msg, nxt = problem
                self._finish(h, JobState.FAILED, ParserOutcome.NOT_RUN, ImportOutcome.NOT_APPLICABLE, cat, msg, nxt)
                return
            if h.installation_id:
                self.tools.acquire(h.installation_id)
                acquired = True
            jd = case.job_dir(h.job_id)
            self._set(case, h.job_id, state=JobState.RUNNING.value, phase="running", started_utc=utcnow())

            def on_line(stream: str, line: str) -> None:
                for fn in list(self._line_listeners):
                    try:
                        fn(h.job_id, stream, line)
                    except Exception:
                        pass
            rp = RunningProcess(h.invocation, jd / "stdout.log", jd / "stderr.log", on_line=on_line, preview_lines=self.preview_lines)
            h.process = rp
            rp.start()
            if rp.pid:
                self._set(case, h.job_id, pid=rp.pid)
            result = rp.wait(h.timeout_s)
            out_root = Path(case.db.scalar("SELECT output_root FROM jobs WHERE id=?", (h.job_id,)))
            outputs = h.adapter.discover_outputs(h.profile, out_root) if h.adapter and h.profile else _generic_outputs(out_root)
            if h.adapter and h.profile:
                cls = h.adapter.classify_completion(h.profile, result, outputs)
            else:
                cls = _generic_classification(result, outputs)
            self._set(case, h.job_id, exit_code=result.exit_code, ended_utc=result.ended_utc, parser_outcome=cls.parser_outcome.value,
                      classification_json=json.dumps(cls.as_dict(), ensure_ascii=False), warnings_json=json.dumps(cls.warnings, ensure_ascii=False))
            parser_ok = cls.parser_outcome in (ParserOutcome.SUCCESS, ParserOutcome.SUCCESS_WITH_WARNINGS)
            output_ids = self._register_outputs(h, outputs, out_root, complete=parser_ok, log_paths=(jd / "stdout.log", jd / "stderr.log"))
            if not parser_ok:
                state = {ParserOutcome.CANCELLED: JobState.CANCELLED, ParserOutcome.TIMED_OUT: JobState.TIMED_OUT}.get(cls.parser_outcome, JobState.FAILED)
                self._finish(h, state, cls.parser_outcome, ImportOutcome.NOT_APPLICABLE, cls.error_category, cls.reason, cls.next_action)
                return
            self._set(case, h.job_id, state=JobState.IMPORTING.value, phase="importing outputs")
            import_outcome, import_note = self._import_outputs(h, output_ids)
            self._post_process(h, outputs)
            state = JobState.COMPLETED
            msg = cls.reason
            cat = None
            nxt = ""
            if cls.parser_outcome == ParserOutcome.SUCCESS_WITH_WARNINGS:
                state = JobState.PARTIAL
                nxt = "Review the warnings; usable records were imported."
            if import_outcome in (ImportOutcome.FAILED, ImportOutcome.UNSUPPORTED_SCHEMA, ImportOutcome.PARTIAL):
                state = JobState.PARTIAL
                cat = ErrorCategory.IMPORT_FAILURE if import_outcome == ImportOutcome.FAILED else (
                    ErrorCategory.UNSUPPORTED_OUTPUT_SCHEMA if import_outcome == ImportOutcome.UNSUPPORTED_SCHEMA else None)
                nxt = "Raw outputs are preserved; open the Outputs tab for importer details."
            if import_outcome == ImportOutcome.CANCELLED:
                state, cat = JobState.CANCELLED, ErrorCategory.CANCELLED
                nxt = "Parser output is preserved; re-import it from the Outputs tab."
            self._finish(h, state, cls.parser_outcome, import_outcome, cat, (msg + " " + import_note).strip(), nxt)
        except Exception as exc:  # storage failures, unexpected bugs: never leave a job Running
            tb = traceback.format_exc()
            try:
                (case.job_dir(h.job_id) / "workbench_error.log").write_text(tb, encoding="utf-8")
            except OSError:
                pass
            try:
                self._finish(h, JobState.FAILED, ParserOutcome.NOT_RUN if h.process is None else ParserOutcome.FAILED, ImportOutcome.NOT_APPLICABLE,
                             ErrorCategory.STORAGE_FAILURE if isinstance(exc, OSError) else ErrorCategory.PROCESS_FAILURE,
                             f"{type(exc).__name__}: {exc}", "See workbench_error.log in the job folder.")
            except Exception:
                pass
        finally:
            if acquired:
                self.tools.release(h.installation_id)
            with self._cond:
                self._running.pop(h.job_id, None)
                self._cond.notify_all()

    def _run_preflight(self, h: JobHandle) -> tuple[ErrorCategory, str, str] | None:
        case = h.case
        row = case.db.query_one("SELECT exe_sha256, executable FROM jobs WHERE id=?", (h.job_id,))
        exe = Path(row["executable"])
        if not exe.is_file():
            return ErrorCategory.TOOL_MISSING, f"Executable missing at run time: {exe}", "Re-import the tool; the queued job keeps its frozen identity."
        if row["exe_sha256"]:
            actual = file_sha256(exe)
            if actual != row["exe_sha256"]:
                return (ErrorCategory.UNSUPPORTED_VERSION, "The executable changed after submission (hash mismatch). The job was not run.",
                        "Submit a new analysis; it will record the new executable.")
        for r in case.db.query("SELECT * FROM job_inputs WHERE job_id=?", (h.job_id,)):
            p = r["path"]
            if not os.path.exists(p):
                return ErrorCategory.INPUT_UNAVAILABLE, f"Input not accessible at run time: {p}", "Reconnect the evidence location and retry."
            size = _size(p)
            case.db.execute("UPDATE job_inputs SET size_at_run=?, checked_at_run_utc=? WHERE id=?", (size, utcnow(), r["id"]))
            if r["size_at_submit"] is not None and size is not None and size != r["size_at_submit"] and not os.path.isdir(p):
                return ErrorCategory.INPUT_MISMATCH, f"{p} changed size after submission.", "Register the new version of this evidence and resubmit."
        if h.invocation.requires_elevation and not _is_elevated():
            return ErrorCategory.ACCESS_DENIED, "This operation needs administrator rights.", "Restart the workbench as administrator only for this specialist operation."
        return None

    def _register_outputs(self, h: JobHandle, outputs: list[OutputFile], out_root: Path, *, complete: bool, log_paths) -> list[tuple[str, OutputFile]]:
        case = h.case
        ids = []
        with case.db.write() as c:
            for o in outputs:
                oid = new_id("out")
                importable = o.importable and o.kind in ("csv", "json", "jsonl")
                c.execute("""INSERT INTO outputs(id, case_id, job_id, origin, rel_path, abs_path, kind, role, size, sha256, completion, import_state,
                             created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (oid, case.id, h.job_id, "job", o.rel_path, str(o.path), o.kind, o.role, o.size, file_sha256(o.path),
                           "complete" if complete else "quarantined", "pending" if (importable and complete) else ImportOutcome.NOT_APPLICABLE.value, utcnow()))
                ids.append((oid, o))
            for lp in log_paths:
                if lp.exists():
                    c.execute("""INSERT INTO outputs(id, case_id, job_id, origin, rel_path, abs_path, kind, role, size, sha256, completion, import_state,
                                 created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (new_id("out"), case.id, h.job_id, "job", lp.name, str(lp), "log", "console_log" if "stdout" in lp.name else "console_errors",
                               lp.stat().st_size, file_sha256(lp), "complete", ImportOutcome.NOT_APPLICABLE.value, utcnow()))
        return ids

    def _import_outputs(self, h: JobHandle, output_ids: list[tuple[str, OutputFile]]) -> tuple[ImportOutcome, str]:
        case = h.case
        job = case.db.query_one("SELECT * FROM jobs WHERE id=?", (h.job_id,))
        ev_ids = [r["evidence_id"] for r in case.db.query("SELECT DISTINCT evidence_id FROM job_inputs WHERE job_id=? AND evidence_id IS NOT NULL", (h.job_id,))]
        outcomes: list[ImportOutcome] = []
        notes = []
        source_tz = h.import_options.get("source_tz")
        for oid, o in output_ids:
            if not (o.importable and o.kind in ("csv", "json", "jsonl")):
                continue
            if h.cancel_event.is_set():
                case.db.execute("UPDATE outputs SET import_state=? WHERE id=?", (ImportOutcome.CANCELLED.value, oid))
                outcomes.append(ImportOutcome.CANCELLED)
                continue
            case.db.execute("UPDATE outputs SET import_state='importing' WHERE id=?", (oid,))
            ctx = DatasetContext(case_id=case.id, name=o.rel_path, source_path=o.path, source_format="csv" if o.kind == "csv" else "json", origin="job",
                                 output_id=oid, job_id=h.job_id, tool_id=h.tool_id, tool_version=job["tool_version"], schema_hint=o.schema_hint,
                                 host=job["host"], user_profile=job["user_profile"], evidence_ids=ev_ids,
                                 source_sha256=case.db.scalar("SELECT sha256 FROM outputs WHERE id=?", (oid,)))
            res = import_output(case.results, ctx, cancel=h.cancel_event)
            mapping_note = ""
            if res.state in ("complete", "partial"):
                mo = map_dataset(case.results, res.dataset_id, schema_hint=o.schema_hint, source_tz=source_tz, cancel=h.cancel_event)
                mapping_note = f"timeline: {mo.state}" + (f" ({mo.events} events)" if mo.state == "mapped" else "")
                if mo.state == "unsupported_schema":
                    outcomes.append(ImportOutcome.UNSUPPORTED_SCHEMA)
                    notes.append(f"{o.rel_path}: {mo.detail}")
            state = {"complete": ImportOutcome.EMPTY if res.stats.total == 0 else ImportOutcome.IMPORTED, "partial": ImportOutcome.PARTIAL,
                     "cancelled": ImportOutcome.CANCELLED}.get(res.state, ImportOutcome.FAILED)
            outcomes.append(state)
            detail = f"{res.stats.total} records ({res.stats.ok} ok, {res.stats.adjusted} adjusted, {res.stats.rejected} rejected); {mapping_note}"
            if res.error:
                detail = f"{res.error}"
                notes.append(f"{o.rel_path}: {res.error}")
            case.db.execute("UPDATE outputs SET import_state=?, import_detail=?, dataset_ids_json=? WHERE id=?",
                            (state.value, detail, json.dumps([res.dataset_id]), oid))
        if not outcomes:
            return ImportOutcome.NOT_APPLICABLE, ""
        for worst in (ImportOutcome.CANCELLED, ImportOutcome.FAILED, ImportOutcome.UNSUPPORTED_SCHEMA, ImportOutcome.PARTIAL):
            if worst in outcomes:
                return worst, " ".join(notes)[:2000]
        if all(o == ImportOutcome.EMPTY for o in outcomes):
            return ImportOutcome.EMPTY, "Outputs contained zero records (the tool produced valid, empty results)."
        return ImportOutcome.IMPORTED, ""

    def _post_process(self, h: JobHandle, outputs: list[OutputFile]) -> None:
        """Register RLA's replayed hives as derived evidence with parent links, and a KAPE
        Targets collection as a new evidence root carrying the job's provenance."""
        if h.tool_id == "kape":
            coll = Path(h.invocation.output_root) / "kape_collection"
            if coll.is_dir() and any(coll.rglob("*")):
                from ..evidence.discovery import discover
                rep = discover(coll)
                EvidenceService(h.case).register(rep, mode="in_place", collected_by_job=h.job_id, label=f"KAPE collection (job {h.job_id})")
            return
        if h.tool_id != "rla":
            return
        case = h.case
        ev = EvidenceService(case)
        parents = [r["evidence_id"] for r in case.db.query("SELECT evidence_id FROM job_inputs WHERE job_id=? AND evidence_id IS NOT NULL", (h.job_id,))]
        prow = {p: case.db.query_one("SELECT host, user_profile, artifact_type, original_path FROM evidence WHERE id=?", (p,)) for p in parents}
        for o in outputs:
            if o.role != "rla.derived_hive":
                continue
            matching = [p for p, r in prow.items() if r and os.path.basename(r["original_path"]).lower() == o.path.name.lower()] or parents
            first = prow.get(matching[0]) if matching else None
            ev.register_derived(o.path, parents=matching, relation="derived_hive", artifact_type=first["artifact_type"] if first else "registry_hive",
                                host=first["host"] if first else None, user_profile=first["user_profile"] if first else None,
                                note=f"Replayed by RLA in job {h.job_id}; the original hive and logs were not modified.")

    def _finish(self, h: JobHandle, state: JobState, parser: ParserOutcome, imp: ImportOutcome, cat: ErrorCategory | None, msg: str, nxt: str) -> None:
        case = h.case
        if state in (JobState.CANCELLED, JobState.TIMED_OUT, JobState.FAILED):
            with case.db.write() as c:
                c.execute("UPDATE outputs SET completion='quarantined' WHERE job_id=? AND origin='job' AND kind NOT IN ('log')", (h.job_id,))
        self._set(case, h.job_id, state=state.value, phase="finished", parser_outcome=parser.value, import_outcome=imp.value,
                  error_category=cat.value if cat else None, error_message=msg[:4000], next_action=nxt,
                  ended_utc=case.db.scalar("SELECT COALESCE(ended_utc, ?) FROM jobs WHERE id=?", (utcnow(), h.job_id)))
        self._write_job_json(case, h.job_id)
        case.audit("job.finished", h.job_id, {"state": state.value, "parser": parser.value, "import": imp.value})


def _size(p: str) -> int | None:
    try:
        if os.path.isdir(p):
            return None
        return os.path.getsize(p)
    except OSError:
        return None


def _is_elevated() -> bool:
    if os.name == "nt":  # pragma: no cover
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


def _generic_outputs(out_root: Path) -> list[OutputFile]:
    from ..adapters.base import _kind_from_suffix

    res = []
    if out_root.exists():
        for p in sorted(out_root.rglob("*")):
            if p.is_file() and not p.is_symlink():
                res.append(OutputFile(p, p.relative_to(out_root).as_posix(), _kind_from_suffix(p), "maintenance_output", None, False, p.stat().st_size))
    return res


def _generic_classification(result, outputs) -> CompletionClassification:
    if result.launch_error:
        return CompletionClassification(ParserOutcome.FAILED, f"Could not start: {result.launch_error}", error_category=ErrorCategory.PROCESS_FAILURE)
    if result.cancelled:
        return CompletionClassification(ParserOutcome.CANCELLED, "Cancelled by the operator.", error_category=ErrorCategory.CANCELLED)
    if result.timed_out:
        return CompletionClassification(ParserOutcome.TIMED_OUT, "Timed out.", error_category=ErrorCategory.TIMEOUT)
    if result.exit_code != 0:
        return CompletionClassification(ParserOutcome.FAILED, f"Exit code {result.exit_code}.", error_category=ErrorCategory.PROCESS_FAILURE,
                                        next_action="Read the job log.")
    return CompletionClassification(ParserOutcome.SUCCESS, "Exit code 0.", [], [f"{o.rel_path}" for o in outputs])
