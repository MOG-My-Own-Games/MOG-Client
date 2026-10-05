"""The window driven the way the pad and keyboard drive it, on an offscreen display."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from mog_client import config, launcher, logstore  # noqa: E402
from mog_client.config import Settings, load_settings  # noqa: E402
from mog_client.gui import app as gui  # noqa: E402
from mog_client.gui import gamepad  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.STYLE)
    return app


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(launcher, "_known", ["faugus", "umu", "wine"])
    config.save_settings(Settings(base="http://server:5000", user="u", password="p"))
    window = gui.MainWindow(gui.App())
    window.resize(1100, 640)
    window.show()
    yield window
    window.pad_stop.set()
    window.close()


def pump(qapp):
    for _ in range(4):
        qapp.processEvents()


def test_messages_wait_their_turn_and_the_pad_answers_them_without_reaching_the_page(win, qapp):
    page = win.current_page()
    win.message("first", "info")
    win.message("second", "error")
    assert win.overlay.showing and win.overlay.body.text() == "first"
    assert win.legend_context() == "message"

    win.on_pad(gamepad.DOWN)  # swallowed: nothing moves behind the message
    win.on_pad(gamepad.PAGE_NEXT)
    assert win.overlay.body.text() == "first" and win.current_page() is page

    win.on_pad(gamepad.ACCEPT)
    assert win.overlay.body.text() == "second"
    win.on_pad(gamepad.BACK)
    assert not win.overlay.showing and win.legend_context() == "library"
    assert [r.text for r in logstore.store.records()[-2:]] == ["first", "second"]


def test_escape_closes_a_message_instead_of_going_back_a_page(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.message("boom")
    pump(qapp)
    QApplication.sendEvent(win.overlay.ok, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not win.overlay.showing and win.current_page() is page


def test_the_triggers_switch_settings_tabs_and_the_stick_scrolls_the_log(win, qapp):
    for n in range(80):
        logstore.info(f"line {n}")
    win.open_settings()
    page = win.current_page()
    name = lambda: page.tabs.tabText(page.tabs.currentIndex())  # noqa: E731
    assert name() == "General"

    win.on_pad(gamepad.TRIGGER_R)
    win.on_pad(gamepad.TRIGGER_R)
    assert name() == "Logs" and win.legend_context() == "logs"
    pump(qapp)
    bar = page.logview.view.verticalScrollBar()
    end = bar.value()
    win.on_pad("scroll:-1.000")
    assert bar.value() < end
    win.on_pad("scroll:1.000")
    assert bar.value() > end - 60

    win.on_pad(gamepad.TRIGGER_L)
    win.on_pad(gamepad.TRIGGER_L)
    win.on_pad(gamepad.TRIGGER_L)
    assert name() == "About" and win.legend_context() == "settings"  # wraps round


def test_the_log_is_coloured_by_level_filtered_and_follows_new_records(win, qapp):
    logstore.store.clear()
    logstore.info("an info line")
    logstore.warning("a warning line")
    logstore.error("an error line")
    win.open_settings()
    view = win.current_page().logview
    html = view.view.toHtml()
    assert "#e8c547" in html and "#e2574c" in html

    view.filters["info"].setChecked(False)
    shown = view.view.toPlainText()
    assert "an info line" not in shown and "a warning line" in shown and "an error line" in shown

    logstore.error("arrived live")
    pump(qapp)
    assert "arrived live" in view.view.toPlainText()


def test_the_launcher_dropdown_can_be_driven_with_the_pad(win, qapp):
    win.open_settings()
    page = win.current_page()
    combo = page.launcher
    win.activateWindow()
    combo.setFocus()
    pump(qapp)
    assert combo.currentIndex() == 0 and QApplication.focusWidget() is combo

    win.on_pad(gamepad.ACCEPT)
    pump(qapp)
    assert combo.view().isVisible()
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    win.on_pad(gamepad.ACCEPT)
    pump(qapp)
    assert not combo.view().isVisible() and combo.currentData() == "wine"

    win.on_pad(gamepad.ACCEPT)
    pump(qapp)
    win.on_pad(gamepad.BACK)  # B closes the open list, it does not leave the page
    pump(qapp)
    assert not combo.view().isVisible() and win.current_page() is page


def test_saving_the_settings_keeps_what_the_form_does_not_show(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.app.settings.show_sidebar = False
    win.app.settings.launchers = ["wine"]
    page.sounds_box.setChecked(False)
    page.save()
    saved = load_settings()
    assert saved.sounds is False and saved.show_sidebar is False and saved.launchers == ["wine"]


def test_the_rescan_button_refreshes_the_list_of_launchers(win, qapp, monkeypatch):
    monkeypatch.setattr(launcher, "scan_launchers", lambda: ["umu", "wine"])
    win.open_settings()
    page = win.current_page()
    next(b for b in page.findChildren(QPushButton) if b.text() == "Rescan").click()
    assert [page.launcher.itemData(i) for i in range(page.launcher.count())] == ["umu", "wine"]
    assert page.launcher_status.text() == "Found: umu-launcher, Wine (system)"


def test_a_first_start_with_no_server_opens_on_the_server_tab(win, qapp):
    win.app.settings.base = ""
    win.open_settings()
    page = win.current_page()
    assert page.tabs.currentWidget() is page.server


def test_the_arrows_move_past_a_dropdown_and_only_enter_opens_it(win, qapp):
    win.open_settings()
    page = win.current_page()
    combo = page.launcher
    combo.setFocus()
    pump(qapp)
    assert combo.currentIndex() == 0

    QApplication.sendEvent(combo, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    assert combo.currentIndex() == 0 and QApplication.focusWidget() is not combo  # the focus moved on

    combo.setFocus()
    pump(qapp)
    QApplication.sendEvent(combo, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    pump(qapp)
    assert combo.view().isVisible() and combo.currentIndex() == 0
    combo.hidePopup()


def test_a_text_field_lets_the_arrows_carry_on_to_the_next_row(win, qapp):
    win.app.settings.base = ""
    win.open_settings()
    page = win.current_page()
    page.base.setFocus()
    pump(qapp)
    QApplication.sendEvent(page.base, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    assert QApplication.focusWidget() is page.user


def test_the_row_with_the_focus_is_lit_and_a_greyed_option_keeps_its_position(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.activateWindow()
    page.sync_saves_box.setFocus()
    pump(qapp)
    row = page.sync_saves_box.parentWidget()
    assert row.objectName() == "optionRow" and row.property("active") is True
    page.sounds_box.setFocus()
    pump(qapp)
    assert row.property("active") is False

    page.sync_start_box.setChecked(True)
    page.sync_saves_box.setChecked(False)
    assert not page.sync_start_box.isEnabled() and page.sync_start_box.isChecked()  # looks on, does nothing
    page.sync_saves_box.setChecked(True)
    assert page.sync_start_box.isEnabled()
    page.sync_saves_box.setChecked(False)
    page.save()
    assert load_settings().sync_on_start is True and load_settings().sync_saves is False


def test_the_pad_does_nothing_while_this_window_is_not_the_one_in_front(win, qapp, monkeypatch):
    played = []

    class Spy:
        def __init__(self, path, volume):
            self.name = path.name

        def play(self):
            played.append(self.name)

    win.sounds.factory = lambda path, volume: Spy(path, volume)
    win.open_settings()
    page = win.current_page()
    first = page.tabs.currentIndex()

    with monkeypatch.context() as behind_a_game:  # no window of ours is the active one
        behind_a_game.setattr(QApplication, "activeWindow", staticmethod(lambda: None))
        for name in (gamepad.TRIGGER_R, gamepad.DOWN, gamepad.ACCEPT, gamepad.QUIT):
            win.on_pad_event(name)
        pump(qapp)
        assert played == [] and page.tabs.currentIndex() == first and win.isVisible()

    win.activateWindow()
    pump(qapp)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))  # in front again
    win.on_pad_event(gamepad.TRIGGER_R)
    assert page.tabs.currentIndex() != first and played == ["navigate.wav"]


# --- the "Playing" message while a game started from here runs ---


def _game_process(folder):
    import subprocess
    import sys

    ready = folder / "ready"
    ready.unlink(missing_ok=True)
    code = f"import time\nopen({str(ready)!r}, 'w').close()\ntime.sleep(60)"
    game = subprocess.Popen([sys.executable, "-c", code, str(folder / "Game" / "game.exe")], start_new_session=True)
    _wait(lambda: ready.exists())
    return game


def _wait(condition, timeout=8.0, qapp=None):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if qapp is not None:
            qapp.processEvents()
        if condition():
            return True
        time.sleep(0.05)
    return False


def _installed(tmp_path):
    from mog_client.config import InstalledGame

    return InstalledGame(
        game_id=1, name="Jazz Jackrabbit 2", install_dir=str(tmp_path), executable=str(tmp_path / "Game/game.exe"),
        state="installed", save_sync=False,
    )


def test_a_running_game_shows_the_message_and_stop_closes_it(win, qapp, tmp_path):
    from mog_client.saves import watcher

    rec = _installed(tmp_path)
    game = _game_process(tmp_path)
    try:
        win.begin_play(rec, game, poll=0.05, linger=0.2, appear_timeout=10)
        assert win.playing.showing and win.playing.name.text() == "Jazz Jackrabbit 2"
        assert win.legend_context() == "playing"

        win.on_pad(gamepad.DOWN)  # nothing moves behind it
        win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert win.playing.showing and win.current_page() is win.library

        assert _wait(lambda: watcher.running_pids(tmp_path))
        win.on_pad(gamepad.ACCEPT)  # A presses Stop
        assert win.playing.stop_button.text() == "Stopping..."
        assert _wait(lambda: not win.playing.showing, qapp=qapp)
        assert game.poll() is not None and not win.overlay.showing  # it was stopped, which is no failure
        assert win.legend_context() == "library"
    finally:
        game.kill()


def test_a_game_that_closes_by_itself_takes_the_message_down(win, qapp, tmp_path):
    rec = _installed(tmp_path)
    game = _game_process(tmp_path)
    try:
        win.begin_play(rec, game, poll=0.05, linger=0.2, appear_timeout=10)
        _wait(lambda: False, timeout=0.4, qapp=qapp)  # long enough for it to be seen running
        game.kill()
        assert _wait(lambda: not win.playing.showing, qapp=qapp)
        assert not win.overlay.showing  # ending by itself is no failure
    finally:
        game.kill()


def test_a_launcher_that_fails_before_the_game_appears_ends_it_with_the_reason(win, qapp, tmp_path):
    import subprocess
    import sys

    launcher_proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    launcher_proc.wait()
    win.begin_play(_installed(tmp_path), launcher_proc, poll=0.05, linger=0.2, appear_timeout=30)
    assert _wait(lambda: not win.playing.showing, qapp=qapp)
    assert win.overlay.showing and "code 3" in win.overlay.body.text()


def test_stop_before_the_game_ever_appeared_just_closes_the_message(win, qapp, tmp_path):
    import subprocess
    import sys

    never = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        win.begin_play(_installed(tmp_path), never, poll=0.05, linger=0.2, appear_timeout=60)
        win.on_pad(gamepad.ACCEPT)
        assert _wait(lambda: not win.playing.showing, qapp=qapp)
        assert not win.overlay.showing  # stopping is not a failure
    finally:
        never.kill()


def test_a_message_can_be_shown_over_the_playing_one_and_answered_first(win, qapp, tmp_path):
    rec = _installed(tmp_path)
    game = _game_process(tmp_path)
    try:
        win.begin_play(rec, game, poll=0.05, linger=0.2, appear_timeout=10)
        win.message("Something to read", "warning")
        assert win.overlay.showing and win.playing.showing and win.legend_context() == "message"
        win.on_pad(gamepad.ACCEPT)  # answers the message, does not press Stop
        assert not win.overlay.showing and win.playing.showing
        assert win.playing.stop_button.isEnabled() and win.legend_context() == "playing"
    finally:
        game.kill()
        win.stop_playing()
        _wait(lambda: not win.playing.showing, qapp=qapp)


# --- Up and Down through Settings: rows, the tabs, and round to the first row ---


def _press(widget, key):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


def test_up_from_the_first_row_goes_to_the_tabs_and_down_comes_back_in(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.activateWindow()
    page.sync_saves_box.setFocus()
    pump(qapp)
    bar = page.tabs.tabBar()

    _press(page.sync_saves_box, Qt.Key_Up)
    assert QApplication.focusWidget() is bar

    _press(bar, Qt.Key_Down)
    assert QApplication.focusWidget() is page.sync_saves_box


def test_down_walks_the_rows_and_from_the_last_goes_to_the_first(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.activateWindow()
    chain = page._chain()
    assert chain[0] is page.sync_saves_box and chain[-1] is page.save_button
    assert page.sync_start_box in chain and page.launcher in chain and page.games_dir in chain

    seen = []
    page.sync_saves_box.setFocus()
    pump(qapp)
    for _ in range(len(chain)):
        seen.append(QApplication.focusWidget())
        _press(QApplication.focusWidget(), Qt.Key_Down)
    assert seen == chain  # every control once, in order
    assert QApplication.focusWidget() is page.sync_saves_box  # and round again, not out to the tabs


def test_a_greyed_row_is_passed_over(win, qapp):
    win.open_settings()
    page = win.current_page()
    page.sync_saves_box.setChecked(False)
    assert page.sync_start_box not in page._chain()
    page.sync_saves_box.setFocus()
    pump(qapp)
    _press(page.sync_saves_box, Qt.Key_Down)
    assert QApplication.focusWidget() is not page.sync_start_box


def test_the_same_on_the_pad_and_the_tab_bar_switches_tabs_with_left_and_right(win, qapp):
    win.open_settings()
    page = win.current_page()
    win.activateWindow()
    page.sync_saves_box.setFocus()
    pump(qapp)
    win.on_pad(gamepad.UP)
    pump(qapp)
    bar = page.tabs.tabBar()
    assert QApplication.focusWidget() is bar

    _press(bar, Qt.Key_Right)  # a tab at a time, with the focus staying on the bar
    pump(qapp)
    assert page.tabs.tabText(page.tabs.currentIndex()) == "Server" and QApplication.focusWidget() is bar
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    assert QApplication.focusWidget() is page.base


def test_on_the_logs_tab_up_goes_to_the_tabs_and_the_log_scrolls_before_handing_over(win, qapp):
    for n in range(120):
        logstore.info(f"line {n}")
    win.open_settings()
    page = win.current_page()
    win.activateWindow()
    page.tabs.setCurrentWidget(page.logview)
    pump(qapp)
    first = page.logview.focus_targets()[0]
    first.setFocus()
    pump(qapp)
    _press(first, Qt.Key_Up)
    assert QApplication.focusWidget() is page.tabs.tabBar()

    _press(page.tabs.tabBar(), Qt.Key_Down)
    assert QApplication.focusWidget() is first
    _press(first, Qt.Key_Down)
    assert QApplication.focusWidget() is page.logview.view
    bar = page.logview.view.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    assert page.navigate(False) is False and QApplication.focusWidget() is page.logview.view  # mid-log: it scrolls
    bar.setValue(bar.minimum())
    assert page.navigate(False) is True and QApplication.focusWidget() is first  # at the top: back to the buttons


def test_settings_opens_from_the_button_that_had_the_focus(qapp, win):
    """Hiding the focused settings button hands the focus on while the page is shown; the tab bar can
    take it, and must not restyle itself from that focus event (some system styles loop forever)."""
    win.settings_btn.setFocus()
    win.open_settings()
    qapp.processEvents()
    page = win.current_page()
    assert isinstance(page, gui.SettingsPage)
    assert QApplication.focusWidget() is not None


def test_the_focused_tab_bar_outlines_the_selected_tab(qapp, win):
    win.open_settings()
    bar = win.current_page().tabs.tabBar()
    bar.clearFocus()
    qapp.processEvents()
    plain = bar.grab().toImage()
    bar.setFocus()
    qapp.processEvents()
    assert bar.grab().toImage() != plain
    bar.clearFocus()
    qapp.processEvents()
    assert bar.grab().toImage() == plain
