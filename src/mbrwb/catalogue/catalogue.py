"""Reviewed tool catalogue and reconciliation against the official catalogue page.

The reviewed manifest (``data/catalogue.json``) is the only source of executable names,
download URLs and adapter links. A freshly fetched catalogue page is *data*: it can reveal
new entries or newer version numbers, which are shown as "New upstream entry - integration
pending review" or "Update listed upstream", but it never creates an executable mapping,
an adapter, or a download URL outside the allowlisted origins.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from urllib.parse import urlparse

from ..domain.enums import IntegrationLevel, ToolKind


@dataclass(frozen=True)
class DownloadSource:
    flavor: str
    url: str
    catalogue_version: str


@dataclass(frozen=True)
class ToolEntry:
    id: str
    name: str
    kind: ToolKind
    integration: IntegrationLevel
    category: str
    description: str
    homepage: str
    repository: str | None
    repository_status: str
    documentation: str
    downloads: dict[str, DownloadSource]
    executable: str
    archive_subdir: str | None
    managed_assembly: str | None
    input_types: tuple[str, ...]
    output_formats: tuple[str, ...]
    sidecars: tuple[str, ...]
    privileges: str
    network: str
    adapter: str | None
    expected_signers: tuple[str, ...]
    license_source: str
    redistribution: str
    gui_open_argument: str | None
    notes: str

    @property
    def is_cli(self) -> bool:
        return self.kind == ToolKind.CLI

    @property
    def is_gui(self) -> bool:
        return self.kind == ToolKind.GUI

    @property
    def downloadable(self) -> bool:
        return bool(self.downloads) and self.integration != IntegrationLevel.RESTRICTED_OPTIONAL

    def latest_catalogue_version(self, flavor: str = "net10") -> str | None:
        d = self.downloads.get(flavor) or next(iter(self.downloads.values()), None)
        return d.catalogue_version if d else None


@dataclass
class Catalogue:
    tools: dict[str, ToolEntry]
    allowed_download_origins: tuple[str, ...]
    allowed_catalogue_origins: tuple[str, ...]
    runtime_links: dict[str, str]
    notes: tuple[str, ...]
    reviewed_utc: str
    static_evidence: dict[str, dict] = field(default_factory=dict)

    def get(self, tool_id: str) -> ToolEntry:
        return self.tools[tool_id]

    def by_name(self, name: str) -> ToolEntry | None:
        key = _norm(name)
        for t in self.tools.values():
            if _norm(t.name) == key or _norm(t.executable.rsplit(".", 1)[0]) == key:
                return t
        return None

    def is_allowed_download(self, url: str) -> bool:
        return _origin(url) in self.allowed_download_origins


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")


def _read_json(name: str) -> dict:
    return json.loads(resources.files("mbrwb.catalogue").joinpath("data", name).read_text("utf-8"))


@lru_cache(maxsize=1)
def load_catalogue() -> Catalogue:
    doc = _read_json("catalogue.json")
    tools: dict[str, ToolEntry] = {}
    for t in doc["tools"]:
        tools[t["id"]] = ToolEntry(
            id=t["id"],
            name=t["name"],
            kind=ToolKind(t["kind"]),
            integration=IntegrationLevel(t["integration"]),
            category=t["category"],
            description=t["description"],
            homepage=t["homepage"],
            repository=t.get("repository"),
            repository_status=t.get("repository_status", ""),
            documentation=t["documentation"],
            downloads={k: DownloadSource(k, v["url"], v["catalogue_version"]) for k, v in t.get("downloads", {}).items()},
            executable=t["executable"],
            archive_subdir=t.get("archive_subdir"),
            managed_assembly=t.get("managed_assembly"),
            input_types=tuple(t.get("input_types", ())),
            output_formats=tuple(t.get("output_formats", ())),
            sidecars=tuple(t.get("sidecars", ())),
            privileges=t.get("privileges", ""),
            network=t.get("network", ""),
            adapter=t.get("adapter"),
            expected_signers=tuple(t.get("expected_signers", ())),
            license_source=t["license"]["source"],
            redistribution=t["license"]["redistribution"],
            gui_open_argument=t.get("gui_open_argument"),
            notes=t.get("notes", ""),
        )
    try:
        ev = _read_json("static_evidence.json")
        static = {r["tool_id"]: r for r in ev.get("records", [])}
    except FileNotFoundError:
        static = {}
    return Catalogue(
        tools=tools,
        allowed_download_origins=tuple(doc["allowed_download_origins"]),
        allowed_catalogue_origins=tuple(doc["allowed_catalogue_origins"]),
        runtime_links=doc["runtime_links"],
        notes=tuple(doc.get("catalogue_notes", ())),
        reviewed_utc=doc["reviewed_utc"],
        static_evidence=static,
    )


# -- upstream reconciliation -------------------------------------------------------------

@dataclass
class UpstreamEntry:
    name: str
    versions: dict[str, str]  # flavor -> version text as listed
    urls: dict[str, str]
    purpose: str


@dataclass
class ReconciliationItem:
    status: str  # "known" | "update_listed" | "new_pending_review" | "missing_upstream"
    name: str
    tool_id: str | None
    detail: str
    upstream_versions: dict[str, str] = field(default_factory=dict)


_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_LINK_RE = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def parse_catalogue_page(page: str) -> list[UpstreamEntry]:
    """Extract (name, version per flavor, purpose) rows from the official catalogue HTML.

    The page is treated strictly as data. Only rows with three cells (name, versions,
    purpose) are considered. URLs are recorded for review but never trusted for download
    unless they also appear in the reviewed manifest.
    """
    entries: list[UpstreamEntry] = []
    for row in _ROW_RE.findall(page):
        cells = _CELL_RE.findall(row)
        if len(cells) < 3:
            continue
        name = re.sub(r"^[^\w$]+", "", _text(cells[0])).strip()
        if not name or name.lower() == "name":
            continue
        versions: dict[str, str] = {}
        urls: dict[str, str] = {}
        version_cells = cells[1:-1]
        for idx, cell in enumerate(version_cells):
            links = _LINK_RE.findall(cell)
            flavor = ("net4", "net10")[idx] if len(version_cells) == 2 else "listed"
            if links:
                for href, label in links:
                    f = "net10" if "/net/" in href else "net4"
                    versions[f] = _text(label)
                    urls[f] = html.unescape(href)
            else:
                t = _text(cell)
                if t and t not in ("-", "—"):
                    versions[flavor] = t
        entries.append(UpstreamEntry(name=name, versions=versions, urls=urls, purpose=_text(cells[-1])))
    return entries


def reconcile(catalogue: Catalogue, upstream: list[UpstreamEntry]) -> list[ReconciliationItem]:
    items: list[ReconciliationItem] = []
    seen: set[str] = set()
    for u in upstream:
        if u.name.lower() in ("change log", "changelog"):
            continue
        entry = catalogue.by_name(u.name)
        if entry is None:
            items.append(ReconciliationItem("new_pending_review", u.name, None,
                                            "New upstream entry - integration pending review. Not downloaded, not executed, not labelled supported.",
                                            u.versions))
            continue
        seen.add(entry.id)
        changed = []
        for flavor, src in entry.downloads.items():
            listed = u.versions.get(flavor)
            if listed and listed != src.catalogue_version and listed.upper() != "NA":
                changed.append(f"{flavor}: reviewed {src.catalogue_version}, listed {listed}")
        if changed:
            items.append(ReconciliationItem("update_listed", entry.name, entry.id,
                                            "Upstream lists a different version (" + "; ".join(changed) + "). Adapters for a new version family need review before they are marked compatible.",
                                            u.versions))
        else:
            items.append(ReconciliationItem("known", entry.name, entry.id, "Matches reviewed manifest", u.versions))
    for tid, t in catalogue.tools.items():
        if tid not in seen:
            items.append(ReconciliationItem("missing_upstream", t.name, tid, "Not found on the fetched catalogue page; the reviewed entry is kept."))
    return items
