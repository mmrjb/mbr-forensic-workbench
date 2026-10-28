"""Settings recovery, SQL safety, overlap rules, download allowlist, signatures off-Windows,
KAPE definition parsing, GUI launch sessions, catalogue coverage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mbrwb.catalogue.catalogue import load_catalogue
from mbrwb.config.paths import AppPaths
from mbrwb.config.settings import SettingsStore
from mbrwb.results.importer import DatasetContext, import_csv
from mbrwb.results.query import DatasetQuery, DatasetView, Filter, spreadsheet_safe
from mbrwb.storage.case_store import CaseError, CaseStore, path_within, peek_case
from mbrwb.storage.db import Database
from mbrwb.storage.schema import APP_MIGRATIONS, RESULTS_MIGRATIONS
from mbrwb.tools.download import DownloadError, fetch


def test_invalid_settings_file_is_set_aside_and_defaults_used(tmp_path):
    paths = AppPaths(tmp_path)
    paths.ensure()
    paths.settings_file.write_text("{ not json", "utf-8")
    st = SettingsStore(paths)
    assert st.notice and "defaults" in st.notice
    assert any(p.name.startswith("settings.invalid-") for p in tmp_path.iterdir())
    st.update(execution={"max_parallel_jobs": 3})
    assert json.loads(paths.settings_file.read_text())["execution"]["max_parallel_jobs"] == 3
    with pytest.raises(Exception):
        st.update(case_defaults={"display_timezone": "Mars/Olympus"})
    assert SettingsStore(paths).settings.case_defaults.display_timezone == "UTC", "invalid update was not persisted"


def test_settings_import_leaves_foreign_paths_unresolved(tmp_path):
    st = SettingsStore(AppPaths(tmp_path))
    exported = st.export_dict()
    exported["tool_storage"]["tools_dir"] = r"D:\\OtherMachine\\Tools"
    merged, notes = st.preview_import(exported)
    assert merged.tool_storage.tools_dir is None and any("unresolved" in n for n in notes)


def test_filters_are_parameterised(tmp_path):
    db = Database(tmp_path / "r.sqlite", RESULTS_MIGRATIONS)
    p = tmp_path / "x.csv"
    p.write_text("Name,Value\r\na,1\r\nb'); DROP TABLE datasets;--,2\r\n", "utf-8")
    r = import_csv(db, DatasetContext("c", "x", p, "csv"))
    q = DatasetQuery(db, r.dataset_id)
    assert q.count(DatasetView(r.dataset_id, [Filter("Name", "contains", "'); DROP TABLE datasets;--")])) == 1
    assert q.count(DatasetView(r.dataset_id, search="%")) == 0, "LIKE wildcards in user text are escaped"
    with pytest.raises(KeyError):
        q.count(DatasetView(r.dataset_id, [Filter("Name) OR 1=1 --", "equals", "x")]))
    with pytest.raises(ValueError):
        q.count(DatasetView(r.dataset_id, [Filter("Name", "UNION", "x")]))
    assert db.scalar("SELECT COUNT(*) FROM datasets") == 1


@pytest.mark.parametrize("v,expected", [("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("normal", "normal"), ("", "")])
def test_spreadsheet_safe(v, expected):
    assert spreadsheet_safe(v) == expected


def test_case_cannot_live_inside_evidence_and_foreign_folders_are_refused(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    with pytest.raises(CaseError):
        CaseStore.create(ev / "case", title="x", forbidden_parents=[str(ev)])
    other = tmp_path / "random"
    other.mkdir()
    (other / "case.json").write_text('{"marker": "something-else"}')
    (other / "case.sqlite").write_bytes(b"")
    with pytest.raises(CaseError):
        CaseStore.open(other)
    assert peek_case(other) is None
    assert path_within(tmp_path / "a" / "b", tmp_path / "a") and not path_within(tmp_path / "ab", tmp_path / "a")


def test_second_process_lock_is_respected(tmp_path):
    c = CaseStore.create(tmp_path / "c1", title="t")
    lock = json.loads((c.root / "case.lock").read_text())
    assert lock["pid"]
    (c.root / "case.lock").write_text(json.dumps({"pid": 1, "since_utc": "x"}))  # pid 1 is alive on POSIX
    if sys.platform != "win32":
        with pytest.raises(CaseError):
            CaseStore.open(c.root)
    (c.root / "case.lock").write_text(json.dumps({"pid": 99999999, "since_utc": "x"}))
    CaseStore.open(c.root).close()  # stale lock is taken over
    c.close()


def test_download_refuses_non_allowlisted_and_plain_http():
    allowed = ("https://download.ericzimmermanstools.com",)
    with pytest.raises(DownloadError, match="HTTPS"):
        fetch("http://download.ericzimmermanstools.com/net/PECmd.zip", Path("x"), allowed_origins=allowed)
    with pytest.raises(DownloadError, match="not a reviewed download origin"):
        fetch("https://evil.example/PECmd.zip", Path("x"), allowed_origins=allowed)
    with pytest.raises(DownloadError):
        fetch("https://download.ericzimmermanstools.com.evil.example/x.zip", Path("x"), allowed_origins=allowed)


def test_catalogue_accounts_for_every_official_entry():
    cat = load_catalogue()
    names = {t.name for t in cat.tools.values()}
    expected = {"AmcacheParser", "AppCompatCacheParser", "bstrings", "EvtxECmd", "JLECmd", "LECmd", "MFTECmd", "PECmd", "RBCmd", "RecentFileCacheParser",
                "RECmd", "RLA", "SBECmd", "SQLECmd", "SrumECmd", "SumECmd", "VSCMount", "WxTCmd", "EZViewer", "Hasher", "JumpList Explorer", "MFTExplorer",
                "Registry Explorer", "SDB Explorer", "ShellBags Explorer", "Timeline Explorer", "Get-ZimmermanTools", "iisGeoLocate", "KAPE", "TimeApp", "XWFIM"}
    assert expected == names
    for t in cat.tools.values():
        assert t.license_source and t.redistribution
        for d in t.downloads.values():
            assert cat.is_allowed_download(d.url), (t.id, d.url)
    kape = cat.tools["kape"]
    assert not kape.downloadable and "commercial" in kape.license_source


@pytest.mark.skipif(not Path("/root/mbr-workbench-devenv/upstream/zips/PECmd.zip").exists(), reason="official archive not on this host")
def test_signature_assessment_off_windows_is_unavailable_not_valid(tmp_path):
    import zipfile

    from mbrwb.domain.enums import SignatureState
    from mbrwb.tools.signature import assess_signature
    p = tmp_path / "PECmd.dll"
    p.write_bytes(zipfile.ZipFile("/root/mbr-workbench-devenv/upstream/zips/PECmd.zip").read("PECmd.dll"))
    a = assess_signature(p, ("ZimTech LLC",))
    if sys.platform != "win32":
        assert a.state == SignatureState.UNAVAILABLE and a.publisher == "ZimTech LLC"
    from pe_builder import build_pe
    q = tmp_path / "unsigned.exe"
    q.write_bytes(build_pe("1.0"))
    assert assess_signature(q).state == SignatureState.UNSIGNED


def test_kape_definitions_are_read_as_data(tmp_path):
    from mbrwb.adapters.kape import KapeInstallation
    root = tmp_path / "KAPE"
    (root / "Modules" / "bin").mkdir(parents=True)
    (root / "Targets" / "Windows").mkdir(parents=True)
    (root / "kape.exe").write_bytes(b"MZ")
    (root / "Targets" / "Windows" / "Prefetch.tkape").write_text("Description: Prefetch\nCategory: Executables\nTargets:\n  - Name: Prefetch\n    Path: C:\\Windows\\prefetch\\\n", "utf-8")
    (root / "Modules" / "PECmd.mkape").write_text("Description: PECmd\nCategory: ProgramExecution\nProcessors:\n  - Executable: PECmd.exe\n    CommandLine: -d %sourceDirectory% --csv %destinationDirectory%\n    ExportFormat: csv\n", "utf-8")
    (root / "Modules" / "Evil.mkape").write_text("!!python/object/apply:os.system ['echo pwned']\n", "utf-8")
    k = KapeInstallation.inspect(root)
    assert {t.name for t in k.targets} == {"Prefetch"}
    evil = [m for m in k.modules if m.name == "Evil"][0]
    assert evil.error, "unsafe YAML tags are rejected by safe_load"
    deps = k.module_dependencies(["PECmd", "Missing"])
    assert deps[0]["status"] == "missing" and deps[1]["status"] == "module not found"
    (root / "Modules" / "bin" / "PECmd.exe").write_bytes(b"MZ")
    assert k.module_dependencies(["PECmd"])[0]["status"] == "present"


def test_gui_launch_is_a_session_not_a_job(tmp_path, monkeypatch):
    from mbrwb.catalogue.catalogue import load_catalogue
    from mbrwb.domain.models import InstallationRef
    from mbrwb.tools import launcher

    class FakeProc:
        pid = 4242

        def wait(self):
            return 0
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: FakeProc())
    exe = tmp_path / "TimelineExplorer.exe"
    exe.write_bytes(b"MZ")
    inst = InstallationRef("i1", "timelineexplorer", "2026.10.0", None, "net10", str(tmp_path), str(exe), None, "ab" * 32, None, "unavailable")
    app = Database(tmp_path / "app.sqlite", APP_MIGRATIONS)
    case = CaseStore.create(tmp_path / "case", title="t")
    sid = launcher.launch_gui(app, load_catalogue().tools["timelineexplorer"], inst, case=case, subject_path=str(tmp_path / "out.csv"))
    import time
    time.sleep(0.2)
    assert app.query_one("SELECT * FROM launch_sessions WHERE id=?", (sid,))["classification"] == "gui_session"
    assert case.db.scalar("SELECT COUNT(*) FROM launch_sessions") == 1
    assert case.db.scalar("SELECT COUNT(*) FROM jobs") == 0, "a GUI launch is never recorded as a parser job"
    assert json.loads(case.db.scalar("SELECT args_json FROM launch_sessions")) == [], "no unverified file-open argument is passed"
    case.close()


def test_upstream_page_reconciliation_flags_new_entries():
    from mbrwb.catalogue.catalogue import parse_catalogue_page, reconcile
    page = ("<table><tr><td>Name</td><td>Version</td><td>Purpose</td></tr>"
            "<tr><td>PECmd</td><td><a href='https://download.ericzimmermanstools.com/net/PECmd.zip'>2026.11.0</a></td><td>Prefetch parser</td></tr>"
            "<tr><td>NewShinyCmd</td><td><a href='https://download.ericzimmermanstools.com/net/NewShinyCmd.zip'>2026.11.0</a></td><td>New</td></tr></table>")
    items = {i.name: i for i in reconcile(load_catalogue(), parse_catalogue_page(page.replace("'", '"')))}
    assert items["NewShinyCmd"].status == "new_pending_review" and items["NewShinyCmd"].tool_id is None
    assert items["PECmd"].status == "update_listed"


def test_kape_collection_is_registered_with_job_provenance(tmp_path):
    """FAKE KAPE run: a job whose output contains a Targets collection registers that tree as evidence
    carrying the job id; other case-internal folders stay refused."""
    import sys as _sys

    from harness import make_bench
    from mbrwb.adapters.base import Request
    from mbrwb.adapters import registry
    from mbrwb.domain.models import EvidenceRef
    from mbrwb.evidence.discovery import discover
    from mbrwb.evidence.service import EvidenceError
    from pe_builder import build_pe

    b = make_bench(tmp_path, tools=())
    try:
        kroot = tmp_path / "KAPE"
        kroot.mkdir()
        (kroot / "kape.exe").write_bytes(build_pe("1.3.0.2"))
        b.tools.register_external("kape", kroot)
        script = tmp_path / "fake_kape.py"
        script.write_text("import sys,os,pathlib\na=sys.argv[1:]\nd=pathlib.Path(a[a.index('--tdest')+1])/'C'/'Windows'/'Prefetch'\n"
                          "d.mkdir(parents=True,exist_ok=True)\n(d/'X.EXE-1.pf').write_bytes(b'\\x1e\\x00\\x00\\x00SCCA'+b'\\0'*64)\n", "utf-8")
        registry.set_host_resolver(lambda inst: ([_sys.executable, str(script)], inst.executable) if inst.tool_id == "kape" else ([], inst.executable))
        src = tmp_path / "mounted"
        src.mkdir()
        jid, pf = b.jobs.submit_parser(b.case, "kape", Request({"tsource": [EvidenceRef("path", str(src), str(src), "mounted_evidence", is_dir=True)]}, {"target": "Prefetch"}))
        assert jid, [p.message for p in pf.problems]
        row = b.wait(jid)
        assert row["state"] in ("Completed", "Partial"), row
        argv = json.loads(row["args_json"])
        assert argv[argv.index("--target") + 1] == "Prefetch" and argv[argv.index("--tsource") + 1] == str(src)
        assert argv[argv.index("--tdest") + 1].endswith("kape_collection") and "--tflush" not in argv
        roots = b.case.db.query("SELECT * FROM evidence_roots")
        assert len(roots) == 1 and jid in roots[0]["label"]
        assert b.case.db.scalar("SELECT COUNT(*) FROM evidence WHERE artifact_type='prefetch_file'") == 1
        with pytest.raises(EvidenceError):
            b.evidence.register(discover(b.case.root / "jobs"), mode="in_place")
    finally:
        b.close()
