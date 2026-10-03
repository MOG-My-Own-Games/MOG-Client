"""Steam's on-screen keyboard for text fields, behaving as Steam's own apps do.

Steam exposes it to non-Steam apps through steam:// URLs. It opens when a field
is tapped, or when a focused field is activated with the controller's A button
(moving onto a field with the D-pad does not open it). It closes when focus
leaves the field, with B, or once Enter is sent from it. Only active where
Steam runs the app (Steam Deck); MOG_OSK=1/0 forces it on/off.
"""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QWidget

OPEN_URL = "steam://open/keyboard"
CLOSE_URL = "steam://close/keyboard"


def available() -> bool:
    forced = os.environ.get("MOG_OSK")
    if forced in ("0", "1"):
        return forced == "1"
    return os.environ.get("SteamDeck") == "1" or os.environ.get("SteamGamepadUI") is not None


def is_text_input(widget: QWidget | None) -> bool:
    return isinstance(widget, (QLineEdit, QPlainTextEdit)) and not widget.isReadOnly()


def _steam_url(url: str) -> None:
    if not QDesktopServices.openUrl(QUrl(url)):
        try:
            subprocess.Popen(["steam", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class SteamKeyboard(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = available()
        self.visible = False

    def install(self, qapp: QApplication) -> None:
        if self.enabled:
            qapp.installEventFilter(self)
            qapp.focusChanged.connect(self._focus_changed)

    def show(self) -> None:
        if not self.visible:
            self.visible = True
            _steam_url(OPEN_URL)

    def hide(self) -> None:
        if self.visible:
            self.visible = False
            _steam_url(CLOSE_URL)

    def _focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        if not is_text_input(new):
            self.hide()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.FocusIn and is_text_input(obj) and event.reason() == Qt.MouseFocusReason:
            self.show()
        elif kind == QEvent.KeyPress and self.visible and is_text_input(obj) and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.hide()
        return False
