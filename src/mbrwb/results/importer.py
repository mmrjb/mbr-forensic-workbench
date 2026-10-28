"""Streaming import of parser outputs into per-dataset row tables.

Guarantees:
* the original file is opened read-only and never modified;
* every value is stored exactly as the original string (no type coercion in raw columns);
* each row keeps its logical record number (``rec_id``, 1-based, stable across sorting and
  filtering), the physical line range and, for byte-addressable encodings, the byte range;
* duplicate headers are disambiguated for storage (``Name``, ``Name (2)``) while the original
  header list is kept in the dataset's column metadata;
* short rows are padded and long rows keep their extra values in an ``extra`` column; both are
  counted as *adjusted*; undecodable or NUL-containing records are *rejected* with samples;
* rows, the dataset's final state and counts are committed in one transaction, so a crash
  cannot leave a dataset marked complete with only part of its rows;
* unknown encodings are not guessed: UTF-8 (with or without BOM) and UTF-16 with BOM are
  detected; anything else needs an explicit operator-selected encoding.
"""
from __future__ import annotations

import codecs
import csv
import io
import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from ..storage.db import Database, quote_ident, utcnow

IMPORTER_VERSION = 1
BATCH = 5000
MAX_COLUMNS = 2000
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class ImportCancelled(Exception):
    pass


class UnsupportedEncoding(Exception):
    pass


@dataclass
class ImportStats:
    total: int = 0
    ok: int = 0
    adjusted: int = 0
    rejected: int = 0
    rejected_samples: list[dict] = field(default_factory=list)

    def reject(self, locator: dict, reason: str) -> None:
        self.rejected += 1
        if len(self.rejected_samples) < 50:
            self.rejected_samples.append({**locator, "reason": reason})


@dataclass
class DatasetContext:
    case_id: str
    name: str
    source_path: Path
    source_format: str  # csv | jsonl | json
    origin: str = "job"
    output_id: str | None = None
    job_id: str | None = None
    tool_id: str | None = None
    tool_version: str | None = None
    schema_hint: str | None = None
    host: str | None = None
    user_profile: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    encoding: str | None = None  # explicit operator choice; None = detect
    source_sha256: str | None = None


@dataclass
class ImportResult:
    dataset_id: int
    state: str
    stats: ImportStats
    columns: list[dict]
    error: str | None = None


def detect_encoding(path: Path) -> tuple[str, int]:
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8", 3
    if head.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", 2
    if head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", 2
    return "utf-8", 0


def disambiguate(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        base = h if h.strip() else "(blank)"
        key = base.lower()
        if key in seen:
            seen[key] += 1
            name = f"{base} ({seen[key]})"
        else:
            seen[key] = 1
            name = base
        out.append(name)
    return out


class _LineSource:
    """Iterates decoded physical lines while recording byte offsets (for UTF-8 sources)."""

    def __init__(self, fh, encoding: str, start: int, stats_cb: Callable[[int], None] | None):
        self.fh = fh
        self.encoding = encoding
        self.byte_addressable = encoding in ("utf-8", "ascii", "cp1252", "latin-1", "iso-8859-1")
        self.ends = {0: start}  # ends[n] = byte offset just after physical line n (ends[0] = data start)
        self.decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        self.stats_cb = stats_cb
        self.pos = start
        self.line_no = 0

    def __iter__(self) -> Iterator[str]:
        if self.byte_addressable:
            for raw in iter(self.fh.readline, b""):
                self.pos += len(raw)
                self.line_no += 1
                self.ends[self.line_no] = self.pos
                try:
                    yield raw.decode(self.encoding, errors="strict")
                except UnicodeDecodeError as exc:
                    raise UnsupportedEncoding(f"line {self.line_no}: bytes are not valid {self.encoding} ({exc.reason})") from exc
        else:
            text = io.TextIOWrapper(self.fh, encoding=self.encoding, errors="strict", newline="")
            try:
                for line in text:
                    self.line_no += 1
                    yield line
            except UnicodeDecodeError as exc:
                raise UnsupportedEncoding(f"line {self.line_no + 1}: {exc.reason}") from exc

    def byte_range(self, line_start: int, line_end: int) -> tuple[int | None, int | None]:
        if not self.byte_addressable:
            return None, None
        return self.ends.get(line_start - 1), self.ends.get(line_end)

    def forget_before(self, line: int) -> None:
        """Drop offsets that can no longer be needed (keeps memory flat on huge files)."""
        if len(self.ends) > 4096:
            for k in [k for k in self.ends if k < line - 1]:
                del self.ends[k]


class DatasetWriter:
    """Creates the dataset row and its table; inserts batches; finalises atomically."""

    def __init__(self, results: Database, ctx: DatasetContext):
        self.results = results
        self.ctx = ctx
        with results.write() as c:
            cur = c.execute(
                """INSERT INTO datasets(case_id, output_id, job_id, origin, tool_id, tool_version, name, source_path, source_sha256, source_size,
                   source_format, schema_profile, importer_version, host, user_profile, evidence_ids_json, state, started_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'importing',?)""",
                (ctx.case_id, ctx.output_id, ctx.job_id, ctx.origin, ctx.tool_id, ctx.tool_version, ctx.name, str(ctx.source_path), ctx.source_sha256,
                 ctx.source_path.stat().st_size if ctx.source_path.exists() else None, ctx.source_format, ctx.schema_hint, IMPORTER_VERSION,
                 ctx.host, ctx.user_profile, json.dumps(ctx.evidence_ids), utcnow()),
            )
            self.dataset_id = int(cur.lastrowid)
        self.table = f"ds_{self.dataset_id}"
        self.ncols = 0

    def create_table(self, ncols: int, c) -> None:
        self.ncols = ncols
        cols = ", ".join(f"c{i} TEXT" for i in range(ncols))
        c.execute(f"CREATE TABLE {quote_ident(self.table)}(rec_id INTEGER PRIMARY KEY, line_start INTEGER, line_end INTEGER, byte_start INTEGER, byte_end INTEGER, "
                  f"rstatus INTEGER NOT NULL DEFAULT 0, extra TEXT{', ' + cols if cols else ''})")

    def insert_sql(self) -> str:
        cols = "".join(f", c{i}" for i in range(self.ncols))
        q = ", ?" * self.ncols
        return f"INSERT INTO {quote_ident(self.table)}(rec_id, line_start, line_end, byte_start, byte_end, rstatus, extra{cols}) VALUES (?,?,?,?,?,?,?{q})"

    def finish(self, c, *, state: str, stats: ImportStats, columns: list[dict], encoding: str, error: str | None = None) -> None:
        c.execute(
            """UPDATE datasets SET state=?, rows_total=?, rows_ok=?, rows_adjusted=?, rows_rejected=?, rejected_samples_json=?, columns_json=?,
               encoding=?, row_table=?, completed_utc=?, error=? WHERE id=?""",
            (state, stats.total, stats.ok, stats.adjusted, stats.rejected, json.dumps(stats.rejected_samples, ensure_ascii=False),
             json.dumps(columns, ensure_ascii=False), encoding, self.table, utcnow(), error, self.dataset_id),
        )

    def fail(self, state: str, error: str) -> None:
        with self.results.write() as c:
            c.execute(f"DROP TABLE IF EXISTS {quote_ident(self.table)}")
            c.execute("UPDATE datasets SET state=?, error=?, completed_utc=?, row_table=NULL WHERE id=?", (state, error, utcnow(), self.dataset_id))


def import_csv(results: Database, ctx: DatasetContext, *, cancel: threading.Event | None = None,
               progress: Callable[[int], None] | None = None) -> ImportResult:
    path = ctx.source_path
    encoding, bom = (ctx.encoding, 0) if ctx.encoding else detect_encoding(path)
    if ctx.encoding and ctx.encoding.lower().replace("_", "-") in ("utf-8-sig",):
        encoding, bom = "utf-8", detect_encoding(path)[1]
    w = DatasetWriter(results, ctx)
    stats = ImportStats()
    columns: list[dict] = []
    try:
        results.conn().execute("PRAGMA cache_size=-131072")
        with open(path, "rb") as fh, results.write() as c:
            fh.seek(bom)
            src = _LineSource(fh, encoding, bom, None)
            reader = csv.reader(iter(src), strict=False)
            prev_line = 0
            try:
                header = next(reader)
            except StopIteration:
                header = []
            prev_line = reader.line_num
            if len(header) > MAX_COLUMNS:
                raise UnsupportedEncoding(f"{len(header)} columns exceed the {MAX_COLUMNS} column limit")
            names = disambiguate(header)
            columns = [{"idx": i, "name": n, "original": header[i]} for i, n in enumerate(names)]
            w.create_table(len(header), c)
            sql = w.insert_sql()
            batch = []
            rec = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise ImportCancelled()
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    rec += 1
                    stats.total += 1
                    stats.reject({"record": rec, "line": prev_line + 1}, f"CSV error: {exc}")
                    prev_line = reader.line_num
                    continue
                line_start, line_end = prev_line + 1, reader.line_num
                prev_line = reader.line_num
                if not row and line_start == line_end:
                    continue  # blank physical line between records
                rec += 1
                stats.total += 1
                bstart, bend = src.byte_range(line_start, line_end)
                src.forget_before(line_start)
                rstatus, extra = 0, None
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                    rstatus = 1
                elif len(row) > len(header):
                    extra = json.dumps(row[len(header):], ensure_ascii=False)
                    row = row[: len(header)]
                    rstatus = 2
                if rstatus:
                    stats.adjusted += 1
                else:
                    stats.ok += 1
                batch.append((rec, line_start, line_end, bstart, bend, rstatus, extra, *row))
                if len(batch) >= BATCH:
                    c.executemany(sql, batch)
                    batch.clear()
                    if progress:
                        progress(stats.total)
            if batch:
                c.executemany(sql, batch)
            state = "complete" if not stats.rejected else "partial"
            w.finish(c, state=state, stats=stats, columns=columns, encoding=encoding + ("-sig" if bom == 3 else ""))
        return ImportResult(w.dataset_id, state, stats, columns)
    except ImportCancelled:
        w.fail("cancelled", "Import cancelled by the operator. The original output is unchanged; re-import creates a fresh dataset.")
        return ImportResult(w.dataset_id, "cancelled", stats, columns, "cancelled")
    except UnsupportedEncoding as exc:
        msg = f"Unsupported or inconsistent encoding: {exc}. Re-import with an explicit encoding if you know it."
        w.fail("failed", msg)
        return ImportResult(w.dataset_id, "failed", stats, columns, msg)
    except (OSError, ValueError) as exc:
        w.fail("failed", f"{type(exc).__name__}: {exc}")
        return ImportResult(w.dataset_id, "failed", stats, columns, str(exc))


def _flatten(obj, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten(v, key))
            elif isinstance(v, list):
                out[key] = json.dumps(v, ensure_ascii=False)
            else:
                out[key] = "" if v is None else (json.dumps(v) if isinstance(v, bool) else str(v))
    else:
        out[prefix or "value"] = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (list, dict)) else str(obj)
    return out


def sniff_json_layout(path: Path) -> str:
    """'array' (a single JSON array), 'jsonl' (one object per line) or 'unknown'."""
    enc, bom = detect_encoding(path)
    with open(path, "rb") as fh:
        fh.seek(bom)
        head = fh.read(4096).decode(enc, errors="ignore").lstrip()
    if head.startswith("["):
        return "array"
    if head.startswith("{"):
        first = head.split("\n", 1)[0].strip()
        try:
            json.loads(first)
            return "jsonl"
        except ValueError:
            return "unknown"
    return "unknown"


def import_jsonl(results: Database, ctx: DatasetContext, *, cancel: threading.Event | None = None,
                 progress: Callable[[int], None] | None = None, column_scan: int = 2000) -> ImportResult:
    """Line-delimited JSON objects, streamed. Columns are the union of keys seen in the first
    ``column_scan`` records; keys first seen later are kept per record in ``extra``."""
    path = ctx.source_path
    encoding, bom = detect_encoding(path)
    w = DatasetWriter(results, ctx)
    stats = ImportStats()
    columns: list[dict] = []
    try:
        with open(path, "rb") as fh:
            fh.seek(bom)
            keys: list[str] = []
            seen = set()
            for i, raw in enumerate(fh):
                if i >= column_scan:
                    break
                try:
                    for k in _flatten(json.loads(raw.decode(encoding))):
                        if k not in seen:
                            seen.add(k)
                            keys.append(k)
                except (ValueError, UnicodeDecodeError):
                    continue
        columns = [{"idx": i, "name": k, "original": k} for i, k in enumerate(keys)]
        index = {k: i for i, k in enumerate(keys)}
        with open(path, "rb") as fh, results.write() as c:
            fh.seek(bom)
            w.create_table(len(keys), c)
            sql = w.insert_sql()
            pos = bom
            batch = []
            line = 0
            for raw in fh:
                if cancel is not None and cancel.is_set():
                    raise ImportCancelled()
                line += 1
                start, pos = pos, pos + len(raw)
                if not raw.strip():
                    continue
                stats.total += 1
                try:
                    flat = _flatten(json.loads(raw.decode(encoding)))
                except (ValueError, UnicodeDecodeError) as exc:
                    stats.reject({"record": stats.total, "line": line}, f"invalid JSON: {exc}")
                    continue
                vals = [""] * len(keys)
                extra = {}
                for k, v in flat.items():
                    if k in index:
                        vals[index[k]] = v
                    else:
                        extra[k] = v
                rstatus = 2 if extra else 0
                stats.adjusted += 1 if extra else 0
                stats.ok += 0 if extra else 1
                batch.append((stats.total, line, line, start, pos, rstatus, json.dumps(extra, ensure_ascii=False) if extra else None, *vals))
                if len(batch) >= BATCH:
                    c.executemany(sql, batch)
                    batch.clear()
                    if progress:
                        progress(stats.total)
            if batch:
                c.executemany(sql, batch)
            state = "complete" if not stats.rejected else "partial"
            w.finish(c, state=state, stats=stats, columns=columns, encoding=encoding)
        return ImportResult(w.dataset_id, state, stats, columns)
    except ImportCancelled:
        w.fail("cancelled", "Import cancelled by the operator.")
        return ImportResult(w.dataset_id, "cancelled", stats, columns, "cancelled")
    except (OSError, ValueError) as exc:
        w.fail("failed", f"{type(exc).__name__}: {exc}")
        return ImportResult(w.dataset_id, "failed", stats, columns, str(exc))


def import_json_array(results: Database, ctx: DatasetContext, *, cancel: threading.Event | None = None, max_bytes: int = 256 * 1024**2) -> ImportResult:
    """A top-level JSON array. Decoded incrementally element by element with
    ``json.JSONDecoder.raw_decode`` over a sliding buffer; locator = element index."""
    path = ctx.source_path
    encoding, bom = detect_encoding(path)
    w = DatasetWriter(results, ctx)
    stats = ImportStats()
    dec = json.JSONDecoder()
    elements: list[dict] = []
    try:
        if path.stat().st_size > max_bytes:
            raise ValueError(f"JSON array larger than {max_bytes // 1024**2} MB; import the CSV or JSONL output instead")
        text = path.read_bytes()[bom:].decode(encoding)
        i = text.index("[") + 1
        idx = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise ImportCancelled()
            while i < len(text) and text[i] in " \r\n\t,":
                i += 1
            if i >= len(text) or text[i] == "]":
                break
            obj, end = dec.raw_decode(text, i)
            elements.append(_flatten(obj))
            idx += 1
            i = end
        keys: list[str] = []
        seen = set()
        for e in elements:
            for k in e:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        columns = [{"idx": n, "name": k, "original": k} for n, k in enumerate(keys)]
        with results.write() as c:
            w.create_table(len(keys), c)
            sql = w.insert_sql()
            rows = []
            for n, e in enumerate(elements, start=1):
                rows.append((n, None, None, None, None, 0, json.dumps({"json_index": n - 1}), *[e.get(k, "") for k in keys]))
            c.executemany(sql, rows)
            stats.total = stats.ok = len(rows)
            w.finish(c, state="complete", stats=stats, columns=columns, encoding=encoding)
        return ImportResult(w.dataset_id, "complete", stats, columns)
    except ImportCancelled:
        w.fail("cancelled", "Import cancelled by the operator.")
        return ImportResult(w.dataset_id, "cancelled", stats, [], "cancelled")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        w.fail("failed", f"{type(exc).__name__}: {exc}")
        return ImportResult(w.dataset_id, "failed", stats, [], str(exc))


def import_output(results: Database, ctx: DatasetContext, **kw) -> ImportResult:
    if ctx.source_format == "csv":
        return import_csv(results, ctx, **kw)
    layout = sniff_json_layout(ctx.source_path)
    if layout == "jsonl":
        ctx.source_format = "jsonl"
        return import_jsonl(results, ctx, **{k: v for k, v in kw.items() if k in ("cancel", "progress")})
    if layout == "array":
        ctx.source_format = "json"
        return import_json_array(results, ctx, cancel=kw.get("cancel"))
    w = DatasetWriter(results, ctx)
    w.fail("failed", "Unrecognised JSON layout (neither a JSON array nor one object per line). The raw file remains available.")
    return ImportResult(w.dataset_id, "failed", ImportStats(), [], "unrecognised JSON layout")
