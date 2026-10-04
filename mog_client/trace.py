"""A timestamped trace of what downloads do, for working out where one stalls. Each download appends
a section, so the history of recent ones is there when something went wrong earlier."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from mog_client.config import data_dir

MAX_BYTES = 1024 * 1024
_lock = threading.Lock()
_start = time.monotonic()


def trace_path() -> Path:
    return data_dir() / "logs" / "transfer.log"


def start(title: str) -> None:
    """Begin a fresh trace (the previous download's is replaced)."""
    global _start
    _start = time.monotonic()
    path = trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            keep = path.read_bytes()[-MAX_BYTES // 2 :]  # earlier downloads stay, the oldest go
            path.write_bytes(keep[keep.find(b"\n") + 1 :])
        with open(path, "a") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {title}\n")


def event(message: str) -> None:
    thread = threading.current_thread().name.replace("ThreadPoolExecutor-", "w")
    line = f"+{time.monotonic() - _start:8.2f}s [{thread}] {message}\n"
    try:
        with _lock, open(trace_path(), "a") as f:
            f.write(line)
    except OSError:
        pass  # a trace must never break a download
