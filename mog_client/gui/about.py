"""The About page: its About tab (what this is, who made it, under what licence, where to find it) and its Changelog tab."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from mog_client import changelog, updater
from mog_client.version import __version__

ASSETS = Path(__file__).parent / "assets"
AUTHOR = "Xargon"
LICENSE = "GNU General Public License v3.0"
GITHUB_URL = f"https://github.com/{updater.REPO}"
KOFI_URL = "https://ko-fi.com/xargon"
SUPPORT = "MOG is free. If you like it and want to chip in, you can buy me a coffee."
CREDITS = "Controller and keyboard icons and the menu sounds: Kenney (kenney.nl), CC0 1.0."
LINK_COLOR = "#6fb1ff"


def support_widgets() -> tuple[QLabel, QPushButton]:
    """The invitation to chip in and the Ko-fi button, the same on both tabs."""
    text = QLabel(SUPPORT)
    text.setAlignment(Qt.AlignCenter)
    text.setWordWrap(True)
    button = QPushButton("Support on Ko-fi")
    button.setIcon(QIcon(str(ASSETS / "kofi.png")))
    button.setIconSize(QSize(30, 24))
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
    return text, button


class AboutTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / "mascotte.png")).scaledToHeight(200, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)  # the mascotte carries the title
        details = QLabel(
            f"Version {__version__}<br>"
            f"By {AUTHOR}<br>"
            f"Licence: {LICENSE}<br>"
            f'<span style="color:#9aa3b0">{CREDITS}</span>'
        )
        details.setAlignment(Qt.AlignCenter)
        details.setWordWrap(True)
        self.github = QPushButton("Open the GitHub page")
        self.github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        support, self.kofi = support_widgets()
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(logo)
        lay.addSpacing(10)
        lay.addWidget(details)
        lay.addSpacing(10)
        lay.addWidget(self.github, 0, Qt.AlignHCenter)
        lay.addSpacing(16)
        lay.addWidget(support)
        lay.addWidget(self.kofi, 0, Qt.AlignHCenter)
        lay.addStretch()

    def focus_default(self) -> None:
        self.github.setFocus()


class ChangelogView(QTextBrowser):
    """The notes, read-only. Up at the top hands the focus to the control above instead of keeping it."""

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt's name
        if e.key() == Qt.Key_Up and self.verticalScrollBar().value() == self.verticalScrollBar().minimum():
            self.focusPreviousChild()
            return
        super().keyPressEvent(e)


class ChangelogTab(QWidget):
    """What changed in the running version, from the notes its release was built with, and the invitation to chip in."""

    def __init__(self) -> None:
        super().__init__()
        title = QLabel(f"What's new in {__version__}")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        support, self.kofi = support_widgets()
        self.view = ChangelogView()
        self.view.setOpenExternalLinks(True)
        notes = changelog.text()
        if notes:
            self.view.setMarkdown(notes)
        elif notes == "":
            self.view.setPlainText("Nothing worth noting in this version.")
        else:
            self.view.setHtml(
                "<p>This build carries no changelog.</p>"
                f'<p>The notes of every release are on the <a href="{GITHUB_URL}/releases">releases page</a>.</p>'
            )
        self._color_links()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(support)
        lay.addWidget(self.kofi, 0, Qt.AlignHCenter)
        lay.addSpacing(10)
        lay.addWidget(self.view, 1)

    def _color_links(self) -> None:
        """The links in the notes in a blue that shows on the dark page (the document takes the application's own, which does not)."""
        doc = self.view.document()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LINK_COLOR))
        spans = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.charFormat().isAnchor():
                    spans.append((fragment.position(), fragment.position() + fragment.length()))
                it += 1
            block = block.next()
        for start, end in spans:
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.mergeCharFormat(fmt)

    def focus_default(self) -> None:
        self.view.setFocus()

    def scroll_by(self, amount: float) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.value() + int(amount * 60))
