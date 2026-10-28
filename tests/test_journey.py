"""Realistic core journey with FAKE tools: case -> evidence -> plan -> run -> results ->
bookmark -> finding -> report -> reopen. Proves the workbench's own logic, not EZ tools."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from harness import make_bench, register
from mbrwb.domain.enums import PlanStepStatus
from mbrwb.findings.service import FindingsService, Reference
from mbrwb.reports.report import ReportOptions, generate
from mbrwb.results.query import DatasetQuery, DatasetView, Filter, TimelineQuery, TimelineView, export_dataset_csv, search_case
from mbrwb.storage.case_store import CaseStore
from mbrwb.workflows.planner import Planner
from mbrwb.workflows.presets import PRESET_BY_ID, load_custom, CUSTOM_TEMPLATE
from pe_builder import build_pe


def _collection(tmp: Path) -> Path:
    root = tmp / "collection"
    pf = root / "LAB-WIN-01" / "C" / "Windows" / "Prefetch"
    pf.mkdir(parents=True)
    (pf / "CALC.EXE-3FBEF7FD.pf").write_bytes(b"\x1e\x00\x00\x00SCCA" + b"\0" * 300)
    (pf / "CMD.EXE-0BD30981.pf").write_bytes(b"MAM\x04" + b"\0" * 300)
    logs = root / "LAB-WIN-01" / "C" / "Windows" / "System32" / "winevt" / "Logs"
    logs.mkdir(parents=True)
    (logs / "Security.evtx").write_bytes(b"ElfFile\x00" + b"\0" * 300)
    vol = root / "LAB-WIN-01" / "C"
    (vol / "$MFT").write_bytes(b"FILE0" + b"\0" * 1019)
    cfg = vol / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    (cfg / "SYSTEM").write_bytes(b"regf" + b"\0" * 4092)
    (cfg / "SYSTEM.LOG1").write_bytes(b"regf" + b"\0" * 500)
    (root / "LAB-WIN-01" / "notes.txt").write_text("unrelated")
    return root


def _hashes(root: Path) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_full_journey(tmp_path):
    b = make_bench(tmp_path, tools=("pecmd", "mftecmd", "evtxecmd"))
    try:
        coll = _collection(tmp_path)
        before = _hashes(coll)
        root_id = register(b, coll, host="LAB-WIN-01")
        types = {r["artifact_type"] for r in b.case.db.query("SELECT artifact_type FROM evidence")}
        assert {"prefetch_file", "evtx_file", "ntfs_mft", "system_hive", "registry_transaction_log"} <= types
        rel = b.case.db.query("SELECT relation FROM evidence_relations")
        assert any(r["relation"] == "transaction_log" for r in rel), "hive and its log are linked"
        counts = b.evidence.hash_pending(root_id=root_id)
        assert counts["hashed"] >= 6
        manifest = json.loads(Path(b.case.db.scalar("SELECT manifest_path FROM evidence_roots WHERE id=?", (root_id,))).read_text("utf-8"))
        assert all(i["hash_state"] == "hashed" for i in manifest["items"])

        planner = Planner(b.case, b.jobs)
        plan = planner.plan(PRESET_BY_ID["quick_triage"])
        by_tool = {}
        for s in plan.steps:
            by_tool.setdefault(s.tool_id, []).append(s)
        assert by_tool["pecmd"][0].status == PlanStepStatus.READY and "dir" in by_tool["pecmd"][0].inputs
        assert by_tool["amcacheparser"][0].status == PlanStepStatus.NOT_PRESENT
        assert by_tool["appcompatcacheparser"][0].status == PlanStepStatus.NEEDS_SETUP, "SYSTEM present but tool not installed"
        assert "ready" in plan.summary() and "need" in plan.summary().lower()
        dry = planner.save(plan, dry_run=True)
        assert b.case.db.scalar("SELECT dry_run FROM workflow_plans WHERE id=?", (dry,)) == 1
        assert b.case.db.scalar("SELECT COUNT(*) FROM jobs") == 0, "dry run executes nothing"

        plan_id, jobs, problems = planner.run(plan)
        assert not problems and len(jobs) == 3
        states = {b.wait(j)["state"] for j in jobs}
        assert states == {"Completed"}
        snap = json.loads(b.case.db.scalar("SELECT snapshot_json FROM workflow_plans WHERE id=?", (plan_id,)))
        assert snap["workflow"]["id"] == "quick_triage"
        omitted = json.loads(b.case.db.scalar("SELECT omissions_json FROM workflow_plans WHERE id=?", (plan_id,)))
        assert any(o["status"] == "Needs setup" for o in omitted) and any(o["status"] == "Not present" for o in omitted)

        # results: filter, sort, bookmark by identity
        ds_id = b.case.results.scalar("SELECT id FROM datasets WHERE schema_profile='pecmd.files'")
        q = DatasetQuery(b.case.results, ds_id)
        view = DatasetView(ds_id, [Filter("ExecutableName", "contains", "EVIL")])
        assert q.count(view) == 1
        rec = q.page(view, 0)[0][0]
        fs = FindingsService(b.case)
        bm = fs.bookmark(Reference("record", dataset_id=ds_id, rec_id=rec), note="suspicious name")
        sorted_view = DatasetView(ds_id, sort_column="ExecutableName", sort_desc=True)
        q.ensure_sort_index("ExecutableName")
        pos = q.position_of(sorted_view, rec)
        assert q.page(sorted_view, pos, 1)[0][0] == rec, "bookmark resolves to the same logical record after resorting"
        hits = search_case(b.case.results, "LAB-WIN-01")
        assert any(h["dataset_id"] for h in hits)

        # timeline: event links back to its source row and job
        tq = TimelineQuery(b.case.results)
        tv = TimelineView(text="EVIL")
        ev = tq.page(tv, 0)[0]
        e = tq.event(ev[0])
        assert e["dataset_id"] and e["rec_id"] and e["job_id"] in jobs
        src = DatasetQuery(b.case.results, e["dataset_id"]).record(e["rec_id"])
        assert any(v == e["raw_value"] for _, v in src["fields"])
        lo, width, hist = tq.histogram(TimelineView())
        assert sum(hist) == tq.count(TimelineView())
        ctxv = tq.context(ev[0], 300)
        assert tq.count(ctxv) >= 1

        # finding + report (HTML escaped, formula-like text preserved raw but safe in CSV export)
        fid = fs.create("Unexpected program executed", narrative="<b>not bold</b> علامت", status="open", severity="high")
        fs.link(fid, Reference("event", event_id=ev[0]))
        fs.link(fid, Reference("record", dataset_id=ds_id, rec_id=rec))
        evtx_ds = b.case.results.scalar("SELECT id FROM datasets WHERE schema_profile='evtxecmd.events'")
        fs.link(fid, Reference("record", dataset_id=evtx_ds, rec_id=2))
        text, path = generate(b.case, ReportOptions(timezone="UTC", scope_note="Triage of LAB-WIN-01"), "html")
        assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
        assert "&lt;b&gt;not bold&lt;/b&gt;" in text and "علامت" in text
        assert "http://" not in text.replace("http://www.w3.org", "") and "<link" not in text, "self-contained"
        assert path.exists() and b.case.db.scalar("SELECT sha256 FROM reports") == hashlib.sha256(path.read_bytes()).hexdigest()
        red_text, _ = generate(b.case, ReportOptions(redact_hosts=True, redact_examiner=True), "html", preview_only=True)
        assert "LAB-WIN-01" not in red_text and "Redaction applied" in red_text
        assert b.case.db.scalar("SELECT COUNT(*) FROM evidence WHERE host='LAB-WIN-01'") > 0, "case data unchanged by redaction"
        md, _ = generate(b.case, ReportOptions(), "md", preview_only=True)
        assert "Unexpected program executed" in md
        man, mpath = generate(b.case, ReportOptions(), "manifest")
        assert json.loads(man)["jobs"][0]["exe_sha256"]

        eq = DatasetQuery(b.case.results, evtx_ds)
        safe = tmp_path / "safe.csv"
        raw = tmp_path / "raw.csv"
        export_dataset_csv(eq, DatasetView(evtx_ds), safe, safe=True)
        export_dataset_csv(eq, DatasetView(evtx_ds), raw, safe=False)
        safe_vals = [r for r in csv.reader(open(safe, encoding="utf-8-sig"))]
        raw_vals = [r for r in csv.reader(open(raw, encoding="utf-8-sig"))]
        col = safe_vals[0].index("ExecutableInfo")
        assert safe_vals[2][col] == "'=cmd|' /C calc'!A0" and raw_vals[2][col] == "=cmd|' /C calc'!A0"

        assert _hashes(coll) == before, "evidence unchanged by the whole workflow"
        root = b.case.root
    finally:
        b.close()
    # reopen
    reopened = CaseStore.open(root)
    try:
        assert reopened.db.scalar("SELECT COUNT(*) FROM findings") == 1
        assert reopened.db.scalar("SELECT COUNT(*) FROM bookmarks WHERE id=?", (bm,)) == 1
        assert reopened.results.scalar("SELECT COUNT(*) FROM timeline_events") > 0
    finally:
        reopened.close()


def test_rla_derived_hive_keeps_originals(tmp_path):
    b = make_bench(tmp_path, tools=("rla",))
    try:
        coll = _collection(tmp_path)
        before = _hashes(coll)
        register(b, coll)
        hive = b.evidence.items(artifact_types=("system_hive",))[0]
        from mbrwb.adapters.base import Request
        jid, pf = b.jobs.submit_parser(b.case, "rla", Request({"file": [hive]}, {"ca": "true"}))
        assert jid, [p.message for p in pf.problems]
        row = b.wait(jid)
        assert row["state"] == "Completed", row
        derived = b.case.db.query("SELECT * FROM evidence WHERE artifact_basis='derived'")
        assert len(derived) == 1 and derived[0]["original_path"].startswith(str(b.case.root))
        rel = b.case.db.query_one("SELECT * FROM evidence_relations WHERE child_id=?", (derived[0]["id"],))
        assert rel["parent_id"] == hive.id and rel["relation"] == "derived_hive"
        assert _hashes(coll) == before, "original hive and logs unchanged"
    finally:
        b.close()


def test_two_hosts_never_mix(tmp_path):
    b = make_bench(tmp_path, tools=("pecmd",))
    try:
        root = tmp_path / "multi"
        for host in ("HOST-A", "HOST-B"):
            d = root / host / "C" / "Windows" / "Prefetch"
            d.mkdir(parents=True)
            (d / "X.EXE-1.pf").write_bytes(b"\x1e\x00\x00\x00SCCA" + b"\0" * 64)
        from mbrwb.evidence.discovery import discover
        b.evidence.register(discover(root), mode="in_place")
        hosts = {r["host"] for r in b.case.db.query("SELECT host FROM evidence")}
        assert hosts == {"HOST-A", "HOST-B"}
        plan = Planner(b.case, b.jobs).plan(PRESET_BY_ID["program_presence_execution"])
        steps = [s for s in plan.steps if s.tool_id == "pecmd"]
        assert {s.host for s in steps} == {"HOST-A", "HOST-B"} and all(len(s.inputs["dir"]) == 1 for s in steps)
        _, jobs, _ = Planner(b.case, b.jobs).run(plan)
        for j in jobs:
            b.wait(j)
        assert {r["host"] for r in b.case.results.query("SELECT DISTINCT host FROM timeline_events")} == {"HOST-A", "HOST-B"}
    finally:
        b.close()


def test_custom_workflow_is_validated_data():
    spec = load_custom(CUSTOM_TEMPLATE)
    assert spec.steps[0].tool_id == "pecmd"
    import pytest
    from pydantic import ValidationError
    bad = json.loads(json.dumps(CUSTOM_TEMPLATE))
    bad["steps"][0]["tool_id"] = "powershell"
    with pytest.raises(ValidationError):
        load_custom(bad)
    bad2 = json.loads(json.dumps(CUSTOM_TEMPLATE))
    bad2["steps"][0]["options"] = {"csv": "C:\\elsewhere"}
    with pytest.raises(ValidationError):
        load_custom(bad2)


def test_source_removed_after_import_keeps_results(tmp_path):
    b = make_bench(tmp_path, tools=("pecmd",))
    try:
        coll = _collection(tmp_path)
        register(b, coll)
        plan = Planner(b.case, b.jobs).plan(PRESET_BY_ID["program_presence_execution"])
        _, jobs, _ = Planner(b.case, b.jobs).run(plan)
        for j in jobs:
            b.wait(j)
        for p in (coll / "LAB-WIN-01" / "C" / "Windows" / "Prefetch").iterdir():
            p.unlink()
        out = b.evidence.verify_availability()
        assert out["unavailable"] == 2
        assert b.case.results.scalar("SELECT COUNT(*) FROM datasets WHERE state='complete'") >= 2
        mani = list((b.case.root / "evidence_manifest").glob("*.json"))
        assert mani, "prior manifest preserved"
    finally:
        b.close()
