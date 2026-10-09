"""Finding the Wine prefix a game runs in, without ever choosing one for the user.

MOG does not tell Faugus, umu, Proton or Wine which prefix to use (the user may start the game
by hand, in whatever setup they have), so it works out where the game lives from what those
programs already record, and asks when it cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mog_client.config import InstalledGame
from mog_client.launcher import PREFIX_DIR

FAUGUS_FLATPAK = "io.github.Faugus.faugus-launcher"

FROM_GAME = "game-folder"  # the `pfx` MOG hands the engine, inside the game's own folder
FROM_USER = "chosen"
FROM_STATE = "remembered"
FROM_FAUGUS = "faugus"
FROM_STEAM = "steam"
FROM_ENGINE = "default"


@dataclass(frozen=True)
class Found:
    prefix: Path
    source: str


def drive_c_of(prefix: Path) -> Path | None:
    """The `drive_c` folder of a prefix as the user, Faugus, umu or Proton lays it out."""
    for rel in ("pfx/drive_c", "drive_c", "pfx-data/pfx/drive_c"):
        candidate = prefix / rel
        if candidate.is_dir():
            return candidate
    return None


# What a finished prefix holds next to its drive_c (umu, Proton and Wine alike): the registry files, and the stamp
# Wine writes when it has set the prefix up.
MADE_MARKERS = ("system.reg", "user.reg", ".update-timestamp")


def prefix_made(prefix: Path) -> bool:
    """Whether a launcher has finished making the prefix: the markers are there and the profile folder exists. Not a size
    test: a prefix is 100 MB to 900 MB depending on the runner, and it passes any size while it is still being copied."""
    drive_c = drive_c_of(prefix)
    if drive_c is None or not all((drive_c.parent / marker).is_file() for marker in MADE_MARKERS):
        return False
    users = drive_c / "users"
    try:
        return any(p.is_dir() and p.name != "Public" for p in users.iterdir())
    except OSError:
        return False


def is_prefix(path: Path) -> bool:
    return path.is_dir() and drive_c_of(path) is not None


def faugus_games_files() -> list[Path]:
    home = Path.home()
    return [
        home / ".var/app" / FAUGUS_FLATPAK / "data/faugus-launcher/games.json",
        home / ".local/share/faugus-launcher/games.json",
    ]


def faugus_prefix_for(executable: str) -> Path | None:
    """The prefix of the Faugus game whose executable is this one, if the user added it to Faugus."""
    wanted = _resolved(executable)
    for games_json in faugus_games_files():
        try:
            games = json.loads(games_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for game in games if isinstance(games, list) else []:
            if isinstance(game, dict) and game.get("prefix") and _resolved(game.get("path", "")) == wanted:
                return Path(game["prefix"]).expanduser()
    return None


def steam_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/debian-installation",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]


def steam_prefix_for(rec: InstalledGame) -> Path | None:
    """The compatdata prefix Steam made for the game's non-Steam shortcut, once it has run."""
    for entry in rec.steam_entries:
        appid = entry.get("appid")
        if not appid:
            continue
        for root in steam_roots():
            prefix = root / "steamapps/compatdata" / str(appid)
            if is_prefix(prefix):
                return prefix
    return None


def engine_default_prefix(engine: str) -> Path | None:
    """What the runner uses when nobody sets WINEPREFIX. Proton has no default: it cannot run without one."""
    home = Path.home()
    if engine == "wine":
        return home / ".wine"
    if engine == "umu":
        return home / "Games/umu/umu-default"
    return None


def candidate_prefixes() -> list[Path]:
    """Prefixes that exist on this machine, for the user to pick from when none could be worked out."""
    home = Path.home()
    found: list[Path] = [home / ".wine"]
    for parent in (home / "Games/umu", home / "Faugus"):
        if parent.is_dir():
            found += sorted(p for p in parent.iterdir() if p.is_dir())
    for root in steam_roots():
        compat = root / "steamapps/compatdata"
        if compat.is_dir():
            found += sorted(p for p in compat.iterdir() if p.is_dir())
    seen: set[Path] = set()
    unique = []
    for prefix in found:
        key = prefix.resolve()
        if key not in seen and is_prefix(prefix):
            seen.add(key)
            unique.append(prefix)
    return unique


def resolve_prefix(
    rec: InstalledGame, engine: str, remembered: str | None = None, remembered_source: str | None = None
) -> Found | None:
    """Where the game's prefix is, or None when the user has to say. Never creates anything.

    What the user set for the game or picked for the sync comes first, then what Faugus or Steam currently record (so
    moving the game there is followed), then what an earlier run remembered, then the runner's default."""
    if rec.prefix:
        in_game_folder = Path(rec.prefix) == Path(rec.install_dir) / PREFIX_DIR
        return Found(Path(rec.prefix), FROM_GAME if in_game_folder else FROM_USER)
    if remembered and remembered_source == FROM_USER:
        return Found(Path(remembered), FROM_USER)  # picked for the sync only: it does not change how the game starts
    own = _own_prefix(rec)
    if own is not None and drive_c_of(own) is not None:
        return Found(own, FROM_GAME)  # the game has run in it, whether or not its record says so
    if rec.executable:
        if prefix := faugus_prefix_for(rec.executable):
            return Found(prefix, FROM_FAUGUS)
    if prefix := steam_prefix_for(rec):
        return Found(prefix, FROM_STEAM)
    if remembered:
        return Found(Path(remembered), remembered_source or FROM_STATE)
    if (prefix := engine_default_prefix(engine)) is not None and is_prefix(prefix):
        return Found(prefix, FROM_ENGINE)
    if own is not None:
        return Found(own, FROM_GAME)  # made but never run in: nothing to save yet, and nothing to ask the user about
    return None


def _own_prefix(rec: InstalledGame) -> Path | None:
    """The `pfx` folder MOG hands the engine, inside the game's own folder, when it is there. A game whose record
    does not name it (one installed by an earlier version) still runs in it. A native game has no prefix."""
    if rec.native:
        return None
    path = Path(rec.install_dir) / PREFIX_DIR
    return path if path.is_dir() else None


def _resolved(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return path
