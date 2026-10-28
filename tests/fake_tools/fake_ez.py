"""Purpose-built FAKE stand-in for EZ command-line tools, used only to test process
management, argument passing and import handling. Its output is synthetic and proves nothing
about the real tools.

Invoked as: python fake_ez.py --tool <id> --exe <path> <tool args...>
Behaviour switches come from the input file/folder name:
  CRASH   -> partial CSV then exit 3        WARN  -> exit 0 with "Error ... skipping" lines
  EMPTY   -> "No files found", no CSV       BADSCHEMA -> CSV with unexpected columns
  SLEEP   -> spawn a grandchild and sleep (for cancel/timeout tree tests)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def arg(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


def main() -> int:
    a = sys.argv[1:]
    tool = a[a.index("--tool") + 1]
    a = a[a.index("--exe") + 2:]
    tmp = os.environ.get("TMP") or "."
    Path(tmp).mkdir(parents=True, exist_ok=True)
    Path(tmp, "fake_argv.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    src = arg(a, "-f") or arg(a, "-d") or ""
    name = Path(src).name.upper()
    csv_dir = arg(a, "--csv")
    print(f"{tool} (FAKE) processing {src}", flush=True)
    if "SLEEP" in name:
        child = subprocess.Popen([sys.executable, "-c", "import time,os,sys; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(600)",
                                  os.path.join(tmp, "grandchild.pid")])
        print(f"spawned {child.pid}", flush=True)
        time.sleep(600)
        return 0
    if "EMPTY" in name:
        print("No files found", flush=True)
        return 0
    if tool == "rla":
        out = Path(arg(a, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        hive = Path(src)
        data = hive.read_bytes()
        (out / hive.name).write_bytes(data + b"[replayed]")  # derived copy; the source is only read
        print(f"Hive {hive.name} written to {out}", flush=True)
        return 0
    if not csv_dir:
        print("no --csv given", file=sys.stderr)
        return 2
    Path(csv_dir).mkdir(parents=True, exist_ok=True)
    ts = "20260101000000"
    if tool == "pecmd":
        files = Path(csv_dir, f"{ts}_PECmd_Output.csv")
        tl = Path(csv_dir, f"{ts}_PECmd_Output_Timeline.csv")
        if "BADSCHEMA" in name:
            tl.write_text("When,What\r\n2024-01-01 00:00:00,x\r\n", "utf-8")
            files.write_text("A,B\r\n1,2\r\n", "utf-8")
            return 0
        files.write_text("SourceFilename,SourceCreated,SourceModified,SourceAccessed,ExecutableName,RunCount,LastRun\r\n"
                         f'"{src}\\CALC.EXE-3FBEF7FD.pf",2024-03-01 09:59:00.0000000,2024-03-01 10:00:05.1234567,2024-03-01 10:00:05.1234567,CALC.EXE,2,2024-03-01 10:00:00.1234567\r\n'
                         f'"{src}\\EVIL (1) & CO.EXE-1.pf",2024-03-02 08:00:00,2024-03-02 08:00:01,2024-03-02 08:00:01,"EVIL (1) & CO.EXE",1,2024-03-02 08:00:00\r\n', "utf-8-sig")
        tl.write_text("RunTime,ExecutableName\r\n2024-03-01 10:00:00.1234567,CALC.EXE\r\n2024-02-28 07:00:00,CALC.EXE\r\n2024-03-02 08:00:00,EVIL (1) & CO.EXE\r\n", "utf-8-sig")
    elif tool == "mftecmd":
        Path(csv_dir, f"{ts}_MFTECmd_$MFT_Output.csv").write_text(
            "EntryNumber,SequenceNumber,InUse,ParentPath,FileName,Extension,FileSize,IsDirectory,Created0x10,Created0x30,LastModified0x10,LastModified0x30,LastRecordChange0x10,LastRecordChange0x30,LastAccess0x10,LastAccess0x30\r\n"
            "41,1,True,.\\Users\\alice\\Desktop,notes.txt,.txt,12,False,2024-03-01 10:01:00.0000001,,2024-03-01 10:02:00,,2024-03-01 10:02:00,,2024-03-01 10:02:00,\r\n", "utf-8-sig")
    elif tool == "evtxecmd":
        Path(csv_dir, f"{ts}_EvtxECmd_Output.csv").write_text(
            "RecordNumber,EventRecordId,TimeCreated,EventId,Level,Provider,Channel,ProcessId,ThreadId,Computer,UserId,MapDescription,UserName,RemoteHost,PayloadData1,ExecutableInfo,SourceFile\r\n"
            "1,100,2024-03-01 10:00:03.5000000,4624,LogAlways,Microsoft-Windows-Security-Auditing,Security,4,8,LAB-WIN-01,S-1-5-18,Successful logon,alice,-,LogonType 2,,Security.evtx\r\n"
            '2,101,2024-03-01 10:05:00.0000000,4688,LogAlways,Microsoft-Windows-Security-Auditing,Security,4,8,LAB-WIN-01,S-1-5-18,A new process has been created,alice,-,"<script>alert(1)</script>",=cmd|\' /C calc\'!A0,Security.evtx\r\n', "utf-8-sig")
    else:
        Path(csv_dir, f"{ts}_{tool}_Output.csv").write_text("Value\r\nx\r\n", "utf-8")
    if "WARN" in name:
        print("Error parsing record 7: data appears damaged. Skipping", flush=True)
    if "CRASH" in name:
        print("Unhandled exception: simulated failure", file=sys.stderr, flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
