"""A settings menu: sections of rows, each a title and what it does on the left and its control on the
right, the row with the focus lit. Moving between rows is Up and Down; a control is only changed or
opened with Enter (or the pad's A), never by the arrows passing over it."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget
from PySide6.QtWidgets import QHBoxLayout

CONTROL_MIN_WIDTH = 380


class MenuCombo(QComboBox):
    """A drop-down the arrows move past instead of changing; Enter opens it."""

    def keyPressEvent(self, e) -> None:
        if not self.view().isVisible():
            if e.key() in (Qt.Key_Up, Qt.Key_Down):
                self.focusNextPrevChild(e.key() == Qt.Key_Down)
                return
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.showPopup()
                return
        super().keyPressEvent(e)

    def wheelEvent(self, e) -> None:
        e.ignore()  # scrolling the menu must not change an option it passes over


class MenuLineEdit(QLineEdit):
    """A text field that lets Up and Down carry on to the next row."""

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.focusNextPrevChild(e.key() == Qt.Key_Down)
            return
        super().keyPressEvent(e)


class OptionRow(QFrame):
    focused = Signal(object)

    def __init__(self, title: str, description: str, control: QWidget, wide: bool = False) -> None:
        super().__init__()
        self.setObjectName("optionRow")
        self.setProperty("active", False)
        self.control = control
        self.title = QLabel(title)
        self.title.setObjectName("optionTitle")
        self.description = QLabel(description)
        self.description.setObjectName("optionDescription")
        self.description.setWordWrap(True)
        self.status = QLabel()
        self.status.setObjectName("optionStatus")
        self.status.setWordWrap(True)
        self.status.hide()
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.description)
        texts.addWidget(self.status)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(24)
        row.addLayout(texts, 1)
        if wide:
            control.setMinimumWidth(CONTROL_MIN_WIDTH)
        row.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)
        for widget in (control, *control.findChildren(QWidget)):
            widget.installEventFilter(self)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.FocusIn:
            self._set_active(True)
            self.focused.emit(self)
        elif event.type() == QEvent.FocusOut:
            self._set_active(False)
        return False


class MenuView(QScrollArea):
    """The rows, in sections, scrolling when they do not all fit; the focused row is kept in view."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("menuBody")
        self.setObjectName("menuView")
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(0, 18, 8, 12)
        self.layout_.setSpacing(8)
        self.layout_.addStretch()
        self.setWidget(body)
        self.rows: list[OptionRow] = []

    def section(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setObjectName("menuSection")
        self.layout_.insertWidget(self.layout_.count() - 1, label)

    def add(self, row: OptionRow) -> OptionRow:
        self.layout_.insertWidget(self.layout_.count() - 1, row)
        row.focused.connect(lambda r: self.ensureWidgetVisible(r, 0, 28))
        self.rows.append(row)
        return row
