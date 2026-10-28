"""Core value objects passed between services.

Plain dataclasses are used inside the process; JSON boundaries (custom workflow files,
settings, exported manifests) are validated with pydantic models in their own modules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .enums import ErrorCategory, ParserOutcome


@dataclass(frozen=True)
class InstallationRef:
    id: str
    tool_id: str
    version: str | None
    product_version: str | None
    flavor: str
    install_dir: str
    executable: str
    managed_assembly: str | None
    exe_sha256: str
    assembly_sha256: str | None
    signature_state: str
    runtime_json: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    path: str  # the path the tool reads: working copy when present, else original
    original_path: str
    artifact_type: str | None
    host: str | None = None
    user_profile: str | None = None
    volume: str | None = None
    size: int | None = None
    sha256: str | None = None
    is_dir: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Invocation:
    """Structured process launch. ``argv`` = ``launcher + [executable] + args`` is what runs.
    There is no shell and no string form that is executed."""

    executable: str
    args: list[str]
    cwd: str
    env_overrides: dict[str, str] = field(default_factory=dict)
    launcher: list[str] = field(default_factory=list)  # e.g. [dotnet] for a framework-dependent .dll
    input_refs: list[dict] = field(default_factory=list)
    output_root: str = ""
    dependency_snapshot: dict = field(default_factory=dict)
    exclusive: bool = False
    requires_elevation: bool = False

    @property
    def argv(self) -> list[str]:
        return [*self.launcher, self.executable, *self.args]

    def display(self) -> str:
        from ..adapters.winargs import display_command

        return display_command(self.argv)


@dataclass
class ProcessResult:
    exit_code: int | None
    started_utc: str
    ended_utc: str
    cancelled: bool = False
    timed_out: bool = False
    launch_error: str | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    pid: int | None = None


@dataclass
class OutputFile:
    path: Path
    rel_path: str
    kind: str  # csv | json | jsonl | xml | html | body | text | log | hive | binary
    role: str
    schema_hint: str | None = None
    importable: bool = False
    size: int = 0


@dataclass
class CompletionClassification:
    parser_outcome: ParserOutcome
    reason: str
    warnings: list[str] = field(default_factory=list)
    expected_output_observations: list[str] = field(default_factory=list)
    error_category: ErrorCategory | None = None
    next_action: str = ""
    legitimately_empty: bool = False

    def as_dict(self) -> dict:
        return {
            "parser_outcome": str(self.parser_outcome),
            "reason": self.reason,
            "warnings": self.warnings,
            "expected_output_observations": self.expected_output_observations,
            "error_category": str(self.error_category) if self.error_category else None,
            "next_action": self.next_action,
            "legitimately_empty": self.legitimately_empty,
        }
