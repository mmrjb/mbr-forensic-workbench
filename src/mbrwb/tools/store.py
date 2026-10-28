"""Managed tool installations.

Layout: ``<tools_dir>/<tool_id>/<version>-<flavor>-<sha8>/...`` plus ``<tools_dir>/.staging``
(same volume, so promotion is an atomic rename). An installation directory is immutable once
promoted; updates create a new directory and the previous one is retained until it is neither
selected nor used by an active job and exceeds the retention count.

Import sources:
* an official tool archive (``X.zip``),
* an existing EZ Tools folder (for example a Get-ZimmermanTools destination),
* an operator-prepared offline bundle (``mbrwb-offline-bundle.json`` + files).
Copies are made into the managed store, so later changes in the source folder never alter a
recorded job's tool identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import file_sha256, inventory
from ..catalogue.catalogue import Catalogue, ToolEntry, load_catalogue
from ..catalogue.pe import PEFormatError, inspect_pe
from ..domain.enums import ToolKind
from ..domain.models import InstallationRef
from ..storage.db import Database, utcnow
from .archive import ExtractionCancelled, UnsafeArchive, is_reparse_or_link, remove_tree, safe_extract_zip
from .signature import assess_signature

FLAT_SIDECARS = {"amcacheparser": ("e_sqlite3.dll", "libe_sqlite3.so", "libe_sqlite3.dylib"), "wxtcmd": ("e_sqlite3.dll", "libe_sqlite3.so", "libe_sqlite3.dylib")}
BUNDLE_MANIFEST = "mbrwb-offline-bundle.json"


class InstallError(Exception):
    pass


@dataclass
class LocatedTool:
    tool: ToolEntry
    root: Path  # folder that will become the installation root
    executable: Path
    files: list[Path] = field(default_factory=list)  # for flat layouts: the files to copy
    flavor: str = "unknown"


@dataclass
class InstallResult:
    installed: list[InstallationRef] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _row_to_ref(r) -> InstallationRef:
    return InstallationRef(
        id=r["id"], tool_id=r["tool_id"], version=r["version"], product_version=r["product_version"], flavor=r["flavor"] or "unknown",
        install_dir=r["install_dir"], executable=r["executable"], managed_assembly=r["managed_assembly"], exe_sha256=r["exe_sha256"],
        assembly_sha256=r["assembly_sha256"], signature_state=r["signature_state"], runtime_json=r["runtime_json"],
    )


class ToolStore:
    def __init__(self, app_db: Database, tools_dir: Path, retained_versions: int = 2, catalogue: Catalogue | None = None):
        self.db = app_db
        self.tools_dir = Path(tools_dir)
        self.retained = retained_versions
        self.catalogue = catalogue or load_catalogue()
        self._in_use: dict[str, int] = {}
        self._lock = threading.RLock()
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    # -- queries ----------------------------------------------------------------------
    def installations(self, tool_id: str | None = None) -> list[InstallationRef]:
        if tool_id:
            rows = self.db.query("SELECT * FROM installations WHERE tool_id=? ORDER BY installed_utc DESC", (tool_id,))
        else:
            rows = self.db.query("SELECT * FROM installations ORDER BY tool_id, installed_utc DESC")
        return [_row_to_ref(r) for r in rows]

    def installation(self, inst_id: str) -> InstallationRef | None:
        r = self.db.query_one("SELECT * FROM installations WHERE id=?", (inst_id,))
        return _row_to_ref(r) if r else None

    def installation_row(self, inst_id: str):
        return self.db.query_one("SELECT * FROM installations WHERE id=?", (inst_id,))

    def selected(self, tool_id: str) -> InstallationRef | None:
        r = self.db.query_one("SELECT * FROM installations WHERE tool_id=? AND selected=1", (tool_id,))
        return _row_to_ref(r) if r else None

    # -- in-use tracking (jobs) ---------------------------------------------------------
    def acquire(self, inst_id: str) -> None:
        with self._lock:
            self._in_use[inst_id] = self._in_use.get(inst_id, 0) + 1

    def release(self, inst_id: str) -> None:
        with self._lock:
            n = self._in_use.get(inst_id, 0) - 1
            if n <= 0:
                self._in_use.pop(inst_id, None)
            else:
                self._in_use[inst_id] = n

    def in_use(self, inst_id: str) -> bool:
        with self._lock:
            return inst_id in self._in_use

    # -- selection ----------------------------------------------------------------------
    def select(self, inst_id: str) -> None:
        inst = self.installation(inst_id)
        if inst is None:
            raise InstallError("unknown installation")
        if not Path(inst.executable).is_file():
            raise InstallError(f"{inst.executable} no longer exists; re-import this version.")
        with self.db.write() as c:
            c.execute("UPDATE installations SET selected=0 WHERE tool_id=?", (inst.tool_id,))
            c.execute("UPDATE installations SET selected=1, retained=1 WHERE id=?", (inst_id,))

    def rollback(self, tool_id: str) -> InstallationRef | None:
        """Select the most recent retained installation other than the current one."""
        cur = self.selected(tool_id)
        for inst in self.installations(tool_id):
            if cur and inst.id == cur.id:
                continue
            if Path(inst.executable).is_file():
                self.select(inst.id)
                return inst
        return None

    # -- import from archive ------------------------------------------------------------
    def staging_dir(self) -> Path:
        d = self.tools_dir / ".staging" / uuid.uuid4().hex[:12]
        d.mkdir(parents=True, exist_ok=False)
        return d

    def install_archive(self, archive: Path, *, tool_id: str | None = None, source: str = "import_archive", source_detail: str | None = None,
                        select: bool = True, cancel: threading.Event | None = None) -> InstallResult:
        archive = Path(archive)
        result = InstallResult()
        staging = self.staging_dir()
        try:
            safe_extract_zip(archive, staging, cancel=cancel)
            archive_sha = file_sha256(archive)
            if (staging / BUNDLE_MANIFEST).is_file():
                return self._install_bundle(staging, archive_sha, select=select)
            located = self.locate_tools(staging, only=tool_id)
            if not located:
                result.errors.append(f"No catalogue tool with the expected executable layout was found in {archive.name}.")
                return result
            for loc in located:
                try:
                    result.installed.append(self._promote(loc, source=source, source_detail=source_detail or str(archive), archive_sha=archive_sha, select=select))
                except InstallError as exc:
                    result.errors.append(f"{loc.tool.name}: {exc}")
            return result
        except (UnsafeArchive, ExtractionCancelled, OSError, ValueError) as exc:
            result.errors.append(f"{type(exc).__name__}: {exc}")
            return result
        finally:
            remove_tree(staging)

    def import_folder(self, folder: Path, *, select: bool = True, only: set[str] | None = None) -> InstallResult:
        folder = Path(folder)
        result = InstallResult()
        if not folder.is_dir():
            result.errors.append(f"{folder} is not a folder.")
            return result
        if (folder / BUNDLE_MANIFEST).is_file():
            return self._install_bundle(folder, None, select=select)
        located = []
        # Get-ZimmermanTools puts .NET 10 builds in a "net" subfolder and .NET 4 builds at the top.
        for base in (folder / "net", folder):
            if base.is_dir():
                for loc in self.locate_tools(base):
                    if only and loc.tool.id not in only:
                        continue
                    if not any(l.tool.id == loc.tool.id for l in located):
                        located.append(loc)
        if not located:
            result.errors.append("No catalogue tool executables were recognised in that folder.")
        for loc in located:
            try:
                result.installed.append(self._promote(loc, source="import_folder", source_detail=str(folder), archive_sha=None, select=select, copy=True))
            except InstallError as exc:
                result.errors.append(f"{loc.tool.name}: {exc}")
        return result

    def locate_tools(self, base: Path, only: str | None = None) -> list[LocatedTool]:
        """Recognise tools by their catalogue executable names and layouts. Never by guessing
        from display names; unknown executables are ignored."""
        found: list[LocatedTool] = []
        try:
            entries = {p.name.lower(): p for p in base.iterdir() if not is_reparse_or_link(p)}
        except OSError:
            return found
        for tool in self.catalogue.tools.values():
            if only and tool.id != only:
                continue
            if tool.id == "kape":  # referenced in place via register_external, never copied
                continue
            exe_l = tool.executable.lower()
            if tool.archive_subdir:
                sub = entries.get(tool.archive_subdir.lower())
                if sub is not None and sub.is_dir():
                    inner = {p.name.lower(): p for p in sub.iterdir()}
                    if exe_l in inner:
                        found.append(LocatedTool(tool, sub, inner[exe_l], flavor=_flavor(inner[exe_l])))
                        continue
                # some folders (e.g. an operator copy) may hold the subdir's contents directly
                if exe_l in entries and tool.kind == ToolKind.GUI and entries.get(exe_l).stat().st_size > 50_000_000:
                    found.append(LocatedTool(tool, base, entries[exe_l], files=[entries[exe_l]], flavor="net10"))
                continue
            if exe_l in entries and entries[exe_l].is_file():
                stem = tool.executable.rsplit(".", 1)[0].lower()
                files = [p for n, p in entries.items() if p.is_file() and (n.startswith(stem + ".") or n in FLAT_SIDECARS.get(tool.id, ()))]
                found.append(LocatedTool(tool, base, entries[exe_l], files=files, flavor=_flavor(entries[exe_l])))
        return found

    def _install_bundle(self, root: Path, archive_sha: str | None, *, select: bool) -> InstallResult:
        result = InstallResult()
        try:
            manifest = json.loads((root / BUNDLE_MANIFEST).read_text("utf-8"))
        except (OSError, ValueError) as exc:
            result.errors.append(f"Unreadable bundle manifest: {exc}")
            return result
        bad = []
        for f in manifest.get("files", []):
            p = (root / f["path"]).resolve()
            if not str(p).startswith(str(root.resolve())) or not p.is_file() or file_sha256(p) != f.get("sha256"):
                bad.append(f["path"])
        if bad:
            result.errors.append(f"Bundle hash check failed for {len(bad)} file(s): {', '.join(bad[:5])}")
            return result
        for t in manifest.get("tools", []):
            base = root / t["folder"]
            for loc in self.locate_tools(base, only=t["tool_id"]):
                try:
                    result.installed.append(self._promote(loc, source="offline_bundle", source_detail=manifest.get("label", "offline bundle"), archive_sha=archive_sha, select=select, copy=True))
                except InstallError as exc:
                    result.errors.append(f"{loc.tool.name}: {exc}")
        return result

    def _promote(self, loc: LocatedTool, *, source: str, source_detail: str | None, archive_sha: str | None, select: bool, copy: bool = False) -> InstallationRef:
        tool = loc.tool
        if tool.kind != ToolKind.SCRIPT:
            try:
                info = inspect_pe(loc.executable)
            except (OSError, PEFormatError) as exc:
                raise InstallError(f"{loc.executable.name} is not a valid Windows executable ({exc}).") from exc
            assembly = loc.executable.with_suffix(".dll") if tool.managed_assembly else None
            if assembly is not None and not assembly.is_file():
                if loc.flavor == "net10":
                    raise InstallError(f"{assembly.name} is missing next to {loc.executable.name}; the archive is incomplete.")
                assembly = None
            ainfo = inspect_pe(assembly) if assembly else None
            version = (ainfo or info).version
            product_version = (ainfo or info).product_version
            runtime = (ainfo.runtime if ainfo and ainfo.runtime.flavor != "unknown" else info.runtime)
            runtime_json = json.dumps({"flavor": runtime.flavor, "tfm": runtime.tfm, "frameworks": runtime.frameworks, "source": runtime.source})
            arch = (ainfo or info).architecture
        else:
            assembly, version, product_version, arch = None, None, None, "script"
            text = loc.executable.read_text("utf-8-sig", errors="replace")
            for line in text.splitlines()[:80]:
                if "Updated:" in line:
                    version = line.split("Updated:", 1)[1].strip()
                    break
            runtime_json = json.dumps({"flavor": "powershell", "frameworks": [["Windows PowerShell", "5.1"]], "source": "script"})
        exe_sha = file_sha256(loc.executable)
        flavor = "script" if tool.kind == ToolKind.SCRIPT else loc.flavor
        existing = self.db.query_one("SELECT * FROM installations WHERE tool_id=? AND exe_sha256=?", (tool.id, exe_sha))
        if existing is not None and Path(existing["executable"]).is_file():
            if select:
                self.select(existing["id"])
            return _row_to_ref(existing)
        name = f"{version or 'unknown'}-{flavor}-{exe_sha[:8]}".replace(" ", "_").replace(",", "")
        final = self.tools_dir / tool.id / name
        if final.exists():
            remove_tree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        stage = self.tools_dir / ".staging" / f"promote-{uuid.uuid4().hex[:10]}"
        try:
            if loc.files:  # flat layout: copy only this tool's files
                stage.mkdir(parents=True)
                for f in loc.files:
                    shutil.copy2(f, stage / f.name)
            else:
                shutil.copytree(loc.root, stage, symlinks=True)
                for p in stage.rglob("*"):
                    if is_reparse_or_link(p):
                        raise InstallError(f"links are not allowed inside a tool folder ({p.name}).")
            exe_final = final / loc.executable.relative_to(loc.root if not loc.files else loc.executable.parent)
            # validate the expected binary in staging before promotion
            staged_exe = stage / exe_final.relative_to(final)
            if file_sha256(staged_exe) != exe_sha:
                raise InstallError("copied executable does not match the source hash")
            os.replace(stage, final)
        except BaseException:
            remove_tree(stage)
            raise
        asm_final = exe_final.with_suffix(".dll") if assembly else None
        sig = assess_signature(exe_final, tool.expected_signers) if tool.kind != ToolKind.SCRIPT else None
        deps = {}
        for d in ("Maps", "BatchExamples", "Plugins"):
            if (final / d).is_dir():
                inv = inventory(final / d)
                deps[d] = {"file_count": inv["file_count"], "inventory_sha256": inv["inventory_sha256"]}
        inst_id = f"{tool.id}-{(version or 'x').replace(' ', '')}-{exe_sha[:10]}-{uuid.uuid4().hex[:4]}"
        with self.db.write() as c:
            if select:
                c.execute("UPDATE installations SET selected=0 WHERE tool_id=?", (tool.id,))
            c.execute(
                """INSERT INTO installations(id, tool_id, version, product_version, flavor, runtime_json, architecture, install_dir, executable,
                   managed_assembly, exe_sha256, assembly_sha256, signature_state, signature_detail, signature_checked_utc, source, source_detail,
                   archive_sha256, installed_utc, selected, retained, dependencies_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (inst_id, tool.id, version, product_version, flavor, runtime_json, arch, str(final), str(exe_final),
                 str(asm_final) if asm_final else None, exe_sha, file_sha256(asm_final) if asm_final else None,
                 str(sig.state) if sig else "not_checked", (sig.detail + (f" Publisher: {sig.publisher}" if sig.publisher else "")) if sig else "PowerShell script; see review state",
                 sig.checked_utc if sig else None, source, source_detail, archive_sha, utcnow(), 1 if select else 0, 1, json.dumps(deps)),
            )
        self.enforce_retention(tool.id)
        return self.installation(inst_id)

    def register_external(self, tool_id: str, folder: Path) -> InstallationRef:
        """Reference an operator-supplied installation in place (KAPE). It is not copied, so the
        record notes that later changes in that folder are outside the workbench's control."""
        tool = self.catalogue.tools[tool_id]
        exe = Path(folder) / tool.executable
        if not exe.is_file():
            raise InstallError(f"{tool.executable} not found in {folder}.")
        info = inspect_pe(exe)
        exe_sha = file_sha256(exe)
        sig = assess_signature(exe, tool.expected_signers)
        inst_id = f"{tool_id}-ext-{exe_sha[:10]}-{uuid.uuid4().hex[:4]}"
        rt = info.runtime
        with self.db.write() as c:
            c.execute("UPDATE installations SET selected=0 WHERE tool_id=?", (tool_id,))
            c.execute(
                """INSERT INTO installations(id, tool_id, version, product_version, flavor, runtime_json, architecture, install_dir, executable,
                   exe_sha256, signature_state, signature_detail, signature_checked_utc, source, source_detail, installed_utc, selected, retained, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?)""",
                (inst_id, tool_id, info.version, info.product_version, _flavor(exe), json.dumps({"flavor": rt.flavor, "tfm": rt.tfm, "frameworks": rt.frameworks, "source": rt.source}),
                 info.architecture, str(folder), str(exe), exe_sha, str(sig.state), sig.detail, sig.checked_utc, "external_reference", str(folder), utcnow(),
                 "Referenced in place; the executable hash is re-checked before every job."),
            )
        return self.installation(inst_id)

    def recheck_signature(self, inst_id: str) -> None:
        inst = self.installation(inst_id)
        if inst is None:
            return
        tool = self.catalogue.tools.get(inst.tool_id)
        sig = assess_signature(inst.executable, tool.expected_signers if tool else ())
        self.db.execute("UPDATE installations SET signature_state=?, signature_detail=?, signature_checked_utc=? WHERE id=?",
                        (str(sig.state), sig.detail + (f" Publisher: {sig.publisher}" if sig.publisher else ""), sig.checked_utc, inst_id))

    def enforce_retention(self, tool_id: str) -> list[str]:
        """Remove old, unselected, unused installations beyond the retention count."""
        removed = []
        insts = self.installations(tool_id)
        keep = 0
        for inst in insts:
            row = self.installation_row(inst.id)
            if row["selected"] or self.in_use(inst.id):
                continue
            keep += 1
            if keep <= self.retained:
                continue
            remove_tree(Path(inst.install_dir))
            self.db.execute("DELETE FROM installations WHERE id=?", (inst.id,))
            removed.append(inst.id)
        return removed

    def verified_hashes(self, tool_id: str) -> set[str]:
        rows = self.db.query("SELECT exe_sha256 FROM verification_records WHERE tool_id=? AND method='help_capture' AND result='compatible'", (tool_id,))
        return {r[0] for r in rows}


def _flavor(exe: Path) -> str:
    if exe.suffix.lower() == ".ps1":
        return "script"
    if exe.with_suffix(".runtimeconfig.json").is_file():
        return "net10"
    try:
        info = inspect_pe(exe)
    except (OSError, PEFormatError):
        return "unknown"
    if info.runtime.flavor == "dotnet":
        return "net10" if (info.runtime.tfm or "").startswith("net10") or any(v.startswith("10.") for _, v in info.runtime.frameworks) else "dotnet"
    if info.runtime.flavor == "netfx":
        return "net4"
    return "unknown"


def make_offline_bundle(store: ToolStore, dest_zip: Path, tool_ids: list[str], label: str, *, acknowledge_licenses: bool) -> dict:
    """Export selected installations as an offline bundle. The operator must confirm that the
    included tools' licenses permit it; restricted tools (KAPE) are always refused."""
    import zipfile

    if not acknowledge_licenses:
        raise InstallError("Confirm that the licenses of the selected tools permit copying them into an offline bundle.")
    files, tools = [], []
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for tid in tool_ids:
            entry = store.catalogue.tools[tid]
            if entry.redistribution.startswith("restricted"):
                raise InstallError(f"{entry.name} cannot be included in a bundle ({entry.redistribution}).")
            inst = store.selected(tid)
            if inst is None:
                continue
            root = Path(inst.install_dir)
            folder = f"{tid}/{root.name}"
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    arc = f"{folder}/{p.relative_to(root).as_posix()}"
                    z.write(p, arc)
                    files.append({"path": arc, "sha256": file_sha256(p)})
            tools.append({"tool_id": tid, "folder": folder, "version": inst.version, "exe_sha256": inst.exe_sha256})
        manifest = {"schema_version": 1, "label": label, "created_utc": utcnow(), "tools": tools, "files": files,
                    "notice": "Third-party binaries; redistribution subject to each tool's license."}
        z.writestr(BUNDLE_MANIFEST, json.dumps(manifest, indent=1))
    return manifest


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
