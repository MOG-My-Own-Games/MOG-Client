from mog_client.gui import keyboard
from mog_client.gui.keyboard import TextBuffer, neighbour


def test_typing_and_backspace():
    buf = TextBuffer("ab")
    for key in ("c", keyboard.BACKSPACE, keyboard.BACKSPACE, "x", keyboard.SPACE, "y"):
        assert buf.press(key) is None
    assert buf.text == "ax y"


def test_shift_applies_to_one_letter():
    buf = TextBuffer()
    buf.press(keyboard.SHIFT)
    buf.press("a")
    buf.press("b")
    assert buf.text == "Ab"


def test_done_and_cancel_are_reported():
    assert TextBuffer().press(keyboard.DONE) == keyboard.DONE
    assert TextBuffer().press(keyboard.CANCEL) == keyboard.CANCEL


def test_navigation_wraps_and_keeps_position():
    assert neighbour(1, 9, 0, 1) == (1, 0)
    assert neighbour(1, 0, 0, -1) == (1, 9)
    assert neighbour(0, 0, -1, 0)[0] == len(keyboard.ROWS) - 1
    row, col = neighbour(1, 9, 1, 0)
    assert row == 2 and col == len(keyboard.ROWS[2]) - 1
