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


def test_select_alone_does_nothing():
    combo, events = _combo()
    combo.set_select(True)
    combo.set_select(False)
    assert events == []


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
