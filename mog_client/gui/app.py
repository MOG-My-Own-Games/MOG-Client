"""Qt GUI: library grid, per-game install/play dialog, settings. Fully
operable from a gamepad (D-pad/stick = arrows, A = Enter, B = Esc)."""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStyledItemDelegate,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from mog_client import manager, steam
from mog_client.api import fetch_url, fmt_bytes
from mog_client.config import (
    InstalledGame,
    Settings,
    data_dir,
    load_library,
    load_settings,
    save_settings,
)
from mog_client.gui import gamepad
from mog_client.launcher import available_launchers, detect_launcher, launch, launcher_label, list_executables
from mog_client.scrape import artwork_urls, metadata_lines, screenshot_urls
from mog_client.version import __version__

STYLE = """
* { font-size: 18px; }
QMainWindow { background: #14171c; color: #e8eaed; }
QLabel, QCheckBox { color: #e8eaed; }
QLineEdit, QComboBox, QPlainTextEdit { background: #1e232b; color: #e8eaed; border: 2px solid #2c333d; border-radius: 6px; padding: 8px; }
QPushButton { background: #2c333d; color: #e8eaed; border: 2px solid transparent; border-radius: 8px; padding: 12px 22px; }
QPushButton:hover { background: #38414d; }
QPushButton:default { background: #2f6fed; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { color: #e8eaed; border: 3px solid transparent; border-radius: 10px; padding: 6px; }
QListWidget::item:selected { border-color: #4c8dff; background: #1e2733; }
*:focus { border-color: #4c8dff; }
QProgressBar { background: #1e232b; border: none; border-radius: 6px; height: 18px; text-align: center; color: white; }
QProgressBar::chunk { background: #2f6fed; border-radius: 6px; }
"""

COVER_SIZE = QSize(200, 270)
_KEYS = {
    gamepad.UP: Qt.Key_Up,
    gamepad.DOWN: Qt.Key_Down,
    gamepad.LEFT: Qt.Key_Left,
    gamepad.RIGHT: Qt.Key_Right,
    gamepad.ACCEPT: Qt.Key_Return,
    gamepad.BACK: Qt.Key_Escape,
    gamepad.PAGE_NEXT: Qt.Key_Tab,
    gamepad.PAGE_PREV: Qt.Key_Backtab,
}


class Bridge(QObject):
    """Thread-safe hand-off from worker threads to the GUI thread."""

    games = Signal(list)
    error = Signal(str)
    cover = Signal(int, bytes)
    image = Signal(str, bytes)  # url, bytes (screenshots)
    progress = Signal(int, int, int, str)  # game, written, total, state text
    log = Signal(int, str)
    finished = Signal(int, str)  # game, error text ("" on success)
    pad = Signal(str)


def is_deck() -> bool:
    return os.environ.get("SteamDeck") == "1" or os.environ.get("SteamGamepadUI") is not None


class App:
    """Shared state: settings, server data, running installs."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.bridge = Bridge()
        self.games: dict[int, dict] = {}
        self.installs: dict[int, threading.Event] = {}
        self.progress: dict[int, tuple[int, int, str]] = {}
        self.vnc: dict[int, str] = {}

    def client(self):
        return manager.make_client(self.settings)

    def run_bg(self, fn, on_error=None) -> None:
        def wrapper():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - surfaced in the UI
                (on_error or self.bridge.error.emit)(str(e))

        threading.Thread(target=wrapper, daemon=True).start()

    def refresh(self) -> None:
        def work():
            games = self.client().list_games()
            self.bridge.games.emit(games)
            for g in games:
                self._load_cover(g)

        self.run_bg(work)

    def _load_cover(self, game: dict) -> None:
        cache = data_dir() / "covers" / f"{game['id']}.img"
        if not cache.is_file():
            url = artwork_urls(game).get("portrait")
            if not url:
                return
            try:
                blob = fetch_url(url)
            except RuntimeError:
                return
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(blob)
        self.bridge.cover.emit(game["id"], cache.read_bytes())

    def fetch_images(self, urls: list[str]) -> None:
        def work():
            for url in urls:
                cache = data_dir() / "shots" / hashlib.sha1(url.encode()).hexdigest()
                if not cache.is_file():
                    try:
                        blob = fetch_url(url)
                    except RuntimeError:
                        continue
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(blob)
                self.bridge.image.emit(url, cache.read_bytes())

        self.run_bg(work, on_error=lambda _m: None)

    def start_install(self, game: dict) -> None:
        gid = game["id"]
        if gid in self.installs:
            return
        stop = threading.Event()
        self.installs[gid] = stop
        bridge = self.bridge

        server_state = {"label": ""}

        def report(written: int, total: int) -> None:
            label = f"Downloading {fmt_bytes(written)} / {fmt_bytes(total)}"
            if server_state["label"]:
                label += f"  (server: {server_state['label']})"
            self.progress[gid] = (written, total, label)
            bridge.progress.emit(gid, written, total, label)

        def on_session(s: dict) -> None:
            detail = s.get("phase_detail")
            server_state["label"] = s.get("state", "") + (f": {detail}" if detail else "")
            if s.get("auto_status") == "running" and s.get("auto_detail"):
                server_state["label"] += f" (auto: {s['auto_detail']})"
            if s.get("vnc_url"):
                self.vnc[gid] = self.settings.base.rstrip("/") + s["vnc_url"]
            if s.get("auto_status") == "needs_manual" and not server_state.get("warned"):
                server_state["warned"] = True
                server_state["label"] = "auto mode needs you: open the installer display"
                bridge.error.emit(f"{game['name']}: auto mode cannot continue, finish the installer by hand")
            written, total, _ = self.progress.get(gid, (0, 0, ""))
            report(written, total)

        def work():
            err = ""
            try:
                manager.run_install(
                    self.client(), game, self.settings, stop,
                    lambda m: bridge.log.emit(gid, m), on_session,
                    report,
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)
            self.installs.pop(gid, None)
            self.progress.pop(gid, None)
            bridge.finished.emit(gid, err)

        threading.Thread(target=work, daemon=True).start()

    def pause_install(self, gid: int) -> None:
        if gid in self.installs:
            self.installs[gid].set()

    def cancel_install(self, gid: int) -> None:
        self.pause_install(gid)
        self.run_bg(lambda: self.client().cancel_session(gid))



class ActivateOnEnter(QObject):
    """Outside a QDialog Qt only presses a button on Space; Enter and the
    gamepad's A must activate the focused button too."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if isinstance(obj, QAbstractButton) and obj.isEnabled():
                obj.click()
                return True
        return False


class Page(QWidget):
    """One screen of the single window. `title` shows in the header."""

    title = ""
    searchable = False

    def focus_default(self) -> None:
        self.setFocus()


def _row(*widgets, stretch_first: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    if stretch_first:
        row.addStretch()
    for w in widgets:
        row.addWidget(w)
    return row


class ConfirmPage(Page):
    """Inline yes/no question; "No" holds the initial focus so a stray A press is safe."""

    def __init__(self, win: "MainWindow", text: str, on_yes, title: str = "Are you sure?"):
        super().__init__()
        self.title = title
        label = QLabel(text)
        label.setWordWrap(True)
        self.no, yes = QPushButton("No"), QPushButton("Yes")
        self.no.clicked.connect(win.back)
        yes.clicked.connect(lambda: (win.back(), on_yes()))
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(label)
        lay.addLayout(_row(self.no, yes))
        lay.addStretch()

    def focus_default(self) -> None:
        self.no.setFocus()


class SettingsPage(Page):
    title = "Settings"

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        s = win.app.settings
        self.base = QLineEdit(s.base)
        self.base.setPlaceholderText("http://server:5000")
        self.user = QLineEdit(s.user)
        self.password = QLineEdit(s.password)
        self.password.setEchoMode(QLineEdit.Password)
        self.games_dir = QLineEdit(s.games_dir)
        self.games_dir.setPlaceholderText(str(s.games_path))
        self.launcher = QComboBox()
        available = available_launchers()
        for name in available:
            self.launcher.addItem(launcher_label(name), name)
        chosen = detect_launcher(s.launcher)
        if chosen:
            self.launcher.setCurrentIndex(available.index(chosen))
        self.launcher.setEnabled(len(available) > 1)
        hint = "Faugus is the default when installed; otherwise pick among the system's umu, Proton or Wine."
        if not available:
            hint = "No launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine."
        form = QFormLayout()
        form.addRow("Server URL", self.base)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        form.addRow("Games folder", self.games_dir)
        form.addRow("Launcher", self.launcher)
        form.addRow("Version", QLabel(__version__))
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        note = QLabel(hint)
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()
        lay.addLayout(_row(save))

    def focus_default(self) -> None:
        (self.base if not self.base.text() else self.launcher).setFocus()

    def save(self) -> None:
        self.win.app.settings = Settings(
            base=self.base.text().strip(),
            user=self.user.text().strip(),
            password=self.password.text(),
            games_dir=self.games_dir.text().strip(),
            launcher=self.launcher.currentData() or "auto",
        )
        save_settings(self.win.app.settings)
        self.win.back()
        self.win.app.refresh()


class BrowsePage(Page):
    """In-window file picker (no native dialog, so it stays gamepad friendly)."""

    title = "Browse for the executable"

    def __init__(self, win: "MainWindow", start: Path, on_pick):
        super().__init__()
        self.win, self.on_pick, self.cwd = win, on_pick, start
        self.where = QLabel()
        self.list = QListWidget()
        self.list.itemActivated.connect(self._activate)
        lay = QVBoxLayout(self)
        lay.addWidget(self.where)
        lay.addWidget(self.list, 1)
        self._load(start)

    def focus_default(self) -> None:
        self.list.setFocus()

    def _load(self, path: Path) -> None:
        self.cwd = path
        self.where.setText(str(path))
        self.list.clear()
        entries = [("..", path.parent)] if path.parent != path else []
        try:
            children = sorted(path.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            children = []
        entries += [(c.name + ("/" if c.is_dir() else ""), c) for c in children if c.is_dir() or c.suffix.lower() == ".exe"]
        for label, target in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(target))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem) -> None:
        target = Path(item.data(Qt.UserRole))
        if target.is_dir():
            self._load(target)
        else:
            self.win.back()
            self.on_pick(str(target))


class ExecutablePage(Page):
    """The "awaiting executable info" step: pick what to run, then create the entries."""

    def __init__(self, win: "MainWindow", rec: InstalledGame, on_done):
        super().__init__()
        self.title = f"Choose the executable for {rec.name}"
        self.win, self.on_done = win, on_done
        self.root = Path(rec.install_dir)
        self.list = QListWidget()
        for exe in list_executables(self.root):
            item = QListWidgetItem(f"{exe.relative_to(self.root)}   ({fmt_bytes(exe.stat().st_size)})")
            item.setData(Qt.UserRole, str(exe))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(lambda _: self.accept())
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        self.desktop = QCheckBox("Create a desktop entry")
        self.desktop.setChecked(sys.platform != "win32")
        self.desktop.setVisible(sys.platform != "win32")
        self.steam_users = steam.steam_user_dirs()
        self.steam = QCheckBox("Add to Steam")
        self.steam.setChecked(bool(self.steam_users))
        self.steam.setEnabled(bool(self.steam_users))
        self.steam_user = QComboBox()
        for d in self.steam_users:
            self.steam_user.addItem(f"Steam user {d.name}", str(d))
        self.steam_user.setVisible(len(self.steam_users) > 1)
        ok = QPushButton("Use this executable")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        later = QPushButton("Later")
        later.clicked.connect(win.back)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Which file starts the game?"))
        lay.addWidget(self.list, 1)
        for w in (self.desktop, self.steam, self.steam_user):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(browse)
        row.addStretch()
        row.addWidget(later)
        row.addWidget(ok)
        lay.addLayout(row)

    def focus_default(self) -> None:
        self.list.setFocus()

    def _browse(self) -> None:
        def picked(path: str) -> None:
            item = QListWidgetItem(f"{path}   (manual)")
            item.setData(Qt.UserRole, path)
            self.list.insertItem(0, item)
            self.list.setCurrentRow(0)

        self.win.push(BrowsePage(self.win, self.root, picked))

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        steam_user = Path(self.steam_user.currentData()) if self.steam.isChecked() else None
        self.win.back()
        self.on_done(item.data(Qt.UserRole), steam_user, self.desktop.isChecked())


SHOT_SIZE = QSize(224, 126)


class StripList(QListWidget):
    """One horizontal row of thumbnails. Up/Down leave the row, so a pad can reach the buttons."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Static)
        self.setIconSize(SHOT_SIZE)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Down:
            self.focusNextChild()
        elif e.key() == Qt.Key_Up:
            self.focusPreviousChild()
        else:
            super().keyPressEvent(e)


class ViewerPage(Page):
    """Full-size screenshot; Left/Right to flip, Back to return."""

    title = "Screenshots"

    def __init__(self, urls: list[str], pixmaps: dict[str, QPixmap], index: int):
        super().__init__()
        self.urls, self.pixmaps, self.index = urls, pixmaps, index
        self.setFocusPolicy(Qt.StrongFocus)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        QVBoxLayout(self).addWidget(self.label)

    def focus_default(self) -> None:
        self.setFocus()
        self._render()

    def resizeEvent(self, e) -> None:
        self._render()

    def _render(self) -> None:
        pix = self.pixmaps[self.urls[self.index]]
        self.label.setPixmap(pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.title = f"Screenshot {self.index + 1} / {len(self.urls)}"

    def keyPressEvent(self, e: QKeyEvent) -> None:
        step = {Qt.Key_Right: 1, Qt.Key_Down: 1, Qt.Key_Left: -1, Qt.Key_Up: -1}.get(e.key())
        if step:
            self.index = (self.index + step) % len(self.urls)
            self._render()
        else:
            super().keyPressEvent(e)


class GamePage(Page):
    """Install/play screen. Stays alive across navigation so an install keeps
    reporting into it while the user browses the library."""

    def __init__(self, win: "MainWindow", game: dict):
        super().__init__()
        self.win, self.app, self.game = win, win.app, game
        self.title = game["name"]
        self.cover = QLabel()
        self.cover.setFixedSize(COVER_SIZE)
        self.cover.setAlignment(Qt.AlignCenter)
        self._set_cover(win.covers.get(game["id"]))
        info_col = QVBoxLayout()
        form = QFormLayout()
        for label, value in metadata_lines(game):
            val = QLabel(value)
            val.setWordWrap(True)
            form.addRow(label, val)
        info_col.addLayout(form)
        text = game.get("summary") or (game.get("igdb_metadata") or {}).get("summary") or ""
        summary = QLabel(text if len(text) < 700 else text[:700].rsplit(" ", 1)[0] + "...")
        summary.setWordWrap(True)
        info_col.addWidget(summary)
        info_col.addStretch()
        head = QHBoxLayout()
        head.addWidget(self.cover)
        head.addLayout(info_col, 1)

        self.shots = StripList()
        self.shots.setFixedHeight(SHOT_SIZE.height() + 30)
        self.shot_urls = screenshot_urls(game)
        self.shot_pix: dict[str, QPixmap] = {}
        self.shots.setVisible(bool(self.shot_urls))
        self.shots.itemActivated.connect(self._open_shot)

        self.status = QLabel()
        self.bar = QProgressBar()
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(500)
        self.logbox.setMaximumHeight(90)
        self.buttons = QHBoxLayout()
        self.first: QPushButton | None = None
        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.shots)
        for w in (self.status, self.bar, self.logbox):
            lay.addWidget(w)
        lay.addLayout(self.buttons)
        b = self.app.bridge
        b.progress.connect(self._on_progress)
        b.log.connect(self._on_log)
        b.finished.connect(self._on_finished)
        b.image.connect(self._on_image)
        b.cover.connect(lambda gid, _blob: gid == game["id"] and self._set_cover(win.covers.get(gid)))
        self.app.fetch_images(self.shot_urls)
        self.rebuild()

    def _set_cover(self, pix: QPixmap | None) -> None:
        if pix:
            self.cover.setPixmap(pix)

    def _on_image(self, url: str, blob: bytes) -> None:
        pix = QPixmap()
        if url not in self.shot_urls or url in self.shot_pix or not pix.loadFromData(blob):
            return
        self.shot_pix[url] = pix
        item = QListWidgetItem()
        item.setData(Qt.DecorationRole, pix.scaled(SHOT_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        item.setData(Qt.UserRole, url)
        self.shots.addItem(item)
        if self.shots.count() == 1:
            self.shots.setCurrentRow(0)

    def _open_shot(self, item: QListWidgetItem) -> None:
        ordered = [u for u in self.shot_urls if u in self.shot_pix]
        self.win.push(ViewerPage(ordered, self.shot_pix, ordered.index(item.data(Qt.UserRole))))

    def focus_default(self) -> None:
        self.rebuild()
        if self.first:
            self.first.setFocus()

    def _on_progress(self, gid: int, written: int, total: int, label: str) -> None:
        if gid != self.game["id"]:
            return
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * written / total))
            self.bar.setFormat(f"{fmt_bytes(written)} / {fmt_bytes(total)}")
        else:
            self.bar.setRange(0, 0)
        self.status.setText(label)

    def _on_log(self, gid: int, msg: str) -> None:
        if gid == self.game["id"]:
            self.logbox.appendPlainText(msg)

    def _on_finished(self, gid: int, err: str) -> None:
        if gid != self.game["id"]:
            return
        if err:
            self.logbox.appendPlainText(err)
            self.win.notify(f"{self.game['name']}: {err}")
        self.rebuild()
        rec = load_library().get(gid)
        if rec and rec.state == "awaiting_executable" and not err:
            if self.win.current_page() is self:
                self.choose_executable(rec)
            else:
                self.win.notify(f"{self.game['name']} finished installing: open it to choose the executable")

    def _button(self, text: str, fn, default: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(fn)
        if default:
            btn.setDefault(True)
        self.buttons.addWidget(btn)
        return btn

    def rebuild(self) -> None:
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()
        gid = self.game["id"]
        rec = load_library().get(gid)
        self.first = None
        if gid in self.app.installs:
            self.status.setText("Installing... you can go back, it keeps running")
            written, total, label = self.app.progress.get(gid, (0, 0, ""))
            self._on_progress(gid, written, total, label or self.status.text())
            self.first = self._button("Back to library", self.win.back, True)
            if gid in self.app.vnc:
                self._button("Open installer display", lambda: QDesktopServices.openUrl(QUrl(self.app.vnc[gid])))
            self._button("Pause", lambda: self.app.pause_install(gid))
            self._button("Cancel install", self.cancel)
            return
        if rec and rec.state == "installed":
            self.status.setText("Installed")
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.first = self._button("Play", self.play, True)
            self._button("Shortcuts / executable", lambda: self.choose_executable(rec))
            self._button("Uninstall", lambda: self.uninstall(rec))
        elif rec and rec.state == "awaiting_executable":
            self.status.setText("Awaiting executable info")
            self.first = self._button("Choose executable", lambda: self.choose_executable(rec), True)
            self._button("Uninstall", lambda: self.uninstall(rec))
        else:
            self.status.setText("Partially downloaded, can resume" if rec else "Not installed")
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.first = self._button("Resume install" if rec else "Install", self.install, True)
            if rec:
                self._button("Discard", lambda: self.uninstall(rec))
        self._button("Back", self.win.back)

    def install(self) -> None:
        self.app.start_install(self.game)
        self.rebuild()
        if self.first:
            self.first.setFocus()

    def cancel(self) -> None:
        self.win.ask(
            "Cancel the install on the server? Downloaded files are kept for a later resume.",
            lambda: self.app.cancel_install(self.game["id"]),
        )

    def play(self) -> None:
        rec = load_library()[self.game["id"]]
        try:
            launch(rec, self.app.settings.launcher)
        except (RuntimeError, OSError) as e:
            self.win.notify(str(e))

    def choose_executable(self, rec: InstalledGame) -> None:
        def done(exe: str, steam_user: Path | None, desktop: bool) -> None:
            if steam_user and steam.steam_running():
                self.win.notify("Steam is running: restart it to see the new shortcut.")
            manager.remove_entries(rec)
            self.logbox.appendPlainText("Creating entries and fetching artwork...")

            def work():
                manager.finish_setup(rec, self.game, exe, steam_user, desktop)
                self.app.bridge.finished.emit(rec.game_id, "")

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.finished.emit(rec.game_id, m))

        self.win.push(ExecutablePage(self.win, rec, done))

    def uninstall(self, rec: InstalledGame) -> None:
        def remove(delete_prefix: bool) -> None:
            leftovers = manager.uninstall(rec, delete_prefix)
            self.win.notify(
                f"Could not remove everything, delete by hand: {', '.join(leftovers)}"
                if leftovers
                else f"{rec.name} uninstalled"
            )
            self.win.refresh_items()
            self.rebuild()

        def after_files() -> None:
            if rec.prefix and Path(rec.prefix).exists():
                self.win.ask(
                    "Also delete the local Wine prefix?\nIt may contain your save files. This cannot be undone.",
                    lambda: remove(True),
                    on_no=lambda: remove(False),
                )
            else:
                remove(False)

        self.win.ask(f"Delete the game files of {rec.name} and its shortcuts?", after_files)


ROLE_COVER, ROLE_PROGRESS, ROLE_INSTALLED = Qt.UserRole + 1, Qt.UserRole + 2, Qt.UserRole + 3
BADGE_SIZE = 56


def paint_installed_badge(painter: QPainter, cover: QRect) -> None:
    """Blue gradient corner with a check, bottom-right of the cover."""
    right, bottom = cover.right() + 1, cover.bottom() + 1
    corner = QPolygonF(
        [QPointF(right, bottom - BADGE_SIZE), QPointF(right, bottom), QPointF(right - BADGE_SIZE, bottom)]
    )
    grad = QLinearGradient(right - BADGE_SIZE, bottom, right, bottom - BADGE_SIZE)
    grad.setColorAt(0.0, QColor("#3c64c4"))
    grad.setColorAt(0.5, QColor("#4c8dff"))
    grad.setColorAt(1.0, QColor("#8fb4ff"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(grad)
    painter.drawPolygon(corner)
    check = QPainterPath()
    check.moveTo(right - 25, bottom - 14)
    check.lineTo(right - 19, bottom - 8)
    check.lineTo(right - 8, bottom - 20)
    pen = QPen(QColor("white"), 3.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(check)



class CoverDelegate(QStyledItemDelegate):
    """Cover art with an install progress bar along its bottom edge, then the title."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(COVER_SIZE.width() + 40, COVER_SIZE.height() + 80)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        cell = option.rect.adjusted(6, 6, -6, -6)
        if option.state & QStyle.State_Selected:
            painter.setPen(QColor("#4c8dff"))
            painter.setBrush(QColor("#1e2733"))
            painter.drawRoundedRect(cell, 10, 10)
        cover = QRect(cell.left() + (cell.width() - COVER_SIZE.width()) // 2, cell.top() + 8, COVER_SIZE.width(), COVER_SIZE.height())
        painter.fillRect(cover, QColor("#1e232b"))
        pix = index.data(ROLE_COVER)
        if pix:
            painter.drawPixmap(cover.left() + (cover.width() - pix.width()) // 2, cover.top() + (cover.height() - pix.height()) // 2, pix)
        progress = index.data(ROLE_PROGRESS)
        if progress is not None:
            bar = QRect(cover.left(), cover.bottom() - 9, cover.width(), 10)
            painter.fillRect(bar, QColor(0, 0, 0, 170))
            painter.fillRect(bar.adjusted(0, 0, int((progress - 1000) * bar.width() / 1000), 0), QColor("#2f6fed"))
        if index.data(ROLE_INSTALLED):
            painter.setClipRect(cover)
            paint_installed_badge(painter, cover)
            painter.setClipping(False)
        painter.setPen(QColor("#e8eaed"))
        text = QRect(cell.left() + 4, cover.bottom() + 6, cell.width() - 8, cell.bottom() - cover.bottom() - 6)
        painter.drawText(text, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, index.data(Qt.DisplayRole))
        painter.restore()


class LibraryPage(Page):
    title = "Library"
    searchable = True

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.active = QLabel()
        self.active.setVisible(False)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setItemDelegate(CoverDelegate(self.grid))
        self.grid.setGridSize(QSize(COVER_SIZE.width() + 40, COVER_SIZE.height() + 80))
        self.grid.itemActivated.connect(win.open_game)
        self.items: dict[int, QListWidgetItem] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(self.active)
        lay.addWidget(self.grid, 1)

    def focus_default(self) -> None:
        self.grid.setFocus()

    def _progress(self, gid: int) -> int | None:
        """0..1000 while installing (0 until the size is known), None otherwise."""
        if gid not in self.win.app.installs:
            return None
        written, total, _ = self.win.app.progress.get(gid, (0, 0, ""))
        return 1000 * written // total if total else 0

    def label(self, gid: int, rec: InstalledGame | None) -> str:
        name = self.win.app.games[gid]["name"]
        badge = {"awaiting_executable": "Setup needed", "installing": "Partial"}.get(
            rec.state if rec else "", ""
        )
        progress = self._progress(gid)
        if progress is not None:
            badge = f"Installing {progress // 10}%"
        return name + (f"\n[{badge}]" if badge else "")

    def update_label(self, gid: int) -> None:
        item = self.items.get(gid)
        if item is not None:
            rec = load_library().get(gid)
            item.setText(self.label(gid, rec))
            item.setData(ROLE_PROGRESS, self._progress(gid))
            item.setData(ROLE_INSTALLED, bool(rec and rec.state == "installed"))
        self.update_active()

    def update_active(self) -> None:
        running = [gid for gid in self.win.app.installs if gid in self.win.app.games]
        if not running:
            self.active.setVisible(False)
            return
        parts = [f"{self.win.app.games[g]['name']} {(self._progress(g) or 0) // 10}%" for g in running]
        self.active.setText(f"{len(running)} install{'s' if len(running) > 1 else ''} in progress: " + ", ".join(parts))
        self.active.setVisible(True)

    def populate(self, needle: str) -> None:
        current = self.grid.currentItem().data(Qt.UserRole) if self.grid.currentItem() else None
        lib = load_library()
        self.grid.clear()
        self.items = {}
        for gid, g in sorted(self.win.app.games.items(), key=lambda kv: kv[1]["name"].lower()):
            if needle and needle not in g["name"].lower():
                continue
            item = QListWidgetItem(self.label(gid, lib.get(gid)))
            item.setData(Qt.UserRole, gid)
            item.setData(ROLE_COVER, self.win.covers.get(gid))
            item.setData(ROLE_PROGRESS, self._progress(gid))
            item.setData(ROLE_INSTALLED, bool(lib.get(gid) and lib[gid].state == "installed"))
            self.grid.addItem(item)
            self.items[gid] = item
            if gid == current:
                self.grid.setCurrentItem(item)
        if not self.grid.currentItem() and self.grid.count():
            self.grid.setCurrentRow(0)
        self.update_active()


class MainWindow(QMainWindow):
    """One window, a stack of pages. Back (button, Esc or gamepad B) pops the
    stack; installs run on worker threads, so they continue while navigating."""

    def __init__(self, app: App):
        super().__init__()
        self.app = app
        self.setWindowTitle("MOG")
        self.resize(1280, 800)
        self.back_btn = QPushButton("< Back")
        self.back_btn.clicked.connect(self.back)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self.refresh_items)
        self.reload_btn, self.settings_btn = QPushButton("Refresh"), QPushButton("Settings")
        self.reload_btn.clicked.connect(app.refresh)
        self.settings_btn.clicked.connect(self.open_settings)
        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addWidget(self.title)
        top.addWidget(self.search, 1)
        top.addStretch(1)
        top.addWidget(self.reload_btn)
        top.addWidget(self.settings_btn)
        self.stack = QStackedWidget()
        self.library = LibraryPage(self)
        self.stack.addWidget(self.library)
        self.history: list[Page] = [self.library]
        self.game_pages: dict[int, GamePage] = {}
        self.status = QLabel()
        self.status.setWordWrap(True)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.status)
        self.setCentralWidget(central)
        self.covers: dict[int, QPixmap] = {}
        b = app.bridge
        b.games.connect(self.set_games)
        b.error.connect(self.notify)
        b.cover.connect(self.set_cover)
        b.finished.connect(lambda *_: self.refresh_items())
        b.progress.connect(lambda gid, *_: self.library.update_label(gid))
        b.pad.connect(self.on_pad)
        self.pad_stop = gamepad.start(b.pad.emit)
        self._show(self.library)

    def closeEvent(self, e):
        self.pad_stop.set()
        super().closeEvent(e)

    def current_page(self) -> Page:
        return self.history[-1]

    def _show(self, page: Page) -> None:
        self.stack.setCurrentWidget(page)
        on_library = page is self.library
        self.back_btn.setVisible(not on_library)
        self.search.setVisible(page.searchable)
        self.reload_btn.setVisible(on_library)
        self.settings_btn.setVisible(on_library)
        self.title.setText("MOG" if on_library else page.title)
        page.focus_default()

    def push(self, page: Page) -> None:
        if self.stack.indexOf(page) < 0:
            self.stack.addWidget(page)
        self.history.append(page)
        self._show(page)

    def back(self) -> None:
        if len(self.history) <= 1:
            return
        page = self.history.pop()
        if not isinstance(page, GamePage):
            self.stack.removeWidget(page)
            page.deleteLater()
        self._show(self.history[-1])
        self.refresh_items()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.back()
        else:
            super().keyPressEvent(e)

    def notify(self, text: str) -> None:
        self.status.setText(text)

    def ask(self, text: str, on_yes, on_no=None) -> None:
        def no():
            if on_no:
                on_no()

        page = ConfirmPage(self, text, on_yes)
        page.no.clicked.connect(no)
        self.push(page)

    def on_pad(self, name: str) -> None:
        if name == gamepad.MENU:
            if self.current_page() is self.library:
                self.open_settings()
            return
        target = QApplication.focusWidget() or self.current_page()
        key = _KEYS[name]
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.postEvent(target, QKeyEvent(kind, key, Qt.NoModifier))

    def open_settings(self) -> None:
        self.push(SettingsPage(self))

    def set_games(self, games: list) -> None:
        self.app.games = {g["id"]: g for g in games}
        self.notify(f"{len(games)} games")
        self.refresh_items()

    def set_cover(self, gid: int, blob: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(blob):
            self.covers[gid] = pix.scaled(COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item = self.library.items.get(gid)
            if item is not None:
                item.setData(ROLE_COVER, self.covers[gid])
                self.library.grid.viewport().update()

    def refresh_items(self) -> None:
        self.library.populate(self.search.text().lower())

    def open_game(self, item: QListWidgetItem) -> None:
        gid = item.data(Qt.UserRole)
        game = self.app.games.get(gid)
        if game:
            if gid not in self.game_pages:
                self.game_pages[gid] = GamePage(self, game)
            self.push(self.game_pages[gid])


def run_gui() -> int:
    qapp = QApplication(sys.argv[:1])
    qapp.setStyleSheet(STYLE)
    enter_filter = ActivateOnEnter()
    qapp.installEventFilter(enter_filter)
    app = App()
    win = MainWindow(app)
    if is_deck():
        win.showFullScreen()
    else:
        win.show()
    if app.settings.configured:
        app.refresh()
    else:
        win.open_settings()
    return qapp.exec()
