"""MOG Client as a game of its own in Steam: a non-Steam shortcut to the file the user keeps (the AppImage or the exe),
with the artwork made for it, so it opens from Steam's library and Game Mode (stdlib only).

The entry is written at once, with Steam open or not (Steam shows it after a restart). Steam is said to rewrite its
shortcuts file from memory when it quits, which would undo a change made while it ran; so one made then is looked at again
at the next start with Steam closed, and put back if it is gone (`settle`)."""

from __future__ import annotations

from pathlib import Path

from mog_client import steam
from mog_client.config import Settings, save_settings
from mog_client.launcher import client_command

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
    """Put the client in Steam's library now. "added", "added-open" (Steam was running: it shows the entry after a restart,
    and the next start of MOG makes sure Steam did not undo it) or "no-steam"."""
    user_dir = user_dir or next(iter(users()), None)
    if user_dir is None:
        return "no-steam"
    exe, start_dir = _target()
    if settings.steam_client:  # one entry only: a new one replaces what an earlier request made
        steam.remove_shortcut(settings.steam_client)
    settings.steam_client = steam.add_shortcut(user_dir, NAME, exe, start_dir, "", artwork=artwork())
    settings.steam_client_pending = ""
    settings.steam_client_verify = steam.steam_running()
    save_settings(settings)
    return "added-open" if settings.steam_client_verify else "added"


def remove(settings: Settings) -> bool:
    """Take the client out of Steam's library. False when it is not there."""
    if not settings.steam_client:
        return False
    steam.remove_shortcut(settings.steam_client)
    settings.steam_client = None
    settings.steam_client_verify = False
    save_settings(settings)
    return True


def settle(settings: Settings) -> str | None:
    """At start: put the entry back if Steam undid it, follow the client if it was moved or renamed (the shortcut holds its
    path), give it pictures added since it was made. Returns "added" or "updated" when something was written."""
    running = steam.steam_running()
    if settings.steam_client_pending and not settings.steam_client:  # a request an earlier version left waiting
        user_dir = Path(settings.steam_client_pending)
        settings.steam_client_pending = ""
        save_settings(settings)
        if not user_dir.is_dir():
            return None
        add(settings, user_dir)
        return "added"
    record = settings.steam_client
    if not record:
        return None
    if settings.steam_client_verify and not running:
        settings.steam_client_verify = False
        if not steam.has_shortcut(record):  # Steam wrote its own copy of the file when it quit
            add(settings, Path(record["shortcuts_path"]).parent.parent)
            return "added"
        save_settings(settings)
    exe, start_dir = _target()
    changed = bool(steam.update_shortcut(record, exe, start_dir, "", name=NAME)) | _add_missing_artwork(record)
    if changed:
        settings.steam_client_verify = settings.steam_client_verify or running
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
        and not settings.steam_never_ask
        and settings.steam_asked_for != version
        and available()
    )
