"""Shared test setup: synthetic tool installs + fake process host + a case."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mbrwb.adapters import registry
from mbrwb.adapters.base import default_host_resolver
from mbrwb.domain.enums import JobState
from mbrwb.evidence.discovery import discover
from mbrwb.evidence.service import EvidenceService
from mbrwb.jobs.manager import JobManager
from mbrwb.storage.case_store import CaseStore
from mbrwb.storage.db import Database
from mbrwb.storage.schema import APP_MIGRATIONS
from mbrwb.tools.store import ToolStore
from pe_builder import write_tool_zip

FAKE = Path(__file__).parent / "fake_tools" / "fake_ez.py"


def fake_resolver(inst):
    return [sys.executable, str(FAKE), "--tool", inst.tool_id, "--exe"], inst.executable


@dataclass
class Bench:
    app: Database
    tools: ToolStore
    jobs: JobManager
    case: CaseStore
    evidence: EvidenceService

    def close(self):
        self.jobs.shutdown()
        self.case.close()
        self.app.close()
        registry.set_host_resolver(default_host_resolver)

    def wait(self, job_id: str, timeout: float = 30.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            row = self.case.db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
            if row and JobState(row["state"]).is_terminal:
                return dict(row)
            time.sleep(0.05)
        raise TimeoutError(f"job {job_id} did not finish")


def make_bench(tmp: Path, *, tools=("pecmd", "mftecmd", "evtxecmd"), max_parallel: int = 2, timeout_s: int = 60) -> Bench:
    registry.set_host_resolver(fake_resolver)
    app = Database(tmp / "app.sqlite", APP_MIGRATIONS, label="app")
    store = ToolStore(app, tmp / "tools")
    specs = {
        "pecmd": ("PECmd", "2026.10.3+fake", None, None),
        "mftecmd": ("MFTECmd", "2026.10.6+fake", None, None),
        "evtxecmd": ("EvtxECmd", "2026.10.2+fake", "EvtxeCmd", {"Maps/Security_4624.map": b"Author: test\n"}),
        "rla": ("rla", "2026.10.1+fake", None, None),
    }
    for t in tools:
        stem, ver, sub, extra = specs[t]
        res = store.install_archive(write_tool_zip(tmp / f"{stem}.zip", stem, ver, subdir=sub, extra=extra))
        assert not res.errors, res.errors
    jm = JobManager(store, max_parallel=max_parallel, default_timeout_s=timeout_s, runtime_checker=lambda req: (True, [], []))
    case = CaseStore.create(tmp / "cases" / "Case 1", title="Test case", examiner="tester", display_tz="UTC")
    return Bench(app, store, jm, case, EvidenceService(case))


def register(bench: Bench, folder: Path, host: str = "LAB-WIN-01") -> str:
    rep = discover(folder, default_host=host)
    return bench.evidence.register(rep, mode="in_place", host=host)
