"""The message over the window while a game started from here is running: its cover, its name, and Stop."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QPushButton, QWidget

COVER_HEIGHT = 240


class PlayingOverlay(QWidget):
    changed = Signal()  # shown or hidden, so the guide at the bottom can follow
    stop_clicked = Signal()

    def __init__(self, parent: QWidget, suspended: Callable[[], bool] = lambda: False) -> None:
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.suspended = suspended  # true while a message is over this one and has the focus
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignCenter)
        self.heading = QLabel("Playing")
        self.heading.setObjectName("overlayTitle")
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.status = QLabel()  # what is going on right now, in a few words
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setProperty("danger", True)
        self.stop_button.setDefault(True)
        self.stop_button.clicked.connect(self.stop_clicked)
        card = QFrame()
        card.setObjectName("overlayCard")
        card.setMaximumWidth(760)
        grid = QGridLayout(card)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.addWidget(self.cover, 0, 0, 3, 1, Qt.AlignTop)
        grid.addWidget(self.heading, 0, 1, Qt.AlignBottom)
        grid.addWidget(self.name, 1, 1, Qt.AlignTop)
        grid.addWidget(self.status, 2, 1, Qt.AlignTop)
        grid.addWidget(self.stop_button, 3, 0, 1, 2, Qt.AlignHCenter)
        grid.setRowStretch(2, 1)
        outer = QGridLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addWidget(card, 0, 0, Qt.AlignCenter)
        self.hide()
        QApplication.instance().focusChanged.connect(self._keep_focus)

    @property
    def showing(self) -> bool:
        return self.isVisible()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def show_game(self, name: str, cover: QPixmap | None, status: str = "") -> None:
        self.name.setText(name)
        self.status.setText(status)
        self.cover.setPixmap(cover.scaledToHeight(COVER_HEIGHT, Qt.SmoothTransformation) if cover else QPixmap())
        self.cover.setVisible(cover is not None)
        self.stop_button.setText("Stop")
        self.stop_button.setEnabled(True)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.stop_button.setFocus()
        self.changed.emit()

    def stopping(self) -> None:
        self.stop_button.setText("Stopping...")
        self.stop_button.setEnabled(False)

    def end(self) -> None:
        if self.isVisible():
            self.hide()
            self.changed.emit()

    def fit_to_parent(self) -> None:
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def _keep_focus(self, _old: QWidget | None, new: QWidget | None) -> None:
        """Nothing behind this may take the focus (Tab, a click), unless a message is over it."""
        if self.isVisible() and not self.suspended() and new is not None and not self.isAncestorOf(new):
            self.stop_button.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        e.accept()  # Esc and the arrows do nothing here: only Stop ends a game by mistake-proof choice
