"""Settings and the local record of installed games (JSON files, stdlib only)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mog_client import launchargs


def _xdg(env: str, fallback: str, win_env: str = "APPDATA") -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get(win_env, Path.home())) / "mog-client"
    return Path(os.environ.get(env) or Path.home() / fallback) / "mog-client"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share", "LOCALAPPDATA")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        if private:
            tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)  # a half-written copy would only take the room the disk has left
        raise


def default_install_root() -> Path:
    return data_dir() / "games"


@dataclass
class Settings:
    base: str = ""
    user: str = ""
    password: str = ""
    # Where games are installed, in order of priority: the first that is connected and has room wins.
    # Empty means the default folder inside the client's data directory.
    install_dirs: list[str] = field(default_factory=list)
    # "auto", "faugus", "umu", "wine" (Linux) or "native" (Windows)
    launcher: str = "auto"
    # Look for a newer release at startup (only in builds that can self-update).
    check_updates: bool = True
    # The library page's left-hand sidebar (toggled with L2).
    show_sidebar: bool = True
    # Sidebar sections the user folded ("installs", "libraries", "sorts").
    collapsed_sections: list[str] = field(default_factory=list)
    # How the library is ordered (see ordering.py): last played first, installed first, then this order.
    sort_last_played: bool = False
    sort_installed_first: bool = True
    sort_order: str = "newest"
    # Games the server holds an install cache for first (after the installed ones), and the two filters.
    # The library chosen in the sidebar (None is every game), kept for the next visit and the next start.
    library_filter: int | None = None
    sort_cached_first: bool = False
    filter_saves: bool = False
    filter_mods: bool = False
    # The launchers found on this machine, in order of preference, and the MOG version that looked.
    launchers: list[str] = field(default_factory=list)
    launchers_scanned_for: str = ""
    # Short sounds when moving through the menus and when a game starts.
    sounds: bool = True
    # Look over the saves of every game when the client starts (only with sync_saves on).
    sync_on_start: bool = True
    # Back up each game's save files to the server (a game can opt out: InstalledGame.save_sync).
    sync_saves: bool = True
    # Send this device's log to the server when saves are backed up (see logsend.py); off unless the user asks.
    upload_logs: bool = False
    # Show a small "Syncing saves" window when a game that was started outside MOG ends.
    sync_window: bool = True
    # The MOG version that last wrote the games' launch scripts (they hold the client's whereabouts).
    scripts_written_for: str = ""
    # MOG Client as a shortcut of its own in Steam (see selfsteam.py): the record of each entry (one per Steam account), the
    # version the user was last asked about it for, and a request left by an earlier version.
    steam_clients: list[dict] = field(default_factory=list)
    steam_client_pending: str = ""  # only what an earlier version left: a request to add it once Steam was closed
    steam_client_verify: bool = False  # added while Steam ran: check at the next start with it closed that it is still there
    steam_asked_for: str = ""
    # The Steam accounts (their userdata folder names) MOG and its games go into. None is every account found; an empty list
    # turns the integration off. `steam_decided` is whether the user has been asked, here or at the first install.
    steam_accounts: list[str] | None = None
    steam_decided: bool = False
    # Offline by choice, as in Steam: MOG Server is not asked for anything, and nothing looks for a way back online.
    offline_forced: bool = False
    # The version whose changelog was shown (see changelog.py): after an update it is opened once.
    changelog_seen: str = ""
    # The first-start guide has been through (or the client was set up before there was one).
    first_run_done: bool = False

    @property
    def install_roots(self) -> list[Path]:
        return [Path(d) for d in self.install_dirs] or [default_install_root()]

    @property
    def configured(self) -> bool:
        return bool(self.base and self.user and self.password)


def settings_path() -> Path:
    return config_dir() / "config.json"


def load_settings() -> Settings:
    raw = _read_json(settings_path(), {})
    known = Settings.__dataclass_fields__
    if raw.get("games_dir") and not raw.get("install_dirs"):  # the single folder of older versions
        raw["install_dirs"] = [raw["games_dir"]]
    if raw.get("steam_client") and not raw.get("steam_clients"):  # the single entry of older versions
        raw["steam_clients"] = [raw["steam_client"]]
        if raw.get("steam_accounts") is None:
            raw["steam_accounts"] = [Path(raw["steam_client"]["shortcuts_path"]).parent.parent.name]
            raw["steam_decided"] = True
    if raw.get("steam_never_ask") and raw.get("steam_accounts") is None:
        raw["steam_accounts"], raw["steam_decided"] = [], True
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(s: Settings) -> None:
    _write_json(settings_path(), asdict(s), private=True)


def is_native_executable(path: str | Path | None) -> bool:
    """Whether an executable is a Linux program (a script, as GOG's Linux games start with one) that runs as it is,
    not a Windows .exe that needs Proton or Wine."""
    if not path:
        return False
    if str(path).lower().endswith(".sh"):
        return True
    return sys.platform != "win32" and _is_elf(str(path))


def _is_elf(path: str) -> bool:
    """A program of the game's own that Linux runs (itch.io's `Game.x86_64`), not a .exe: told by its first bytes."""
    if path.lower().endswith((".exe", ".lnk", ".bat", ".cmd")):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


@dataclass
class InstalledGame:
    game_id: int
    name: str
    install_dir: str
    # "installing" | "awaiting_executable" | "installed"
    state: str = "installing"
    session_id: int | None = None
    executable: str | None = None
    prefix: str | None = None
    desktop_entry: str | None = None
    # The server's folder name for the game when this record was made: what tells it is still the same game.
    server_name: str | None = None
    # The copy of the entry on the user's desktop folder, when asked for.
    desktop_shortcut: str | None = None
    # {"shortcuts_path": str, "appid": int, "artwork": [str]} for each Steam entry created
    steam_entries: list[dict] = field(default_factory=list)
    # The folder the game's own files are in when they were put in a folder of their own inside `install_dir` (they came
    # without one); None when they are in `install_dir` itself.
    files_dir: str | None = None
    # This game's launch engine; "auto" follows the launcher chosen in Settings.
    launcher: str = "auto"
    # Arguments given to the game's executable, as tags (see launchargs.py); every launch and shortcut passes them on.
    launch_args: list[str] = field(default_factory=list)
    # The tags as they last were the same here and on the server (None: never). What differs from them was changed here and
    # not sent yet, and the server's list is merged with ours from them; see launchsettings._sync_args.
    launch_args_synced: list[str] | None = None
    # None follows Settings.sync_saves; True or False overrides it for this game.
    save_sync: bool | None = None
    # The Proton build this game runs under, by name (None: the engine's own). Kept on the server too, see launchsettings.py.
    proton: str | None = None
    # The Wine build this game runs under when its engine is Wine, by name (None: the system's wine). Local to this device.
    wine: str | None = None
    # The winetricks verbs installed in this game's prefix (see winetricks.py); a reinstall starts with none.
    winetricks: list[str] = field(default_factory=list)
    # Entries winetricks could not apply: a start does not try them again (that would show winetricks' window every time) until
    # the list is changed or Done is pressed in the Winetricks page.
    winetricks_failed: list[str] = field(default_factory=list)
    # A digest of the winecfg settings last put into this game's prefix from the server, so they are not applied twice.
    winecfg_applied: str | None = None
    # Whether the server has held launch settings for this game that this device sent or took: when it later holds none, they
    # were deleted there, and the game goes back to the launcher's own (a game that never had any keeps what it has).
    launch_synced: bool = False
    # Chosen when the install began: the game's archive is extracted as it is (it holds a game that needs no
    # installer), so a restarted session does the same.
    extract_only: bool = False
    # The Steam user folders this game has a shortcut in (or should have, while one waits for Steam to close).
    steam_users: list[str] = field(default_factory=list)
    # A change to the shortcut waited for Steam to be closed: it rewrites its shortcuts file when it quits.
    steam_pending: bool = False

    def __post_init__(self) -> None:
        # An earlier version kept the arguments as one text: they become tags the first time the record is read.
        self.launch_args = launchargs.as_tags(self.launch_args)
        if self.launch_args_synced is not None:
            self.launch_args_synced = launchargs.as_tags(self.launch_args_synced)

    @property
    def native(self) -> bool:
        """The chosen executable runs as it is: no launcher, no Wine prefix, no save sync (which reads the prefix)."""
        return is_native_executable(self.executable)


def library_path() -> Path:
    return data_dir() / "installed.json"


def load_library() -> dict[int, InstalledGame]:
    raw = _read_json(library_path(), {})
    known = InstalledGame.__dataclass_fields__
    out = {}
    for key, rec in raw.items():
        if rec.get("steam_user") and not rec.get("steam_users"):  # the single account of older versions
            rec["steam_users"] = [rec["steam_user"]]
        elif rec.get("steam_entries") and not rec.get("steam_users"):  # made before the account was recorded
            rec["steam_users"] = [str(Path(e["shortcuts_path"]).parent.parent) for e in rec["steam_entries"]]
        out[int(key)] = InstalledGame(**{k: v for k, v in rec.items() if k in known})
    return out


def save_library(lib: dict[int, InstalledGame]) -> None:
    _write_json(library_path(), {str(k): asdict(v) for k, v in lib.items()})
