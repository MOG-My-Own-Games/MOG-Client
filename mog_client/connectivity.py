"""Whether MOG Server and the internet can be reached (Qt-free).

Three states: `ONLINE`; `NO_SERVER` (the internet answers, the server does not); `NO_INTERNET` (neither does). The user can
also force offline by hand (`set_forced`), as in Steam: then nothing probes for a way out, and only the user leaves it.

Only the GUI turns tracking on (`enable`). The CLI keeps the state at `ONLINE`, so a command run from a terminal behaves as before.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
from collections.abc import Callable

from mog_client import net

ONLINE = "online"
NO_SERVER = "no_server"
NO_INTERNET = "no_internet"

PROBE_TIMEOUT = 3.0
INTERNET_HOST = ("api.github.com", 443)  # where the updates and the Proton builds come from
INTERNET_TTL = 30.0  # seconds an answer to "is the internet there" is trusted by `internet_ok`

_lock = threading.RLock()
_state = ONLINE
_forced = False
_tracking = False
_internet = (0.0, True)  # when it was last looked at, and what was found
_listeners: list[Callable[[str, bool], None]] = []


def enable() -> None:
    """Start following the server's reachability: a request that fails for good marks it unreachable."""
    global _tracking
    _tracking = True


def state() -> str:
    return _state


def forced() -> bool:
    return _forced


def is_offline() -> bool:
    return _state != ONLINE or _forced


def subscribe(listener: Callable[[str, bool], None]) -> None:
    """`listener(state, forced)` runs, on whichever thread changed it, when either changes."""
    _listeners.append(listener)


def _notify(changed: bool) -> None:
    if changed:
        for listener in list(_listeners):
            listener(_state, _forced)


def _set_state(new: str) -> None:
    global _state
    with _lock:
        changed = new != _state
        _state = new
    _notify(changed)


def set_forced(on: bool) -> None:
    """Offline by choice. Leaving it is up to the caller to follow with a `probe`."""
    global _forced
    with _lock:
        changed = on != _forced
        _forced = on
    _notify(changed)


def mark_failure() -> None:
    """A request to the server failed for good (after its retries). Tracking only: the caller probes next to confirm."""
    if _tracking and _state == ONLINE:
        _set_state(NO_SERVER)


def reset() -> None:
    """Back to the starting point (tests)."""
    global _state, _forced, _tracking, _internet
    with _lock:
        _state, _forced, _tracking, _internet = ONLINE, False, False, (0.0, True)
        _listeners.clear()


def _reaches_internet() -> bool:
    try:
        with socket.create_connection(INTERNET_HOST, timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def internet_ok() -> bool:
    """Whether a download from the internet (an update, a Proton build) is worth trying. The answer is remembered for a while."""
    global _internet
    if _state == ONLINE and not _forced:
        return True
    if _state == NO_INTERNET:
        return False
    when, found = _internet
    if time.monotonic() - when < INTERNET_TTL:
        return found
    found = _reaches_internet()
    _internet = (time.monotonic(), found)
    return found


def server_reachable(base: str) -> bool:
    """MOG Server answers its unauthenticated health check, one try, a short wait."""
    from mog_client.api import normalize_base

    try:
        with net.urlopen(normalize_base(base) + "/api/health", timeout=PROBE_TIMEOUT):
            return True
    except urllib.error.HTTPError:
        return True  # it answered, whatever it said
    except Exception:  # a refused or timed-out connection, DNS, TLS
        return False


def probe(base: str) -> str:
    """Look at the server and, if it is out of reach, the internet, and record what was found (unless offline is forced)."""
    global _internet
    if server_reachable(base):
        found = ONLINE
    else:
        up = _reaches_internet()
        _internet = (time.monotonic(), up)
        found = NO_SERVER if up else NO_INTERNET
    if not _forced:
        _set_state(found)
    return found
