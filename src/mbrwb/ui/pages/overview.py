"""Overview: actionable summary. No demo numbers; counts are records/events/jobs, never scores."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...results.query import TimelineQuery, TimelineView
from ...tools.inventory import all_statuses
from ..widgets.common import Card, EmptyState, Pill, button, label
from ..widgets.histogram import Histogram
from .base import Page


def metric(card: Card, value: str, unit: str) -> tuple[QLabel, QLabel]:
    row = QHBoxLayout()
    v = label(value, obj="Metric")
    u = label(unit, obj="MetricUnit")
    row.addWidget(v)
    row.addWidget(u, 1)
    card.lay.addLayout(row)
    return v, u


class OverviewPage(Page):
    case_scoped = False

    def __init__(self, ctx, win):
        super().__init__(ctx, win, "Overview", "What is in this case, what has been processed, and what needs attention.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.grid = QGridLayout(inner)
        self.grid.setSpacing(12)
        self.grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(inner)
        self.body_layout.addWidget(scroll)
        # no-case state
        self.start = EmptyState("Start an investigation",
                                "Create a case, open an existing case, or set up the Eric Zimmerman tools. No numbers are shown until a case has real data.",
                                [button("Create a case", icon_name="plus", primary=True, on_click=win.new_case),
                                 button("Open an existing case", icon_name="folder", on_click=win.open_case_dialog),
                                 button("Set up tools", icon_name="tools", on_click=lambda: win.go("tools"))], "overview")
        self.tool_health = label("", muted=True, wrap=True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(self.start, 1)
        hl.addWidget(self.tool_health)
        self.switch.addWidget(holder)
        self._holder = holder
        # cards
        self.c_case = Card("Case")
        self.case_info = label("", wrap=True, selectable=True)
        self.c_case.lay.addWidget(self.case_info)
        self.c_evidence = Card("Evidence", clickable=True)
        self.ev_val, _ = metric(self.c_evidence, "0", "registered items")
        self.ev_detail = label("", muted=True, wrap=True)
        self.c_evidence.lay.addWidget(self.ev_detail)
        self.c_evidence.clicked.connect(lambda: win.go("evidence"))
        self.c_jobs = Card("Jobs", clickable=True)
        self.jobs_val, _ = metric(self.c_jobs, "0", "running or queued")
        self.jobs_fail = Pill("", "muted")
        self.c_jobs.lay.addWidget(self.jobs_fail)
        self.c_jobs.clicked.connect(lambda: win.go("jobs", {"filter": "attention"}))
        self.c_data = Card("Results", clickable=True)
        self.ds_val, _ = metric(self.c_data, "0", "datasets")
        self.rec_detail = label("", muted=True, wrap=True)
        self.c_data.lay.addWidget(self.rec_detail)
        self.c_data.clicked.connect(lambda: win.go("results"))
        self.c_cov = Card("Processing coverage")
        self.cov = label("", wrap=True)
        self.c_cov.lay.addWidget(self.cov)
        self.c_tools = Card("Tools", clickable=True)
        self.tools_val, _ = metric(self.c_tools, "0", "ready")
        self.tools_detail = label("", muted=True, wrap=True)
        self.c_tools.lay.addWidget(self.tools_detail)
        self.c_tools.clicked.connect(lambda: win.go("tools"))
        self.c_find = Card("Findings & bookmarks", clickable=True)
        self.find_val, _ = metric(self.c_find, "0", "findings")
        self.bm_detail = label("", muted=True)
        self.c_find.lay.addWidget(self.bm_detail)
        self.c_find.clicked.connect(lambda: win.go("findings"))
        self.c_time = Card("Event density (timeline events, UTC)")
        self.hist = Histogram(height=110)
        self.hist.rangeSelected.connect(lambda a, b: win.go("timeline", {"range": (a, b)}))
        self.time_note = label("", muted=True, wrap=True)
        self.c_time.lay.addWidget(self.hist)
        self.c_time.lay.addWidget(self.time_note)
        self.c_recent = Card("Recent analyses")
        self.recent = label("", wrap=True)
        self.c_recent.lay.addWidget(self.recent)
        g = self.grid
        g.addWidget(self.c_case, 0, 0, 1, 2)
        g.addWidget(self.c_tools, 0, 2)
        g.addWidget(self.c_evidence, 1, 0)
        g.addWidget(self.c_jobs, 1, 1)
        g.addWidget(self.c_data, 1, 2)
        g.addWidget(self.c_time, 2, 0, 1, 3)
        g.addWidget(self.c_cov, 3, 0, 1, 2)
        g.addWidget(self.c_find, 3, 2)
        g.addWidget(self.c_recent, 4, 0, 1, 3)
        for c in range(3):
            g.setColumnStretch(c, 1)
        g.setRowStretch(5, 1)

    def on_case_changed(self) -> None:
        self.switch.setCurrentWidget(self.body if self.ctx.case else self._holder)
        self.refresh()

    def on_show(self) -> None:
        self.on_case_changed()

    def refresh(self) -> None:
        ctx = self.ctx
        self.ctx.run_bg(lambda: all_statuses(ctx.catalogue, ctx.tools, ctx.runtimes()), on_done=self._tools_done)
        case = ctx.case
        if case is None:
            return

        def gather():
            db, rs = case.db, case.results
            d = {
                "info": case.info,
                "evidence": db.scalar("SELECT COUNT(*) FROM evidence"),
                "hashed": db.scalar("SELECT COUNT(*) FROM evidence WHERE hash_state='hashed'"),
                "unavail": db.scalar("SELECT COUNT(*) FROM evidence WHERE availability!='available'"),
                "hosts": db.scalar("SELECT COUNT(DISTINCT host) FROM evidence WHERE host IS NOT NULL"),
                "failed": db.scalar("SELECT COUNT(*) FROM jobs WHERE state IN ('Failed','Timed out','Interrupted')"),
                "partial": db.scalar("SELECT COUNT(*) FROM jobs WHERE state='Partial'"),
                "completed": db.scalar("SELECT COUNT(*) FROM jobs WHERE state='Completed'"),
                "last": db.scalar("SELECT MAX(ended_utc) FROM jobs"),
                "datasets": rs.scalar("SELECT COUNT(*) FROM datasets WHERE state IN ('complete','partial')"),
                "records": rs.scalar("SELECT COALESCE(SUM(rows_total),0) FROM datasets WHERE state IN ('complete','partial')"),
                "events": rs.scalar("SELECT COUNT(*) FROM timeline_events"),
                "unresolved": rs.scalar("SELECT COUNT(*) FROM timeline_events WHERE utc_ticks IS NULL"),
                "findings": db.scalar("SELECT COUNT(*) FROM findings"),
                "bookmarks": db.scalar("SELECT COUNT(*) FROM bookmarks"),
                "recent": [dict(r) for r in db.query("SELECT title, state, ended_utc, tool_id FROM jobs ORDER BY created_utc DESC LIMIT 8")],
                "coverage": [dict(r) for r in db.query(
                    "SELECT e.artifact_type AS t, COUNT(DISTINCT e.id) AS n, "
                    " SUM(CASE WHEN j.state='Completed' THEN 1 ELSE 0 END) AS ok, SUM(CASE WHEN j.state='Partial' THEN 1 ELSE 0 END) AS part "
                    "FROM evidence e LEFT JOIN job_inputs ji ON ji.evidence_id=e.id LEFT JOIN jobs j ON j.id=ji.job_id "
                    "WHERE e.artifact_type IS NOT NULL GROUP BY e.artifact_type ORDER BY n DESC")],
            }
            tq = TimelineQuery(rs)
            d["hist"] = tq.histogram(TimelineView(), 120) if d["events"] else (0, 1, [])
            return d
        self.ctx.run_bg(gather, on_done=self._case_done)

    def _tools_done(self, statuses) -> None:
        ready = sum(1 for s in statuses if s.readiness in ("ready", "launch_only"))
        setup = sum(1 for s in statuses if s.readiness in ("needs_setup", "unsupported_version"))
        winonly = sum(1 for s in statuses if s.readiness == "not_on_this_os")
        self.tools_val.setText(str(ready))
        self.tools_detail.setText(f"{setup} need setup, {winonly} installed but need Windows to run, {len(statuses)} catalogue entries.")
        self.tool_health.setText(f"Tools: {ready} ready, {setup} need setup, {winonly} installed (Windows needed) of {len(statuses)} catalogue entries.")

    def _case_done(self, d: dict) -> None:
        from ...evidence.artifacts import label as tl
        info = d["info"]
        self.case_info.setText(f"<b>{_h(info.title)}</b>{' (DEMO)' if info.is_demo else ''}<br>Examiner: {_h(info.examiner) or '-'} &middot; "
                               f"Display timezone: {_h(info.display_tz)} &middot; Created {info.created_utc[:19]}Z<br>"
                               f"Last analysis finished: {(d['last'] or 'none yet')[:19]}")
        self.ev_val.setText(f"{d['evidence']:,}")
        self.ev_detail.setText(f"{d['hashed']:,} hashed, {d['unavail']:,} unavailable/changed, {d['hosts']} host(s)")
        active = self.ctx.jobs.active_count(self.ctx.case.id)
        self.jobs_val.setText(str(active))
        if d["failed"]:
            self.jobs_fail.set(f"{d['failed']} failed / interrupted - needs attention", "error")
        elif d["partial"]:
            self.jobs_fail.set(f"{d['partial']} partial - review warnings", "warn")
        else:
            self.jobs_fail.set(f"{d['completed']} completed", "ok" if d["completed"] else "muted")
        self.ds_val.setText(f"{d['datasets']:,}")
        self.rec_detail.setText(f"{d['records']:,} records imported; {d['events']:,} timeline events ({d['unresolved']:,} with unresolved time)")
        self.find_val.setText(str(d["findings"]))
        self.bm_detail.setText(f"{d['bookmarks']} bookmarked records/events")
        lo, width, counts = d["hist"]
        self.hist.set_data(lo, width, counts, "UTC")
        self.time_note.setText("Counts are timeline events (one record can create several). Drag across bars to open that range in Timeline."
                               if counts else "No timeline events yet. Events appear after parsers with reviewed mappings complete.")
        cov = []
        for c in d["coverage"][:14]:
            state = "processed" if c["ok"] else ("partially processed" if c["part"] else "not processed yet")
            cov.append(f"{tl(c['t'])}: {c['n']} item(s), {state}")
        self.cov.setText("<br>".join(_h(x) for x in cov) or "No classified evidence registered yet.")
        self.recent.setText("<br>".join(f"{_h(r['title'])} &middot; <b>{_h(r['state'])}</b> &middot; {(r['ended_utc'] or 'running')[:19]}" for r in d["recent"])
                            or "No analyses yet. Use New analysis.")


def _h(s) -> str:
    import html
    return html.escape("" if s is None else str(s))
