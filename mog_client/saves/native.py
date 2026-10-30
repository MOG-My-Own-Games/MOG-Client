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
    MAC_ROOTS,
    XDG_ROOTS,
    Candidate,
    UnsafeKey,
    is_denied,
    native_key,
    native_root,
    sandbox_roots,
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
# What is looked at in one Flatpak app's folders: there can be a hundred apps, and a game's own folder is near the top.
SANDBOX_MAX_VISITED = 20_000
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
    # The Flatpak app whose sandbox the folder is in (`~/.var/app/<id>/...`), when it is not the real one.
    sandbox: str | None = None


@dataclass(frozen=True)
class Names:
    whole: tuple[str, ...]
    words: tuple[str, ...]


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def user_names() -> frozenset[str]:
    """The login name and the home folder's name: a game folder that is the home (or is named after the user) says nothing
    about the game, so these are no name for it."""
    names = {os.environ.get("USER", ""), os.environ.get("USERNAME", ""), Path.home().name}
    return frozenset(squash(n) for n in names if squash(n))


def game_names(rec: InstalledGame) -> Names:
    """The ways a folder may be named after this game: its title, install folder and executable, squashed. The title is
    what the user typed; the folder and the executable only count when they are not the user's own name."""
    taken = user_names()
    folders = [Path(rec.install_dir).name]
    if rec.executable:
        folders.append(Path(rec.executable).name.split(".")[0])
    whole, words = [], []
    for source, is_title in [(rec.name, True), *((f, False) for f in folders)]:
        if squash(source) in GENERIC_STEMS or (not is_title and squash(source) in taken):
            continue
        if len(squash(source)) >= MIN_NAME_LEN:
            whole.append(squash(source))
        for word in re.split(r"[^A-Za-z0-9]+", source):
            if len(word) >= MIN_NAME_LEN and word.casefold() not in STOP_WORDS and (is_title or squash(word) not in taken):
                words.append(squash(word))
    return Names(tuple(dict.fromkeys(whole)), tuple(dict.fromkeys(words)))


def affinity(component: str, names: Names) -> int:
    squashed = squash(component)
    if len(squashed) >= MIN_NAME_LEN and any(n in squashed or squashed in n for n in names.whole):
        return 2
    return 1 if any(w in squashed for w in names.words) else 0


def search_roots(sandbox: str | None = None) -> list[Path]:
    """Where saves are looked for. With a Flatpak app's `sandbox`, its own XDG folders stand in for the real ones."""
    roots = [native_root(name, sandbox) for name in (*XDG_ROOTS, *MAC_ROOTS)] + [native_root(HOME_KEY) / sub for sub in HOME_SEARCH]
    return list(dict.fromkeys(r for r in roots if r is not None and r.is_dir() and not r.is_symlink()))


def sandbox_search_roots(sandbox: str) -> list[Path]:
    """The XDG folders of a Flatpak app that exist: what a game started from it writes to."""
    return [r for r in sandbox_roots(sandbox).values() if r.is_dir() and not r.is_symlink()]


# TODO: running a native game in a bubblewrap overlay over ~/.config and ~/.local/share would give exactly the
# files it wrote, with no guessing. Not done: it needs bwrap and user namespaces, and nests badly with Steam's
# runtime. Until a source names the folder (the server's manifest), the user is asked where the game saves.
def session_files(since_ns: int, exclude: Iterable[Path] = (), sandbox: str | None = None) -> Iterator[Candidate]:
    """Files below the search roots written since `since_ns`, as candidates keyed by native root.
    `exclude` are folders to leave out (the game's own install folder is handled separately)."""
    skip = tuple(exclude)
    visited = 0
    for root in search_roots(sandbox):
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
                if key := native_key(path, sandbox):
                    yield Candidate(key, path)


# A folder or file inside a game's own folder that looks like where it keeps its saves.
SAVE_WORDS = ("save", "savegame", "savedata", "profile", "slot")
SAVE_SUFFIXES = (".sav", ".save", ".sl2", ".sav2")


def folder_candidates(names: Names, sandboxes: Iterable[str] = ()) -> list[Folder]:
    """The folders below the search roots that are named like the game, for a game that already has its saves and so has no
    session to tell them by (`session_files` does that for a game that just ran). A folder named like the game is taken whole
    and not looked into; the likeliest first. `sandboxes` are Flatpak apps whose own XDG folders are looked in too: a game an
    app started wrote its saves there, and a folder named like the game in one is the proof of it."""
    found: dict[tuple[str, str | None], int] = {}
    visited = 0
    places = [(root, None) for root in search_roots()]
    for app in sandboxes:
        places += [(root, app) for root in sandbox_search_roots(app)]
    for root, sandbox in places:
        if sandbox is not None:
            visited = 0  # each app has a budget of its own: one with a huge folder must not hide the ones after it
        limit = MAX_VISITED if sandbox is None else SANDBOX_MAX_VISITED
        root_depth = len(root.parts)
        for dirpath, dirnames, _files in os.walk(root, followlinks=False):
            here = Path(dirpath)
            depth = len(here.parts) - root_depth
            keep = []
            for d in sorted(dirnames):
                visited += 1
                path = here / d
                if visited > limit:
                    if sandbox is None:
                        return _ranked(found)
                    dirnames[:] = []
                    break
                if path.is_symlink() or d.casefold() in DENY_DIRS or (depth == 0 and d.casefold() in TOP_DENY):
                    continue
                score = affinity(d, names)
                if score and (key := native_key(path, sandbox)) is not None and _holds_files(path):
                    found[key, sandbox] = max(found.get((key, sandbox), 0), score)
                elif depth + 1 < MAX_DEPTH:
                    keep.append(d)  # not the game's folder: its own subfolders may be (`unity3d/Studio/Game`)
            dirnames[:] = keep
    return _ranked(found)


def _ranked(found: dict[tuple[str, str | None], int]) -> list[Folder]:
    return sorted((Folder(k, s, app) for (k, app), s in found.items()), key=lambda f: (-f.score, f.key, f.sandbox or ""))


def _holds_files(folder: Path) -> bool:
    return any(True for _ in walk(folder, "x"))


def game_folder_candidates(install_dir: Path) -> list[Folder]:
    """Folders and files inside the game's own folder that are probably its saves (`saves/`, `profile.sav`), as `game/...` keys,
    at most three levels down. They score 1: the name says it, nothing links them to this game any more than the folder they are in."""
    found: list[Folder] = []
    if not install_dir.is_dir() or install_dir.is_symlink():
        return found
    for dirpath, dirnames, filenames in os.walk(install_dir, followlinks=False):
        here = Path(dirpath)
        depth = len(here.relative_to(install_dir).parts)
        dirnames[:] = sorted(d for d in dirnames if d.casefold() not in DENY_DIRS and not (here / d).is_symlink())
        for d in list(dirnames):
            if any(word in d.casefold() for word in SAVE_WORDS) and _holds_files(here / d):
                found.append(Folder(f"{GAME_KEY}/{(here / d).relative_to(install_dir).as_posix()}", 1))
                dirnames.remove(d)  # taken whole
        for name in sorted(filenames):
            path = here / name
            if name.casefold().endswith(SAVE_SUFFIXES) and not path.is_symlink() and not is_denied((name,)):
                found.append(Folder(f"{GAME_KEY}/{path.relative_to(install_dir).as_posix()}", 1))
        if depth + 1 >= 3:
            dirnames[:] = []
    return found


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


def included_files(includes: Iterable[str], install_dir: Path, sandbox: str | None = None) -> Iterator[Candidate]:
    """Every file in the folders (or the single files) the user confirmed as this game's."""
    for prefix in includes:
        try:
            path = target_for_key(prefix, None, install_dir, sandbox)
        except UnsafeKey:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            yield Candidate(prefix, path)
        elif path.is_dir():
            yield from walk(path, prefix)
