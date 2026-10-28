"""Synthetic million-row benchmark (import, paging, filtering, sorting, timeline, UI latency).

Usage: python scripts/benchmark_million.py --dir <scratch folder> [--rows 1000000] [--json out.json]

The CSV is synthetic (MFTECmd-shaped columns). Numbers measure this workbench's own storage
and UI paths on the machine where the script runs; they say nothing about EZ tool speed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HEADER = ("EntryNumber,SequenceNumber,InUse,ParentPath,FileName,Extension,FileSize,IsDirectory,Created0x10,Created0x30,LastModified0x10,LastModified0x30,"
          "LastRecordChange0x10,LastRecordChange0x30,LastAccess0x10,LastAccess0x30,SiFlags,NameType,SecurityId,ZoneIdContents")


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def make_csv(path: Path, rows: int) -> None:
    rnd = random.Random(42)
    exts = [".txt", ".exe", ".dll", ".log", ".docx", ".tmp", ".ps1", ".lnk"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(HEADER + "\r\n")
        for i in range(rows):
            d = rnd.randint(0, 900)
            ts = f"2026-{1 + d // 80:02d}-{1 + d % 28:02d} {rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}.{rnd.randint(0, 9999999):07d}"
            ts2 = f"2026-{1 + d // 80:02d}-{1 + d % 28:02d} {rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}.{rnd.randint(0, 9999999):07d}"
            ext = exts[i % len(exts)]
            name = f"file_{i:07d}{ext}" if i % 997 else f'"name, with comma {i}{ext}"'
            fh.write(f"{i},{rnd.randint(1, 9)},True,.\\Users\\user{i % 50}\\AppData\\Local\\dir{i % 1000},{name},{ext},{rnd.randint(0, 10**7)},False,"
                     f"{ts},,{ts2},,{ts2},,,,Archive,Windows,{rnd.randint(100, 9000)},\r\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    a.dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from mbrwb.results.importer import DatasetContext, import_csv
    from mbrwb.results.query import DatasetQuery, DatasetView, Filter, TimelineQuery, TimelineView
    from mbrwb.storage.db import Database
    from mbrwb.storage.schema import RESULTS_MIGRATIONS
    from mbrwb.timeline.mapper import map_dataset

    out: dict = {"rows": a.rows, "machine": {"platform": platform.platform(), "python": sys.version.split()[0], "cpu_count": os.cpu_count(),
                                               "processor": platform.processor() or platform.machine()}}
    try:
        with open("/proc/meminfo") as fh:
            out["machine"]["mem_total"] = fh.readline().split(":")[1].strip()
        with open("/proc/cpuinfo") as fh:
            out["machine"]["cpu_model"] = next((l.split(":")[1].strip() for l in fh if l.startswith("model name")), "")
    except OSError:
        pass
    csvp = a.dir / "synthetic_mft.csv"
    t = time.perf_counter()
    make_csv(csvp, a.rows)
    out["generate_s"] = round(time.perf_counter() - t, 2)
    out["csv_mb"] = round(csvp.stat().st_size / 1024**2, 1)
    dbp = a.dir / "results.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(str(dbp) + suffix).unlink(missing_ok=True)
    db = Database(dbp, RESULTS_MIGRATIONS, label="results")
    t = time.perf_counter()
    r = import_csv(db, DatasetContext(case_id="bench", name=csvp.name, source_path=csvp, source_format="csv", schema_hint="mftecmd.mft"))
    out["import_s"] = round(time.perf_counter() - t, 2)
    out["import_state"] = r.state
    out["imported_records"] = r.stats.total
    out["rss_after_import_mb"] = round(rss_mb(), 1)
    t = time.perf_counter()
    m = map_dataset(db, r.dataset_id, schema_hint="mftecmd.mft")
    out["timeline_map_s"] = round(time.perf_counter() - t, 2)
    out["timeline_events"] = m.events
    q = DatasetQuery(db, r.dataset_id)
    v = DatasetView(r.dataset_id)

    def timed(fn):
        t0 = time.perf_counter()
        res = fn()
        return res, round((time.perf_counter() - t0) * 1000, 1)
    out["count_all_ms"] = timed(lambda: q.count(v))[1]
    out["first_page_ms"] = timed(lambda: q.page(v, 0, 200))[1]
    out["deep_page_900k_ms"] = timed(lambda: q.page(v, min(900_000, a.rows - 200), 200))[1]
    fv = DatasetView(r.dataset_id, [Filter("Extension", "equals", ".ps1")])
    out["filter_equals_count_ms"] = timed(lambda: q.count(fv))[1]
    out["filter_equals_first_page_ms"] = timed(lambda: q.page(fv, 0, 200))[1]
    sv = DatasetView(r.dataset_id, search="dir999")
    out["search_all_columns_first_page_ms"] = timed(lambda: q.page(sv, 0, 200))[1]
    out["search_all_columns_count_ms"] = timed(lambda: q.count(sv))[1]
    out["sort_index_build_ms"] = timed(lambda: q.ensure_sort_index("FileName"))[1]
    srt = DatasetView(r.dataset_id, sort_column="FileName", sort_desc=True)
    out["sorted_first_page_ms"] = timed(lambda: q.page(srt, 0, 200))[1]
    out["position_of_ms"] = timed(lambda: q.position_of(srt, a.rows // 2))[1]
    tq = TimelineQuery(db)
    out["timeline_count_ms"] = timed(lambda: tq.count(TimelineView()))[1]
    out["timeline_first_page_ms"] = timed(lambda: tq.page(TimelineView(), 0, 200))[1]
    out["timeline_histogram_ms"] = timed(lambda: tq.histogram(TimelineView(), 140))[1]
    out["db_mb"] = round(dbp.stat().st_size / 1024**2, 1)
    out["rss_peak_mb"] = round(rss_mb(), 1)
    # UI responsiveness: event-loop gaps while the paged model counts/fetches in the background
    from PySide6.QtCore import QElapsedTimer, QTimer
    from PySide6.QtWidgets import QApplication, QTableView

    from mbrwb.config.paths import AppPaths
    from mbrwb.ui.context import AppContext
    from mbrwb.ui.widgets.paged_model import PagedTableModel

    app = QApplication([])
    os.environ["MBRWB_HOME"] = str(a.dir / "home")
    ctx = AppContext(AppPaths(a.dir / "home"))
    model = PagedTableModel(ctx)
    view = QTableView()
    view.setModel(model)
    view.resize(1200, 800)
    view.show()
    gaps: list[float] = []
    clock = QElapsedTimer()
    clock.start()
    last = [clock.elapsed()]

    def tick():
        now = clock.elapsed()
        gaps.append(now - last[0])
        last[0] = now
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(10)
    ready = []
    model.countReady.connect(lambda n: ready.append((n, clock.elapsed())))
    t0 = clock.elapsed()
    model.configure(["#"] + [c["name"] for c in q.columns], lambda off, lim: q.page(sv, off, lim), lambda: q.count(sv))
    while not ready and clock.elapsed() - t0 < 120_000:
        app.processEvents()
    first_rows = None
    while clock.elapsed() - t0 < 120_000:
        app.processEvents()
        if model.total() and model.row_tuple(0) is not None:
            first_rows = clock.elapsed() - t0
            break
    deep = model.total() - 1
    t1 = clock.elapsed()
    view.scrollTo(model.index(deep, 0))
    model.row_tuple(deep)
    while model.row_tuple(deep) is None and clock.elapsed() - t1 < 60_000:
        app.processEvents()
    out["ui_search_count_ready_ms"] = ready[0][1] - t0 if ready else None
    out["ui_first_rows_visible_ms"] = first_rows
    out["ui_scroll_to_last_row_ms"] = clock.elapsed() - t1
    out["ui_max_event_loop_gap_ms"] = max(gaps) if gaps else None
    out["ui_event_loop_ticks"] = len(gaps)
    out["qt_objects_note"] = "QTableView + PagedTableModel: only fetched pages (200 rows each, max 60 cached) exist in Python; no per-cell items."
    ctx.shutdown()
    db.close()
    text = json.dumps(out, indent=2)
    print(text)
    if a.json:
        a.json.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
