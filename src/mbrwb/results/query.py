"""Paged, filtered queries over imported datasets and timeline events.

Security: values are always bound parameters; column references are resolved through the
dataset's own column metadata (``c<N>``) or a fixed whitelist, and operators come from a fixed
set. User text never becomes SQL.

Performance: pages are fetched with LIMIT/OFFSET against indexed row tables; sorting by a
column creates an index on demand (in the worker thread that runs the query); counts run once
per filter change; nothing materialises a whole dataset in Python.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..storage.db import Database, quote_ident

OPERATORS = {
    "contains": ("{col} LIKE ? ESCAPE '\\'", lambda v: f"%{_like_escape(v)}%"),
    "not_contains": ("{col} NOT LIKE ? ESCAPE '\\'", lambda v: f"%{_like_escape(v)}%"),
    "equals": ("{col} = ?", lambda v: v),
    "not_equals": ("{col} != ?", lambda v: v),
    "starts_with": ("{col} LIKE ? ESCAPE '\\'", lambda v: f"{_like_escape(v)}%"),
    "ends_with": ("{col} LIKE ? ESCAPE '\\'", lambda v: f"%{_like_escape(v)}"),
    "is_empty": ("({col} IS NULL OR {col} = '')", None),
    "not_empty": ("({col} IS NOT NULL AND {col} != '')", None),
    "gt": ("{col} > ?", lambda v: v),
    "lt": ("{col} < ?", lambda v: v),
    "num_gt": ("CAST({col} AS REAL) > ?", lambda v: float(v)),
    "num_lt": ("CAST({col} AS REAL) < ?", lambda v: float(v)),
}
OPERATOR_LABELS = {"contains": "contains", "not_contains": "does not contain", "equals": "equals", "not_equals": "does not equal",
                   "starts_with": "starts with", "ends_with": "ends with", "is_empty": "is empty", "not_empty": "is not empty",
                   "gt": "after / greater (text)", "lt": "before / less (text)", "num_gt": "number >", "num_lt": "number <"}


def _like_escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class Filter:
    column: str  # dataset column display name, or a whitelisted timeline field
    op: str
    value: str = ""

    def chip(self) -> str:
        return f"{self.column} {OPERATOR_LABELS.get(self.op, self.op)} {self.value}".strip()


@dataclass
class DatasetView:
    dataset_id: int
    filters: list[Filter] = field(default_factory=list)
    search: str = ""
    sort_column: str | None = None
    sort_desc: bool = False

    def to_json(self) -> str:
        return json.dumps({"dataset_id": self.dataset_id, "filters": [f.__dict__ for f in self.filters], "search": self.search,
                           "sort_column": self.sort_column, "sort_desc": self.sort_desc})

    @classmethod
    def from_json(cls, s: str) -> "DatasetView":
        d = json.loads(s)
        return cls(d["dataset_id"], [Filter(**f) for f in d.get("filters", [])], d.get("search", ""), d.get("sort_column"), d.get("sort_desc", False))


class DatasetQuery:
    """Query helper bound to one dataset."""

    META_COLUMNS = ("rec_id", "line_start", "line_end", "rstatus")

    def __init__(self, results: Database, dataset_id: int):
        self.db = results
        self.dataset_id = dataset_id
        row = results.query_one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
        if row is None or not row["row_table"]:
            raise ValueError("dataset has no imported rows")
        self.meta = dict(row)
        self.table = quote_ident(row["row_table"])
        self.columns: list[dict] = json.loads(row["columns_json"] or "[]")
        self._by_name = {c["name"]: f"c{c['idx']}" for c in self.columns}

    def column_sql(self, name: str) -> str:
        if name in self._by_name:
            return self._by_name[name]
        if name == "#":
            return "rec_id"
        raise KeyError(f"unknown column {name!r}")

    def _where(self, view: DatasetView) -> tuple[str, list]:
        clauses, params = [], []
        for f in view.filters:
            if f.op not in OPERATORS:
                raise ValueError(f"unsupported operator {f.op}")
            tmpl, conv = OPERATORS[f.op]
            clauses.append(tmpl.format(col=self.column_sql(f.column)))
            if conv is not None:
                params.append(conv(f.value))
        if view.search.strip():
            ors = [f"c{c['idx']} LIKE ? ESCAPE '\\'" for c in self.columns]
            if ors:
                clauses.append("(" + " OR ".join(ors) + ")")
                params += [f"%{_like_escape(view.search.strip())}%"] * len(ors)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def count(self, view: DatasetView) -> int:
        where, params = self._where(view)
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {self.table}{where}", params) or 0)

    def ensure_sort_index(self, column: str) -> None:
        col = self.column_sql(column)
        if col == "rec_id":
            return
        name = quote_ident(f"ix_{self.meta['row_table']}_{col}")
        with self.db.write() as c:
            c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {self.table}({col})")

    def page(self, view: DatasetView, offset: int, limit: int = 200) -> list[tuple]:
        where, params = self._where(view)
        order = "rec_id"
        if view.sort_column:
            order = f"{self.column_sql(view.sort_column)} {'DESC' if view.sort_desc else 'ASC'}, rec_id"
        cols = ", ".join(["rec_id", "rstatus"] + [f"c{c['idx']}" for c in self.columns])
        sql = f"SELECT {cols} FROM {self.table}{where} ORDER BY {order} LIMIT ? OFFSET ?"
        return [tuple(r) for r in self.db.query(sql, [*params, int(limit), int(offset)])]

    def record(self, rec_id: int) -> dict | None:
        r = self.db.query_one(f"SELECT * FROM {self.table} WHERE rec_id=?", (int(rec_id),))
        if r is None:
            return None
        fields = [(c["original"], r[f"c{c['idx']}"]) for c in self.columns]
        return {"rec_id": r["rec_id"], "line_start": r["line_start"], "line_end": r["line_end"], "byte_start": r["byte_start"], "byte_end": r["byte_end"],
                "rstatus": r["rstatus"], "extra": json.loads(r["extra"]) if r["extra"] else None, "fields": fields}

    def position_of(self, view: DatasetView, rec_id: int) -> int | None:
        """Row index of ``rec_id`` under the current filter/sort (for restoring a selection)."""
        where, params = self._where(view)
        if view.sort_column:
            col = self.column_sql(view.sort_column)
            val = self.db.scalar(f"SELECT {col} FROM {self.table} WHERE rec_id=?", (rec_id,))
            cmp = "<" if not view.sort_desc else ">"
            extra = f"({col} {cmp} ? OR ({col} = ? AND rec_id < ?))"
            p2 = [val, val, rec_id]
        else:
            extra, p2 = "rec_id < ?", [rec_id]
        joiner = " AND " if where else " WHERE "
        exists = self.db.scalar(f"SELECT COUNT(*) FROM {self.table}{where}{joiner}rec_id = ?", [*params, rec_id])
        if not exists:
            return None
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {self.table}{where}{joiner}{extra}", [*params, *p2]) or 0)

    def iter_rows(self, view: DatasetView, batch: int = 5000):
        where, params = self._where(view)
        order = "rec_id"
        if view.sort_column:
            order = f"{self.column_sql(view.sort_column)} {'DESC' if view.sort_desc else 'ASC'}, rec_id"
        cols = ", ".join(["rec_id"] + [f"c{c['idx']}" for c in self.columns])
        cur = self.db.conn().execute(f"SELECT {cols} FROM {self.table}{where} ORDER BY {order}", params)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield from rows


def search_case(results: Database, text: str, *, limit_per_dataset: int = 50) -> list[dict]:
    """Search every imported dataset (whole-case scope). Returns per-dataset hit counts (capped)
    and the first matching record ids."""
    out = []
    for ds in results.query("SELECT id, name FROM datasets WHERE state IN ('complete','partial') AND row_table IS NOT NULL ORDER BY id"):
        q = DatasetQuery(results, ds["id"])
        view = DatasetView(ds["id"], search=text)
        rows = q.page(view, 0, limit_per_dataset)
        if rows:
            out.append({"dataset_id": ds["id"], "name": ds["name"], "hits_shown": len(rows), "capped": len(rows) == limit_per_dataset,
                        "rec_ids": [r[0] for r in rows]})
    return out


# -- exports -------------------------------------------------------------------------------

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")


def spreadsheet_safe(value: str) -> str:
    """Neutralise formula injection for spreadsheet consumers (OWASP CSV injection guidance):
    prefix risky leading characters with an apostrophe. Raw exports never do this."""
    if value and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def export_dataset_csv(q: DatasetQuery, view: DatasetView, dest: Path, *, safe: bool = True, include_locator: bool = True) -> int:
    dest = Path(dest)
    n = 0
    with open(dest, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        header = (["source_record"] if include_locator else []) + [c["original"] for c in q.columns]
        w.writerow(header)
        for row in q.iter_rows(view):
            vals = [v if v is not None else "" for v in row[1:]]
            if safe:
                vals = [spreadsheet_safe(v) for v in vals]
            w.writerow(([f"ds{q.dataset_id}:rec{row[0]}"] if include_locator else []) + vals)
            n += 1
    return n


def export_dataset_json(q: DatasetQuery, view: DatasetView, dest: Path) -> int:
    n = 0
    names = [c["original"] for c in q.columns]
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        for row in q.iter_rows(view):
            fh.write(json.dumps({"source_record": f"ds{q.dataset_id}:rec{row[0]}", "fields": dict(zip(names, row[1:]))}, ensure_ascii=False) + "\n")
            n += 1
    return n


# -- timeline ------------------------------------------------------------------------------

TIMELINE_FILTERABLE = {"host": "host", "user": "user_profile", "tool": "tool_id", "category": "category", "meaning": "meaning",
                       "object": "object_path", "description": "description", "field": "field", "status": "ts_status"}


@dataclass
class TimelineView:
    start_ticks: int | None = None
    end_ticks: int | None = None
    hosts: list[str] = field(default_factory=list)
    users: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    text: str = ""
    unresolved_only: bool = False
    dataset_ids: list[int] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, s: str) -> "TimelineView":
        return cls(**json.loads(s))


class TimelineQuery:
    COLUMNS = ("id", "utc_ticks", "precision_digits", "ts_status", "raw_value", "field", "meaning", "category", "object_path", "description",
               "host", "user_profile", "tool_id", "dataset_id", "rec_id", "job_id", "ts_assumption", "source_output", "mapping_id", "mapping_version")

    def __init__(self, results: Database):
        self.db = results

    def _where(self, v: TimelineView) -> tuple[str, list]:
        c, p = [], []
        if v.unresolved_only:
            c.append("utc_ticks IS NULL")
        else:
            c.append("utc_ticks IS NOT NULL")
            if v.start_ticks is not None:
                c.append("utc_ticks >= ?")
                p.append(int(v.start_ticks))
            if v.end_ticks is not None:
                c.append("utc_ticks <= ?")
                p.append(int(v.end_ticks))
        for col, vals in (("host", v.hosts), ("user_profile", v.users), ("tool_id", v.tools), ("category", v.categories)):
            if vals:
                c.append(f"{col} IN ({','.join('?' * len(vals))})")
                p += list(vals)
        if v.dataset_ids:
            c.append(f"dataset_id IN ({','.join('?' * len(v.dataset_ids))})")
            p += [int(x) for x in v.dataset_ids]
        if v.text.strip():
            t = f"%{_like_escape(v.text.strip())}%"
            c.append("(description LIKE ? ESCAPE '\\' OR object_path LIKE ? ESCAPE '\\' OR meaning LIKE ? ESCAPE '\\')")
            p += [t, t, t]
        return " WHERE " + " AND ".join(c), p

    def count(self, v: TimelineView) -> int:
        w, p = self._where(v)
        return int(self.db.scalar(f"SELECT COUNT(*) FROM timeline_events{w}", p) or 0)

    def page(self, v: TimelineView, offset: int, limit: int = 200) -> list[tuple]:
        w, p = self._where(v)
        order = "utc_ticks, id" if not v.unresolved_only else "dataset_id, rec_id, id"
        return [tuple(r) for r in self.db.query(f"SELECT {', '.join(self.COLUMNS)} FROM timeline_events{w} ORDER BY {order} LIMIT ? OFFSET ?",
                                                [*p, int(limit), int(offset)])]

    def event(self, event_id: int) -> dict | None:
        r = self.db.query_one("SELECT * FROM timeline_events WHERE id=?", (int(event_id),))
        return dict(r) if r else None

    def bounds(self, v: TimelineView | None = None) -> tuple[int | None, int | None]:
        v = v or TimelineView()
        base = TimelineView(hosts=v.hosts, users=v.users, tools=v.tools, categories=v.categories, text=v.text, dataset_ids=v.dataset_ids)
        w, p = self._where(base)
        r = self.db.query_one(f"SELECT MIN(utc_ticks), MAX(utc_ticks) FROM timeline_events{w}", p)
        return (r[0], r[1]) if r else (None, None)

    def histogram(self, v: TimelineView, buckets: int = 120) -> tuple[int, int, list[int]]:
        lo, hi = (v.start_ticks, v.end_ticks)
        if lo is None or hi is None:
            blo, bhi = self.bounds(v)
            lo = blo if lo is None else lo
            hi = bhi if hi is None else hi
        if lo is None or hi is None:
            return 0, 0, []
        width = max(1, (hi - lo + 1 + buckets - 1) // buckets)
        w, p = self._where(TimelineView(lo, hi, v.hosts, v.users, v.tools, v.categories, v.text, False, v.dataset_ids))
        counts = [0] * buckets
        for r in self.db.query(f"SELECT (utc_ticks - ?) / ? AS b, COUNT(*) FROM timeline_events{w} GROUP BY b", [lo, width, *p]):
            b = int(r[0])
            if 0 <= b < buckets:
                counts[b] += r[1]
        return lo, width, counts

    def context(self, event_id: int, seconds: int = 300) -> TimelineView:
        e = self.event(event_id)
        if e is None or e["utc_ticks"] is None:
            raise ValueError("event has no resolved time")
        span = seconds * 10_000_000
        return TimelineView(e["utc_ticks"] - span, e["utc_ticks"] + span, hosts=[e["host"]] if e["host"] else [])

    def distinct(self, column: str) -> list[str]:
        if column not in ("host", "user_profile", "tool_id", "category"):
            raise ValueError("not filterable")
        return [r[0] for r in self.db.query(f"SELECT DISTINCT {column} FROM timeline_events WHERE {column} IS NOT NULL ORDER BY 1")]

    def unresolved_count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM timeline_events WHERE utc_ticks IS NULL") or 0)

    def iter_events(self, v: TimelineView, batch: int = 5000):
        w, p = self._where(v)
        cur = self.db.conn().execute(f"SELECT {', '.join(self.COLUMNS)} FROM timeline_events{w} ORDER BY utc_ticks, id", p)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield from rows


def export_timeline_csv(tq: TimelineQuery, v: TimelineView, dest: Path, display_tz: str, *, safe: bool = True) -> int:
    from ..timeline.timestamps import format_ticks

    n = 0
    with open(dest, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(["event_id", "utc", f"display ({display_tz})", "timestamp_status", "original_value", "field", "meaning", "category", "object", "description",
                    "host", "user", "tool", "source_record", "job_id", "assumption", "mapping"])
        for r in tq.iter_events(v):
            d = dict(zip(TimelineQuery.COLUMNS, r))
            vals = [d["id"], format_ticks(d["utc_ticks"], "UTC", d["precision_digits"] or 0), format_ticks(d["utc_ticks"], display_tz, d["precision_digits"] or 0),
                    d["ts_status"], d["raw_value"], d["field"], d["meaning"], d["category"] or "", d["object_path"] or "", d["description"] or "",
                    d["host"] or "", d["user_profile"] or "", d["tool_id"] or "", f"ds{d['dataset_id']}:rec{d['rec_id']}", d["job_id"] or "",
                    d["ts_assumption"] or "", f"{d['mapping_id']}@{d['mapping_version']}"]
            if safe:
                vals = [spreadsheet_safe(str(x)) if isinstance(x, str) else x for x in vals]
            w.writerow(vals)
            n += 1
    return n


def sanitize_filename(name: str, default: str = "export") -> str:
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if out.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        out = "_" + out
    return out[:120] or default


def text_preview(path: Path, *, max_bytes: int = 2 * 1024 * 1024, encoding: str = "utf-8") -> tuple[str, bool]:
    """Bounded text read for console-only outputs and logs (never loads a huge file)."""
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode(encoding, errors="replace")
    return text, truncated


def stream_text_search(path: Path, needle: str, *, limit: int = 500, encoding: str = "utf-8") -> list[tuple[int, str]]:
    hits = []
    n = needle.lower()
    with open(path, "rb") as fh:
        for i, raw in enumerate(fh, start=1):
            line = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if n in line.lower():
                hits.append((i, line[:500]))
                if len(hits) >= limit:
                    break
    return hits

