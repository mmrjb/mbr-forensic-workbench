from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from ..widgets.common import EmptyState, PageHeader, button


class Page(QWidget):
    """Header + content area + a 'no case' empty state for case-scoped pages."""

    case_scoped = True

    def __init__(self, ctx, win, title: str, subtitle: str = ""):
        super().__init__()
        self.ctx, self.win = ctx, win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 16)
        outer.setSpacing(10)
        self.header = PageHeader(title, subtitle)
        outer.addWidget(self.header)
        self.switch = QStackedWidget()
        outer.addWidget(self.switch, 1)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        self.no_case = EmptyState("No case open", "This view shows data from a case. Create a new case or open an existing one.",
                                  [button("Create a case", icon_name="plus", primary=True, on_click=win.new_case),
                                   button("Open an existing case", icon_name="folder", on_click=win.open_case_dialog)], "cases")
        self.switch.addWidget(self.body)
        self.switch.addWidget(self.no_case)

    def on_case_changed(self) -> None:
        if self.case_scoped:
            self.switch.setCurrentWidget(self.body if self.ctx.case else self.no_case)
        if self.isVisible():
            self.refresh()

    def on_show(self) -> None:
        if self.case_scoped:
            self.switch.setCurrentWidget(self.body if self.ctx.case else self.no_case)
        self.refresh()

    def refresh(self) -> None:
        pass
