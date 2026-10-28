"""Product identity. The display name is configurable (``product.json`` next to the
executable / package, or the ``MBRWB_PRODUCT_NAME`` environment variable) so the
owner can rebrand without code changes. The internal data-directory name is stable."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import __version__


@dataclass(frozen=True)
class Product:
    name: str
    short_name: str
    data_dir_name: str
    version: str
    disclaimer: str


_DEFAULT = Product(
    name="MBR Forensic Workbench",
    short_name="MBR Workbench",
    data_dir_name="MBR Forensic Workbench",
    version=__version__,
    disclaimer=(
        "Independent investigation workbench that runs and opens Eric Zimmerman's tools. "
        "It is not an official Eric Zimmerman or Kroll product."
    ),
)


def _candidate_files() -> list[Path]:
    out = []
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).with_name("product.json"))
    out.append(Path(__file__).resolve().parents[1] / "product.json")
    return out


def load_product() -> Product:
    data: dict = {}
    for f in _candidate_files():
        if f.is_file():
            try:
                data = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                data = {}
            break
    name = os.environ.get("MBRWB_PRODUCT_NAME") or data.get("name") or _DEFAULT.name
    return Product(
        name=name,
        short_name=data.get("short_name") or (name if len(name) <= 20 else _DEFAULT.short_name),
        data_dir_name=_DEFAULT.data_dir_name,
        version=__version__,
        disclaimer=_DEFAULT.disclaimer,
    )


PRODUCT = load_product()
