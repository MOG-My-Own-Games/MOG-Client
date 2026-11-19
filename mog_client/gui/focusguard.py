"""The overlays (message, playing, busy, card) each take the focus back when something behind them gets it. Two of them up at
once took it from each other without end, until Python's recursion limit froze the window (a message over a card, the playing
message over a card). One that is taking the focus keeps the others from answering to it."""

from __future__ import annotations

from collections.abc import Callable

_taking = False


def take(set_focus: Callable[[], None]) -> None:
    """Run `set_focus` (it makes the focus change, which the other overlays hear) unless an overlay is already doing it."""
    global _taking
    if _taking:
        return
    _taking = True
    try:
        set_focus()
    finally:
        _taking = False
