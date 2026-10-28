"""Get-ZimmermanTools as a separately identified, optional maintenance operation.

Reviewed facts (copy dated "Updated: October 23, 2026", SHA-256 below, read 2026-10-28):
* parameters: ``-Dest``, ``-NetVersion`` (0, 4, 9, 10), ``-Proxy``/``-ProxyCredential``/
  ``-ProxyUseDefaultCredentials``, ``-Sync``, ``-NoUpdate``;
* without ``-NoUpdate`` the script replaces itself with a newer download and stops, which
  would run unreviewed code next time; the workbench therefore always passes ``-NoUpdate``;
* ``-Sync`` runs EvtxECmd/SQLECmd/RECmd ``--sync`` (network updates of maps); never passed.

Execution uses Windows PowerShell with ``-NoProfile -NonInteractive -File <reviewed copy>``.
No ``-ExecutionPolicy Bypass``, no ``Invoke-Expression``, no download-and-execute. If the
local execution policy blocks signed scripts the job fails with that message; the
application does not depend on this helper (its own download/import flow is independent).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..domain.enums import AdapterVerification, ErrorCategory
from ..domain.errors import Preflight
from ..domain.models import Invocation

REVIEWED_SCRIPT_SHA256 = {
    "2cbf18d4e9abe26d41cdcc0f0c22add212e87f132f606b5acce7b504462889b4": "Get-ZimmermanTools.ps1 'Updated: October 23, 2026' (reviewed 2026-10-28)",
}
VERIFICATION = AdapterVerification.STATIC_STRINGS  # parameters read from the script source; not executed on the build host


def powershell_path() -> Path:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def script_review_state(script: Path, approved_hashes: set[str] | None = None) -> tuple[str, str]:
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if digest in REVIEWED_SCRIPT_SHA256:
        return "reviewed", REVIEWED_SCRIPT_SHA256[digest]
    if approved_hashes and digest in approved_hashes:
        return "operator_approved", f"Operator-approved script hash {digest[:16]}..."
    return "unreviewed", f"Script hash {digest} is not a reviewed version. Review its content before approving it."


def build_get_zimmermantools(script: Path, dest: Path, net_version: str = "10", approved_hashes: set[str] | None = None) -> tuple[Invocation | None, Preflight]:
    pf = Preflight()
    if os.name != "nt":
        pf.add(ErrorCategory.RUNTIME_MISSING, "Get-ZimmermanTools needs Windows PowerShell.", "Run this maintenance action on Windows.")
    ps = powershell_path()
    if os.name == "nt" and not ps.is_file():
        pf.add(ErrorCategory.RUNTIME_MISSING, f"Windows PowerShell not found at {ps}.", "Repair Windows PowerShell or use the workbench's own download/import.")
    if not script.is_file():
        pf.add(ErrorCategory.TOOL_MISSING, "No local reviewed copy of Get-ZimmermanTools.ps1 is installed.", "Import Get-ZimmermanTools.zip on the Tools page.")
        return None, pf
    state, detail = script_review_state(script, approved_hashes)
    if state == "unreviewed":
        pf.add(ErrorCategory.UNSUPPORTED_VERSION, "This Get-ZimmermanTools.ps1 version has not been reviewed.", "Open 'Review script', read it, and approve its hash.", diagnostics=detail)
    if net_version not in ("0", "4", "10"):
        pf.add(ErrorCategory.INPUT_MISMATCH, "NetVersion must be 0, 4 or 10.", "Choose a .NET flavor.")
    dest.mkdir(parents=True, exist_ok=True)
    inv = Invocation(
        executable=str(ps),
        args=["-NoProfile", "-NonInteractive", "-File", str(script), "-Dest", str(dest), "-NetVersion", net_version, "-NoUpdate"],
        cwd=str(script.parent),
        output_root=str(dest),
        dependency_snapshot={"script": {"path": str(script), "review": detail}},
    )
    return inv, pf
