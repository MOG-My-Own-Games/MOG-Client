"""A page shown as a smaller card laid over the page beneath it, which stays in view behind a dimmed backdrop. It is
still a page of the window (Back, Esc and the pad's B close it, and the stack of pages counts it), only drawn small."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

MAX_WIDTH = 780
MAX_HEIGHT = 540
COMPACT_HEIGHT_SHARE = 0.94  # a compact card may take more of the window than a full-size one: it is only as tall as it needs
COMPACT_WIDTH = 560  # a question or a short menu: a card as big as what it holds, not the full-size one
SHARE = 0.82  # of the window, at most


class FitScrollArea(QScrollArea):
    """A scroll area that asks for as much room as what it holds, so a card sizes itself to a tall menu and
    scrolls it only when the window is too short for it."""

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")

    def sizeHint(self) -> QSize:
        return self.widget().sizeHint() + QSize(self.verticalScrollBar().sizeHint().width(), 8)  # a little over, so a menu that fits shows no scrollbar


class ModalHost(QWidget):
    """Holds one page at a time in a centred card; a click on the backdrop asks to close it."""

    dismissed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("modalHost")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.page: QWidget | None = None
        self.card = QFrame(self)
        self.card.setObjectName("modalCard")
        self.title = QLabel()
        self.title.setObjectName("modalTitle")
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.dismissed)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(18, 14, 18, 14)
        self.body.addWidget(self.title)
        self.hide()
        QApplication.instance().focusChanged.connect(self._keep_focus)

    @property
    def showing(self) -> bool:
        return self.isVisible()

    def show_page(self, page: QWidget, title: str) -> None:
        if self.page is not page:
            self.release()
            self.page = page
            self.body.insertWidget(1, page)
            self.body.addWidget(self.close_button, 0, Qt.AlignHCenter)
            self.close_button.setVisible(getattr(page, "show_close", True))
            page.show()
        self.title.setText(title)
        self.fit_to_parent()
        self.show()
        self.raise_()

    def release(self, page: QWidget | None = None) -> None:
        """Take the page out of the card (it is going away, or another one is taking its place)."""
        if page is not None and page is not self.page:
            return
        if self.page is not None:
            self.body.removeWidget(self.page)
            self.body.removeWidget(self.close_button)
            self.page.hide()
            self.page.setParent(None)
            self.page = None

    def hide_page(self) -> None:
        """Out of sight while another page is over it, the page itself kept for when that one is gone."""
        self.hide()

    def fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        max_width = min(MAX_WIDTH, int(self.width() * SHARE))
        max_height = min(MAX_HEIGHT, int(self.height() * SHARE))
        width, height = max_width, max_height
        if getattr(self.page, "compact", False):
            max_height = int(self.height() * COMPACT_HEIGHT_SHARE)
            width = min(COMPACT_WIDTH, max_width)
            margins = self.body.contentsMargins()
            inner = width - margins.left() - margins.right()
            wanted = self.body.totalHeightForWidth(inner) if self.body.hasHeightForWidth() else self.body.sizeHint().height()
            height = min(max_height, max(wanted, self.body.minimumSize().height()))
        self.card.setGeometry(QRect((self.width() - width) // 2, (self.height() - height) // 2, width, height))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if not self.card.geometry().contains(e.position().toPoint()):
            self.dismissed.emit()
        e.accept()

    def _keep_focus(self, _old: QWidget | None, new: QWidget | None) -> None:
        """Nothing behind the card may take the focus (Tab, a click) while it is up."""
        if self.isVisible() and self.page is not None and new is not None and not self.isAncestorOf(new):
            self.page.focus_default()
