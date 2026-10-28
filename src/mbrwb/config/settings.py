"""Operator settings: typed, validated, atomically persisted.

* Writes go to a temp file in the same directory, are fsynced and then ``os.replace``d.
* An unreadable or invalid file is moved aside (``settings.invalid-<ts>.json``) and
  defaults are used; the UI shows a notice instead of crashing.
* Exported settings never contain secrets (there are none in v1) and mark every path as
  machine-specific so an import on another machine treats it as *unresolved*.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from .paths import AppPaths


class Appearance(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    density: Literal["comfortable", "compact"] = "comfortable"
    monospace_paths: bool = True


class ToolStorage(BaseModel):
    tools_dir: str | None = None  # None -> AppPaths.default_tools_dir
    retained_versions: int = Field(2, ge=1, le=10)
    preferred_flavor: Literal["net10", "net4"] = "net10"


class CaseDefaults(BaseModel):
    cases_dir: str | None = None
    examiner: str = ""
    display_timezone: str = "UTC"
    evidence_mode: Literal["in_place", "working_copy"] = "in_place"
    hash_on_register: bool = True
    hash_size_limit_mb: int = Field(0, ge=0)  # 0 = hash everything

    @field_validator("display_timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {v!r}") from exc
        return v


class Execution(BaseModel):
    max_parallel_jobs: int = Field(2, ge=1, le=16)
    default_timeout_minutes: int = Field(240, ge=1, le=7 * 24 * 60)
    log_preview_lines: int = Field(2000, ge=100, le=50000)
    search_index_row_limit: int = Field(1_500_000, ge=0)


class Network(BaseModel):
    offline_mode: bool = False  # True disables every workbench-initiated network request
    download_timeout_s: int = Field(60, ge=5, le=600)
    max_download_mb: int = Field(512, ge=1, le=4096)


class Diagnostics(BaseModel):
    log_level: Literal["DEBUG", "INFO", "WARNING"] = "INFO"


class Settings(BaseModel):
    schema_version: int = 1
    first_run_completed: bool = False
    appearance: Appearance = Appearance()
    tool_storage: ToolStorage = ToolStorage()
    case_defaults: CaseDefaults = CaseDefaults()
    execution: Execution = Execution()
    network: Network = Network()
    diagnostics: Diagnostics = Diagnostics()
    recent_paths: dict[str, str] = Field(default_factory=dict)
    column_layouts: dict[str, dict] = Field(default_factory=dict)


_PATH_FIELDS = [("tool_storage", "tools_dir"), ("case_defaults", "cases_dir")]


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SettingsStore:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self.notice: str | None = None
        self.settings = self._load()

    def _load(self) -> Settings:
        f = self.paths.settings_file
        if not f.exists():
            return Settings()
        try:
            return Settings.model_validate(json.loads(f.read_text("utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            aside = f.with_name(f"settings.invalid-{stamp}.json")
            try:
                os.replace(f, aside)
                where = f" The unreadable file was kept as {aside.name}."
            except OSError:
                where = ""
            self.notice = f"Settings could not be read ({type(exc).__name__}); defaults are in use.{where}"
            return Settings()

    def save(self) -> None:
        atomic_write_text(self.paths.settings_file, self.settings.model_dump_json(indent=2))

    def update(self, **sections) -> Settings:
        """Validate a partial update as a whole before persisting it."""
        data = self.settings.model_dump()
        for k, v in sections.items():
            if isinstance(v, BaseModel):
                v = v.model_dump()
            if isinstance(data.get(k), dict) and isinstance(v, dict):
                data[k].update(v)
            else:
                data[k] = v
        self.settings = Settings.model_validate(data)
        self.save()
        return self.settings

    # -- derived locations ------------------------------------------------------------
    @property
    def tools_dir(self) -> Path:
        return Path(self.settings.tool_storage.tools_dir) if self.settings.tool_storage.tools_dir else self.paths.default_tools_dir

    @property
    def cases_dir(self) -> Path:
        return Path(self.settings.case_defaults.cases_dir) if self.settings.case_defaults.cases_dir else self.paths.default_cases_dir

    def remember_path(self, key: str, path: str | Path) -> None:
        self.settings.recent_paths[key] = str(path)
        self.save()

    # -- export / import ----------------------------------------------------------------
    def export_dict(self) -> dict:
        data = self.settings.model_dump()
        data.pop("recent_paths", None)
        data["_exported_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        data["_machine_specific_paths"] = [f"{a}.{b}" for a, b in _PATH_FIELDS]
        return data

    def preview_import(self, data: dict) -> tuple[Settings, list[str]]:
        """Return the settings that *would* apply plus human-readable notes. Paths from the
        imported file are dropped (left unresolved) and listed so the operator can re-select
        them on this machine; nothing is persisted here."""
        notes: list[str] = []
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        for section, key in _PATH_FIELDS:
            val = (clean.get(section) or {}).get(key)
            if val:
                notes.append(f"{section}.{key} = {val!r} came from another machine and was left unresolved; choose a local folder.")
                clean[section][key] = getattr(getattr(self.settings, section), key)
        clean.setdefault("first_run_completed", self.settings.first_run_completed)
        merged = Settings.model_validate({**self.settings.model_dump(), **clean})
        return merged, notes

    def apply_import(self, merged: Settings) -> None:
        self.settings = merged
        self.save()
