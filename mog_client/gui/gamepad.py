"""Gamepad to navigation events, with no third-party dependency.

Qt 6 ships no gamepad module, so a small thread reads the pad itself (Linux
joydev, Windows XInput) and emits logical buttons the GUI turns into key
presses. On Steam Deck desktop mode Steam Input may already map the pad to
keys; the thread then just sees nothing extra.
"""

from __future__ import annotations

import ctypes
import glob
import struct
import sys
import threading
import time
from typing import Callable

# Logical buttons handed to the callback.
UP, DOWN, LEFT, RIGHT, ACCEPT, BACK, PAGE_PREV, PAGE_NEXT, MENU = (
    "up", "down", "left", "right", "accept", "back", "prev", "next", "menu"
)

_JS_BUTTONS = {0: ACCEPT, 1: BACK, 3: MENU, 4: PAGE_PREV, 5: PAGE_NEXT, 7: MENU}
_DEADZONE = 16000
_REPEAT_DELAY, _REPEAT_RATE = 0.4, 0.09


class _Repeater:
    """Held directions repeat like a keyboard, so a stick scrolls the grid."""

    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit
        self.held: dict[str, float] = {}

    def press(self, name: str) -> None:
        self.held[name] = time.monotonic() + _REPEAT_DELAY
        self.emit(name)

    def release(self, name: str) -> None:
        self.held.pop(name, None)

    def tick(self) -> None:
        now = time.monotonic()
        for name, due in list(self.held.items()):
            if now >= due:
                self.held[name] = now + _REPEAT_RATE
                self.emit(name)


class _Axes:
    """Turns raw axis values into directional presses/releases."""

    def __init__(self, rep: _Repeater):
        self.rep = rep
        self.state = {"x": 0, "y": 0}

    def set(self, axis: str, value: int, neg: str, pos: str) -> None:
        new = -1 if value < -_DEADZONE else 1 if value > _DEADZONE else 0
        old = self.state[axis]
        if new == old:
            return
        self.state[axis] = new
        for direction, name in ((-1, neg), (1, pos)):
            if old == direction:
                self.rep.release(name)
            if new == direction:
                self.rep.press(name)


def _run_joydev(emit: Callable[[str], None], stop: threading.Event) -> None:
    import os
    import select

    rep = _Repeater(emit)
    sticks, hats = _Axes(rep), _Axes(rep)
    fds: dict[int, str] = {}
    next_scan = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_scan:
            next_scan = time.monotonic() + 3
            for path in glob.glob("/dev/input/js*"):
                if path not in fds.values():
                    try:
                        fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
                    except OSError:
                        pass
        if not fds:
            stop.wait(1.0)
            continue
        ready, _, _ = select.select(list(fds), [], [], 0.03)
        for fd in ready:
            try:
                data = os.read(fd, 8 * 32)
            except OSError:
                os.close(fd)
                del fds[fd]
                continue
            for off in range(0, len(data) - 7, 8):
                _, value, kind, number = struct.unpack_from("<IhBB", data, off)
                if kind & 0x80:  # synthetic init event
                    continue
                if kind == 0x01 and value:
                    name = _JS_BUTTONS.get(number)
                    if name:
                        emit(name)
                elif kind == 0x02:
                    if number == 0:
                        sticks.set("x", value, LEFT, RIGHT)
                    elif number == 1:
                        sticks.set("y", value, UP, DOWN)
                    elif number == 6:
                        hats.set("x", value, LEFT, RIGHT)
                    elif number == 7:
                        hats.set("y", value, UP, DOWN)
        rep.tick()


class _XInputState(ctypes.Structure):
    _fields_ = [
        ("packet", ctypes.c_uint32),
        ("buttons", ctypes.c_uint16),
        ("lt", ctypes.c_uint8),
        ("rt", ctypes.c_uint8),
        ("lx", ctypes.c_int16),
        ("ly", ctypes.c_int16),
        ("rx", ctypes.c_int16),
        ("ry", ctypes.c_int16),
    ]


_XI_BUTTONS = {
    0x0001: UP, 0x0002: DOWN, 0x0004: LEFT, 0x0008: RIGHT, 0x0010: MENU,
    0x0100: PAGE_PREV, 0x0200: PAGE_NEXT, 0x1000: ACCEPT, 0x2000: BACK,
}


def _run_xinput(emit: Callable[[str], None], stop: threading.Event) -> None:
    lib = None
    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            lib = ctypes.WinDLL(dll)  # type: ignore[attr-defined]
            break
        except OSError:
            continue
    if lib is None:
        return
    rep = _Repeater(emit)
    sticks = _Axes(rep)
    previous = 0
    state = _XInputState()
    while not stop.is_set():
        if lib.XInputGetState(0, ctypes.byref(state)) == 0:
            pressed = state.buttons & ~previous
            released = previous & ~state.buttons
            for mask, name in _XI_BUTTONS.items():
                if name in (UP, DOWN, LEFT, RIGHT):
                    if pressed & mask:
                        rep.press(name)
                    if released & mask:
                        rep.release(name)
                elif pressed & mask:
                    emit(name)
            previous = state.buttons
            sticks.set("x", state.lx, LEFT, RIGHT)
            sticks.set("y", -state.ly, UP, DOWN)
        rep.tick()
        stop.wait(0.016)


def start(emit: Callable[[str], None]) -> threading.Event:
    """Start the reader thread; set the returned event to stop it."""
    stop = threading.Event()
    target = _run_xinput if sys.platform == "win32" else _run_joydev if sys.platform.startswith("linux") else None
    if target:
        threading.Thread(target=target, args=(emit, stop), daemon=True, name="gamepad").start()
    return stop
