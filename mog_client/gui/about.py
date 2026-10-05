"""The About tab: what this is, who made it, under what licence, and where to find it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from mog_client import updater
from mog_client.version import __version__

ASSETS = Path(__file__).parent / "assets"
AUTHOR = "Xargon"
LICENSE = "GNU General Public License v3.0"
GITHUB_URL = f"https://github.com/{updater.REPO}"
CREDITS = "Controller and keyboard icons and the menu sounds: Kenney (kenney.nl), CC0 1.0."


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
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(logo)
        lay.addSpacing(10)
        lay.addWidget(details)
        lay.addSpacing(10)
        lay.addWidget(self.github, 0, Qt.AlignHCenter)
        lay.addStretch()

    def focus_default(self) -> None:
        self.github.setFocus()
