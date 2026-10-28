from __future__ import annotations

import codecs
import json

import pytest

from mbrwb.domain.enums import TimestampStatus
from mbrwb.results.importer import DatasetContext, import_csv, import_jsonl, import_output
from mbrwb.storage.db import Database
from mbrwb.storage.schema import RESULTS_MIGRATIONS
from mbrwb.timeline.mapper import map_dataset
from mbrwb.timeline.timestamps import format_ticks, parse_timestamp


@pytest.fixture
def results(tmp_path):
    db = Database(tmp_path / "results.sqlite", RESULTS_MIGRATIONS, label="results")
    yield db
    db.close()


def _ctx(path, fmt="csv", hint=None):
    return DatasetContext(case_id="case-1", name=path.name, source_path=path, source_format=fmt, schema_hint=hint, host="LAB-WIN-01")


def _rows(db, ds_id):
    t = db.query_one("SELECT row_table FROM datasets WHERE id=?", (ds_id,))["row_table"]
    return [dict(r) for r in db.query(f'SELECT * FROM "{t}" ORDER BY rec_id')]


def test_csv_bom_quotes_multiline_duplicates_and_malformed(results, tmp_path):
    p = tmp_path / "out.csv"
    body = ('Name,Path,Name,Note\r\n'
            'a,"C:\\x, y",dup1,"line1\r\nline2"\r\n'
            '\r\n'
            'b,short\r\n'
            'c,p,n,note,EXTRA1,EXTRA2\r\n'
            'd,"=cmd|\' /C calc\'!A0",x,""\r\n')
    original = codecs.BOM_UTF8 + body.encode("utf-8")
    p.write_bytes(original)
    r = import_csv(results, _ctx(p))
    assert r.state == "complete"
    assert [c["name"] for c in r.columns] == ["Name", "Path", "Name (2)", "Note"]
    assert [c["original"] for c in r.columns] == ["Name", "Path", "Name", "Note"]
    rows = _rows(results, r.dataset_id)
    assert [x["rec_id"] for x in rows] == [1, 2, 3, 4]
    assert rows[0]["c1"] == "C:\\x, y" and rows[0]["c3"] == "line1\r\nline2"
    assert (rows[0]["line_start"], rows[0]["line_end"]) == (2, 3)
    # byte range of logical record 1 reproduces the exact original bytes
    assert original[rows[0]["byte_start"]:rows[0]["byte_end"]] == 'a,"C:\\x, y",dup1,"line1\r\nline2"\r\n'.encode()
    assert rows[1]["rstatus"] == 1 and rows[1]["c3"] == ""
    assert rows[2]["rstatus"] == 2 and json.loads(rows[2]["extra"]) == ["EXTRA1", "EXTRA2"]
    assert rows[3]["c1"] == "=cmd|' /C calc'!A0", "raw values are stored unchanged"
    assert r.stats.adjusted == 2 and r.stats.ok == 2
    assert p.read_bytes() == original, "source file must not change"


def test_unknown_encoding_is_not_silently_corrupted(results, tmp_path):
    p = tmp_path / "latin.csv"
    p.write_bytes("Name\r\nCaf\xe9\r\n".encode("latin-1"))
    r = import_csv(results, _ctx(p))
    assert r.state == "failed" and "encoding" in (r.error or "").lower()
    ds = results.query_one("SELECT * FROM datasets WHERE id=?", (r.dataset_id,))
    assert ds["row_table"] is None and "explicit encoding" in ds["error"]
    ctx = _ctx(p)
    ctx.encoding = "cp1252"
    r2 = import_csv(results, ctx)
    assert r2.state == "complete" and _rows(results, r2.dataset_id)[0]["c0"] == "Café"


def test_utf16_bom_and_persian_text(results, tmp_path):
    p = tmp_path / "u16.csv"
    p.write_bytes(codecs.BOM_UTF16_LE + "Path\r\nC:\\کاربر\\سند.txt\r\n".encode("utf-16-le"))
    r = import_csv(results, _ctx(p))
    assert r.state == "complete" and _rows(results, r.dataset_id)[0]["c0"] == "C:\\کاربر\\سند.txt"


def test_jsonl_streaming_and_array(results, tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1, "b": {"c": "x"}}\n{"a": 2, "late": true}\nnot json\n', "utf-8")
    r = import_jsonl(results, _ctx(p, "jsonl"), column_scan=1)
    assert r.state == "partial" and r.stats.rejected == 1
    rows = _rows(results, r.dataset_id)
    assert rows[0]["c0"] == "1" and rows[0]["c1"] == "x"
    assert rows[1]["line_start"] == 2 and json.loads(rows[1]["extra"]) == {"late": "true"}
    q = tmp_path / "arr.json"
    q.write_text('[{"k": "v1"}, {"k": "v2"}]', "utf-8")
    r2 = import_output(results, _ctx(q, "json"))
    assert r2.state == "complete" and [x["c0"] for x in _rows(results, r2.dataset_id)] == ["v1", "v2"]


def test_empty_csv_is_a_valid_empty_dataset(results, tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("RunTime,ExecutableName\r\n", "utf-8")
    r = import_csv(results, _ctx(p, hint="pecmd.timeline"))
    assert r.state == "complete" and r.stats.total == 0
    out = map_dataset(results, r.dataset_id, schema_hint="pecmd.timeline")
    assert out.state == "mapped" and out.events == 0


# -- timestamps -------------------------------------------------------------------------

def test_timestamp_precision_and_statuses():
    p = parse_timestamp("2024-03-01 10:00:00.1234567", convention="utc")
    assert p.status == TimestampStatus.TOOL_UTC_CONVENTION and p.precision == 7
    assert format_ticks(p.ticks, "UTC", 7) == "2024-03-01 10:00:00.1234567 +00:00"
    q = parse_timestamp("2024-03-01 10:00:00.1234568", convention="utc")
    assert q.ticks - p.ticks == 1, "100 ns resolution survives (no datetime truncation)"
    assert parse_timestamp("2024-03-01T10:00:00Z").status == TimestampStatus.UTC
    off = parse_timestamp("2024-03-01T12:00:00+02:00")
    assert off.status == TimestampStatus.OFFSET and off.ticks == parse_timestamp("2024-03-01 10:00:00", convention="utc").ticks
    assert parse_timestamp("2024-03-01 10:00:00").status == TimestampStatus.UNRESOLVED
    assert parse_timestamp("1601-01-01 00:00:00", convention="utc").status == TimestampStatus.ZERO
    assert parse_timestamp("0001-01-01 00:00:00.0000000", convention="utc").status == TimestampStatus.ZERO
    assert parse_timestamp("2024-13-01 10:00:00", convention="utc").status == TimestampStatus.INVALID
    assert parse_timestamp("not a date").status == TimestampStatus.INVALID
    assert parse_timestamp("").status == TimestampStatus.EMPTY


def test_dst_ambiguity_and_gap_with_operator_timezone():
    amb = parse_timestamp("2024-11-03 01:30:00", source_tz="America/New_York")
    assert amb.status == TimestampStatus.AMBIGUOUS and amb.ticks is None
    gap = parse_timestamp("2024-03-10 02:30:00", source_tz="America/New_York")
    assert gap.status == TimestampStatus.NONEXISTENT and gap.ticks is None
    ok = parse_timestamp("2024-07-01 12:00:00", source_tz="Asia/Tehran")
    assert ok.status == TimestampStatus.OPERATOR_ASSUMPTION
    assert format_ticks(ok.ticks, "UTC", 0) == "2024-07-01 08:30:00 +00:00"
    # display in another zone does not reinterpret the instant
    assert format_ticks(ok.ticks, "Asia/Tehran", 0).startswith("2024-07-01 12:00:00")


def test_mapping_creates_multiple_events_and_reports_unsupported_schema(results, tmp_path):
    p = tmp_path / "pf.csv"
    p.write_text("SourceFilename,SourceCreated,SourceModified,ExecutableName\r\n"
                 "C:\\W\\Prefetch\\CALC.EXE-1.pf,2024-03-01 10:00:00.1234567,2024-03-02 11:00:00,CALC.EXE\r\n", "utf-8")
    r = import_csv(results, _ctx(p, hint="pecmd.files"))
    out = map_dataset(results, r.dataset_id, schema_hint="pecmd.files")
    assert out.state == "mapped" and out.events == 2
    ev = [dict(x) for x in results.query("SELECT * FROM timeline_events ORDER BY utc_ticks")]
    assert {e["field"] for e in ev} == {"SourceCreated", "SourceModified"}
    assert all(e["rec_id"] == 1 and e["dataset_id"] == r.dataset_id for e in ev)
    assert ev[0]["raw_value"] == "2024-03-01 10:00:00.1234567" and ev[0]["ts_status"] == "tool_utc_convention"
    assert ev[0]["host"] == "LAB-WIN-01"
    q = tmp_path / "other.csv"
    q.write_text("Foo,Bar\r\n1,2\r\n", "utf-8")
    r2 = import_csv(results, _ctx(q, hint="pecmd.files"))
    out2 = map_dataset(results, r2.dataset_id, schema_hint="pecmd.files")
    assert out2.state == "unsupported_schema" and out2.events == 0
    out3 = map_dataset(results, r2.dataset_id, schema_hint=None)
    assert out3.state == "no_mapping"
