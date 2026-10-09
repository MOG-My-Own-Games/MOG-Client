"""Settings and the local record of installed games (JSON files, stdlib only)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    tmp.write_text(json.dumps(data, indent=2))
    if private:
        tmp.chmod(0o600)
    tmp.replace(path)


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
    # How the library is ordered (see ordering.py): last played first, installed first, then this order.
    sort_last_played: bool = False
    sort_installed_first: bool = True
    sort_order: str = "newest"
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
    return bool(path) and str(path).lower().endswith(".sh")


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
    # {"shortcuts_path": str, "appid": int, "artwork": [str]} for each Steam entry created
    steam_entries: list[dict] = field(default_factory=list)
    # This game's launch engine; "auto" follows the launcher chosen in Settings.
    launcher: str = "auto"
    # None follows Settings.sync_saves; True or False overrides it for this game.
    save_sync: bool | None = None
    # Chosen when the install began: the game's archive is extracted as it is (it holds a game that needs no
    # installer), so a restarted session does the same.
    extract_only: bool = False
    # The Steam user folders this game has a shortcut in (or should have, while one waits for Steam to close).
    steam_users: list[str] = field(default_factory=list)
    # A change to the shortcut waited for Steam to be closed: it rewrites its shortcuts file when it quits.
    steam_pending: bool = False

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
