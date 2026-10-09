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


def accounts(settings: Settings) -> list[Path]:
    """The Steam accounts MOG goes into: every one found, or the ones the user picked (none when it is turned off)."""
    found = users()
    if settings.steam_accounts is None:
        return found
    return [d for d in found if d.name in settings.steam_accounts]


def account_of(record: dict) -> Path:
    return Path(record["shortcuts_path"]).parent.parent


def _target() -> tuple[str, str]:
    exe = client_command()
    return exe, str(Path(exe).parent)


def added(settings: Settings) -> bool:
    return bool(settings.steam_clients)


def apply(settings: Settings) -> str:
    """Bring the client's entries in line with the accounts wanted: one in each, none anywhere else. "added" or
    "added-open" (Steam was running: it shows the entry after a restart, and the next start of MOG makes sure Steam did
    not undo it) when one was written, "removed" when only entries went, "unchanged", or "no-steam"."""
    if not users():
        return "no-steam"
    wanted = accounts(settings)
    kept = []
    for record in settings.steam_clients:
        if account_of(record) in wanted:
            kept.append(record)
        else:
            steam.remove_shortcut(record)
    removed = len(kept) != len(settings.steam_clients)
    exe, start_dir = _target()
    have = {account_of(r) for r in kept}
    new = [steam.add_shortcut(d, NAME, exe, start_dir, "", artwork=artwork()) for d in wanted if d not in have]
    settings.steam_clients = kept + new
    settings.steam_client_pending = ""
    if new:
        settings.steam_client_verify = steam.steam_running()
    save_settings(settings)
    if new:
        return "added-open" if settings.steam_client_verify else "added"
    return "removed" if removed else "unchanged"


def remove(settings: Settings) -> bool:
    """Take the client out of Steam's library. False when it is not there."""
    if not settings.steam_clients:
        return False
    for record in settings.steam_clients:
        steam.remove_shortcut(record)
    settings.steam_clients = []
    settings.steam_client_verify = False
    save_settings(settings)
    return True


def settle(settings: Settings) -> str | None:
    """At start, and whenever Steam has closed: put the entries back if Steam undid them, follow the client if it was moved
    or renamed (the shortcut holds its path), give them pictures added since they were made. Returns "added" or "updated"
    when something was written."""
    running = steam.steam_running()
    if settings.steam_client_pending and not settings.steam_clients:  # a request an earlier version left waiting
        user_dir = Path(settings.steam_client_pending)
        settings.steam_client_pending = ""
        save_settings(settings)
        if not user_dir.is_dir():
            return None
        settings.steam_accounts = [user_dir.name]
        settings.steam_decided = True
        apply(settings)
        return "added"
    if not settings.steam_clients:
        return None
    outcome = None
    if settings.steam_client_verify and not running:
        settings.steam_client_verify = False
        gone = [r for r in settings.steam_clients if not steam.has_shortcut(r)]  # Steam wrote its own copy when it quit
        if gone:
            exe, start_dir = _target()
            for record in gone:
                settings.steam_clients[settings.steam_clients.index(record)] = steam.add_shortcut(
                    account_of(record), NAME, exe, start_dir, "", artwork=artwork()
                )
            outcome = "added"
        save_settings(settings)
    exe, start_dir = _target()
    changed = False
    for record in settings.steam_clients:
        changed |= bool(steam.update_shortcut(record, exe, start_dir, "", name=NAME)) | _add_missing_artwork(record)
    if changed:
        settings.steam_client_verify = settings.steam_client_verify or running
        save_settings(settings)
        outcome = outcome or "updated"
    return outcome


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
    """Whether to offer the integration now: Steam is here, the user has not answered yet, and they were not asked already
    for this version (so an update brings the question back once)."""
    return not settings.steam_decided and settings.steam_asked_for != version and available()


def undecided(settings: Settings) -> bool:
    """Steam is here and the user has not said which accounts MOG goes into."""
    return not settings.steam_decided and available()


def status(settings: Settings) -> str:
    """What the settings row says: where MOG stands in Steam."""
    if not available():
        return "Steam was not found on this computer."
    wanted = accounts(settings)
    if not wanted:
        return "Off: MOG and its games are not added to Steam."
    names = ", ".join(label(d, users()) for d in wanted)
    return f"MOG and its games go into: {names}."
