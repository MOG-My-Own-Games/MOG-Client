"""Quiver Launcher's games, read from its own files (Qt-free, nothing is written there).

Two lines of Quiver exist and both are read: 3.x (.NET, what the AppImage and the Windows zip are today) keeps `apps.json` and
`settings.json` in a folder of its own and a game's markers in the game's folder; 4.x (Tauri) keeps `installs.json` and
`library.json` in a data folder (below). `find` says which one a folder is by the file it holds.

Quiver installs native ports and recompilations and keeps them up to date, in place and several times a day. MOG does not
install or update them: it lists what Quiver installed as a library of its own, starts the games, gives them Steam shortcuts
and syncs their saves. The folder of such a game keeps changing under MOG's feet (shaders, recompiled files), so the saves are
never guessed there: they are exactly the places `known_saves` or the user named (`InstalledGame.explicit_saves`).

Quiver keeps its data in one folder (`QUIVER_DATA`, else `<program folder>/data` for a portable install, else Tauri's
`com.quiverlauncher.app` folder under the user's data folder, which the Flatpak build shares):

    library.json   [{id, slug, overrides: {name, ...}, ...}]
    installs.json  {id: {folder, version, executables, launch: {executable}, dir, group, inApps, ...}}
    catalog.json   the catalog it last fetched (names, art)
    apps/          where the games are, `apps-folder.json` ({"path": ...}) moves it
    proton/<folder>  the Wine prefix of a Windows game started on Linux
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from mog_client.launcher import list_executables

APP_DIR = "com.quiverlauncher.app"
# The library the sidebar shows them under; a server library never has a negative id.
LIBRARY_ID = -1
LIBRARY_NAME = "Quiver Launcher"
PORTABLE_MARKER = "portable.txt"


def _appimage_folders() -> list[Path]:
    """Linux: the folders a Quiver 3.x AppImage sits in, which is where it keeps its data (its first default). Found by the menu
    entry AppImageLauncher and others write for it, then by the AppImage itself in the folders such files usually go to."""
    home = Path.home()
    found: list[Path] = []
    try:
        entries = sorted((home / ".local" / "share" / "applications").glob("*.desktop"))
    except OSError:
        entries = []
    for entry in entries:
        if "quiver" not in entry.name.lower():
            continue
        try:
            lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith(("Exec=", "TryExec=")):
                try:
                    program = Path(shlex.split(line.partition("=")[2])[0])
                except (ValueError, IndexError):
                    continue
                if program.is_absolute() and program.name.lower().endswith(".appimage"):
                    found.append(program.parent)
    for folder in (home / "Applications", home / "AppImages", home / "Downloads", home / ".local" / "bin", home / "Desktop", home / "bin", Path("/opt")):
        try:
            names = [n.name.lower() for n in folder.iterdir()]
        except OSError:
            continue
        if any("quiver" in n and n.endswith(".appimage") for n in names):
            found.append(folder)
    return list(dict.fromkeys(found))


def default_dirs() -> list[Path]:
    """Where Quiver's data folder can be when it was not picked, in the order Quiver itself prefers them. 3.x keeps it beside its
    program (the AppImage's folder; the folder of the portable zip on Windows) or, failing that, in `QuiverLauncher` below the user's
    data folder; 4.x in `com.quiverlauncher.app` there (`QUIVER_DATA` moves it)."""
    found: list[Path] = []
    if env := os.environ.get("QUIVER_DATA"):
        found.append(Path(env))
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        found.append(local / "QuiverLauncher")  # where an installed 3.x puts itself; the portable zip is wherever it was unpacked
        found.append(Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_DIR)
        for folder in (Path.home() / "Downloads", Path.home() / "Desktop", Path.home(), Path.home() / "Documents"):
            try:
                found += [c for c in folder.iterdir() if c.is_dir() and c.name.lower().startswith("quiver")]
            except OSError:
                continue
        return found
    if sys.platform == "darwin":
        support = Path.home() / "Library" / "Application Support"
        return [*found, support / "QuiverLauncher", support / APP_DIR]
    base = os.environ.get("XDG_DATA_HOME", "")
    # The variable can point into a sandbox the client was started from; Quiver itself, started from the menu, uses the real one.
    roots = list(dict.fromkeys([*([Path(base)] if os.path.isabs(base) else []), Path.home() / ".local" / "share"]))
    return [*found, *_appimage_folders(), *(r / "QuiverLauncher" for r in roots), *(r / APP_DIR for r in roots)]


V3_FILE, V4_FILE = "apps.json", "installs.json"


def _is_quiver_folder(folder: Path) -> bool:
    """Whether a folder holds Quiver's list of games. `apps.json` is a common name, so it must look like Quiver's: games with a
    `folderName`, or a library still empty beside Quiver's `settings.json`."""
    if (folder / V4_FILE).is_file():
        return True
    apps = _read(folder / V3_FILE, None)
    apps = apps.get("apps") if isinstance(apps, dict) else apps
    if not isinstance(apps, list):
        return False
    if any(isinstance(a, dict) and "folderName" in a for a in apps):
        return True
    return not apps and isinstance(_read(folder / "settings.json", None), dict)


def find(chosen: str = "") -> Path | None:
    """Quiver's data folder: the one the user picked, else the first usual place that holds its list of games."""
    for folder in ([Path(chosen)] if chosen else []) + default_dirs():
        if _is_quiver_folder(folder):
            return folder
    return None


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # 3.x writes a byte order mark
    except (OSError, ValueError):
        return default


@dataclass
class QuiverGame:
    id: str  # Quiver's own (`github:owner/repo`)
    name: str
    folder: Path  # where the game is
    executable: Path | None
    version: str = ""
    cover: str = ""
    # "windows" when Quiver starts it with Wine or Proton (a .exe on Linux, or any game on Windows), else "linux"
    target: str = "linux"
    prefix: Path | None = None
    keys: list[str] = field(default_factory=list)  # where its saves are, when known (`known_saves`)

    @property
    def present(self) -> bool:
        return self.folder.is_dir()


def virtual_id(quiver_id: str) -> int:
    """The id a game has in the library list until it has an entry on the server: negative, so it never meets a server's id."""
    return -(zlib.crc32(quiver_id.encode()) % 2_000_000_000) - 2


def is_virtual(game_id: int) -> bool:
    return game_id < 0


def standin(game: QuiverGame) -> dict:
    """The library entry of a Quiver game that has no record here yet (it gets one when it is first started from MOG)."""
    return {
        "id": virtual_id(game.id),
        "name": game.name,
        "fs_name": game.name,
        "library_id": LIBRARY_ID,
        "igdb_id": None,
        "quiver": game.id,
        "cover_path": game.cover or None,
    }


def _pretty(folder: str) -> str:
    """A name from the install folder when the catalog is not there: `TheLegendOfZelda-Recomp` is `The Legend Of Zelda`."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", folder.split("-")[0]).strip() or folder


def _catalog(data: Path) -> dict[str, dict]:
    """The cached catalog by id. Its shape is not documented, so a list, a dict of entries or {"apps": [...]} all work."""
    raw = _read(data / "catalog.json", {})
    if isinstance(raw, dict):
        for key in ("apps", "entries", "items", "data"):
            if isinstance(raw.get(key), (list, dict)):
                raw = raw[key]
                break
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {str(e["id"]): e for e in raw if isinstance(e, dict) and e.get("id")} if isinstance(raw, list) else {}


def apps_folder(data: Path) -> Path:
    moved = _read(data / "apps-folder.json", {})
    return Path(moved["path"]) if isinstance(moved, dict) and moved.get("path") else data / "apps"


def _folder_of(data: Path, install: dict) -> Path | None:
    candidates = []
    if install.get("dir"):
        candidates.append(Path(install["dir"]))
    name = install.get("folder") or ""
    if name:
        apps = apps_folder(data)
        if install.get("group"):
            candidates.append(apps / install["group"] / name)
        candidates.append(apps / name)
    return next((c for c in candidates if c.is_dir()), candidates[0] if candidates else None)


def _executable(folder: Path, install: dict) -> Path | None:
    launch = install.get("launch") if isinstance(install.get("launch"), dict) else {}
    named = [launch.get("executable"), *(install.get("executables") or [])]
    for name in named:
        if isinstance(name, str) and name:
            path = Path(name)
            path = path if path.is_absolute() else folder / path
            if path.is_file():
                return path
    found = list_executables(folder) if folder.is_dir() else []
    return found[0] if found else None


def installed(data: Path) -> list[QuiverGame]:
    """Every game Quiver has installed, in the order it lists them. A record without a folder on disk is left out."""
    if not (data / V4_FILE).is_file() and (data / V3_FILE).is_file():
        return _installed_v3(data)
    return _installed_v4(data)


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _installed_v3(data: Path) -> list[QuiverGame]:
    """Quiver 3.x: `apps.json` lists the games (`folderName`, an absolute `installPath` when it was put elsewhere), `settings.json`
    says where the rest go (`AppsPath`), and the game's own folder holds `version.txt` and `selected_executable.txt`. It starts
    a Windows program on Linux with the user's own command (`LinuxWindowsLaunchCommand`), not with a prefix of its own."""
    listed = _read(data / V3_FILE, {})
    apps = listed.get("apps") if isinstance(listed, dict) else listed  # a bare array is read too
    settings = _read(data / "settings.json", {})
    root = Path(settings.get("AppsPath") or data / "Apps") if isinstance(settings, dict) else data / "Apps"
    names = settings.get("UserAppDisplayNames") if isinstance(settings, dict) else None
    out = []
    for app in apps if isinstance(apps, list) else []:
        if not isinstance(app, dict) or not (app.get("folderName") or app.get("installPath")):
            continue
        folder = Path(app["installPath"]) if app.get("installPath") else root / app["folderName"]
        if not folder.is_dir():
            continue
        qid = str(app.get("repository") or app.get("folderName"))
        chosen = _text(folder / "selected_executable.txt")
        exe = Path(chosen) if chosen else None
        if exe is not None and not exe.is_absolute():
            exe = folder / exe
        if exe is None or not exe.is_file():
            found = list_executables(folder)
            exe = found[0] if found else None
        windows = sys.platform == "win32" or (exe is not None and exe.suffix.lower() in (".exe", ".bat", ".cmd"))
        shown = (names or {}).get(qid) or (names or {}).get(app.get("folderName") or "") if isinstance(names, dict) else None
        game = QuiverGame(
            id=qid,
            name=shown or app.get("customDisplayName") or app.get("name") or _pretty(app.get("folderName") or folder.name),
            folder=folder,
            executable=exe,
            version=_text(folder / "version.txt"),
            cover=str(app.get("appIconUrl") or ""),
            target="windows" if windows else "linux",
        )
        game.keys = known_saves(game)
        out.append(game)
    return out


def _installed_v4(data: Path) -> list[QuiverGame]:
    installs = _read(data / "installs.json", {})
    if not isinstance(installs, dict):
        return []
    library = _read(data / "library.json", [])
    items = {str(i["id"]): i for i in library if isinstance(i, dict) and i.get("id")} if isinstance(library, list) else {}
    catalog = _catalog(data)
    out = []
    for qid in [*items, *(k for k in installs if k not in items)]:
        install = installs.get(qid)
        if not isinstance(install, dict) or install.get("removed") or (items.get(qid) or {}).get("removed"):
            continue
        folder = _folder_of(data, install)
        if folder is None or not folder.is_dir():
            continue
        entry = catalog.get(qid) or {}
        overrides = (items.get(qid) or {}).get("overrides") or {}
        art = entry.get("libraryArt") if isinstance(entry.get("libraryArt"), dict) else {}
        exe = _executable(folder, install)
        windows = sys.platform == "win32" or (exe is not None and exe.suffix.lower() in (".exe", ".bat", ".cmd"))
        game = QuiverGame(
            id=qid,
            name=overrides.get("name") or entry.get("name") or _pretty(install.get("folder") or folder.name),
            folder=folder,
            executable=exe,
            version=str(install.get("version") or ""),
            cover=str(overrides.get("cover") or art.get("capsule") or ""),
            target="windows" if windows else "linux",
            prefix=(data / "proton" / (install.get("folder") or folder.name)) if windows and sys.platform != "win32" else None,
        )
        game.keys = known_saves(game)
        out.append(game)
    return out


# The ports whose saves are known, by a word of their folder or id: (word, keys). A key is where `saves/locations.py` names the
# place; the second list is for a game started with `portable.txt` beside its program (it keeps everything in its own folder).
# Anything not here has no saves set until the user names them, which is the safe side of a folder the launcher rewrites.
PORTS: list[tuple[tuple[str, ...], dict[str, list[str]]]] = [
    (
        ("zelda64recomp",),
        {
            "portable": ["game/saves"],
            "linux": ["xdg-config/Zelda64Recompiled/saves"],
            "windows": ["users/USER/AppData/Local/Zelda64Recompiled/saves"],
        },
    ),
    # Seen on a Linux machine: the game keeps `saves/` (and its logs and ini files, which are not saves) in `~/.local/share/Soulcalibur II`.
    (("soulcalibur2-recomp", "soulcaliburiirecomp"), {"linux": ["xdg-data/Soulcalibur II/saves"]}),
]


def known_saves(game: QuiverGame) -> list[str]:
    """The save places of a port MOG knows, as archive keys; empty for a port it does not."""
    haystack = f"{game.id} {game.folder.name}".lower()
    for words, places in PORTS:
        if any(word in haystack for word in words):
            if "portable" in places and (game.folder / PORTABLE_MARKER).is_file():
                return list(places["portable"])
            return list(places.get(game.target, []))
    return []
