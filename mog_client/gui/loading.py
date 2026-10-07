"""The screen shown while the library is being fetched: Mog carrying boxes, bobbing."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QVariantAnimation, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

IMAGE = Path(__file__).parent / "assets" / "mog-loading.png"
BOB = 28  # pixels the picture rises and falls


class LoadingPanel(QWidget):
    def __init__(self, text: str = "MOG is loading your library...") -> None:
        super().__init__()
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        pix = QPixmap(str(IMAGE))
        if not pix.isNull():
            self.picture.setPixmap(pix)
        self.caption = QLabel(text)
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setStyleSheet("color: #9aa3b0; font-size: 18px;")
        self.lay = QVBoxLayout(self)
        self.lay.addStretch(1)
        self.lay.addWidget(self.picture)
        self.lay.addWidget(self.caption)
        self.lay.addStretch(1)
        self.bob = QVariantAnimation(self)
        self.bob.setKeyValueAt(0.0, 0)
        self.bob.setKeyValueAt(0.5, BOB)
        self.bob.setKeyValueAt(1.0, 0)
        self.bob.setDuration(1500)
        self.bob.setEasingCurve(QEasingCurve.InOutSine)
        self.bob.setLoopCount(-1)
        self.bob.valueChanged.connect(self._move)

    def _move(self, offset: int) -> None:
        self.picture.setContentsMargins(0, int(offset), 0, BOB - int(offset))

    def showEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().showEvent(e)
        self.bob.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self.bob.stop()

    def fail(self, text: str) -> None:
        self.bob.stop()
        self.caption.setText(text)
