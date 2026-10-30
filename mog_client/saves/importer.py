"""Adding a game that is already on this machine, outside MOG, so its saves are synced (Qt-free).

The game's folder and prefix stay where they are. What this module works out is where the saves are: for a Windows game the prefix
(or, on Windows, the real profile), for a Linux or macOS game the usual folders, and the game's own folder. The user confirms what is
found (`sync.confirm_folders`), and the first sync then takes **everything** in those folders as the baseline, not only what a play
session writes: saves that already exist and are never touched again still go up, and every later version holds all of them.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mog_client import logstore
from mog_client.config import InstalledGame
from mog_client.saves import native, sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.locations import (
    GAME_KEY,
    SHARED_DIRS,
    USER_KEY,
    SANDBOX_DIRS,
    USER_SUBDIRS,
    Candidate,
    describe_key,
    is_native_key,
    profile_candidates,
    sandbox_apps,
    windows_profile_candidates,
)
from mog_client.saves.state import load_state, save_state

WINDOWS, LINUX, MACOS = "windows", "linux", "macos"
TARGET_LABELS = {WINDOWS: "Windows", LINUX: "Linux", MACOS: "macOS"}
# How far down a game's folder is looked at to tell what it is made for.
DETECT_DEPTH = 3
# Folders many games keep their own folder in: the container is not the game's.
CONTAINERS = frozenset({"my games", "ea games", "ubisoft", "paradox interactive"})
DETECT_LIMIT = 6000


def host_system() -> str:
    return {"win32": WINDOWS, "darwin": MACOS}.get(sys.platform, LINUX)


def supported_targets(host: str | None = None) -> list[str]:
    """What a game on this machine can be made for: a Windows game needs a Wine prefix everywhere but on Windows, where nothing else runs."""
    return {WINDOWS: [WINDOWS], MACOS: [WINDOWS, MACOS], LINUX: [WINDOWS, LINUX]}[host or host_system()]


def needs_prefix(target: str, host: str | None = None) -> bool:
    return target == WINDOWS and (host or host_system()) != WINDOWS


def detect_targets(folder: Path, host: str | None = None) -> list[str]:
    """What the game in `folder` looks made for, from the programs in it (a few levels down), the one that runs here first."""
    seen: set[str] = set()
    visited = 0
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(dirpath)
        depth = len(here.relative_to(folder).parts)
        dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
        if any(d.casefold().endswith(".app") for d in dirnames):
            seen.add(MACOS)
        for name in filenames:
            visited += 1
            lower = name.casefold()
            if lower.endswith(".exe"):
                seen.add(WINDOWS)
            elif lower.endswith(".sh") or _is_elf(here / name):
                seen.add(LINUX)
        if depth + 1 >= DETECT_DEPTH or visited > DETECT_LIMIT:
            dirnames[:] = []
        if visited > DETECT_LIMIT:
            break
    first = host or host_system()
    return sorted(seen, key=lambda t: (t != first, t))


def _is_elf(path: Path) -> bool:
    try:
        if path.is_symlink() or not os.access(path, os.X_OK) or "." in path.name[-5:]:
            return False
        with path.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


@dataclass(frozen=True)
class Suggestion:
    key: str  # an archive key: the folder (or the one file) to take as the game's saves
    score: int  # 2: named like the game, 1: shares a word with it or looks like a save folder, 0: nothing links it to the game
    files: int
    newest_ns: int
    sandbox: str | None = None  # the Flatpak app whose own folders the `xdg-*` key is in, when not the real ones

    @property
    def token(self) -> str:
        return choice_token(self.key, self.sandbox)


def label(key: str, sandbox: str | None = None) -> str:
    """A key as a path the user knows; the Flatpak app a folder of is in is named."""
    if key == GAME_KEY or key.startswith(f"{GAME_KEY}/"):
        return "<game folder>" + key[len(GAME_KEY) :]
    if key.startswith(f"{USER_KEY}/"):
        return "<profile>" + key[len(USER_KEY) :]
    if not is_native_key(key):
        return key
    where = describe_key(key, sandbox if key.partition("/")[0] in SANDBOX_DIRS else None)
    return f"{where}   (Flatpak: {sandbox})" if sandbox and key.partition("/")[0] in SANDBOX_DIRS else where


FLATPAK = "flatpak:"


def choice_token(key: str, sandbox: str | None) -> str:
    """What a row of the list of folders stands for: the key, and the Flatpak app when the folder is in its sandbox (the same
    `xdg-*` key can be in the real home and in a sandbox, which are two rows)."""
    return f"{FLATPAK}{sandbox}:{key}" if sandbox and key.partition("/")[0] in SANDBOX_DIRS else key


def split_token(token: str) -> tuple[str, str | None]:
    if token.startswith(FLATPAK):
        sandbox, _, key = token[len(FLATPAK) :].partition(":")
        return key, sandbox
    return token, None


def resolve_choice(tokens: Iterable[str]) -> tuple[list[str], str | None]:
    """The keys a person ticked and the sandbox they are in. A game saves in one place: folders of the real home and of a sandbox, or
    of two sandboxes, cannot be taken together (RuntimeError says so)."""
    keys, apps, real = [], set(), False
    for token in tokens:
        key, sandbox = split_token(token)
        keys.append(key)
        if key.partition("/")[0] in SANDBOX_DIRS:
            apps.add(sandbox)
    if len(apps) > 1:
        raise RuntimeError("A game saves in one place: pick folders from the real home or from one Flatpak app, not both")
    return keys, next(iter(apps), None) if apps != {None} else None


def _unit_of(key: str) -> str:
    """The game-sized folder a profile file sits in, or the file itself when it sits directly in a profile folder (offering
    the whole Documents folder as one game's saves would be taking everything the user has)."""
    for root in (*(f"{USER_KEY}/{sub}" for sub in USER_SUBDIRS), *SHARED_DIRS):
        if key.startswith(root + "/"):
            inside = key[len(root) + 1 :].split("/")
            if len(inside) == 1:
                return key
            if inside[0].casefold() in CONTAINERS and len(inside) > 2:
                return f"{root}/{inside[0]}/{inside[1]}"  # `My Games/Game`: the game is one level further
            return f"{root}/{inside[0]}"
    return key.split("/")[0] if "/" in key else key


def _group(candidates: Iterable[Candidate], names: native.Names) -> dict[str, tuple[int, int, int]]:
    """key of a game-sized folder (or file) -> (score, files, newest mtime)."""
    units: dict[str, list[int]] = {}
    for candidate in candidates:
        unit = _unit_of(candidate.key)
        try:
            mtime = candidate.path.stat().st_mtime_ns
        except OSError:
            continue
        entry = units.setdefault(unit, [0, 0, 0])
        entry[1] += 1
        entry[2] = max(entry[2], mtime)
    for unit, entry in units.items():
        entry[0] = max((native.affinity(part, names) for part in unit.split("/")[3:]), default=0)
    return {k: (v[0], v[1], v[2]) for k, v in units.items()}


def _measure(keys: Iterable[tuple[str, str | None]], install_dir: Path) -> dict[tuple[str, str | None], tuple[int, int]]:
    out = {}
    for key, sandbox in keys:
        files = newest = 0
        for candidate in native.included_files([key], install_dir, sandbox):
            try:
                newest = max(newest, candidate.path.stat().st_mtime_ns)
            except OSError:
                continue
            files += 1
        out[key, sandbox] = (files, newest)
    return out


def suggest(rec: InstalledGame, prefix: Path | None = None, hints: Iterable[str] = ()) -> list[Suggestion]:
    """Where the saves of the game may be, the likeliest first. `prefix` is the Wine prefix of a Windows game (on Windows the
    real profile is read); `hints` are keys the server's manifest names. A native game's are also looked for in the sandbox of
    each Flatpak app (itch's, say): a folder named like the game there says the game was started from that app. Nothing is read
    beyond the usual save places."""
    names = native.game_names(rec)
    install_dir = Path(rec.install_dir)
    found: dict[tuple[str, str | None], tuple[int, int, int]] = {}
    if rec.target == WINDOWS:
        if not prefixes.has_prefixes():
            found.update({(k, None): v for k, v in _group(windows_profile_candidates(), names).items()})
        elif prefix is not None and (drive_c := prefixes.drive_c_of(prefix)) is not None:
            found.update({(k, None): v for k, v in _group(profile_candidates(drive_c), names).items()})
    else:
        folders = native.folder_candidates(names, sandbox_apps() if sys.platform != "win32" else ())
        measured = _measure(((f.key, f.sandbox) for f in folders), install_dir)
        found.update({(f.key, f.sandbox): (f.score, *measured[f.key, f.sandbox]) for f in folders})
    inside = native.game_folder_candidates(install_dir)
    measured = _measure(((f.key, None) for f in inside), install_dir)
    for folder in inside:
        found.setdefault((folder.key, None), (folder.score, *measured[folder.key, None]))
    hinted = _measure(((key, None) for key in dict.fromkeys(hints)), install_dir)
    for item, (files, newest) in hinted.items():
        if files:
            found[item] = (2, files, newest)
    ranked = sorted(found.items(), key=lambda item: (-item[1][0], -item[1][2], item[0][0], item[0][1] or ""))
    return [Suggestion(k, score, files, newest, app) for (k, app), (score, files, newest) in ranked if files]


def sandbox_suggestions(rec: InstalledGame) -> list[Suggestion]:
    """The folders named like the game in the sandbox of a Flatpak app: proof the game was started from that app (the proof is
    the folder; MOG does not read the app's own records). Nothing for a game that is not native or is already tied to one."""
    if rec.target == WINDOWS or not (apps := sandbox_apps()):
        return []
    names = native.game_names(rec)
    install_dir = Path(rec.install_dir)
    folders = [f for f in native.folder_candidates(names, apps) if f.sandbox and f.score == 2]
    measured = _measure(((f.key, f.sandbox) for f in folders), install_dir)
    return [
        Suggestion(f.key, f.score, *measured[f.key, f.sandbox], f.sandbox) for f in folders if measured[f.key, f.sandbox][0]
    ]


def remember(rec: InstalledGame, includes: Iterable[str], prefix: Path | None = None) -> None:
    """Record what the user confirmed: the prefix to look in and the folders that are the game's saves (an empty list is an answer
    too: a game with no saves here yet, which takes another machine's)."""
    if prefix is not None:
        sync.remember_prefix(rec.game_id, prefix, prefixes.FROM_GAME if rec.prefix_dedicated else prefixes.FROM_USER)
    state = load_state(rec.game_id)
    state.includes = sorted(set(includes))
    state.pending_folders = []
    save_state(rec.game_id, state)


def first_sync(ctx: sync.Context) -> sync.StartupOutcome:
    """The first look at an added game's saves. When another machine already left a version, `apply_newer` brings it over, or says
    there is a conflict for the person to settle (`sync.restore`, or `settle_keeping_local`). Otherwise everything in the confirmed
    folders is sent as the baseline, whether or not a play session touched it."""
    try:
        outcome = sync.apply_newer(ctx)
    except RuntimeError:
        outcome = None  # the server could not say: send ours
    if outcome is not None:
        return outcome
    return _send_baseline(ctx)


def settle_keeping_local(ctx: sync.Context, version_id: int) -> sync.StartupOutcome:
    """After a conflict the person chose to keep this machine's saves: the other machine's version is set aside and ours goes up."""
    sync.decline(ctx, version_id)
    return _send_baseline(ctx)


def _send_baseline(ctx: sync.Context) -> sync.StartupOutcome:
    result = sync.backup(ctx, sync.IMPORT, force=True)
    status = {"uploaded": "uploaded", "duplicate": "unchanged", "offline": "offline"}.get(result.status, result.status)
    logstore.info(f"Saves of {ctx.rec.name}: first sync {status}")
    return sync.StartupOutcome(status, version=result.version, files=len(result.changed))
