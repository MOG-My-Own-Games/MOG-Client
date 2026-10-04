"""Qt GUI: library grid, per-game install/play dialog, settings. Fully
operable from a gamepad (D-pad/stick = arrows, A = Enter, B = Esc)."""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QIcon,
    QKeySequence,
    QPixmap,
    QShortcut,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
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
    QSizePolicy,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from mog_client import manager, steam, trace, updater
from mog_client.api import fetch_url, fmt_bytes
from mog_client.grouping import ADDONS_ONLY, SAVES_ONLY, Group, corner_state, group_games
from mog_client.progress import RateMeter, format_eta
from mog_client.stopactions import StopActions
from mog_client.config import (
    InstalledGame,
    Settings,
    data_dir,
    load_library,
    load_settings,
    save_settings,
)
from mog_client.gui import gamepad, keyboard, osk
from mog_client.gui.saves_ui import SaveSync
from mog_client.saves.sync import enabled as sync_enabled
from mog_client.gui.widgets import ACCENT_HOVER_STOPS, Toggle, accent_gradient, accent_qss, bell_icon
from mog_client.launcher import (
    available_launchers,
    detect_launcher,
    launch,
    launch_failure,
    launcher_label,
    list_executables,
)
from mog_client.scrape import artwork_urls, metadata_lines, screenshot_urls
from mog_client.version import __version__

ASSETS = Path(__file__).parent / "assets"


def asset_pixmap(name: str, height: int) -> QPixmap:
    return QPixmap(str(ASSETS / name)).scaledToHeight(height, Qt.SmoothTransformation)


STYLE = """
* { font-size: 18px; }
QMainWindow { background: #14171c; color: #e8eaed; }
QLabel { color: #e8eaed; }
QLineEdit, QComboBox, QPlainTextEdit { background: #1e232b; color: #e8eaed; border: 2px solid #2c333d; border-radius: 6px; padding: 8px; }
QPushButton { background: {accent}; color: white; border: 2px solid transparent; border-radius: 8px; padding: 12px 22px; max-width: 460px; }
QPushButton:hover { background: {accent_hover}; }
QPushButton:focus { border-color: white; }
QPushButton#key { padding: 2px; font-size: 17px; border-radius: 6px; max-width: 16777215px; }
QPushButton[danger="true"] { background: #e2574c; }
QPushButton[danger="true"]:hover { background: #c94439; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { color: #e8eaed; border: 3px solid transparent; border-radius: 10px; padding: 6px; }
QListWidget::item:selected { border-color: #4c8dff; background: #1e2733; }
*:focus { border-color: #4c8dff; }
QProgressBar { background: #1e232b; border: none; border-radius: 6px; height: 18px; text-align: center; color: white; }
QProgressBar::chunk { background: {accent}; border-radius: 6px; }
""".replace("{accent_hover}", accent_qss(ACCENT_HOVER_STOPS)).replace("{accent}", accent_qss())

COVER_SIZE = QSize(200, 270)
OPTIONS_BUTTON_WIDTH = 440
SIDEBAR_WIDTH = 280
IMAGE_WORKERS = 6
PAD_ICON_HEIGHT = 28


def keyboard_legend(typing: bool = False, inbox: bool = False) -> str:
    """The same guide as the controller's, for the keyboard."""
    if typing:
        items = (("Type", "Enter text"), ("Backspace", "Delete"), ("Esc", "Cancel"))
    elif inbox:
        items = (("\u2191\u2193", "Move"), ("Enter", "Open"), ("Del", "Delete"), ("Esc", "Back"))
    else:
        items = (
            ("\u2191\u2193\u2190\u2192", "Move"),
            ("Enter", "Select"),
            ("Esc", "Back"),
            ("F5", "Refresh"),
            ("Ctrl+F", "Search"),
            ("Ctrl+B", "Sidebar"),
            ("Ctrl+N", "Notifications"),
            ("Ctrl+,", "Settings"),
            ("Ctrl+Q", "Quit"),
        )
    return "&nbsp;&nbsp;&nbsp;&nbsp;".join(f"<b>{keys}</b> {label}" for keys, label in items)


def pad_legend(family: str, typing: bool = False, inbox: bool = False) -> str:
    def icon(button: str) -> str:
        path = (ASSETS / "pad" / family / f"{button}.png").as_posix()
        return f'<img src="{path}" height="{PAD_ICON_HEIGHT}" style="vertical-align: middle;">'

    def glyph(function: str) -> str:
        return icon(gamepad.BUTTON_FOR[function])

    gap = "&nbsp;&nbsp;&nbsp;&nbsp;"
    if typing:
        items = (
            (icon(gamepad.DPAD), "Move"),
            (glyph(gamepad.ACCEPT), "Type"),
            (glyph(gamepad.REFRESH), "Delete"),
            (glyph(gamepad.SEARCH), "Space"),
            (glyph(gamepad.PAGE_PREV) + glyph(gamepad.PAGE_NEXT), "Cursor"),
            (glyph(gamepad.TRIGGER_R), "Done"),
            (glyph(gamepad.BACK), "Cancel"),
        )
        return gap.join(f"{glyphs} {label}" for glyphs, label in items)
    if inbox:
        items = (
            (icon(gamepad.DPAD), "Move"),
            (glyph(gamepad.ACCEPT), "Open"),
            (glyph(gamepad.REFRESH), "Delete"),
            (glyph(gamepad.BACK), "Back"),
        )
        return gap.join(f"{glyphs} {label}" for glyphs, label in items)
    items = (
        (icon(gamepad.DPAD), "Move"),
        (glyph(gamepad.ACCEPT), "Select"),
        (glyph(gamepad.BACK), "Back"),
        (glyph(gamepad.PAGE_PREV) + glyph(gamepad.PAGE_NEXT), "Switch focus"),
        (glyph(gamepad.REFRESH), "Refresh"),
        (glyph(gamepad.SEARCH), "Search"),
        (glyph(gamepad.TRIGGER_L), "Sidebar"),
        (glyph(gamepad.MENU), "Settings"),
        (f"{glyph(gamepad.MENU)}+{icon(gamepad.SELECT)}", "Quit"),
    )
    return gap.join(f"{glyphs} {label}" for glyphs, label in items)
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
    pad_connected = Signal(bool, str)  # connected, controller family
    notifications = Signal(object)  # {"notifications": [...], "unread": n}
    libraries = Signal(list)
    installers = Signal(int, object, str)  # game id, candidates (None on error), error
    game_size = Signal(int, int)  # game id, bytes on the server
    update_checked = Signal(object, str, bool)  # UpdateInfo or None, error text, user asked
    update_progress = Signal(int, int)  # written, total
    update_ready = Signal(str)  # path of the replaced build, to relaunch
    update_failed = Signal(str)
    call = Signal(object)  # a callable to run on the GUI thread


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
        self.group_of: dict[int, Group] = {}  # any game id -> the versions of its title
        self.stopping: set[int] = set()  # installs asked to stop, still winding down
        self.after_stop = StopActions()  # runs once that install has stopped

    def client(self):
        return manager.make_client(self.settings)

    def run_bg(self, fn, on_error=None) -> None:
        def wrapper():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - surfaced in the UI
                (on_error or self.bridge.error.emit)(str(e))

        threading.Thread(target=wrapper, daemon=True).start()

    def check_update(self, manual: bool = False) -> None:
        def work():
            try:
                self.bridge.update_checked.emit(updater.check_for_update(), "", manual)
            except Exception as e:  # noqa: BLE001 - a failed check must never block startup
                self.bridge.update_checked.emit(None, str(e), manual)

        threading.Thread(target=work, daemon=True).start()

    def install_update(self, info) -> None:
        def work():
            try:
                target = updater.apply_update(info, self.bridge.update_progress.emit)
            except Exception as e:  # noqa: BLE001 - surfaced in the UI
                self.bridge.update_failed.emit(str(e))
                return
            self.bridge.update_ready.emit(str(target))

        threading.Thread(target=work, daemon=True).start()

    def poll_notifications(self) -> None:
        if not self.settings.configured:
            return

        def work():
            try:
                self.bridge.notifications.emit(self.client().notifications())
            except Exception:  # noqa: BLE001, S110 - the inbox is best effort, a failed poll just retries
                pass

        threading.Thread(target=work, daemon=True).start()

    def refresh(self) -> None:
        def work():
            client = self.client()
            try:
                self.bridge.libraries.emit(client.list_libraries())
            except RuntimeError:
                pass  # an older server without the endpoint just has no library filter
            games = client.list_games()
            self.bridge.games.emit(games)
            with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
                list(pool.map(lambda g: self._load_cover(g, client), games))

        self.run_bg(work)

    def _image(self, cache: Path, server_path: str, url: str, client) -> bytes | None:
        """Cached image: from the MOG-Server first (it caches and shrinks them, so a
        remote client does not depend on the provider CDN), else straight from `url`."""
        if not cache.is_file():
            blob = client.get_image(server_path)
            if blob is None:
                try:
                    blob = fetch_url(url)
                except RuntimeError:
                    return None
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(blob)
        return cache.read_bytes()

    def _load_cover(self, game: dict, client) -> None:
        url = artwork_urls(game).get("portrait")
        if not url:
            return
        # The URL is part of the name, so a re-scraped cover is not shadowed by the old file.
        cache = data_dir() / "covers" / f"{game['id']}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.img"
        blob = self._image(cache, f"/api/games/{game['id']}/cover", url, client)
        if blob is not None:
            self.bridge.cover.emit(game["id"], blob)

    def fetch_images(self, urls: list[str], game_id: int) -> None:
        def work():
            client = self.client()

            def one(item: tuple[int, str]) -> None:
                index, url = item
                cache = data_dir() / "shots" / hashlib.sha1(url.encode()).hexdigest()
                blob = self._image(cache, f"/api/games/{game_id}/screenshots/{index}", url, client)
                if blob is not None:
                    self.bridge.image.emit(url, blob)

            with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
                list(pool.map(one, enumerate(urls)))

        self.run_bg(work, on_error=lambda _m: None)

    def set_games(self, games: list[dict]) -> None:
        self.games = {g["id"]: g for g in games}
        self.group_of = {g["id"]: group for group in group_games(games) for g in group.members}

    def active_version(self, group: Group) -> dict:
        """The version to show for a title: one being installed, else one with local files, else the first."""
        local = load_library()
        for member in group.members:
            if member["id"] in self.installs:
                return member
        for member in group.members:
            if member["id"] in local:
                return member
        return group.game

    def load_size(self, game_id: int) -> None:
        def work():
            size = self.client().game_size(game_id)
            if size is not None:
                self.bridge.game_size.emit(game_id, size)

        threading.Thread(target=work, daemon=True).start()

    def load_installers(self, game_id: int) -> None:
        def work():
            try:
                data = self.client().candidates(game_id)
                self.bridge.installers.emit(game_id, data.get("candidates", []), "")
            except Exception as e:  # noqa: BLE001 - shown in the picker
                self.bridge.installers.emit(game_id, None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def start_install(self, game: dict, installer: dict | None = None) -> None:
        gid = game["id"]
        if gid in self.installs:
            return
        stop = threading.Event()
        self.after_stop.forget(gid)
        self.installs[gid] = stop
        bridge = self.bridge

        server_state = {"label": ""}
        meter = RateMeter()
        meter_lock = threading.Lock()  # several files report at once

        def report(written: int, total: int) -> None:
            with meter_lock:
                meter.add(time.monotonic(), written)
                speed, eta = meter.speed(), meter.eta(written, total)
            label = f"Downloading {fmt_bytes(written)} / {fmt_bytes(total)}"
            if speed:
                label += f"  {fmt_bytes(speed)}/s"
            if eta is not None:
                # The total grows while the server is still producing files, so this is a floor.
                label += f"  ETA {format_eta(eta)}"
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
                    report, installer,
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)
            self.installs.pop(gid, None)
            self.progress.pop(gid, None)
            self.stopping.discard(gid)
            follow_up = self.after_stop.take(gid)
            if follow_up:
                try:
                    follow_up()
                except Exception as e:  # noqa: BLE001
                    err = err or str(e)
            trace.event(f"install of game {gid} ended: error={err!r}, state={getattr(load_library().get(gid), 'state', None)}")
            bridge.finished.emit(gid, err)

        threading.Thread(target=work, daemon=True).start()

    def pause_install(self, gid: int) -> None:
        if gid in self.installs:
            self.stopping.add(gid)
            self.installs[gid].set()

    def cancel_local_install(self, gid: int) -> None:
        """Stop downloading and delete what was downloaded; the server's install carries on."""

        def discard() -> None:
            rec = load_library().get(gid)
            # Only a partial download is thrown away: an install that finished before the stop
            # reached it is the user's now, and is removed from Options if they still want that.
            if rec and rec.state == "installing":
                manager.uninstall(rec)
                trace.event(f"cancel local: discarded the partial download of game {gid}")
            elif rec:
                self.bridge.error.emit(f"{rec.name} had already finished installing, so it was kept")

        if self.after_stop.register(gid, discard, running=gid in self.installs):
            self.pause_install(gid)

    def cancel_server_install(self, gid: int) -> None:
        """Stop the installer on the server; what was downloaded here is kept for a later resume."""
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


def _centered_row(*widgets) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    for w in widgets:
        row.addWidget(w)
    row.addStretch()
    return row


def _row(*widgets, stretch_first: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    if stretch_first:
        row.addStretch()
    for i, w in enumerate(widgets):
        row.addWidget(w, 1 if i == 0 and not stretch_first else 0)
    return row


class ConfirmPage(Page):
    """Inline yes/no question; "No" holds the initial focus so a stray A press is safe."""

    def __init__(self, win: "MainWindow", text: str, on_yes, title: str = "Are you sure?", danger: bool = False):
        super().__init__()
        self.title = title
        label = QLabel(text)
        label.setWordWrap(True)
        self.no, yes = QPushButton("No"), QPushButton("Yes")
        yes.setProperty("danger", danger)
        self.no.clicked.connect(win.back)
        yes.clicked.connect(lambda: (win.back(), on_yes()))
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(label)
        lay.addLayout(_centered_row(self.no, yes))
        lay.addStretch()

    def focus_default(self) -> None:
        self.no.setFocus()


class UpdatePage(Page):
    """Downloads and installs a new build, then restarts into it."""

    title = "Updating"

    def __init__(self, win: "MainWindow", info):
        super().__init__()
        self.win = win
        self.label = QLabel(f"Downloading MOG {info.version}...")
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addStretch()
        b = win.app.bridge
        b.update_progress.connect(self.on_progress)
        b.update_ready.connect(self.on_ready)
        b.update_failed.connect(self.on_failed)
        win.app.install_update(info)

    def on_progress(self, written: int, total: int) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(written)

    def on_ready(self, target: str) -> None:
        self.label.setText("Restarting...")
        updater.relaunch(Path(target))

    def on_failed(self, error: str) -> None:
        self.bar.setRange(0, 1)
        self.label.setText(f"Update failed: {error}")


class NotificationsPage(Page):
    """The server's notifications for this user (e.g. an auto mode install that got stuck)."""

    title = "Notifications"

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.list = QListWidget()
        self.list.itemActivated.connect(self.open_item)
        self.empty = QLabel("No notifications.")
        self.empty.setAlignment(Qt.AlignCenter)
        mark_all = QPushButton("Mark all read")
        mark_all.clicked.connect(self.mark_all_read)
        clear = QPushButton("Clear all")
        clear.setProperty("danger", True)
        clear.clicked.connect(
            lambda: win.ask("Delete every notification?", self.clear_all, danger=True)
        )
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty)
        lay.addLayout(_row(mark_all, clear))
        self.populate()

    def focus_default(self) -> None:
        self.list.setFocus()

    def populate(self) -> None:
        row = self.list.currentRow()
        self.list.clear()
        for n in self.win.notifications:
            text = ("" if n["read"] else "\u25cf ") + n["title"]
            if n.get("body"):
                text += "\n    " + n["body"]
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, n["id"])
            self.list.addItem(item)
        self.empty.setVisible(self.list.count() == 0)
        if self.list.count():
            self.list.setCurrentRow(min(max(row, 0), self.list.count() - 1))

    def _notification(self, item: QListWidgetItem | None) -> dict | None:
        if item is None:
            return None
        return next((n for n in self.win.notifications if n["id"] == item.data(Qt.UserRole)), None)

    def open_item(self, item: QListWidgetItem) -> None:
        n = self._notification(item)
        if n is None:
            return
        if not n["read"]:
            self.win.mark_read(n["id"])
        if n.get("game_id") in self.win.app.games:
            self.win.show_game(n["game_id"])

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Delete:
            self.delete_current()
        else:
            super().keyPressEvent(e)

    def delete_current(self) -> None:
        n = self._notification(self.list.currentItem())
        if n is not None:
            self.win.delete_notification(n["id"])

    def mark_all_read(self) -> None:
        self.win.mark_read(None)

    def clear_all(self) -> None:
        self.win.delete_notification(None)


class OptionsPage(Page):
    """The less common actions of a game: launch engine, shortcuts, and deleting it here or on the server."""

    title = "Options"

    def __init__(self, win: "MainWindow", page: "GamePage", rec: InstalledGame | None):
        super().__init__()
        self.win = win
        self.first: QPushButton | None = None
        lay = QVBoxLayout(self)
        lay.addStretch()

        def add(text: str, action, danger: bool = False) -> None:
            button = QPushButton(text)
            button.setProperty("danger", danger)
            button.setFixedWidth(OPTIONS_BUTTON_WIDTH)  # one width for all, centered
            button.clicked.connect(lambda: (win.back(), action()))
            lay.addWidget(button, 0, Qt.AlignHCenter)
            self.first = self.first or button

        if rec and rec.state == "installed":
            if sys.platform != "win32":
                engine = f"{launcher_label(rec.launcher)} (this game)" if rec.launcher != "auto" else "default"
                add(f"Launch engine: {engine}", lambda: page.choose_launcher(rec))
            add("Shortcuts / executable", lambda: page.choose_executable(rec))
            add("Regenerate shortcuts", lambda: page.regenerate(rec))
            on = sync_enabled(rec, win.app.settings)
            add(f"Save sync: {'on' if on else 'off'} (this game)", lambda: page.toggle_save_sync(rec))
            if on:
                add("Back up saves now", lambda: win.saves.backup_now(rec))
                add("Restore a saved version...", lambda: win.saves.restore_pick(rec))
                if sys.platform != "win32":
                    add("Where is this game's prefix?", lambda: win.saves.choose_prefix(rec))
        if rec:
            partial = rec.state not in ("installed", "awaiting_executable")
            add("Discard the partial download" if partial else "Uninstall from this device", lambda: page.uninstall(rec), True)
            add(
                "Discard it and delete the server cache" if partial else "Uninstall and delete the server cache",
                lambda: page.uninstall(rec, and_server_cache=True),
                True,
            )
        add("Delete the install cache on the server", page.delete_server_cache, True)
        lay.addStretch()

    def focus_default(self) -> None:
        if self.first:
            self.first.setFocus()


class ChoicePage(Page):
    """A list to pick one answer from; Esc or Back leaves without answering."""

    def __init__(self, win: "MainWindow", title: str, text: str, options: list, on_choose):
        super().__init__()
        self.title, self.win, self.on_choose = title, win, on_choose
        note = QLabel(text)
        note.setWordWrap(True)
        self.list = QListWidget()
        for label, value in options:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, value)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.choose)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)

    def focus_default(self) -> None:
        self.list.setFocus()

    def choose(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.UserRole)
        self.win.back()
        self.on_choose(value)


class ChecklistPage(Page):
    """A list to tick any number of entries from, then Done."""

    def __init__(self, win: "MainWindow", title: str, text: str, items: list, on_done):
        super().__init__()
        self.title, self.win, self.on_done = title, win, on_done
        note = QLabel(text)
        note.setWordWrap(True)
        self.list = QListWidget()
        for label, value in items:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.toggle)
        done = QPushButton("Done")
        done.clicked.connect(self.finish)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(_row(done))

    def focus_default(self) -> None:
        self.list.setFocus()

    def toggle(self, item: QListWidgetItem) -> None:
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def finish(self) -> None:
        picked = [
            self.list.item(i).data(Qt.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]
        self.win.back()
        self.on_done(picked)


class LauncherPage(Page):
    """Pick the engine that starts one game; its desktop entry and Steam shortcut follow."""

    title = "Launch engine"

    def __init__(self, win: "MainWindow", rec: InstalledGame, on_done):
        super().__init__()
        self.on_done = on_done
        self.win = win
        self.list = QListWidget()
        default = detect_launcher(win.app.settings.launcher)
        default_label = launcher_label(default) if default else "none found"
        entries = [("auto", f"Default (the launcher in Settings: {default_label})")]
        entries += [(name, launcher_label(name)) for name in available_launchers()]
        for key, text in entries:
            item = QListWidgetItem(text + ("   \u2713" if key == rec.launcher else ""))
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
            if key == rec.launcher:
                self.list.setCurrentItem(item)
        if not self.list.currentItem():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.choose)
        note = QLabel("The game's desktop entry and Steam shortcut are updated to use it; Steam may need a restart.")
        note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addWidget(note)

    def focus_default(self) -> None:
        self.list.setFocus()

    def choose(self, item: QListWidgetItem) -> None:
        self.win.back()
        self.on_done(item.data(Qt.UserRole))


class KeyButton(QPushButton):
    """A key; the D-pad moves over the grid instead of down the tab order."""

    def __init__(self, page: "KeyboardPage", key: str, row: int, col: int):
        super().__init__(keyboard.LABELS.get(key, key))
        self.page, self.key, self.pos_in_grid = page, key, (row, col)
        self.setObjectName("key")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(46)
        self.clicked.connect(lambda: page.press(key))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        steps = {Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0), Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1)}
        if e.key() in steps:
            self.page.move(self.pos_in_grid, *steps[e.key()])
        else:
            super().keyPressEvent(e)


class KeyboardPage(Page):
    """In-app on-screen keyboard that types into `target`; A presses a key, B cancels."""

    title = "Type"

    def __init__(self, win: "MainWindow", target: QWidget):
        super().__init__()
        self.win, self.target = win, target
        self.password = isinstance(target, QLineEdit) and target.echoMode() != QLineEdit.Normal
        text = target.text() if isinstance(target, QLineEdit) else target.toPlainText()
        self.buffer = keyboard.TextBuffer(text)
        self.display = QLabel()
        self.display.setStyleSheet("font-size: 22px; padding: 10px; background: #1e232b; border-radius: 8px;")
        self.display.setWordWrap(True)
        board = QWidget()
        rows = QVBoxLayout(board)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.keys: list[list[KeyButton]] = []
        for r, row in enumerate(keyboard.ROWS):
            line = QHBoxLayout()
            line.setSpacing(6)
            buttons = []
            for c, key in enumerate(row):
                btn = KeyButton(self, key, r, c)
                line.addWidget(btn, keyboard.WIDE.get(key, 1))
                buttons.append(btn)
            self.keys.append(buttons)
            rows.addLayout(line)
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(self.display)
        lay.addWidget(board)
        self._refresh()

    def focus_default(self) -> None:
        self.keys[1][0].setFocus()

    def _refresh(self) -> None:
        shown = "\u2022" * len(self.buffer.text) if self.password else self.buffer.text
        self.display.setText(shown[: self.buffer.pos] + "|" + shown[self.buffer.pos :])
        for row in self.keys:
            for btn in row:
                if len(btn.key) == 1:
                    btn.setText(btn.key.upper() if self.buffer.shift else btn.key)

    def press(self, key: str) -> None:
        result = self.buffer.press(key)
        self._refresh()
        if result is None:
            return
        self.win.back()
        if result == keyboard.DONE:
            if isinstance(self.target, QLineEdit):
                self.target.setText(self.buffer.text)
                self.target.setCursorPosition(self.buffer.pos)
            else:
                self.target.setPlainText(self.buffer.text)
        self.target.setFocus()

    def move_cursor(self, delta: int) -> None:
        self.buffer.move(delta)
        self._refresh()

    def move(self, at: tuple[int, int], d_row: int, d_col: int) -> None:
        r, c = keyboard.neighbour(*at, d_row, d_col)
        self.keys[r][c].setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Backspace:
            self.press(keyboard.BACKSPACE)
        elif e.text() and e.text().isprintable():
            self.buffer.insert(e.text())
            self._refresh()
        else:
            super().keyPressEvent(e)


def version_label(game: dict, libraries: list[dict]) -> str:
    """A version is told apart by its folder or file name (and library, when there are several)."""
    name = game["fs_name"]
    if len(libraries) > 1:
        lib = next((lib["name"] for lib in libraries if lib["id"] == game.get("library_id")), "")
        if lib:
            name += f" [{lib}]"
    return name


class InstallerPickerPage(Page):
    """The installers of every version of a title, each under its version, to pick which one to run."""

    title = "Choose an installer"

    def __init__(self, win: "MainWindow", page: "GamePage", group: Group):
        super().__init__()
        self.win, self.page, self.group = win, page, group
        self.found: dict[int, list[dict] | str] = {}
        self.list = QListWidget()
        self.list.itemActivated.connect(self.choose)
        self.status = QLabel("Looking for installers...")
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.list, 1)
        win.app.bridge.installers.connect(self.on_installers)
        for version in group.versions:
            win.app.load_installers(version["id"])
        self.render()

    def focus_default(self) -> None:
        self.list.setFocus()

    def on_installers(self, game_id: int, candidates, error: str) -> None:
        if game_id in {v["id"] for v in self.group.versions}:
            self.found[game_id] = candidates if candidates is not None else error
            self.render()

    def render(self) -> None:
        self.list.clear()
        for version in self.group.versions:
            header = QListWidgetItem(version_label(version, self.win.library.libraries))
            header.setFlags(Qt.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.list.addItem(header)
            entry = QListWidgetItem("    Let the server choose")
            entry.setData(Qt.UserRole, (version, None))
            self.list.addItem(entry)
            found = self.found.get(version["id"])
            if found is None:
                self.list.addItem(self._note("    Looking..."))
            elif isinstance(found, str):
                self.list.addItem(self._note(f"    Could not list installers: {found}"))
            for cand in found if isinstance(found, list) else []:
                tag = "" if cand.get("category", "game") == "game" else f"[{cand['category'].upper()}] "
                row = QListWidgetItem(f"    {tag}{cand['path']}  ({fmt_bytes(cand['file_size_bytes'])}, {cand['kind']})")
                row.setData(Qt.UserRole, (version, cand))
                self.list.addItem(row)
        done = len(self.found) == len(self.group.versions)
        self.status.setText("Pick the installer to run:" if done else "Looking for installers...")
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole):
                self.list.setCurrentRow(i)
                break

    @staticmethod
    def _note(text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        return item

    def choose(self, item: QListWidgetItem) -> None:
        picked = item.data(Qt.UserRole)
        if not picked:
            return
        version, installer = picked
        self.win.back()
        self.page.start_with(version, installer)


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
        hint = (
            "Default is whatever opens .exe files on this system, else Faugus when installed, "
            "else the system's umu, Proton or Wine. None of them is given a prefix by MOG."
        )
        if not available:
            hint = "No launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine."
        mascotte = QLabel()
        mascotte.setPixmap(asset_pixmap("mascotte.png", 160))
        mascotte.setAlignment(Qt.AlignCenter)
        form = QFormLayout()
        form.addRow("Server URL", self.base)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        browse_games = QPushButton("Browse...")
        browse_games.clicked.connect(self.browse_games_dir)
        form.addRow("Games folder", _row(self.games_dir, browse_games, stretch_first=False))
        form.addRow("Launcher", self.launcher)
        form.addRow("Version", QLabel(__version__))
        self.sync_saves_box = Toggle("Back up game saves to the server")
        self.sync_saves_box.setChecked(s.sync_saves)
        form.addRow("Saves", self.sync_saves_box)
        self.check_updates_box = Toggle("Check for updates at startup")
        self.check_updates_box.setChecked(s.check_updates)
        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        if updater.supported():
            check_now = QPushButton("Check now")
            check_now.clicked.connect(self.check_updates)
            form.addRow("Updates", _row(self.check_updates_box, check_now, stretch_first=False))
            form.addRow("", self.update_status)
            win.app.bridge.update_checked.connect(self.on_checked)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        lay = QVBoxLayout(self)
        lay.addWidget(mascotte)
        lay.addLayout(form)
        note = QLabel(hint)
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()
        lay.addLayout(_row(save))

    def browse_games_dir(self) -> None:
        start = Path(self.games_dir.text().strip() or str(self.win.app.settings.games_path))
        while not start.is_dir() and start.parent != start:
            start = start.parent
        self.win.push(BrowsePage(self.win, start, self.games_dir.setText, folders=True))

    def check_updates(self) -> None:
        self.update_status.setText("Checking...")
        self.win.app.check_update(manual=True)

    def on_checked(self, info, error: str, manual: bool) -> None:
        if not manual:
            return
        if error:
            self.update_status.setText(f"Could not check: {error}")
        elif info is None:
            self.update_status.setText("You are up to date.")

    def focus_default(self) -> None:
        (self.base if not self.base.text() else self.launcher).setFocus()

    def save(self) -> None:
        self.win.app.settings = Settings(
            base=self.base.text().strip(),
            user=self.user.text().strip(),
            password=self.password.text(),
            games_dir=self.games_dir.text().strip(),
            # "auto" keeps following the preference order (Faugus first) as launchers come and go.
            launcher="auto" if self.launcher.currentData() == detect_launcher() else self.launcher.currentData() or "auto",
            check_updates=self.check_updates_box.isChecked(),
            show_sidebar=self.win.app.settings.show_sidebar,
            sync_saves=self.sync_saves_box.isChecked(),
        )
        save_settings(self.win.app.settings)
        self.win.back()
        self.win.app.refresh()


class BrowsePage(Page):
    """In-window file picker (no native dialog, so it stays gamepad friendly)."""

    title = "Browse for the executable"

    def __init__(self, win: "MainWindow", start: Path, on_pick, folders: bool = False):
        super().__init__()
        if folders:
            self.title = "Choose a folder"
        self.win, self.on_pick, self.cwd, self.folders = win, on_pick, start, folders
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
        entries = [("[ Use this folder ]", path)] if self.folders else []
        entries += [("..", path.parent)] if path.parent != path else []
        try:
            children = sorted(path.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            children = []
        entries += [
            (c.name + ("/" if c.is_dir() else ""), c)
            for c in children
            if c.is_dir() or (not self.folders and c.suffix.lower() == ".exe")
        ]
        for label, target in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(target))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem) -> None:
        target = Path(item.data(Qt.UserRole))
        if self.folders and self.list.row(item) == 0:
            self.win.back()
            self.on_pick(str(self.cwd))
        elif target.is_dir():
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
        self.desktop = Toggle("Create a desktop entry")
        self.desktop.setChecked(sys.platform != "win32")
        self.desktop.setVisible(sys.platform != "win32")
        self.steam_users = steam.steam_user_dirs()
        self.steam = Toggle("Add to Steam")
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


class EdgeList(QListWidget):
    """A list that hands focus on past its first and last row, so a pad can walk through a stack of lists."""

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Down and self.currentRow() >= self.count() - 1:
            self.focusNextChild()
        elif e.key() == Qt.Key_Up and self.currentRow() <= 0:
            self.focusPreviousChild()
        else:
            super().keyPressEvent(e)


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
        self._last_row = 0

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self.count() and self.currentRow() < 0:
            self.setCurrentRow(min(self._last_row, self.count() - 1))

    def focusOutEvent(self, e) -> None:
        # A thumbnail stays highlighted only while the strip has the focus.
        if self.currentRow() >= 0:
            self._last_row = self.currentRow()
        self.clearSelection()
        self.setCurrentRow(-1)
        super().focusOutEvent(e)

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
        self.version_label = QLabel()
        self.version_label.setWordWrap(True)
        info_col.addWidget(self.version_label)
        form = QFormLayout()
        for label, value in metadata_lines(game):
            val = QLabel(value)
            val.setWordWrap(True)
            form.addRow(label, val)
        self.size_label = QLabel("...")
        form.addRow("Size on server", self.size_label)
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
        b.game_size.connect(self._on_size)
        b.cover.connect(lambda gid, _blob: gid == self.game["id"] and self._set_cover(win.covers.get(gid)))
        self.app.fetch_images(self.shot_urls, game["id"])
        self._update_version_label()
        self.app.load_size(game["id"])
        self.rebuild()

    def _on_size(self, gid: int, size: int) -> None:
        if gid == self.game["id"]:
            self.size_label.setText(fmt_bytes(size))

    def _set_cover(self, pix: QPixmap | None) -> None:
        if pix:
            self.cover.setPixmap(pix)

    def _group(self) -> Group | None:
        return self.app.group_of.get(self.game["id"])

    def _update_version_label(self) -> None:
        group = self._group()
        many = group is not None and len(group.versions) > 1
        self.version_label.setVisible(many)
        if many:
            self.version_label.setText(
                f"Version: {version_label(self.game, self.win.library.libraries)}  ({len(group.versions)} versions available)"
            )

    def switch_version(self, game: dict) -> None:
        """Show another version of the same title (its install state, cover and files)."""
        self.game = game
        self.title = game["name"]
        self._set_cover(self.win.covers.get(game["id"]))
        self._update_version_label()
        self.size_label.setText("...")
        self.app.load_size(game["id"])
        self.rebuild()

    def start_with(self, version: dict, installer: dict | None) -> None:
        self.switch_version(version)
        self.app.start_install(version, installer)
        self.rebuild()
        if self.first:
            self.first.setFocus()

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
            pct = max(0.0, min(1.0, written / total))
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * pct))
            self.bar.setFormat(f"{pct * 100:.1f}%")
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

    def _button(self, text: str, fn, default: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("danger", danger)
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
        self.buttons.addStretch()  # the buttons sit in the middle, none wider than the stylesheet allows
        self._fill_buttons()
        self.buttons.addStretch()

    def _fill_buttons(self) -> None:
        gid = self.game["id"]
        rec = load_library().get(gid)
        self.first = None
        if gid in self.app.installs:
            self.status.setText("Installing... you can go back, it keeps running")
            written, total, label = self.app.progress.get(gid, (0, 0, ""))
            self._on_progress(gid, written, total, label or self.status.text())
            if gid in self.app.vnc:
                self._button("Open installer display", lambda: QDesktopServices.openUrl(QUrl(self.app.vnc[gid])))
            pause = self._button("Pausing..." if gid in self.app.stopping else "Pause", self.pause, True)
            pause.setEnabled(gid not in self.app.stopping)
            self.first = pause if pause.isEnabled() else None
            self._button("Cancel local install", self.cancel_local, danger=True)
            self._button("Cancel server install", self.cancel_server, danger=True)
            return
        if rec and rec.state == "installed":
            self.status.setText("Installed")
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.first = self._button("Play", self.play, True)
            self._button("Options", lambda: self.show_options(rec))
        elif rec and rec.state == "awaiting_executable":
            self.status.setText("Awaiting executable info")
            self.first = self._button("Choose executable", lambda: self.choose_executable(rec), True)
            self._button("Options", lambda: self.show_options(rec))
        else:
            self.status.setText("Partially downloaded, can resume" if rec else "Not installed")
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.first = self._button("Resume" if rec else "Install", self.install, True)
            self._button("Options", lambda: self.show_options(rec))

    def install(self) -> None:
        group = self._group()
        local = load_library()
        fresh = group is not None and not any(m["id"] in local for m in group.members)
        if group is not None and len(group.versions) > 1 and fresh:
            self.win.push(InstallerPickerPage(self.win, self, group))
            return
        self.app.start_install(self.game)
        self.rebuild()
        if self.first:
            self.first.setFocus()

    def pause(self) -> None:
        self.app.pause_install(self.game["id"])
        self.rebuild()

    def cancel_local(self) -> None:
        self.win.ask(
            "Stop downloading and delete the files downloaded so far? The install keeps running on the server.",
            lambda: self.app.cancel_local_install(self.game["id"]),
            danger=True,
        )

    def cancel_server(self) -> None:
        self.win.ask(
            "Stop the installer on the server? What was already downloaded here is kept for a later resume.",
            lambda: self.app.cancel_server_install(self.game["id"]),
            danger=True,
        )

    def play(self) -> None:
        rec = load_library()[self.game["id"]]
        self.win.saves.before_launch(rec, lambda: self.start(rec))

    def start(self, rec: InstalledGame) -> None:
        try:
            proc = launch(rec, self.app.settings.launcher)
        except (RuntimeError, OSError) as e:
            self.win.notify(str(e))
            return
        self.win.notify(f"Starting {rec.name}...")
        self.win.saves.watch(rec, proc)
        # A launcher that dies within seconds never showed the game: say why.
        QTimer.singleShot(6000, lambda: (msg := launch_failure(proc)) and self.win.notify(msg))

    def show_options(self, rec: InstalledGame | None) -> None:
        self.win.push(OptionsPage(self.win, self, rec))

    def regenerate(self, rec: InstalledGame) -> None:
        def go() -> None:
            def work():
                try:
                    changed = manager.regenerate_entries(rec, self.game, self.app.settings.launcher, self.app.client())
                except RuntimeError as e:
                    self.app.bridge.error.emit(str(e))
                    return
                message = f"Shortcuts of {rec.name} rebuilt"
                if changed and steam.steam_running():
                    message += "; restart Steam to see the updated shortcut"
                self.app.bridge.error.emit(message)
                self.app.bridge.finished.emit(rec.game_id, "")

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(m))

        self.win.ask(
            "Rebuild this game's launch script, desktop entry and Steam shortcut from its current settings? "
            "The Steam shortcut is edited in place, so it keeps its artwork and play time.",
            go,
        )

    def toggle_save_sync(self, rec: InstalledGame) -> None:
        now = sync_enabled(rec, self.app.settings)
        wanted = not now
        manager._update(rec, save_sync=None if wanted == self.app.settings.sync_saves else wanted)
        self.win.notify(f"Save sync for {rec.name} is {'on' if wanted else 'off'}")

    def delete_server_cache(self) -> None:
        gid = self.game["id"]

        def go() -> None:
            def work():
                self.app.client().clear_cache(gid)
                self.app.bridge.error.emit("The install cache on the server was deleted")

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not delete the server cache: {m}"))

        self.win.ask(
            "Delete this game's install cache on the server? The copy on this device is not touched.",
            go,
            danger=True,
        )

    def choose_launcher(self, rec: InstalledGame) -> None:
        def done(engine: str) -> None:
            def work():
                try:
                    steam_changed = manager.set_launcher(rec, engine, self.app.settings.launcher)
                except RuntimeError as e:
                    self.app.bridge.error.emit(str(e))
                    return
                message = f"{rec.name} now launches through {launcher_label(engine) if engine != 'auto' else 'the default engine'}"
                if steam_changed and steam.steam_running():
                    message += "; restart Steam to see the updated shortcut"
                self.app.bridge.error.emit(message)
                self.app.bridge.finished.emit(rec.game_id, "")

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(m))

        self.win.push(LauncherPage(self.win, rec, done))

    def choose_executable(self, rec: InstalledGame) -> None:
        def done(exe: str, steam_user: Path | None, desktop: bool) -> None:
            self.logbox.appendPlainText("Updating entries and fetching artwork...")

            def work():
                changed = manager.finish_setup(
                    rec, self.game, exe, steam_user, desktop, self.app.settings.launcher, self.app.client()
                )
                if changed and steam_user and steam.steam_running():
                    self.app.bridge.error.emit("Steam is running: restart it to see the updated shortcut.")
                self.app.bridge.finished.emit(rec.game_id, "")
                self.app.bridge.call.emit(lambda: self.win.saves.offer_after_install(rec))

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.finished.emit(rec.game_id, m))

        self.win.push(ExecutablePage(self.win, rec, done))

    def uninstall(self, rec: InstalledGame, and_server_cache: bool = False) -> None:
        def remove(delete_prefix: bool) -> None:
            leftovers = manager.uninstall(rec, delete_prefix)
            if and_server_cache:
                self.app.run_bg(
                    lambda: self.app.client().clear_cache(rec.game_id),
                    on_error=lambda m: self.app.bridge.error.emit(f"Could not delete the server cache: {m}"),
                )
            self.win.notify(
                f"Could not remove everything, delete by hand: {', '.join(leftovers)}"
                if leftovers
                else f"{rec.name} uninstalled" + (" and its server cache deleted" if and_server_cache else "")
            )
            self.win.refresh_items()
            self.rebuild()

        def after_files() -> None:
            if rec.prefix and Path(rec.prefix).exists():
                self.win.ask(
                    "Also delete the local Wine prefix?\nIt may contain your save files. This cannot be undone.",
                    lambda: remove(True),
                    on_no=lambda: remove(False),
                    danger=True,
                )
            else:
                remove(False)

        what = "its shortcuts and the install cache on the server" if and_server_cache else "its shortcuts"
        self.win.ask(
            f"Delete the game files of {rec.name}, {what}?",
            lambda: self.win.saves.final_backup(rec, after_files),
            danger=True,
        )


ROLE_COVER, ROLE_PROGRESS, ROLE_INSTALLED, ROLE_CORNER = Qt.UserRole + 1, Qt.UserRole + 2, Qt.UserRole + 3, Qt.UserRole + 4
BADGE_SIZE = 56


def paint_installed_badge(painter: QPainter, cover: QRect) -> None:
    """Blue gradient corner with a check, bottom-right of the cover."""
    right, bottom = cover.right() + 1, cover.bottom() + 1
    corner = QPolygonF(
        [QPointF(right, bottom - BADGE_SIZE), QPointF(right, bottom), QPointF(right - BADGE_SIZE, bottom)]
    )
    painter.setPen(Qt.NoPen)
    painter.setBrush(accent_gradient(right - BADGE_SIZE, bottom - BADGE_SIZE, right, bottom))
    painter.drawPolygon(corner)
    check = QPainterPath()
    check.moveTo(right - 25, bottom - 14)
    check.lineTo(right - 19, bottom - 8)
    check.lineTo(right - 8, bottom - 20)
    pen = QPen(QColor("white"), 3.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(check)



def paint_state_badge(painter: QPainter, cover: QRect, state: str) -> None:
    """A colored corner on the left of the cover: amber with a floppy disk (only the saves are
    left, top), violet with a puzzle piece (only add-ons, bottom)."""
    left, top, bottom = cover.left(), cover.top(), cover.bottom() + 1
    saves = state == SAVES_ONLY
    if saves:
        corner = QPolygonF([QPointF(left, top), QPointF(left + BADGE_SIZE, top), QPointF(left, top + BADGE_SIZE)])
    else:
        corner = QPolygonF([QPointF(left, bottom - BADGE_SIZE), QPointF(left + BADGE_SIZE, bottom), QPointF(left, bottom)])
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#d9962b" if saves else "#8b5cf6"))
    painter.drawPolygon(corner)
    white = QColor("white")
    glyph = QPainterPath()
    if saves:
        x, y = left + 8, top + 8
        glyph.addRoundedRect(QRectF(x, y, 17, 17), 2, 2)  # the disk
        glyph.addRect(QRectF(x + 4, y, 8, 5))  # shutter
        glyph.addRect(QRectF(x + 3, y + 10, 11, 7))  # label
        painter.setPen(QPen(white, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
    else:
        x, y = left + 7, bottom - 26
        glyph.addRoundedRect(QRectF(x, y + 5, 14, 14), 2, 2)  # the piece
        glyph.addEllipse(QRectF(x + 3.5, y, 7, 7))  # a knob on top
        glyph.addEllipse(QRectF(x + 11, y + 8.5, 7, 7))  # and one on the right
        painter.setPen(Qt.NoPen)
        painter.setBrush(white)
    painter.drawPath(glyph)


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
            filled = bar.adjusted(0, 0, int((progress - 1000) * bar.width() / 1000), 0)
            painter.fillRect(filled, accent_gradient(bar.left(), bar.top(), bar.right(), bar.bottom()))
        painter.setClipRect(cover)
        if index.data(ROLE_INSTALLED):
            paint_installed_badge(painter, cover)
        if state := index.data(ROLE_CORNER):
            paint_state_badge(painter, cover, state)
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
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setItemDelegate(CoverDelegate(self.grid))
        self.grid.setGridSize(QSize(COVER_SIZE.width() + 40, COVER_SIZE.height() + 80))
        self.grid.itemActivated.connect(win.open_game)
        self.items: dict[int, QListWidgetItem] = {}
        self.library_filter: int | None = None
        self.libraries: list[dict] = []

        # The sidebar sits on the left, beside the grid, not inside it, so scrolling the games leaves it where it is.
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self.libs = EdgeList()
        self.libs.currentRowChanged.connect(self._library_chosen)
        self.installs = EdgeList()
        self.installs.setMaximumHeight(170)
        self.installs.itemActivated.connect(self._open_install)
        self.installs.itemClicked.connect(self._open_install)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(self._heading("Libraries"))
        side.addWidget(self.libs, 1)
        side.addWidget(self._heading("Active installs"))
        side.addWidget(self.installs)
        self.sidebar.setVisible(win.app.settings.show_sidebar)

        lay = QHBoxLayout(self)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.grid, 1)
        self._fill_libraries()

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet("color: #9aa3b0; font-size: 14px; font-weight: bold; padding-top: 8px;")
        return label

    def _fill_libraries(self) -> None:
        self.libs.blockSignals(True)
        self.libs.clear()
        self.libs.addItem("All games")
        for lib in self.libraries:
            self.libs.addItem(lib["name"])
        row = 0
        if self.library_filter is not None:
            row = next((i + 1 for i, lib in enumerate(self.libraries) if lib["id"] == self.library_filter), 0)
            self.library_filter = self.libraries[row - 1]["id"] if row else None
        self.libs.setCurrentRow(row)
        self.libs.blockSignals(False)

    def set_libraries(self, libraries: list[dict]) -> None:
        self.libraries = libraries
        self._fill_libraries()

    def _open_install(self, item: QListWidgetItem) -> None:
        gid = item.data(Qt.UserRole)
        if gid is not None:
            self.win.show_game(gid)

    def _library_chosen(self, row: int) -> None:
        self.library_filter = self.libraries[row - 1]["id"] if row > 0 else None
        self.populate(self.win.search.text().lower())

    def toggle_sidebar(self) -> None:
        shown = not self.sidebar.isVisible()
        self.sidebar.setVisible(shown)
        settings = self.win.app.settings
        settings.show_sidebar = shown
        save_settings(settings)
        (self.libs if shown else self.grid).setFocus()

    def focus_default(self) -> None:
        self.grid.setFocus()

    def _progress(self, gid: int) -> int | None:
        """0..1000 while installing (0 until the size is known), None otherwise."""
        if gid not in self.win.app.installs:
            return None
        written, total, _ = self.win.app.progress.get(gid, (0, 0, ""))
        return 1000 * written // total if total else 0

    def label(self, gid: int, rec: InstalledGame | None, versions: int = 1) -> str:
        name = self.win.app.games[gid]["name"]
        badges = []
        if versions > 1:
            badges.append(f"{versions} versions")
        badge = {"awaiting_executable": "Setup needed", "installing": "Partial"}.get(
            rec.state if rec else "", ""
        )
        progress = self._progress(gid)
        if progress is not None:
            badge = f"Installing {progress // 10}%"
        if badge:
            badges.append(badge)
        return name + (f"\n[{' | '.join(badges)}]" if badges else "")

    def _fill_item(self, item: QListWidgetItem, group: Group, lib: dict) -> None:
        gid = self.win.app.active_version(group)["id"]
        item.setText(self.label(gid, lib.get(gid), len(group.versions)))
        item.setData(Qt.UserRole, gid)
        item.setData(ROLE_COVER, self.win.covers.get(gid))
        item.setData(ROLE_PROGRESS, self._progress(gid))
        item.setData(ROLE_INSTALLED, any(lib.get(g["id"]) and lib[g["id"]].state == "installed" for g in group.members))
        item.setData(ROLE_CORNER, corner_state(self.win.app.active_version(group)))

    def update_label(self, gid: int) -> None:
        item = self.items.get(gid)
        group = self.win.app.group_of.get(gid)
        if item is not None and group is not None:
            self._fill_item(item, group, load_library())
        self.update_active()

    def update_active(self) -> None:
        running = [gid for gid in self.win.app.installs if gid in self.win.app.games]
        row = self.installs.currentRow()
        self.installs.clear()
        for gid in running:
            item = QListWidgetItem(f"{self.win.app.games[gid]['name']}: {(self._progress(gid) or 0) // 10}%")
            item.setData(Qt.UserRole, gid)
            self.installs.addItem(item)
        if not running:
            none = QListWidgetItem("None")
            none.setFlags(Qt.NoItemFlags)
            self.installs.addItem(none)
        elif row >= 0:
            self.installs.setCurrentRow(min(row, len(running) - 1))

    def populate(self, needle: str) -> None:
        current = self.grid.currentItem().data(Qt.UserRole) if self.grid.currentItem() else None
        lib = load_library()
        self.grid.clear()
        self.items = {}
        shown = [
            g
            for g in self.win.app.games.values()
            if (not needle or needle in g["name"].lower())
            and (self.library_filter is None or g.get("library_id") == self.library_filter)
        ]
        groups: dict[int, Group] = {}
        for game in shown:
            group = self.win.app.group_of[game["id"]]
            groups[id(group)] = group  # a title shows once, with all its versions
        def order(group: Group) -> tuple[int, str]:
            """Installing first, then installed, then the rest; alphabetical within each."""
            if any(m["id"] in self.win.app.installs for m in group.members):
                rank = 0
            elif any(lib.get(m["id"]) and lib[m["id"]].state in ("installed", "awaiting_executable") for m in group.members):
                rank = 1
            else:
                rank = 2
            return rank, group.game["name"].lower()

        for group in sorted(groups.values(), key=order):
            item = QListWidgetItem()
            self._fill_item(item, group, lib)
            self.grid.addItem(item)
            for member in group.members:
                self.items[member["id"]] = item
            if item.data(Qt.UserRole) == current or any(m["id"] == current for m in group.members):
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
        self.setWindowIcon(QIcon(str(ASSETS / "icon.png")))
        self.resize(1280, 800)
        self.back_btn = QPushButton("< Back")
        self.back_btn.clicked.connect(self.back)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.logo = QLabel()
        self.logo.setPixmap(asset_pixmap("title.png", 44))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self.refresh_items)
        self.reload_btn, self.settings_btn = QPushButton("Refresh"), QPushButton("Settings")
        self.notif_btn = QPushButton(" Notifications")
        self.notif_btn.setIcon(bell_icon(26))
        self.notif_btn.setIconSize(QSize(26, 26))
        self.notif_btn.clicked.connect(self.open_notifications)
        self.notifications: list[dict] = []
        self.seen_notification_id: int | None = None
        self.reload_btn.clicked.connect(app.refresh)
        self.settings_btn.clicked.connect(self.open_settings)
        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addWidget(self.logo)
        top.addWidget(self.title)
        top.addWidget(self.search, 1)
        top.addStretch(1)
        top.addWidget(self.reload_btn)
        top.addWidget(self.notif_btn)
        top.addWidget(self.settings_btn)
        self.stack = QStackedWidget()
        self.library = LibraryPage(self)
        self.stack.addWidget(self.library)
        self.history: list[Page] = [self.library]
        self.game_pages: dict[int, GamePage] = {}
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.legend = QLabel()
        self.legend.setAlignment(Qt.AlignCenter)
        self.legend.setStyleSheet("color: #9aa3b0; font-size: 15px;")
        self.pad_family: str | None = None
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.legend)
        self.setCentralWidget(central)
        self.covers: dict[int, QPixmap] = {}
        b = app.bridge
        b.call.connect(lambda fn: fn())
        self.saves = SaveSync(self)
        b.games.connect(self.set_games)
        b.error.connect(self.notify)
        b.cover.connect(self.set_cover)
        b.finished.connect(lambda *_: self.refresh_items())
        b.progress.connect(lambda gid, *_: self.library.update_label(gid))
        b.pad.connect(self.on_pad)
        b.pad_connected.connect(self.set_pad)
        b.update_checked.connect(self.on_update_checked)
        b.notifications.connect(self.on_notifications)
        b.libraries.connect(self.library.set_libraries)
        self.notif_timer = QTimer(self)
        self.notif_timer.timeout.connect(app.poll_notifications)
        self.notif_timer.start(15000)
        self.osk = osk.OnScreenKeyboard(self.open_keyboard)
        self._add_shortcuts()
        self.pad_stop = gamepad.start(b.pad.emit, b.pad_connected.emit)
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
        self.notif_btn.setVisible(on_library)
        self.logo.setVisible(on_library)
        self.title.setVisible(not on_library)
        self.title.setText(page.title)
        self.refresh_legend()
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

    def choose(self, title: str, text: str, options: list, on_choose) -> None:
        self.push(ChoicePage(self, title, text, options, on_choose))

    def checklist(self, title: str, text: str, items: list, on_done) -> None:
        self.push(ChecklistPage(self, title, text, items, on_done))

    def browse_folder(self, start: Path, on_pick) -> None:
        self.push(BrowsePage(self, start, on_pick, folders=True))

    def ask(self, text: str, on_yes, on_no=None, danger: bool = False) -> None:
        def no():
            if on_no:
                on_no()

        page = ConfirmPage(self, text, on_yes, danger=danger)
        page.no.clicked.connect(no)
        self.push(page)

    def on_update_checked(self, info, error: str, manual: bool) -> None:
        if error and not manual:
            self.notify(f"Update check failed: {error}")
        if info is None:
            return
        self.ask(
            f"MOG {info.version} is available (you have {__version__}). Update now? The app restarts when done.",
            lambda: self.push(UpdatePage(self, info)),
        )

    def _add_shortcuts(self) -> None:
        def on_library(action):
            return lambda: action() if self.current_page() is self.library else None

        for keys, action in (
            ("F5", self.app.refresh),
            ("Ctrl+F", self.search.setFocus),
            ("Ctrl+B", self.library.toggle_sidebar),
            ("Ctrl+N", self.open_notifications),
            ("Ctrl+,", self.open_settings),
        ):
            QShortcut(QKeySequence(keys), self, on_library(action))
        QShortcut(QKeySequence("Ctrl+Q"), self, self.quit_app)

    def set_pad(self, connected: bool, family: str) -> None:
        self.pad_family = family if connected else None
        self.refresh_legend()

    def refresh_legend(self) -> None:
        """The controller's guide while one is connected, the keyboard's otherwise."""
        page = self.current_page()
        typing, inbox = isinstance(page, KeyboardPage), isinstance(page, NotificationsPage)
        family = self.pad_family
        self.legend.setText(pad_legend(family, typing, inbox) if family else keyboard_legend(typing, inbox))
        self.legend.setVisible(True)

    def open_keyboard(self, target: QWidget) -> None:
        if not isinstance(self.current_page(), KeyboardPage):
            self.push(KeyboardPage(self, target))

    def quit_app(self) -> None:
        if self.app.installs:
            self.ask(
                "An install is still running. Quit anyway? Downloaded files are kept for a later resume.",
                self.close,
                danger=True,
            )
        else:
            self.close()

    def on_pad(self, name: str) -> None:
        if self.osk.steam_visible:
            # Steam's keyboard owns the controller while it is up; B dismisses it.
            if name == gamepad.BACK:
                self.osk.hide_steam()
            return
        if name == gamepad.ACCEPT and self.osk.enabled and osk.is_text_input(QApplication.focusWidget()):
            self.osk.request(QApplication.focusWidget())
            return
        if name == gamepad.QUIT:
            self.quit_app()
            return
        page = self.current_page()
        if isinstance(page, KeyboardPage):
            if name == gamepad.PAGE_PREV:
                page.move_cursor(-1)
            elif name == gamepad.PAGE_NEXT:
                page.move_cursor(1)
            elif name == gamepad.TRIGGER_R:
                page.press(keyboard.DONE)
            elif name == gamepad.REFRESH:
                page.press(keyboard.BACKSPACE)
            elif name == gamepad.SEARCH:
                page.press(keyboard.SPACE)
            else:
                self._post_key(name)
            return
        if isinstance(page, NotificationsPage) and name == gamepad.REFRESH:
            page.delete_current()
            return
        if name == gamepad.TRIGGER_L:
            if page is self.library:
                self.library.toggle_sidebar()
            return
        if name == gamepad.TRIGGER_R:
            return
        if name in (gamepad.REFRESH, gamepad.SEARCH):
            if self.current_page() is self.library:
                if name == gamepad.REFRESH:
                    self.app.refresh()
                else:
                    self.search.setFocus()
                    if self.osk.enabled:
                        self.osk.request(self.search)
            return
        if name == gamepad.MENU:
            if self.current_page() is self.library:
                self.open_settings()
            return
        popup = QApplication.activePopupWidget()
        focus = QApplication.focusWidget()
        if popup is None and focus is not None:
            if name in (gamepad.UP, gamepad.DOWN) and isinstance(focus, (QLineEdit, QComboBox, QPlainTextEdit)):
                # These widgets swallow Up/Down, so the pad would be stuck on them.
                focus.focusNextPrevChild(name == gamepad.DOWN)
                return
            if name == gamepad.ACCEPT and isinstance(focus, QComboBox):
                focus.showPopup()
                return
            if name == gamepad.ACCEPT and isinstance(focus, QLineEdit):
                focus.focusNextPrevChild(True)
                return
        self._post_key(name)

    def _post_key(self, name: str) -> None:
        key = _KEYS.get(name)
        if key is None:
            return
        target = QApplication.activePopupWidget() or QApplication.focusWidget() or self.current_page()
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.postEvent(target, QKeyEvent(kind, key, Qt.NoModifier))

    def open_settings(self) -> None:
        self.push(SettingsPage(self))

    def set_games(self, games: list) -> None:
        self.app.set_games(games)
        self.app.poll_notifications()
        self.notify(f"{len(games)} games")
        self.refresh_items()
        self.saves.check_all()

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
        self.show_game(item.data(Qt.UserRole))

    def show_game(self, gid: int) -> None:
        game = self.app.games.get(gid)
        if game:
            # A page can have switched to another version of its title, so match on what it shows now.
            page = next((p for p in self.game_pages.values() if p.game["id"] == gid), None)
            if page is None:
                page = self.game_pages[gid] = GamePage(self, game)
            self.push(page)

    def open_notifications(self) -> None:
        self.push(NotificationsPage(self))

    def on_notifications(self, data: dict) -> None:
        items = data["notifications"]
        newest = max((n["id"] for n in items), default=0)
        if self.seen_notification_id is not None:
            for n in items:
                if n["id"] > self.seen_notification_id and not n["read"]:
                    self.notify(f"Notification: {n['title']}")
        self.seen_notification_id = newest
        self.notifications = items
        unread = data["unread"]
        self.notif_btn.setText(f" Notifications ({unread})" if unread else " Notifications")
        page = self.current_page()
        if isinstance(page, NotificationsPage):
            page.populate()

    def mark_read(self, notification_id: int | None) -> None:
        for n in self.notifications:
            if notification_id is None or n["id"] == notification_id:
                n["read"] = True
        self.app.run_bg(lambda: self.app.client().mark_notifications_read(notification_id))
        self.on_notifications({"notifications": self.notifications, "unread": sum(not n["read"] for n in self.notifications)})
        QTimer.singleShot(1000, self.app.poll_notifications)

    def delete_notification(self, notification_id: int | None) -> None:
        self.notifications = [n for n in self.notifications if notification_id is not None and n["id"] != notification_id]
        self.app.run_bg(lambda: self.app.client().delete_notifications(notification_id))
        self.on_notifications({"notifications": self.notifications, "unread": sum(not n["read"] for n in self.notifications)})
        QTimer.singleShot(1000, self.app.poll_notifications)


def run_gui() -> int:
    osk.prefer_xcb()
    qapp = QApplication(sys.argv[:1])
    qapp.setStyleSheet(STYLE)
    enter_filter = ActivateOnEnter()
    qapp.installEventFilter(enter_filter)
    updater.cleanup_old()
    app = App()
    win = MainWindow(app)
    win.osk.install(qapp)
    if is_deck():
        win.showFullScreen()
    else:
        win.show()
    if updater.enabled() and app.settings.check_updates:
        app.check_update()
    if app.settings.configured:
        app.refresh()
    else:
        win.open_settings()
    return qapp.exec()
