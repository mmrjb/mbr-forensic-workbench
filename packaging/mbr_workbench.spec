# PyInstaller spec - one-folder Windows build of MBR Forensic Workbench.
# Build on Windows with packaging/build_windows.ps1 (Python 3.12 x64, pinned requirements).
# Third-party forensic binaries (EZ Tools, KAPE) are NOT included.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = [
    (str(SRC / "mbrwb" / "catalogue" / "data"), "mbrwb/catalogue/data"),
    (str(SRC / "mbrwb" / "timeline" / "mappings"), "mbrwb/timeline/mappings"),
    (str(SRC / "mbrwb" / "ui" / "icons"), "mbrwb/ui/icons"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "USER_GUIDE.md"), "."),
]

excluded_qt = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtNfc", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtHttpServer", "PySide6.QtGraphs",
]

a = Analysis(
    [str(SRC / "mbrwb" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "yaml", "zoneinfo", "tzdata"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "pytestqt", *excluded_qt],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MBR Forensic Workbench",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MBR Forensic Workbench")
