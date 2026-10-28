"""Per-tool readiness, combining catalogue, installation, runtime, signature and adapter state.

Readiness is never "Ready" unless the selected executable exists *now*, its runtime is present
and (for CLI tools) a reviewed adapter profile matches the installed version.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.registry import get_adapter
from ..catalogue.catalogue import Catalogue, ToolEntry
from ..catalogue.pe import RuntimeRequirement
from ..domain.enums import AdapterVerification, IntegrationLevel, SignatureState, ToolKind
from ..domain.models import InstallationRef
from .runtime import RuntimeInventory, assess
from .store import ToolStore


@dataclass
class ToolStatus:
    tool: ToolEntry
    installation: InstallationRef | None
    readiness: str  # ready | launch_only | needs_setup | unsupported_version | manual_import | pending_review | not_on_this_os
    label: str
    level: str  # ok | warn | error | muted | info
    primary_action: str  # configure | open | set_up | manage
    details: list[str] = field(default_factory=list)
    runtime_ok: bool | None = None
    runtime_message: str = ""
    signature: str = "not_checked"
    verification: str = ""
    other_versions: int = 0


def _runtime_req(inst: InstallationRef) -> RuntimeRequirement | None:
    if not inst.runtime_json:
        return None
    rj = json.loads(inst.runtime_json)
    return RuntimeRequirement(rj.get("flavor", "unknown"), [tuple(x) for x in rj.get("frameworks", [])], rj.get("tfm"), rj.get("source", ""))


def tool_status(cat: Catalogue, store: ToolStore, tool_id: str, runtimes: RuntimeInventory) -> ToolStatus:
    tool = cat.tools[tool_id]
    inst = store.selected(tool_id)
    count = len(store.installations(tool_id))
    if inst is None:
        if tool.integration == IntegrationLevel.RESTRICTED_OPTIONAL:
            return ToolStatus(tool, None, "manual_import", "Optional - use your own copy", "muted", "set_up",
                              ["Not bundled or downloaded. Point the workbench at your licensed KAPE folder."])
        if tool.integration == IntegrationLevel.PENDING_REVIEW:
            return ToolStatus(tool, None, "pending_review", "New upstream entry - integration pending review", "muted", "manage")
        return ToolStatus(tool, None, "needs_setup", "Not installed", "warn", "set_up",
                          ["Import an existing EZ Tools folder or archive, or download it from the official source."], other_versions=count)
    details: list[str] = []
    level, readiness, label = "ok", "ready", "Ready"
    primary = "configure" if tool.kind == ToolKind.CLI else ("open" if tool.kind == ToolKind.GUI else "manage")
    if not Path(inst.executable).is_file():
        return ToolStatus(tool, inst, "needs_setup", "Executable missing", "error", "set_up", [f"{inst.executable} no longer exists."], other_versions=count)
    req = _runtime_req(inst)
    rt_ok, rt_msg = None, ""
    if req is not None and req.flavor in ("dotnet", "netfx"):
        a = assess(req, runtimes, min_netfx="4.8" if tool.id == "timeapp" else "4.7.2")
        rt_ok = a.ok
        rt_msg = ("Runtime present: " + ", ".join(a.satisfied_by)) if a.ok else ("Missing runtime: " + ", ".join(a.missing) + ". " + " ".join(a.guidance))
        if not a.ok:
            level, readiness, label = "error", "needs_setup", "Runtime missing"
            details.append(rt_msg)
    sig = inst.signature_state
    if sig in (SignatureState.INVALID.value, SignatureState.UNSIGNED.value, SignatureState.UNEXPECTED_PUBLISHER.value):
        level, readiness, label = "error", "needs_setup", {"invalid": "Invalid signature", "unsigned": "Unsigned binary", "unexpected_publisher": "Unexpected publisher"}[sig]
        details.append("The executable's Authenticode state needs review before use.")
    verification = ""
    if tool.kind == ToolKind.CLI or tool.id == "kape":
        adapter = get_adapter(tool.adapter or tool.id)
        if adapter is not None:
            caps = adapter.describe_capabilities(inst, store.verified_hashes(tool.id))
            verification = caps.verification.label
            if not caps.compatible:
                if readiness == "ready":
                    level, readiness, label = "warn", "unsupported_version", "Version not reviewed"
                details.append(caps.message)
            elif caps.verification != AdapterVerification.VERIFIED_HELP and readiness == "ready":
                label = "Ready (options statically verified)" if caps.verification == AdapterVerification.STATIC_STRINGS else "Ready (unverified adapter)"
    if tool.kind == ToolKind.GUI or tool.integration == IntegrationLevel.SPECIALIST and tool.kind == ToolKind.GUI:
        if readiness == "ready":
            readiness, label = "launch_only", "Ready to open"
        if sys.platform != "win32":
            details.append("Windows program: it can only be opened on Windows.")
    if sys.platform != "win32" and readiness in ("ready", "launch_only"):
        readiness, level, label = "not_on_this_os", "warn", "Installed - runs on Windows"
    return ToolStatus(tool, inst, readiness, label, level, primary, details, rt_ok, rt_msg, sig, verification, count)


def all_statuses(cat: Catalogue, store: ToolStore, runtimes: RuntimeInventory) -> list[ToolStatus]:
    return [tool_status(cat, store, tid, runtimes) for tid in cat.tools]
