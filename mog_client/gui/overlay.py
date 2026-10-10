"""A message laid over the window with an OK button: not a window of its own, and not part of the page stack."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

ICON_HEIGHT = 96
TITLES = {"error": "Something went wrong", "warning": "Attention", "info": "Done"}


class MessageOverlay(QWidget):
    """Dims the window and shows one message at a time; more that arrive wait their turn. OK, Enter,
    Esc (or the pad's A and B, which the window forwards here) show the next one or close it."""

    changed = Signal()  # shown or hidden, so the guide at the bottom can follow

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.queue: deque[tuple[str, str, tuple[str, Callable[[], None]] | None, QPixmap | None]] = deque()
        self.current: tuple[str, str] | None = None
        self._action: Callable[[], None] | None = None
        self._returns_to: QWidget | None = None

        self.card = QFrame()
        self.card.setObjectName("overlayCard")
        self.card.setMaximumWidth(720)
        self.title = QLabel()
        self.title.setObjectName("overlayTitle")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.icon = QLabel()  # the game the message is about, at its left
        self.icon.hide()
        self.ok = QPushButton("OK")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.dismiss)
        self.action_button = QPushButton()  # what the message offers to do about it, when it offers anything
        self.action_button.clicked.connect(self.act)
        self.action_button.hide()
        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(14)
        card_layout.addWidget(self.title)
        content = QHBoxLayout()
        content.setSpacing(16)
        content.addWidget(self.icon, 0, Qt.AlignTop)
        content.addWidget(self.body, 1)
        card_layout.addLayout(content)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.action_button)
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

    def show_message(
        self, text: str, level: str = "error", action: tuple[str, Callable[[], None]] | None = None,
        icon: QPixmap | None = None,
    ) -> None:
        """`action` is a (label, function) offered next to OK; it runs, and the message closes. `icon` is shown at the
        left of the text."""
        self.queue.append((level, text, action, icon))
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
        level, text, action, icon = self.queue.popleft()
        self.current = (level, text)
        has_icon = icon is not None and not icon.isNull()
        self.icon.setVisible(has_icon)
        if has_icon:
            self.icon.setPixmap(icon.scaledToHeight(ICON_HEIGHT, Qt.SmoothTransformation))
        self._action = action[1] if action else None
        self.action_button.setVisible(action is not None)
        self.action_button.setText(action[0] if action else "")
        self.action_button.setDefault(action is not None)
        self.ok.setDefault(action is None)
        self.card.setProperty("level", level)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        self.title.setText(TITLES.get(level, TITLES["info"]))
        self.body.setText(text)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        (self.action_button if action else self.ok).setFocus()
        self.changed.emit()

    def dismiss(self) -> None:
        self._next()

    def act(self) -> None:
        run, self._action = self._action, None
        self._next()
        if run is not None:
            run()

    def accept(self) -> None:
        """A on the pad, Enter: the button that has the focus (the action when there is one)."""
        if self.action_button.isVisible() and self.focusWidget() is self.action_button:
            self.act()
        else:
            self.dismiss()

    def fit_to_parent(self) -> None:
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def _keep_focus(self, _old: QWidget | None, new: QWidget | None) -> None:
        """Nothing behind the message may take the focus (Tab, a click) while it is up."""
        if self.isVisible() and new is not None and new is not self.ok and not self.isAncestorOf(new):
            (self.action_button if self.action_button.isVisible() else self.ok).setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.dismiss()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and self.action_button.isVisible():
            (self.ok if self.focusWidget() is self.action_button else self.action_button).setFocus()
        else:
            e.accept()  # swallowed: the page underneath is not driven from here
