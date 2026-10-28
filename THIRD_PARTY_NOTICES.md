# Third-party notices

MBR Forensic Workbench is an independent project. It is **not** an official Eric Zimmerman, ZimTech LLC or Kroll product.

## Runtime dependencies (bundled in the Windows package)

| Component | Version (pinned) | License | Source |
|---|---|---|---|
| Python | 3.12.x | PSF License | https://www.python.org/ |
| Qt for Python (PySide6, shiboken6) | 6.10.1 | LGPL-3.0-only / GPL (open-source editions); commercial license also offered by The Qt Company | https://doc.qt.io/qtforpython-6/ |
| Qt 6 libraries (QtCore, QtGui, QtWidgets, QtSvg) | 6.10.1 | LGPL-3.0 | https://www.qt.io/ |
| pydantic / pydantic-core | 2.14.1 / 2.50.1 | MIT | https://github.com/pydantic/pydantic |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| tzdata (Windows only) | 2025.2 | Apache-2.0 | https://github.com/python/tzdata |

LGPL notes for Qt/PySide6: the Windows package is built in PyInstaller **one-folder** mode, so the Qt and PySide6 shared libraries remain separate, replaceable DLLs. Only open-source Qt modules are used (Core, Gui, Widgets, Svg); no commercial-only add-on module is imported. Source for Qt and PySide6 is available from the URLs above. Licenses were checked against the package metadata available on the build host (EPEL/EL10 builds of PySide6 6.10.1 and Qt 6.10.1); re-check the exact wheels used on the Windows build machine before distribution.

## Build/test-only dependencies (not shipped)

| Component | Version | License |
|---|---|---|
| pytest | 9.1.1 | MIT |
| pytest-qt | 4.5.0 | MIT |
| PyInstaller | 6.16.0 | GPL-2.0 with bootloader exception |

## Icons and fonts
The SVG icons in `src/mbrwb/ui/icons` were drawn for this project from simple geometric primitives and are covered by the project's own license (see `src/mbrwb/ui/icons/LICENSE.txt`). No font files are bundled; the UI uses Segoe UI / Cascadia Mono / Consolas from Windows when present. (The Linux screenshot environment used Noto Sans, Noto Sans Arabic and Noto Sans Mono from the distribution; they are not part of the package.)

## Eric Zimmerman's tools and KAPE (not bundled)
- The EZ Tools are downloaded by the operator from https://download.ericzimmermanstools.com/ (or imported from a local copy). The workbench does not redistribute them. Per-repository license pages were unreachable from the build host on 2026-10-28; the catalogue therefore records redistribution as *unknown* and the workbench never bundles or republishes the binaries. Official binaries observed on 2026-10-28 are Authenticode-signed by **ZimTech LLC** (Sectigo Public Code Signing CA R36 chain).
- **KAPE**: separately obtained from Kroll under its own terms. KapeDocs FAQ (fetched 2026-10-28): free for government agencies and for educational, research and internal company use; since 1 January 2026 not available for commercial use (third-party networks or paid engagements). The KapeFiles repository license does not cover the KAPE binary. The workbench never bundles, downloads or exports KAPE.
- Reference copies of the KapeDocs pages used to check KAPE switches are kept in `docs/upstream/` with their SHA-256 values (data only).

## Source/attribution links
- EZ Tools catalogue: https://ericzimmerman.github.io/
- Documentation index: https://ericzimmerman.github.io/documentation/
- Repositories: https://github.com/EricZimmerman
- Get-ZimmermanTools: https://github.com/EricZimmerman/Get-ZimmermanTools
- KapeFiles / KapeDocs: https://github.com/EricZimmerman/KapeFiles, https://ericzimmerman.github.io/KapeDocs/
