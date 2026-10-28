"""Adapter profiles must only use flags that exist in the inspected official builds."""
from __future__ import annotations

import re

import pytest

from mbrwb.adapters.base import OptionKind
from mbrwb.adapters.registry import ADAPTER_CLASSES, get_adapter
from mbrwb.catalogue.catalogue import load_catalogue
from mbrwb.domain.enums import AdapterVerification, IntegrationLevel

# Flags whose help text sits elsewhere in the #US heap (the extractor pairs them with a
# neighbouring literal) but which were confirmed by targeted string checks recorded in
# TOOL_COVERAGE.md (e.g. "Directory to save CSV formatted results to ... This or --json
# required unless --de or --body is specified" for MFTECmd --csv).
CONFIRMED_ELSEWHERE = {
    "mftecmd": {"--csv", "--json", "--body"},
    "recmd": {"--csv", "--json", "--bn"},
    "sbecmd": {"--csv", "--json"},
    "rla": {"--ca"},
}

EZ_TOOLS = [t for t in ADAPTER_CLASSES if t != "kape"]


@pytest.mark.parametrize("tool_id", EZ_TOOLS)
def test_every_flag_exists_in_static_evidence(tool_id):
    static = load_catalogue().static_evidence[tool_id]
    known = {k.lower() for k in static["static_options"]} | {f.lower() for f in CONFIRMED_ELSEWHERE.get(tool_id, set())}
    adapter = get_adapter(tool_id)
    for prof in adapter.profiles:
        flags = {o.flag for o in prof.options} | {i.flag for i in prof.inputs} | {m.flag for m in prof.managed_outputs}
        missing = sorted(f for f in flags if f.lower() not in known)
        assert not missing, f"{tool_id}: flags not found in official binary strings: {missing}"


@pytest.mark.parametrize("tool_id", EZ_TOOLS)
def test_profile_family_matches_inspected_build(tool_id):
    static = load_catalogue().static_evidence[tool_id]
    adapter = get_adapter(tool_id)
    assert adapter.select_profile(static["version"]) is not None
    assert adapter.select_profile("2024.1.0") is None
    for prof in adapter.profiles:
        assert prof.verification == AdapterVerification.STATIC_STRINGS
        assert "not executed" in prof.evidence


def test_every_catalogue_cli_adapter_is_registered():
    cat = load_catalogue()
    for t in cat.tools.values():
        if t.integration in (IntegrationLevel.CLI_ADAPTER,) or t.id in ("vscmount", "kape"):
            assert t.adapter in ADAPTER_CLASSES, t.id


def test_no_profile_offers_network_or_live_switches_by_default():
    for tool_id, cls in ADAPTER_CLASSES.items():
        for prof in cls().profiles:
            flags = {o.flag for o in prof.options}
            assert "--sync" not in flags, tool_id
            assert "--vss" not in flags, tool_id
            assert "--dt" not in flags, tool_id


def test_dependency_options_resolve_inside_installation_only():
    for cls in ADAPTER_CLASSES.values():
        for prof in cls().profiles:
            for o in prof.options:
                if o.kind == OptionKind.DEPENDENCY:
                    assert o.dependency_glob and not re.match(r"^([A-Za-z]:|/|\\)", o.dependency_glob)


def test_switch_toggles_emit_once_and_value_toggles_emit_value(tmp_path):
    from mbrwb.adapters.base import ValidatedRequest
    from mbrwb.domain.models import EvidenceRef, InstallationRef

    inst = InstallationRef("i", "pecmd", "2026.10.3", None, "net10", str(tmp_path), str(tmp_path / "PECmd.exe"), None, "0" * 64, None, "unavailable")
    a = get_adapter("pecmd")
    prof = a.profiles[0]
    ref = EvidenceRef("e", str(tmp_path), str(tmp_path), "prefetch_directory", is_dir=True)
    v = ValidatedRequest(prof, inst, {"dir": [ref]}, {"json": True, "quiet": True}, [], tmp_path / "job", tmp_path / "job" / "outputs", tmp_path / "job" / "temp")
    args = a.build_invocation(v, create_dirs=False).args
    assert args.count("--json") == 1 and args[args.index("--json") + 1].endswith("json")
    assert not (tmp_path / "job").exists(), "create_dirs=False must not touch the file system"
