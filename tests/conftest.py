from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own application data root; nothing touches the real profile."""
    home = tmp_path / "wb-home"
    monkeypatch.setenv("MBRWB_HOME", str(home))
    yield home


@pytest.fixture
def fake_host():
    """Run fake tool scripts through this Python interpreter instead of a native apphost.
    Used only for process-management tests; fake output is never evidence that an EZ tool works."""
    from mbrwb.adapters import registry

    def resolver(inst):
        return [sys.executable], inst.executable

    registry.set_host_resolver(resolver)
    yield resolver
    from mbrwb.adapters.base import default_host_resolver

    registry.set_host_resolver(default_host_resolver)
