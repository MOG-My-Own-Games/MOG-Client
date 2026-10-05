"""The Logs tab: everything the client logged, coloured by level and filtered by it."""

from __future__ import annotations

import html

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from mog_client import logstore
from mog_client.config import data_dir

COLORS = {"info": "#e8eaed", "warning": "#e8c547", "error": "#e2574c"}
LABELS = {"info": "Info", "warning": "Warning", "error": "Error"}
SCROLL_STEP = 60  # pixels per stick tick at full tilt


class LogView(QWidget):
    appended = Signal(object)  # a logstore.Record, from whichever thread logged it

    def __init__(self) -> None:
        super().__init__()
        self.filters: dict[str, QPushButton] = {}
        top = QHBoxLayout()
        for level in logstore.LEVELS:
            button = QPushButton(LABELS[level])
            button.setCheckable(True)
            button.setChecked(True)
            button.setProperty("filter", True)
            button.toggled.connect(self.render)
            self.filters[level] = button
            top.addWidget(button)
        top.addStretch()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        folder = QPushButton("Open logs folder")
        folder.clicked.connect(self.open_folder)
        top.addWidget(clear)
        top.addWidget(folder)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)

        self._records = logstore.store.records()
        self.render()
        self.appended.connect(self._add)
        unsubscribe = logstore.store.subscribe(self.appended.emit)
        self.destroyed.connect(lambda *_: unsubscribe())

    def focus_targets(self) -> list[QWidget]:
        """What Up and Down visit on this tab: the filter buttons, then the log itself."""
        buttons = self.findChildren(QPushButton)
        return [buttons[0], self.view] if buttons else [self.view]

    def visible_levels(self) -> set[str]:
        return {level for level, button in self.filters.items() if button.isChecked()}

    @staticmethod
    def line(record: logstore.Record) -> str:
        color = COLORS.get(record.level, COLORS["info"])
        stamp = logstore.format_time(record.time)
        return f'<div style="color:{color}">[{stamp}] {html.escape(record.text)}</div>'

    def render(self, *_args) -> None:
        shown = self.visible_levels()
        self.view.setHtml("".join(self.line(r) for r in self._records if r.level in shown))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _add(self, record: logstore.Record) -> None:
        self._records.append(record)
        del self._records[: -logstore.CAPACITY]
        if record.level not in self.visible_levels():
            return
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4  # keep following only if the reader is at the bottom
        self.view.append(self.line(record))
        if at_end:
            bar.setValue(bar.maximum())

    def clear(self) -> None:
        logstore.store.clear()
        self._records = []
        self.view.clear()

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir() / "logs")))

    def scroll_by(self, amount: float) -> None:
        """Scroll by `amount` of a full stick tick (negative is up); used by the right stick."""
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.value() + int(amount * SCROLL_STEP))

    def focus_default(self) -> None:
        self.view.setFocus()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._to_end)  # the bar has its range only once the tab is on screen

    def _to_end(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
