import re
from pathlib import Path

import pytest

from mog_client.gui import gamepad, legend

FAMILIES = (gamepad.XBOX, gamepad.PLAYSTATION, gamepad.NINTENDO, gamepad.STEAM)


def icons(html: str) -> list[str]:
    return re.findall(r'src="([^"]+)"', html)


@pytest.mark.parametrize("context", legend.CONTEXTS)
def test_every_icon_a_page_shows_exists_for_every_controller_and_for_the_keyboard(context):
    for family in (*FAMILIES, None):
        found = icons(legend.render(context, family))
        assert found, f"{context}/{family} shows nothing"
        assert all(Path(path).is_file() for path in found), (context, family)


def test_the_guide_grows_with_the_window_within_limits():
    assert legend.icon_height_for(300) == legend.MIN_ICON
    assert legend.icon_height_for(800) == 40
    assert legend.icon_height_for(5000) == legend.MAX_ICON
    assert legend.icon_height_for(1080) > legend.icon_height_for(720)
    assert legend.font_px_for(40) == 20 and legend.font_px_for(32) == 16 and legend.font_px_for(56) == 28
    assert legend.font_px_for(20) == 12  # never smaller
    small, big = legend.render(legend.LIBRARY, None, 32), legend.render(legend.LIBRARY, None, 56)
    assert 'height="32"' in small and 'height="56"' in big and 'height="32"' not in big


def test_the_guide_stays_on_one_line_by_shrinking_then_dropping_the_least_important_entries():
    ctx = legend.LIBRARY
    assert "<br>" not in legend.render(ctx, None, 56)

    height, entries = legend.fit(ctx, None, 40, 5000)  # plenty of room: as asked, nothing left out
    assert height == 40 and len(entries) == len(legend.entries_for(ctx, None))

    wide = legend.total_width(legend.entries_for(ctx, None), None, 40)
    height, entries = legend.fit(ctx, None, 40, int(wide * 0.8))  # a little short: smaller icons, all entries
    assert legend.COMFORT <= height < 40 and len(entries) == len(legend.entries_for(ctx, None))
    assert legend.total_width(entries, None, height) <= wide * 0.8

    height, entries = legend.fit(ctx, None, 40, 1100)  # a narrow window: the optional entries go, last first
    labels = [e.label for e in entries]
    assert legend.total_width(entries, None, height) <= 1100
    assert "Notifications" not in labels and "Select" in labels and "Quit" in labels and "Settings" in labels
    dropped = [e.label for e in legend.entries_for(ctx, None) if e.label not in labels]
    assert dropped == [e.label for e in legend.entries_for(ctx, None) if e.optional][-len(dropped) :]

    height, entries = legend.fit(ctx, None, 40, 60)  # hopeless: the smallest, core entries only
    assert height == legend.MIN_FIT and not any(e.optional for e in entries)


def test_a_short_guide_stays_big():
    assert legend.fit(legend.MESSAGE, None, 40, 400)[0] == 40


def test_the_right_stick_glyph_exists_for_every_family():
    assert gamepad.STICK_R in gamepad.GLYPHS
    assert all(legend.pad_icon_path(family, gamepad.STICK_R).is_file() for family in FAMILIES)


def test_only_what_the_input_has_is_listed():
    library_pad = [e.label for e in legend.entries_for(legend.LIBRARY, gamepad.XBOX)]
    library_keys = [e.label for e in legend.entries_for(legend.LIBRARY, None)]
    assert "Notifications" in library_keys and "Notifications" not in library_pad  # no button for it
    assert "Sidebar" in library_pad and "Switch focus" in library_pad

    typing_keys = [e.label for e in legend.entries_for(legend.TYPING, None)]
    assert typing_keys == ["Move", "Delete", "Space", "Done", "Cancel"]  # no keyboard equivalent of Type or Cursor


def test_each_page_tells_what_works_on_it():
    assert "Tabs" in [e.label for e in legend.entries_for(legend.SETTINGS, gamepad.XBOX)]
    assert [e.label for e in legend.entries_for(legend.LOGS, gamepad.XBOX)][0] == "Scroll"
    assert "Sidebar" not in [e.label for e in legend.entries_for(legend.PAGE, gamepad.XBOX)]  # library only
    assert [e.label for e in legend.entries_for(legend.MESSAGE, None)] == ["OK"]


def test_chords_are_joined_with_a_plus_and_alternatives_sit_side_by_side():
    keyboard = legend.render(legend.LIBRARY, None)
    assert "keys/ctrl.png" in keyboard and "keys/f.png" in keyboard and "+" in keyboard
    pad = legend.render(legend.LIBRARY, gamepad.PLAYSTATION)
    assert "playstation/start.png" in pad and "playstation/select.png" in pad
    assert "shoulder_l.png" in pad and "shoulder_r.png" in pad


def test_a_message_over_the_window_is_answered_with_one_button():
    assert icons(legend.render(legend.MESSAGE, gamepad.XBOX)) == [str(legend.pad_icon_path(gamepad.XBOX, gamepad.SOUTH).as_posix())]
