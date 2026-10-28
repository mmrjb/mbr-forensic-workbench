"""Bookmarks, notes and analyst findings.

References are immutable identities (dataset id + logical record id, or event id), never
screen row positions. Each link also stores a display snapshot taken when it was added so a
report stays understandable if views change. Analyst text is versioned: every save of a
finding records a snapshot with author/time; notes are amended by adding a new note that
points at the earlier one. None of this is a cryptographic chain of custody.
"""
from __future__ import annotations

import getpass
import json
from dataclasses import dataclass

from ..results.query import DatasetQuery, TimelineQuery
from ..storage.case_store import CaseStore, new_id
from ..storage.db import utcnow
from ..timeline.timestamps import format_ticks

FINDING_STATUSES = ("draft", "open", "confirmed", "refuted", "closed")
SEVERITIES = ("", "informational", "low", "medium", "high", "critical")


def _author() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


@dataclass
class Reference:
    kind: str  # record | event | evidence | output
    dataset_id: int | None = None
    rec_id: int | None = None
    event_id: int | None = None
    evidence_id: str | None = None
    output_id: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "record":
            return f"ds{self.dataset_id}:rec{self.rec_id}"
        if self.kind == "event":
            return f"event {self.event_id}"
        if self.kind == "evidence":
            return f"evidence {self.evidence_id}"
        return f"output {self.output_id}"


class FindingsService:
    def __init__(self, case: CaseStore, author: str | None = None):
        self.case = case
        self.author = author or case.info.examiner or _author()

    # -- snapshots ---------------------------------------------------------------------
    def snapshot(self, ref: Reference) -> dict:
        res = self.case.results
        if ref.kind == "record":
            q = DatasetQuery(res, ref.dataset_id)
            rec = q.record(ref.rec_id) or {}
            ds = q.meta
            return {"dataset": ds["name"], "tool": ds["tool_id"], "tool_version": ds["tool_version"], "job_id": ds["job_id"], "source_output": ds["source_path"],
                    "source_sha256": ds["source_sha256"], "host": ds["host"], "user": ds["user_profile"], "rec_id": ref.rec_id,
                    "lines": [rec.get("line_start"), rec.get("line_end")], "fields": rec.get("fields", [])[:60]}
        if ref.kind == "event":
            e = TimelineQuery(res).event(ref.event_id) or {}
            return {"event_id": ref.event_id, "utc": format_ticks(e.get("utc_ticks"), "UTC", e.get("precision_digits") or 0),
                    "raw_value": e.get("raw_value"), "ts_status": e.get("ts_status"), "assumption": e.get("ts_assumption"), "field": e.get("field"),
                    "meaning": e.get("meaning"), "object": e.get("object_path"), "description": e.get("description"), "host": e.get("host"),
                    "user": e.get("user_profile"), "tool": e.get("tool_id"), "source_record": f"ds{e.get('dataset_id')}:rec{e.get('rec_id')}",
                    "job_id": e.get("job_id"), "source_output": e.get("source_output"), "mapping": f"{e.get('mapping_id')}@{e.get('mapping_version')}"}
        if ref.kind == "evidence":
            r = self.case.db.query_one("SELECT original_path, artifact_type, host, sha256, hash_state FROM evidence WHERE id=?", (ref.evidence_id,))
            return dict(r) if r else {}
        r = self.case.db.query_one("SELECT abs_path, kind, sha256, job_id FROM outputs WHERE id=?", (ref.output_id,))
        return dict(r) if r else {}

    # -- bookmarks ---------------------------------------------------------------------
    def bookmark(self, ref: Reference, note: str = "", tags: str = "") -> str:
        bid = new_id("bm")
        now = utcnow()
        with self.case.db.write() as c:
            c.execute("INSERT INTO bookmarks(id, case_id, target_kind, dataset_id, rec_id, event_id, note, tags, author, snapshot_json, created_utc, modified_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (bid, self.case.id, ref.kind, ref.dataset_id, ref.rec_id, ref.event_id, note, tags, self.author, json.dumps(self.snapshot(ref), ensure_ascii=False), now, now))
        self.case.audit("bookmark.added", bid, {"ref": ref.label})
        return bid

    def bookmarks(self) -> list[dict]:
        return [dict(r) for r in self.case.db.query("SELECT * FROM bookmarks ORDER BY created_utc DESC")]

    def is_bookmarked(self, ref: Reference) -> bool:
        if ref.kind == "record":
            return bool(self.case.db.scalar("SELECT 1 FROM bookmarks WHERE target_kind='record' AND dataset_id=? AND rec_id=?", (ref.dataset_id, ref.rec_id)))
        return bool(self.case.db.scalar("SELECT 1 FROM bookmarks WHERE target_kind='event' AND event_id=?", (ref.event_id,)))

    def remove_bookmark(self, bid: str) -> None:
        self.case.db.execute("DELETE FROM bookmarks WHERE id=?", (bid,))
        self.case.audit("bookmark.removed", bid)

    # -- notes -------------------------------------------------------------------------
    def add_note(self, target_kind: str, target_id: str, text: str, amends: str | None = None) -> str:
        nid = new_id("note")
        self.case.db.execute("INSERT INTO notes(id, case_id, target_kind, target_id, text, author, created_utc, amends) VALUES (?,?,?,?,?,?,?,?)",
                             (nid, self.case.id, target_kind, target_id, text, self.author, utcnow(), amends))
        self.case.audit("note.added", nid, {"target": f"{target_kind}:{target_id}", "amends": amends})
        return nid

    def notes(self, target_kind: str | None = None, target_id: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM notes", []
        if target_kind:
            q += " WHERE target_kind=? AND (target_id=? OR ? IS NULL)"
            a = [target_kind, target_id, target_id]
        return [dict(r) for r in self.case.db.query(q + " ORDER BY created_utc", a)]

    # -- findings ----------------------------------------------------------------------
    def create(self, title: str, narrative: str = "", status: str = "draft", severity: str = "", tags: str = "") -> str:
        if not title.strip():
            raise ValueError("A finding needs a title.")
        fid = new_id("fnd")
        now = utcnow()
        with self.case.db.write() as c:
            c.execute("INSERT INTO findings(id, case_id, title, narrative, status, severity, tags, author, version, created_utc, modified_utc) VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                      (fid, self.case.id, title.strip(), narrative, status, severity or None, tags, self.author, now, now))
            self._version(c, fid)
        self.case.audit("finding.created", fid, {"title": title})
        return fid

    def update(self, fid: str, **fields) -> int:
        allowed = {"title", "narrative", "status", "severity", "tags"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "status" in sets and sets["status"] not in FINDING_STATUSES:
            raise ValueError("unknown status")
        if "severity" in sets and sets["severity"] not in SEVERITIES:
            raise ValueError("unknown severity")
        with self.case.db.write() as c:
            c.execute(f"UPDATE findings SET {', '.join(f'{k}=?' for k in sets)}, version=version+1, modified_utc=? WHERE id=?",
                      (*sets.values(), utcnow(), fid))
            v = self._version(c, fid)
        self.case.audit("finding.updated", fid, {"fields": list(sets), "version": v})
        return v

    def _version(self, c, fid: str) -> int:
        f = dict(c.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())
        links = [dict(r) for r in c.execute("SELECT * FROM finding_links WHERE finding_id=? ORDER BY added_utc", (fid,)).fetchall()]
        c.execute("INSERT OR REPLACE INTO finding_versions(finding_id, version, snapshot_json, saved_utc, author) VALUES (?,?,?,?,?)",
                  (fid, f["version"], json.dumps({"finding": f, "links": links}, ensure_ascii=False), utcnow(), self.author))
        return f["version"]

    def link(self, fid: str, ref: Reference) -> str:
        lid = new_id("lnk")
        with self.case.db.write() as c:
            c.execute("INSERT INTO finding_links(id, finding_id, target_kind, dataset_id, rec_id, event_id, evidence_id, output_id, snapshot_json, added_utc, author) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (lid, fid, ref.kind, ref.dataset_id, ref.rec_id, ref.event_id, ref.evidence_id, ref.output_id,
                       json.dumps(self.snapshot(ref), ensure_ascii=False), utcnow(), self.author))
            c.execute("UPDATE findings SET version=version+1, modified_utc=? WHERE id=?", (utcnow(), fid))
            self._version(c, fid)
        self.case.audit("finding.linked", fid, {"ref": ref.label})
        return lid

    def unlink(self, link_id: str) -> None:
        fid = self.case.db.scalar("SELECT finding_id FROM finding_links WHERE id=?", (link_id,))
        with self.case.db.write() as c:
            c.execute("DELETE FROM finding_links WHERE id=?", (link_id,))
            c.execute("UPDATE findings SET version=version+1, modified_utc=? WHERE id=?", (utcnow(), fid))
            self._version(c, fid)

    def findings(self) -> list[dict]:
        return [dict(r) for r in self.case.db.query("SELECT * FROM findings ORDER BY modified_utc DESC")]

    def finding(self, fid: str) -> dict | None:
        r = self.case.db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
        return dict(r) if r else None

    def links(self, fid: str) -> list[dict]:
        return [dict(r) for r in self.case.db.query("SELECT * FROM finding_links WHERE finding_id=? ORDER BY added_utc", (fid,))]
