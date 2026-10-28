"""Investigation reports: self-contained HTML (no external assets, print styles), Markdown,
and a machine-readable case/job manifest with output hashes.

All case-derived text is HTML-escaped. Paths and hashes are wrapped in LTR-isolated spans so
mixed Persian/English text renders correctly. Redaction replaces selected values *in the
report only*; the case is unchanged and the report records which redactions were applied.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.product import PRODUCT
from ..config.settings import atomic_write_text
from ..findings.service import FindingsService
from ..results.query import sanitize_filename
from ..storage.case_store import CaseStore, new_id
from ..storage.db import utcnow

LIMITATIONS = [
    "Processing success means a parser ran and its output was imported; it does not mean the host is clean or that no other activity occurred.",
    "An empty result or a filter with no matches only describes the processed artifacts and scope listed here.",
    "Presence artifacts (Amcache, ShimCache, RecentFileCache) are not by themselves proof of execution; file system and ShellBag timestamps are not by themselves proof of user access.",
    "Hashes and logs support reproducibility; they do not by themselves establish legal admissibility or a complete acquisition history. The case database is not cryptographically tamper-proof.",
]


@dataclass
class ReportOptions:
    finding_ids: list[str] | None = None  # None = all
    dataset_ids: list[int] = field(default_factory=list)
    scope_note: str = ""
    timezone: str = "UTC"
    redact_examiner: bool = False
    redact_users: bool = False
    redact_hosts: bool = False
    redact_paths: bool = False
    redact_terms: list[str] = field(default_factory=list)
    include_bookmarks: bool = True
    title: str = ""

    def redaction_policy(self) -> dict:
        return {"examiner": self.redact_examiner, "users": self.redact_users, "hosts": self.redact_hosts, "paths": self.redact_paths,
                "custom_terms": len(self.redact_terms)}


class Redactor:
    def __init__(self, case: CaseStore, opts: ReportOptions):
        self.rules: list[tuple[re.Pattern, str]] = []
        info = case.info
        if opts.redact_examiner and info.examiner:
            self.rules.append((re.compile(re.escape(info.examiner), re.I), "[EXAMINER]"))
        if opts.redact_users:
            for (u,) in case.db.query("SELECT DISTINCT user_profile FROM evidence WHERE user_profile IS NOT NULL AND user_profile!=''"):
                self.rules.append((re.compile(r"(?<![\w])" + re.escape(u) + r"(?![\w])", re.I), "[USER]"))
        if opts.redact_hosts:
            for (h,) in case.db.query("SELECT DISTINCT host FROM evidence WHERE host IS NOT NULL AND host!=''"):
                self.rules.append((re.compile(r"(?<![\w-])" + re.escape(h) + r"(?![\w-])", re.I), "[HOST]"))
        for t in opts.redact_terms:
            if t.strip():
                self.rules.append((re.compile(re.escape(t.strip()), re.I), "[REDACTED]"))
        self.paths = opts.redact_paths
        self.applied = 0

    def __call__(self, value) -> str:
        if value is None:
            return ""
        s = str(value)
        for pat, rep in self.rules:
            s, n = pat.subn(rep, s)
            self.applied += n
        if self.paths:
            s, n = re.subn(r"(?:[A-Za-z]:\\|\\\\)[^\s\"'<>|]*\\", lambda m: "[PATH]\\", s)
            self.applied += n
        return s


def gather(case: CaseStore, opts: ReportOptions) -> dict:
    fs = FindingsService(case)
    info = case.info
    findings = []
    for f in fs.findings():
        if opts.finding_ids is not None and f["id"] not in opts.finding_ids:
            continue
        findings.append({**f, "links": [{**l, "snapshot": json.loads(l["snapshot_json"])} for l in fs.links(f["id"])]})
    jobs = [dict(r) for r in case.db.query("SELECT * FROM jobs ORDER BY created_utc")]
    for j in jobs:
        j["inputs"] = [dict(r) for r in case.db.query("SELECT role, path, evidence_id, size_at_submit, sha256_at_submit FROM job_inputs WHERE job_id=?", (j["id"],))]
        j["outputs"] = [dict(r) for r in case.db.query("SELECT rel_path, kind, size, sha256, completion, import_state, import_detail FROM outputs WHERE job_id=?", (j["id"],))]
    plans = [dict(r) for r in case.db.query("SELECT id, preset_id, preset_version, created_utc, summary, omissions_json, dry_run FROM workflow_plans ORDER BY created_utc")]
    for p in plans:
        p["omissions"] = json.loads(p.pop("omissions_json") or "[]")
    evidence = [dict(r) for r in case.db.query("SELECT id, original_path, artifact_type, artifact_basis, host, user_profile, volume, size, hash_state, sha256, availability FROM evidence ORDER BY host, original_path")]
    roots = [dict(r) for r in case.db.query("SELECT * FROM evidence_roots ORDER BY registered_utc")]
    datasets = [dict(r) for r in case.results.query("SELECT id, name, tool_id, tool_version, job_id, origin, state, rows_total, rows_rejected, rows_adjusted, timeline_state, timeline_mapping, timeline_events, timeline_unresolved, source_sha256 FROM datasets ORDER BY id")]
    launches = [dict(r) for r in case.db.query("SELECT * FROM launch_sessions ORDER BY started_utc")]
    bookmarks = fs.bookmarks() if opts.include_bookmarks else []
    unresolved = int(case.results.scalar("SELECT COUNT(*) FROM timeline_events WHERE utc_ticks IS NULL") or 0)
    statuses = [dict(r) for r in case.results.query("SELECT ts_status, COUNT(*) AS n FROM timeline_events GROUP BY ts_status")]
    return {"case": info.__dict__, "generated_utc": utcnow(), "options": opts.__dict__, "findings": findings, "jobs": jobs, "plans": plans,
            "evidence": evidence, "evidence_roots": roots, "datasets": datasets, "launches": launches, "bookmarks": bookmarks,
            "time": {"display_timezone": opts.timezone, "unresolved_events": unresolved, "status_counts": statuses}}


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _ltr(s) -> str:
    return f'<bdi class="ltr" dir="ltr">{_e(s)}</bdi>'


CSS = """
:root{--fg:#111827;--muted:#4b5563;--line:#d1d5db;--accent:#0369a1;--warn:#92400e;--bg:#ffffff;--card:#f8fafc}
*{box-sizing:border-box}body{font:14px/1.5 "Segoe UI",system-ui,sans-serif;color:var(--fg);background:var(--bg);margin:0;padding:32px;max-width:1100px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px;border-bottom:2px solid var(--line);padding-bottom:4px}h3{font-size:16px;margin:20px 0 6px}
.meta{color:var(--muted)}table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:12.5px}th,td{border:1px solid var(--line);padding:4px 6px;vertical-align:top;text-align:start}
th{background:var(--card)}code,.ltr,.mono{font-family:Consolas,"Cascadia Mono","Noto Sans Mono",monospace;font-size:12px;word-break:break-all}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid var(--line);font-size:12px}
.note{background:var(--card);border-inline-start:4px solid var(--accent);padding:8px 12px;margin:8px 0}.warn{border-inline-start-color:var(--warn)}
.finding{border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:12px 0}.narrative{white-space:pre-wrap}
@media print{body{padding:0;max-width:none}h2{page-break-after:avoid}.finding,table{page-break-inside:avoid}a{color:inherit}}
"""


def render_html(data: dict, opts: ReportOptions, red: Redactor) -> str:
    c = data["case"]
    title = opts.title or f"Investigation report - {c['title']}"
    out = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
           f"<title>{_e(red(title))}</title><style>{CSS}</style></head><body>"]
    out.append(f"<h1 dir='auto'>{_e(red(title))}</h1><div class='meta'>Generated {_ltr(data['generated_utc'])} by {_e(PRODUCT.name)} {_e(PRODUCT.version)}. "
               f"{_e(PRODUCT.disclaimer)}</div>")
    if c.get("is_demo"):
        out.append("<div class='note warn'><b>DEMO CASE</b> - synthetic data for demonstration only.</div>")
    out.append("<h2>Case</h2><table>")
    for k, label in (("title", "Title"), ("id", "Case ID"), ("examiner", "Examiner"), ("reference", "Reference"), ("description", "Description"),
                     ("display_tz", "Case display timezone"), ("created_utc", "Created (UTC)")):
        v = red(c.get(k)) if k != "examiner" or not opts.redact_examiner else "[EXAMINER]"
        out.append(f"<tr><th>{label}</th><td dir='auto'>{_e(v)}</td></tr>")
    out.append("</table>")
    if opts.scope_note:
        out.append(f"<h2>Scope</h2><div class='note narrative' dir='auto'>{_e(red(opts.scope_note))}</div>")
    # findings
    out.append("<h2>Findings</h2>")
    if not data["findings"]:
        out.append("<p>No findings were recorded or selected for this report.</p>")
    for f in data["findings"]:
        sev = f" &middot; analyst-assigned severity: {_e(f['severity'])}" if f.get("severity") else ""
        out.append(f"<div class='finding'><h3 dir='auto'>{_e(red(f['title']))}</h3><div class='meta'>Status: <span class='badge'>{_e(f['status'])}</span>{sev}"
                   f" &middot; version {f['version']} &middot; {_e(red(f['author']))} &middot; modified {_ltr(f['modified_utc'])}</div>"
                   f"<p class='narrative' dir='auto'>{_e(red(f['narrative']))}</p>")
        if f["links"]:
            out.append("<table><tr><th>Reference</th><th>Source</th><th>Details (snapshot when linked)</th></tr>")
            for l in f["links"]:
                s = l["snapshot"]
                if l["target_kind"] == "record":
                    ref = f"ds{l['dataset_id']}:rec{l['rec_id']}"
                    job = f"job {_ltr(s.get('job_id'))}" if s.get("job_id") else "external output (no workbench job)"
                    src = f"{_e(s.get('tool'))} {_e(s.get('tool_version'))}<br>{job}<br>{_ltr(red(s.get('source_output')))} lines {_e(s.get('lines'))}"
                    det = "<br>".join(f"<b>{_e(k)}</b>: {_ltr(red(v))}" for k, v in (s.get("fields") or [])[:25] if v)
                elif l["target_kind"] == "event":
                    ref = f"event {l['event_id']}"
                    job = f"job {_ltr(s.get('job_id'))}" if s.get("job_id") else "external output (no workbench job)"
                    src = f"{_e(s.get('tool'))} &middot; {_ltr(s.get('source_record'))}<br>{job}"
                    det = (f"<b>{_e(s.get('meaning'))}</b><br>UTC {_ltr(s.get('utc'))} (original {_ltr(s.get('raw_value'))}, {_e(s.get('ts_status'))}"
                           f"{'; ' + _e(s.get('assumption')) if s.get('assumption') else ''})<br>{_ltr(red(s.get('object')))}<br>{_e(red(s.get('description')))}")
                else:
                    ref = l["target_kind"]
                    src = ""
                    det = "<br>".join(f"{_e(k)}: {_ltr(red(v))}" for k, v in s.items())
                out.append(f"<tr><td class='mono'>{_e(ref)}</td><td>{src}</td><td>{det}</td></tr>")
            out.append("</table>")
        out.append("</div>")
    # evidence
    out.append("<h2>Source evidence</h2>")
    total = len(data["evidence"])
    hashed = sum(1 for e in data["evidence"] if e["hash_state"] == "hashed")
    out.append(f"<p>{total} registered item(s); {hashed} hashed (SHA-256). Items not hashed are listed with their state; folders are never reported as verified as a whole.</p>")
    out.append("<table><tr><th>Path</th><th>Type</th><th>Host / user / volume</th><th>Size</th><th>SHA-256 / state</th><th>Availability</th></tr>")
    for e in data["evidence"][:2000]:
        out.append(f"<tr><td>{_ltr(red(e['original_path']))}</td><td>{_e(e['artifact_type'])} <span class='meta'>({_e(e['artifact_basis'])})</span></td>"
                   f"<td dir='auto'>{_e(red(e['host']))} / {_e(red(e['user_profile']))} / {_e(e['volume'])}</td><td>{_e(e['size'])}</td>"
                   f"<td>{_ltr(e['sha256']) if e['sha256'] else _e(e['hash_state'])}</td><td>{_e(e['availability'])}</td></tr>")
    out.append("</table>")
    # processing
    out.append("<h2>Processing history</h2>")
    for p in data["plans"]:
        out.append(f"<div class='note'>Workflow <b>{_e(p['preset_id'])}</b> v{p['preset_version']} at {_ltr(p['created_utc'])}: {_e(p['summary'])}"
                   + ("" if not p["omissions"] else "<br>Not run: " + "; ".join(f"{_e(o['title'])} ({_e(o['status'])}: {_e(red(o['reason']))})" for o in p["omissions"]))
                   + "</div>")
    out.append("<table><tr><th>Job</th><th>Tool / version / verification</th><th>State</th><th>Executable SHA-256</th><th>Inputs</th><th>Outputs (SHA-256, import)</th></tr>")
    for j in data["jobs"]:
        ins = "<br>".join(f"{_e(i['role'])}: {_ltr(red(i['path']))}" for i in j["inputs"])
        outs = "<br>".join(f"{_ltr(o['rel_path'])} {_ltr((o['sha256'] or '')[:16])}&hellip; ({_e(o['completion'])}, {_e(o['import_state'])})" for o in j["outputs"])
        out.append(f"<tr><td class='mono'>{_e(j['id'])}<br>{_ltr(j['created_utc'])}</td><td>{_e(j['tool_id'])} {_e(j['tool_version'])}<br><span class='meta'>{_e(j['profile_id'])} &middot; {_e(j['verification'])}</span></td>"
                   f"<td>{_e(j['state'])}<br><span class='meta'>parser {_e(j['parser_outcome'])}, import {_e(j['import_outcome'])}</span>"
                   f"{'<br>' + _e(red(j['error_message'])) if j['error_message'] and j['state'] != 'Completed' else ''}</td>"
                   f"<td>{_ltr(j['exe_sha256'])}</td><td>{ins}</td><td>{outs}</td></tr>")
    out.append("</table>")
    if data["launches"]:
        out.append("<h3>Original GUI tool sessions</h3><p>Launch sessions are not parser jobs; they record that a tool was opened.</p><table><tr><th>Tool</th><th>Opened</th><th>Closed</th><th>Subject</th></tr>")
        for s in data["launches"]:
            out.append(f"<tr><td>{_e(s['tool_id'])}</td><td>{_ltr(s['started_utc'])}</td><td>{_ltr(s['ended_utc'] or 'unknown')}</td><td>{_ltr(red(s['subject_path']))}</td></tr>")
        out.append("</table>")
    out.append("<h2>Imported datasets</h2><table><tr><th>ID</th><th>Name</th><th>Tool</th><th>State</th><th>Records</th><th>Timeline</th></tr>")
    for d in data["datasets"]:
        if opts.dataset_ids and d["id"] not in opts.dataset_ids:
            continue
        out.append(f"<tr><td>{d['id']}</td><td>{_ltr(d['name'])}</td><td>{_e(d['tool_id'])} {_e(d['tool_version'])}</td><td>{_e(d['state'])}</td>"
                   f"<td>{_e(d['rows_total'])} records ({_e(d['rows_adjusted'])} adjusted, {_e(d['rows_rejected'])} rejected)</td>"
                   f"<td>{_e(d['timeline_state'])} {_e(d['timeline_mapping'] or '')} ({_e(d['timeline_events'])} events, {_e(d['timeline_unresolved'])} unresolved)</td></tr>")
    out.append("</table>")
    t = data["time"]
    out.append(f"<h2>Time interpretation</h2><p>Times in this report are shown in UTC unless marked otherwise; display timezone selected: {_ltr(t['display_timezone'])}. "
               "Original timestamp text is preserved next to every normalised value. 'tool_utc_convention' means the tool's documented UTC convention was applied to a value "
               "without an explicit offset; 'operator_assumption' means an analyst-supplied source timezone was applied.</p><table><tr><th>Timestamp status</th><th>Events</th></tr>")
    for s in t["status_counts"]:
        out.append(f"<tr><td>{_e(s['ts_status'])}</td><td>{s['n']}</td></tr>")
    out.append(f"</table><p>{t['unresolved_events']} event(s) have unresolved, ambiguous, zero or invalid timestamps and are excluded from time-ordered views.</p>")
    if data["bookmarks"]:
        out.append("<h2>Bookmarked records</h2><table><tr><th>Reference</th><th>Note</th><th>Added</th></tr>")
        for b in data["bookmarks"]:
            ref = f"ds{b['dataset_id']}:rec{b['rec_id']}" if b["target_kind"] == "record" else f"event {b['event_id']}"
            out.append(f"<tr><td class='mono'>{_e(ref)}</td><td dir='auto'>{_e(red(b['note']))}</td><td>{_ltr(b['created_utc'])}</td></tr>")
        out.append("</table>")
    out.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in LIMITATIONS) + "</ul>")
    pol = opts.redaction_policy()
    if any(pol.values()):
        out.append(f"<div class='note warn'>Redaction applied to this report ({_e(json.dumps(pol))}); {red.applied} value(s) replaced. The underlying case data is unchanged.</div>")
    out.append("</body></html>")
    return "".join(out)


def render_markdown(data: dict, opts: ReportOptions, red: Redactor) -> str:
    def md(s) -> str:
        return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|])", r"\\\1", red(s))
    c = data["case"]
    lines = [f"# {md(opts.title or 'Investigation report - ' + c['title'])}", "", f"Generated {data['generated_utc']} by {PRODUCT.name} {PRODUCT.version}.", ""]
    if c.get("is_demo"):
        lines += ["> **DEMO CASE** - synthetic data.", ""]
    lines += ["## Case", "", f"- Case ID: `{c['id']}`", f"- Examiner: {'[EXAMINER]' if opts.redact_examiner else md(c['examiner'])}", f"- Display timezone: {c['display_tz']}", ""]
    if opts.scope_note:
        lines += ["## Scope", "", md(opts.scope_note), ""]
    lines += ["## Findings", ""]
    for f in data["findings"]:
        lines += [f"### {md(f['title'])}", "", f"Status: {f['status']}" + (f"; analyst-assigned severity: {f['severity']}" if f.get("severity") else "") + f"; version {f['version']}", "",
                  md(f["narrative"]), ""]
        for l in f["links"]:
            s = l["snapshot"]
            if l["target_kind"] == "event":
                lines.append(f"- event {l['event_id']}: {md(s.get('meaning'))} at `{s.get('utc')}` (original `{s.get('raw_value')}`, {s.get('ts_status')}) from `{s.get('source_record')}`")
            elif l["target_kind"] == "record":
                lines.append(f"- record `ds{l['dataset_id']}:rec{l['rec_id']}` ({s.get('tool')} {s.get('tool_version')}, job `{s.get('job_id')}`)")
        lines.append("")
    lines += ["## Processing history", "", "| Job | Tool | Version | State | Parser | Import |", "|---|---|---|---|---|---|"]
    for j in data["jobs"]:
        lines.append(f"| `{j['id']}` | {j['tool_id']} | {j['tool_version'] or ''} | {j['state']} | {j['parser_outcome'] or ''} | {j['import_outcome'] or ''} |")
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in LIMITATIONS] + [""]
    pol = opts.redaction_policy()
    if any(pol.values()):
        lines += [f"_Redaction applied to this report: {json.dumps(pol)}; the case data is unchanged._", ""]
    return "\n".join(lines)


def build_manifest(case: CaseStore, data: dict) -> dict:
    return {"schema_version": 1, "kind": "mbr-workbench-case-manifest", "generated_utc": utcnow(), "product": {"name": PRODUCT.name, "version": PRODUCT.version},
            "case": data["case"], "evidence_roots": data["evidence_roots"], "evidence": data["evidence"], "plans": data["plans"],
            "jobs": [{k: v for k, v in j.items() if k not in ("env_json",)} for j in data["jobs"]], "datasets": data["datasets"], "launch_sessions": data["launches"],
            "statement": "Hashes identify content; they do not by themselves prove authenticity or chain of custody."}


def generate(case: CaseStore, opts: ReportOptions, fmt: str = "html", *, preview_only: bool = False, dest_dir: Path | None = None) -> tuple[str, Path | None]:
    data = gather(case, opts)
    red = Redactor(case, opts)
    if fmt == "html":
        text = render_html(data, opts, red)
        ext = "html"
    elif fmt == "md":
        text = render_markdown(data, opts, red)
        ext = "md"
    elif fmt == "manifest":
        text = json.dumps(build_manifest(case, data), indent=1, ensure_ascii=False, default=str)
        ext = "json"
    else:
        raise ValueError(fmt)
    if preview_only:
        return text, None
    dest_dir = Path(dest_dir or case.root / "reports")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().replace(":", "").replace("-", "")[:15]
    path = dest_dir / sanitize_filename(f"{case.info.title}_{'manifest' if fmt == 'manifest' else 'report'}_{stamp}.{ext}")
    atomic_write_text(path, text)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    fv = [{"id": f["id"], "version": f["version"]} for f in data["findings"]]
    with case.db.write() as c:
        c.execute("INSERT INTO reports(id, case_id, created_utc, format, path, sha256, timezone, options_json, finding_versions_json, redaction_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (new_id("rpt"), case.id, utcnow(), fmt, str(path), digest, opts.timezone, json.dumps(opts.__dict__, default=str), json.dumps(fv),
                   json.dumps({**opts.redaction_policy(), "replacements": red.applied})))
    case.audit("report.generated", str(path), {"format": fmt, "sha256": digest})
    return text, path


def save_draft(case: CaseStore, opts: ReportOptions) -> str:
    now = utcnow()
    case.db.execute("INSERT OR REPLACE INTO report_drafts(case_id, options_json, modified_utc) VALUES (?,?,?)", (case.id, json.dumps(opts.__dict__, default=str), now))
    return now


def load_draft(case: CaseStore) -> tuple[ReportOptions, str] | None:
    r = case.db.query_one("SELECT options_json, modified_utc FROM report_drafts WHERE case_id=?", (case.id,))
    if not r:
        return None
    d = json.loads(r["options_json"])
    return ReportOptions(**{k: v for k, v in d.items() if k in ReportOptions.__dataclass_fields__}), r["modified_utc"]
