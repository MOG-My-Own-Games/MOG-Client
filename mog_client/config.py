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


@dataclass
class Settings:
    base: str = ""
    user: str = ""
    password: str = ""
    games_dir: str = ""
    # "auto", "faugus", "umu", "wine" (Linux) or "native" (Windows)
    launcher: str = "auto"
    # Look for a newer release at startup (only in builds that can self-update).
    check_updates: bool = True
    # The library page's left-hand sidebar (toggled with L2).
    show_sidebar: bool = True
    # The launchers found on this machine, in order of preference, and the MOG version that looked.
    launchers: list[str] = field(default_factory=list)
    launchers_scanned_for: str = ""
    # Short sounds when moving through the menus and when a game starts.
    sounds: bool = True
    # Look over the saves of every game when the client starts (only with sync_saves on).
    sync_on_start: bool = True
    # Back up each game's save files to the server (a game can opt out: InstalledGame.save_sync).
    sync_saves: bool = True

    @property
    def games_path(self) -> Path:
        return Path(self.games_dir) if self.games_dir else data_dir() / "games"

    @property
    def configured(self) -> bool:
        return bool(self.base and self.user and self.password)


def settings_path() -> Path:
    return config_dir() / "config.json"


def load_settings() -> Settings:
    raw = _read_json(settings_path(), {})
    known = Settings.__dataclass_fields__
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(s: Settings) -> None:
    _write_json(settings_path(), asdict(s), private=True)


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
    # The Steam user folder this game's shortcut belongs in, if it should have one.
    steam_user: str | None = None
    # A change to the shortcut waited for Steam to be closed: it rewrites its shortcuts file when it quits.
    steam_pending: bool = False


def library_path() -> Path:
    return data_dir() / "installed.json"


def load_library() -> dict[int, InstalledGame]:
    raw = _read_json(library_path(), {})
    known = InstalledGame.__dataclass_fields__
    out = {}
    for key, rec in raw.items():
        out[int(key)] = InstalledGame(**{k: v for k, v in rec.items() if k in known})
    return out


def save_library(lib: dict[int, InstalledGame]) -> None:
    _write_json(library_path(), {str(k): asdict(v) for k, v in lib.items()})
