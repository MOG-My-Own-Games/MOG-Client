"""The small "Syncing saves" window shown when a game started outside MOG ends.

It lives in the process of the command that backs the saves up: it never joins a running client and
never opens the main window."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mog_client.config import InstalledGame, data_dir
from mog_client.gui import gamepad
from mog_client.gui.theme import STYLE
from mog_client.launcher import icon_path

COVER_HEIGHT = 200
CLOSE_OK_MS = 1500
CLOSE_FAILED_MS = 8000  # an error is worth reading; nobody has to press anything to move on


def run(rec: InstalledGame, work: Callable[[Control], object], describe: Callable[[object], tuple[str, bool]]):
    """Show the window, run `work` behind it and show what `describe` says about its result; return that
    result, or raise what `work` raised once the window has closed. `work` is given a `Control` to hide and
    show the window, change its text and ask the user something."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setDesktopFileName("mog-client")
    app.setStyleSheet(STYLE)
    window = SyncWindow(rec.name, _cover(rec))
    done = Done()
    outcome: dict = {}

    def finish(result, error) -> None:
        outcome["result"], outcome["error"] = result, error
        text, ok = (f"Save sync failed: {error}", False) if error else describe(result)
        window.show_result(text, ok)
        QTimer.singleShot(CLOSE_OK_MS if ok else CLOSE_FAILED_MS, app.quit)

    def target() -> None:
        try:
            done.finished.emit(work(Control(done)), None)
        except Exception as e:  # noqa: BLE001 - whatever failed is shown and re-raised below
            done.finished.emit(None, e)

    done.finished.connect(finish)
    done.visible.connect(window.setVisible)
    done.said.connect(window.say)
    done.asked.connect(window.ask)
    pad_stop = gamepad.start(done.pad.emit, lambda connected, family: None)
    done.pad.connect(window.on_pad)
    window.dismissed.connect(app.quit)
    window.show()
    threading.Thread(target=target, daemon=True).start()
    app.exec()
    pad_stop.set()
    if outcome.get("error"):
        raise outcome["error"]
    return outcome.get("result")


def _cover(rec: InstalledGame) -> QPixmap | None:
    """The game's cover as the library cached it (nothing is fetched: the game just ended and the
    server may be the very thing that is slow), else its small icon, else the client's own."""
    covers = sorted((data_dir() / "covers").glob(f"{rec.game_id}-*.img"), key=lambda f: f.stat().st_mtime, reverse=True)
    for path in [*covers[:1], icon_path(rec), Path(__file__).parent / "assets" / "icon.png"]:
        pix = QPixmap(str(path)) if path.is_file() else QPixmap()
        if not pix.isNull():
            return pix
    return None


class Done(QObject):
    finished = Signal(object, object)
    visible = Signal(bool)
    said = Signal(str)
    asked = Signal(str, str, object, object)
    pad = Signal(str)


class Control:
    """What the work running behind the window can do to it, from its own thread."""

    def __init__(self, done: Done) -> None:
        self._done = done

    def __call__(self, visible: bool) -> None:
        self._done.visible.emit(visible)

    def say(self, text: str) -> None:
        self._done.said.emit(text)

    def ask(self, title: str, text: str, options: list[tuple[str, object]]):
        """Show the question with one button per option and wait for the answer; None when it was
        dismissed (Esc, or B on a pad)."""
        box = {"event": threading.Event(), "answer": None}
        self._done.asked.emit(title, text, [label for label, _ in options], box)
        box["event"].wait()
        answer = box["answer"]
        return None if answer is None else options[answer][1]


class SyncWindow(QWidget):
    dismissed = Signal()

    def __init__(self, name: str, cover) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("MOG: syncing saves")
        self.card = QFrame()
        self.card.setObjectName("overlayCard")
        self.card.setMinimumWidth(520)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        if cover is not None:
            self.picture.setPixmap(cover.scaledToHeight(COVER_HEIGHT, Qt.SmoothTransformation))
        self.picture.setVisible(cover is not None)
        self.heading = QLabel("Syncing saves")
        self.heading.setObjectName("overlayTitle")
        self.heading.setWordWrap(True)
        self._game = name
        self.name = QLabel(name)
        self.name.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # no way to tell how long a backup takes: it just moves
        self.bar.setTextVisible(False)
        self.choices = QWidget()
        self.choices.setLayout(QVBoxLayout())
        self.choices.layout().setContentsMargins(0, 8, 0, 0)
        self.choices.hide()
        self._question: dict | None = None
        grid = QGridLayout(self.card)
        grid.setHorizontalSpacing(24)
        grid.addWidget(self.picture, 0, 0, 3, 1, Qt.AlignTop)
        grid.addWidget(self.heading, 0, 1, Qt.AlignBottom)
        grid.addWidget(self.name, 1, 1, Qt.AlignTop)
        grid.addWidget(self.bar, 2, 1, Qt.AlignBottom)
        grid.addWidget(self.choices, 3, 0, 1, 2)
        outer = QGridLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.card)

    def show_result(self, text: str, ok: bool) -> None:
        self.heading.setText(text)
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if ok else 0)
        self.card.setProperty("level", "" if ok else "error")
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.adjustSize()
            self.move(screen.availableGeometry().center() - self.rect().center())

    def say(self, text: str) -> None:
        self.heading.setText(text)

    def ask(self, title: str, text: str, labels: list[str], box: dict) -> None:
        self.heading.setText(title)
        self.name.setText(text)
        self.bar.hide()
        self.buttons = []
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, i=index: self._answered(i))
            self.choices.layout().addWidget(button)
            self.buttons.append(button)
        self._question = box
        self.choices.show()
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()
        self.buttons[0].setFocus()

    def _answered(self, index: int | None) -> None:
        box, self._question = self._question, None
        if box is None:
            return
        for button in self.buttons:
            button.deleteLater()
        self.choices.hide()
        self.bar.show()
        self.heading.setText("Syncing saves")
        self.name.setText(self._game)
        box["answer"] = index
        box["event"].set()

    def on_pad(self, name: str) -> None:
        if self._question is None:
            return
        if name in (gamepad.UP, gamepad.DOWN):
            self.focusNextPrevChild(name == gamepad.DOWN)
        elif name == gamepad.ACCEPT and isinstance(self.focusWidget(), QPushButton):
            self.focusWidget().click()
        elif name == gamepad.BACK:
            self._answered(None)

    def mousePressEvent(self, e) -> None:
        if self._question is None:
            self.dismissed.emit()

    def keyPressEvent(self, e) -> None:
        if self._question is None:
            self.dismissed.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(self.focusWidget(), QPushButton):
            self.focusWidget().click()
        elif e.key() == Qt.Key_Escape:
            self._answered(None)
        elif e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.focusNextPrevChild(e.key() == Qt.Key_Down)
