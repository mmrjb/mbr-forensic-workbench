"""Adapter contract for command-line tools.

An adapter turns a *validated request* (evidence inputs + typed option values) into a
structured :class:`~mbrwb.domain.models.Invocation` for one tool *version profile*, and
later classifies the finished process and discovers its outputs.

Rules enforced here for every adapter:
* option flags come only from the profile (each one statically checked against the official
  binary for that version family - see ``tests/test_adapter_profiles.py``);
* managed destinations (``--csv``, ``--json``, ``--out`` ...) are set by the workbench to the
  job's output directory and cannot be overridden by the operator or by extra arguments;
* evidence and tool paths are passed as separate argv elements, never concatenated into a
  command string, so spaces, Unicode, ``$``, ``&``, ``(`` ``)`` and quotes survive intact;
* nothing is passed through a shell.
"""
from __future__ import annotations

import fnmatch
import hashlib
import ntpath
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from ..domain.enums import AdapterVerification, ErrorCategory, ParserOutcome
from ..domain.errors import Preflight
from ..domain.models import (
    CompletionClassification,
    EvidenceRef,
    InstallationRef,
    Invocation,
    OutputFile,
    ProcessResult,
)
from .winargs import split_windows_args


class OptionKind(StrEnum):
    SWITCH = "switch"  # flag alone means true; omitted means the tool default
    BOOL = "bool"  # flag followed by true/false; omitted means the tool default
    TEXT = "text"
    INT = "int"
    CHOICE = "choice"
    FILE = "file"  # analyst-supplied configuration file (not evidence)
    DEPENDENCY = "dependency"  # file/folder resolved from the trusted tool installation


@dataclass(frozen=True)
class OptionSpec:
    key: str
    flag: str
    kind: OptionKind
    label: str
    help: str
    advanced: bool = False
    default: Any = None  # value the form starts with; None = do not pass the flag
    tool_default: str = ""  # documented tool behaviour when the flag is omitted (display only)
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    pattern: str | None = None
    pattern_hint: str = ""
    depends_on: tuple[str, ...] = ()  # other option keys that must be set
    conflicts: tuple[str, ...] = ()
    dependency_glob: str | None = None  # for DEPENDENCY: glob relative to install dir


@dataclass(frozen=True)
class InputRole:
    key: str
    flag: str
    label: str
    is_dir: bool
    artifact_types: tuple[str, ...]  # accepted evidence artifact types ("*" = any)
    required: bool = False
    group: str | None = None  # roles in the same group are mutually exclusive alternatives
    multiple: bool = False
    help: str = ""


@dataclass(frozen=True)
class ManagedOutput:
    """An output destination the workbench owns. ``option`` (if set) makes it conditional on
    that option being enabled; otherwise it is always passed."""

    key: str
    flag: str
    subdir: str  # relative to the job output root; "" = the root itself
    label: str
    option: str | None = None
    kind: str = "dir"  # dir | file
    file_name: str | None = None


@dataclass(frozen=True)
class OutputPattern:
    glob: str
    kind: str
    role: str
    schema_hint: str | None = None
    importable: bool = False


@dataclass(frozen=True)
class AdapterProfile:
    profile_id: str
    tool_id: str
    version_family: str  # regex matched against the installed display version
    verification: AdapterVerification
    evidence: str  # what the verification is based on
    inputs: tuple[InputRole, ...]
    options: tuple[OptionSpec, ...]
    managed_outputs: tuple[ManagedOutput, ...]
    output_patterns: tuple[OutputPattern, ...]
    exit_semantics: str = "Exit code semantics not documented in the inspected build: non-zero is treated as failure; zero is checked against expected outputs and console warnings."
    not_offered: tuple[str, ...] = ()  # flags deliberately not exposed, with reasons in ``notes``
    notes: tuple[str, ...] = ()
    console_only: bool = False
    exclusive: bool = False
    requires_elevation: bool = False

    def option(self, key: str) -> OptionSpec:
        for o in self.options:
            if o.key == key:
                return o
        raise KeyError(key)

    def managed_flags(self) -> set[str]:
        return {m.flag.lower() for m in self.managed_outputs} | {i.flag.lower() for i in self.inputs}

    def matches_version(self, version: str | None) -> bool:
        return bool(version) and re.fullmatch(self.version_family, version) is not None


@dataclass
class Capabilities:
    tool_id: str
    profile: AdapterProfile | None
    compatible: bool
    message: str
    verification: AdapterVerification


@dataclass
class ValidatedRequest:
    profile: AdapterProfile
    installation: InstallationRef
    inputs: dict[str, list[EvidenceRef]]
    options: dict[str, Any]
    extra_args: list[str]
    job_dir: Path
    output_root: Path
    temp_dir: Path
    dependencies: dict[str, str] = field(default_factory=dict)  # option key -> resolved absolute path


@dataclass
class Request:
    """Unvalidated request as collected from a form or a workflow step."""

    inputs: dict[str, list[EvidenceRef]]
    options: dict[str, Any]
    extra_args_text: str = ""


HostResolver = Callable[[InstallationRef], tuple[list[str], str]]


def default_host_resolver(inst: InstallationRef) -> tuple[list[str], str]:
    """Return (launcher prefix, program) for an installation.

    Windows runs the tool's own apphost ``X.exe``. Elsewhere a framework-dependent ``X.dll``
    could be run through ``dotnet``; that path exists for completeness and is reported as
    "not a Windows validation" wherever results are shown.
    """
    return [], inst.executable


class ToolAdapter:
    """Base adapter. Subclasses provide ``profiles`` and may refine validation/classification."""

    tool_id: str = ""
    profiles: tuple[AdapterProfile, ...] = ()
    #: console phrases meaning "nothing to process" (legitimately empty result)
    empty_result_patterns: tuple[str, ...] = (r"\bno files found\b", r"\bfound 0 files\b", r"\b0 files? found\b")
    #: console phrases that indicate per-record problems even with exit code 0
    warning_patterns: tuple[str, ...] = (
        r"\berror\b",
        r"\bunable to\b",
        r"\bfailed\b",
        r"\bexception\b",
        r"\bcorrupt",
        r"\bdamaged\b",
        r"\bskipping\b",
    )

    def __init__(self, host_resolver: HostResolver = default_host_resolver):
        self.host_resolver = host_resolver

    # -- capability -------------------------------------------------------------------
    def select_profile(self, version: str | None) -> AdapterProfile | None:
        for p in self.profiles:
            if p.matches_version(version):
                return p
        return None

    def describe_capabilities(self, inst: InstallationRef | None, verified_hashes: set[str] | None = None) -> Capabilities:
        if inst is None:
            p = self.profiles[0] if self.profiles else None
            return Capabilities(self.tool_id, p, False, "Tool not installed. Import or download it first.", AdapterVerification.UNVERIFIED)
        prof = self.select_profile(inst.version)
        if prof is None:
            fams = ", ".join(p.version_family for p in self.profiles) or "none"
            return Capabilities(
                self.tool_id,
                None,
                False,
                f"Installed version {inst.version or 'unknown'} does not match a reviewed adapter profile (supported families: {fams}). "
                "The tool and its help remain available; use 'Verify this version' to compare its help output with the closest profile.",
                AdapterVerification.UNVERIFIED,
            )
        verification = prof.verification
        msg = f"Profile {prof.profile_id}: {verification.label}."
        if verified_hashes and inst.exe_sha256 in verified_hashes:
            verification = AdapterVerification.VERIFIED_HELP
            msg = f"Profile {prof.profile_id}: options matched help output captured from this exact executable."
        return Capabilities(self.tool_id, prof, True, msg, verification)

    # -- validation -------------------------------------------------------------------
    def validate_inputs(self, profile: AdapterProfile, request: Request) -> Preflight:
        pf = Preflight()
        groups: dict[str, list[InputRole]] = {}
        for role in profile.inputs:
            if role.group:
                groups.setdefault(role.group, []).append(role)
        for role in profile.inputs:
            refs = request.inputs.get(role.key) or []
            if role.required and not role.group and not refs:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{role.label} is required.", f"Select {role.label.lower()}.", field=role.key)
            if len(refs) > 1 and not role.multiple:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{role.label} accepts one item; {len(refs)} were selected.",
                       "Select a single item or use the directory input.", field=role.key)
            for ref in refs:
                if not os.path.exists(ref.path):
                    pf.add(ErrorCategory.INPUT_UNAVAILABLE, f"{ref.path} is not accessible.",
                           "Reconnect the evidence location or remap the evidence path.", field=role.key)
                    continue
                if role.is_dir and not os.path.isdir(ref.path):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{role.label} must be a folder: {ref.path}", "Choose a folder.", field=role.key)
                if not role.is_dir and os.path.isdir(ref.path):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{role.label} must be a file: {ref.path}", "Choose a file.", field=role.key)
                if "*" not in role.artifact_types and ref.artifact_type and ref.artifact_type not in role.artifact_types:
                    pf.add(ErrorCategory.INPUT_MISMATCH,
                           f"{Path(ref.path).name} is registered as '{ref.artifact_type}', which {role.label.lower()} does not accept.",
                           "Correct the artifact type on the Evidence page if the suggestion was wrong, or choose another input.",
                           field=role.key, blocking=False)
                _path_length_check(pf, ref.path, role.key)
        for gname, roles in groups.items():
            chosen = [r for r in roles if request.inputs.get(r.key)]
            required = any(r.required for r in roles)
            if len(chosen) > 1:
                pf.add(ErrorCategory.INPUT_MISMATCH,
                       "Choose only one of: " + " / ".join(r.label for r in roles) + ".",
                       "Clear one of the inputs.", field=chosen[0].key)
            elif not chosen and required:
                pf.add(ErrorCategory.INPUT_MISMATCH,
                       "One of these inputs is required: " + " / ".join(r.label for r in roles) + ".",
                       "Select a file or a folder to process.", field=roles[0].key)
        self._validate_options(profile, request, pf)
        self.validate_specific(profile, request, pf)
        return pf

    def validate_specific(self, profile: AdapterProfile, request: Request, pf: Preflight) -> None:
        """Tool-specific cross-field rules. Override in subclasses."""

    def _validate_options(self, profile: AdapterProfile, request: Request, pf: Preflight) -> None:
        values = request.options
        known = {o.key for o in profile.options} | {m.option for m in profile.managed_outputs if m.option}
        for k in values:
            if k not in known:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"Unknown option '{k}' for {profile.profile_id}.", "Remove it.", field=k)
        for opt in profile.options:
            v = values.get(opt.key)
            if _is_unset(v):
                continue
            if opt.kind == OptionKind.INT:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} must be a whole number.", "Enter a number.", field=opt.key)
                    continue
                if opt.minimum is not None and iv < opt.minimum or opt.maximum is not None and iv > opt.maximum:
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} must be between {opt.minimum} and {opt.maximum}.",
                           "Enter a value in range.", field=opt.key)
            if opt.kind == OptionKind.CHOICE and str(v) not in opt.choices:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} must be one of {', '.join(opt.choices)}.", "Pick a listed value.", field=opt.key)
            if opt.kind in (OptionKind.TEXT,) and opt.pattern and not re.fullmatch(opt.pattern, str(v)):
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label}: {opt.pattern_hint or 'invalid format'}.", "Correct the value.", field=opt.key)
            if opt.kind == OptionKind.FILE and not os.path.isfile(str(v)):
                pf.add(ErrorCategory.INPUT_UNAVAILABLE, f"{opt.label}: file not found ({v}).", "Choose an existing file.", field=opt.key)
            if isinstance(v, str) and ("\x00" in v or "\n" in v or "\r" in v):
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} contains control characters.", "Remove line breaks.", field=opt.key)
            for dep in opt.depends_on:
                if _is_unset(values.get(dep)) and not self._managed_enabled(profile, dep, values):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} requires {self._label(profile, dep)}.",
                           f"Enable {self._label(profile, dep)} or clear {opt.label}.", field=opt.key)
            for other in opt.conflicts:
                if not _is_unset(values.get(other)):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{opt.label} cannot be combined with {self._label(profile, other)}.",
                           "Clear one of them.", field=opt.key)
        if request.extra_args_text.strip():
            self.parse_extra_args(profile, request.extra_args_text, pf)

    def _managed_enabled(self, profile: AdapterProfile, key: str, values: dict) -> bool:
        for m in profile.managed_outputs:
            if m.key == key:
                return m.option is None or bool(values.get(m.option))
        return False

    def _label(self, profile: AdapterProfile, key: str) -> str:
        for o in profile.options:
            if o.key == key:
                return o.label
        for m in profile.managed_outputs:
            if m.key == key or m.option == key:
                return m.label
        return key

    def parse_extra_args(self, profile: AdapterProfile, text: str, pf: Preflight) -> list[str]:
        """Extra arguments are tokenised with Windows rules and accepted only if every flag is a
        modelled, non-managed option of this profile."""
        tokens = split_windows_args(text)
        allowed = {o.flag.lower(): o for o in profile.options}
        managed = profile.managed_flags()
        blocked = {f.lower() for f in profile.not_offered}
        out: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            tl = t.lower()
            if tl in managed:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{t} is managed by the workbench (inputs and output destinations).",
                       "Use the form fields for inputs and outputs.", field="extra_args")
                return []
            if tl in blocked:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{t} is intentionally not offered for this workflow.",
                       "See the adapter notes for the reason.", field="extra_args")
                return []
            opt = allowed.get(tl)
            if opt is None:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{t} is not a recognised option of this tool version.",
                       "Check the verified help output for valid options.", field="extra_args")
                return []
            out.append(opt.flag)
            if opt.kind == OptionKind.SWITCH:
                if i + 1 < len(tokens) and tokens[i + 1].lower() in ("true", "false"):
                    out.append(tokens[i + 1].lower())
                    i += 1
            elif opt.kind == OptionKind.DEPENDENCY:
                pf.add(ErrorCategory.INPUT_MISMATCH, f"{t} must be set through the form so its file is hashed and recorded.",
                       "Use the form field.", field="extra_args")
                return []
            else:
                if i + 1 >= len(tokens):
                    pf.add(ErrorCategory.INPUT_MISMATCH, f"{t} requires a value.", "Add the value after the flag.", field="extra_args")
                    return []
                out.append(tokens[i + 1])
                i += 1
            i += 1
        return out

    # -- dependencies -----------------------------------------------------------------
    def resolve_dependencies(self, inst: InstallationRef, profile: AdapterProfile, options: dict) -> tuple[dict[str, str], dict, Preflight]:
        """Resolve maps/batch files/plugins from the trusted installation directory and hash
        them into a dependency snapshot. Never resolves anything from evidence locations."""
        pf = Preflight()
        resolved: dict[str, str] = {}
        snapshot: dict = {}
        root = Path(inst.install_dir).resolve()
        for opt in profile.options:
            if opt.kind != OptionKind.DEPENDENCY:
                continue
            v = options.get(opt.key)
            if _is_unset(v):
                continue
            if v is True:  # "use the installation's default folder"
                target = (root / (opt.dependency_glob or "")).resolve()
            else:
                target = (root / str(v)).resolve()
            if not _is_within(target, root):
                pf.add(ErrorCategory.DEPENDENCY_MISSING, f"{opt.label} must come from the tool installation folder.",
                       "Choose a file shipped with the selected tool version.", field=opt.key)
                continue
            if not target.exists():
                pf.add(ErrorCategory.DEPENDENCY_MISSING, f"{opt.label} not found: {target}",
                       "Re-import the complete tool archive (maps, batch files and plugins included).", field=opt.key)
                continue
            resolved[opt.key] = str(target)
            snapshot[opt.key] = inventory(target)
        return resolved, snapshot, pf

    # -- invocation -------------------------------------------------------------------
    def build_invocation(self, req: ValidatedRequest, *, create_dirs: bool = True) -> Invocation:
        """``create_dirs=False`` builds the same argument array for display (command preview)
        without touching the file system."""
        prof = req.profile
        args: list[str] = []
        for role in prof.inputs:
            for ref in req.inputs.get(role.key) or []:
                args += [role.flag, os.path.abspath(ref.path)]
        for m in prof.managed_outputs:
            if m.option is not None and not req.options.get(m.option):
                continue
            dest = req.output_root / m.subdir if m.subdir else req.output_root
            dest = Path(os.path.normpath(dest))
            if create_dirs:
                dest.mkdir(parents=True, exist_ok=True)
            if m.kind == "file":
                dest = dest / (m.file_name or f"{prof.tool_id}_output.txt")
            args += [m.flag, str(dest)]
        toggles = {m.option for m in prof.managed_outputs if m.option}
        for opt in prof.options:
            v = req.options.get(opt.key)
            if _is_unset(v) or (opt.key in toggles and opt.kind == OptionKind.SWITCH):
                # a switch that only enables a managed output emits nothing itself (the managed output
                # emits the flag with its destination); value options such as KAPE --target still emit
                continue
            if opt.kind == OptionKind.SWITCH:
                if v is True or str(v).lower() == "true":
                    args.append(opt.flag)
            elif opt.kind == OptionKind.BOOL:
                args += [opt.flag, "true" if (v is True or str(v).lower() == "true") else "false"]
            elif opt.kind == OptionKind.DEPENDENCY:
                args += [opt.flag, req.dependencies[opt.key]]
            elif opt.kind == OptionKind.FILE:
                args += [opt.flag, os.path.abspath(str(v))]
            else:
                args += [opt.flag, str(v)]
        args += req.extra_args
        args = self.finalize_args(req, args)
        launcher, program = self.host_resolver(req.installation)
        return Invocation(
            executable=program,
            args=args,
            cwd=req.installation.install_dir,
            env_overrides={"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "TMP": str(req.temp_dir), "TEMP": str(req.temp_dir)},
            launcher=launcher,
            input_refs=[{"role": k, **r.as_dict()} for k, refs in req.inputs.items() for r in refs],
            output_root=str(req.output_root),
            dependency_snapshot={"dependencies": self._dep_snapshot(req)},
            exclusive=prof.exclusive,
            requires_elevation=prof.requires_elevation,
        )

    def finalize_args(self, req: ValidatedRequest, args: list[str]) -> list[str]:
        return args

    def _dep_snapshot(self, req: ValidatedRequest) -> dict:
        return {k: {"path": v} for k, v in req.dependencies.items()}

    # -- completion -------------------------------------------------------------------
    def discover_outputs(self, profile: AdapterProfile, output_root: Path) -> list[OutputFile]:
        found: list[OutputFile] = []
        if not output_root.exists():
            return found
        for p in sorted(output_root.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(output_root).as_posix()
            for pat in profile.output_patterns:
                if fnmatch.fnmatch(p.name, pat.glob) or fnmatch.fnmatch(rel, pat.glob):
                    found.append(OutputFile(p, rel, pat.kind, pat.role, pat.schema_hint, pat.importable, p.stat().st_size))
                    break
            else:
                found.append(OutputFile(p, rel, _kind_from_suffix(p), "unrecognised", None, False, p.stat().st_size))
        return found

    def classify_completion(self, profile: AdapterProfile, result: ProcessResult, outputs: list[OutputFile]) -> CompletionClassification:
        if result.launch_error:
            cat = ErrorCategory.ACCESS_DENIED if "denied" in result.launch_error.lower() else ErrorCategory.PROCESS_FAILURE
            return CompletionClassification(ParserOutcome.FAILED, f"The tool could not be started: {result.launch_error}",
                                            error_category=cat, next_action="Check the tool installation and runtime on the Tools page.")
        if result.cancelled:
            return CompletionClassification(ParserOutcome.CANCELLED, "Cancelled by the operator; partial files were quarantined.",
                                            error_category=ErrorCategory.CANCELLED)
        if result.timed_out:
            return CompletionClassification(ParserOutcome.TIMED_OUT, "The job exceeded its time limit and the process tree was stopped.",
                                            error_category=ErrorCategory.TIMEOUT, next_action="Increase the timeout in Settings > Execution or narrow the input.")
        console = "\n".join(result.stdout_tail + result.stderr_tail)
        warnings = self._warnings(console)
        observations = [f"{o.rel_path} ({o.kind}, {o.size} bytes)" for o in outputs]
        if result.exit_code not in (0, None):
            return CompletionClassification(
                ParserOutcome.FAILED,
                f"Exit code {result.exit_code}. {profile.exit_semantics}",
                warnings, observations, ErrorCategory.PROCESS_FAILURE, "Open the Logs tab to read the tool's message.",
            )
        expected = [o for o in outputs if o.importable or o.kind in ("hive", "text") and o.role != "console_log"]
        if profile.console_only:
            return CompletionClassification(ParserOutcome.SUCCESS_WITH_WARNINGS if warnings else ParserOutcome.SUCCESS,
                                            "Console-only operation; the captured log is the output.", warnings, observations)
        if not expected:
            if any(re.search(p, console, re.I) for p in self.empty_result_patterns):
                return CompletionClassification(ParserOutcome.SUCCESS, "The tool reported nothing to process in the selected input.",
                                                warnings, observations, legitimately_empty=True)
            return CompletionClassification(ParserOutcome.FAILED, "Exit code 0 but none of the expected output files were written.",
                                            warnings, observations, ErrorCategory.OUTPUT_MISSING,
                                            "Inspect the job log; the input may not contain supported artifacts.")
        if warnings:
            return CompletionClassification(ParserOutcome.SUCCESS_WITH_WARNINGS,
                                            "Exit code 0, but the console reported problems (see warnings). Usable records were kept.",
                                            warnings, observations)
        return CompletionClassification(ParserOutcome.SUCCESS, "Exit code 0 and expected outputs present.", [], observations)

    def _warnings(self, console: str) -> list[str]:
        out = []
        for line in console.splitlines():
            if any(re.search(p, line, re.I) for p in self.warning_patterns):
                s = line.strip()
                if s and s not in out:
                    out.append(s[:400])
            if len(out) >= 50:
                break
        return out


# -- helpers ----------------------------------------------------------------------------

def _is_unset(v: Any) -> bool:
    """None, empty text and an unticked switch mean "do not pass the flag". BOOL options carry
    the strings "true"/"false" so an explicit false is never confused with "unset"."""
    return v is None or v == "" or v is False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_length_check(pf: Preflight, path: str, field: str) -> None:
    if os.name == "nt" or ntpath.splitdrive(path)[0]:
        if len(path) >= 260 and not path.startswith("\\\\?\\"):
            pf.add(ErrorCategory.INPUT_MISMATCH,
                   f"Path is {len(path)} characters long; some tools fail on paths of 260 characters or more unless Windows long-path support is enabled.",
                   "Use a working copy in a shorter case folder, or enable LongPathsEnabled.", field=field, blocking=False)


def _kind_from_suffix(p: Path) -> str:
    s = p.suffix.lower()
    return {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".xml": "xml", ".html": "html", ".htm": "html",
            ".txt": "text", ".log": "log", ".body": "body"}.get(s, "binary")


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def inventory(target: Path) -> dict:
    """Hash a dependency file or every file below a dependency folder."""
    if target.is_file():
        return {"type": "file", "path": str(target), "sha256": file_sha256(target), "size": target.stat().st_size}
    files = []
    total = hashlib.sha256()
    for p in sorted(target.rglob("*")):
        if p.is_file():
            d = file_sha256(p)
            rel = p.relative_to(target).as_posix()
            files.append({"rel": rel, "sha256": d, "size": p.stat().st_size})
            total.update(f"{rel}\0{d}\n".encode())
    return {"type": "folder", "path": str(target), "file_count": len(files), "inventory_sha256": total.hexdigest(), "files": files}
