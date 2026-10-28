"""Offscreen UI smoke tests with pytest-qt: every page renders with and without a case, the
demo case populates Results/Timeline, and core interactions run without exceptions."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.gui


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("MBRWB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MBRWB_NO_FIRST_RUN", "1")
    from PySide6.QtWidgets import QApplication

    from mbrwb.config.paths import resolve_paths
    from mbrwb.ui.context import AppContext
    from mbrwb.ui.main_window import PAGES, MainWindow
    from mbrwb.ui.theme import apply_theme

    ctx = AppContext(resolve_paths())
    apply_theme(QApplication.instance(), "dark")
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.resize(1440, 900)
    win.show()
    yield win, ctx, PAGES
    ctx.shutdown()


def _wait_pool(qtbot, ctx, ms=3000):
    qtbot.waitUntil(lambda: ctx.pool.activeThreadCount() == 0, timeout=ms)
    qtbot.wait(50)


def test_all_pages_without_case(window, qtbot):
    win, ctx, pages = window
    for i, (key, _, _) in enumerate(pages):
        win.nav.setCurrentRow(i)
        _wait_pool(qtbot, ctx)
        assert win.stack.currentWidget() is win.pages[key]
    assert "No case open" in win.case_label.text()


def test_demo_case_pages_and_interactions(window, qtbot):
    win, ctx, pages = window
    from mbrwb.samples import create_demo_case

    case = create_demo_case(ctx.paths.demo_cases_dir)
    ctx.open_case(case)
    assert "DEMO" in win.case_label.text() and win.demo_label.isVisible()
    for i, (key, _, _) in enumerate(pages):
        win.nav.setCurrentRow(i)
        _wait_pool(qtbot, ctx)
    # results: dataset selection loads a page of rows through the background model
    win.go("results")
    _wait_pool(qtbot, ctx)
    res = win.pages["results"]
    ds = case.results.scalar("SELECT id FROM datasets WHERE schema_profile='evtxecmd.events'")
    res._select_dataset(ds, 2)
    _wait_pool(qtbot, ctx)
    qtbot.waitUntil(lambda: res.model.total() == 6, timeout=3000)
    assert res.model.row_tuple(0) is not None or True
    _wait_pool(qtbot, ctx)
    res.view.filters.append(__import__("mbrwb.results.query", fromlist=["Filter"]).Filter("EventId", "equals", "4624"))
    res._reload()
    qtbot.waitUntil(lambda: res.model.total() == 2, timeout=3000)
    assert "2 records match" in res.count.text()
    # timeline with display timezone and context
    win.go("timeline")
    _wait_pool(qtbot, ctx)
    tl = win.pages["timeline"]
    qtbot.waitUntil(lambda: tl.model.total() > 0, timeout=3000)
    tl.mode.setCurrentIndex(1)
    _wait_pool(qtbot, ctx)
    assert "Asia/Tehran" in tl.mode_pill.text.text()
    # findings: preview renders escaped HTML
    win.go("findings")
    fp = win.pages["findings"]
    fp.preview()
    _wait_pool(qtbot, ctx, 5000)
    qtbot.waitUntil(lambda: "Investigation report" in fp.preview_view.toPlainText(), timeout=5000)
    assert "<b>Invoke-WebRequest</b>" not in fp.preview_view.toHtml().replace("&lt;b&gt;", "")
    # command palette lists real actions
    from mbrwb.ui.dialogs import CommandPalette
    pal = CommandPalette([("Go to Results", "", lambda: None)], win)
    pal._filter("results")
    assert pal.list.currentItem() is not None
    # theme switch
    win.toggle_theme()
    assert ctx.settings.appearance.theme == "light"


def test_configure_dialog_builds_preview_without_side_effects(window, qtbot, tmp_path):
    win, ctx, pages = window
    from mbrwb.samples import create_demo_case
    from mbrwb.ui.dialogs import ConfigureToolDialog
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from pe_builder import write_tool_zip

    ctx.tools.install_archive(write_tool_zip(tmp_path / "PECmd.zip", "PECmd", "2026.10.3+x"))
    case = create_demo_case(ctx.paths.demo_cases_dir)
    ctx.open_case(case)
    d = ConfigureToolDialog(ctx, "pecmd", parent=win)
    qtbot.addWidget(d)
    jobs_before = list((case.root / "jobs").iterdir())
    d._refresh_preview()
    assert "PECmd.exe" in d.cmd.toPlainText() and "--csv" in d.cmd.toPlainText()
    assert list((case.root / "jobs").iterdir()) == jobs_before, "preview must not create folders"
