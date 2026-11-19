"""Games that run in a browser, against a small server of their own on this machine (stdlib only).

Such a game keeps its progress in the browser (`localStorage`, for the address the game is served from), which is a
database shared with every other site in the person's own profile: nothing to back up. So MOG keeps a browser profile of
its own inside the game's folder, starts the game with the option that stops it opening the person's browser, and opens
that profile at the game's address itself. What the game stores is then plain files below `<game folder>/mog-browser`, which
the save sync already knows how to take: only the folders of `STORAGE_PLACES`, never the rest of the profile.

Linux only for now: the process watcher reads `/proc`, and on Windows nothing would open the browser."""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mog_client.config import InstalledGame

PROFILE_DIR = "mog-browser"  # the browser profile of the game, in its folder
GAME_KEY = "game"  # what the save sync calls the game's own folder (saves/locations.py)
# What of a Chromium profile is the game's: its localStorage and IndexedDB. Cookies, preferences and caches are the browser's.
STORAGE_PLACES = ("Default/Local Storage", "Default/IndexedDB")
STORAGE_KEYS = tuple(f"{GAME_KEY}/{PROFILE_DIR}/{place}" for place in STORAGE_PLACES)

# Where a launcher says how to start without opening the browser, and the address it serves the game at.
DETECT_SUFFIXES = (".bat", ".cmd", ".sh", ".py", ".json", ".js", ".ps1")
DETECT_DEPTH = 2  # folders below the game's folder that are looked into
DETECT_MAX_FILES = 400
DETECT_MAX_BYTES = 64 * 1024
_ORIGIN = re.compile(r"http://(?:127\.0\.0\.1|localhost):(\d{4,5})(?![\d])")
_NO_OPEN = re.compile(r"--no-(?:browser|open|open-browser|launch-browser|launch)\b")
_SKIPPED_DIRS = frozenset({PROFILE_DIR, "pfx", "pfx.rebuild", "node_modules", "__pycache__", ".git"})

PORT_TIMEOUT = 60.0  # how long the game's server has to answer before the browser is left unopened
CLOSE_GRACE = 10.0  # seconds a browser is given to write its profile and quit before it is killed

CHROMIUM_COMMANDS = (
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser", "brave",
    "microsoft-edge-stable", "microsoft-edge", "vivaldi",
)  # fmt: skip
CHROMIUM_FLATPAKS = ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.Edge", "com.vivaldi.Vivaldi")
_CHROMIUM_WORDS = ("chrom", "brave", "edge", "vivaldi")


@dataclass(frozen=True)
class WebGame:
    origin: str  # `http://127.0.0.1:41765`, no slash at the end
    no_open_arg: str  # what the game's launcher takes to start without opening a browser


def _text_files(root: Path) -> Iterator[Path]:
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        depth = len(here.relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIPPED_DIRS and not (here / d).is_symlink())
        if depth >= DETECT_DEPTH:
            dirnames[:] = []
        for name in sorted(filenames):
            if not name.lower().endswith(DETECT_SUFFIXES):
                continue
            path = here / name
            try:
                if path.is_symlink() or path.stat().st_size > DETECT_MAX_BYTES:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > DETECT_MAX_FILES:
                return
            yield path


def _has_index_page(root: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS and not (here / d).is_symlink()]
        if len(here.relative_to(root).parts) >= DETECT_DEPTH + 1:
            dirnames[:] = []
        if any(name.lower() == "index.html" for name in filenames):
            return True
    return False


def detect(install_dir: Path) -> WebGame | None:
    """Whether the folder holds a game that runs in the browser: a launcher that serves it at an address on this machine with a
    fixed port (a random one would give the page a new storage every time) and takes an option to start without opening the
    browser, and a page (`index.html`) to serve. Both are needed: without the option MOG cannot keep the game out of the
    person's own browser."""
    origins: Counter[str] = Counter()
    options: Counter[str] = Counter()
    for path in _text_files(install_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _ORIGIN.finditer(text):
            if 1024 <= int(match.group(1)) <= 65535:
                origins[match.group(0)] += 1
        for match in _NO_OPEN.finditer(text):
            options[match.group(0)] += 1
    if not origins or not options or not _has_index_page(install_dir):
        return None
    return WebGame(origins.most_common(1)[0][0], options.most_common(1)[0][0])


def apply(rec: InstalledGame, found: WebGame) -> dict:
    """The fields of a game's record that make it a browser game. Its saves are exactly the storage folders of its profile."""
    return {"web_origin": found.origin, "web_no_open_arg": found.no_open_arg, "web_asked": True, "explicit_saves": True}


def is_storage_key(key: str) -> bool:
    """Whether an archive key is one of the storage folders of the profile, or in one."""
    return any(key == place or key.startswith(place + "/") for place in STORAGE_KEYS)


def profile_dir(rec: InstalledGame) -> Path:
    return Path(rec.install_dir) / PROFILE_DIR


# --- the browser -----------------------------------------------------------------------------------------------------


def _default_browser_id() -> str:
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip().removesuffix(".desktop") if out.returncode == 0 else ""


def _flatpak_installed(app_id: str) -> bool:
    for root in (Path.home() / ".local/share/flatpak/app", Path("/var/lib/flatpak/app")):
        if (root / app_id).is_dir():
            return True
    return False


def find_browser(profile: Path, preferred: str | None = None) -> list[str] | None:
    """The command that starts a Chromium-family browser with `profile` as its profile folder, up to the options of the game
    itself, or None when there is none. `preferred` is a program (a name or a path) the person chose. The system's browser
    comes first when it is one of them; a Flatpak app is given the profile folder, which its sandbox could not reach."""
    if sys.platform == "win32":
        return None
    if preferred and (found := shutil.which(preferred)):
        return [found]
    default = _default_browser_id()
    if default and any(word in default.lower() for word in _CHROMIUM_WORDS):
        if found := shutil.which(default):
            return [found]
        if _flatpak_installed(default):
            return ["flatpak", "run", f"--filesystem={profile}", default]
    for name in CHROMIUM_COMMANDS:
        if found := shutil.which(name):
            return [found]
    flatpak = shutil.which("flatpak")
    for app_id in CHROMIUM_FLATPAKS:
        if flatpak and _flatpak_installed(app_id):
            return [flatpak, "run", f"--filesystem={profile}", app_id]
    return None


def browser_command(rec: InstalledGame) -> list[str] | None:
    profile = profile_dir(rec)
    prefix = find_browser(profile, rec.web_browser)
    if prefix is None or not rec.web_origin:
        return None
    return [*prefix, f"--user-data-dir={profile}", f"--app={rec.web_origin}/", "--no-first-run", "--no-default-browser-check"]


def takes_over(rec: InstalledGame) -> bool:
    """Whether the game is started without opening the person's browser, MOG opening its own instead: it is a browser game
    here and there is a browser to open (otherwise the game is left as it is, and opens its own)."""
    return bool(rec.web_origin and rec.web_no_open_arg and browser_command(rec) is not None)


def wait_for_port(origin: str, stop: Callable[[], bool], timeout: float | None = None, poll: float = 0.5) -> bool:
    """Block until something answers at the address of the game's server; False when it never does or `stop` says to give up."""
    match = _ORIGIN.fullmatch(origin)
    if not match:
        return False
    port = int(match.group(1))
    deadline = time.monotonic() + (PORT_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < deadline and not stop():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(poll)
    return False


class Companion:
    """The browser of a browser game while it runs: opened once the game's server answers, closed (and waited for, so the profile is
    on disk) when the game ends. Does nothing for any other game."""

    def __init__(self, rec: InstalledGame, log: Callable[[str], None] | None = None) -> None:
        self.rec = rec
        self.say = log or (lambda _text: None)
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return takes_over(self.rec)

    def start(self) -> None:
        """Open the browser in the background; the game's server may take a while to come up."""
        if not self.active:
            return
        threading.Thread(target=self._open, daemon=True, name="web-game-browser").start()

    def _open(self) -> None:
        from mog_client.launcher import host_environ  # not at the top: the launcher imports this module

        rec = self.rec
        command = browser_command(rec)
        if command is None or not rec.web_origin:
            return
        if not wait_for_port(rec.web_origin, self._stop.is_set):
            if not self._stop.is_set():
                self.say(f"{rec.web_origin} did not answer in {PORT_TIMEOUT:.0f} seconds: the browser was not opened")
            return
        with self._lock:
            if self._stop.is_set():
                return
            profile_dir(rec).mkdir(parents=True, exist_ok=True)
            try:
                self._proc = subprocess.Popen(
                    command, env=host_environ(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                )
            except OSError as e:
                self.say(f"could not open the browser of the game: {e}")
                return
        self.say(f"opened {rec.web_origin} in {Path(command[0]).name}")

    def close(self) -> None:
        """The game has ended: close the browser MOG opened, waiting until it has written its profile."""
        self._stop.set()
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(CLOSE_GRACE)
        except subprocess.TimeoutExpired:
            self.say("the browser of the game did not quit: it was killed")
            proc.kill()
            proc.wait()


# --- putting a backup back -------------------------------------------------------------------------------------------


def drop_stale(install_dir: Path, restored: set[str], backup: Path) -> list[str]:
    """After a restore: the files of a storage folder that the backup did not hold are taken out (copied into the zip `backup`
    first). A leveldb folder is a set of files that belong together, and a log or table left from before could be replayed over
    what was put back. Only folders the backup wrote to are touched. Returns the keys it removed."""
    removed: list[str] = []
    for place in STORAGE_KEYS:
        if not any(key == place or key.startswith(place + "/") for key in restored):
            continue
        folder = install_dir / PurePosixPath(place).relative_to(GAME_KEY)
        if folder.is_symlink() or not folder.is_dir():
            continue
        stale = []
        for dirpath, _dirs, names in os.walk(folder, followlinks=False):
            for name in names:
                path = Path(dirpath, name)
                key = f"{GAME_KEY}/{path.relative_to(install_dir).as_posix()}"
                if key not in restored and not path.is_symlink():
                    stale.append((key, path))
        if not stale:
            continue
        backup.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup, "a", zipfile.ZIP_DEFLATED) as z:
            names_in = set(z.namelist())
            for key, path in stale:
                if key not in names_in:
                    z.write(path, arcname=key)
        for key, path in stale:
            try:
                path.unlink()
                removed.append(key)
            except OSError:
                pass
    return removed
