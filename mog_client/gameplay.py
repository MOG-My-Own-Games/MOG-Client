"""A game this client started: following it from its start to its end, and stopping it (stdlib only)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from mog_client.saves import watcher

STOP_GRACE = 5.0  # seconds a game is given to close before it is killed


def watch(
    install_dir: Path,
    proc: subprocess.Popen | None,
    stopped: Callable[[], bool],
    on_prefix: Callable[[Path], None] | None = None,
    native: bool = False,
    log: Callable[[str], None] | None = None,
    **timing,
) -> bool:
    """Block until the game has run and ended; False when it never started (the launcher failed, or
    `stopped` said to give up before it appeared). Once it is running, `stopped` changes nothing: the
    wait is for it to really end, since a game closing is when it writes its saves. On Windows the
    process started is the game itself."""
    if sys.platform == "win32" and proc is not None:
        if started := timing.get("on_started"):
            started()
        proc.wait()
        return True

    def launcher_failed() -> bool:
        return proc is not None and proc.poll() not in (None, 0)

    return watcher.wait_for_game(
        install_dir,
        on_prefix=on_prefix,
        abort_before_start=lambda: stopped() or launcher_failed(),
        native=native,
        log=log,
        **timing,
    )


def stop_game(
    install_dir: Path,
    proc: subprocess.Popen | None = None,
    proc_root: Path = watcher.PROC,
    grace: float = STOP_GRACE,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    native: bool = False,
) -> list[int]:
    """Close the game: ask its processes to end, and kill what is still there after `grace` seconds.
    Returns the pids it signalled."""
    if sys.platform == "win32":
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return []
    me = (os.getpid(),)
    signalled = watcher.running_pids(install_dir, proc_root, me, native)

    def send(sig: int, pids: list[int]) -> None:
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    send(signal.SIGTERM, signalled)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)  # the launcher was started in a session of its own
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    deadline = clock() + grace
    while clock() < deadline and watcher.running_pids(install_dir, proc_root, me, native):
        sleep(0.2)
    send(signal.SIGKILL, watcher.running_pids(install_dir, proc_root, me, native))
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    return signalled
