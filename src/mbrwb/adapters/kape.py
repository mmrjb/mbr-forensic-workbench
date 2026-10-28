"""Optional KAPE integration (operator's own installation).

* Target (.tkape) and Module (.mkape) files are read with ``yaml.safe_load`` as data; nothing
  in them is executed by the workbench. KAPE itself runs the selected modules.
* Before a processing run, every selected module's processors (executable + command line) are
  shown for review, and missing module binaries are reported using KAPE's documented lookup
  order: ``Modules\\<ModuleName>\\<exe>`` then ``Modules\\bin\\<exe>``.
* Switches used here were checked against KapeDocs "Using KAPE" (docs/upstream). There is no
  KAPE binary on the build host, so the adapter is "implemented, unverified against a real
  KAPE executable".
* Licensing (KapeDocs FAQ, fetched 2026-10-28): free for government, educational, research
  and internal company use; not available for commercial use (third-party networks or paid
  engagements) since 2026-01-01. The UI repeats this before the first run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..domain.enums import AdapterVerification, ErrorCategory
from ..domain.errors import Preflight
from .base import AdapterProfile, InputRole, ManagedOutput, OptionKind as K, OptionSpec as O, OutputPattern as P, Request, ToolAdapter

KAPE_LICENSE_NOTICE = (
    "KAPE is free for government agencies and for educational, research and internal company use. "
    "Since 1 January 2026 it is not available for commercial use (third-party networks or paid engagements). "
    "Confirm your use is permitted before running it."
)


@dataclass
class KapeDefinition:
    name: str
    path: str
    kind: str  # target | module
    category: str = ""
    description: str = ""
    processors: list[dict] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)  # for compound targets
    error: str | None = None


def _read_defs(root: Path, suffix: str, kind: str, limit: int = 20000) -> list[KapeDefinition]:
    out: list[KapeDefinition] = []
    if not root.is_dir():
        return out
    for i, p in enumerate(sorted(root.rglob(f"*{suffix}"))):
        if i >= limit or p.is_symlink():
            break
        d = KapeDefinition(p.stem, str(p), kind)
        try:
            if p.stat().st_size > 1_000_000:
                raise ValueError("definition larger than 1 MB")
            doc = yaml.safe_load(p.read_text("utf-8-sig", errors="replace")) or {}
            if not isinstance(doc, dict):
                raise ValueError("not a mapping")
            d.description = str(doc.get("Description", ""))[:500]
            d.category = str(doc.get("Category", ""))[:100]
            if kind == "module":
                for proc in doc.get("Processors") or []:
                    if isinstance(proc, dict):
                        d.processors.append({k: str(proc.get(k, "")) for k in ("Executable", "CommandLine", "ExportFormat")})
            else:
                for t in doc.get("Targets") or []:
                    if isinstance(t, dict) and str(t.get("Path", "")).lower().endswith(".tkape"):
                        d.targets.append(str(t.get("Path")))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            d.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        out.append(d)
    return out


@dataclass
class KapeInstallation:
    root: Path
    executable: Path | None
    gui_executable: Path | None
    targets: list[KapeDefinition]
    modules: list[KapeDefinition]

    @classmethod
    def inspect(cls, root: str | Path) -> "KapeInstallation":
        root = Path(root)
        exe = root / "kape.exe"
        gui = root / "gkape.exe"
        return cls(
            root=root,
            executable=exe if exe.is_file() else None,
            gui_executable=gui if gui.is_file() else None,
            targets=_read_defs(root / "Targets", ".tkape", "target"),
            modules=_read_defs(root / "Modules", ".mkape", "module"),
        )

    def module_dependencies(self, module_names: list[str]) -> list[dict]:
        """Resolve each selected module's processor executables the way KAPE documents it."""
        by_name = {m.name.lower(): m for m in self.modules}
        rows = []
        for name in module_names:
            m = by_name.get(name.lower())
            if m is None:
                rows.append({"module": name, "executable": None, "status": "module not found", "path": None})
                continue
            for proc in m.processors or [{"Executable": "", "CommandLine": ""}]:
                exe = proc.get("Executable", "")
                if not exe:
                    rows.append({"module": name, "executable": None, "status": "no processor", "path": None, "command_line": ""})
                    continue
                if os.path.isabs(exe):
                    cand = [Path(exe)]
                else:
                    cand = [self.root / "Modules" / m.name / exe, self.root / "Modules" / "bin" / exe]
                found = next((c for c in cand if c.is_file()), None)
                rows.append({"module": name, "executable": exe, "status": "present" if found else "missing",
                             "path": str(found) if found else None, "command_line": proc.get("CommandLine", ""),
                             "export_format": proc.get("ExportFormat", "")})
        return rows


NAME_LIST = r"[A-Za-z0-9_.!$ -]+(,[A-Za-z0-9_.!$ -]+)*"


class KapeAdapter(ToolAdapter):
    """Collection (Targets) or processing (Modules) of collected files. Live collection needs
    administrator rights and is marked exclusive."""

    tool_id = "kape"
    profiles = (
        AdapterProfile(
            "kape.docs-2026", "kape", r".*", AdapterVerification.UNVERIFIED,
            "Switches checked against KapeDocs 'Using KAPE' (fetched 2026-10-28). No KAPE executable was available to verify help output.",
            inputs=(
                InputRole("tsource", "--tsource", "Collection source (drive or folder)", True, ("live_volume", "mounted_evidence", "collection_root", "any_directory"), group="source",
                          help="Target source drive to copy files from (C, D:, or F:\\ for example) or a directory"),
                InputRole("msource", "--msource", "Processing source folder", True, ("collection_root", "any_directory"), group="source",
                          help="Directory containing files to process"),
            ),
            options=(
                O("target", "--target", K.TEXT, "Targets", "Target configuration(s) to use, comma separated", pattern=NAME_LIST, pattern_hint="comma separated Target names"),
                O("module", "--module", K.TEXT, "Modules", "Module configuration(s) to use, comma separated", pattern=NAME_LIST, pattern_hint="comma separated Module names"),
                O("tdd", "--tdd", K.BOOL, "Deduplicate collected files", "Deduplicate files from --tsource (and VSCs) based on SHA-1", advanced=True, choices=("true", "false")),
                O("mef", "--mef", K.CHOICE, "Module export format", "Export format for modules (csv, html, json)", advanced=True, choices=("csv", "html", "json")),
                O("debug", "--debug", K.SWITCH, "Debug messages", "Show debug information during processing", advanced=True),
                O("trace", "--trace", K.SWITCH, "Trace messages", "Show trace information during processing", advanced=True),
            ),
            managed_outputs=(
                ManagedOutput("tdest", "--tdest", "kape_collection", "Collection destination", option="target"),
                ManagedOutput("mdest", "--mdest", "kape_modules", "Module output destination", option="module"),
            ),
            output_patterns=(P("*ConsoleLog*.txt", "log", "console_log"), P("*_CopyLog.csv", "csv", "kape.copylog", None, True),
                             P("kape_modules/*.csv", "csv", "kape.module_csv", None, True), P("kape_modules/**/*.csv", "csv", "kape.module_csv", None, True),
                             P("kape_collection/*", "collection", "kape.collected_file"), P("kape_collection/**/*", "collection", "kape.collected_file")),
            not_offered=("--vss", "--sync", "--tflush", "--mflush", "--zip", "--vhd", "--vhdx", "--scs", "--s3p", "--asu"),
            notes=(KAPE_LICENSE_NOTICE,
                   "--tflush/--mflush (delete destination) are never passed: destinations are fresh job folders.",
                   "Remote upload, container and VSS switches are not offered."),
            exclusive=True,
        ),
    )

    def classify_completion(self, profile, result, outputs):
        """KAPE Targets runs produce copied files rather than parser tables; Modules runs produce
        module outputs. Either counts as output; neither present with exit 0 is Output missing."""
        from ..domain.enums import ParserOutcome
        from ..domain.models import CompletionClassification
        base = super().classify_completion(profile, result, outputs)
        if base.error_category != ErrorCategory.OUTPUT_MISSING:
            return base
        produced = [o for o in outputs if o.role in ("kape.collected_file", "kape.module_csv")]
        if produced:
            return CompletionClassification(ParserOutcome.SUCCESS_WITH_WARNINGS if base.warnings else ParserOutcome.SUCCESS,
                                            f"KAPE finished: {sum(1 for o in produced if o.role == 'kape.collected_file')} collected file(s), "
                                            f"{sum(1 for o in produced if o.role == 'kape.module_csv')} module CSV output(s).",
                                            base.warnings, base.expected_output_observations[:200])
        return base

    def validate_specific(self, profile, request: Request, pf: Preflight):
        o = request.options
        if not (o.get("target") or o.get("module")):
            pf.add(ErrorCategory.INPUT_MISMATCH, "Choose Targets (collection) and/or Modules (processing).", "Enter at least one Target or Module.", field="target")
        if o.get("target") and not request.inputs.get("tsource"):
            pf.add(ErrorCategory.INPUT_MISMATCH, "Targets need a collection source.", "Select the drive or folder to collect from.", field="tsource")
        if o.get("module") and not o.get("target") and not request.inputs.get("msource"):
            pf.add(ErrorCategory.INPUT_MISMATCH, "Modules need a processing source folder (or Targets in the same run).", "Select the folder to process.", field="msource")
        if o.get("target") and request.inputs.get("msource"):
            pf.add(ErrorCategory.INPUT_MISMATCH, "When Targets run in the same job, KAPE processes the collection destination; clear the separate processing source.",
                   "Clear the processing source.", field="msource")
