"""The guide that opens the first time MOG starts with nothing set up: where the server is (and that it answers), where
games go, how saves are kept, and MOG Client in Steam. Each step can be left for later in Settings."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mog_client import selfsteam
from mog_client.api import Client, MogClient
from mog_client.config import default_install_root, save_settings
from mog_client.gui.menu import OptionRow
from mog_client.gui.widgets import Toggle
from mog_client.version import __version__

MASCOTTE = Path(__file__).parent / "assets" / "mascotte.png"
WELCOME, SERVER, GAMES, SAVES, STEAM, DONE = "welcome", "server", "games", "saves", "steam", "done"
FOLDER_NEEDS = 20 * 1024**3  # free space worth warning about, bytes: a game can take that much


def gib(size: int) -> str:
    return f"{size / 1024**3:.1f} GiB"


class FirstRunPage(QWidget):
    """One step at a time, Back and Next at the bottom. Nothing is saved until the last step, except the server (which has
    to answer before it is taken)."""

    title = "Welcome to MOG"
    searchable = False
    modal = False
    compact = False
    show_close = True

    def __init__(self, win, rerun: bool = False) -> None:
        super().__init__()
        self.win = win
        self.rerun = rerun  # opened again from Settings: it starts from what is set, and leaving it changes nothing
        settings = win.app.settings
        self.steps = [WELCOME, SERVER, GAMES, SAVES, *([STEAM] if selfsteam.available() else []), DONE]
        self.index = 0
        self.connected = rerun and settings.configured  # a server that was working stays accepted until its fields are edited
        self.install_dir: str | None = settings.install_dirs[0] if settings.install_dirs else None
        self.folder_chosen = False

        self.stack = QStackedWidget()
        self.pages = {
            WELCOME: self._welcome(),
            SERVER: self._server(settings),
            GAMES: self._games(),
            SAVES: self._saves(settings),
            STEAM: self._steam(),
            DONE: self._done(),
        }
        for step in self.steps:
            self.stack.addWidget(self.pages[step])

        self.progress = QLabel()
        self.progress.setObjectName("optionStatus")
        self.back_button = QPushButton("Previous")  # the header already has a Back, which leaves the guide
        self.back_button.clicked.connect(self.previous)
        self.skip_button = QPushButton("Cancel" if rerun else "Set up later")
        self.skip_button.setProperty("filter", True)
        self.skip_button.clicked.connect(self.skip)
        self.next_button = QPushButton("Next")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self.advance)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 12)
        lay.addWidget(self.progress)
        lay.addWidget(self.stack, 1)
        lay.addLayout(buttons)
        self.go(0)

    # --- the steps ---

    @staticmethod
    def _text(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("overlayTitle")
        label.setWordWrap(True)
        return label

    def _step(self, heading: str, *widgets: QWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(self._heading(heading))
        for widget in widgets:
            lay.addWidget(widget)
        lay.addStretch()
        return page

    def _welcome(self) -> QWidget:
        picture = QLabel()
        picture.setAlignment(Qt.AlignCenter)
        pix = QPixmap(str(MASCOTTE))
        if not pix.isNull():
            picture.setPixmap(pix.scaledToHeight(220, Qt.SmoothTransformation))
        page = self._step(
            "Welcome to MOG",
            picture,
            self._text(
                "MOG brings the games on your MOG-Server to this computer: install them, start them, and keep their saves "
                "with you. A few questions first, and you can change any answer later in Settings."
            ),
        )
        return page

    def _server(self, settings) -> QWidget:
        self.url = QLineEdit(settings.base)
        self.url.setPlaceholderText("http://192.168.1.10:5000")
        self.user = QLineEdit(settings.user)
        self.password = QLineEdit(settings.password)
        self.password.setEchoMode(QLineEdit.Password)
        for field in (self.url, self.user, self.password):
            field.textEdited.connect(self._server_edited)
            field.returnPressed.connect(self.test_connection)
        form = QFormLayout()
        form.addRow("Server address", self.url)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        holder = QWidget()
        holder.setLayout(form)
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self.test_connection)
        self.server_status = self._text("")
        return self._step(
            "Where is your server?",
            self._text("The address of your MOG-Server and the account to sign in with."),
            holder,
            self.test_button,
            self.server_status,
        )

    def _games(self) -> QWidget:
        self.folder_label = self._text("")
        self.space_label = self._text("")
        choose = QPushButton("Choose another folder...")
        choose.clicked.connect(self.choose_folder)
        self._update_folder()
        return self._step(
            "Where should games go?",
            self._text("Games are installed in a folder of their own each, inside this one."),
            self.folder_label,
            self.space_label,
            choose,
        )

    def _saves(self, settings) -> QWidget:
        self.sync_saves = Toggle()
        self.sync_saves.setChecked(settings.sync_saves)
        self.sync_start = Toggle()
        self.sync_start.setChecked(settings.sync_on_start)
        self.sync_saves.toggled.connect(self.sync_start.setEnabled)
        self.sync_start.setEnabled(settings.sync_saves)
        return self._step(
            "Keep your saves safe",
            self._text("MOG can back each game's saves up to the server and bring a newer one over from another computer."),
            OptionRow("Back up saves", "Send each game's saves to the server when you stop playing.", self.sync_saves),
            OptionRow("Check saves at startup", "Bring a newer save from another computer over when MOG starts.", self.sync_start),
        )

    def _steam(self) -> QWidget:
        self.steam_button = QPushButton("Add MOG Client to Steam")
        self.steam_button.clicked.connect(self.win.add_client_to_steam)
        self.steam_status = self._text("")
        return self._step(
            "MOG Client in Steam",
            self._text(
                "Steam is on this computer. MOG Client can sit in its library as a game of its own, with its artwork, "
                "so it opens from Steam and from Game Mode."
            ),
            self.steam_button,
            self.steam_status,
        )

    def _done(self) -> QWidget:
        self.summary = self._text("")
        return self._step("You are set", self.summary, self._text("Everything here can be changed in Settings."))

    # --- going through them ---

    @property
    def step(self) -> str:
        return self.steps[self.index]

    def go(self, index: int) -> None:
        self.index = max(0, min(index, len(self.steps) - 1))
        self.stack.setCurrentIndex(self.index)
        self.progress.setText(f"Step {self.index + 1} of {len(self.steps)}")
        last = self.step == DONE
        self.back_button.setVisible(self.index > 0)
        self.skip_button.setVisible(not last)
        self.next_button.setText("Open my library" if last else "Next")
        if self.step == DONE:
            self._fill_summary()
        if self.step == STEAM:
            self._update_steam()
        self._update_next()
        self.focus_default()

    def _update_next(self) -> None:
        self.next_button.setEnabled(self.step != SERVER or self.connected)

    def advance(self) -> None:
        if self.step == DONE:
            self.finish()
        else:
            self.go(self.index + 1)

    def previous(self) -> None:
        self.go(self.index - 1)

    def focus_default(self) -> None:
        step = self.step
        target = {SERVER: self.url, GAMES: None, SAVES: self.sync_saves, STEAM: self.steam_button}.get(step)
        if target is not None and not (step == SERVER and self.connected):
            target.setFocus()
        elif self.next_button.isEnabled():
            self.next_button.setFocus()
        else:
            self.test_button.setFocus()

    # --- the server ---

    def _server_edited(self) -> None:
        self.connected = False
        self.server_status.setText("")
        self._update_next()

    def test_connection(self) -> None:
        base, user, password = self.url.text().strip(), self.user.text().strip(), self.password.text()
        if not (base and user and password):
            self.server_status.setText("Fill in the address, the user and the password first.")
            return
        if "://" not in base:
            base = "http://" + base
            self.url.setText(base)
        self.test_button.setEnabled(False)
        self.server_status.setText("Connecting...")
        app = self.win.app

        def work() -> None:
            me = MogClient(Client(base, user, password)).me()
            app.bridge.call.emit(lambda: self._tested(base, user, password, me.get("username") or user, None))

        def failed(error: str) -> None:
            app.bridge.call.emit(lambda: self._tested(base, user, password, "", error))

        app.run_bg(work, on_error=failed)

    def _tested(self, base: str, user: str, password: str, name: str, error: str | None) -> None:
        self.test_button.setEnabled(True)
        if error is not None:
            self.connected = False
            self.server_status.setText(f"Could not sign in: {error}")
        else:
            settings = self.win.app.settings
            settings.base, settings.user, settings.password = base, user, password
            save_settings(settings)
            self.connected = True
            self.server_status.setText(f"Connected as {name}.")
        self._update_next()
        self.focus_default()

    # --- games ---

    def _folder(self) -> Path:
        return Path(self.install_dir) if self.install_dir else default_install_root()

    def _update_folder(self) -> None:
        folder = self._folder()
        self.folder_label.setText(str(folder))
        probe = folder
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            self.space_label.setText("")
            return
        warning = "  That is not much for a big game." if free < FOLDER_NEEDS else ""
        self.space_label.setText(f"{gib(free)} free there.{warning}")

    def choose_folder(self) -> None:
        def picked(path: str) -> None:
            self.install_dir = path
            self.folder_chosen = True
            self._update_folder()

        self.win.browse_folder(self._folder() if self._folder().is_dir() else Path.home(), picked)

    # --- steam ---

    def _update_steam(self) -> None:
        settings = self.win.app.settings
        if selfsteam.added(settings):
            self.steam_status.setText("MOG Client is in your Steam library.")
        elif settings.steam_client_pending:
            self.steam_status.setText("Steam is open: it goes in the next time MOG starts with Steam closed.")
        else:
            self.steam_status.setText("")

    # --- the end ---

    def _fill_summary(self) -> None:
        settings = self.win.app.settings
        saves = "backed up to the server" if self.sync_saves.isChecked() else "kept on this computer only"
        lines = [
            f"Server: {settings.base or 'not set yet'}" + (f" ({settings.user})" if settings.user else ""),
            f"Games go in: {self._folder()}",
            f"Saves are {saves}.",
        ]
        if selfsteam.added(settings):
            lines.append("MOG Client is in your Steam library.")
        self.summary.setText("\n".join(lines))

    def _apply(self) -> None:
        settings = self.win.app.settings
        if self.folder_chosen and self.install_dir:  # the chosen folder goes first; the others stay where they were
            settings.install_dirs = [self.install_dir, *(d for d in settings.install_dirs if d != self.install_dir)]
        settings.sync_saves = self.sync_saves.isChecked()
        settings.sync_on_start = self.sync_start.isChecked()
        settings.first_run_done = True
        settings.steam_asked_for = __version__  # this guide was the question
        save_settings(settings)

    def _leave(self) -> None:
        """Close the guide (and the Settings page it was opened from, which holds the values it may have changed)."""
        self.win.back()
        if self.rerun and type(self.win.current_page()).__name__ == "SettingsPage":
            self.win.back()

    def finish(self) -> None:
        self._apply()
        self._leave()
        if self.win.app.settings.configured:
            self.win.app.refresh()

    def skip(self) -> None:
        """Leave the rest for Settings (or, opened again from Settings, just close the guide)."""
        if self.rerun:
            self.win.back()
            return
        self._apply()
        self.win.back()
        if self.win.app.settings.configured:
            self.win.app.refresh()
        else:
            self.win.open_settings()
