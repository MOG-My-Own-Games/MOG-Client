"""On-screen keyboard for text fields, for controller and touch use on a Steam Deck.

Opens when a field is tapped, or when a focused field is activated with the
controller's A button (moving onto a field with the D-pad does not open it).

MOG_OSK picks the keyboard:
  unset / "builtin"  the in-app keyboard page (default on a Steam Deck); it types
                     into the field itself, so it needs nothing from Steam
  "steam"            Steam's own keyboard through its steam:// URLs, which types
                     via Steam's virtual input device and is not always delivered
                     to non-Steam apps
  "0"                off
Without MOG_OSK it is only on where Steam runs the app (SteamDeck=1).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QWidget

OPEN_URL = "steam://open/keyboard"
CLOSE_URL = "steam://close/keyboard"
BUILTIN, STEAM = "builtin", "steam"


def mode() -> str | None:
    forced = os.environ.get("MOG_OSK")
    if forced == "0":
        return None
    if forced in (BUILTIN, STEAM):
        return forced
    if forced == "1":
        return BUILTIN
    on_deck = os.environ.get("SteamDeck") == "1" or os.environ.get("SteamGamepadUI") is not None
    return BUILTIN if on_deck else None


def prefer_xcb() -> None:
    """Steam injects its keyboard's keys through X (XWayland), which a native
    Wayland Qt window never receives, so run through xcb under Steam. Call
    before the QApplication exists; QT_QPA_PLATFORM set by the user wins."""
    if mode() and sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def is_text_input(widget: QWidget | None) -> bool:
    return isinstance(widget, (QLineEdit, QPlainTextEdit)) and not widget.isReadOnly()


def _steam_url(url: str) -> None:
    if not QDesktopServices.openUrl(QUrl(url)):
        try:
            subprocess.Popen(["steam", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class OnScreenKeyboard(QObject):
    def __init__(self, open_builtin: Callable[[QWidget], None]) -> None:
        super().__init__()
        self.mode = mode()
        self.enabled = self.mode is not None
        self.open_builtin = open_builtin
        self.steam_visible = False

    def install(self, qapp: QApplication) -> None:
        if self.enabled:
            qapp.installEventFilter(self)
            qapp.focusChanged.connect(self._focus_changed)

    def request(self, widget: QWidget) -> None:
        if self.mode == BUILTIN:
            self.open_builtin(widget)
        elif self.mode == STEAM and not self.steam_visible:
            self.steam_visible = True
            _steam_url(OPEN_URL)

    def hide_steam(self) -> None:
        if self.steam_visible:
            self.steam_visible = False
            _steam_url(CLOSE_URL)

    def _focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        # new is None while Steam's keyboard window holds focus: keep it open then.
        if new is not None and not is_text_input(new):
            self.hide_steam()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.FocusIn and is_text_input(obj) and event.reason() == Qt.MouseFocusReason:
            self.request(obj)
        elif (
            kind == QEvent.KeyPress
            and self.steam_visible
            and is_text_input(obj)
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            self.hide_steam()
        return False
