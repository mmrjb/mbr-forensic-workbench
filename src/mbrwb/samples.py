"""Synthetic, clearly labelled DEMO case.

Everything here is invented: host LAB-WIN-01, users 'analyst-demo' and 'جعبه‌تست' (Persian
test string), file names and timestamps. Evidence files contain only file signatures. Parser
outputs are synthetic CSVs imported as *external* outputs declared "SYNTHETIC DEMO"; no parser
job is fabricated. Demo cases live under the separate "Demo cases" folder and carry is_demo=1.
"""
from __future__ import annotations

import json
from pathlib import Path

from .adapters.base import file_sha256
from .evidence.discovery import discover
from .evidence.service import EvidenceService
from .findings.service import FindingsService, Reference
from .results.importer import DatasetContext, import_output
from .storage.case_store import CaseStore, new_id
from .storage.db import utcnow
from .timeline.mapper import map_dataset

DEMO_NOTE = "SYNTHETIC DEMO DATA - not produced by any EZ tool; for demonstration and screenshots only."


def _evidence(root: Path) -> None:
    host = root / "LAB-WIN-01" / "C"
    pf = host / "Windows" / "Prefetch"
    pf.mkdir(parents=True)
    for n in ("POWERSHELL.EXE-022A1004.pf", "RUNDLL32.EXE-1EDB1B1A.pf", "NOTEPAD.EXE-D8414F97.pf", "UPDATER_TMP.EXE-8F4C2A11.pf"):
        (pf / n).write_bytes(b"MAM\x04" + b"\0" * 252)
    logs = host / "Windows" / "System32" / "winevt" / "Logs"
    logs.mkdir(parents=True)
    for n in ("Security.evtx", "System.evtx", "Microsoft-Windows-PowerShell%4Operational.evtx"):
        (logs / n).write_bytes(b"ElfFile\x00" + b"\0" * 504)
    cfg = host / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    for n in ("SYSTEM", "SOFTWARE"):
        (cfg / n).write_bytes(b"regf" + b"\0" * 4092)
        (cfg / (n + ".LOG1")).write_bytes(b"regf" + b"\0" * 508)
    (host / "$MFT").write_bytes(b"FILE0" + b"\0" * 1019)
    prof = host / "Users" / "analyst-demo"
    (prof / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent").mkdir(parents=True)
    (prof / "NTUSER.DAT").write_bytes(b"regf" + b"\0" * 4092)
    lnk = bytes.fromhex("4c000000") + bytes.fromhex("0114020000000000c000000000000046") + b"\0" * 60
    (prof / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent" / "quarterly_report.lnk").write_bytes(lnk)
    p2 = host / "Users" / "جعبه‌تست"
    p2.mkdir(parents=True)
    (p2 / "NTUSER.DAT").write_bytes(b"regf" + b"\0" * 4092)


def _outputs(out: Path) -> dict[str, tuple[Path, str]]:
    out.mkdir(parents=True)
    files = {}
    p = out / "DEMO_PECmd_Output_Timeline.csv"
    p.write_text("RunTime,ExecutableName\r\n"
                 "2026-09-14 08:02:11.4410000,NOTEPAD.EXE\r\n"
                 "2026-09-14 09:15:42.1023377,POWERSHELL.EXE\r\n"
                 "2026-09-14 09:15:58.0000000,RUNDLL32.EXE\r\n"
                 "2026-09-14 09:16:03.9182736,UPDATER_TMP.EXE\r\n"
                 "2026-09-14 13:40:00.0000000,POWERSHELL.EXE\r\n"
                 "2026-09-15 07:58:20.5000000,NOTEPAD.EXE\r\n", "utf-8-sig")
    files["pecmd.timeline"] = (p, "pecmd")
    p = out / "DEMO_PECmd_Output.csv"
    p.write_text("SourceFilename,SourceCreated,SourceModified,SourceAccessed,ExecutableName,RunCount,LastRun,Directories\r\n"
                 '"C:\\Windows\\Prefetch\\POWERSHELL.EXE-022A1004.pf",2026-08-01 10:00:00.0000000,2026-09-14 13:40:10.0000000,2026-09-14 13:40:10.0000000,POWERSHELL.EXE,14,2026-09-14 13:40:00.0000000,"\\VOLUME{01d9}\\WINDOWS\\SYSTEM32"\r\n'
                 '"C:\\Windows\\Prefetch\\UPDATER_TMP.EXE-8F4C2A11.pf",2026-09-14 09:16:13.0000000,2026-09-14 09:16:13.0000000,2026-09-14 09:16:13.0000000,UPDATER_TMP.EXE,1,2026-09-14 09:16:03.9182736,"\\VOLUME{01d9}\\USERS\\ANALYST-DEMO\\APPDATA\\LOCAL\\TEMP"\r\n'
                 '"C:\\Windows\\Prefetch\\NOTEPAD.EXE-D8414F97.pf",2026-07-20 12:00:00.0000000,2026-09-15 07:58:30.0000000,2026-09-15 07:58:30.0000000,NOTEPAD.EXE,33,2026-09-15 07:58:20.5000000,"\\VOLUME{01d9}\\WINDOWS\\SYSTEM32"\r\n', "utf-8-sig")
    files["pecmd.files"] = (p, "pecmd")
    p = out / "DEMO_EvtxECmd_Output.csv"
    rows = ["RecordNumber,EventRecordId,TimeCreated,EventId,Level,Provider,Channel,ProcessId,ThreadId,Computer,UserId,MapDescription,UserName,RemoteHost,PayloadData1,PayloadData2,ExecutableInfo,SourceFile"]
    ev = [
        ("2026-09-14 09:14:30.1200000", "4624", "Security", "Microsoft-Windows-Security-Auditing", "Successful logon", "LAB\\analyst-demo", "10.20.30.40", "LogonType 10", "", ""),
        ("2026-09-14 09:15:42.2000000", "4688", "Security", "Microsoft-Windows-Security-Auditing", "A new process has been created", "LAB\\analyst-demo", "-", "powershell.exe", "-enc <base64 omitted>", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"),
        ("2026-09-14 09:15:43.0000000", "4104", "Microsoft-Windows-PowerShell/Operational", "Microsoft-Windows-PowerShell", "PowerShell script block", "LAB\\analyst-demo", "-", "ScriptBlock: <b>Invoke-WebRequest</b> https://example.invalid/u.exe", "", ""),
        ("2026-09-14 09:16:01.5000000", "7045", "System", "Service Control Manager", "A service was installed", "LocalSystem", "-", "UpdaterSvc", "C:\\Users\\analyst-demo\\AppData\\Local\\Temp\\updater_tmp.exe", ""),
        ("2026-09-14 13:39:58.0000000", "4624", "Security", "Microsoft-Windows-Security-Auditing", "Successful logon", "LAB\\analyst-demo", "-", "LogonType 2", "", ""),
        ("2026-09-15 07:58:00.0000000", "4634", "Security", "Microsoft-Windows-Security-Auditing", "An account was logged off", "LAB\\analyst-demo", "-", "LogonType 2", "", "=HYPERLINK(\"https://example.invalid\")"),
    ]
    for i, (t, eid, ch, prov, desc, user, remote, p1, p2, exe) in enumerate(ev, start=1):
        rows.append(",".join(json.dumps(x) if any(c in x for c in ',"<') else x for x in
                             (str(i), str(1000 + i), t, eid, "Info", prov, ch, "4", "8", "LAB-WIN-01", "S-1-5-21-1000", desc, user, remote, p1, p2, exe, f"{ch}.evtx")))
    p.write_text("\r\n".join(rows) + "\r\n", "utf-8-sig")
    files["evtxecmd.events"] = (p, "evtxecmd")
    p = out / "DEMO_MFTECmd_$MFT_Output.csv"
    p.write_text("EntryNumber,SequenceNumber,InUse,ParentPath,FileName,Extension,FileSize,IsDirectory,Created0x10,Created0x30,LastModified0x10,LastModified0x30,LastRecordChange0x10,LastRecordChange0x30,LastAccess0x10,LastAccess0x30\r\n"
                 "88412,3,True,.\\Users\\analyst-demo\\AppData\\Local\\Temp,updater_tmp.exe,.exe,184320,False,2026-09-14 09:15:59.8811234,2026-09-14 09:15:59.8811234,2026-09-14 09:15:59.9000000,,2026-09-14 09:16:00.0000000,,2026-09-14 09:16:03.0000000,\r\n"
                 "88413,1,True,.\\Users\\analyst-demo\\Documents,quarterly_report.docx,.docx,48211,False,2026-09-10 16:20:00.0000000,,2026-09-14 08:05:00.0000000,,2026-09-14 08:05:00.0000000,,2026-09-14 08:05:00.0000000,\r\n"
                 "90001,2,True,.\\Users\\جعبه‌تست\\Desktop,یادداشت.txt,.txt,12,False,2026-09-15 08:00:00.0000000,,2026-09-15 08:00:00.0000000,,2026-09-15 08:00:00.0000000,,2026-09-15 08:00:00.0000000,\r\n", "utf-8-sig")
    files["mftecmd.mft"] = (p, "mftecmd")
    return files


def create_demo_case(demo_dir: Path, name: str = "DEMO - Synthetic LAB-WIN-01") -> CaseStore:
    root = Path(demo_dir) / name
    case = CaseStore.create(root, title=name, examiner="Demo examiner", display_tz="Asia/Tehran",
                            description=DEMO_NOTE, reference="DEMO-0001", is_demo=True)
    ev_root = root / "working_copies" / "synthetic_collection"
    _evidence(ev_root)
    svc = EvidenceService(case)
    # The synthetic collection lives inside the demo case only so the demo is self-contained;
    # it is registered with an explicit note. Real evidence is never placed inside a case.
    rep = discover(ev_root, default_host="LAB-WIN-01")
    from .evidence.service import EvidenceError
    try:
        svc.register(rep, mode="in_place", host="LAB-WIN-01", label="Synthetic demo collection")
    except EvidenceError:
        _register_demo(svc, case, rep)
    svc.hash_pending()
    outs = _outputs(root / "exports" / "synthetic_demo_outputs")
    ds_ids = {}
    for hint, (path, tool) in outs.items():
        oid = new_id("out")
        digest = file_sha256(path)
        case.db.execute("""INSERT INTO outputs(id, case_id, job_id, origin, rel_path, abs_path, kind, role, size, sha256, completion, import_state, declared_tool, created_utc)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (oid, case.id, None, "external", path.name, str(path), "csv", hint, path.stat().st_size, digest, "complete", "importing",
                         f"SYNTHETIC DEMO (shaped like {tool} output)", utcnow()))
        res = import_output(case.results, DatasetContext(case_id=case.id, name=path.name, source_path=path, source_format="csv", origin="external",
                                                         output_id=oid, tool_id=tool, tool_version="SYNTHETIC-DEMO", schema_hint=hint, host="LAB-WIN-01",
                                                         user_profile="analyst-demo" if hint != "evtxecmd.events" else None, source_sha256=digest))
        map_dataset(case.results, res.dataset_id, schema_hint=hint)
        case.db.execute("UPDATE outputs SET import_state='imported', dataset_ids_json=? WHERE id=?", (json.dumps([res.dataset_id]), oid))
        ds_ids[hint] = res.dataset_id
    fs = FindingsService(case, author="Demo examiner")
    fid = fs.create("Unfamiliar executable run from a user Temp folder (DEMO)",
                    narrative="Synthetic example. Prefetch records UPDATER_TMP.EXE running at 09:16:03 UTC; the $MFT shows the file created in the user's Temp "
                              "folder seconds earlier, and a service pointing to it was installed. Presence in Amcache/ShimCache alone would not have shown execution.\n"
                              "متن فارسی برای آزمایش نمایش راست‌به‌چپ.",
                    status="open", severity="high", tags="demo, execution")
    ev = case.results.query("SELECT id FROM timeline_events WHERE description LIKE '%UPDATER_TMP%' OR object_path LIKE '%UPDATER_TMP%' ORDER BY utc_ticks")
    for e in ev[:1]:
        fs.link(fid, Reference("event", event_id=e["id"]))
    fs.link(fid, Reference("record", dataset_id=ds_ids["mftecmd.mft"], rec_id=1))
    fs.link(fid, Reference("record", dataset_id=ds_ids["evtxecmd.events"], rec_id=4))
    fs.bookmark(Reference("record", dataset_id=ds_ids["evtxecmd.events"], rec_id=2), note="Encoded PowerShell command line (demo)")
    case.audit("demo.created", case.id, {"note": DEMO_NOTE})
    return case


def _register_demo(svc: EvidenceService, case: CaseStore, rep) -> None:
    """Register the synthetic collection that sits inside the demo case (allowed only for demos)."""
    from .evidence.service import _iso
    root_id = new_id("evr")
    with case.db.write() as c:
        c.execute("""INSERT INTO evidence_roots(id, case_id, path, kind, mode, label, host, registered_utc, files_inspected, candidates, unavailable, excluded, discovery_state)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (root_id, case.id, rep.root, "folder", "in_place", "Synthetic demo collection", "LAB-WIN-01", utcnow(),
                                                            rep.files_inspected, len(rep.candidates), 0, 0, "complete"))
        for cand in rep.candidates:
            c.execute("""INSERT INTO evidence(id, case_id, root_id, original_path, rel_path, artifact_type, artifact_basis, host, user_profile, volume, size, mtime_utc,
                         ctime_utc, atime_utc, imported_utc, hash_state, availability, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (new_id("ev"), case.id, root_id, cand.path, cand.rel_path, cand.artifact_type, cand.basis, "LAB-WIN-01", cand.user_profile, cand.volume,
                       cand.size, _iso(cand.mtime), _iso(cand.ctime), _iso(cand.atime), utcnow(), "pending", "available", DEMO_NOTE))
    svc.link_related(root_id)
    svc.write_manifest(root_id, rep)
