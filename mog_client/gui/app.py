"""Qt GUI: library grid, per-game install/play dialog, settings. Fully
operable from a gamepad (D-pad/stick = arrows, A = Enter, B = Esc)."""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QConicalGradient,
    QDesktopServices,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from mog_client import (
    activity,
    crashlog,
    gameplay,
    installdirs,
    logstore,
    manager,
    ordering,
    protocol,
    selfsteam,
    snapshot,
    steam,
    trace,
    updater,
)
from mog_client import mods as mods_download
from mog_client import played as played_at
from mog_client.api import fetch_url, fmt_bytes, safe_dirname
from mog_client.config import (
    InstalledGame,
    data_dir,
    default_install_root,
    load_library,
    load_settings,
    save_settings,
)
from mog_client.grouping import SAVES_ONLY, Group, corner_state, group_games
from mog_client.gui import gamepad, keyboard, legend, osk
from mog_client.gui import saves_ui as sync_ui
from mog_client.gui.firstrun import FirstRunPage
from mog_client.gui.about import AboutTab
from mog_client.gui.busy import BusyOverlay
from mog_client.gui.headerart import HeaderArt
from mog_client.gui.instance import Listener, send_to_running
from mog_client.gui.loading import LoadingPanel
from mog_client.gui.logview import LogView
from mog_client.gui.menu import MenuCombo, MenuLineEdit, MenuView, OptionRow
from mog_client.gui.modal import FitScrollArea, ModalHost
from mog_client.gui.overlay import MessageOverlay
from mog_client.gui.pictures import largest_pixmap
from mog_client.gui.playing import PlayingOverlay
from mog_client.gui.saves_ui import SaveSync
from mog_client.gui.sounds import NAVIGATE, PLAY, NavigationSounds, SoundPlayer
from mog_client.gui.theme import STYLE
from mog_client.gui.widgets import (
    ROLE_DIVIDER,
    ROLE_KIND,
    ROLE_ON,
    BadgeButton,
    FocusTabs,
    OptionDelegate,
    ParagraphLabel,
    Toggle,
    accent_gradient,
    avatar_icon,
    clear_icon,
)
from mog_client.launcher import (
    available_launchers,
    client_command,
    detect_launcher,
    ensure_scanned,
    launch,
    launch_failure,
    launcher_label,
    list_executables,
    remember_client,
)
from mog_client.progress import RateMeter, format_eta
from mog_client.saves import devices, runner, sync
from mog_client.saves.state import load_state, save_install_manifest
from mog_client.saves.sync import enabled as sync_enabled
from mog_client.scrape import artwork_urls, hltb_lines, metadata_lines, screenshot_urls
from mog_client.stopactions import StopActions
from mog_client.transfer import sha1_of
from mog_client.version import __version__

ASSETS = Path(__file__).parent / "assets"


def asset_pixmap(name: str, height: int) -> QPixmap:
    return QPixmap(str(ASSETS / name)).scaledToHeight(height, Qt.SmoothTransformation)




def steam_outcome(rec: InstalledGame, done: str) -> tuple[str, str]:
    """(level, text) for what was done, with a note when Steam's own shortcut was written while it ran: Steam shows
    it after a restart, and MOG puts it back at its next start with Steam closed if Steam undid it."""
    if not rec.steam_pending:
        return "info", done
    return "info", f"{done}. Restart Steam to see the shortcut for {rec.name}."


COVER_SIZE = QSize(200, 270)
TAB_TOP_GAP = 22  # between the tab bar and what is in the tab
OPTIONS_BUTTON_WIDTH = 440
SIDEBAR_WIDTH = 280
CARD_PULSE_MS, CARD_RING, PAGE_RING = 900, 8, 22  # a task starting: the ring around the game's cover, how long it lasts and how far it spreads
SPIN_MS = 1100  # one turn of the wedge on the cover and of the ring on Play
ACTIVE_ICON = QSize(32, 32)
NOTIFICATION_ICON = QSize(64, 64)
# What the server records about something the client did itself; every other kind comes from the server.
CLIENT_KINDS = frozenset({"save_synced", "save_restored", "mod_downloaded"})
ROLE_MOD_ROW = Qt.UserRole + 30  # on a sidebar row: the name of the mod it is fetching (None for an install, a sync or a restore)
IMAGE_WORKERS = 6


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
    error = Signal(str)  # something failed: a message over the window, and the log
    message = Signal(str, str)  # level, text: a message over the window, and the log
    note = Signal(str)  # only the log
    cover = Signal(int, bytes)
    image = Signal(str, bytes)  # url, bytes (screenshots)
    user = Signal(str, object)  # who is signed in: user name, avatar bytes (or None)
    icon = Signal(int, bytes)  # game id, its small icon (the active installs list)
    header_art = Signal(int, bytes)  # game id, the hero or banner behind its page's header
    progress = Signal(int, object, object, str)  # game, written, total (bytes, past 2 GiB: not a 32-bit int), state text
    log = Signal(int, str)
    finished = Signal(int, str)  # game, error text ("" on success)
    pad = Signal(str)
    pad_connected = Signal(bool, str)  # connected, controller family
    notifications = Signal(object)  # {"notifications": [...], "unread": n}
    libraries = Signal(list)
    installers = Signal(int, object, str, bool)  # game id, candidates (None on error), error, the server found no installer
    game_size = Signal(int, object)  # game id, bytes on the server (not a 32-bit int)
    activity = Signal()  # something started, moved on or ended in the background: the sidebar's rows follow
    mods_loaded = Signal(int, object)  # game id, its mods ({name, kind, size_bytes, file_count})
    game_sizes = Signal(int, object)  # game id, {"installer", "cache", "saves", "total"} bytes the server holds
    played = Signal(int)  # a game was played and its saves looked at: its page shows it
    update_checked = Signal(object, str, bool)  # UpdateInfo or None, error text, user asked
    update_progress = Signal(object, object)  # written, total bytes
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
        self._icons_asked: set[int] = set()
        self.mods: dict[int, list[dict]] = {}  # each game's mods, once asked
        self.mod_jobs: dict[tuple[int, str], tuple[str, int]] = {}  # (game, mod) -> (stage, percent) while it is fetched
        self.mod_saved: dict[tuple[int, str], str] = {}  # (game, mod) -> where the last fetch of it was saved
        self.mod_stops: dict[tuple[int, str], threading.Event] = {}  # set to stop that mod's fetch
        self.revision: str | None = None  # the server's library revision at the last load
        self.refreshing = False
        self.sizes: dict[int, int] = {}  # bytes each game takes on the server, once asked
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
            self.revision = client.games_revision()  # before the list, so a change meanwhile shows as a difference
            libraries: list[dict] = []
            try:
                libraries = client.list_libraries()
                self.bridge.libraries.emit(libraries)
            except RuntimeError:
                pass  # an older server without the endpoint just has no library filter
            self._load_user(client)
            games = client.list_games()
            self.bridge.games.emit(games)
            try:
                snapshot.save_library(self.settings.base, games, libraries)
            except OSError as e:
                logstore.warning(f"Could not keep the library for the next start: {e}")
            done()  # the list is in; a change on the server from here on is a new refresh, whatever the covers are doing
            with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
                list(pool.map(lambda g: self._load_cover(g, client), games))

        def done() -> None:
            self.refreshing = False

        self.refreshing = True
        self.run_bg(work, on_error=lambda m: (done(), self.bridge.error.emit(m)))

    def load_mods(self, game_id: int) -> None:
        """Ask for the game's mods; the answer comes as `mods_loaded`."""

        def work() -> None:
            found = self.client().mods(game_id)
            self.mods[game_id] = found
            self.bridge.mods_loaded.emit(game_id, found)

        self.run_bg(work, on_error=lambda _m: None)

    def mod_folder(self, game: dict) -> Path:
        """Where the game's mods go: the mods folder of its own folder, installed or not yet."""
        rec = load_library().get(game["id"])
        base = Path(rec.install_dir) if rec else installdirs.default_root(self.settings) / safe_dirname(game["name"])
        return mods_download.mods_dir(base)

    def prune_mod_folder(self, game: dict) -> None:
        """Drop the empty folders a mod left behind: the mods folder, and without the game installed its folder too. Not
        while another mod of the game is being fetched, which may be about to write there."""
        if any(gid == game["id"] for gid, _name in self.mod_jobs):
            return
        folder = self.mod_folder(game)
        rec = load_library().get(game["id"])
        if rec is not None:
            installdirs.prune_empty(folder, [Path(rec.install_dir)])
        else:
            installdirs.prune_empty(folder.parent, self.settings.install_roots)

    def _note_mod_file(self, game_id: int, path: Path) -> None:
        """A mod saved inside an installed game's folder is one of the game's own files, so it is not taken for a save."""
        rec = load_library().get(game_id)
        if rec is None:
            return
        try:
            relative = path.resolve().relative_to(Path(rec.install_dir).resolve())
            entry = {"path": relative.as_posix(), "size_bytes": path.stat().st_size, "sha1": sha1_of(path)}
        except (OSError, ValueError):
            return
        save_install_manifest(game_id, [entry])

    def cancel_mod(self, game: dict, mod: dict) -> None:
        """Stop a mod's fetch: the server drops the zip and what was downloaded here is removed."""
        stop = self.mod_stops.get((game["id"], mod["name"]))
        if stop is not None:
            stop.set()

    def download_mod(self, game: dict, mod: dict) -> None:
        """Fetch a mod into the game's mods folder (the server zips a folder first), with its progress in the sidebar. The
        server keeps the news in the person's notifications; the fetch is remembered so a restart carries on with it."""
        key = (game["id"], mod["name"])
        if key in self.mod_jobs:
            return
        bridge = self.bridge
        stop = self.mod_stops[key] = threading.Event()
        self.mod_saved.pop(key, None)
        self.mod_jobs[key] = ("Preparing", 0)
        activity.add_mod(game["id"], mod)

        def changed() -> None:
            bridge.activity.emit()

        def progress(stage: str, percent: int) -> None:
            self.mod_jobs[key] = (stage, percent)
            changed()

        def work() -> None:
            client = self.client()
            try:
                saved = mods_download.fetch(client, game["id"], mod, self.mod_folder(game), progress, stop.is_set)
            except mods_download.ModCancelled:
                bridge.note.emit(f"Mod {mod['name']} of {game['name']}: stopped")
            except Exception as e:  # noqa: BLE001 - told to the user
                bridge.message.emit("error", f"Mod {mod['name']} of {game['name']}: {e}")
            else:
                self.mod_saved[key] = str(saved)
                self._note_mod_file(game["id"], saved)
                text = f"Mod {mod['name']} of {game['name']} downloaded: {saved}"
                device = devices.known_device()
                if client.mod_downloaded(game["id"], mod["name"], device.name if device else None):
                    bridge.note.emit(text)  # the server keeps it in the inbox
                    self.poll_notifications()
                else:
                    bridge.message.emit("info", text)
            finally:
                self.mod_jobs.pop(key, None)
                self.mod_stops.pop(key, None)
                activity.remove_mod(game["id"], mod["name"])
                self.prune_mod_folder(game)
                changed()

        changed()
        self.run_bg(work)

    def poll_library(self) -> None:
        """Ask the server whether the library changed (a scan, a scrape, an edit) and, if so, load it again."""
        if not self.settings.configured or self.refreshing:
            return

        def work() -> None:
            current = self.client().games_revision()
            # No revision at the last load (the server did not answer then) is not "never": the first one that comes is
            # a difference, since what happened meanwhile is not known.
            if current is not None and current != self.revision and not self.refreshing:
                logstore.info("The library changed on the server, refreshing it")
                self.refresh()

        self.run_bg(work, on_error=lambda _m: None)

    def load_cached_covers(self, games: list[dict]) -> None:
        """The covers already on disk, for the library shown from the snapshot before the network answers."""

        def work() -> None:
            for game in games:
                url = artwork_urls(game).get("portrait")
                if not url:
                    continue
                cache = data_dir() / "covers" / f"{game['id']}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.img"
                try:
                    self.bridge.cover.emit(game["id"], cache.read_bytes())
                except OSError:
                    continue

        self.run_bg(work, on_error=lambda _m: None)

    def _load_user(self, client) -> None:
        """Who this client is signed in as, and their picture, for the corner of the window."""
        try:
            me = client.me()
        except RuntimeError:
            return
        avatar = me.get("avatar_path")
        name, picture = me.get("username") or self.settings.user, client.get_image(avatar) if avatar else None
        self.bridge.user.emit(name, picture)
        try:
            snapshot.save_user(self.settings.base, name, picture)
        except OSError as e:
            logstore.warning(f"Could not keep the user for the next start: {e}")

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

    def fetch_icon(self, game: dict) -> None:
        """The small icon the server has for the game, once per game."""
        url = ((game.get("media") or {}).get("icon") or {}).get("url")
        if not url or game["id"] in self._icons_asked:
            return
        self._icons_asked.add(game["id"])

        def work() -> None:
            cache = data_dir() / "art" / f"{game['id']}-icon-{hashlib.sha1(url.encode()).hexdigest()[:10]}.img"
            blob = self._image(cache, f"/api/games/{game['id']}/media/icon", url, self.client())
            if blob is not None:
                self.bridge.icon.emit(game["id"], blob)

        self.run_bg(work, on_error=lambda _m: None)

    def fetch_header_art(self, game: dict) -> None:
        """The hero (else the banner) the server chose for the game, for the page's header."""

        def work() -> None:
            media = game.get("media") or {}
            for kind in ("hero", "banner"):
                url = (media.get(kind) or {}).get("url")
                if not url:
                    continue
                cache = data_dir() / "art" / f"{game['id']}-{kind}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.img"
                blob = self._image(cache, f"/api/games/{game['id']}/media/{kind}", url, self.client())
                if blob is not None:
                    self.bridge.header_art.emit(game["id"], blob)
                    return

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
            client = self.client()
            held = client.game_sizes(game_id)
            if held is not None:
                self.sizes[game_id] = held["installer"]
                self.bridge.game_sizes.emit(game_id, held)
                return
            size = client.game_size(game_id)
            if size is not None:
                self.sizes[game_id] = size
                self.bridge.game_size.emit(game_id, size)

        threading.Thread(target=work, daemon=True).start()

    def load_installers(self, game_id: int) -> None:
        def work():
            try:
                data = self.client().candidates(game_id)
                self.bridge.installers.emit(game_id, data.get("candidates", []), "", bool(data.get("extract_suggested")))
            except Exception as e:  # noqa: BLE001 - shown in the picker
                self.bridge.installers.emit(game_id, None, str(e), False)

        threading.Thread(target=work, daemon=True).start()

    def start_install(
        self, game: dict, installer: dict | None = None, root: Path | None = None, extract_only: bool | None = None
    ) -> None:
        gid = game["id"]
        if gid in self.installs:
            return
        stop = threading.Event()
        self.after_stop.forget(gid)
        self.installs[gid] = stop
        activity.add_install(gid)
        bridge = self.bridge
        bridge.activity.emit()  # listed, and its animation shown, now: the first progress comes seconds later

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
                bridge.message.emit(
                    "warning",
                    f"{game['name']}: the installer needs you, auto mode cannot go on. Finish it by hand: open the "
                    "game's page and choose \"Open installer display\" (it opens in your browser). The server has "
                    "also sent you a notification.",
                )
            written, total, _ = self.progress.get(gid, (0, 0, ""))
            report(written, total)

        def work():
            err = ""
            try:
                manager.run_install(
                    self.client(), game, self.settings, stop,
                    lambda m: bridge.log.emit(gid, m), on_session,
                    report, installer, root, extract_only,
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)
            self.installs.pop(gid, None)
            self.progress.pop(gid, None)
            self.stopping.discard(gid)
            activity.remove_install(gid)
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
                self.bridge.message.emit("info", f"{rec.name} had already finished installing, so it was kept")

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


class InputWatcher(QObject):
    """Tells which kind of device was used last: a key pressed on a keyboard (not one the pad posts) says "keys"."""

    def __init__(self, on_use, parent: QObject) -> None:
        super().__init__(parent)
        self.on_use = on_use

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.KeyPress and event.spontaneous():
            self.on_use("keys")
        return False


class Page(QWidget):
    """One screen of the single window. `title` shows in the header."""

    title = ""
    searchable = False
    modal = False  # drawn as a small card over the page beneath it (see ModalHost) instead of filling the window
    compact = False  # a modal card sized to what the page holds (a question), not the full-size card
    show_close = True  # a modal card's own Close button; off where the page has its own way out

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

    modal = True
    compact = True
    show_close = False  # No is the way out

    def __init__(self, win: "MainWindow", text: str, on_yes, title: str = "Are you sure?", danger: bool = False):
        super().__init__()
        self.title = title
        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.no, self.yes = QPushButton("No"), QPushButton("Yes")
        self.yes.setProperty("danger", danger)
        self.no.clicked.connect(win.back)
        self.yes.clicked.connect(lambda: (win.back(), on_yes()))
        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(self.text_label)
        lay.addLayout(_centered_row(self.no, self.yes))
        lay.addStretch()

    def focus_default(self) -> None:
        self.no.setFocus()


class UpdatePage(Page):
    """Downloads and installs a new build, then restarts into it."""

    modal = True
    compact = True
    show_close = False  # it closes itself when the new build starts

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
        self.win.sounds.close()
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
        self.list.setIconSize(NOTIFICATION_ICON)
        self.list.itemActivated.connect(self.open_item)
        win.app.bridge.icon.connect(self._icon_arrived)  # a game's icon that was fetched for this list
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

    def _icon_of(self, n: dict) -> QPixmap | None:
        """The game's own icon for a notification about a game; otherwise the client's for what it did itself and the
        server's for the rest."""
        game = self.win.app.games.get(n.get("game_id"))
        if game is not None:
            art = self.win.icon_art.get(game["id"])
            if art is None:
                self.win.app.fetch_icon(game)
                has_icon = ((game.get("media") or {}).get("icon") or {}).get("url")
                art = None if has_icon else self.win.covers.get(game["id"])  # no icon to wait for: the cover stands in
            return self._sharp(art) if art else None
        return self._sharp(QPixmap(str(ASSETS / ("icon.png" if n.get("kind") in CLIENT_KINDS else "server.png"))))

    def _sharp(self, pix: QPixmap) -> QPixmap:
        """The picture at the icon's size in device pixels, so a screen that scales the interface does not stretch it."""
        ratio = self.devicePixelRatioF()
        out = pix.scaled(NOTIFICATION_ICON * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        out.setDevicePixelRatio(ratio)
        return out

    def _icon_arrived(self, *_args) -> None:
        self.populate()

    def populate(self) -> None:
        row = self.list.currentRow()
        self.list.clear()
        for n in self.win.notifications:
            text = n["title"]
            if n.get("body"):
                text += "\n" + n["body"]
            item = QListWidgetItem(text)
            if not n["read"]:
                bold = item.font()
                bold.setBold(True)
                item.setFont(bold)  # the icon says what it is about; unread is told by the weight
            item.setData(Qt.UserRole, n["id"])
            if (icon := self._icon_of(n)) is not None:
                item.setIcon(QIcon(icon))
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


class ModsPage(Page):
    """A game's mods, one row each, in a small card over the game's page. Enter (or Download) fetches the one on it
    (the server zips a folder first) or, while it is being fetched, asks to cancel it; Delete removes the copy
    already on this computer. Mods are only fetched, never installed. Each row shows its own progress, and where
    the file is once it is done."""

    title = "Mods"
    modal = True

    def __init__(self, win: "MainWindow", game: dict, mods: list[dict]):
        super().__init__()
        self.win, self.game, self.mods = win, game, mods
        note = QLabel(
            "Pick a mod to download it. Nothing is installed: the file is saved in "
            f"{win.app.mod_folder(game)}. A folder is zipped by the server first."
        )
        note.setWordWrap(True)
        self.list = QListWidget()
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(lambda: self._current_then(self.fetch))
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(lambda: self._current_then(self.delete))
        self.render()
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.fetch)  # Enter, A on the pad, a double click: not a single click
        self.list.currentRowChanged.connect(lambda _row: self._sync_buttons())
        win.app.bridge.activity.connect(self.render)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(_row(self.download_button, self.delete_button))
        self._sync_buttons()

    def _local(self, mod: dict) -> list[Path]:
        return mods_download.local_files(self.win.app.mod_folder(self.game), mod)

    def render(self) -> None:
        """One row per mod: what it is, or how its fetch is going, or where its copy on this computer is."""
        app = self.win.app
        row = self.list.currentRow()
        self.list.clear()
        for mod in self.mods:
            key = (self.game["id"], mod["name"])
            if key in app.mod_jobs:
                stage, percent = app.mod_jobs[key]
                what = f"{stage}... {percent}%"
            elif key in app.mod_saved:
                what = f"Downloaded to {app.mod_saved[key]}"
            elif local := self._local(mod):
                what = f"Downloaded to {local[0]}"
            else:
                kind = "folder, zipped on download" if mod.get("kind") == "folder" else mod.get("kind", "file")
                what = f"{kind}, {fmt_bytes(mod.get('size_bytes') or 0)}"
            item = QListWidgetItem(f"{mod['name']}\n{what}")
            item.setData(Qt.UserRole, mod)
            self.list.addItem(item)
        if row >= 0:
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        item = self.list.currentItem()
        mod = item.data(Qt.UserRole) if item is not None else None
        busy = mod is not None and (self.game["id"], mod["name"]) in self.win.app.mod_jobs
        self.download_button.setEnabled(mod is not None)
        self.download_button.setText("Cancel fetching" if busy else "Download")
        self.delete_button.setEnabled(mod is not None and not busy and bool(self._local(mod)))

    def _current_then(self, action) -> None:
        item = self.list.currentItem()
        if item is not None:
            action(item)

    def focus_default(self) -> None:
        self.list.setFocus()

    def fetch(self, item: QListWidgetItem) -> None:
        mod = item.data(Qt.UserRole)
        app = self.win.app
        if (self.game["id"], mod["name"]) in app.mod_jobs:
            self.win.ask(f"Cancel fetching {mod['name']}?", lambda: app.cancel_mod(self.game, mod), danger=True)
            return
        app.download_mod(self.game, mod)
        self.win.notify(f"Fetching the mod {mod['name']}")

    def delete(self, item: QListWidgetItem) -> None:
        """Remove the mod's copy from this computer, after asking."""
        mod = item.data(Qt.UserRole)
        files = self._local(mod)
        if not files:
            return
        names = ", ".join(p.name for p in files)
        self.win.ask(f"Delete {names} from this computer? The mod stays on the server.", lambda: self._delete(mod), danger=True)

    def _delete(self, mod: dict) -> None:
        app = self.win.app
        try:
            removed = mods_download.remove_local(app.mod_folder(self.game), mod)
        except OSError as e:
            self.win.message(f"Could not delete {mod['name']}: {e}", "error")
            return
        app.mod_saved.pop((self.game["id"], mod["name"]), None)
        app.prune_mod_folder(self.game)
        self.win.notify(f"Deleted the mod {mod['name']} ({len(removed)} file{'s' if len(removed) != 1 else ''})")
        self.render()


class OptionsPage(Page):
    """The less common actions of a game: launch engine, shortcuts, and deleting it here or on the server."""

    modal = True
    compact = True

    title = "Options"

    def __init__(self, win: "MainWindow", page: "GamePage", rec: InstalledGame | None):
        super().__init__()
        self.win = win
        self.first: QPushButton | None = None
        content = QWidget()
        lay = QVBoxLayout(content)

        def add(text: str, action, danger: bool = False) -> None:
            button = QPushButton(text)
            button.setProperty("danger", danger)
            button.setFixedWidth(OPTIONS_BUTTON_WIDTH)  # one width for all, centered
            button.setMinimumHeight(button.sizeHint().height())  # a short window scrolls the list, it never squeezes a button
            button.clicked.connect(lambda: (win.back(), action()))
            lay.addWidget(button, 0, Qt.AlignHCenter)
            self.first = self.first or button

        if rec and rec.state == "installed" and installdirs.present(rec):
            if sys.platform != "win32":
                engine = f"{launcher_label(rec.launcher)} (this game)" if rec.launcher != "auto" else "default"
                add(f"Launch engine: {engine}", lambda: page.choose_launcher(rec))
            add("Shortcuts / executable", lambda: page.choose_executable(rec))
            add("Refresh metadata", lambda: page.refresh_metadata(rec))
            on = sync_enabled(rec, win.app.settings)
            add(f"Save sync: {'on' if on else 'off'} (this game)", lambda: page.toggle_save_sync(rec))
            if on:
                add("Back up saves now", lambda: win.saves.backup_now(rec))
                add("Restore a saved version...", lambda: win.saves.restore_pick(rec))
                if rec.game_id in win.saves.restoring:
                    add("Cancel restoring saves", lambda: win.saves.cancel_restore(rec.game_id))
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(FitScrollArea(content))

    def focus_default(self) -> None:
        if self.first:
            self.first.setFocus()


class ChoicePage(Page):
    """A list to pick one answer from, then OK (or Enter on an entry). `skip` adds a red button that
    answers None; Esc or Back leaves without answering. With `confirm`, picking an entry only selects it (Enter or A
    moves on to the next control) and OK is what answers; with a `skip` button the page needs no Close of its own."""

    modal = True

    def __init__(
        self, win: "MainWindow", title: str, text: str, options: list, on_choose, skip: str | None = None, confirm: bool = False
    ):
        super().__init__()
        self.title, self.win, self.on_choose = title, win, on_choose
        self.show_close = skip is None
        note = QLabel(text)
        note.setWordWrap(True)
        self.list = EdgeList()
        for label, value in options:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, value)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.focusNextChild if confirm else self.choose)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons = [ok]
        if skip is not None:
            skip_button = QPushButton(skip)
            skip_button.setProperty("danger", True)
            skip_button.clicked.connect(self.skip)
            buttons.append(skip_button)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(_centered_row(*buttons))

    def focus_default(self) -> None:
        self.list.setFocus()

    def choose(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.UserRole)
        self.win.back()
        self.on_choose(value)

    def accept(self) -> None:
        if self.list.currentItem() is not None:
            self.choose(self.list.currentItem())

    def skip(self) -> None:
        self.win.back()
        self.on_choose(None)


class ChecklistPage(Page):
    """A list to tick any number of entries from, then Done."""

    modal = True

    def __init__(self, win: "MainWindow", title: str, text: str, items: list, on_done):
        super().__init__()
        self.title, self.win, self.on_done = title, win, on_done
        note = QLabel(text)
        note.setWordWrap(True)
        self.list = EdgeList()
        for label, value, *ticked in items:  # an entry may say it starts ticked
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if ticked and ticked[0] else Qt.Unchecked)
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


class SteamAccountsPage(Page):
    """Which Steam accounts MOG and its installed games go into. "All users" takes every account, present and future;
    Continue takes the ticked ones, and none turns the integration off."""

    modal = True
    title = "Steam integration"

    def __init__(self, win: "MainWindow", users: list[Path], chosen: list[str] | None, on_done):
        super().__init__()
        self.win, self.on_done = win, on_done
        note = QLabel("Do you want to add MOG and its installed games to Steam?")
        note.setWordWrap(True)
        self.list = EdgeList()
        for user in users:
            item = QListWidgetItem(f"{selfsteam.label(user, users)} ({user.name})")
            item.setData(Qt.UserRole, user.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if chosen is None or user.name in chosen else Qt.Unchecked)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(self.toggle)
        self.everyone = QPushButton("All users")
        self.everyone.setDefault(True)
        self.everyone.clicked.connect(lambda: self.finish(None))
        go = QPushButton("Continue")
        go.clicked.connect(lambda: self.finish(self.ticked()))
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(_centered_row(self.everyone, go))

    def focus_default(self) -> None:
        self.everyone.setFocus()

    def ticked(self) -> list[str]:
        return [
            self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked
        ]

    def toggle(self, item: QListWidgetItem) -> None:
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def finish(self, accounts: list[str] | None) -> None:
        self.win.back()
        self.on_done(accounts)


class LauncherPage(Page):
    """Pick the engine that starts one game; its desktop entry and Steam shortcut follow."""

    modal = True

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


# The picker's "just extract" entry: install the game's own files as they are, with nothing run.
EXTRACT_AS_IS = {"extract_only": True}


class InstallerPickerPage(Page):
    """The installers of every version of a title, each under its version, to pick which one to run. `needed` is when
    the server has already tried and could not tell which one: then "let the server choose" is not offered."""

    modal = True

    title = "Choose an installer"

    def __init__(self, win: "MainWindow", page: "GamePage", group: Group, needed: bool = False):
        super().__init__()
        self.win, self.page, self.group, self.needed = win, page, group, needed
        self.found: dict[int, list[dict] | str] = {}
        self.portable: set[int] = set()  # versions the server found no installer in: probably the game itself
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

    def on_installers(self, game_id: int, candidates, error: str, portable: bool = False) -> None:
        if game_id in {v["id"] for v in self.group.versions}:
            self.found[game_id] = candidates if candidates is not None else error
            if portable:
                self.portable.add(game_id)
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
            if version["id"] in self.portable:
                entry = QListWidgetItem("    Just extract: use the files as they are, run nothing")
                entry.setData(Qt.UserRole, (version, EXTRACT_AS_IS))
                self.list.addItem(entry)
            if not self.needed:
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
        if self.needed and self.portable:
            pick = "No installer was found, so this is probably the game itself. Pick an executable to run, or extract it as it is:"
        elif self.needed:
            pick = "The server could not tell which installer to run. Pick it:"
        else:
            pick = "Pick the installer to run:"
        self.status.setText(pick if done else "Looking for installers...")
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
        if installer is EXTRACT_AS_IS:
            self.page.start_with(version, None, extract=True)
        else:
            self.page.start_with(version, installer)


class AboutPage(Page):
    """What MOG is, who made it, under what licence and where to find it; opened from the user menu."""

    title = "About"

    def __init__(self) -> None:
        super().__init__()
        self.about = AboutTab()
        QVBoxLayout(self).addWidget(self.about)

    def focus_default(self) -> None:
        self.about.focus_default()


class SettingsPage(Page):
    """Settings in tabs (General, Server, Logs, About); the pad's L2 and R2 switch between them."""

    title = "Settings"
    SAVED_TABS = (0, 1)  # the tabs with fields to save

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        s = win.app.settings
        self.base = MenuLineEdit(s.base)
        self.base.setPlaceholderText("http://server:5000")
        self.user = MenuLineEdit(s.user)
        self.password = MenuLineEdit(s.password)
        self.password.setEchoMode(QLineEdit.Password)
        self.install_dirs = list(s.install_dirs)
        self.folders_button = QPushButton("Edit install dirs")
        self.folders_button.clicked.connect(self.edit_install_dirs)
        self.launcher = MenuCombo()
        self.fill_launchers(s.launcher)
        self.sync_saves_box = Toggle()
        self.sync_saves_box.setChecked(s.sync_saves)
        self.sync_start_box = Toggle()
        self.sync_start_box.setChecked(s.sync_on_start)
        self.sync_start_box.setEnabled(s.sync_saves)  # greyed while saves are not synced at all
        self.sync_saves_box.toggled.connect(self.sync_start_box.setEnabled)
        self.sync_window_box = Toggle()
        self.sync_window_box.setChecked(s.sync_window)
        self.sync_window_box.setEnabled(s.sync_saves)
        self.sync_saves_box.toggled.connect(self.sync_window_box.setEnabled)
        self.upload_logs_box = Toggle()
        self.upload_logs_box.setChecked(s.upload_logs)
        self.upload_logs_box.setEnabled(s.sync_saves)
        self.sync_saves_box.toggled.connect(self.upload_logs_box.setEnabled)
        self.sounds_box = Toggle()
        self.sounds_box.setChecked(s.sounds)
        self.check_updates_box = Toggle()
        self.check_updates_box.setChecked(s.check_updates)
        if updater.supported():
            win.app.bridge.update_checked.connect(self.on_checked)

        self.logview = LogView()
        self.tabs = FocusTabs()
        self.tabs.tabBar().setFocusPolicy(Qt.StrongFocus)  # reached with Up from the first row
        self.general = self._general_tab()
        self.server = self._server_tab()
        self.tabs.addTab(self.general, "General")
        self.tabs.addTab(self.server, "Server")
        self.tabs.addTab(self.logview, "Logs")
        self.tabs.currentChanged.connect(self._on_tab)
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(_row(self.save_button))
        if not s.base:
            self.tabs.setCurrentWidget(self.server)  # a first start begins where the server is entered
        listening = (
            self.tabs.tabBar(),
            self.save_button,
            *self._focus_targets(self.general, enabled_only=False),
            *self._focus_targets(self.server, enabled_only=False),
            *self.logview.findChildren(QPushButton),
            self.logview.view,
        )
        for widget in listening:
            widget.installEventFilter(self)

    # --- moving with Up and Down: rows, then the tabs; past the end back to the first row ---

    @staticmethod
    def _focus_targets(menu: MenuView, enabled_only: bool = True) -> list[QWidget]:
        """The controls of a menu in the order Up and Down visit them; a greyed one is passed over."""
        found = []
        for row in menu.rows:
            inside = [] if isinstance(row.control, QComboBox) else row.control.findChildren(QWidget)  # not a popup's list
            for widget in (row.control, *inside):
                if widget.focusPolicy() != Qt.NoFocus and not widget.isHidden() and (widget.isEnabled() or not enabled_only):
                    found.append(widget)
        return found

    def _chain(self) -> list[QWidget]:
        """What Up and Down walk through on the tab showing."""
        tab = self.tabs.currentWidget()
        if tab is self.logview:
            return self.logview.focus_targets()
        return [*self._focus_targets(tab), self.save_button]

    def navigate(self, down: bool) -> bool:
        """Up from the first row goes to the tabs, Down from the tabs into the first row, and Down from the
        last wraps to the first. Returns False when the key is not for this to decide (a log being scrolled)."""
        bar = self.tabs.tabBar()
        focus = QApplication.focusWidget()
        chain = self._chain()
        if focus is bar:
            if down and chain:
                chain[0].setFocus()
            return True
        if focus not in chain:
            return False
        at = chain.index(focus)
        if focus is self.logview.view:  # a log scrolls; only its edges hand the key on
            scrollbar = self.logview.view.verticalScrollBar()
            if (down and scrollbar.value() < scrollbar.maximum()) or (not down and scrollbar.value() > scrollbar.minimum()):
                return False
        if down:
            chain[(at + 1) % len(chain)].setFocus()
        elif at == 0:
            bar.setFocus()
        else:
            chain[at - 1].setFocus()
        return True

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if (
            event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Up, Qt.Key_Down)
            and not event.modifiers()
            and QApplication.activePopupWidget() is None
        ):
            return self.navigate(event.key() == Qt.Key_Down)
        return False

    def _general_tab(self) -> MenuView:
        menu = MenuView()
        menu.section("Saves")
        menu.add(
            OptionRow(
                "Back up saves",
                "Keep each game's saves on the server and bring a newer save from another machine over, so you can continue a game on any PC.",
                self.sync_saves_box,
            )
        )
        menu.add(
            OptionRow(
                "Check saves at startup",
                "When MOG starts, send what changed here and take a newer save another machine left (what it replaces "
                "is backed up first). Greyed while saves are not backed up.",
                self.sync_start_box,
            )
        )
        menu.add(
            OptionRow(
                "Show a window when saving",
                "When a game started outside MOG (a shortcut or Steam) ends, show a small window while its saves are "
                "backed up (a newer save found when a game starts always shows one). Greyed while saves are not backed up.",
                self.sync_window_box,
            )
        )
        menu.add(
            OptionRow(
                "Send the log with saves",
                "When saves are backed up, also send this device's log to the server, so a problem on a machine you cannot "
                "reach can be read (Profile > My devices > Log). Off by default. Greyed while saves are not backed up.",
                self.upload_logs_box,
            )
        )
        menu.section("Games")
        self._folders_row = menu.add(
            OptionRow(
                "Install folders",
                "Where games are installed, in order of priority: the first folder that is connected and has room is "
                "used. Games in any of them show in the library.",
                self.folders_button,
            )
        )
        self._update_folders_row()
        menu.add(
            OptionRow(
                "Launcher",
                "What starts the games: the first of Faugus, umu, Proton and Wine that is installed, unless you pick "
                "another. Each game gets a prefix of its own in its folder (pfx), which the launcher fills."
                if sys.platform != "win32"
                else "Games run directly.",
                self.launcher,
                wide=True,
            )
        )
        self.launcher_status = QLabel()  # replaced below on systems that have launchers to look for
        if sys.platform != "win32":
            rescan = QPushButton("Rescan")
            rescan.clicked.connect(self.rescan_launchers)
            row = menu.add(
                OptionRow(
                    "Scan for launchers",
                    "Look again for what is installed. This also happens at the first start and after an update.",
                    rescan,
                )
            )
            self.launcher_status = row.status
            self._scan_row = row
        menu.section("Application")
        self.update_status = QLabel()
        if updater.supported():
            menu.add(OptionRow("Check for updates at startup", "Offer a new version when one is out.", self.check_updates_box))
            check_now = QPushButton("Check now")
            check_now.clicked.connect(self.check_updates)
            row = menu.add(OptionRow("Check for updates", "Look for a new version right now.", check_now))
            self.update_status = row.status
            self._update_row = row
        self.steam_button = QPushButton("Settings")
        self.steam_button.clicked.connect(self.win.open_steam_integration)
        self._steam_row = menu.add(
            OptionRow(
                "Steam Integration",
                "Put MOG Client and the games installed from it in Steam's library, with their artwork, so they open from "
                "Steam and from Game Mode. Choose which Steam accounts get them.",
                self.steam_button,
            )
        )
        self.win.steam_changed.connect(self._update_steam_row)
        self._update_steam_row()
        guide = QPushButton("Run again")
        guide.clicked.connect(lambda: self.win.open_first_run(rerun=True))
        menu.add(
            OptionRow(
                "Setup guide",
                "Go through the first-start questions again: the server, where games go, saves and Steam. It starts from "
                "what is set now, and nothing is reset.",
                guide,
            )
        )
        menu.add(
            OptionRow(
                "Sounds",
                "A short sound when moving through the menus and when a game starts.",
                self.sounds_box,
            )
        )
        return menu

    def _update_steam_row(self) -> None:
        self.steam_button.setEnabled(selfsteam.available())
        self._steam_row.set_status(selfsteam.status(self.win.app.settings))

    def _server_tab(self) -> MenuView:
        menu = MenuView()
        menu.section("MOG-Server")
        menu.add(OptionRow("Server URL", "The address of your MOG-Server, for example http://server:5000.", self.base, wide=True))
        menu.add(OptionRow("User", "The account to sign in with.", self.user, wide=True))
        menu.add(OptionRow("Password", "Kept in this device's settings file, readable only by you.", self.password, wide=True))
        return menu

    def switch_tab(self, step: int) -> None:
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def _on_tab(self, index: int) -> None:
        self.save_button.setVisible(index in self.SAVED_TABS)
        if QApplication.focusWidget() is not self.tabs.tabBar():  # switching tabs from the bar keeps the focus there
            self.focus_default()
        self.win.refresh_legend()

    def scroll_logs(self, amount: float) -> bool:
        """The right stick: scroll the log when its tab is the one showing."""
        if self.tabs.currentWidget() is not self.logview:
            return False
        self.logview.scroll_by(amount)
        return True

    def fill_launchers(self, preference: str) -> None:
        """The launcher list as the last scan found it, with the current choice selected."""
        available = available_launchers()
        self.launcher.clear()
        for name in available:
            self.launcher.addItem(launcher_label(name), name)
        chosen = detect_launcher(preference)
        if chosen:
            self.launcher.setCurrentIndex(available.index(chosen))
        self.launcher.setEnabled(len(available) > 1)

    def rescan_launchers(self) -> None:
        settings = self.win.app.settings
        keep = self.launcher.currentData() or settings.launcher
        found = ensure_scanned(settings, rescan=True)
        self.fill_launchers(keep)
        self.launcher_status.setText(
            "Found: " + ", ".join(launcher_label(n) for n in found)
            if found
            else "No launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine."
        )

    def edit_install_dirs(self) -> None:
        self.win.push(InstallDirsPage(self.win, self.install_dirs, self._folders_changed))

    def _folders_changed(self) -> None:
        """The folders are kept as soon as they change: that page has no Save of its own."""
        settings = self.win.app.settings
        settings.install_dirs = list(self.install_dirs)
        save_settings(settings)
        self._update_folders_row()

    def _update_folders_row(self) -> None:
        count = len(self.install_dirs)
        self._folders_row.set_status(
            f"{count} folder{'s' if count != 1 else ''}, {sum(installdirs.reachable(Path(d)) for d in self.install_dirs)} connected"
            if count
            else f"None listed: games go into {default_install_root()}"
        )

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
        tab = self.tabs.currentWidget()
        if tab is self.logview:
            tab.focus_default()
        elif tab is self.server:
            self.base.setFocus()
        else:
            self.sync_saves_box.setFocus()

    def save(self) -> None:
        old = self.win.app.settings
        self.win.app.settings = replace(
            old,
            base=self.base.text().strip(),
            user=self.user.text().strip(),
            password=self.password.text(),
            install_dirs=list(self.install_dirs),
            # "auto" keeps following the preference order (Faugus first) as launchers come and go.
            launcher="auto" if self.launcher.currentData() == detect_launcher() else self.launcher.currentData() or "auto",
            check_updates=self.check_updates_box.isChecked(),
            sync_saves=self.sync_saves_box.isChecked(),
            sync_on_start=self.sync_start_box.isChecked(),
            sync_window=self.sync_window_box.isChecked(),
            upload_logs=self.upload_logs_box.isChecked(),
            sounds=self.sounds_box.isChecked(),
        )
        save_settings(self.win.app.settings)
        self.win.back()
        self.win.app.refresh()


class InstallDirsPage(Page):
    """The install folders in order of priority: + adds one, - removes the selected one (the games in it stay
    on disk), and the arrows beside them change which is tried first. `dirs` is edited in place."""

    modal = True

    title = "Install folders"

    def __init__(self, win: "MainWindow", dirs: list[str], changed) -> None:
        super().__init__()
        self.win, self.dirs, self.changed = win, dirs, changed
        note = QLabel(
            "A new game goes into the first folder that is connected and has room; when an earlier one is full you "
            "are asked before the next is used. A folder whose drive is not connected is passed over, and its games "
            "stay in the library, greyed."
        )
        note.setWordWrap(True)
        self.list = QListWidget()
        self.list.installEventFilter(self)
        self.list.currentRowChanged.connect(self._sync_buttons)
        self.add_button, self.remove_button = QPushButton("+"), QPushButton("-")
        self.remove_button.setProperty("danger", True)
        self.up_button, self.down_button = QPushButton("Move up"), QPushButton("Move down")
        for button in (self.add_button, self.remove_button):
            button.setFixedWidth(72)
        self.add_button.setToolTip("Add an install dir")
        self.remove_button.setToolTip("Remove the selected install dir from the list")
        self.add_button.clicked.connect(self.add)
        self.remove_button.clicked.connect(self.remove)
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button.clicked.connect(lambda: self.move(1))
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(_centered_row(self.add_button, self.remove_button, self.up_button, self.down_button))
        self._fill()

    def focus_default(self) -> None:
        (self.list if self.dirs else self.add_button).setFocus()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        """Down from the last folder (or from an empty list) reaches the buttons below."""
        if obj is self.list and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
            if self.list.currentRow() >= len(self.dirs) - 1:
                self.add_button.setFocus()
                return True
        return False

    def _fill(self, keep: int = 0) -> None:
        self.list.clear()
        if not self.dirs:
            item = QListWidgetItem(f"None listed: games go into {default_install_root()}")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        for i, folder in enumerate(self.dirs):
            self.list.addItem(f"{i + 1}.  [{installdirs.status(Path(folder))}]  {folder}")
        if self.dirs:
            self.list.setCurrentRow(min(max(keep, 0), len(self.dirs) - 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        row, count = (self.list.currentRow() if self.dirs else -1), len(self.dirs)
        self.remove_button.setEnabled(row >= 0)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)

    def add(self) -> None:
        start = Path(self.dirs[-1]).parent if self.dirs else Path.home()
        while not start.is_dir() and start.parent != start:
            start = start.parent
        self.win.push(BrowsePage(self.win, start, self._added, folders=True))

    def _added(self, folder: str) -> None:
        folder = str(Path(folder).expanduser())
        if folder not in self.dirs:
            self.dirs.append(folder)
        self.changed()
        self._fill(self.dirs.index(folder))
        self.add_button.setFocus()

    def remove(self) -> None:
        row = self.list.currentRow()
        if not self.dirs or row < 0:
            return
        folder = self.dirs[row]
        self.win.ask(
            f"Remove {folder} from the install folders? The games in it stay on disk, and show greyed in the library.",
            lambda: self._remove(folder),
            danger=True,
        )

    def _remove(self, folder: str) -> None:
        if folder not in self.dirs:
            return
        row = self.dirs.index(folder)
        del self.dirs[row]
        self.changed()
        self._fill(row)
        self.focus_default()

    def move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if not self.dirs or not 0 <= row < len(self.dirs) or not 0 <= target < len(self.dirs):
            return
        self.dirs[row], self.dirs[target] = self.dirs[target], self.dirs[row]
        self.changed()
        self._fill(target)
        button = self.down_button if step > 0 else self.up_button
        (button if button.isEnabled() else self.list).setFocus()


class BrowsePage(Page):
    """In-window file picker (no native dialog, so it stays gamepad friendly), in a small card over its page."""

    title = "Browse for the executable"
    modal = True

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

    modal = True
    show_close = False  # Later is the way out

    def __init__(self, win: "MainWindow", rec: InstalledGame, on_done):
        super().__init__()
        self.title = f"Choose the executable for {rec.name}"
        self.win, self.on_done = win, on_done
        self.root = Path(rec.install_dir)
        self.list = EdgeList()
        for exe in list_executables(self.root):
            item = QListWidgetItem(f"{exe.relative_to(self.root)}   ({fmt_bytes(exe.stat().st_size)})")
            item.setData(Qt.UserRole, str(exe))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(lambda _: self.focusNextChild())  # picking is not confirming
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        self.desktop = Toggle("Create a desktop entry")
        self.desktop.setChecked(sys.platform != "win32")
        self.desktop.setVisible(sys.platform != "win32")
        self.steam = Toggle("Add to Steam")
        self.account_toggles: dict[Path, Toggle] = {}
        self.steam.toggled.connect(lambda _: self._update_steam_toggles())
        self.refresh_steam()
        ok = QPushButton("Use this executable")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        later = QPushButton("Later")
        later.clicked.connect(win.back)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Which file starts the game?"))
        lay.addWidget(self.list, 1)
        lay.addWidget(self.desktop)
        lay.addWidget(self.steam)
        self.accounts_box = QVBoxLayout()
        lay.addLayout(self.accounts_box)
        self._fill_accounts()
        row = QHBoxLayout()
        row.addWidget(browse)
        row.addStretch()
        row.addWidget(later)
        row.addWidget(ok)
        lay.addLayout(row)

    def focus_default(self) -> None:
        self.list.setFocus()

    def refresh_steam(self) -> None:
        """The Steam switches as the integration settings stand: off where the user left an account out (or all of them)."""
        settings = self.win.app.settings
        self.steam_found = steam.steam_user_dirs()
        self.steam_wanted = selfsteam.accounts(settings)
        self.steam.setEnabled(bool(self.steam_wanted))
        self.steam.setChecked(bool(self.steam_wanted))
        if hasattr(self, "accounts_box"):
            self._fill_accounts()

    def _fill_accounts(self) -> None:
        while self.accounts_box.count():
            self.accounts_box.takeAt(0).widget().deleteLater()
        self.account_toggles = {}
        if len(self.steam_found) > 1:  # one account needs no choosing
            for user in self.steam_found:
                toggle = Toggle(selfsteam.label(user, self.steam_found))
                toggle.setChecked(user in self.steam_wanted)
                self.account_toggles[user] = toggle
                self.accounts_box.addWidget(toggle)
        self._update_steam_toggles()

    def _update_steam_toggles(self) -> None:
        for user, toggle in self.account_toggles.items():
            toggle.setEnabled(self.steam.isChecked() and user in self.steam_wanted)

    def steam_users(self) -> list[Path]:
        if not self.steam.isChecked():
            return []
        if not self.account_toggles:
            return list(self.steam_wanted)
        return [user for user, toggle in self.account_toggles.items() if toggle.isChecked() and user in self.steam_wanted]

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
        users = self.steam_users()
        self.win.back()
        self.on_done(item.data(Qt.UserRole), users, self.desktop.isChecked())


SHOT_SIZE = QSize(224, 126)


class SearchBox(QLineEdit):
    """The search field: a cross in its corner empties it, and so does Ctrl+Backspace (which would otherwise delete a
    word), the keyboard's way of the pad's R2."""

    def __init__(self) -> None:
        super().__init__()
        self._clear = QAction(clear_icon(), "Clear the search", self)
        self._clear.triggered.connect(self.empty)
        self.addAction(self._clear, QLineEdit.TrailingPosition)
        self._clear.setVisible(False)
        self.textChanged.connect(lambda text: self._clear.setVisible(bool(text)))

    def empty(self) -> None:
        self.clear()
        self.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        if e.key() == Qt.Key_Backspace and e.modifiers() & Qt.ControlModifier:
            self.clear()
            e.accept()
            return
        super().keyPressEvent(e)


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
        # Some desktop styles keep a field at its size hint, and a word-wrapped label's hint is a
        # narrow guess: the text then wraps early and its row is cut off.
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(5)

        def row(label: str, widget: QLabel) -> None:
            key = QLabel(label.upper())  # the name small, dim and capitalised, its value large and bright
            key.setObjectName("metaKey")
            key.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            widget.setObjectName("metaValue")
            form.addRow(key, widget)

        # Wrapped labels here are ParagraphLabels: a plain word-wrapped one makes the header's height depend on its
        # width, and when the window is short the form rows are squeezed under their own height.
        for label, value in metadata_lines(game):
            row(label, ParagraphLabel(value, max_lines=3, min_lines=1))
        self.size_label = QLabel("...")
        row("Size on server", self.size_label)
        self.played_label = QLabel()
        row("Last played", self.played_label)
        self.sync_label = ParagraphLabel(max_lines=2, min_lines=1)
        row("Last save sync", self.sync_label)
        self.sync_key = form.labelForField(self.sync_label)
        info_col.addLayout(form)
        text = game.get("summary") or (game.get("igdb_metadata") or {}).get("summary") or ""
        summary = ParagraphLabel(text, min_lines=3)
        summary.setObjectName("summary")
        info_col.addSpacing(12)
        info_col.addWidget(summary)
        info_col.addStretch()
        head = QHBoxLayout()
        head.setContentsMargins(14, 14, 14, 14)
        head.setSpacing(24)
        head.addWidget(self.cover)
        head.addLayout(info_col, 1)
        self.hltb_box = QFrame()
        self.hltb_box.setObjectName("hltbBox")
        QGridLayout(self.hltb_box).setContentsMargins(16, 12, 16, 12)
        head.addWidget(self.hltb_box, 0, Qt.AlignTop)
        self._show_hltb()
        self.header = HeaderArt()
        self.header.setLayout(head)

        self.shots = StripList()
        self.shots.setFixedHeight(SHOT_SIZE.height() + 30)
        self.shot_urls = screenshot_urls(game)
        self.shot_pix: dict[str, QPixmap] = {}
        self.shots.setVisible(bool(self.shot_urls))
        self.shots.itemActivated.connect(self._open_shot)

        self.status = QLabel()
        self.bar = QProgressBar()
        self.buttons = QHBoxLayout()
        self.first: QPushButton | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.shots)
        lay.addSpacing(6)
        for w in (self.status, self.bar):
            lay.addWidget(w)
        lay.addSpacing(14)  # lifts the bar, and the screenshots above it, off the buttons
        lay.addLayout(self.buttons)
        b = self.app.bridge
        b.progress.connect(self._on_progress)
        b.log.connect(self._on_log)
        b.finished.connect(self._on_finished)
        b.image.connect(self._on_image)
        b.game_size.connect(self._on_size)
        b.game_sizes.connect(self._on_sizes)
        b.mods_loaded.connect(self._on_mods)
        b.played.connect(self._on_played)
        b.header_art.connect(self._on_header_art)
        b.cover.connect(lambda gid, _blob: gid == self.game["id"] and self._set_cover(win.covers.get(gid)))
        self.app.fetch_images(self.shot_urls, game["id"])
        self.app.fetch_header_art(game)
        self._update_version_label()
        self.app.load_size(game["id"])
        self.app.load_mods(game["id"])
        self._show_play_rows()
        self.rebuild()

    def _show_hltb(self) -> None:
        """The HowLongToBeat times as a small table at the right of the header; no table when there are none."""
        grid = self.hltb_box.layout()
        while grid.count():
            grid.takeAt(0).widget().deleteLater()
        rows = hltb_lines(self.game)
        self.hltb_box.setVisible(bool(rows))
        if not rows:
            return
        title = QLabel("HOW LONG TO BEAT")
        title.setObjectName("hltbTitle")
        grid.addWidget(title, 0, 0, 1, 2)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        for i, (label, value) in enumerate(rows, start=1):
            name, time = QLabel(label), QLabel(value)
            name.setObjectName("hltbLabel")
            time.setObjectName("hltbTime")
            time.setAlignment(Qt.AlignRight)
            grid.addWidget(name, i, 0)
            grid.addWidget(time, i, 1)

    def _on_mods(self, gid: int, found: list) -> None:
        if gid == self.game["id"] and found:  # the Mods button appears; a game with none changes nothing
            had_focus = self.first is not None and self.first.hasFocus()
            self.rebuild()
            if had_focus and self.first:
                self.first.setFocus()

    def open_mods(self) -> None:
        self.win.push(ModsPage(self.win, self.game, self.app.mods.get(self.game["id"], [])))

    def _on_size(self, gid: int, size: int) -> None:
        if gid == self.game["id"]:
            self.size_label.setText(fmt_bytes(size))

    def _on_sizes(self, gid: int, held: dict) -> None:
        if gid == self.game["id"]:
            parts = [
                f"{name} {fmt_bytes(held[key])}"
                for name, key in (("Installer", "installer"), ("Cache", "cache"), ("Saves", "saves"))
                if held[key]
            ]
            total = fmt_bytes(held["total"])
            self.size_label.setText(f"{total} ({', '.join(parts)})" if len(parts) > 1 else total)

    def _on_played(self, gid: int) -> None:
        group = self._group()
        if gid == self.game["id"] or (group is not None and any(m["id"] == gid for m in group.members)):
            self._show_play_rows()

    def _show_play_rows(self) -> None:
        """When this machine last started the game, and whether its saves were synced when it was closed."""
        gid = self.game["id"]
        group = self._group()
        members = group.members if group else [self.game]
        device = devices.known_device()
        played, machine = ordering.last_played_on(
            Group(members=members, versions=members), played_at.history(), device.name if device else None
        )
        text = sync_ui.when_epoch(played) if played else "Never"
        self.played_label.setText(f"{text} on {machine}" if played and machine else text)
        rec = load_library().get(gid)
        enabled = bool(rec) and sync.enabled(rec, self.app.settings)
        self.sync_key.setVisible(enabled)
        self.sync_label.setVisible(enabled)
        if enabled:
            self.sync_label.set_paragraph(sync_ui.describe_check(load_state(gid)))

    def _on_header_art(self, gid: int, blob: bytes) -> None:
        pix = QPixmap()
        if gid == self.game["id"] and pix.loadFromData(blob):
            self.header.set_image(pix)

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
        self._show_hltb()
        self.header.set_image(None)
        self.app.fetch_header_art(game)
        self.app.load_size(game["id"])
        self.app.load_mods(game["id"])
        self._show_play_rows()
        self.rebuild()

    def start_with(self, version: dict, installer: dict | None, extract: bool = False) -> None:
        self.switch_version(version)
        if extract:
            self.win.install_game(version, installer, self._installing, extract=True)
        else:
            self.win.install_game(version, installer, self._installing)

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
            logstore.info(f"{self.game['name']}: {msg}")

    def _on_finished(self, gid: int, err: str) -> None:
        if gid != self.game["id"]:
            return
        if err.startswith(manager.NEEDS_PICK):
            self.rebuild()
            self._ask_for_installer()
            return
        if err:
            self.win.message(f"{self.game['name']}: {err}", "error")
        self.rebuild()
        rec = load_library().get(gid)
        if rec and rec.state == "awaiting_executable" and not err:
            if self.win.current_page() is self:
                self.choose_executable(rec)
            else:
                self.win.message(f"{self.game['name']} finished installing: open it to choose the executable", "info")

    def _ask_for_installer(self) -> None:
        """The server could not choose the installer: the person does, here, from the list of what it found."""
        group = self._group()
        if self.win.current_page() is not self or group is None:
            self.win.message(
                f"{self.game['name']}: the server could not tell which installer to run. Open the game's page to choose which installer to run.",
                "warning",
            )
            return
        self.win.push(InstallerPickerPage(self.win, self, group, needed=True))

    def _check_again(self) -> None:
        self.rebuild()
        self.win.library.update_presence()
        if self.first:
            self.first.setFocus()

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
        self.play_ring = None
        self._fill_buttons()
        self.buttons.addStretch()
        self.update_transfer()

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
        if rec and rec.state in ("installed", "awaiting_executable") and not installdirs.present(rec):
            self.status.setText(f"Not available: {rec.install_dir} is not connected")
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.first = self._button("Check again", self._check_again, True)
            self._button("Options", lambda: self.show_options(rec))
        elif rec and rec.state == "installed":
            self.status.setText("Installed")
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.first = self._button("Play", self.play, True)
            self.play_ring = SpinRing(self.first)
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
        if self.app.mods.get(gid):
            self._button("Mods", self.open_mods)

    def install(self) -> None:
        group = self._group()
        local = load_library()
        fresh = group is not None and not any(m["id"] in local for m in group.members)
        if group is not None and len(group.versions) > 1 and fresh:
            self.win.push(InstallerPickerPage(self.win, self, group))
            return
        self.win.install_game(self.game, None, self._installing)

    def _installing(self) -> None:
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

    def update_transfer(self) -> None:
        """While this game's saves are being downloaded or uploaded: a pie on the cover (how far a download is) and a ring
        turning round Play."""
        groups = self.app.group_of
        mine = groups.get(self.game["id"])
        kind, percent = None, None
        for gid, job in self.win.saves.restoring.items():
            if groups.get(gid) is mine:
                kind, percent = "download", 100 if job.applying else job.percent
        if kind is None:
            for gid in self.win.saves.uploading | self.win.library.syncing:
                if groups.get(gid) is mine:
                    kind = "upload"
        if kind is None:
            if hasattr(self, "_pie"):
                self._pie.clear()
        else:
            if not hasattr(self, "_pie"):
                self._pie = TransferPie(self.header)
            self._pie.show_transfer(self.cover, kind, percent)
        ring = getattr(self, "play_ring", None)
        if ring is not None:
            try:
                ring.set_active(kind is not None)
            except RuntimeError:  # the button it sat on was rebuilt and is gone
                self.play_ring = None

    def pulse(self) -> None:
        """A task was queued for this game: the cover flashes and a ring spreads out from it."""
        if not hasattr(self, "_ring"):
            self._ring = RingPulse(self.header)
        self._ring.fire(self.cover)

    def play(self) -> None:
        rec = load_library()[self.game["id"]]
        if self.win.saves.wait_for_restore(rec, lambda: self.start(rec)):
            return  # its saves are still coming: the user was told, and can cancel that and play anyway
        self.win.saves.before_launch(rec, lambda: self.start(rec))

    def start(self, rec: InstalledGame) -> None:
        try:
            proc = launch(rec, self.app.settings.launcher)
        except (RuntimeError, OSError) as e:
            self.win.message(str(e), "error")
            return
        self.win.notify(f"Starting {rec.name}...")
        self.win.sounds.play(PLAY)
        self.win.begin_play(rec, proc)

    def show_options(self, rec: InstalledGame | None) -> None:
        self.win.push(OptionsPage(self.win, self, rec))

    def toggle_save_sync(self, rec: InstalledGame) -> None:
        now = sync_enabled(rec, self.app.settings)
        wanted = not now
        manager._update(rec, save_sync=None if wanted == self.app.settings.sync_saves else wanted)
        self.win.message(f"Save sync for {rec.name} is {'on' if wanted else 'off'}", "info")

    def refresh_metadata(self, rec: InstalledGame) -> None:
        """Fetch the game's metadata and artwork from the server again and rebuild its icons, entries and
        Steam shortcut from them (the shortcut is edited, not recreated)."""

        def work() -> None:
            game, _changed = manager.refresh_metadata(rec, self.app.client(), self.app.settings.launcher)
            self.app.games[rec.game_id] = game
            self.app.bridge.message.emit(*steam_outcome(rec, f"Metadata of {rec.name} refreshed"))
            self.app.bridge.call.emit(self.app.refresh)

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not refresh: {m}"))

    def delete_server_cache(self) -> None:
        gid = self.game["id"]

        def go() -> None:
            def work():
                self.app.client().clear_cache(gid)
                self.app.bridge.message.emit("info", "The install cache on the server was deleted")

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
                    manager.set_launcher(rec, engine, self.app.settings.launcher)
                except RuntimeError as e:
                    self.app.bridge.error.emit(str(e))
                    return
                what = launcher_label(engine) if engine != "auto" else "the default engine"
                self.app.bridge.message.emit(*steam_outcome(rec, f"{rec.name} now launches through {what}"))
                self.app.bridge.finished.emit(rec.game_id, "")

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(m))

        self.win.push(LauncherPage(self.win, rec, done))

    def choose_executable(self, rec: InstalledGame) -> None:
        def done(exe: str, steam_users: list[Path], desktop: bool) -> None:
            logstore.info(f"{rec.name}: updating entries and fetching artwork...")

            def work():
                manager.finish_setup(
                    rec, self.game, exe, steam_users, desktop, self.app.settings.launcher, self.app.client()
                )
                if rec.steam_pending:
                    self.app.bridge.message.emit(*steam_outcome(rec, f"{rec.name} is ready"))
                self.app.bridge.finished.emit(rec.game_id, "")
                self.app.bridge.call.emit(lambda: self.win.saves.offer_after_install(rec))

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.finished.emit(rec.game_id, m))

        page = ExecutablePage(self.win, rec, done)
        self.win.push(page)
        if selfsteam.undecided(self.app.settings):  # the first install: ask which accounts, over the page
            self.win.open_steam_integration(then=page.refresh_steam)

    def uninstall(self, rec: InstalledGame, and_server_cache: bool = False) -> None:
        def remove(delete_prefix: bool) -> None:
            had_steam_shortcut = bool(rec.steam_entries) and steam.steam_running()
            leftovers = manager.uninstall(rec, delete_prefix)
            if had_steam_shortcut:  # Steam writes its own copy of the file back when it quits
                self.win.message(
                    f"Steam is running, so it puts the shortcut of {rec.name} back when it quits. "
                    "Close Steam and delete it there.",
                    "warning",
                )
            if and_server_cache:
                self.app.run_bg(
                    lambda: self.app.client().clear_cache(rec.game_id),
                    on_error=lambda m: self.app.bridge.error.emit(f"Could not delete the server cache: {m}"),
                )
            if leftovers:
                self.win.message(f"Could not remove everything, delete by hand: {', '.join(leftovers)}", "error")
            else:
                self.win.message(
                    f"{rec.name} uninstalled" + (" and its server cache deleted" if and_server_cache else ""), "info"
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


ROLE_COVER, ROLE_PROGRESS, ROLE_INSTALLED, ROLE_CORNER, ROLE_ABSENT, ROLE_PULSE, ROLE_BUSY = (Qt.UserRole + n for n in range(1, 8))
_GREY: dict[int, QPixmap] = {}  # a cover's grey twin, by the cover's cache key


def greyed(pix: QPixmap | None) -> QPixmap | None:
    """The cover without its colour, for a game whose folder is not there."""
    if pix is None or pix.isNull():
        return pix
    if pix.cacheKey() not in _GREY:
        _GREY[pix.cacheKey()] = QPixmap.fromImage(pix.toImage().convertToFormat(QImage.Format_Grayscale8))
    return _GREY[pix.cacheKey()]
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
    """Cover art with an install progress bar along its bottom edge, then the title. A game with a task going on has a
    ring turning round its cover (`angle` is where its head is, moved by the library page)."""

    angle = 0.0

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
        absent = bool(index.data(ROLE_ABSENT))
        if absent:
            painter.fillRect(cover, QColor(14, 16, 20, 150))
        painter.setClipRect(cover)
        if index.data(ROLE_INSTALLED):
            paint_installed_badge(painter, cover)
        if state := index.data(ROLE_CORNER):
            paint_state_badge(painter, cover, state)
        painter.setClipping(False)
        if index.data(ROLE_BUSY):
            paint_spin_ring(painter, cover.adjusted(-2, -2, 2, 2), self.angle)
        if (pulse := index.data(ROLE_PULSE)) is not None:
            paint_ring(painter, cover, pulse)
        painter.setPen(QColor("#6b7380" if absent else "#e8eaed"))
        text = QRect(cell.left() + 4, cover.bottom() + 6, cell.width() - 8, cell.bottom() - cover.bottom() - 6)
        painter.drawText(text, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, index.data(Qt.DisplayRole))
        painter.restore()


def paint_ring(painter: QPainter, rect: QRect, progress: float, spread: int = CARD_RING) -> None:
    """A task has just started for what is in `rect`: it flashes and a ring spreads out from it and fades."""
    fade = 1.0 - progress
    painter.save()
    painter.fillRect(rect, QColor(255, 255, 255, int(110 * fade)))
    grow = int(spread * progress)
    gradient = accent_gradient(rect.left() - grow, rect.top(), rect.right() + grow, rect.bottom())
    painter.setOpacity(fade)
    painter.setPen(QPen(QBrush(gradient), 3 + spread // 6))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), 6, 6)
    painter.restore()


class RingPulse(QWidget):
    """The same ring over a widget that is not in the grid (the cover on a game's page), where there is no sidebar to
    show that a task was queued."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.progress = 1.0
        self.target: QWidget | None = None
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(CARD_PULSE_MS)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._frame)
        self.animation.finished.connect(self.hide)
        self.hide()

    def fire(self, target: QWidget) -> None:
        self.target = target
        room = PAGE_RING + 6
        self.setGeometry(target.geometry().adjusted(-room, -room, room, room))
        self.show()
        self.raise_()
        self.animation.stop()
        self.animation.start()

    def _frame(self, progress: float) -> None:
        self.progress = progress
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        room = PAGE_RING + 6
        paint_ring(painter, self.rect().adjusted(room, room, -room, -room), self.progress, PAGE_RING)


class TransferPie(QWidget):
    """Over a game's cover while its saves are being downloaded or uploaded: the cover dims and a pie fills clockwise as
    the download goes; an upload, which has no figure to show, is a wedge going round."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.kind = "download"
        self.percent: int | None = None
        self.angle = 0.0
        self.spin = QVariantAnimation(self)
        self.spin.setStartValue(0.0)
        self.spin.setEndValue(360.0)
        self.spin.setDuration(SPIN_MS)
        self.spin.setLoopCount(-1)
        self.spin.valueChanged.connect(self._turn)
        self.hide()

    def show_transfer(self, target: QWidget, kind: str, percent: int | None) -> None:
        self.kind, self.percent = kind, percent
        self.setGeometry(target.geometry())
        if not self.isVisible():
            self.show()
        self.raise_()
        if percent is None and self.spin.state() != QAbstractAnimation.Running:
            self.spin.start()
        elif percent is not None:
            self.spin.stop()
        self.update()

    def clear(self) -> None:
        self.spin.stop()
        self.hide()

    def _turn(self, angle: float) -> None:
        self.angle = angle
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_pie(painter, self.rect(), self.kind, self.percent, self.angle)


def paint_pie(painter: QPainter, rect: QRect, kind: str, percent: int | None, angle: float = 0.0) -> None:
    """The dimmed cover with a pie on it: filled clockwise to `percent`, or a wedge at `angle` when there is no figure,
    an arrow in the middle for which way the saves go, and the percent beneath."""
    painter.save()
    painter.fillRect(rect, QColor(0, 0, 0, 120))
    radius = int(min(rect.width(), rect.height()) * 0.28)
    disc = QRect(rect.center().x() - radius, rect.center().y() - radius, radius * 2, radius * 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(20, 24, 32, 225))
    painter.drawEllipse(disc.adjusted(-5, -5, 5, 5))
    painter.setBrush(QBrush(accent_gradient(disc.left(), disc.top(), disc.right(), disc.bottom())))
    if percent is None:
        painter.drawPie(disc, int((90 - angle) * 16), -100 * 16)  # a wedge going round, clockwise
    else:
        painter.drawPie(disc, 90 * 16, -int(360 * 16 * max(0, min(percent, 100)) / 100))
    painter.setPen(QPen(QColor(20, 24, 32, 230), 3))  # the arrow keeps a dark edge over the pie, filled or not
    painter.setBrush(QColor("white"))
    c, h = disc.center(), disc.height() / 4.5
    shaft, head, flip = h * 0.3, h * 0.8, -1 if kind == "upload" else 1
    outline = [(-shaft, -h), (shaft, -h), (shaft, 0.1 * h), (head, 0.1 * h), (0, h), (-head, 0.1 * h), (-shaft, 0.1 * h)]
    painter.drawPolygon(QPolygonF([QPointF(c.x() + x, c.y() + flip * y) for x, y in outline]))
    if percent is not None:
        painter.setPen(QColor("white"))
        label = QRect(rect.left(), disc.bottom() + 12, rect.width(), 30)
        painter.drawText(label, Qt.AlignHCenter | Qt.AlignTop, f"{percent}%")
    painter.restore()


class SpinRing(QWidget):
    """A ring that turns round the edge of the Play button while the game's saves are on the move."""

    def __init__(self, button: QWidget) -> None:
        super().__init__(button)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.angle = 0.0
        self.spin = QVariantAnimation(self)
        self.spin.setStartValue(0.0)
        self.spin.setEndValue(360.0)
        self.spin.setDuration(SPIN_MS)
        self.spin.setLoopCount(-1)
        self.spin.valueChanged.connect(self._turn)
        button.installEventFilter(self)
        self.hide()

    def set_active(self, active: bool) -> None:
        if active:
            self.setGeometry(self.parentWidget().rect())
            self.show()
            self.raise_()
            if self.spin.state() != QAbstractAnimation.Running:
                self.spin.start()
        else:
            self.spin.stop()
            self.hide()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
        return False

    def _turn(self, angle: float) -> None:
        self.angle = angle
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_spin_ring(painter, self.rect(), self.angle)


def paint_spin_ring(painter: QPainter, rect: QRect, angle: float) -> None:
    """A bright arc with a fading tail, round the inside of a rounded button; `angle` is where its head is."""
    painter.save()
    gradient = QConicalGradient(QPointF(rect.center()), -angle)
    gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
    gradient.setColorAt(0.4, QColor(255, 255, 255, 0))
    gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setPen(QPen(QBrush(gradient), 3))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)
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
        self.installs.setIconSize(ACTIVE_ICON)
        self.installs.setWordWrap(True)  # a long game or mod name goes over lines, never cut
        self.installs.setTextElideMode(Qt.ElideNone)
        self.installs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.installs.itemActivated.connect(self._open_install)
        self.installs.itemClicked.connect(self._open_install)
        # Running installs sit above the libraries, and the section is only there while one runs.
        self.installs_box = QWidget()
        box = QVBoxLayout(self.installs_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._heading("Active installs"))
        box.addWidget(self.installs)
        self.sorts = EdgeList()
        self.sorts.setItemDelegate(OptionDelegate(self.sorts))
        self.sorts.itemActivated.connect(self._sort_chosen)
        self.sorts.itemClicked.connect(self._sort_chosen)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(self.installs_box)
        side.addWidget(self._heading("Libraries"))
        side.addWidget(self.libs)
        side.addWidget(self._heading("Sort by"))
        side.addWidget(self.sorts)
        side.addStretch(1)
        self.space = QLabel()
        self.space.setStyleSheet("color: #9aa3b0; font-size: 14px; padding: 8px 4px;")
        side.addWidget(self.space)
        self.space_timer = QTimer(self)
        self.space_timer.timeout.connect(self.refresh_space)
        self._ranks: dict[int, tuple[bool, bool]] = {}
        self.syncing: set[int] = set()  # games whose saves are being backed up after they closed
        self._task_keys: set = set()  # the tasks listed the last time, to notice a new one
        self._card_pulses: dict[int, QVariantAnimation] = {}
        self._cards_due: set[int] = set()  # the games whose task started while this page was not in front
        self._busy_gids: set[int] = set()  # the games with a task going on: their covers carry the turning ring
        self._spin = QVariantAnimation(self)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(360.0)
        self._spin.setDuration(SPIN_MS)
        self._spin.setLoopCount(-1)
        self._spin.valueChanged.connect(self._turn_rings)
        self._fill_sorts()
        self.installs_box.setVisible(False)  # only while an install runs (see update_active)
        self.sidebar.setVisible(win.app.settings.show_sidebar)

        self.loading = LoadingPanel()
        self.shelf = QStackedLayout()  # the grid, or Mog at work while the games are being fetched
        self.shelf.addWidget(self.grid)
        self.shelf.addWidget(self.loading)
        lay = QHBoxLayout(self)
        lay.addWidget(self.sidebar)
        lay.addLayout(self.shelf, 1)
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
        # As tall as its rows (up to a limit), so "Sort by" sits right under it and the space left is at the bottom.
        self.libs.setFixedHeight(min(260, self.libs.sizeHintForRow(0) * self.libs.count() + 12))

    def set_libraries(self, libraries: list[dict]) -> None:
        self.libraries = libraries
        self._fill_libraries()

    def _rank(self, group: Group, lib: dict) -> tuple[bool, bool]:
        """(being installed, installed): what puts a title ahead in the order. A game that is only waiting for its
        executable to be chosen has its files but is not playable, so it does not count as installed here."""
        installing = any(m["id"] in self.win.app.installs for m in group.members)
        installed = any(lib.get(m["id"]) and lib[m["id"]].state == "installed" for m in group.members)
        return installing, bool(installed)

    def _fill_sorts(self) -> None:
        """Two switches (last played first, installed first) and the orders, one of which is the chosen one."""
        settings = self.win.app.settings
        row = max(self.sorts.currentRow(), 0)
        self.sorts.blockSignals(True)
        self.sorts.clear()
        switches = (("last_played", "Last played first", settings.sort_last_played), ("installed", "Installed first", settings.sort_installed_first))
        for key, text, on in switches:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, ("switch", key))
            item.setData(ROLE_KIND, "switch")
            item.setData(ROLE_ON, on)
            self.sorts.addItem(item)
        for number, (key, text) in enumerate(ordering.ORDERS.items()):
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, ("order", key))
            item.setData(ROLE_KIND, "radio")
            item.setData(ROLE_ON, settings.sort_order == key)
            item.setData(ROLE_DIVIDER, number == 0)
            self.sorts.addItem(item)
        self.sorts.setCurrentRow(min(row, self.sorts.count() - 1))
        self.sorts.blockSignals(False)
        self.sorts.setFixedHeight(self.sorts.sizeHintForRow(0) * self.sorts.count() + 12)

    def _sort_chosen(self, item: QListWidgetItem) -> None:
        kind, key = item.data(Qt.UserRole)
        settings = self.win.app.settings
        if kind == "switch" and key == "last_played":
            settings.sort_last_played = not settings.sort_last_played
        elif kind == "switch":
            settings.sort_installed_first = not settings.sort_installed_first
        else:
            settings.sort_order = key
        save_settings(settings)
        self._fill_sorts()
        self.populate(self.win.search.text().lower())

    def _open_install(self, item: QListWidgetItem) -> None:
        """An install or a sync row opens the game's page; a mod row opens that game's mods, with Back to its page."""
        gid = item.data(Qt.UserRole)
        if gid is None:
            return
        self.win.show_game(gid)
        if item.data(ROLE_MOD_ROW) is not None:
            page = self.win.current_page()
            if isinstance(page, GamePage):
                page.open_mods()

    def _library_chosen(self, row: int) -> None:
        self.library_filter = self.libraries[row - 1]["id"] if row > 0 else None
        self.populate(self.win.search.text().lower())

    def show_loading(self, loading: bool) -> None:
        self.shelf.setCurrentWidget(self.loading if loading else self.grid)
        if not loading and self.isVisible():
            self.grid.setFocus()

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

    def label(self, gid: int, rec: InstalledGame | None, versions: int = 1, absent: bool = False) -> str:
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
        if absent:
            badge = "Not available"
        if badge:
            badges.append(badge)
        return name + (f"\n[{' | '.join(badges)}]" if badges else "")

    def _absent(self, group: Group, lib: dict) -> bool:
        """Installed, but its folder is not there now (a drive that is not connected)."""
        recs = [lib[m["id"]] for m in group.members if lib.get(m["id"]) and lib[m["id"]].state in ("installed", "awaiting_executable")]
        return bool(recs) and not any(installdirs.present(r) for r in recs) and not any(m["id"] in self.win.app.installs for m in group.members)

    def update_presence(self) -> None:
        """Grey out the games whose folder went away, and bring back those whose drive came back."""
        lib = load_library()
        for item in {id(i): i for i in self.items.values()}.values():
            group = self.win.app.group_of.get(item.data(Qt.UserRole))
            if group is not None and self._absent(group, lib) != bool(item.data(ROLE_ABSENT)):
                self._fill_item(item, group, lib)
        self.grid.viewport().update()

    def _fill_item(self, item: QListWidgetItem, group: Group, lib: dict) -> None:
        gid = self.win.app.active_version(group)["id"]
        absent = self._absent(group, lib)
        item.setText(self.label(gid, lib.get(gid), len(group.versions), absent))
        item.setData(Qt.UserRole, gid)
        item.setData(ROLE_ABSENT, absent)
        item.setData(ROLE_COVER, greyed(self.win.covers.get(gid)) if absent else self.win.covers.get(gid))
        item.setData(ROLE_PROGRESS, self._progress(gid))
        item.setData(ROLE_INSTALLED, any(lib.get(g["id"]) and lib[g["id"]].state == "installed" for g in group.members))
        item.setData(ROLE_CORNER, corner_state(self.win.app.active_version(group)))
        item.setData(ROLE_BUSY, any(m["id"] in self._busy_gids for m in group.members))

    def update_label(self, gid: int) -> None:
        item = self.items.get(gid)
        group = self.win.app.group_of.get(gid)
        if item is not None and group is not None:
            self._fill_item(item, group, load_library())
            now = self._rank(group, load_library())
            if self._ranks.get(id(group), now) != now:
                # Started or finished installing, or removed: the game belongs elsewhere in the order.
                QTimer.singleShot(0, lambda: self.populate(self.win.search.text().lower()))
        self.update_active()

    def refresh_space(self) -> None:
        free = installdirs.total_free(self.win.app.settings.install_roots)
        self.space.setText(f"{fmt_bytes(free)} free")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh_space()
        self.space_timer.start(30_000)
        if self._cards_due:
            due, self._cards_due = self._cards_due, set()
            for gid in due & {gid for gid, _what in self._task_keys}:  # only what is still going on
                QTimer.singleShot(0, lambda gid=gid: self.pulse_card(gid))

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.space_timer.stop()

    def pulse_card(self, gid: int) -> None:
        """The ring around a game's cover in the grid, so a task that has just started for it is seen there too."""
        if gid in self._card_pulses or gid not in self.items:
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(CARD_PULSE_MS)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def frame(progress: float | None) -> None:
            if item := self.items.get(gid):  # the list may have been rebuilt meanwhile
                item.setData(ROLE_PULSE, progress)

        def done() -> None:
            frame(None)
            self._card_pulses.pop(gid, None)
            animation.deleteLater()

        animation.valueChanged.connect(frame)
        animation.finished.connect(done)
        self._card_pulses[gid] = animation
        animation.start()

    def update_active(self) -> None:
        """What is going on in the background, one row each: installs, saves being synced and mods being fetched."""
        app = self.win.app
        installing = [gid for gid in app.installs if gid in app.games]
        rows = [(gid, f"Installing... {(self._progress(gid) or 0) // 10}%", None, "install") for gid in installing]
        rows += [
            (gid, "Syncing saves...", None, "sync") for gid in sorted(self.syncing) if gid in app.games and gid not in installing
        ]
        rows += [
            (gid, "Backing up saves...", None, "backup")
            for gid in sorted(self.win.saves.uploading)
            if gid in app.games and gid not in installing and gid not in self.syncing
        ]
        rows += [
            (gid, "Putting the saves back..." if job.applying else f"Restoring saves... {job.percent}%", None, "restore")
            for gid, job in self.win.saves.restoring.items()
            if gid in app.games and gid not in installing
        ]
        rows += [
            (gid, f"Mod {name}\n{stage}... {pct}%", name, "mod")
            for (gid, name), (stage, pct) in app.mod_jobs.items()
            if gid in app.games
        ]
        keys = {(gid, mod_name or kind) for gid, _doing, mod_name, kind in rows}
        if keys != self._task_keys:
            QTimer.singleShot(0, self.refresh_space)  # an install or a removal moved the free space
        new_games = {gid for gid, _what in keys - self._task_keys}
        started = bool(keys - self._task_keys)
        self._task_keys = keys
        row = self.installs.currentRow()
        self.installs.clear()
        for gid, doing, mod_name, _kind in rows:
            game = app.games[gid]
            item = QListWidgetItem(f"{game['name']}\n{doing}")
            item.setData(Qt.UserRole, gid)
            item.setData(ROLE_MOD_ROW, mod_name)
            if gid in self.win.icons:
                item.setIcon(QIcon(self.win.icons[gid]))
            else:
                app.fetch_icon(game)
            self.installs.addItem(item)
        if rows and row >= 0:
            self.installs.setCurrentRow(min(row, len(rows) - 1))
        if not rows and self.installs_box.isAncestorOf(QApplication.focusWidget()):
            self.libs.setFocus()  # the list the focus was in is about to go
        height = self.installs.sizeHintForRow(0) if rows else 0
        self.installs.setFixedHeight(min(300, height * len(rows) + 12))  # as tall as its rows, up to a limit
        self.installs_box.setVisible(bool(rows))
        page = self.win.stack.currentWidget()
        if isinstance(page, GamePage):
            page.update_transfer()
        if started:
            if isinstance(page, GamePage):  # no sidebar here: the cover of the game it is for says it
                groups = self.win.app.group_of
                for gid in new_games:
                    if groups.get(gid) is not None and groups.get(gid) is groups.get(page.game["id"]):
                        page.pulse()
            if self.isVisible():
                for gid in new_games:
                    self.pulse_card(gid)
            else:
                self._cards_due |= new_games
        self._busy_gids = {gid for gid, _what in keys}
        self._refresh_rings()

    def _refresh_rings(self) -> None:
        """Mark the covers of the games with a task going on (an install or download, mods, saves moving) so they carry
        the turning ring, and keep it turning only while there is one."""
        busy = False
        for item in {id(i): i for i in self.items.values()}.values():
            group = self.win.app.group_of.get(item.data(Qt.UserRole))
            on = group is not None and any(m["id"] in self._busy_gids for m in group.members)
            if bool(item.data(ROLE_BUSY)) != on:
                item.setData(ROLE_BUSY, on)
            busy |= on
        if busy and self._spin.state() != QAbstractAnimation.Running:
            self._spin.start()
        elif not busy and self._spin.state() == QAbstractAnimation.Running:
            self._spin.stop()

    def _turn_rings(self, angle: float) -> None:
        delegate = self.grid.itemDelegate()
        delegate.angle = angle
        for item in {id(i): i for i in self.items.values()}.values():
            if item.data(ROLE_BUSY):
                self.grid.update(self.grid.indexFromItem(item))

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
        settings = self.win.app.settings
        played = played_at.history()

        ordered = ordering.sort_groups(
            list(groups.values()),
            order=settings.sort_order,
            installing=lambda g: self._rank(g, lib)[0],
            installed=lambda g: self._rank(g, lib)[1],
            played=lambda g: ordering.last_played(g, played),
            last_played_first=settings.sort_last_played,
            installed_first=settings.sort_installed_first,
        )
        self._ranks = {id(g): self._rank(g, lib) for g in ordered}

        for group in ordered:
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

    steam_changed = Signal()  # MOG Client was added to or taken out of Steam's library (or the request was changed)

    def __init__(self, app: App):
        super().__init__()
        self.app = app
        self.setWindowTitle("MOG")
        self.setWindowIcon(QIcon(str(ASSETS / "icon.png")))
        self.resize(1320, 800)
        self.back_btn = QPushButton("< Back")
        self.back_btn.clicked.connect(self.back)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.logo = QLabel()
        self.logo.setPixmap(asset_pixmap("title.png", 44))
        self.search = SearchBox()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self.refresh_items)
        # Who is signed in, with their picture, as on MOG-Server; the unread notifications count on its corner.
        self.user_btn = BadgeButton(app.settings.user)
        self.user_btn.setObjectName("userButton")
        self.user_btn.setIcon(avatar_icon(None, app.settings.user))
        self.user_btn.setIconSize(QSize(28, 28))
        self.user_btn.setToolTip("Settings, notifications, refresh and sign out")
        self.user_btn.setAccessibleName("Account")
        self.user_btn.clicked.connect(self.open_user_menu)
        self.user_btn.setVisible(bool(app.settings.user))
        self.user_menu = QMenu(self)
        self.user_menu.addAction("Settings", self.open_settings)
        self.notifications_action = self.user_menu.addAction("Notifications", self.open_notifications)
        self.user_menu.addAction("Refresh library", lambda: self.app.refresh())
        self.user_menu.addSeparator()
        self.user_menu.addAction("About", self.open_about)
        self.user_menu.addSeparator()
        self.user_menu.addAction("Sign out", self.sign_out)
        self.notifications: list[dict] = []  # what the list shows: this client's own notices, then the server's
        self.server_notifications: list[dict] = []
        self.local_notifications: list[dict] = []  # notices the server could not keep (an older one), lost with the session
        self._server_unread = 0
        self.seen_notification_id: int | None = None
        self._resumed = False  # what was running when the client last closed is carried on once, at the first list
        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addWidget(self.logo)
        top.addWidget(self.title)
        top.addWidget(self.search, 1)
        top.addStretch(1)
        top.addWidget(self.user_btn)
        self.stack = QStackedWidget()
        self.library = LibraryPage(self)
        self.stack.addWidget(self.library)
        self.history: list[Page] = [self.library]
        self.game_pages: dict[int, GamePage] = {}
        self.legend = QLabel()
        self.legend.setAlignment(Qt.AlignCenter)
        self.legend.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # never makes the window wider
        self.pad_family: str | None = None
        self.input_mode = "pad"  # which guide to show while a controller is connected: it follows the last device used
        QApplication.instance().installEventFilter(InputWatcher(self.set_input_mode, self))
        self._pending_link: str | None = None  # a mog:// link that arrived before the games were loaded
        self._link_refreshed_for: str | None = None  # the link the games were reloaded for, once
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.legend)
        self.setCentralWidget(central)
        self.playing = PlayingOverlay(central, suspended=lambda: self.overlay.isVisible())
        self.playing.changed.connect(self.refresh_legend)
        self.playing.stop_clicked.connect(self.stop_playing)
        self.busy = BusyOverlay(central, suspended=lambda: self.overlay.isVisible())
        self.busy.changed.connect(self.refresh_legend)
        self.modal = ModalHost(central)  # a modal page's card, under any message
        self.modal.dismissed.connect(self.back)
        self.overlay = MessageOverlay(central)  # created last: a message is shown over everything
        self.overlay.changed.connect(self.refresh_legend)
        self.covers: dict[int, QPixmap] = {}
        self.icons: dict[int, QPixmap] = {}
        self.icon_art: dict[int, QPixmap] = {}
        b = app.bridge
        b.call.connect(lambda fn: fn())
        self._steam_settled = False
        self._playing = None
        self.saves = SaveSync(self)
        self.sounds = SoundPlayer(lambda: app.settings.sounds)
        self.nav_sounds = NavigationSounds(self.sounds)
        QApplication.instance().installEventFilter(self.nav_sounds)
        QApplication.instance().aboutToQuit.connect(self.sounds.close)
        b.games.connect(self.set_games)
        b.error.connect(self._on_error)
        b.message.connect(lambda level, text: self.message(text, level))  # the signal says level first
        b.note.connect(self.notify)
        b.cover.connect(self.set_cover)
        b.icon.connect(self.set_icon)
        b.user.connect(self.set_user)
        b.activity.connect(self.library.update_active)
        b.finished.connect(lambda *_: self.refresh_items())
        b.progress.connect(lambda gid, *_: self.library.update_label(gid))
        b.pad.connect(self.on_pad_event)
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
        self.library.show_loading(app.settings.configured)
        self._show_snapshot()
        self.library_timer = QTimer(self)
        self.library_timer.timeout.connect(app.poll_library)
        self.library_timer.start(20000)
        self._show(self.library)

    def _show_snapshot(self) -> None:
        """What the server last sent, so the window shows the library and the user at once, before the real list
        arrives (and is the same when nothing changed)."""
        settings = self.app.settings
        if not settings.configured:
            return
        games, libraries = snapshot.load_library(settings.base)
        if games:
            self.app.set_games(games)
            self.library.set_libraries(libraries)
            self.library.show_loading(False)
            self.refresh_items()
            self.app.load_cached_covers(games)
        user = snapshot.load_user(settings.base)
        if user:
            self.set_user(*user)
        kept = snapshot.load_notifications(settings.base)
        if kept:
            self._show_notifications(kept)  # shown, not announced: the first real answer settles what is new

    def closeEvent(self, e):
        self.pad_stop.set()
        self.sounds.close()
        super().closeEvent(e)

    def current_page(self) -> Page:
        return self.history[-1]

    def _show(self, page: Page) -> None:
        # A modal page is drawn over the nearest page that is not one, which is the one the window's header is for.
        base = next(p for p in reversed(self.history) if not p.modal) if page.modal else page
        self.stack.setCurrentWidget(base)
        if page.modal:
            self.modal.show_page(page, page.title)
        else:
            self.modal.hide_page()
        page, shown = base, page
        on_library = page is self.library
        self.back_btn.setVisible(not on_library)
        self.search.setVisible(page.searchable)
        self.user_btn.setVisible(bool(self.app.settings.user) and (on_library or isinstance(page, GamePage)))
        self.logo.setVisible(on_library)
        self.title.setVisible(not on_library)
        self.title.setText(page.title)
        self.refresh_legend()
        shown.focus_default()

    def push(self, page: Page) -> None:
        if not page.modal and self.stack.indexOf(page) < 0:
            self.stack.addWidget(page)
        self.history.append(page)
        self._show(page)

    def back(self) -> None:
        if len(self.history) <= 1:
            return
        page = self.history.pop()
        if page.modal:
            self.modal.release(page)
            page.deleteLater()
        elif not isinstance(page, GamePage):
            self.stack.removeWidget(page)
            page.deleteLater()
        self._show(self.history[-1])
        self.refresh_items()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self.overlay.showing or self.playing.showing or self.busy.showing:
            return
        if e.key() == Qt.Key_Escape:
            self.back()
        else:
            super().keyPressEvent(e)

    def notify(self, text: str, level: str = "info") -> None:
        """Something worth keeping but not worth interrupting for: it goes to the log."""
        getattr(logstore, "warning" if level == "warning" else "error" if level == "error" else "info")(text)

    def message(self, text: str, level: str = "error") -> None:
        """A message laid over the window until OK is pressed; also kept in the log."""
        self.notify(text, level)
        self.overlay.show_message(text, level)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.modal.fit_to_parent()
        self.overlay.fit_to_parent()
        self.playing.fit_to_parent()
        self.busy.fit_to_parent()
        self.refresh_legend()  # its size follows the window

    def refresh_tasks(self) -> None:
        """The sidebar's list of what is going on, after a task started, moved on or ended."""
        self.library.update_active()

    def show_busy(self, title: str, name: str, game_id: int | None = None, on_cancel=None) -> None:
        """Cover the window with what is under way (a restore, a backup). Cancel is offered when `on_cancel` is."""
        if getattr(self, "_busy_cancel", None) is not None:
            self.busy.cancel_clicked.disconnect(self._busy_cancel)
            self._busy_cancel = None
        if on_cancel is not None:
            self._busy_cancel = lambda: (self.busy.cancelling(), on_cancel())
            self.busy.cancel_clicked.connect(self._busy_cancel)
        self.busy.show_busy(title, name, self.covers.get(game_id) if game_id is not None else None, on_cancel is not None)

    def busy_progress(self, done: int, total: int, detail: str | None = None) -> None:
        self.busy.set_progress(done, total, detail)

    def hide_busy(self) -> None:
        self.busy.end()

    def choose(
        self, title: str, text: str, options: list, on_choose, skip: str | None = None, confirm: bool = False
    ) -> None:
        self.push(ChoicePage(self, title, text, options, on_choose, skip, confirm))

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

    def install_game(self, game: dict, installer: dict | None = None, then=None, extract: bool = False) -> None:
        """Start installing `game`. One that was begun carries on where it is (its drive has to be back); a new
        one goes into the first install folder that is connected and has room, and when an earlier folder is
        full the user is asked, folder by folder, before it goes into a later one. `then` runs once started."""
        gid = game["id"]
        if gid in self.app.installs:
            return
        rec = load_library().get(gid)
        if rec and not installdirs.resumable(rec):
            self.message(f"{rec.name} was being installed in {rec.install_dir}, which is not available now. Connect it and try again.")
            return

        def go(root: Path | None = None, extract: bool | None = None) -> None:
            self.app.start_install(game, installer, root, extract)
            if then:
                then()

        if rec and rec.extract_only:
            go()  # decided when it began
            return

        def place(extract: bool) -> None:
            self._place(game, self.app.sizes.get(gid) or 0, lambda root=None: go(root, extract))

        def size_then_place(extract: bool) -> None:
            if gid in self.app.sizes:
                place(extract)
                return

            def ask_size() -> None:
                size = self.app.client().game_size(gid)
                if size is not None:
                    self.app.sizes[gid] = size
                self.app.bridge.call.emit(lambda: place(extract))

            self.app.run_bg(ask_size, on_error=lambda _m: self.app.bridge.call.emit(lambda: place(extract)))

        if extract:  # asked for in the picker: nothing left to ask
            go(None, True) if rec else size_then_place(True)
            return
        # A game that was begun is asked too when its session is not running (an attempt that failed
        # leaves its record behind, and starting it again would only fail the same way).
        self._ask_extraction(game, installer, (lambda extract: go(None, extract)) if rec else size_then_place)

    def _ask_extraction(self, game: dict, installer: dict | None, proceed) -> None:
        """A game that is an archive with no installer in it (one that needs none) can be extracted as it is and
        taken as the install, nothing run. The server says whether an archive looks like that; when it does the user
        is asked, and `proceed(extract)` goes on with their answer. The lookup never gets in the way: when it fails
        the install goes on as usual."""

        def work() -> None:
            client = self.app.client()
            try:
                session = client.get_session(game["id"]) or {}
            except Exception:  # noqa: BLE001 - no session, or the server cannot say: look at the archive
                session = {}
            # A session that is running, or finished, already settled how this game is installed.
            live = session.get("state") in ("detecting", "installing", "streaming", "done")
            name = None if live else client.portable_archive(game["id"], installer)
            self.app.bridge.call.emit(lambda: self._extraction_answer(name, proceed))

        self.app.run_bg(work, on_error=lambda _m: self.app.bridge.call.emit(lambda: proceed(None)))

    def _extraction_answer(self, name: str | None, proceed) -> None:
        if name is None:
            proceed(None)  # nothing was asked: the server decides, and extracts an archive with no installer in it
            return
        self.ask(
            f"No installer was found in {name}: it looks like a game that needs none. Extract its contents and use "
            "them as they are? Yes unpacks them as the install, with nothing run. No still tries to install it "
            "by running what it finds inside.",
            lambda: proceed(True),
            lambda: proceed(False),
        )

    def _place(self, game: dict, needed: int, go) -> None:
        plan = installdirs.choose(self.app.settings.install_roots, needed)
        for slot in plan.slots:
            logstore.info(f"Install folder {slot.path}: {slot.state}, {fmt_bytes(slot.free)} free, {game['name']} needs about {fmt_bytes(needed)}")
        ready = plan.ready
        if not ready:
            full = [slot for slot in plan.slots if slot.state == "full"]
            if not full:
                self.message(
                    f"No install folder is available for {game['name']}. Connect a drive or add a folder in Settings."
                )
                return
            # The figure is the size of the game's whole folder on the server (every installer, version and extra),
            # which can be well above what this install writes, so a folder that looks too small is offered anyway.
            best = max(full, key=lambda slot: slot.free)
            self.ask(
                f"{game['name']} takes about {fmt_bytes(needed)} on the server (all of its folder), and {best.path} has "
                f"{fmt_bytes(best.free)} free. The install itself may need less. Install it there anyway?",
                lambda: go(best.path),
            )
            return
        full = plan.blocked_by
        if full is None:
            go(ready[0].path)
            return

        def offer(i: int) -> None:
            slot = ready[i]
            lead = (
                f"{full.path} has {fmt_bytes(full.free)} free and {game['name']} needs {fmt_bytes(needed)}. "
                if i == 0
                else ""
            )
            self.ask(
                f"{lead}Install it in {slot.path} instead? ({fmt_bytes(slot.free)} free)",
                lambda: go(slot.path),
                (lambda: offer(i + 1)) if i + 1 < len(ready) else None,
            )

        offer(0)

    def on_update_checked(self, info, error: str, manual: bool) -> None:
        if error and not manual:
            self.notify(f"Update check failed: {error}", "warning")
        if info is None:
            return
        self.ask(
            f"MOG {info.version} is available (you have {__version__}). Update now? The app restarts when done.",
            lambda: self.push(UpdatePage(self, info)),
        )

    def _shortcut_menu(self) -> None:
        page = self.current_page()
        if page is self.library or isinstance(page, GamePage):
            self.open_menu()

    def _add_shortcuts(self) -> None:
        def on_library(action):
            return lambda: action() if self.current_page() is self.library else None

        for keys, action in (
            ("F5", self.app.refresh),
            ("Ctrl+F", self.search.setFocus),
            ("Ctrl+B", self.library.toggle_sidebar),
            ("Ctrl+Backspace", self.search.clear),
            ("Ctrl+N", self.open_notifications),
        ):
            QShortcut(QKeySequence(keys), self, on_library(action))
        QShortcut(QKeySequence("Ctrl+,"), self, self._shortcut_menu)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.quit_app)
        QShortcut(QKeySequence("Ctrl+U"), self, self._shortcut_menu)

    def set_pad(self, connected: bool, family: str) -> None:
        self.pad_family = family if connected else None
        self.refresh_legend()

    def set_input_mode(self, mode: str) -> None:
        """The last thing pressed was a controller button ("pad") or a keyboard key ("keys"): the guide follows."""
        if mode != self.input_mode:
            self.input_mode = mode
            self.refresh_legend()

    def legend_family(self) -> str | None:
        """The controller's family while one is connected and was the last thing used, None for the keyboard's guide."""
        return self.pad_family if self.input_mode == "pad" else None

    def refresh_legend(self) -> None:
        """The controller's guide while one is connected and in use, the keyboard's otherwise."""
        context = self.legend_context()
        family = self.legend_family()
        height, entries = legend.fit(context, family, legend.icon_height_for(self.height()), int(self.width() * 0.96))
        self.legend.setStyleSheet(f"color: #9aa3b0; font-size: {legend.font_px_for(height)}px;")
        self.legend.setText(legend.render(context, family, height, entries))
        self.legend.setVisible(True)

    def legend_context(self) -> str:
        page = self.current_page()
        if self.overlay.showing:
            return legend.MESSAGE
        if self.playing.showing:
            return legend.PLAYING
        if self.busy.showing:
            return legend.BUSY
        if isinstance(page, KeyboardPage):
            return legend.TYPING
        if isinstance(page, NotificationsPage):
            return legend.INBOX
        if isinstance(page, SettingsPage):
            return legend.LOGS if page.tabs.currentWidget() is page.logview else legend.SETTINGS
        return legend.LIBRARY if page is self.library else legend.PAGE

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

    def on_pad_event(self, name: str) -> None:
        """What the pad reader reports. It reads the device whatever window has the focus, so while a
        game (or anything else) is in front these presses are not ours: ignoring them keeps the menus
        from reacting, and from making sounds, to someone playing."""
        if QApplication.activeWindow() is None:
            return
        self.set_input_mode("pad")
        self.on_pad(name)

    def on_pad(self, name: str) -> None:
        if self.overlay.showing and name != gamepad.QUIT:
            # A message is up: A and B answer it, nothing else reaches the page underneath.
            if name in (gamepad.ACCEPT, gamepad.BACK):
                self.overlay.dismiss()
            return
        if self.busy.showing and name != gamepad.QUIT:
            # Something is under way: A and B cancel it, nothing else reaches the page underneath.
            if name in (gamepad.ACCEPT, gamepad.BACK) and self.busy.cancel_button.isEnabled():
                self.busy.cancel_button.click()
            return
        if self.playing.showing and name != gamepad.QUIT:
            # A game is running: A stops it, nothing else reaches the page underneath.
            if name == gamepad.ACCEPT:
                self.playing.stop_button.click()
            return
        if name in (gamepad.UP, gamepad.DOWN, gamepad.LEFT, gamepad.RIGHT, gamepad.PAGE_PREV, gamepad.PAGE_NEXT):
            self.sounds.play(NAVIGATE)
        amount = gamepad.scroll_amount(name)
        if amount is not None:
            self._scroll(amount)
            return
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
        if isinstance(page, SettingsPage) and name in (gamepad.TRIGGER_L, gamepad.TRIGGER_R):
            page.switch_tab(-1 if name == gamepad.TRIGGER_L else 1)
            self.sounds.play(NAVIGATE)
            return
        if name == gamepad.TRIGGER_L:
            if page is self.library:
                self.library.toggle_sidebar()
            return
        if name == gamepad.TRIGGER_R:
            if page is self.library:
                self.search.clear()
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
        if name in (gamepad.MENU, gamepad.ACCOUNT):  # Start, and Select as well: the user menu
            if page is self.library or isinstance(page, GamePage):
                self.open_menu()
            return
        popup = QApplication.activePopupWidget()
        focus = QApplication.focusWidget()
        if popup is None and isinstance(page, SettingsPage) and name in (gamepad.UP, gamepad.DOWN):
            if page.navigate(name == gamepad.DOWN):
                return
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

    def _scroll(self, amount: float) -> None:
        """The right stick: scroll the log in Settings, else the list or text that has the focus."""
        page = self.current_page()
        if isinstance(page, SettingsPage) and page.scroll_logs(amount):
            return
        area = QApplication.focusWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()
        if area is not None:
            bar = area.verticalScrollBar()
            bar.setValue(bar.value() + int(amount * 60))

    def _post_key(self, name: str) -> None:
        key = _KEYS.get(name)
        if key is None:
            return
        popup = QApplication.activePopupWidget()
        # A combo's popup is a frame around the list that has the focus and takes the keys.
        target = (popup.focusWidget() or popup) if popup is not None else (QApplication.focusWidget() or self.current_page())
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.postEvent(target, QKeyEvent(kind, key, Qt.NoModifier))

    def open_menu(self) -> None:
        """The user menu, whose first row (Settings) is ready for Enter; without a signed-in user there is no menu to
        show, and the key goes straight to Settings so the server can be set up."""
        if self.user_btn.isVisibleTo(self):
            self.open_user_menu()
        elif self.current_page() is self.library:
            self.open_settings()

    def open_user_menu(self) -> None:
        """The menu under the user's picture, aligned to the window's right edge; the same again closes it."""
        if not self.user_btn.isVisible():
            return
        if self.user_menu.isVisible():
            self.user_menu.hide()
            return
        self.user_menu.adjustSize()
        corner = self.user_btn.mapToGlobal(QPoint(self.user_btn.width() - self.user_menu.sizeHint().width(), self.user_btn.height()))
        self.user_menu.popup(corner)
        first = self.user_menu.actions()[0]
        self.user_menu.setActiveAction(first)  # Enter (A) takes the first row at once, the arrows move on

    def sign_out(self) -> None:
        settings = self.app.settings
        self.ask(
            f"Sign out of {settings.base}? The saved password is forgotten and the library is emptied; the games "
            "installed here stay where they are.",
            self._signed_out,
        )

    def _signed_out(self) -> None:
        settings = self.app.settings
        settings.password = ""
        save_settings(settings)
        snapshot.forget()
        self.app.set_games([])
        self.library.set_libraries([])
        self.refresh_items()
        self.user_btn.setVisible(False)
        self.user_btn.set_count(0)
        self.notify("Signed out")
        self.open_settings()

    def open_settings(self) -> None:
        self.push(SettingsPage(self))

    def open_first_run(self, rerun: bool = False) -> None:
        self.push(FirstRunPage(self, rerun))

    # --- Steam integration ---

    def open_steam_integration(self, then=None) -> None:
        """Ask which Steam accounts MOG and its installed games go into, and bring Steam in line with the answer. `then`
        runs once answered (not when the page is left with Back)."""
        users = selfsteam.users()
        if not users:
            self.message("Steam was not found on this computer.", "warning")
            return
        settings = self.app.settings

        def answered(accounts: list[str] | None) -> None:
            settings.steam_accounts = accounts
            settings.steam_decided = True
            settings.steam_asked_for = __version__
            save_settings(settings)
            self._apply_steam()
            if then:
                then()

        self.push(SteamAccountsPage(self, users, settings.steam_accounts, answered))

    def _apply_steam(self) -> None:
        """Write MOG's own entries and every installed game's shortcuts for the accounts chosen, in the background."""
        settings = self.app.settings

        def work() -> None:
            outcome = selfsteam.apply(settings)
            if settings.configured:
                manager.apply_steam_accounts(selfsteam.accounts(settings), self.app.client(), settings.launcher)
            self.app.bridge.call.emit(lambda: self._steam_applied(outcome))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Steam integration: {m}"))

    def _steam_applied(self, outcome: str) -> None:
        if outcome == "no-steam":
            self.message("Steam was not found on this computer.", "warning")
        elif outcome in ("added", "added-open"):
            self.message("MOG and its games are in your Steam library. Restart Steam to see them.", "info")
        elif selfsteam.accounts(self.app.settings) == []:
            self.message("MOG and its games are out of Steam. If Steam is open, restart it to see that.", "info")
        self.steam_changed.emit()

    def offer_steam_client(self) -> None:
        """After an update (and at the first start with Steam), once per version until answered: ask which accounts."""
        settings = self.app.settings
        if not selfsteam.should_ask(settings, __version__):
            return
        settings.steam_asked_for = __version__
        save_settings(settings)
        self.open_steam_integration()

    def open_about(self) -> None:
        self.push(AboutPage())

    def begin_play(self, rec: InstalledGame, proc, **timing) -> None:
        """A game was started from here: show that it is running, follow it to its end (a launcher that
        failed before the game showed up is reported), then back its saves up."""
        stop = threading.Event()
        self._playing = (rec, proc, stop)
        self.playing.show_game(rec.name, self.covers.get(rec.game_id))
        started_ns = time.time_ns()
        played_at.record(rec.game_id)

        def work() -> None:
            ctx = None
            if sync_enabled(rec, self.app.settings):
                ctx = runner.make_context(rec, self.app.settings, self.app.client(), log=logstore.warning)
            recorder = runner.runtime_prefix_recorder(ctx) if ctx and not rec.native else None
            started = gameplay.watch(Path(rec.install_dir), proc, stop.is_set, on_prefix=recorder, native=rec.native, **timing)
            if started and ctx:
                self.app.bridge.call.emit(lambda: self._sync_started(rec))
            result = sync.backup(ctx, sync.QUIT, since_ns=started_ns) if (started and ctx) else None
            self.app.bridge.call.emit(lambda: self._play_ended(rec, proc, started, stop.is_set(), result))

        def failed(message: str) -> None:
            self.app.bridge.call.emit(lambda: (self.playing.end(), self.message(message, "error")))

        self.app.run_bg(work, on_error=failed)

    def stop_playing(self) -> None:
        playing = getattr(self, "_playing", None)
        if playing is None:
            return
        rec, proc, stop = playing
        stop.set()
        self.playing.stopping()
        logstore.info(f"Stopping {rec.name}")
        threading.Thread(
            target=lambda: gameplay.stop_game(Path(rec.install_dir), proc, native=rec.native), daemon=True, name="stop-game"
        ).start()

    def _sync_started(self, rec: InstalledGame) -> None:
        """The game has closed and its saves are being backed up: the window is free again, and the sidebar shows
        the sync the way it shows an install."""
        self._playing = None
        self.playing.end()
        self.library.syncing.add(rec.game_id)
        self.library.update_active()

    def _play_ended(self, rec: InstalledGame, proc, started: bool, stopped: bool, result) -> None:
        self._playing = None
        self.playing.end()
        self.library.syncing.discard(rec.game_id)
        self.library.update_active()
        self.app.bridge.played.emit(rec.game_id)
        if self.app.settings.sort_last_played:
            self.library.populate(self.search.text().lower())
        if not started and not stopped:
            self.message(launch_failure(proc) or f"{rec.name} did not start", "error")
        if result is not None:
            self.saves.after_backup(rec, result, lambda: None, quiet=True)

    def settle_steam(self) -> None:
        """Once per session, with Steam closed: check the shortcuts written while it ran are still there."""
        if self._steam_settled or not self.app.settings.configured:
            return
        self._steam_settled = True

        def work() -> None:
            done = manager.settle_steam_shortcuts(self.app.client(), self.app.settings.launcher)
            if done:
                self.app.bridge.message.emit("info", "Steam shortcuts brought up to date: " + ", ".join(done))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.note.emit(f"Steam shortcuts: {m}"))

    def _on_error(self, text: str) -> None:
        self.message(text, "error")
        if self.library.shelf.currentWidget() is self.library.loading:
            self.library.show_loading(False)  # nothing is coming: do not leave Mog carrying boxes for good

    def set_games(self, games: list) -> None:
        self.library.show_loading(False)
        self.app.set_games(games)
        self.app.poll_notifications()
        self.notify(f"{len(games)} games")
        self.refresh_items()
        self.saves.check_all()
        self.settle_steam()
        self._resume_background()
        if self._pending_link:
            link, self._pending_link = self._pending_link, None
            self.handle_link(link)

    def _resume_background(self) -> None:
        """Carry on with what was running when the client closed, once per start: an install that stopped half way
        (its folder has to be reachable) and the mods that were being fetched. What cannot go on is forgotten."""
        if self._resumed:
            return
        self._resumed = True
        local = load_library()
        state = activity.load()
        for gid in state["installs"]:
            game, rec = self.app.games.get(gid), local.get(gid)
            if game and rec and rec.state not in ("installed", "awaiting_executable") and installdirs.resumable(rec):
                self.install_game(game)
            else:
                activity.remove_install(gid)
        for entry in state["mods"]:
            game = self.app.games.get(entry["game_id"])
            if game:
                self.app.download_mod(game, entry["mod"])
            else:
                activity.remove_mod(entry["game_id"], entry["mod"].get("name"))

    def set_user(self, name: str, avatar: bytes | None) -> None:
        self.user_btn.setText(name)
        self.user_btn.setIcon(avatar_icon(avatar, name))
        page = self.current_page()
        self.user_btn.setVisible(bool(name) and (page is self.library or isinstance(page, GamePage)))

    def set_icon(self, gid: int, blob: bytes) -> None:
        pix = largest_pixmap(blob)
        if not pix.isNull():
            self.icon_art[gid] = pix  # as it came, for where it is shown larger than in the sidebar
            self.icons[gid] = pix.scaled(ACTIVE_ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.library.update_active()

    def set_cover(self, gid: int, blob: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(blob):
            self.covers[gid] = pix.scaled(COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item = self.library.items.get(gid)
            if item is not None:
                item.setData(ROLE_COVER, greyed(self.covers[gid]) if item.data(ROLE_ABSENT) else self.covers[gid])
                self.library.grid.viewport().update()

    def handle_link(self, text: str) -> None:
        """What a later start, or the system, handed this one: a `mog://install/<id>` link from the web UI (or
        nothing, which only brings the window forward). A link for a game waits for the games to be loaded."""
        self.show()
        self.raise_()
        self.activateWindow()
        if not text:
            return
        link = protocol.parse(text)
        if link is None:
            self.message(f"This link is not one MOG understands: {text[:200]}", "warning")
            return
        settings = self.app.settings
        if not settings.configured:
            self.message("Set up the server in Settings first, then open the link again.", "warning")
            self.open_settings()
            return
        if not self.app.games:
            self._pending_link = text
            return
        game = self.app.games.get(link.game_id)
        if game is None and self._link_refreshed_for != text:
            # The library may have changed since it was loaded (a game added on the server, which the web page
            # already shows): look again before saying it is not there.
            self._link_refreshed_for = text
            self._pending_link = text
            logstore.info(f"Game {link.game_id} is not in the list yet, refreshing it")
            self.app.refresh()
            return
        self._link_refreshed_for = None
        if game is None:
            self.message(f"Game {link.game_id} is not on {settings.base}.", "error")
            return
        if link.server and not protocol.same_server(link.server, settings.base):
            # The same server under another name is common (a host name here, an address there), so ask.
            self.ask(
                f"The link came from {link.server}, but MOG is connected to {settings.base}. "
                f"Install {game['name']} from {settings.base}?",
                lambda: self._install_from_link(game),
            )
            return
        self._install_from_link(game)

    def _install_from_link(self, game: dict) -> None:
        self.show_game(game["id"])
        rec = load_library().get(game["id"])
        if game["id"] in self.app.installs:
            return  # already on its way
        if rec and rec.state == "installed":
            self.message(f"{game['name']} is already installed.", "info")
            return
        page = self.current_page()
        if isinstance(page, GamePage):
            page.install()

    def refresh_items(self) -> None:
        self.library.populate(self.search.text().lower())

    def changeEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.library.update_presence()  # a drive may have been connected or removed meanwhile

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
            added = False
            for n in items:
                if n["id"] > self.seen_notification_id and not n["read"]:
                    self.notify(f"Notification: {n['title']}")
                    added |= n.get("kind") == "games_added"
            if added:
                self.app.poll_library()  # the server says games came in: no waiting for the next look at the library
        self.seen_notification_id = newest
        self._show_notifications(data)
        if self.app.settings.configured:
            try:
                snapshot.save_notifications(self.app.settings.base, data)
            except OSError as e:
                logstore.warning(f"Could not keep the notifications for the next start: {e}")

    def _show_notifications(self, data: dict) -> None:
        self.server_notifications, self._server_unread = data["notifications"], data["unread"]
        self._merge_notifications()

    def _merge_notifications(self) -> None:
        self.notifications = self.local_notifications + self.server_notifications
        unread = self._server_unread + sum(not n["read"] for n in self.local_notifications)
        self.user_btn.set_count(unread)
        self.notifications_action.setText(f"Notifications ({unread})" if unread else "Notifications")
        page = self.current_page()
        if isinstance(page, NotificationsPage):
            page.populate()

    def add_local_notification(self, kind: str, title: str, body: str | None, game_id: int | None = None) -> None:
        """A notice this client keeps itself, in the list beside the server's, when the server cannot keep it."""
        self.local_notifications.insert(
            0, {"id": -(len(self.local_notifications) + 1), "kind": kind, "title": title, "body": body, "game_id": game_id, "read": False}
        )
        self.notify(f"Notification: {title}")
        self._merge_notifications()

    def _server_unread_now(self) -> int:
        return sum(not n["read"] for n in self.server_notifications)

    def mark_read(self, notification_id: int | None) -> None:
        for n in self.notifications:
            if notification_id is None or n["id"] == notification_id:
                n["read"] = True
        if notification_id is None or notification_id > 0:  # a notice of this client's own is not on the server
            self.app.run_bg(lambda: self.app.client().mark_notifications_read(notification_id))
        self.on_notifications({"notifications": self.server_notifications, "unread": self._server_unread_now()})
        QTimer.singleShot(1000, self.app.poll_notifications)

    def delete_notification(self, notification_id: int | None) -> None:
        keep = lambda n: notification_id is not None and n["id"] != notification_id  # noqa: E731
        self.local_notifications = [n for n in self.local_notifications if keep(n)]
        self.server_notifications = [n for n in self.server_notifications if keep(n)]
        if notification_id is None or notification_id > 0:
            self.app.run_bg(lambda: self.app.client().delete_notifications(notification_id))
        self.on_notifications({"notifications": self.server_notifications, "unread": self._server_unread_now()})
        QTimer.singleShot(1000, self.app.poll_notifications)


def run_gui(link: str | None = None) -> int:
    """Open the client. `link` is a `mog://` URL it was started with; when a client is already running the link
    is handed to that one, which comes forward, and this start exits."""
    if send_to_running(link or ""):
        return 0
    osk.prefer_xcb()
    qapp = QApplication(sys.argv[:1])
    logstore.write_to_file(data_dir() / "logs" / "client.log")  # what is logged survives the window closing
    qapp.setDesktopFileName("mog-client")  # how the desktop finds the entry (and the icon) of this window
    qapp.setStyleSheet(STYLE)
    if crashlog.install():
        heartbeat = QTimer()
        heartbeat.timeout.connect(crashlog.beat)
        heartbeat.start(1000)
    enter_filter = ActivateOnEnter()
    qapp.installEventFilter(enter_filter)
    updater.cleanup_old()
    app = App()
    ensure_scanned(app.settings)
    if (settled := selfsteam.settle(app.settings)) is not None:  # a request that waited for Steam to close, or a moved client
        logstore.info(f"MOG Client's Steam shortcut was {settled}")
    remember_client()  # where the launch scripts find this client, whatever it is called or wherever it was moved to
    if app.settings.scripts_written_for != __version__:
        app.settings.scripts_written_for = __version__
        save_settings(app.settings)
        app.run_bg(lambda: manager.refresh_launch_files(app.settings), on_error=logstore.warning)
    win = MainWindow(app)
    listener = Listener()
    if listener.listen():
        listener.received.connect(win.handle_link)
    win.osk.install(qapp)
    try:
        protocol.register(client_command())  # so the web UI's Install button finds this client
    except (OSError, ImportError) as e:
        logstore.warning(f"Could not register the mog:// handler: {e}")
    if is_deck():
        win.showFullScreen()
    else:
        win.show()
    if updater.enabled() and app.settings.check_updates:
        app.check_update()
    if link:
        win._pending_link = link
    if app.settings.configured and not app.settings.first_run_done:
        app.settings.first_run_done = True  # set up before there was a guide: it has nothing to add
        save_settings(app.settings)
    if app.settings.configured:
        app.refresh()
    elif not app.settings.first_run_done:
        win.open_first_run()
    else:
        win.open_settings()
        if link:
            win.handle_link(link)
    if app.settings.first_run_done:
        QTimer.singleShot(0, win.offer_steam_client)
    return qapp.exec()
