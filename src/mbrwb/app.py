"""Application entry point: ``python -m mbrwb`` or the ``mbr-workbench`` script."""
from __future__ import annotations

import argparse
import faulthandler
import logging
import logging.handlers
import os
import sys


def _setup_logging(paths, level: str) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(paths.logs / "workbench.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.addHandler(h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mbr-workbench")
    ap.add_argument("--case", help="open this case folder on start")
    ap.add_argument("--theme", choices=["dark", "light"], help="override the saved theme for this session")
    ap.add_argument("--no-first-run", action="store_true", help="skip the first-run tool setup prompt")
    args = ap.parse_args(argv)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from .config.paths import resolve_paths
    from .config.product import PRODUCT

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(PRODUCT.name)
    app.setOrganizationName(PRODUCT.data_dir_name)
    app.setApplicationVersion(PRODUCT.version)
    paths = resolve_paths()
    paths.ensure()
    try:
        faulthandler.enable(open(paths.logs / "faults.log", "a", encoding="utf-8"))  # noqa: SIM115 - kept open for the process lifetime
    except OSError:
        pass

    from .ui.context import AppContext
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    ctx = AppContext(paths)
    _setup_logging(paths, ctx.settings.diagnostics.log_level)
    logging.getLogger("mbrwb").info("starting %s %s (frozen=%s)", PRODUCT.name, PRODUCT.version, getattr(sys, "frozen", False))
    apply_theme(app, args.theme or ctx.settings.appearance.theme)
    win = MainWindow(ctx)
    win.show()
    if args.case:
        win.open_case_path(args.case)
    elif not args.no_first_run and not os.environ.get("MBRWB_NO_FIRST_RUN"):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, win.first_run)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
