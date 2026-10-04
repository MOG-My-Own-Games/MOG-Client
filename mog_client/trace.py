"""A timestamped trace of what a download is doing, for working out where one stalls."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from mog_client.config import data_dir

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
        path.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {title}\n")


def event(message: str) -> None:
    thread = threading.current_thread().name.replace("ThreadPoolExecutor-", "w")
    line = f"+{time.monotonic() - _start:8.2f}s [{thread}] {message}\n"
    try:
        with _lock, open(trace_path(), "a") as f:
            f.write(line)
    except OSError:
        pass  # a trace must never break a download
