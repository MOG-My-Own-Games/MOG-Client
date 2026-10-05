"""Where a stuck or crashed client was, for a bug report: the Python stack of every thread goes to
`logs/crash.log` when the interface stops answering for a few seconds, or on SIGABRT, SIGSEGV and the like."""

from __future__ import annotations

import faulthandler
import time

from mog_client.config import data_dir

STALL_SECONDS = 5.0
MAX_BYTES = 256 * 1024
_file = None


def crash_path():
    return data_dir() / "logs" / "crash.log"


def install(stall_seconds: float = STALL_SECONDS) -> bool:
    """Start recording; returns False when the file cannot be opened (nothing is lost but the report)."""
    global _file
    path = crash_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            path.unlink()
        _file = open(path, "a", buffering=1)  # noqa: SIM115 - kept open for the life of the process
        _file.write(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        faulthandler.enable(file=_file, all_threads=True)
    except OSError:
        return False
    beat(stall_seconds)
    return True


def beat(stall_seconds: float = STALL_SECONDS) -> None:
    """Called by the interface's own timer: while it keeps being called nothing is written, and when
    it stops for `stall_seconds` the stacks are dumped (again every `stall_seconds` while it stays stuck)."""
    if _file is not None:
        faulthandler.dump_traceback_later(stall_seconds, repeat=True, file=_file)


def stop() -> None:
    global _file
    faulthandler.cancel_dump_traceback_later()
    faulthandler.disable()
    if _file is not None:
        _file.close()
        _file = None
