"""Layout and text logic of the in-app on-screen keyboard (no Qt, so it is testable)."""

from __future__ import annotations

SHIFT, BACKSPACE, SPACE, DONE, CANCEL = "shift", "backspace", "space", "done", "cancel"

ROWS: list[list[str]] = [
    list("1234567890"),
    list("qwertyuiop"),
    list("asdfghjkl"),
    [SHIFT, *"zxcvbnm", BACKSPACE],
    [*"-_.@/:", SPACE, CANCEL, DONE],
]

LABELS = {SHIFT: "Shift", BACKSPACE: "Del", SPACE: "Space", DONE: "Done", CANCEL: "Cancel"}
WIDE = {SHIFT: 2, BACKSPACE: 2, SPACE: 4, DONE: 2, CANCEL: 2}


class TextBuffer:
    """The text being edited with a cursor; Shift upper-cases the next letter only."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.pos = len(text)
        self.shift = False

    def insert(self, chars: str) -> None:
        self.text = self.text[: self.pos] + chars + self.text[self.pos :]
        self.pos += len(chars)

    def backspace(self) -> None:
        if self.pos:
            self.text = self.text[: self.pos - 1] + self.text[self.pos :]
            self.pos -= 1

    def move(self, delta: int) -> None:
        self.pos = min(max(self.pos + delta, 0), len(self.text))

    def press(self, key: str) -> str | None:
        """Apply a key; returns DONE/CANCEL when the keyboard should close."""
        if key == SHIFT:
            self.shift = not self.shift
        elif key == BACKSPACE:
            self.backspace()
        elif key == SPACE:
            self.insert(" ")
        elif key in (DONE, CANCEL):
            return key
        else:
            self.insert(key.upper() if self.shift else key)
            self.shift = False
        return None


def neighbour(row: int, col: int, d_row: int, d_col: int) -> tuple[int, int]:
    """Key reached from (row, col) by a step; rows wrap at the ends and a vertical
    step keeps the horizontal position by proportion, as rows differ in length."""
    if d_col:
        return row, (col + d_col) % len(ROWS[row])
    new_row = (row + d_row) % len(ROWS)
    ratio = (col + 0.5) / len(ROWS[row])
    return new_row, min(int(ratio * len(ROWS[new_row])), len(ROWS[new_row]) - 1)
