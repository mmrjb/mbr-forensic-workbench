"""Structured, user-facing errors: a category, a short explanation, a next action, and
separate diagnostics that the UI shows only in an expandable panel."""
from __future__ import annotations

from dataclasses import dataclass, field

from .enums import ErrorCategory


@dataclass
class Problem:
    category: ErrorCategory
    message: str
    next_action: str = ""
    diagnostics: str = ""
    field: str | None = None  # form field the problem belongs to, if any
    blocking: bool = True

    def as_dict(self) -> dict:
        return {
            "category": str(self.category),
            "message": self.message,
            "next_action": self.next_action,
            "diagnostics": self.diagnostics,
            "field": self.field,
            "blocking": self.blocking,
        }


class WorkbenchError(Exception):
    """Exception carrying a :class:`Problem`."""

    def __init__(self, problem: Problem):
        super().__init__(problem.message)
        self.problem = problem


@dataclass
class Preflight:
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.blocking for p in self.problems)

    def add(self, category: ErrorCategory, message: str, next_action: str = "", *, field: str | None = None,
            blocking: bool = True, diagnostics: str = "") -> None:
        self.problems.append(Problem(category, message, next_action, diagnostics, field, blocking))

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if not p.blocking]

    @property
    def blockers(self) -> list[Problem]:
        return [p for p in self.problems if p.blocking]
