"""Closed vocabularies shared by storage, services and UI."""
from __future__ import annotations

from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "Queued"
    PREFLIGHT = "Preflight"
    RUNNING = "Running"
    IMPORTING = "Importing"
    COMPLETED = "Completed"
    PARTIAL = "Partial"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    TIMED_OUT = "Timed out"
    INTERRUPTED = "Interrupted"
    SKIPPED = "Skipped"

    @property
    def is_active(self) -> bool:
        return self in (JobState.QUEUED, JobState.PREFLIGHT, JobState.RUNNING, JobState.IMPORTING)

    @property
    def is_terminal(self) -> bool:
        return not self.is_active


class ErrorCategory(StrEnum):
    INPUT_UNAVAILABLE = "Input unavailable"
    INPUT_MISMATCH = "Input mismatch"
    TOOL_MISSING = "Tool missing"
    RUNTIME_MISSING = "Runtime missing"
    DEPENDENCY_MISSING = "Dependency missing"
    UNSUPPORTED_VERSION = "Unsupported version"
    ACCESS_DENIED = "Access denied"
    PROCESS_FAILURE = "Process failure"
    TIMEOUT = "Timeout"
    CANCELLED = "Cancelled"
    OUTPUT_MISSING = "Output missing"
    UNSUPPORTED_OUTPUT_SCHEMA = "Unsupported output schema"
    IMPORT_FAILURE = "Import failure"
    STORAGE_FAILURE = "Storage failure"


class ParserOutcome(StrEnum):
    SUCCESS = "success"
    SUCCESS_WITH_WARNINGS = "success_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NOT_RUN = "not_run"


class ImportOutcome(StrEnum):
    NOT_APPLICABLE = "not_applicable"  # console-only tools, derived hives
    PENDING = "pending"
    IMPORTED = "imported"
    PARTIAL = "partial"  # some records rejected
    EMPTY = "empty"  # legitimately zero records
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolKind(StrEnum):
    CLI = "cli"
    GUI = "gui"
    SCRIPT = "script"
    BUNDLE = "bundle"  # KAPE: collection/processing framework with its own GUI


class IntegrationLevel(StrEnum):
    CLI_ADAPTER = "cli_adapter"
    GUI_LAUNCH = "gui_launch"
    MAINTENANCE_SCRIPT = "maintenance_script"
    SPECIALIST = "specialist"  # VSCMount, XWFIM, TimeApp
    RESTRICTED_OPTIONAL = "restricted_optional"  # KAPE
    PENDING_REVIEW = "pending_review"  # newly discovered upstream entries


class AdapterVerification(StrEnum):
    VERIFIED_HELP = "verified_help"  # options matched against help output captured from this exact executable hash
    STATIC_STRINGS = "static_strings"  # options found in the official binary of this version family; not executed
    UNVERIFIED = "unverified"
    SYNTHETIC_FIXTURE_ONLY = "synthetic_fixture_only"

    @property
    def label(self) -> str:
        return {
            AdapterVerification.VERIFIED_HELP: "Verified against captured help output",
            AdapterVerification.STATIC_STRINGS: "Options statically verified (binary not executed)",
            AdapterVerification.UNVERIFIED: "Unverified",
            AdapterVerification.SYNTHETIC_FIXTURE_ONLY: "Synthetic test fixture only",
        }[self]


class SignatureState(StrEnum):
    VALID = "valid"
    VALID_REVOCATION_UNCHECKED = "valid_revocation_unchecked"
    UNEXPECTED_PUBLISHER = "unexpected_publisher"
    UNSIGNED = "unsigned"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"  # cannot verify on this platform / API failure
    NOT_CHECKED = "not_checked"


class HashState(StrEnum):
    PENDING = "pending"
    HASHING = "hashing"
    HASHED = "hashed"
    UNAVAILABLE = "unavailable"  # locked, permission denied, vanished
    EXCLUDED = "excluded"  # intentionally not hashed (operator choice / size policy)
    FAILED = "failed"
    CANCELLED = "cancelled"


class Availability(StrEnum):
    AVAILABLE = "available"
    CHANGED = "changed"  # size or hash differs from the registered manifest
    UNAVAILABLE = "unavailable"


class TimestampStatus(StrEnum):
    UTC = "utc"  # explicit Z / +00:00 in the value
    OFFSET = "offset"  # explicit non-zero offset in the value
    TOOL_UTC_CONVENTION = "tool_utc_convention"  # no offset in value; tool's documented convention is UTC
    OPERATOR_ASSUMPTION = "operator_assumption"  # resolved with an operator-supplied source timezone
    UNRESOLVED = "unresolved"  # local-looking value without a verified rule
    AMBIGUOUS = "ambiguous"  # DST fold: two possible instants
    NONEXISTENT = "nonexistent"  # DST gap: no such local time
    INVALID = "invalid"
    ZERO = "zero"  # 0, 1601-01-01, 0001-01-01 and similar "no value" markers
    EMPTY = "empty"

    @property
    def is_resolved(self) -> bool:
        return self in (
            TimestampStatus.UTC,
            TimestampStatus.OFFSET,
            TimestampStatus.TOOL_UTC_CONVENTION,
            TimestampStatus.OPERATOR_ASSUMPTION,
        )


class PlanStepStatus(StrEnum):
    READY = "Ready"
    NEEDS_SETUP = "Needs setup"
    NOT_PRESENT = "Not present"
    NOT_SELECTED = "Not selected"
    NOT_APPLICABLE = "Not applicable"
    BLOCKED = "Blocked"
