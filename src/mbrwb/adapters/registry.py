"""Tool id -> adapter class. Only reviewed adapters are registered; a catalogue entry without
an adapter here is launch-only or maintenance-only."""
from __future__ import annotations

from .base import HostResolver, ToolAdapter, default_host_resolver
from .ez_cli import (
    AmcacheParserAdapter,
    AppCompatCacheParserAdapter,
    BstringsAdapter,
    EvtxECmdAdapter,
    IisGeolocateAdapter,
    JLECmdAdapter,
    LECmdAdapter,
    MFTECmdAdapter,
    PECmdAdapter,
    RBCmdAdapter,
    RecentFileCacheParserAdapter,
    RECmdAdapter,
    RLAAdapter,
    SBECmdAdapter,
    SQLECmdAdapter,
    SrumECmdAdapter,
    SumECmdAdapter,
    VSCMountAdapter,
    WxTCmdAdapter,
)
from .kape import KapeAdapter

ADAPTER_CLASSES: dict[str, type[ToolAdapter]] = {
    c.tool_id: c
    for c in (
        AmcacheParserAdapter, AppCompatCacheParserAdapter, BstringsAdapter, EvtxECmdAdapter, IisGeolocateAdapter,
        JLECmdAdapter, LECmdAdapter, MFTECmdAdapter, PECmdAdapter, RBCmdAdapter, RecentFileCacheParserAdapter,
        RECmdAdapter, RLAAdapter, SBECmdAdapter, SQLECmdAdapter, SrumECmdAdapter, SumECmdAdapter, VSCMountAdapter,
        WxTCmdAdapter, KapeAdapter,
    )
}

_host_resolver: HostResolver = default_host_resolver


def set_host_resolver(resolver: HostResolver) -> None:
    """Used by tests (fake tools) and by non-Windows hosts that run framework-dependent
    assemblies through ``dotnet``. Production Windows builds keep the default."""
    global _host_resolver
    _host_resolver = resolver


def get_adapter(tool_id: str) -> ToolAdapter | None:
    cls = ADAPTER_CLASSES.get(tool_id)
    return cls(_host_resolver) if cls else None
