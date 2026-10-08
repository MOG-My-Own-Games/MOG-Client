"""Noticing that a game started and has ended, from the process table (Linux).

The game may have been started by anything, so nothing is asked of the launcher: a process whose
command line has a .exe inside the game's install folder is the game (or what starts it). The
prefix it runs in is read from that process's environment, which is how a game started by hand,
in a setup MOG never saw, still gets its saves found. A native game has no .exe: it is a process
whose command line names the install folder or whose program file sits in it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

PROC = Path("/proc")
APPEAR_TIMEOUT = 180.0  # a first start may create the prefix and download a runtime
LINGER = 6.0  # a launcher hands over to the game, and a game to its helpers, with gaps
POLL = 2.0


def _read(path: Path) -> str:
    try:
        return path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _runs_from(entry: Path, needle: str) -> bool:
    """Whether the process's program file is inside the folder (a game started as `./game`)."""
    try:
        return (os.readlink(entry / "exe") + "/").startswith(needle)
    except OSError:
        return False


def running_pids(
    install_dir: Path, proc_root: Path = PROC, exclude: tuple[int, ...] = (), native: bool = False
) -> list[int]:
    """Processes running a .exe from the install folder (Wine writes its paths as `Z:\\home\\...`),
    or, for a native game, anything started from it."""
    needle = str(install_dir).rstrip("/") + "/"
    found = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) in exclude:
            continue
        cmdline = _read(entry / "cmdline").replace("\\", "/")
        if native:
            if needle in cmdline or _runs_from(entry, needle):
                found.append(int(entry.name))
        elif needle in cmdline and ".exe" in cmdline.casefold():
            found.append(int(entry.name))
    return found


def prefix_from_environ(pid: int, proc_root: Path = PROC) -> Path | None:
    """The Wine prefix a process was started with: WINEPREFIX, or Proton's compatdata folder."""
    env = {}
    try:
        raw = (proc_root / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    for item in raw.decode(errors="replace").split("\0"):  # NUL-separated: a value may hold spaces
        key, _, value = item.partition("=")
        if key in ("WINEPREFIX", "STEAM_COMPAT_DATA_PATH") and value:
            env[key] = value
    if "WINEPREFIX" in env:
        return Path(env["WINEPREFIX"])
    if "STEAM_COMPAT_DATA_PATH" in env:
        data = Path(env["STEAM_COMPAT_DATA_PATH"])
        return data / "pfx" if (data / "pfx").is_dir() else data
    return None


def wait_for_game(
    install_dir: Path,
    on_prefix: Callable[[Path], None] | None = None,
    proc_root: Path = PROC,
    appear_timeout: float = APPEAR_TIMEOUT,
    linger: float = LINGER,
    poll: float = POLL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    stop: Callable[[], bool] = lambda: False,
    abort_before_start: Callable[[], bool] = lambda: False,
    native: bool = False,
) -> bool:
    """Block until the game has started and then ended. Returns False when it never started.

    `on_prefix` is called once with the prefix found in the game's environment, while it runs.
    `abort_before_start` ends the wait early while no game process has appeared (a launcher that
    already failed), and is not asked once one has."""
    me = (os.getpid(),)
    waited_from = clock()
    started = False
    gone_since: float | None = None
    reported = False
    while not stop():
        pids = running_pids(install_dir, proc_root, me, native)
        now = clock()
        if pids:
            started, gone_since = True, None
            if on_prefix and not reported:
                for pid in pids:
                    if prefix := prefix_from_environ(pid, proc_root):
                        on_prefix(prefix)
                        reported = True
                        break
        elif started:
            gone_since = now if gone_since is None else gone_since
            if now - gone_since >= linger:
                return True
        elif now - waited_from >= appear_timeout or abort_before_start():
            return False
        sleep(poll)
    return started
