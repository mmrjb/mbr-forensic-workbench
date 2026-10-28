"""Explicit, versioned mappings from recognised output schemas to timeline events.

A mapping applies only when the dataset's schema hint is listed *and* every required column is
present. Otherwise the dataset is marked ``unsupported_schema`` (hint known, columns differ)
or ``no_mapping`` (no reviewed mapping), and zero events are created - never guessed from
column names. One source row can yield several events with distinct meanings.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from ..storage.db import Database, quote_ident
from ..domain.enums import TimestampStatus
from .timestamps import parse_timestamp


@dataclass(frozen=True)
class MappingProfile:
    mapping_id: str
    version: int
    tool_id: str
    schema_hints: tuple[str, ...]
    required_columns: tuple[str, ...]
    convention: str
    convention_basis: str
    object_columns: tuple[str, ...]
    events: tuple[dict, ...]
    column_verification: str
    notes: str


@lru_cache(maxsize=1)
def load_mappings() -> dict[str, MappingProfile]:
    out = {}
    base = resources.files("mbrwb.timeline").joinpath("mappings")
    for f in base.iterdir():
        if f.name.endswith(".json"):
            d = json.loads(f.read_text("utf-8"))
            out[d["mapping_id"]] = MappingProfile(
                d["mapping_id"], int(d["version"]), d["tool_id"], tuple(d["schema_hints"]), tuple(d["required_columns"]),
                d["timestamp_convention"], d["convention_basis"], tuple(d.get("object_columns", ())), tuple(d["events"]),
                d.get("column_verification", ""), d.get("notes", ""),
            )
    return out


def mapping_for(schema_hint: str | None) -> MappingProfile | None:
    if not schema_hint:
        return None
    for m in load_mappings().values():
        if schema_hint in m.schema_hints:
            return m
    return None


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _compile(template: str) -> list[tuple[bool, str]]:
    """Split a description template once into literal and column parts."""
    parts, pos = [], 0
    for m in _PLACEHOLDER.finditer(template):
        if m.start() > pos:
            parts.append((False, template[pos:m.start()]))
        parts.append((True, m.group(1)))
        pos = m.end()
    if pos < len(template):
        parts.append((False, template[pos:]))
    return parts


def _render_parts(parts: list[tuple[bool, str]], row: dict[str, str]) -> str:
    return "".join(row.get(v, "") if is_col else v for is_col, v in parts).strip(" -:\\")


def _render(template: str, row: dict[str, str]) -> str:
    return _render_parts(_compile(template), row)


@dataclass
class MappingOutcome:
    state: str  # mapped | unsupported_schema | no_mapping | failed | cancelled
    events: int = 0
    unresolved: int = 0
    detail: str = ""
    mapping_id: str | None = None


def map_dataset(results: Database, dataset_id: int, *, schema_hint: str | None, source_tz: str | None = None,
                cancel: threading.Event | None = None, ctx: dict | None = None) -> MappingOutcome:
    """Create timeline events for one dataset (replacing any earlier events for it)."""
    ds = results.query_one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
    if ds is None or ds["state"] not in ("complete", "partial"):
        return MappingOutcome("failed", detail="dataset is not imported")
    mapping = mapping_for(schema_hint)
    if mapping is None:
        _set_state(results, dataset_id, "no_mapping", None, 0, 0)
        return MappingOutcome("no_mapping", detail="No reviewed timeline mapping exists for this output; raw rows remain searchable.")
    cols = json.loads(ds["columns_json"] or "[]")
    by_name = {c["original"]: c["idx"] for c in cols}
    missing = [c for c in mapping.required_columns if c not in by_name]
    if missing:
        _set_state(results, dataset_id, "unsupported_schema", mapping.mapping_id, 0, 0)
        return MappingOutcome("unsupported_schema", detail=f"Columns expected by mapping {mapping.mapping_id} v{mapping.version} are missing: {', '.join(missing)}. "
                                                           "No events were created; the raw dataset is unaffected.", mapping_id=mapping.mapping_id)
    needed = set(mapping.object_columns)
    for e in mapping.events:
        needed.add(e["field"])
        needed.update(_PLACEHOLDER.findall(e.get("description", "")))
    needed = [n for n in needed if n in by_name]
    sel = ", ".join(f"c{by_name[n]}" for n in needed)
    table = quote_ident(ds["row_table"])
    ctx = ctx or {}
    total = unresolved = 0
    assumption = f"source timezone {source_tz} (operator assumption)" if source_tz else None
    convention = mapping.convention
    insert = ("INSERT INTO timeline_events(case_id, dataset_id, rec_id, host, user_profile, job_id, tool_id, tool_version, source_output, field, raw_value,"
              " utc_ticks, precision_digits, ts_status, ts_assumption, meaning, category, object_path, description, mapping_id, mapping_version)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    compiled = [(_compile(e.get("description") or ""), e["field"], e["meaning"], e.get("category")) for e in mapping.events]
    head = (ds["case_id"], dataset_id)
    ctx_cols = (ds["host"], ds["user_profile"], ds["job_id"], ds["tool_id"], ds["tool_version"], ds["source_path"])
    tail = (mapping.mapping_id, mapping.version)
    conv = convention if not source_tz else "unknown"
    idx = list(enumerate(needed, start=1))
    reader = results.conn()
    reader.execute("PRAGMA cache_size=-262144")  # 256 MB page cache for this bulk write (index pages stay hot)
    try:
        with results.write() as c:
            c.execute("DELETE FROM timeline_events WHERE dataset_id=?", (dataset_id,))
            batch = []
            cur = reader.execute(f"SELECT rec_id, {sel} FROM {table} ORDER BY rec_id")
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                for r in rows:
                    row = {n: (r[i] or "") for i, n in idx}
                    obj = next((row[o] for o in mapping.object_columns if row.get(o)), "")
                    for parts, fld, meaning, category in compiled:
                        raw = row.get(fld, "")
                        if not raw:
                            continue
                        p = parse_timestamp(raw, convention=conv, source_tz=source_tz)
                        if p.status is TimestampStatus.EMPTY:
                            continue
                        if not p.resolved:
                            unresolved += 1
                        batch.append((*head, r[0], *ctx_cols, fld, raw, p.ticks, p.precision, p.status.value, assumption or (p.note or None),
                                      meaning, category, obj, _render_parts(parts, row)[:1000], *tail))
                        total += 1
                if batch:
                    c.executemany(insert, batch)
                    batch.clear()
            c.execute("UPDATE datasets SET timeline_state='mapped', timeline_mapping=?, timeline_events=?, timeline_unresolved=? WHERE id=?",
                      (f"{mapping.mapping_id}@{mapping.version}", total, unresolved, dataset_id))
    except InterruptedError:
        return MappingOutcome("cancelled", detail="Timeline mapping cancelled; no events were committed.")
    finally:
        reader.execute("PRAGMA cache_size=-65536")
    return MappingOutcome("mapped", total, unresolved, f"{total} events, {unresolved} with unresolved/invalid time", mapping.mapping_id)


def _set_state(results: Database, dataset_id: int, state: str, mapping: str | None, events: int, unresolved: int) -> None:
    with results.write() as c:
        c.execute("DELETE FROM timeline_events WHERE dataset_id=?", (dataset_id,))
        c.execute("UPDATE datasets SET timeline_state=?, timeline_mapping=?, timeline_events=?, timeline_unresolved=? WHERE id=?",
                  (state, mapping, events, unresolved, dataset_id))
