"""Build the synthetic DEMO case and export an example report into samples/.

Usage: python scripts/make_sample.py
Everything produced is synthetic (see src/mbrwb/samples.py); no real personal data.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mbrwb.reports.report import ReportOptions, generate  # noqa: E402
from mbrwb.samples import create_demo_case  # noqa: E402


def main() -> int:
    out = ROOT / "samples"
    build = out / "build"
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    case = create_demo_case(build)
    opts = ReportOptions(timezone="Asia/Tehran", scope_note="SYNTHETIC DEMO. Scope: synthetic collection of LAB-WIN-01 (Prefetch, event logs, $MFT). "
                                                           "Not examined: memory, network logs, other hosts.")
    for fmt, name in (("html", "example_report.html"), ("md", "example_report.md"), ("manifest", "example_case_manifest.json")):
        _, path = generate(case, opts, fmt)
        shutil.copy2(path, out / name)
    red, _ = generate(case, ReportOptions(redact_hosts=True, redact_users=True, redact_examiner=True), "html")
    (out / "example_report_redacted.html").write_text(red, encoding="utf-8")
    root = case.root
    case.close()
    zpath = out / "demo_case.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.endswith(("-wal", "-shm", ".lock")):
                z.write(p, p.relative_to(build).as_posix())
    shutil.rmtree(build)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
