"""What to do once a running install has stopped (e.g. delete its partial files after "Cancel local install")."""

from __future__ import annotations

import threading
from collections.abc import Callable


class StopActions:
    def __init__(self) -> None:
        self._pending: dict[int, Callable[[], None]] = {}
        self._lock = threading.Lock()

    def register(self, game_id: int, action: Callable[[], None], running: bool) -> bool:
        """Run `action` when the game's install stops. With no install running there is nothing to
        wait for, and an action left pending would fire at the end of the *next* install, so it runs
        now instead. Returns True if it was left waiting."""
        if not running:
            action()
            return False
        with self._lock:
            self._pending[game_id] = action
        return True

    def take(self, game_id: int) -> Callable[[], None] | None:
        with self._lock:
            return self._pending.pop(game_id, None)

    def forget(self, game_id: int) -> None:
        """A new install begins: nothing requested for an earlier one applies to it."""
        with self._lock:
            self._pending.pop(game_id, None)
