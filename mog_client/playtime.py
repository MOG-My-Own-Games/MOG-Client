"""How long each game was played from here, told to the server (stdlib only).

A game's time is from when its first process showed up to when its processes were gone (`saves.watcher.wait_for_game`), so
the wait for it to start, the restore of its saves before and the backup of them after are not in it. Each run is kept in
a file until the server has it, since the machine may be offline when a game closes; the server tells a run by when it began,
so sending one again changes nothing."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

from mog_client import config, connectivity
from mog_client.api import MogClient
from mog_client.saves import devices

MIN_SECONDS = 10  # a game that closed within moments (a crash at start, a launcher) was not played
SESSION_MAX_SECONDS = 24 * 60 * 60  # what the server takes for one run: a longer one is sent as several
RETRY_STATUSES = (401, 403, 408, 429)  # besides a server that cannot be reached and every 5xx: it may be put right
# An older server has no route for it (404, 405) and does not stop being the one asked until it is updated: the run stays, and
# does not hold back the others, for this long. A game the server no longer has is the same answer, and is let go after it.
UNKNOWN_KEEP_SECONDS = 30 * 24 * 60 * 60
UNKNOWN_STATUSES = (404, 405)

_LOCK = threading.RLock()


def outbox_path():
    return config.data_dir() / "playtime-outbox.json"


def _load() -> list[dict]:
    raw = config._read_json(outbox_path(), [])
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _save(entries: list[dict]) -> None:
    config._write_json(outbox_path(), entries)


def known_device_id() -> int | None:
    """This machine's id on the server when it is already known there; the run is told without one otherwise."""
    record = devices.known_device()
    return record.device_id if record is not None else None


def describe(seconds: float | None) -> str:
    """"3 h 20 min", "12 min", "Not played"."""
    minutes = int((seconds or 0) // 60)
    if minutes < 1:
        return "Not played" if not seconds else "Less than a minute"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


def waiting() -> list[dict]:
    """What the server has not taken yet: [{"game_id", "started_at", "seconds"}]."""
    with _LOCK:
        return _load()


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(game_id: int, started_at: float, seconds: float) -> bool:
    """Keep a run to send. False when it was too short to count. A run longer than the server takes is cut in several."""
    seconds = int(seconds)
    if seconds < max(MIN_SECONDS, 1):
        return False
    with _LOCK:
        entries = _load()
        begin = int(started_at)
        while seconds > 0:
            part = min(seconds, SESSION_MAX_SECONDS)
            entry = {"game_id": game_id, "started_at": iso(begin), "seconds": part}
            if entry not in entries:
                entries.append(entry)
            begin += part
            seconds -= part
        _save(entries)
    return True


def _too_old(entry: dict) -> bool:
    try:
        began = datetime.strptime(entry["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return True  # a line nobody can read is no use to keep
    return (datetime.now(timezone.utc) - began).total_seconds() > UNKNOWN_KEEP_SECONDS


def worth_retrying(status: int | None) -> bool:
    return status is None or status >= 500 or status in RETRY_STATUSES


def flush(client: MogClient, device: Callable[[], int | None] = known_device_id, say: Callable[[str], None] = lambda _m: None) -> bool:
    """Send what waits, oldest first. A run goes when the server says 200, or refuses it for good (sending it again would
    not change that, and it would block the ones after it); one the server could not take for now stays, and the rest
    wait behind it. True when nothing is left."""
    with _LOCK:
        if connectivity.is_offline():
            return not _load()
        device_id = None
        left = False
        for entry in _load():
            if device_id is None:
                device_id = device()  # asked once, and only when there is something to send
            status = client.add_playtime(entry["game_id"], entry["started_at"], entry["seconds"], device_id)
            if status in UNKNOWN_STATUSES and not _too_old(entry):
                left = True  # not dropped, not in the way of the next, and not "nothing is left"
                continue
            if status is not None and not worth_retrying(status):
                _save([e for e in _load() if e != entry])
                if status != 200:
                    say(f"The server refused a run of game {entry['game_id']} (HTTP {status}); it was dropped")
            else:
                return False
        return not left


def send_in_background(client: MogClient, device: Callable[[], int | None] = known_device_id, say: Callable[[str], None] = lambda _m: None) -> None:
    """`flush` on a thread of its own: the end of a game is not kept waiting for the server."""

    def work() -> None:
        try:
            flush(client, device, say)
        except Exception as e:  # noqa: BLE001 - what is not sent stays in the file for the next time
            say(f"Could not send the play time: {e}")

    threading.Thread(target=work, daemon=True, name="playtime").start()
