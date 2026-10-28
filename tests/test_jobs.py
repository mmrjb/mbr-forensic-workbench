"""Job lifecycle with FAKE tools (process management, argv integrity, state aggregation).
These tests do not show that any real EZ tool works."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from harness import make_bench, register
from mbrwb.adapters.base import Request
from mbrwb.domain.models import EvidenceRef
from mbrwb.storage.case_store import CaseStore


def _pf_folder(tmp: Path, name: str) -> Path:
    d = tmp / "evidence" / "LAB-WIN-01" / "C" / "Windows" / name
    d.mkdir(parents=True)
    (d / "CALC.EXE-3FBEF7FD.pf").write_bytes(b"\x1e\x00\x00\x00SCCA" + b"\0" * 200)
    return d


def _dir_ref(path: Path, host="LAB-WIN-01") -> EvidenceRef:
    return EvidenceRef(id="path", path=str(path), original_path=str(path), artifact_type="prefetch_directory", host=host, is_dir=True)


@pytest.fixture
def bench(tmp_path):
    b = make_bench(tmp_path)
    yield b
    b.close()


def test_hostile_paths_reach_the_tool_unchanged(bench, tmp_path):
    folder = _pf_folder(tmp_path, "Prefetch (copy) & $MFT %PATH% `x` پرونده")
    jid, pf = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {"quiet": True, "mp": True}))
    assert jid, [p.message for p in pf.problems]
    row = bench.wait(jid)
    assert row["state"] == "Completed", row
    argv = json.loads((Path(row["job_dir"]) / "temp" / "fake_argv.json").read_text("utf-8"))
    assert argv[argv.index("-d") + 1] == str(folder)
    assert argv[argv.index("--csv") + 1] == str(Path(row["output_root"]) / "csv")
    assert "-q" in argv and "--mp" in argv
    frozen = json.loads(row["args_json"])
    assert frozen == argv, "the frozen argument array is exactly what ran"
    job_json = json.loads((Path(row["job_dir"]) / "job.json").read_text("utf-8"))
    assert job_json["job"]["exe_sha256"] == bench.tools.selected("pecmd").exe_sha256


def test_completed_job_imports_datasets_and_timeline(bench, tmp_path):
    folder = _pf_folder(tmp_path, "Prefetch")
    register(bench, folder.parent.parent.parent.parent)
    jid, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {"mp": True}))
    row = bench.wait(jid)
    assert row["state"] == "Completed" and row["parser_outcome"] == "success" and row["import_outcome"] == "imported"
    ds = [dict(r) for r in bench.case.results.query("SELECT * FROM datasets WHERE job_id=? ORDER BY id", (jid,))]
    assert {d["schema_profile"] for d in ds} == {"pecmd.files", "pecmd.timeline"}
    ev = bench.case.results.query("SELECT * FROM timeline_events")
    assert any(e["meaning"].startswith("Program run time") for e in ev)
    outs = bench.case.db.query("SELECT * FROM outputs WHERE job_id=?", (jid,))
    assert all(o["sha256"] and o["completion"] == "complete" for o in outs)
    assert any(o["kind"] == "log" for o in outs), "console logs are registered outputs"


@pytest.mark.parametrize("variant,state,parser,imp", [
    ("WARN", "Partial", "success_with_warnings", "imported"),
    ("CRASH", "Failed", "failed", "not_applicable"),
    ("EMPTY", "Completed", "success", "not_applicable"),
    ("BADSCHEMA", "Partial", "success", "unsupported_schema"),
])
def test_outcomes_stay_distinct(bench, tmp_path, variant, state, parser, imp):
    folder = _pf_folder(tmp_path, f"Prefetch_{variant}")
    jid, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
    row = bench.wait(jid)
    assert (row["state"], row["parser_outcome"], row["import_outcome"]) == (state, parser, imp), row
    if variant == "CRASH":
        outs = bench.case.db.query("SELECT completion, import_state FROM outputs WHERE job_id=? AND kind!='log'", (jid,))
        assert outs and all(o["completion"] == "quarantined" and o["import_state"] == "not_applicable" for o in outs)
        assert bench.case.results.scalar("SELECT COUNT(*) FROM datasets WHERE job_id=?", (jid,)) == 0
    if variant == "WARN":
        assert "damaged" in row["warnings_json"]


def _wait_file(p: Path, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if p.exists() and p.read_text().strip():
            return int(p.read_text())
        time.sleep(0.05)
    raise TimeoutError(p)


def _alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        from mbrwb.storage.case_store import _pid_alive
        return _pid_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # reaped zombies are gone; a zombie still answers kill(0)
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def test_cancel_kills_whole_tree_and_quarantines(bench, tmp_path):
    folder = _pf_folder(tmp_path, "Prefetch_SLEEP")
    jid, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
    gpid = _wait_file(bench.case.job_dir(jid) / "temp" / "grandchild.pid")
    assert _alive(gpid)
    t0 = time.monotonic()
    assert bench.jobs.cancel(jid)
    row = bench.wait(jid)
    assert row["state"] == "Cancelled" and row["parser_outcome"] == "cancelled"
    assert time.monotonic() - t0 < 10
    time.sleep(0.3)
    assert not _alive(gpid), "grandchild must not survive cancellation"
    assert (Path(row["job_dir"]) / "stdout.log").read_text().startswith("pecmd (FAKE)")


def test_timeout_is_its_own_state(tmp_path):
    b = make_bench(tmp_path, timeout_s=1)
    try:
        folder = _pf_folder(tmp_path, "Prefetch_SLEEP")
        jid, _ = b.jobs.submit_parser(b.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
        row = b.wait(jid, 30)
        assert row["state"] == "Timed out" and row["error_category"] == "Timeout"
    finally:
        b.close()


def test_interrupted_jobs_are_marked_on_reopen_and_not_rerun(bench, tmp_path):
    folder = _pf_folder(tmp_path, "Prefetch_SLEEP")
    jid, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
    _wait_file(bench.case.job_dir(jid) / "temp" / "grandchild.pid")
    # simulate an application crash: reopen the case from disk while the job row says Running
    reopened = CaseStore.open(bench.case.root)
    row = reopened.db.query_one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert row["state"] == "Interrupted" and "stopped while this job was active" in row["error_message"]
    reopened.close()
    bench.jobs.cancel(jid)


def test_queued_job_keeps_frozen_tool_identity_after_update(bench, tmp_path):
    from pe_builder import write_tool_zip
    slow = _pf_folder(tmp_path, "Prefetch_SLEEP")
    bench.jobs.set_max_parallel(1)
    j1, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(slow)]}, {}))
    folder = _pf_folder(tmp_path, "Prefetch2")
    j2, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
    before = bench.case.db.scalar("SELECT exe_sha256 FROM jobs WHERE id=?", (j2,))
    bench.tools.install_archive(write_tool_zip(tmp_path / "new.zip", "PECmd", "2026.10.9+newer"))
    assert bench.tools.selected("pecmd").exe_sha256 != before
    bench.jobs.cancel(j1)
    row = bench.wait(j2)
    assert row["exe_sha256"] == before and row["state"] == "Completed", "queued job ran with its frozen executable"


def test_missing_tool_and_wrong_inputs_give_actionable_preflight(bench, tmp_path):
    jid, pf = bench.jobs.submit_parser(bench.case, "sbecmd", Request({}, {}))
    assert jid is None and pf.blockers[0].category.value == "Tool missing"
    jid, pf = bench.jobs.submit_parser(bench.case, "pecmd", Request({}, {}))
    assert jid is None and any("required" in p.message for p in pf.blockers)
    f = tmp_path / "x.pf"
    f.write_bytes(b"x")
    both = Request({"file": [EvidenceRef("a", str(f), str(f), "prefetch_file")], "dir": [_dir_ref(tmp_path)]}, {})
    jid, pf = bench.jobs.submit_parser(bench.case, "pecmd", both)
    assert jid is None and any("only one" in p.message.lower() for p in pf.blockers)
    jid, pf = bench.jobs.submit_parser(bench.case, "pecmd", Request({"file": [EvidenceRef("a", str(f), str(f), "prefetch_file")]}, {}, "--csv C:\\elsewhere"))
    assert jid is None and any("managed" in p.message for p in pf.blockers)
    jid, pf = bench.jobs.submit_parser(bench.case, "mftecmd", Request({"file": [EvidenceRef("a", str(f), str(f), "ntfs_mft")]}, {"csv": True, "fl": True, "body": True}))
    assert jid is None and any("--bdl" in p.message for p in pf.blockers)


def test_runtime_missing_blocks_with_guidance(tmp_path):
    from mbrwb.jobs.manager import JobManager

    b = make_bench(tmp_path)
    try:
        jm = JobManager(b.tools, runtime_checker=lambda req: (False, ["Microsoft.NETCore.App 10.0.0 (x64)"], ["Install the .NET 10 Runtime (x64) from https://dotnet.microsoft.com/en-us/download/dotnet/10.0"]))
        folder = _pf_folder(tmp_path, "Prefetch")
        jid, pf = jm.submit_parser(b.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
        assert jid is None
        p = [x for x in pf.blockers if x.category.value == "Runtime missing"][0]
        assert "10.0" in p.message and "dotnet.microsoft.com" in p.next_action
        jm.shutdown()
    finally:
        b.close()


def test_retry_runs_in_fresh_folder_linked_to_original(bench, tmp_path):
    folder = _pf_folder(tmp_path, "Prefetch_CRASH")
    jid, _ = bench.jobs.submit_parser(bench.case, "pecmd", Request({"dir": [_dir_ref(folder)]}, {}))
    bench.wait(jid)
    rid, pf = bench.jobs.retry(bench.case, jid)
    assert rid and rid != jid
    row = bench.wait(rid)
    assert row["retry_of"] == jid and row["job_dir"] != bench.case.db.scalar("SELECT job_dir FROM jobs WHERE id=?", (jid,))
