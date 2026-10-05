"""A message laid over the window with an OK button: not a window of its own, and not part of the page stack."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

TITLES = {"error": "Something went wrong", "warning": "Attention", "info": "Done"}


class MessageOverlay(QWidget):
    """Dims the window and shows one message at a time; more that arrive wait their turn. OK, Enter,
    Esc (or the pad's A and B, which the window forwards here) show the next one or close it."""

    changed = Signal()  # shown or hidden, so the guide at the bottom can follow

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.queue: deque[tuple[str, str]] = deque()
        self.current: tuple[str, str] | None = None
        self._returns_to: QWidget | None = None

        self.card = QFrame()
        self.card.setObjectName("overlayCard")
        self.card.setMaximumWidth(720)
        self.title = QLabel()
        self.title.setObjectName("overlayTitle")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ok = QPushButton("OK")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.dismiss)
        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(14)
        card_layout.addWidget(self.title)
        card_layout.addWidget(self.body)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.ok)
        buttons.addStretch()
        card_layout.addLayout(buttons)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.card, 3)
        row.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.hide()
        QApplication.instance().focusChanged.connect(self._keep_focus)

    @property
    def showing(self) -> bool:
        return self.isVisible()

    def show_message(self, text: str, level: str = "error") -> None:
        self.queue.append((level, text))
        if not self.isVisible():
            self._returns_to = QApplication.focusWidget()
            self._next()

    def _next(self) -> None:
        if not self.queue:
            self.current = None
            self.hide()
            self.changed.emit()
            if self._returns_to is not None:
                try:
                    self._returns_to.setFocus()
                except RuntimeError:  # the widget it came from is gone
                    pass
                self._returns_to = None
            return
        level, text = self.queue.popleft()
        self.current = (level, text)
        self.card.setProperty("level", level)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        self.title.setText(TITLES.get(level, TITLES["info"]))
        self.body.setText(text)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.ok.setFocus()
        self.changed.emit()

    def dismiss(self) -> None:
        self._next()

    def fit_to_parent(self) -> None:
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def _keep_focus(self, _old: QWidget | None, new: QWidget | None) -> None:
        """Nothing behind the message may take the focus (Tab, a click) while it is up."""
        if self.isVisible() and new is not None and new is not self.ok and not self.isAncestorOf(new):
            self.ok.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.dismiss()
        else:
            e.accept()  # swallowed: the page underneath is not driven from here
