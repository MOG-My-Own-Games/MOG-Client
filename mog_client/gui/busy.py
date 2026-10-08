"""The message over the window while something the user asked for is under way: what it is, how far it has got, Cancel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QProgressBar, QPushButton, QWidget

COVER_HEIGHT = 200
MIB = 1024 * 1024


def size_text(done: int, total: int) -> str:
    """"3.2 of 9.5 MiB", or just what has arrived when the total is not known."""
    if total:
        return f"{done / MIB:.1f} of {total / MIB:.1f} MiB"
    return f"{done / MIB:.1f} MiB"


class BusyOverlay(QWidget):
    changed = Signal()  # shown or hidden, so the guide at the bottom can follow
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget, suspended=lambda: False) -> None:
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.suspended = suspended  # true while a message is over this one and has the focus
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignCenter)
        self.heading = QLabel()
        self.heading.setObjectName("overlayTitle")
        self.heading.setWordWrap(True)
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.detail = QLabel()
        self.detail.setStyleSheet("color: #9aa3b0; font-size: 15px;")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.cancel_clicked)
        card = QFrame()
        card.setObjectName("overlayCard")
        card.setMaximumWidth(760)
        card.setMinimumWidth(520)
        grid = QGridLayout(card)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.addWidget(self.cover, 0, 0, 4, 1, Qt.AlignTop)
        grid.addWidget(self.heading, 0, 1, Qt.AlignBottom)
        grid.addWidget(self.name, 1, 1, Qt.AlignTop)
        grid.addWidget(self.bar, 2, 1, Qt.AlignBottom)
        grid.addWidget(self.detail, 3, 1, Qt.AlignTop)
        grid.addWidget(self.cancel_button, 4, 0, 1, 2, Qt.AlignHCenter)
        grid.setRowStretch(3, 1)
        outer = QGridLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addWidget(card, 0, 0, Qt.AlignCenter)
        self.hide()
        QApplication.instance().focusChanged.connect(self._keep_focus)

    @property
    def showing(self) -> bool:
        return self.isVisible()

    def show_busy(self, title: str, name: str, cover: QPixmap | None = None, cancellable: bool = True) -> None:
        self.heading.setText(title)
        self.name.setText(name)
        self.cover.setPixmap(cover.scaledToHeight(COVER_HEIGHT, Qt.SmoothTransformation) if cover else QPixmap())
        self.cover.setVisible(cover is not None)
        self.bar.setRange(0, 0)  # moves until there is a figure to show
        self.detail.clear()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(cancellable)
        self.fit_to_parent()
        self.show()
        self.raise_()
        self.cancel_button.setFocus()
        self.changed.emit()

    def set_progress(self, done: int, total: int, detail: str | None = None) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        self.detail.setText(size_text(done, total) if detail is None else detail)

    def set_detail(self, text: str) -> None:
        self.detail.setText(text)

    def cancelling(self) -> None:
        self.cancel_button.setText("Cancelling...")
        self.cancel_button.setEnabled(False)

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
            self.cancel_button.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape and self.cancel_button.isEnabled() and self.cancel_button.isVisible():
            self.cancel_button.click()
        e.accept()
