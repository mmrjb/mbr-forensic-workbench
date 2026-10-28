from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import pytest

from mbrwb.catalogue.pe import inspect_pe
from mbrwb.storage.db import Database
from mbrwb.storage.schema import APP_MIGRATIONS
from mbrwb.tools.archive import UnsafeArchive, safe_extract_zip
from mbrwb.tools.store import ToolStore
from pe_builder import build_pe, write_cli_tool, write_tool_zip

OFFICIAL = os.environ.get("MBRWB_OFFICIAL_ARCHIVES", "/root/mbr-workbench-devenv/upstream/zips")


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "app.sqlite", APP_MIGRATIONS, label="app")
    yield ToolStore(db, tmp_path / "tools", retained_versions=1)
    db.close()


def test_synthetic_pe_is_inspectable(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(build_pe("2026.10.3+abc"))
    info = inspect_pe(p)
    assert info.product_version == "2026.10.3+abc"
    assert info.version == "2026.10.3"


# -- archive safety -------------------------------------------------------------------

def _zip(path: Path, members: dict[str, bytes], link: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return path


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/abs.txt", "C:/win.txt", "a/file.txt:ads", "CON.txt", "dir/nul", "a\\..\\..\\x"])
def test_traversal_and_special_names_rejected(tmp_path, name):
    z = _zip(tmp_path / "bad.zip", {name: b"x"})
    with pytest.raises(UnsafeArchive):
        safe_extract_zip(z, tmp_path / "stage")
    assert not (tmp_path / "evil.txt").exists()


def test_symlink_member_rejected(tmp_path):
    z = _zip(tmp_path / "link.zip", {"ok.txt": b"x"}, link="escape")
    with pytest.raises(UnsafeArchive):
        safe_extract_zip(z, tmp_path / "stage")


def test_preexisting_link_in_staging_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    stage = tmp_path / "stage"
    z = _zip(tmp_path / "a.zip", {"sub/file.txt": b"x"})
    stage.mkdir()
    # staging must be empty: a planted link is refused before anything is written
    try:
        os.symlink(outside, stage / "sub", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs Developer Mode or administrator rights on this Windows host")
    with pytest.raises(UnsafeArchive):
        safe_extract_zip(z, stage)
    assert not (outside / "file.txt").exists()


def test_zip_bomb_ratio_and_total_limits(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("big.bin", b"\0" * (64 * 1024 * 1024))
    with pytest.raises(UnsafeArchive):
        safe_extract_zip(z, tmp_path / "s1")
    with pytest.raises(UnsafeArchive):
        safe_extract_zip(z, tmp_path / "s2", max_total_bytes=1024, max_ratio=10**9)


# -- install / update / rollback --------------------------------------------------------

def test_install_update_rollback_and_retention(store, tmp_path):
    z1 = write_tool_zip(tmp_path / "PECmd1.zip", "PECmd", "2026.10.3+one")
    r1 = store.install_archive(z1)
    assert not r1.errors and r1.installed[0].version == "2026.10.3"
    first = r1.installed[0]
    z2 = write_tool_zip(tmp_path / "PECmd2.zip", "PECmd", "2026.10.4+two")
    second = store.install_archive(z2).installed[0]
    assert store.selected("pecmd").id == second.id
    assert Path(first.executable).is_file(), "prior version retained"
    back = store.rollback("pecmd")
    assert back.id == first.id and store.selected("pecmd").id == first.id
    # a third version: the oldest unselected beyond retention (1) is removed unless in use
    store.select(second.id)
    store.acquire(first.id)
    third = store.install_archive(write_tool_zip(tmp_path / "PECmd3.zip", "PECmd", "2026.10.5+three")).installed[0]
    assert Path(first.executable).is_file(), "in-use installation must never be removed"
    store.release(first.id)
    store.enforce_retention("pecmd")
    remaining = {i.id for i in store.installations("pecmd")}
    assert third.id in remaining and len(remaining) == 2


def test_failed_update_keeps_prior_version_selected(store, tmp_path):
    good = store.install_archive(write_tool_zip(tmp_path / "ok.zip", "MFTECmd", "2026.10.6+ok")).installed[0]
    # incomplete archive: apphost without its managed assembly
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("MFTECmd.exe", build_pe("2026.10.7+bad", 0x8664))
        z.writestr("MFTECmd.runtimeconfig.json", b"{}")
    res = store.install_archive(bad)
    assert res.errors and not res.installed
    assert store.selected("mftecmd").id == good.id
    assert Path(good.executable).is_file()
    # unsafe archive also leaves the selection untouched and staging cleaned
    res2 = store.install_archive(_zip(tmp_path / "evil.zip", {"../x": b"1"}))
    assert res2.errors and store.selected("mftecmd").id == good.id
    assert not any((store.tools_dir / ".staging").iterdir())


def test_import_folder_with_subdir_layout_and_dependencies(store, tmp_path):
    src = tmp_path / "ez"
    write_cli_tool(src / "net" / "EvtxeCmd", "EvtxECmd", "2026.10.2+x", extra={"Maps/Security_4624.map": b"Maps: 1\n"})
    write_cli_tool(src / "net", "PECmd", "2026.10.3+x")
    (src / "net" / "unrelated.exe").write_bytes(b"MZ junk")
    res = store.import_folder(src)
    ids = {i.tool_id for i in res.installed}
    assert ids == {"evtxecmd", "pecmd"}, res.errors
    ev = store.selected("evtxecmd")
    assert (Path(ev.install_dir) / "Maps" / "Security_4624.map").is_file()
    # source changes later do not alter the managed copy
    (src / "net" / "PECmd.exe").write_bytes(b"changed")
    assert Path(store.selected("pecmd").executable).read_bytes()[:2] == b"MZ"


@pytest.mark.skipif(not Path(OFFICIAL, "PECmd.zip").exists(), reason="official archive not available on this host")
def test_install_official_archive_static_only(store):
    """Installs (copies) the official PECmd archive. Nothing is executed."""
    res = store.install_archive(Path(OFFICIAL, "PECmd.zip"))
    assert not res.errors
    inst = res.installed[0]
    assert inst.version == "2026.10.3" and inst.flavor == "net10"
    assert inst.managed_assembly and inst.managed_assembly.endswith("PECmd.dll")


@pytest.mark.skipif(not Path(OFFICIAL, "PECmd.zip").exists(), reason="official archives not available on this host")
def test_install_every_official_archive_static_only(store):
    """Imports every official 2026.10 archive into the managed store (copy + inspect only; nothing is
    executed) and checks the detected version against the static evidence of the same archive."""
    from mbrwb.catalogue.catalogue import load_catalogue
    cat = load_catalogue()
    names = {r["archive_name"]: tid for tid, r in cat.static_evidence.items()}
    installed = {}
    for name, tid in names.items():
        res = store.install_archive(Path(OFFICIAL, name), tool_id=tid)
        assert not res.errors, (name, res.errors)
        installed[tid] = res.installed[0]
    assert len(installed) == 30 and "kape" not in installed
    for tid, inst in installed.items():
        rec = cat.static_evidence[tid]
        if rec.get("version"):
            assert inst.version == rec["version"], tid
        assert Path(inst.executable).name.lower() == cat.tools[tid].executable.lower()
        if cat.tools[tid].archive_subdir and tid in ("evtxecmd", "recmd", "sqlecmd"):
            assert any((Path(inst.install_dir) / d).is_dir() for d in ("Maps", "BatchExamples")), tid
