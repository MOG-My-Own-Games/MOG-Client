"""Where a native Linux game may have put its saves, and which of those places look like its own.

A native game has no prefix and writes wherever it likes below the home folder, so the search is
narrow: a few roots, only files written since the game started, and nothing from a program known
to be noisy. What is found is offered as folders, the ones named like the game first, and the
user confirms them once (see sync).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from mog_client.config import InstalledGame
from mog_client.saves.locations import (
    DENY_DIRS,
    GAME_KEY,
    HOME_KEY,
    XDG_ROOTS,
    Candidate,
    UnsafeKey,
    is_denied,
    native_key,
    native_root,
    target_for_key,
    walk,
)
from mog_client.saves.scan import WINDOW_SLACK_NS

# Folders of other programs below the search roots (any case), never a game's saves.
TOP_DENY = frozenset(
    {
        "steam", "flatpak", "containers", "trash", "applications", "icons", "mime", "google-chrome", "chromium",
        "mozilla", "firefox", "discord", "slack", "code", "jetbrains", "pulse", "dconf", "systemd", "gtk-3.0",
        "gtk-4.0", "kde.org", "kdeconnect", "baloo", "fontconfig", "ibus", "evolution", "nautilus", "mog-client",
        "faugus-launcher", "umu", "lutris", "heroic", "bottles", "protontricks", "pip", "uv", "npm", "yarn",
        "cargo", "mesa_shader_cache", "nvidia", "autostart", "xdg-desktop-portal", "wireplumber", "pipewire",
        "obs-studio", "spotify", "telegram desktop", "vlc",
    }
)  # fmt: skip
# Under the home folder, only these hold a game's saves; the rest of it is the user's own files.
HOME_SEARCH = ("Documents",)
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_VISITED = 100_000
# How many folders below a root a game's own folder may sit (`unity3d/Studio/Game`).
MAX_DEPTH = 3
MIN_NAME_LEN = 4
# Words and file names that say nothing about which game a folder belongs to.
STOP_WORDS = frozenset(
    {"the", "and", "edition", "game", "definitive", "remastered", "complete", "collection", "deluxe", "goty", "ultimate"}
)
GENERIC_STEMS = frozenset({"start", "run", "launch", "launcher", "game", "play", "main", "install", "setup"})


@dataclass(frozen=True)
class Folder:
    key: str
    # 2: named like the game, 1: shares a word with its name, 0: nothing links it to the game.
    score: int


@dataclass(frozen=True)
class Names:
    whole: tuple[str, ...]
    words: tuple[str, ...]


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def game_names(rec: InstalledGame) -> Names:
    """The ways a folder may be named after this game: its title, install folder and executable, squashed."""
    sources = [rec.name, Path(rec.install_dir).name]
    if rec.executable:
        sources.append(Path(rec.executable).name.split(".")[0])
    whole, words = [], []
    for source in sources:
        if squash(source) in GENERIC_STEMS:
            continue
        if len(squash(source)) >= MIN_NAME_LEN:
            whole.append(squash(source))
        for word in re.split(r"[^A-Za-z0-9]+", source):
            if len(word) >= MIN_NAME_LEN and word.casefold() not in STOP_WORDS:
                words.append(squash(word))
    return Names(tuple(dict.fromkeys(whole)), tuple(dict.fromkeys(words)))


def affinity(component: str, names: Names) -> int:
    squashed = squash(component)
    if len(squashed) >= MIN_NAME_LEN and any(n in squashed or squashed in n for n in names.whole):
        return 2
    return 1 if any(w in squashed for w in names.words) else 0


def search_roots() -> list[Path]:
    roots = [native_root(name) for name in XDG_ROOTS] + [native_root(HOME_KEY) / sub for sub in HOME_SEARCH]
    return list(dict.fromkeys(r for r in roots if r is not None and r.is_dir() and not r.is_symlink()))


# TODO: running a native game in a bubblewrap overlay over ~/.config and ~/.local/share would give exactly the
# files it wrote, with no guessing. Not done: it needs bwrap and user namespaces, and nests badly with Steam's
# runtime. Until a source names the folder (the server's manifest), the user is asked where the game saves.
def session_files(since_ns: int, exclude: Iterable[Path] = ()) -> Iterator[Candidate]:
    """Files below the search roots written since `since_ns`, as candidates keyed by native root.
    `exclude` are folders to leave out (the game's own install folder is handled separately)."""
    skip = tuple(exclude)
    visited = 0
    for root in search_roots():
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            at_root = here == root
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d.casefold() not in DENY_DIRS
                and not (at_root and d.casefold() in TOP_DENY)
                and not (here / d).is_symlink()
                and not any((here / d) == s or s in (here / d).parents for s in skip)
            )
            for name in sorted(filenames):
                visited += 1
                if visited > MAX_VISITED:
                    return
                path = here / name
                if path.is_symlink() or is_denied((*here.relative_to(root).parts, name)):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime_ns < since_ns - WINDOW_SLACK_NS or stat.st_size > MAX_FILE_BYTES:
                    continue
                if key := native_key(path):
                    yield Candidate(key, path)


def _below_root(key: str) -> tuple[str, list[str]]:
    """A key split into its search root and the names below it (`home/Documents` is a root of its own)."""
    parts = key.split("/")
    depth = 2 if key.startswith(f"{HOME_KEY}/{HOME_SEARCH[0]}/") else 1
    return "/".join(parts[:depth]), parts[depth:]


def folder_score(key: str, names: Names) -> int:
    """How much a folder or file key looks like it belongs to the game, from the names below its root."""
    return max((affinity(part, names) for part in _below_root(key)[1]), default=0)


def suggest_folders(keys: Iterable[str], names: Names) -> list[Folder]:
    """The folders the keys sit in, game-sized, the likeliest first. A folder is as deep as the first
    level named like the game (`unity3d/Studio/Game`), else the top one."""
    best: dict[str, int] = {}
    for key in keys:
        root, below = _below_root(key)
        if len(below) <= 1:
            folder, score = key, affinity(Path(below[0]).stem if below else "", names)
        else:
            levels = [(depth, affinity(below[depth - 1], names)) for depth in range(1, min(MAX_DEPTH, len(below) - 1) + 1)]
            depth, score = next((lv for lv in levels if lv[1] == 2), None) or next((lv for lv in levels if lv[1]), levels[0])
            folder = "/".join([root, *below[:depth]])
        best[folder] = max(best.get(folder, 0), score)
    return sorted((Folder(k, s) for k, s in best.items()), key=lambda f: (-f.score, f.key))


def restored_folder(key: str) -> str:
    """The folder a restored file is kept in, so the files the game writes beside it are backed up too."""
    root, below = _below_root(key)
    return "/".join([root, *below[:-1]]) if len(below) > 1 else key


def manifest_folders(paths: Iterable[str], install_dir: Path) -> list[str]:
    """Keys for the save paths the server's manifest names (`<xdgConfig>/Game/Saves`, `<base>/saves`): the folder
    of a path with a wildcard, and nothing for a path with another placeholder or one that is a whole root."""
    roots = {
        "<home>": native_root(HOME_KEY),
        "<xdgConfig>": native_root("xdg-config"),
        "<xdgData>": native_root("xdg-data"),
        "<base>": install_dir,
    }
    keys: dict[str, None] = {}
    for raw in paths:
        placeholder, _, rest = raw.partition("/")
        if placeholder not in roots or roots[placeholder] is None or ".." in rest.split("/"):
            continue
        parts = []
        for part in rest.split("/"):
            if any(c in part for c in "*?["):
                break
            parts.append(part)
        path = roots[placeholder].joinpath(*parts)
        if path == install_dir:
            continue
        try:
            key = f"{GAME_KEY}/{path.relative_to(install_dir).as_posix()}"
        except ValueError:
            key = native_key(path)
        if key and "/" in key:
            keys.setdefault(key)
    return list(keys)


def included_files(includes: Iterable[str], install_dir: Path) -> Iterator[Candidate]:
    """Every file in the folders (or the single files) the user confirmed as this game's."""
    for prefix in includes:
        try:
            path = target_for_key(prefix, None, install_dir)
        except UnsafeKey:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            yield Candidate(prefix, path)
        elif path.is_dir():
            yield from walk(path, prefix)
