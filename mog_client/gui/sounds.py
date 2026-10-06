"""Short sounds for moving through the menus and for starting a game (Kenney Interface Sounds, CC0).

They are a nicety: with no audio device, no multimedia backend or the setting off, nothing happens
and nothing fails."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

SOUNDS = Path(__file__).parent / "assets" / "sounds"
NAVIGATE, PLAY = "navigate", "play"
VOLUME = {NAVIGATE: 0.35, PLAY: 0.6}
DEBOUNCE = {NAVIGATE: 0.05, PLAY: 0.5}  # seconds: a held direction repeats many times a second
NAVIGATION_KEYS = (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right, Qt.Key_Tab, Qt.Key_Backtab)


def _qt_effect(path: Path, volume: float):
    from PySide6.QtMultimedia import QSoundEffect

    effect = QSoundEffect()
    effect.setSource(QUrl.fromLocalFile(str(path)))
    effect.setVolume(volume)
    return effect


class SoundPlayer:
    def __init__(
        self,
        enabled: Callable[[], bool],
        factory: Callable[[Path, float], object] = _qt_effect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled, self.factory, self.clock = enabled, factory, clock
        self._effects: dict[str, object | None] = {}
        self._last: dict[str, float] = {}
        self._closed = False

    def close(self) -> None:
        """Silence what is playing or still queued in the audio buffer and play nothing more. Called as the
        window closes: a sound left to its destructor finishes in the audio server after the client is gone."""
        self._closed = True
        for effect in self._effects.values():
            for action in ("stop", "deleteLater"):
                try:
                    getattr(effect, action)()
                except Exception:  # noqa: BLE001, S110 - a fake or an already deleted effect
                    pass
        self._effects.clear()

    def play(self, name: str) -> None:
        if self._closed or not self.enabled():
            return
        now = self.clock()
        if now - self._last.get(name, -1e9) < DEBOUNCE.get(name, 0.0):
            return
        self._last[name] = now
        effect = self._effect(name)
        if effect is not None:
            try:
                effect.play()
            except Exception:  # noqa: BLE001, S110 - a sound must never break the interface
                pass

    def _effect(self, name: str):
        if name not in self._effects:
            path = SOUNDS / f"{name}.wav"
            try:
                self._effects[name] = self.factory(path, VOLUME.get(name, 0.5)) if path.is_file() else None
            except Exception:  # noqa: BLE001 - no multimedia support here
                self._effects[name] = None
        return self._effects[name]


def is_navigation(key: int, spontaneous: bool, typing: bool) -> bool:
    """Whether a key event is the user moving through the interface with the keyboard. Events the
    window posts itself for the pad are not spontaneous: the pad handler plays its own sound."""
    return spontaneous and not typing and key in NAVIGATION_KEYS


class NavigationSounds(QObject):
    """Plays the navigation sound for the keyboard's arrow and Tab keys."""

    def __init__(self, player: SoundPlayer) -> None:
        super().__init__()
        self.player = player

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.KeyPress:
            focus = QApplication.focusWidget()
            typing = isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit)) and not focus.isReadOnly()
            if is_navigation(event.key(), event.spontaneous(), typing):
                self.player.play(NAVIGATE)
        return False
