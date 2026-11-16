"""Gamepad to navigation events, with no third-party dependency.

Qt 6 ships no gamepad module, so a small thread reads the pad itself (Linux
joydev, Windows XInput) and emits logical buttons the GUI turns into key
presses. On Steam Deck desktop mode Steam Input may already map the pad to
keys; the thread then just sees nothing extra.
"""

from __future__ import annotations

import ctypes
import glob
import os
import re
import struct
import sys
import threading
import time
from typing import Callable

# Logical buttons handed to the callback.
UP, DOWN, LEFT, RIGHT, ACCEPT, BACK, PAGE_PREV, PAGE_NEXT, MENU, QUIT, REFRESH, SEARCH, TRIGGER_R, TRIGGER_L, ACCOUNT = (
    "up", "down", "left", "right", "accept", "back", "prev", "next", "menu", "quit", "refresh", "search", "trigger_r", "trigger_l", "account"
)

# The right stick as a stream of "scroll:<tilt>" events, tilt from -1 (up) to 1 (down).
SCROLL = "scroll"


def scroll_amount(name: str) -> float | None:
    """The tilt in a scroll event, or None when `name` is a different event."""
    if not name.startswith(f"{SCROLL}:"):
        return None
    try:
        return float(name.split(":", 1)[1])
    except ValueError:
        return None


# Physical buttons, named by position: pad/<family>/<name>.png is whatever that family
# prints there (A, Cross, Nintendo's B...), whatever function the button has here.
SOUTH, EAST, WEST, NORTH = "south", "east", "west", "north"
SHOULDER_L, SHOULDER_R, TRIGGER_R_GLYPH, TRIGGER_L_GLYPH, START, SELECT, DPAD = (
    "shoulder_l", "shoulder_r", "trigger_r", "trigger_l", "start", "select", "dpad"
)
STICK_R = "stick_r"
GLYPHS = (
    SOUTH, EAST, WEST, NORTH, SHOULDER_L, SHOULDER_R, TRIGGER_R_GLYPH, TRIGGER_L_GLYPH, START, SELECT, DPAD, STICK_R
)

# Which physical button triggers each function (the joydev/XInput tables below
# implement the same assignment).
BUTTON_FOR = {
    ACCEPT: SOUTH,
    BACK: EAST,
    REFRESH: WEST,
    SEARCH: NORTH,
    PAGE_PREV: SHOULDER_L,
    PAGE_NEXT: SHOULDER_R,
    MENU: START,
    ACCOUNT: SELECT,
    TRIGGER_R: TRIGGER_R_GLYPH,
    TRIGGER_L: TRIGGER_L_GLYPH,
}

# What an Xbox-style pad numbers (joydev numbers the buttons a pad has in the order of their evdev codes). Start and
# Select go through _Combo, the triggers through _Trigger.
_JS_DEFAULT = {0: ACCEPT, 1: BACK, 2: REFRESH, 3: SEARCH, 4: PAGE_PREV, 5: PAGE_NEXT, 6: ACCOUNT, 7: MENU}
# The same by evdev code. A pad with L2/R2 as buttons (a PlayStation one) numbers them 6 and 7 and puts Select and Start
# after them, so the numbers alone would take a pulled L2 for Select and open the user menu beside the sidebar.
_JS_CODES = {
    0x130: ACCEPT, 0x131: BACK, 0x133: REFRESH, 0x134: SEARCH, 0x136: PAGE_PREV, 0x137: PAGE_NEXT,
    0x138: TRIGGER_L, 0x139: TRIGGER_R, 0x13A: ACCOUNT, 0x13B: MENU,
}
_BTN_MISC, _KEY_MAX = 0x100, 0x2FF
XBOX, PLAYSTATION, NINTENDO, STEAM = "xbox", "playstation", "nintendo", "steam"
_FAMILY_PATTERNS = (
    (STEAM, re.compile(r"steam|valve")),
    (PLAYSTATION, re.compile(r"sony|playstation|dualshock|dualsense|ps[345]|wireless controller")),
    (NINTENDO, re.compile(r"nintendo|pro controller|joy-?con")),
)


def family_of(name: str, deck: bool | None = None) -> str:
    """Controller family from the device name, for picking button glyphs. Steam
    Input hands the app a virtual Xbox pad, which on a Deck is the Deck itself."""
    lowered = name.lower()
    for family, pattern in _FAMILY_PATTERNS:
        if pattern.search(lowered):
            return family
    if deck is None:
        deck = os.environ.get("SteamDeck") == "1"
    return STEAM if deck else XBOX


def _joydev_name(path: str) -> str:
    try:
        with open(f"/sys/class/input/{os.path.basename(path)}/device/name") as f:
            return f.read().strip()
    except OSError:
        return ""


_DEADZONE = 16000
_TRIGGER_ON, _TRIGGER_OFF = 8000, 4000  # pulled past the first, let go below the second: a trigger resting near one does not flutter
_JS_TRIGGER_L_AXIS, _JS_TRIGGER_R_AXIS = 2, 5
_TRIGGER_AXIS = {TRIGGER_L: _JS_TRIGGER_L_AXIS, TRIGGER_R: _JS_TRIGGER_R_AXIS}
_REPEAT_DELAY, _REPEAT_RATE = 0.4, 0.09
_SCROLL_RATE = 0.05
_JS_RIGHT_STICK_Y = 4


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


class _Scroller:
    """The right stick, scrolling: while it is tilted past the dead zone an event with the tilt goes
    out every few milliseconds, so the further it is pushed the faster a list moves."""

    def __init__(self, emit: Callable[[str], None], clock: Callable[[], float] = time.monotonic):
        self.emit, self.clock = emit, clock
        self.tilt = 0.0
        self.due = 0.0

    def set(self, raw: int) -> None:
        self.tilt = 0.0 if abs(raw) < _DEADZONE else max(-1.0, min(1.0, raw / 32767))

    def tick(self) -> None:
        now = self.clock()
        if self.tilt and now >= self.due:
            self.due = now + _SCROLL_RATE
            self.emit(f"{SCROLL}:{self.tilt:.3f}")


def _joydev_table(fd: int, family: str = "xbox") -> dict[int, str]:
    """The function of each button number of this pad, read from the codes the driver gives them (JSIOCGBTNMAP); the Xbox
    numbering when the driver will not say. The Xbox driver calls the left face button BTN_NORTH (it is BTN_X), where the
    PlayStation and Nintendo ones name the buttons by where they are: the top one is BTN_NORTH."""
    try:
        import fcntl

        count = bytearray(1)
        fcntl.ioctl(fd, (2 << 30) | (1 << 16) | (ord("j") << 8) | 0x12, count)  # JSIOCGBUTTONS
        size = (_KEY_MAX - _BTN_MISC + 1) * 2
        raw = bytearray(size)
        fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("j") << 8) | 0x34, raw)  # JSIOCGBTNMAP
        codes = struct.unpack(f"<{size // 2}H", bytes(raw))
    except (ImportError, OSError, struct.error):
        return dict(_JS_DEFAULT)
    functions = dict(_JS_CODES)
    if family in (PLAYSTATION, NINTENDO):
        functions[0x133], functions[0x134] = SEARCH, REFRESH  # top: search, left: refresh, as the glyphs say
    return {number: functions[code] for number, code in enumerate(codes[: count[0]]) if code in functions}


class _Combo:
    """Start+Select quits. Start alone opens the menu and Select alone the user menu, each once released, so
    neither flashes up before the combo completes."""

    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit
        self.start = self.select = False
        self.start_used = self.select_used = False

    def set_start(self, down: bool) -> None:
        if down:
            self.start = True
            if self.select:
                self._quit()
        else:
            if self.start and not self.start_used:
                self.emit(MENU)
            self.start = self.start_used = False

    def set_select(self, down: bool) -> None:
        if down:
            self.select = True
            if self.start:
                self._quit()
        else:
            if self.select and not self.select_used:
                self.emit(ACCOUNT)
            self.select = self.select_used = False

    def _quit(self) -> None:
        if not (self.start_used or self.select_used):
            self.emit(QUIT)
        self.start_used = self.select_used = True


class _Trigger:
    """An analog trigger as a button: fires once per pull."""

    def __init__(self, emit: Callable[[str], None], name: str):
        self.emit, self.name, self.down = emit, name, False

    def set(self, down: bool) -> None:
        if down and not self.down:
            self.emit(self.name)
        self.down = down


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


def _run_joydev(
    emit: Callable[[str], None], stop: threading.Event, on_connection: Callable[[bool, str], None]
) -> None:
    import select

    rep = _Repeater(emit)
    scroller = _Scroller(emit)
    combo = _Combo(emit)
    rest: dict[int, int] = {}  # axis rest values; analog triggers rest at their minimum
    # One state per trigger, whether the pad reports it as an axis, as a button or both: a pull is one press.
    pulls = {TRIGGER_L: _Trigger(emit, TRIGGER_L), TRIGGER_R: _Trigger(emit, TRIGGER_R)}
    triggers = {_JS_TRIGGER_L_AXIS: pulls[TRIGGER_L], _JS_TRIGGER_R_AXIS: pulls[TRIGGER_R]}
    tables: dict[int, dict[int, str]] = {}  # per device: the function of each button number
    connected = False
    sticks, hats = _Axes(rep), _Axes(rep)
    fds: dict[int, str] = {}
    next_scan = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_scan:
            next_scan = time.monotonic() + 3
            for path in glob.glob("/dev/input/js*"):
                if path not in fds.values():
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    except OSError:
                        continue
                    fds[fd] = path
                    tables[fd] = _joydev_table(fd, family_of(_joydev_name(path)))
        if bool(fds) != connected:
            connected = bool(fds)
            on_connection(connected, family_of(_joydev_name(next(iter(fds.values())))) if fds else XBOX)
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
                tables.pop(fd, None)
                continue
            for off in range(0, len(data) - 7, 8):
                _, value, kind, number = struct.unpack_from("<IhBB", data, off)
                if kind & 0x80:  # synthetic init event
                    if kind & 0x7F == 0x02:
                        rest[number] = value
                    continue
                if kind == 0x01:
                    function = tables.get(fd, _JS_DEFAULT).get(number)
                    if function == MENU:
                        combo.set_start(bool(value))
                    elif function == ACCOUNT:
                        combo.set_select(bool(value))
                    elif function in pulls:
                        # A pad that reports the pull as an axis as well is read from the axis only: the button goes down and
                        # up while the axis is still past its threshold, and the two together would press twice.
                        if rest.get(_TRIGGER_AXIS[function], 0) >= -_DEADZONE:
                            pulls[function].set(bool(value))
                    elif value and function:
                        emit(function)
                elif kind == 0x02:
                    if number == 0:
                        sticks.set("x", value, LEFT, RIGHT)
                    elif number == 1:
                        sticks.set("y", value, UP, DOWN)
                    elif number == _JS_RIGHT_STICK_Y:
                        scroller.set(value)
                    elif number == 6:
                        hats.set("x", value, LEFT, RIGHT)
                    elif number == 7:
                        hats.set("y", value, UP, DOWN)
                    elif number in triggers and rest.get(number, 0) < -_DEADZONE:
                        triggers[number].set(value > (_TRIGGER_OFF if triggers[number].down else _TRIGGER_ON))
        rep.tick()
        scroller.tick()


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
    0x0001: UP, 0x0002: DOWN, 0x0004: LEFT, 0x0008: RIGHT,
    0x0100: PAGE_PREV, 0x0200: PAGE_NEXT, 0x1000: ACCEPT, 0x2000: BACK, 0x4000: REFRESH, 0x8000: SEARCH,
}


_XI_START, _XI_BACK = 0x0010, 0x0020


def _run_xinput(
    emit: Callable[[str], None], stop: threading.Event, on_connection: Callable[[bool, str], None]
) -> None:
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
    scroller = _Scroller(emit)
    combo = _Combo(emit)
    previous = 0
    trigger_l, trigger_r = _Trigger(emit, TRIGGER_L), _Trigger(emit, TRIGGER_R)
    connected = False
    state = _XInputState()
    while not stop.is_set():
        present = lib.XInputGetState(0, ctypes.byref(state)) == 0
        if present != connected:
            connected = present
            on_connection(connected, family_of("xinput"))
        if present:
            pressed = state.buttons & ~previous
            released = previous & ~state.buttons
            if pressed & _XI_START:
                combo.set_start(True)
            if released & _XI_START:
                combo.set_start(False)
            if pressed & _XI_BACK:
                combo.set_select(True)
            if released & _XI_BACK:
                combo.set_select(False)
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
            trigger_l.set(state.lt > 128)
            trigger_r.set(state.rt > 128)
            scroller.set(-state.ry)  # XInput's Y grows upwards
        rep.tick()
        scroller.tick()
        stop.wait(0.016)


def start(emit: Callable[[str], None], on_connection: Callable[[bool, str], None]) -> threading.Event:
    """Start the reader thread; set the returned event to stop it. `on_connection`
    is called from that thread with (connected, family) whenever a pad appears or goes away."""
    stop = threading.Event()
    target = _run_xinput if sys.platform == "win32" else _run_joydev if sys.platform.startswith("linux") else None
    if target:
        threading.Thread(target=target, args=(emit, stop, on_connection), daemon=True, name="gamepad").start()
    return stop
