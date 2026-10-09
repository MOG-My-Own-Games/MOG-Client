"""Sending this device's log to the server along with the saves, for the people who turned it on (stdlib only).

A machine out of reach (a Steam Deck) is where things go wrong with no log to read; with `Settings.upload_logs` the
newest part of it is kept on the server, per device, next to the saves."""

from __future__ import annotations

import platform
import threading
import time
from pathlib import Path

from mog_client.config import Settings, data_dir
from mog_client.version import __version__

CLIENT_LOG_BYTES = 400 * 1024
GAME_LOG_BYTES = 100 * 1024
MIN_GAP = 30.0  # seconds between two uploads: a start-up check over many games must not send the log for each

_lock = threading.Lock()
_last_sent = 0.0


def tail(path: Path, limit: int) -> str:
    """The last `limit` bytes of a text file, from a whole line; empty when it is not there."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return ""
    if size > limit:
        data = data[data.find(b"\n") + 1 :]
    return data.decode("utf-8", errors="replace")


def collect(game_id: int | None = None) -> str:
    """The client's log (the older copy first when the newest is short), then the game's own launch log."""
    logs = data_dir() / "logs"
    client = tail(logs / "client.log.1", CLIENT_LOG_BYTES) + tail(logs / "client.log", CLIENT_LOG_BYTES)
    parts = [f"MOG Client {__version__} on {platform.system()} {platform.release()}", "--- client.log ---", client[-CLIENT_LOG_BYTES:]]
    if game_id is not None and (launch := tail(logs / f"launch-{game_id}.log", GAME_LOG_BYTES)):
        parts += [f"--- launch-{game_id}.log ---", launch]
    return "\n".join(parts)


def send(client, device_id: int | None, settings: Settings, game_id: int | None = None, force: bool = False) -> bool:
    """Send the log when the user allowed it. Never raises; True when the server kept it."""
    global _last_sent
    if not settings.upload_logs or device_id is None:
        return False
    with _lock:
        if not force and time.monotonic() - _last_sent < MIN_GAP:
            return False
        _last_sent = time.monotonic()
    try:
        return client.upload_device_log(device_id, collect(game_id))
    except Exception:  # noqa: BLE001 - the log is a courtesy: whatever goes wrong, the save sync carries on
        return False
