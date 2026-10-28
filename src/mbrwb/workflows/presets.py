"""Versioned, declarative workflow presets and custom workflow configuration.

A preset references registered tool ids and validated options only. Custom workflows are
imported as data and validated with pydantic: unknown tools, unknown option keys and any
attempt to set managed inputs/outputs are rejected. No script execution exists in this model.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..adapters.registry import ADAPTER_CLASSES


class StepSpec(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_.-]{1,60}$")
    tool_id: str
    title: str
    why: str
    input_mode: Literal["file", "dir", "group"] = "group"
    artifact_types: list[str]
    options: dict[str, str | bool | int] = Field(default_factory=dict)
    related: dict[str, str] = Field(default_factory=dict)  # input role -> relation name (e.g. mft: same_volume_mft)
    optional: bool = False

    @field_validator("tool_id")
    @classmethod
    def _known_tool(cls, v: str) -> str:
        if v not in ADAPTER_CLASSES or v in ("vscmount", "kape"):
            raise ValueError(f"{v!r} is not a registered parser adapter usable in workflows")
        return v

    @field_validator("options")
    @classmethod
    def _known_options(cls, v: dict, info) -> dict:
        tool = info.data.get("tool_id")
        if tool in ADAPTER_CLASSES:
            prof = ADAPTER_CLASSES[tool]().profiles[0]
            keys = {o.key for o in prof.options}
            bad = [k for k in v if k not in keys]
            if bad:
                raise ValueError(f"unknown option(s) for {tool}: {bad}")
        return v


class WorkflowSpec(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9_.-]{1,60}$")
    version: int = Field(ge=1)
    title: str
    question: str
    steps: list[StepSpec]


def _s(key, tool, title, why, types, mode="group", **kw) -> StepSpec:
    return StepSpec(key=key, tool_id=tool, title=title, why=why, artifact_types=types, input_mode=mode, **kw)


PECMD = lambda k="prefetch": _s(k, "pecmd", "Prefetch", "Prefetch records program names and recent run times (execution evidence when Prefetch is enabled).", ["prefetch_file"], options={"mp": True, "quiet": True})
AMCACHE = lambda: _s("amcache", "amcacheparser", "Amcache", "Amcache inventories files and programs that were present; entries are not proof of execution.", ["amcache_hive"], "file", options={"include_files": True, "mp": True})
SHIMCACHE = lambda: _s("shimcache", "appcompatcacheparser", "ShimCache (AppCompatCache)", "ShimCache lists files the OS evaluated; its timestamps are file modification times.", ["system_hive"], "file")
EVTX = lambda: _s("evtx", "evtxecmd", "Event logs", "Event logs record logons, process creation and service activity where auditing was enabled.", ["evtx_file"], options={"maps": True})
MFT = lambda: _s("mft", "mftecmd", "$MFT", "The $MFT lists files and their file system timestamps on one volume.", ["ntfs_mft"], "file", options={"csv": True})
USNJ = lambda: _s("usnj", "mftecmd", "$UsnJrnl:$J", "The change journal records file creations, renames and deletions on a volume.", ["ntfs_usnjrnl_j"], "file", options={"csv": True}, related={"mft": "same_volume_mft"}, optional=True)
LNK = lambda: _s("lnk", "lecmd", "Shortcuts (LNK)", "Shortcuts record files and folders a user opened, with target metadata.", ["lnk_file"], options={"mp": True, "quiet": True})
JL = lambda: _s("jumplists", "jlecmd", "Jump Lists", "Jump Lists record recently used items per application.", ["jumplist_file"], options={"mp": True, "quiet": True})
SB = lambda: _s("shellbags", "sbecmd", "ShellBags", "ShellBags record folder view state; timestamps are registry/shell-item times.", ["usrclass_hive", "ntuser_hive"], "group")
RB = lambda: _s("recyclebin", "rbcmd", "Recycle Bin", "$I records give the original path and deletion time.", ["recyclebin_i_file", "recyclebin_info2"], options={"quiet": True})
RECMD = lambda: _s("registry_batch", "recmd", "Registry batch", "RECmd's batch file extracts many investigator-relevant registry keys at once.", ["system_hive", "software_hive", "sam_hive", "ntuser_hive", "usrclass_hive", "registry_hive"], "group", options={"batch": "BatchExamples/Kroll_Batch.reb"})
SRUM = lambda: _s("srum", "srumecmd", "SRUM", "SRUM records per-application resource and network usage over time.", ["srum_db"], "file", related={"software": "software_hive"})
WXT = lambda: _s("wintimeline", "wxtcmd", "Windows Timeline", "ActivitiesCache.db records application activity per user.", ["activitiescache_db"], "file", optional=True)
SQLE = lambda: _s("sqlite", "sqlecmd", "SQLite application artifacts", "SQLECmd maps known application databases (browsers, OS components) to tables.", ["sqlite_db"], "group", options={"maps": True})
RFC = lambda: _s("recentfilecache", "recentfilecacheparser", "RecentFileCache", "RecentFileCache.bcf (Windows 7 era) lists program paths; presence only.", ["recentfilecache_bcf"], "file", optional=True)

PRESETS: list[WorkflowSpec] = [
    WorkflowSpec(id="quick_triage", version=1, title="Quick Windows Triage", question="What ran, what changed and who logged on, from the most common artifacts?",
                 steps=[PECMD(), AMCACHE(), SHIMCACHE(), EVTX(), MFT(), LNK(), JL()]),
    WorkflowSpec(id="program_presence_execution", version=1, title="Program Presence & Execution Evidence",
                 question="Which programs were present, and which artifacts actually record them running?",
                 steps=[PECMD(), AMCACHE(), SHIMCACHE(), RFC(), EVTX()]),
    WorkflowSpec(id="file_folder_activity", version=1, title="File and Folder Activity", question="Which files and folders were created, changed, opened or deleted?",
                 steps=[MFT(), USNJ(), LNK(), JL(), SB(), RB()]),
    WorkflowSpec(id="user_activity", version=1, title="User Activity", question="What did each user open, browse and run?",
                 steps=[LNK(), JL(), SB(), WXT(), RB()]),
    WorkflowSpec(id="registry_analysis", version=1, title="Registry Analysis", question="What do the system and user hives record about configuration, devices and use?",
                 steps=[RECMD(), SHIMCACHE(), AMCACHE()]),
    WorkflowSpec(id="windows_event_logs", version=1, title="Windows Event Logs", question="What did the event logs record about logons, processes and services?",
                 steps=[EVTX()]),
    WorkflowSpec(id="srum_resource_usage", version=1, title="SRUM and Application Resource Usage", question="Which applications used network and resources, and when?",
                 steps=[SRUM()]),
    WorkflowSpec(id="sqlite_app_artifacts", version=1, title="SQLite Application Artifacts", question="What do application databases (browsers, Windows components) contain?",
                 steps=[SQLE(), WXT()]),
]

PRESET_BY_ID = {p.id: p for p in PRESETS}

CUSTOM_TEMPLATE = {
    "schema_version": 1, "id": "my_workflow", "version": 1, "title": "Custom Workflow", "question": "Describe the investigation question.",
    "steps": [{"key": "prefetch", "tool_id": "pecmd", "title": "Prefetch", "why": "Execution evidence", "input_mode": "group",
               "artifact_types": ["prefetch_file"], "options": {"mp": True}}],
}


def load_custom(data: dict) -> WorkflowSpec:
    """Validate an imported custom workflow (data, never code)."""
    return WorkflowSpec.model_validate(data)
