"""MOG Client as a game of its own in Steam: a non-Steam shortcut to the file the user keeps (the AppImage or the exe),
with the artwork made for it, so it opens from Steam's library and Game Mode (stdlib only).

Steam rewrites its shortcuts file from memory when it quits, so nothing is written while it runs: a request made then
waits in `Settings.steam_client_pending` and is carried out at a start with Steam closed (`settle`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from mog_client import steam
from mog_client.config import Settings, save_settings
from mog_client.launcher import _flatpak_has, client_command

STEAM_FLATPAK = "com.valvesoftware.Steam"

NAME = "MOG - My Own Games"
ASSETS = Path(__file__).parent / "gui" / "assets"
# What Steam calls each image, and the file that is made for it. They are the artwork published on SteamGridDB for MOG
# (https://www.steamgriddb.com/game/5555531); the wide capsule is its "galaxy" picture cut to Steam's shape, and the logo is
# the text logo, transparent so Steam can lay it over the hero.
ARTWORK = {
    "portrait": "steam/capsule.png",
    "wide": "steam/wide.png",
    "hero": "steam/hero.png",
    "logo": "steam/logo.png",
    "icon": "icon.png",
}


def artwork() -> dict[str, bytes]:
    out = {}
    for kind, name in ARTWORK.items():
        try:
            out[kind] = (ASSETS / name).read_bytes()
        except OSError:
            continue  # a build without it still makes the shortcut
    return out


def running() -> bool:
    return steam.steam_running()


def steam_command() -> list[str] | None:
    """What starts Steam on this computer (and, with `-shutdown`, closes it), or None when it cannot be told."""
    if shutil.which("steam"):
        return ["steam"]
    if shutil.which("flatpak") and _flatpak_has(STEAM_FLATPAK):
        return ["flatpak", "run", STEAM_FLATPAK]
    return None


def can_close_steam() -> bool:
    """Whether MOG may close Steam for the user: not in Game Mode, and not when Steam itself started this client, since
    closing Steam would close it too."""
    inside_steam = any(os.environ.get(v) for v in ("SteamGameId", "SteamAppId", "SteamGamepadUI"))
    return sys.platform.startswith("linux") and not inside_steam and steam_command() is not None


def close_steam(
    wait: float = 40.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Ask Steam to quit and wait for it to be gone. True when it is not running any more."""
    command = steam_command()
    if command is None:
        return False
    try:
        subprocess.run([*command, "-shutdown"], check=False, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return not running()
    deadline = clock() + wait
    while running() and clock() < deadline:
        sleep(0.5)
    return not running()


def start_steam() -> bool:
    """Open Steam again, on its own so it outlives this client."""
    command = steam_command()
    if command is None:
        return False
    try:
        subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def users() -> list[Path]:
    """The Steam user folders on this machine, the most recently active first; none when Steam is not installed."""
    try:
        return steam.steam_user_dirs()
    except OSError:
        return []


def label(user_dir: Path, among: list[Path] | None = None) -> str:
    """How to call a Steam account to the user: its name, with the number only where two accounts share a name (or it
    has none to show)."""
    name = steam.persona_name(user_dir)
    if name is None:
        return f"Account {user_dir.name}"
    twins = [d for d in (among or []) if d != user_dir and steam.persona_name(d) == name]
    return f"{name} ({user_dir.name})" if twins else name


def available() -> bool:
    return bool(users())


def _target() -> tuple[str, str]:
    exe = client_command()
    return exe, str(Path(exe).parent)


def added(settings: Settings) -> bool:
    return bool(settings.steam_client)


def add(settings: Settings, user_dir: Path | None = None) -> str:
    """Put the client in Steam's library. "added", "pending" (Steam is running: it goes in at the next start with it
    closed) or "no-steam"."""
    user_dir = user_dir or next(iter(users()), None)
    if user_dir is None:
        return "no-steam"
    if steam.steam_running():
        settings.steam_client_pending = str(user_dir)
        save_settings(settings)
        return "pending"
    exe, start_dir = _target()
    if settings.steam_client:  # one entry only: a new one replaces what an earlier request made
        steam.remove_shortcut(settings.steam_client)
    settings.steam_client = steam.add_shortcut(user_dir, NAME, exe, start_dir, "", artwork=artwork())
    settings.steam_client_pending = ""
    save_settings(settings)
    return "added"


def remove(settings: Settings) -> bool:
    """Take the client out of Steam's library. False when it is not there, or Steam is running (it would put it back)."""
    if not settings.steam_client or steam.steam_running():
        return False
    steam.remove_shortcut(settings.steam_client)
    settings.steam_client = None
    save_settings(settings)
    return True


def settle(settings: Settings) -> str | None:
    """At start: carry out a request that waited for Steam to close, and follow the client if it was moved or renamed
    (the shortcut holds its path). Returns "added" or "updated" when something was written."""
    if steam.steam_running():
        return None
    if settings.steam_client_pending and not settings.steam_client:
        user_dir = Path(settings.steam_client_pending)
        if user_dir.is_dir():
            return add(settings, user_dir)
        settings.steam_client_pending = ""
        save_settings(settings)
        return None
    if settings.steam_client:
        exe, start_dir = _target()
        changed = bool(steam.update_shortcut(settings.steam_client, exe, start_dir, "", name=NAME))
        changed |= _add_missing_artwork(settings.steam_client)
        if changed:
            save_settings(settings)
            return "updated"
    return None


def _add_missing_artwork(record: dict) -> bool:
    """An entry made by an earlier version lacks the pictures added since (the logo): put them in its grid folder, under
    the id it already has, and note them in its record."""
    have = {Path(f).stem.removeprefix(str(record["appid"])) for f in record.get("artwork", []) if Path(f).is_file()}
    suffixes = {"portrait": "p", "wide": "", "hero": "_hero", "logo": "_logo", "icon": "_icon"}
    wanted = {kind: blob for kind, blob in artwork().items() if suffixes[kind] not in have}
    if not wanted:
        return False
    user_dir = Path(record["shortcuts_path"]).parent.parent
    record["artwork"] = [*record.get("artwork", []), *steam.write_artwork(user_dir, record["appid"], wanted)]
    return True


def should_ask(settings: Settings, version: str) -> bool:
    """Whether to offer the shortcut now: Steam is here, it is not in yet, the user did not say never, and they were not
    asked already for this version (so an update brings the question back once)."""
    return (
        not settings.steam_client
        and not settings.steam_client_pending
        and not settings.steam_never_ask
        and settings.steam_asked_for != version
        and available()
    )
