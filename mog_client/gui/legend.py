"""The guide at the bottom of the window: what each button or key does on the page being shown,
drawn with the controller's own button icons or the keyboard's key icons (Kenney Input Prompts, CC0).

Qt-free: it builds the HTML the label shows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mog_client.gui import gamepad

ASSETS = Path(__file__).parent / "assets"
ICON_HEIGHT = 28  # the smallest it is drawn; see icon_height_for
MIN_ICON, MAX_ICON = 32, 56
COMFORT = 28  # icons are shrunk to this before entries are dropped, and below it only if that is not enough
MIN_FIT = 20  # the smallest they are made, to keep the guide on one line
GAP = "&nbsp;&nbsp;&nbsp;&nbsp;"

LIBRARY, PAGE, SETTINGS, LOGS, INBOX, TYPING, MESSAGE, PLAYING, BUSY = (
    "library", "page", "settings", "logs", "inbox", "typing", "message", "playing", "busy"
)  # fmt: skip
CONTEXTS = (LIBRARY, PAGE, SETTINGS, LOGS, INBOX, TYPING, MESSAGE, PLAYING, BUSY)

G = gamepad  # glyph names
STICK_R = G.STICK_R


@dataclass(frozen=True)
class Entry:
    label: str
    pad: tuple[str, ...] = ()  # controller glyphs (file names under pad/<family>/); empty: not on the pad
    keys: tuple[str, ...] = ()  # key icons (file names under keys/); empty: not on the keyboard
    pad_chord: bool = False  # glyphs pressed together, shown with a plus between them
    keys_chord: bool = True
    optional: bool = False  # left out first, last one first, when the guide does not fit on a line


MOVE = Entry("Move", (G.DPAD,), ("arrows",), keys_chord=False)
SELECT = Entry("Select", (G.SOUTH,), ("enter",))
BACK = Entry("Back", (G.EAST,), ("escape",))
QUIT = Entry("Quit", (G.START, G.SELECT), ("ctrl", "q"), pad_chord=True)
TABS = Entry("Tabs", (G.TRIGGER_L_GLYPH, G.TRIGGER_R_GLYPH), ("ctrl", "page_up", "page_down"))

LEGENDS: dict[str, tuple[Entry, ...]] = {
    LIBRARY: (
        MOVE,
        SELECT,
        BACK,
        Entry("Switch focus", (G.SHOULDER_L, G.SHOULDER_R), ("tab",), optional=True),
        Entry("Refresh", (G.WEST,), ("f5",), optional=True),
        Entry("Search", (G.NORTH,), ("ctrl", "f")),
        Entry("Clear search", (G.TRIGGER_R_GLYPH,), ("ctrl", "backspace"), optional=True),
        Entry("Sidebar", (G.TRIGGER_L_GLYPH,), ("ctrl", "b"), optional=True),
        Entry("Notifications", (), ("ctrl", "n"), optional=True),
        Entry("Menu", (G.START,), ("ctrl", "comma")),
        QUIT,
    ),
    PAGE: (
        MOVE,
        SELECT,
        BACK,
        Entry("Switch focus", (G.SHOULDER_L, G.SHOULDER_R), ("tab",), optional=True),
        Entry("Menu", (G.START,), ("ctrl", "comma"), optional=True),
        QUIT,
    ),
    SETTINGS: (MOVE, SELECT, BACK, TABS, QUIT),
    LOGS: (Entry("Scroll", (STICK_R,), ("arrows",), keys_chord=False), TABS, BACK, QUIT),
    INBOX: (MOVE, Entry("Open", (G.SOUTH,), ("enter",)), Entry("Delete", (G.WEST,), ("delete",)), BACK),
    TYPING: (
        MOVE,
        Entry("Type", (G.SOUTH,)),
        Entry("Delete", (G.WEST,), ("backspace",)),
        Entry("Space", (G.NORTH,), ("space",)),
        Entry("Cursor", (G.SHOULDER_L, G.SHOULDER_R)),
        Entry("Done", (G.TRIGGER_R_GLYPH,), ("enter",)),
        Entry("Cancel", (G.EAST,), ("escape",)),
    ),
    MESSAGE: (Entry("OK", (G.SOUTH,), ("enter",)),),
    PLAYING: (Entry("Stop", (G.SOUTH,), ("enter",)),),
    BUSY: (Entry("Cancel", (G.SOUTH,), ("enter",)),),
}


def pad_icon_path(family: str, glyph: str) -> Path:
    return ASSETS / "pad" / family / f"{glyph}.png"


def key_icon_path(key: str) -> Path:
    return ASSETS / "keys" / f"{key}.png"


def icon_height_for(window_height: int) -> int:
    """How big the guide is drawn: it follows the window so it stays readable on a small screen
    and does not take over a large one."""
    return max(MIN_ICON, min(MAX_ICON, window_height // 20))


def font_px_for(icon_height: int) -> int:
    return max(12, round(icon_height * 0.5))


def _img(path: Path, height: int) -> str:
    return f'<img src="{path.as_posix()}" height="{height}" style="vertical-align: middle;">'


def _icons(paths: list[Path], chord: bool, height: int) -> str:
    return ("+" if chord else "").join(_img(p, height) for p in paths)


def entries_for(context: str, family: str | None) -> list[Entry]:
    """The entries shown for a page: those with something on the controller when one is in use,
    those with a key otherwise."""
    return [e for e in LEGENDS[context] if (e.pad if family else e.keys)]


def _entry_width(entry: Entry, family: str | None, height: int) -> float:
    """About how wide an entry is drawn, in pixels: its icons, the plus signs and its label."""
    glyphs = len(entry.pad if family else entry.keys)
    font = font_px_for(height)
    plus = (glyphs - 1) * font * 0.7 if glyphs > 1 and (entry.pad_chord if family else entry.keys_chord) else 0
    return glyphs * height + plus + (len(entry.label) + 1) * font * 0.62


def total_width(entries: list[Entry], family: str | None, height: int) -> float:
    """About how wide the guide is drawn on one line, in pixels."""
    gaps = max(0, len(entries) - 1) * font_px_for(height) * 1.6
    return sum(_entry_width(e, family, height) for e in entries) + gaps


def fit(context: str, family: str | None, wanted: int, max_width: int) -> tuple[int, list[Entry]]:
    """The icon height and the entries that keep the guide on one line in `max_width`: as `wanted` when it
    fits; else the icons are made smaller, then the optional entries are left out (the last first),
    and only then are the icons made smaller still."""
    entries = entries_for(context, family)

    def fits(height: int) -> bool:
        return total_width(entries, family, height) <= max_width

    height = wanted
    while height > min(COMFORT, wanted) and not fits(height):
        height -= 1
    while not fits(height) and any(e.optional for e in entries):
        last = max(i for i, e in enumerate(entries) if e.optional)
        entries = entries[:last] + entries[last + 1 :]
    while height > MIN_FIT and not fits(height):
        height -= 1
    return height, entries


def render(context: str, family: str | None, height: int = MIN_ICON, entries: list[Entry] | None = None) -> str:
    """The HTML for the guide, on one line; `family` is the connected controller's, None for the
    keyboard; `height` is the icons' height in pixels; `entries` are those to show (from `fit`)."""
    parts = []
    for entry in entries if entries is not None else entries_for(context, family):
        if family:
            icons = _icons([pad_icon_path(family, g) for g in entry.pad], entry.pad_chord, height)
        else:
            icons = _icons([key_icon_path(k) for k in entry.keys], entry.keys_chord, height)
        parts.append(f"{icons} {entry.label}")
    return GAP.join(parts)
