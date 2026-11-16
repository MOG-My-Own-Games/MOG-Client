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


def _pad_with_codes(monkeypatch, codes, family="xbox"):
    """A joydev device that says its buttons carry these evdev codes."""
    import fcntl
    import struct

    def ioctl(fd, request, buf):
        if request & 0xFF == 0x12:  # JSIOCGBUTTONS
            buf[0] = len(codes)
        else:  # JSIOCGBTNMAP
            buf[:] = struct.pack(f"<{len(buf) // 2}H", *codes, *([0] * (len(buf) // 2 - len(codes))))

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    return gamepad._joydev_table(0, family)


def test_a_playstation_pad_has_l2_and_r2_as_buttons_not_select_and_start(monkeypatch):
    # Cross, Circle, Triangle, Square, L1, R1, L2, R2, Share, Options, PS, L3, R3: the order the driver numbers them in
    table = _pad_with_codes(monkeypatch, [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E], gamepad.PLAYSTATION)

    assert table[6] == gamepad.TRIGGER_L and table[7] == gamepad.TRIGGER_R
    assert table[8] == gamepad.ACCOUNT and table[9] == gamepad.MENU
    assert table[0] == gamepad.ACCEPT and table[1] == gamepad.BACK and 10 not in table


def test_an_xbox_pad_keeps_back_and_start_where_they_were(monkeypatch):
    table = _pad_with_codes(monkeypatch, [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E])

    assert table[6] == gamepad.ACCOUNT and table[7] == gamepad.MENU and table[4] == gamepad.PAGE_PREV


def test_a_driver_that_will_not_say_gets_the_xbox_numbering(monkeypatch):
    import fcntl

    def refuse(*_args):
        raise OSError

    monkeypatch.setattr(fcntl, "ioctl", refuse)

    assert gamepad._joydev_table(0) == gamepad._JS_DEFAULT


def test_a_trigger_that_is_both_a_button_and_an_axis_is_one_pull():
    events: list[str] = []
    trigger = gamepad._Trigger(events.append, gamepad.TRIGGER_L)
    for down in (True, True, False, False):  # the axis crosses its threshold and the button goes down, then both let go
        trigger.set(down)
    assert events == [gamepad.TRIGGER_L]


def test_the_top_face_button_is_search_and_the_left_one_refresh_on_every_family(monkeypatch):
    codes = [0x130, 0x131, 0x133, 0x134]  # the driver's order: bottom, right, then the other two
    # xpad names the Xbox X (left) BTN_NORTH; the PlayStation and Nintendo drivers name the top button so
    assert _pad_with_codes(monkeypatch, codes, gamepad.XBOX)[2] == gamepad.REFRESH
    assert _pad_with_codes(monkeypatch, codes, gamepad.XBOX)[3] == gamepad.SEARCH
    for family in (gamepad.PLAYSTATION, gamepad.NINTENDO):
        table = _pad_with_codes(monkeypatch, codes, family)
        assert table[2] == gamepad.SEARCH and table[3] == gamepad.REFRESH
