"""What the client did, kept in memory for the Logs tab and, once enabled, in a file (stdlib only).

Everything goes through the `mog` logger: `info`, `warning` and `error` here, and `api.log`/`api.warn`
(which the transfer code already calls). The store keeps the newest records and tells subscribers about
each new one, on the thread that logged it."""

from __future__ import annotations

import logging
import logging.handlers
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LOGGER_NAME = "mog"
CAPACITY = 2000
FILE_MAX_BYTES = 512 * 1024
LEVELS = ("info", "warning", "error")


@dataclass(frozen=True)
class Record:
    time: float
    level: str  # one of LEVELS
    text: str


def level_name(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    return "warning" if levelno >= logging.WARNING else "info"


class Store(logging.Handler):
    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__()
        self._records: deque[Record] = deque(maxlen=capacity)
        self._subscribers: list[Callable[[Record], None]] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = Record(record.created, level_name(record.levelno), record.getMessage())
        with self._lock:
            self._records.append(entry)
            subscribers = list(self._subscribers)
        for notify in subscribers:
            try:
                notify(entry)
            except Exception:  # noqa: BLE001, S110 - a broken listener must never break logging
                pass

    def records(self) -> list[Record]:
        with self._lock:
            return list(self._records)

    def subscribe(self, notify: Callable[[Record], None]) -> Callable[[], None]:
        """Call `notify` with every new record; returns the function that stops it."""
        with self._lock:
            self._subscribers.append(notify)

        def unsubscribe() -> None:
            with self._lock:
                if notify in self._subscribers:
                    self._subscribers.remove(notify)

        return unsubscribe

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


store = Store()
logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(store)


def info(text: str) -> None:
    logger.info(text)


def warning(text: str) -> None:
    logger.warning(text)


def error(text: str) -> None:
    logger.error(text)


def best_effort(what: str, fn, *args, **kwargs):
    """Run something the client can do without (a file it keeps for itself). A full or unwritable disk is logged and
    skipped, so it never keeps the client from starting: someone with a full disk needs it to remove games."""
    try:
        return fn(*args, **kwargs)
    except OSError as e:
        logger.warning(f"{what}: {e}")
        return None


def write_to_file(path: Path) -> None:
    """Also keep the log in a file that stays small (one older copy is kept). A disk that cannot take it leaves the
    log in memory only."""
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=FILE_MAX_BYTES, backupCount=1, encoding="utf-8")
    except OSError as e:
        logger.warning(f"The log is not kept in a file: {e}")
        return
    handler.handleError = lambda _record: None  # a write that fails later (disk full) must not spray the terminal
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def format_time(stamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(stamp))
