from mog_client.gui import gamepad


def _combo():
    events: list[str] = []
    return gamepad._Combo(events.append), events


def test_start_alone_opens_menu_on_release():
    combo, events = _combo()
    combo.set_start(True)
    assert events == []
    combo.set_start(False)
    assert events == [gamepad.MENU]


def test_start_then_select_quits_without_menu():
    combo, events = _combo()
    combo.set_start(True)
    combo.set_select(True)
    combo.set_select(False)
    combo.set_start(False)
    assert events == [gamepad.QUIT]


def test_select_then_start_quits_without_menu():
    combo, events = _combo()
    combo.set_select(True)
    combo.set_start(True)
    combo.set_start(False)
    assert events == [gamepad.QUIT]


def test_select_alone_opens_the_user_menu_on_release():
    combo, events = _combo()
    combo.set_select(True)
    assert events == []
    combo.set_select(False)
    assert events == [gamepad.ACCOUNT]


def test_the_quit_combo_leaves_neither_the_menu_nor_the_user_menu_behind_and_each_works_again_after():
    combo, events = _combo()
    combo.set_select(True)
    combo.set_start(True)
    combo.set_select(False)
    combo.set_start(False)
    assert events == [gamepad.QUIT]

    combo.set_select(True)
    combo.set_select(False)
    combo.set_start(True)
    combo.set_start(False)
    assert events == [gamepad.QUIT, gamepad.ACCOUNT, gamepad.MENU]


def test_family_from_device_name():
    assert gamepad.family_of("Sony Interactive Entertainment DualSense Wireless Controller", deck=False) == gamepad.PLAYSTATION
    assert gamepad.family_of("Wireless Controller", deck=False) == gamepad.PLAYSTATION
    assert gamepad.family_of("Nintendo Switch Pro Controller", deck=False) == gamepad.NINTENDO
    assert gamepad.family_of("Valve Software Steam Deck Controller", deck=False) == gamepad.STEAM
    assert gamepad.family_of("Microsoft X-Box 360 pad", deck=False) == gamepad.XBOX


def test_virtual_pad_on_a_deck_is_the_deck():
    assert gamepad.family_of("Microsoft X-Box 360 pad", deck=True) == gamepad.STEAM


def test_every_family_has_every_glyph():
    from pathlib import Path

    assets = Path(gamepad.__file__).parent / "assets" / "pad"
    for family in (gamepad.XBOX, gamepad.PLAYSTATION, gamepad.NINTENDO, gamepad.STEAM):
        for button in gamepad.GLYPHS:
            assert (assets / family / f"{button}.png").is_file(), (family, button)


def test_every_function_has_a_glyph():
    assert set(gamepad.BUTTON_FOR.values()) <= set(gamepad.GLYPHS)


def test_trigger_fires_once_per_pull():
    events: list[str] = []
    trigger = gamepad._Trigger(events.append, gamepad.TRIGGER_L)
    for down in (False, True, True, False, True):
        trigger.set(down)
    assert events == [gamepad.TRIGGER_L, gamepad.TRIGGER_L]


def test_the_right_stick_scrolls_faster_the_further_it_is_pushed():
    events, now = [], [0.0]
    scroller = gamepad._Scroller(events.append, clock=lambda: now[0])

    scroller.set(5000)  # inside the dead zone
    scroller.tick()
    assert events == []

    scroller.set(32767)
    scroller.tick()
    scroller.tick()  # too soon after the first
    now[0] = 0.06
    scroller.tick()
    scroller.set(-16384)
    now[0] = 0.12
    scroller.tick()
    assert events == ["scroll:1.000", "scroll:1.000", "scroll:-0.500"]

    scroller.set(0)
    now[0] = 1.0
    scroller.tick()
    assert len(events) == 3


def test_scroll_events_are_told_apart_from_buttons():
    assert gamepad.scroll_amount("scroll:0.500") == 0.5 and gamepad.scroll_amount("scroll:-1.000") == -1.0
    assert gamepad.scroll_amount("up") is None and gamepad.scroll_amount("scroll:x") is None
