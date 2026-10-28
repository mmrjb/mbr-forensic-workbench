"""Turn a workflow preset plus registered evidence into a reviewable plan.

Each step shows the evidence it will use, why it applies, its related inputs, and a status:
Ready, Needs setup (tool/runtime/version), Not present (no matching evidence), Not selected
(operator deselected), Blocked (preflight failed). Directory-mode tools get one job per
(host, folder) group rather than one per file; a folder that contains another group's folder
falls back to per-file jobs so hosts never mix. The plan snapshot is stored with the jobs and
is immutable afterwards.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from ..adapters.base import Request
from ..domain.enums import ErrorCategory, PlanStepStatus
from ..domain.models import EvidenceRef
from ..evidence.artifacts import label as type_label
from ..evidence.service import EvidenceService
from ..jobs.manager import JobManager
from ..storage.case_store import CaseStore, new_id, path_within
from ..storage.db import utcnow
from .presets import StepSpec, WorkflowSpec


@dataclass
class PlannedStep:
    key: str
    step_key: str
    tool_id: str
    title: str
    why: str
    inputs: dict[str, list[EvidenceRef]]
    options: dict
    host: str | None
    user: str | None
    status: PlanStepStatus
    reason: str = ""
    selected: bool = True
    prerequisites: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def evidence_label(self) -> str:
        parts = []
        for role, refs in self.inputs.items():
            for r in refs:
                parts.append(f"{role}: {r.path}")
        return "; ".join(parts) or "-"

    def snapshot(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["inputs"] = {k: [r.as_dict() for r in v] for k, v in self.inputs.items()}
        return d


@dataclass
class Plan:
    workflow: WorkflowSpec
    steps: list[PlannedStep]
    created_utc: str = field(default_factory=utcnow)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            st = PlanStepStatus.NOT_SELECTED if (not s.selected and s.status == PlanStepStatus.READY) else s.status
            out[st.value] = out.get(st.value, 0) + 1
        return out

    def summary(self) -> str:
        c = self.counts()
        order = [PlanStepStatus.READY, PlanStepStatus.NEEDS_SETUP, PlanStepStatus.BLOCKED, PlanStepStatus.NOT_PRESENT, PlanStepStatus.NOT_SELECTED, PlanStepStatus.NOT_APPLICABLE]
        return ", ".join(f"{c[s.value]} {s.value.lower()}" for s in order if c.get(s.value))


class Planner:
    def __init__(self, case: CaseStore, jobs: JobManager):
        self.case = case
        self.jobs = jobs
        self.ev = EvidenceService(case)

    def plan(self, wf: WorkflowSpec, *, evidence_filter: set[str] | None = None, hosts: set[str] | None = None) -> Plan:
        steps: list[PlannedStep] = []
        for spec in wf.steps:
            steps += self._plan_step(spec, evidence_filter, hosts)
        return Plan(wf, steps)

    def _candidates(self, spec: StepSpec, evidence_filter, hosts) -> list[EvidenceRef]:
        refs = self.ev.items(artifact_types=tuple(spec.artifact_types))
        if evidence_filter is not None:
            refs = [r for r in refs if r.id in evidence_filter]
        if hosts is not None:
            refs = [r for r in refs if r.host in hosts]
        return refs

    def _plan_step(self, spec: StepSpec, evidence_filter, hosts) -> list[PlannedStep]:
        refs = self._candidates(spec, evidence_filter, hosts)
        if not refs:
            types = ", ".join(type_label(t) for t in spec.artifact_types)
            return [PlannedStep(spec.key, spec.key, spec.tool_id, spec.title, spec.why, {}, dict(spec.options), None, None,
                                PlanStepStatus.NOT_PRESENT, f"No registered {types} in the selected evidence.", selected=False)]
        units: list[tuple[dict[str, list[EvidenceRef]], str | None, str | None]] = []
        if spec.input_mode == "file":
            for r in refs:
                inputs = {"file": [r]}
                for role, relation in spec.related.items():
                    rel = self._related(r, relation)
                    if rel is not None:
                        inputs[role] = [rel]
                units.append((inputs, r.host, r.user_profile))
        else:
            groups = self._groups(spec, refs)
            for g in groups:
                if g.is_dir:
                    role = "dir"
                    units.append(({role: [g]}, g.host, g.user_profile))
                else:
                    units.append(({"file": [g]}, g.host, g.user_profile))
        out = []
        for i, (inputs, host, user) in enumerate(units):
            key = f"{spec.key}#{i + 1}" if len(units) > 1 else spec.key
            st = PlannedStep(key, spec.key, spec.tool_id, spec.title, spec.why, inputs, dict(spec.options), host, user, PlanStepStatus.READY)
            if spec.tool_id == "jlecmd" and user and "user" not in st.options:
                st.options["user"] = user
            self._assess(st)
            out.append(st)
        return out

    def _groups(self, spec: StepSpec, refs: list[EvidenceRef]) -> list[EvidenceRef]:
        """One folder input per (host, user, folder); hive-folder tools (SBECmd, RECmd) get the
        folder holding the hives. Falls back to single files when folders nest across groups."""
        if spec.tool_id in ("sbecmd", "recmd"):
            by: dict[tuple, list[EvidenceRef]] = {}
            for r in refs:
                by.setdefault((r.host, r.user_profile if spec.tool_id == "sbecmd" else None, os.path.dirname(r.path)), []).append(r)
        else:
            by = {}
            for r in refs:
                by.setdefault((r.host, r.user_profile, os.path.dirname(r.path)), []).append(r)
        folders = [k[2] for k in by]
        out: list[EvidenceRef] = []
        for (host, user, folder), members in by.items():
            nested = any(f != folder and path_within(f, folder) for f in folders)
            if nested or spec.tool_id in ("amcacheparser", "appcompatcacheparser"):
                out += members
                continue
            out.append(EvidenceRef(id="grp:" + ",".join(m.id for m in members), path=folder, original_path=folder, artifact_type="any_directory",
                                   host=host, user_profile=user, volume=members[0].volume, size=sum(m.size or 0 for m in members), is_dir=True))
        return out

    def _related(self, ref: EvidenceRef, relation: str) -> EvidenceRef | None:
        for rel in self.ev.related(ref.id):
            if rel["relation"] == relation:
                other = rel["child_id"] if rel["parent_id"] == ref.id else rel["parent_id"]
                try:
                    return self.ev.ref(other)
                except Exception:
                    return None
        return None

    def _assess(self, st: PlannedStep) -> None:
        pf, _ctx = self.jobs.preflight_parser(self.case, st.tool_id, Request(st.inputs, st.options))
        setup = [p for p in pf.blockers if p.category in (ErrorCategory.TOOL_MISSING, ErrorCategory.RUNTIME_MISSING, ErrorCategory.UNSUPPORTED_VERSION, ErrorCategory.DEPENDENCY_MISSING)]
        other = [p for p in pf.blockers if p not in setup]
        st.warnings = [p.message for p in pf.warnings]
        st.prerequisites = [p.next_action for p in pf.problems if p.next_action]
        if setup:
            st.status = PlanStepStatus.NEEDS_SETUP
            st.reason = setup[0].message
            st.selected = False
        elif other:
            st.status = PlanStepStatus.BLOCKED
            st.reason = other[0].message
            st.selected = False
        else:
            st.reason = "; ".join(st.warnings) if st.warnings else "Ready to run."

    # -- persistence / execution ------------------------------------------------------------
    def save(self, plan: Plan, *, dry_run: bool) -> str:
        plan_id = new_id("plan")
        omissions = [{"key": s.key, "title": s.title, "status": (PlanStepStatus.NOT_SELECTED.value if not s.selected and s.status == PlanStepStatus.READY else s.status.value),
                      "reason": s.reason} for s in plan.steps if not s.selected or s.status != PlanStepStatus.READY]
        snapshot = {"workflow": plan.workflow.model_dump(), "steps": [s.snapshot() for s in plan.steps], "created_utc": plan.created_utc, "dry_run": dry_run}
        with self.case.db.write() as c:
            c.execute("INSERT INTO workflow_plans(id, case_id, preset_id, preset_version, created_utc, dry_run, snapshot_json, omissions_json, summary) VALUES (?,?,?,?,?,?,?,?,?)",
                      (plan_id, self.case.id, plan.workflow.id, plan.workflow.version, utcnow(), 1 if dry_run else 0,
                       json.dumps(snapshot, ensure_ascii=False), json.dumps(omissions, ensure_ascii=False), plan.summary()))
        self.case.audit("plan.saved", plan_id, {"preset": plan.workflow.id, "dry_run": dry_run, "summary": plan.summary()})
        return plan_id

    def run(self, plan: Plan, *, import_options: dict | None = None) -> tuple[str, list[str], list[tuple[str, str]]]:
        """Save the plan snapshot and submit every selected Ready step. Returns (plan id, job ids,
        [(step key, problem)] for steps that failed final preflight)."""
        plan_id = self.save(plan, dry_run=False)
        jobs, problems = [], []
        for s in plan.steps:
            if not s.selected or s.status != PlanStepStatus.READY:
                continue
            jid, pf = self.jobs.submit_parser(self.case, s.tool_id, Request(s.inputs, s.options), plan_id=plan_id, step_key=s.key,
                                              title=f"{s.title} ({s.host or 'unknown host'})", import_options=import_options)
            if jid:
                jobs.append(jid)
            else:
                problems.append((s.key, "; ".join(p.message for p in pf.blockers)))
        return plan_id, jobs, problems
