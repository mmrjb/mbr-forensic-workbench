"""Capture screenshots of the actual running UI (offscreen Qt platform).

Usage: python scripts/screenshots.py --out docs/screenshots [--theme dark|light] [--size 1440x900] [--official-archives DIR]

Uses an isolated profile (MBRWB_HOME in a temp folder) with the synthetic DEMO case. When
--official-archives points at official EZ Tools zips, a few are *copied* into the profile so
the Tools page shows real versions and hashes; nothing is executed.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/screenshots"))
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--official-archives", type=Path)
    a = ap.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    home = Path(tempfile.mkdtemp(prefix="mbrwb-shots-", dir=os.environ.get("MBRWB_SHOT_TMP")))
    os.environ["MBRWB_HOME"] = str(home)
    os.environ["MBRWB_NO_FIRST_RUN"] = "1"
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from mbrwb.config.paths import resolve_paths
    from mbrwb.samples import create_demo_case
    from mbrwb.ui.context import AppContext
    from mbrwb.ui.main_window import PAGES, MainWindow
    from mbrwb.ui.theme import apply_theme

    app = QApplication([])
    ctx = AppContext(resolve_paths())
    if a.official_archives:
        for name in ("PECmd.zip", "MFTECmd.zip", "EvtxECmd.zip", "TimelineExplorer.zip", "RegistryExplorer.zip"):
            p = a.official_archives / name
            if p.exists():
                ctx.tools.install_archive(p)
    apply_theme(app, a.theme)
    w, h = (int(x) for x in a.size.split("x"))
    win = MainWindow(ctx)
    win.resize(w, h)
    win.show()

    def settle(ms: int = 400) -> None:
        for _ in range(60):
            app.processEvents()
            if QThreadPool.globalInstance().activeThreadCount() == 0:
                break
            QThreadPool.globalInstance().waitForDone(50)
        end = ms
        while end > 0:
            app.processEvents()
            QThreadPool.globalInstance().waitForDone(20)
            end -= 20

    a.out.mkdir(parents=True, exist_ok=True)
    sfx = f"_{a.theme}_{a.size}{a.suffix}"
    settle()
    win.grab().save(str(a.out / f"00_overview_no_case{sfx}.png"))
    case = create_demo_case(ctx.paths.demo_cases_dir)
    ctx.open_case(case)
    settle()
    for i, (key, _, _) in enumerate(PAGES):
        win.nav.setCurrentRow(i)
        settle(500)
        if key == "results":
            ds = case.results.scalar("SELECT id FROM datasets WHERE schema_profile='evtxecmd.events'")
            win.pages["results"]._select_dataset(ds, 2)
            settle(700)
            win.pages["results"].table.selectRow(1)
            settle(300)
        if key == "timeline":
            settle(500)
            win.pages["timeline"].table.selectRow(3)
            settle(300)
        if key == "workflows":
            from mbrwb.workflows.presets import PRESET_BY_ID
            win.pages["workflows"]._build_plan(PRESET_BY_ID["quick_triage"])
            settle(800)
        if key == "findings":
            win.pages["findings"].list.setCurrentRow(0)
            settle(300)
        win.grab().save(str(a.out / f"{i + 1:02d}_{key}{sfx}.png"))
    win.go("findings")
    fp = win.pages["findings"]
    fp.findChild(type(fp.preview_view).__mro__[0])  # keep reference
    tabs = fp.body.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)[0]
    tabs.setCurrentIndex(1)
    fp.preview()
    settle(1500)
    win.grab().save(str(a.out / f"11_report_preview{sfx}.png"))
    ctx.shutdown()
    print(f"screenshots written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
