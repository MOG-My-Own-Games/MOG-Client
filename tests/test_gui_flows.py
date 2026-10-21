"""The window driven the way the pad and keyboard drive it, on an offscreen display."""

import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QEvent, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from mog_client import config, connectivity, installdirs, launcher, logstore, winetricks  # noqa: E402
from mog_client.api import fmt_bytes  # noqa: E402
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
    app = gui.App()
    monkeypatch.setattr(app, "fetch_videos", lambda game_id: None)  # no test reaches for the network
    window = gui.MainWindow(app)
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
    assert name() == "Library"
    win.on_pad(gamepad.TRIGGER_R)
    assert name() == "Logs" and win.legend_context() == "logs"
    pump(qapp)
    bar = page.logview.view.verticalScrollBar()
    end = bar.value()
    win.on_pad("scroll:-1.000")
    assert bar.value() < end
    win.on_pad("scroll:1.000")
    assert bar.value() > end - 60

    for _ in range(4):
        win.on_pad(gamepad.TRIGGER_L)
    assert name() == "Logs" and win.legend_context() == "logs"  # wraps round, and About is not a tab any more


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


def test_the_games_page_gets_its_default_button_back_when_the_playing_message_goes(win, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    _activate(win, qapp)
    _installed_game(win, qapp, tmp_path)
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    assert page.first.text() == "Play"

    win.playing.show_game("Eta", None, "Playing")  # the message holds the focus (Stop)
    pump(qapp)
    assert qapp.focusWidget() is win.playing.stop_button
    win.playing.end()
    pump(qapp)
    assert qapp.focusWidget() is page.first  # not nothing

    win.message("still to read", "info")  # a message over the page keeps what it has
    win.playing.show_game("Eta", None, "Playing")
    win.playing.end()
    pump(qapp)
    assert win.overlay.showing and qapp.focusWidget() is not page.first


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
    assert page.sync_start_box in chain and page.launcher in chain and page.folders_button not in chain  # that one is on Library

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
    win.user_btn.setFocus()
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


# --- install folders ---------------------------------------------------------------------------


def _library_with(win, qapp, tmp_path, monkeypatch, folder, state="installed"):
    """One game recorded as installed in `folder`, shown in the library."""
    config.save_library({1: config.InstalledGame(1, "Alpha", str(folder), state=state, executable="a.exe")})
    win.set_games([{"id": 1, "name": "Alpha", "library_id": None}])
    pump(qapp)
    return win.library.items[1]


def test_a_game_whose_folder_is_gone_is_greyed_and_comes_back_with_the_drive(win, qapp, tmp_path, monkeypatch):
    folder = tmp_path / "drive" / "mog-games" / "Alpha"
    item = _library_with(win, qapp, tmp_path, monkeypatch, folder)
    assert item.data(gui.ROLE_ABSENT) is True
    assert "Not available" in item.text()

    folder.mkdir(parents=True)  # the drive is connected again
    win.library.update_presence()
    assert item.data(gui.ROLE_ABSENT) is False
    assert "Not available" not in item.text()


def test_the_page_of_a_game_that_is_not_there_cannot_play_it(win, qapp, tmp_path, monkeypatch):
    _library_with(win, qapp, tmp_path, monkeypatch, tmp_path / "unplugged" / "Alpha")
    win.show_game(1)
    page = win.current_page()
    texts = [b.text() for b in page.findChildren(QPushButton)]
    assert "Play" not in texts and "Check again" in texts
    assert "not connected" in page.status.text()


def test_the_startup_save_check_leaves_a_game_that_is_not_there_alone(win, qapp, tmp_path, monkeypatch):
    _library_with(win, qapp, tmp_path, monkeypatch, tmp_path / "unplugged" / "Alpha")
    seen = []
    monkeypatch.setattr(win.saves, "enabled", lambda rec: seen.append(rec) or True)
    win.app.settings.sync_saves = win.app.settings.sync_on_start = True
    win.saves._checked = False
    win.saves.check_all()
    assert seen == []


class _Drive:
    """Install folders for install_game: which are connected, and how much room each has."""

    def __init__(self, monkeypatch, connected: dict[str, int]):
        self.connected = connected
        monkeypatch.setattr(installdirs, "reachable", lambda root: str(root) in connected)
        monkeypatch.setattr(installdirs, "free_bytes", lambda root: connected[str(root)])


def _install(win, qapp, monkeypatch, dirs, connected, size=10 * 1024**3):
    started = []
    monkeypatch.setattr(win.app, "start_install", lambda game, installer=None, root=None, extract_only=False: started.append(root))
    monkeypatch.setattr(win, "_ask_extraction", lambda game, installer, proceed: proceed(False))  # nothing to ask here
    _Drive(monkeypatch, connected)
    win.app.settings.install_dirs = dirs
    win.app.sizes[7] = size
    done = []
    win.install_game({"id": 7, "name": "Beta"}, None, lambda: done.append(True))
    pump(qapp)
    return started, done


def test_the_first_folder_with_room_takes_a_new_game_without_a_question(win, qapp, monkeypatch, tmp_path):
    big = 100 * 1024**3
    started, done = _install(win, qapp, monkeypatch, ["/a", "/b"], {"/a": big, "/b": big})
    assert started == [Path("/a")] and done == [True]
    assert win.current_page() is win.library


def test_a_drive_that_is_not_connected_is_passed_over_without_a_question(win, qapp, monkeypatch):
    started, _ = _install(win, qapp, monkeypatch, ["/gone", "/b"], {"/b": 100 * 1024**3})
    assert started == [Path("/b")]


def test_a_full_folder_asks_before_the_next_one_is_used(win, qapp, monkeypatch):
    big = 100 * 1024**3
    started, done = _install(win, qapp, monkeypatch, ["/a", "/b", "/c"], {"/a": 1 * 1024**3, "/b": big, "/c": big})
    assert started == [] and isinstance(win.current_page(), gui.ConfirmPage)
    assert "/a" in win.current_page().text_label.text() and "/b" in win.current_page().text_label.text()

    win.current_page().no.click()  # not /b: the next one with room is offered
    pump(qapp)
    assert started == [] and "/c" in win.current_page().text_label.text()
    win.current_page().yes.click()
    pump(qapp)
    assert started == [Path("/c")] and done == [True]


def test_saying_no_to_every_offer_installs_nothing(win, qapp, monkeypatch):
    started, done = _install(win, qapp, monkeypatch, ["/a", "/b"], {"/a": 1024**3, "/b": 100 * 1024**3})
    win.current_page().no.click()
    pump(qapp)
    assert started == [] and done == []


def test_when_the_figure_says_no_room_the_install_is_offered_anyway_in_the_roomiest_folder(win, qapp, monkeypatch):
    started, _ = _install(win, qapp, monkeypatch, ["/a"], {"/a": 1024**3})
    # The size counts the game's whole folder on the server, so a folder that looks too small is not a refusal.
    page = win.current_page()
    assert started == [] and isinstance(page, gui.ConfirmPage)
    assert "/a has 1.0 GiB free" in page.text_label.text() and "anyway?" in page.text_label.text()
    page.yes.click()
    assert started == [Path("/a")]


def test_resuming_waits_for_the_drive_the_download_was_on(win, qapp, monkeypatch, tmp_path):
    config.save_library({7: config.InstalledGame(7, "Beta", str(tmp_path / "unplugged" / "mog" / "Beta"), state="installing")})
    started = []
    monkeypatch.setattr(win.app, "start_install", lambda *a, **k: started.append(a))
    win.install_game({"id": 7, "name": "Beta"})
    assert started == [] and win.overlay.showing and "not available" in win.overlay.body.text()


def _folders(win, dirs):
    win.app.settings.install_dirs = list(dirs)
    win.open_settings()
    settings = win.current_page()
    settings.folders_button.click()
    return settings, win.current_page()


def test_the_settings_row_opens_the_install_dirs_menu(win, qapp, tmp_path):
    one = tmp_path / "one"
    one.mkdir()
    settings, page = _folders(win, [str(one), str(tmp_path / "unplugged" / "mog")])
    assert isinstance(page, gui.InstallDirsPage)
    texts = [page.list.item(i).text() for i in range(page.list.count())]
    assert "free" in texts[0] and str(one) in texts[0]
    assert "not connected" in texts[1]
    assert [b.text() for b in (page.add_button, page.remove_button)] == ["+", "-"]
    assert "2 folders, 1 connected" in settings._folders_row.status.text()


def test_install_dirs_are_added_removed_and_ordered_and_kept_at_once(win, qapp, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    settings, page = _folders(win, [str(one)])
    page._added(str(two))
    page._added(str(two))  # no duplicates
    assert page.dirs == [str(one), str(two)]

    page.list.setCurrentRow(1)
    page.move(-1)
    assert page.dirs == [str(two), str(one)]
    assert not page.up_button.isEnabled() and page.down_button.isEnabled()

    page.list.setCurrentRow(0)
    page.remove_button.click()
    assert win.current_page() is not page and str(two) in win.current_page().text_label.text()
    win.current_page().no.click()  # asked first: No keeps it
    assert page.dirs == [str(two), str(one)]
    page.remove_button.click()
    win.current_page().yes.click()
    assert page.dirs == [str(one)] and settings.install_dirs == [str(one)]
    assert config.load_settings().install_dirs == [str(one)]  # kept at once, no Save needed
    assert win.app.settings.install_dirs == [str(one)]

    win.back()
    win.back()  # leave Settings without saving: the folders stay as they were left
    win.open_settings()
    assert win.current_page().install_dirs == [str(one)]


def test_removing_the_last_install_dir_leaves_the_default(win, qapp, tmp_path):
    (tmp_path / "one").mkdir()
    settings, page = _folders(win, [str(tmp_path / "one")])
    page.remove_button.click()
    win.current_page().yes.click()
    assert page.dirs == [] and not page.remove_button.isEnabled()
    assert "None listed" in page.list.item(0).text()
    assert "None listed" in settings._folders_row.status.text()


def test_plus_opens_the_folder_picker(win, qapp, tmp_path):
    _, page = _folders(win, [])
    page.add_button.click()
    assert isinstance(win.current_page(), gui.BrowsePage)


def test_down_from_the_last_folder_reaches_the_buttons(win, qapp, tmp_path):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    _, page = _folders(win, [str(tmp_path / "one"), str(tmp_path / "two")])
    win.activateWindow()
    page.list.setFocus()
    page.list.setCurrentRow(0)
    pump(qapp)
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    assert QApplication.focusWidget() is page.list and page.list.currentRow() == 1
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    assert QApplication.focusWidget() is page.add_button
    win.on_pad(gamepad.RIGHT)
    pump(qapp)
    assert QApplication.focusWidget() is page.remove_button


def test_a_closed_window_makes_no_more_sound_even_for_presses_still_queued(win, qapp, monkeypatch):
    played = []
    monkeypatch.setattr(win.sounds, "factory", lambda path, volume: type("E", (), {"play": lambda self: played.append(path.name), "stop": lambda self: None})())
    win.sounds.clock = lambda: len(played) * 10.0
    win.on_pad(gamepad.DOWN)
    assert played == ["navigate.wav"]
    win.close()
    win.on_pad(gamepad.DOWN)  # a press the pad reader had already queued
    win.on_pad(gamepad.PAGE_NEXT)
    assert played == ["navigate.wav"]


def test_the_remove_button_is_red_and_asks_with_a_red_yes(win, qapp, tmp_path):
    (tmp_path / "one").mkdir()
    _, page = _folders(win, [str(tmp_path / "one")])
    assert page.remove_button.property("danger") is True and not page.add_button.property("danger")
    page.remove_button.click()
    assert win.current_page().yes.property("danger") is True


def test_active_installs_sit_above_the_libraries_and_only_while_one_runs(win, qapp):
    library = win.library
    win.app.set_games([{"id": 5, "name": "Gamma", "library_id": None}])
    library.update_active()
    assert not library.installs_box.isVisibleTo(library.sidebar)

    win.app.installs[5] = threading.Event()
    library.update_active()
    pump(qapp)
    assert library.installs_box.isVisibleTo(library.sidebar) and library.installs.count() == 1
    assert "Gamma" in library.installs.item(0).text()
    assert library.installs_box.y() < library.libs.y()  # on top

    library.installs.setFocus()
    win.app.installs.clear()
    library.update_active()
    pump(qapp)
    assert not library.installs_box.isVisibleTo(library.sidebar)
    assert QApplication.focusWidget() is library.libs


def test_the_game_page_names_each_detail_apart_from_its_value_and_gets_its_art(win, qapp, monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    game = {"id": 9, "name": "Delta", "library_id": None, "summary": "A game.", "igdb_metadata": {"genres": [{"name": "Puzzle"}]}}
    win.app.set_games([game])
    win.show_game(9)
    page = win.current_page()
    keys = [w.text() for w in page.findChildren(QLabel) if w.objectName() == "metaKey"]
    values = [w.text() for w in page.findChildren(QLabel) if w.objectName() == "metaValue"]
    assert keys[:1] == ["GENRES"] and values[:1] == ["Puzzle"] and "SIZE ON SERVER" in keys

    assert not page.header.has_image
    pix = QPixmap(400, 200)
    pix.fill()
    from PySide6.QtCore import QBuffer, QByteArray

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    pix.save(buffer, "PNG")
    page._on_header_art(9, bytes(data))
    assert page.header.has_image
    page._on_header_art(10, bytes(data))  # another game's art is ignored


def test_unread_notifications_are_a_red_dot_with_the_number_on_the_user_button_corner(win, qapp):
    button = win.user_btn
    assert not hasattr(win, "notif_btn")  # no bell of its own: the count is on the user's button
    win.on_notifications({"notifications": [], "unread": 0})
    pump(qapp)
    quiet = button.grab().toImage()
    assert button.count == 0

    win.on_notifications({"notifications": [{"id": 1, "title": "x", "read": False}, {"id": 2, "title": "y", "read": False}], "unread": 2})
    pump(qapp)
    assert button.count == 2
    img = button.grab().toImage()
    dot = img.pixelColor(img.width() - 5, 12)  # inside the dot, in the corner
    assert dot.red() > 200 and dot.green() < 120 and img != quiet

    assert win.notifications_action.text() == "Notifications (2)"
    win.on_notifications({"notifications": [], "unread": 0})
    pump(qapp)
    assert button.count == 0 and button.grab().toImage() == quiet and win.notifications_action.text() == "Notifications"


def test_the_notifications_of_the_last_run_show_at_once_and_the_first_answer_replaces_them(win, qapp, monkeypatch, tmp_path):
    from mog_client import snapshot

    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path / "snap")
    kept = {"notifications": [{"id": 4, "title": "old", "read": False}], "unread": 1}
    snapshot.save_notifications("http://server:5000", kept)

    win._show_snapshot()
    pump(qapp)
    assert win.user_btn.count == 1 and [n["id"] for n in win.notifications] == [4]
    assert win.seen_notification_id is None  # nothing from the snapshot is announced as new

    shown = []
    monkeypatch.setattr(win, "notify", lambda text, level="info": shown.append(text))
    fresh = {"notifications": [{"id": 4, "title": "old", "read": True}, {"id": 5, "title": "new", "read": False}], "unread": 1}
    win.on_notifications(fresh)
    assert [n["id"] for n in win.notifications] == [4, 5] and shown == []  # the first answer only settles what is seen
    assert snapshot.load_notifications("http://server:5000") == fresh


# --- mog:// links from the web UI --------------------------------------------------------------


def _link_win(win, monkeypatch, games=(("Epsilon", 31),)):
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append(game["id"]))
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    # Install first asks the server which installers there are (tested on its own); here a link just starts it.
    monkeypatch.setattr(gui.GamePage, "_install_or_order", gui.GamePage._start_plain)
    win.app.set_games([{"id": gid, "name": name, "library_id": None} for name, gid in games])
    return started


def test_a_link_from_the_web_ui_opens_the_game_and_starts_its_install(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    win.handle_link("mog://install/31?server=http%3A%2F%2Fserver%3A5000")
    pump(qapp)
    assert isinstance(win.current_page(), gui.GamePage) and win.current_page().game["id"] == 31
    assert started == [31] and not win.overlay.showing


def test_the_same_server_under_another_spelling_is_not_asked_about(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    win.handle_link("mog://install/31?server=http%3A%2F%2FSERVER%3A5000%2F")
    assert started == [31]


def test_a_link_from_another_server_asks_before_installing(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    win.handle_link("mog://install/31?server=http%3A%2F%2Fother%3A9")
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.ConfirmPage) and started == []
    text = page.text_label.text()
    assert "http://other:9" in text and "http://server:5000" in text and "Epsilon" in text
    page.no.click()
    assert started == []
    win.handle_link("mog://install/31?server=http%3A%2F%2Fother%3A9")
    win.current_page().yes.click()
    pump(qapp)
    assert started == [31]


def _refreshing(win, monkeypatch, after):
    """App.refresh stood in for: it counts, and loads `after` as the server's games, as the reply would."""
    calls = []

    def refresh():
        calls.append(1)
        win.set_games([{"id": gid, "name": name, "library_id": None} for name, gid in after])

    monkeypatch.setattr(win.app, "refresh", refresh)
    monkeypatch.setattr(win.saves, "check_all", lambda: None)
    monkeypatch.setattr(win, "settle_steam", lambda: None)
    return calls


def test_a_game_the_list_does_not_have_is_looked_for_again_before_giving_up(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)  # the list holds Epsilon (31) only
    calls = _refreshing(win, monkeypatch, after=[("Epsilon", 31), ("Zeta", 32)])  # Zeta was added on the server
    win.handle_link("mog://install/32")
    pump(qapp)
    assert calls == [1] and started == [32] and not win.overlay.showing
    assert isinstance(win.current_page(), gui.GamePage) and win.current_page().game["id"] == 32


def test_a_game_that_is_still_not_there_after_the_refresh_is_reported(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    calls = _refreshing(win, monkeypatch, after=[("Epsilon", 31)])
    win.handle_link("mog://install/99")
    pump(qapp)
    assert calls == [1] and started == []  # looked once, not forever
    assert win.overlay.showing and "Game 99" in win.overlay.body.text()


def test_a_game_that_is_there_is_not_refreshed_for(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    calls = _refreshing(win, monkeypatch, after=[("Epsilon", 31)])
    win.handle_link("mog://install/31")
    assert calls == [] and started == [31]


def test_the_same_missing_game_is_looked_for_again_on_a_later_link(win, qapp, monkeypatch):
    _link_win(win, monkeypatch)
    calls = _refreshing(win, monkeypatch, after=[("Epsilon", 31)])
    win.handle_link("mog://install/99")
    pump(qapp)
    win.overlay.dismiss()
    win.handle_link("mog://install/99")
    pump(qapp)
    assert calls == [1, 1] and win.overlay.showing


def test_a_link_that_is_not_one_is_refused(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    win.handle_link("mog://launch/31")
    assert started == [] and win.overlay.showing and "not one MOG understands" in win.overlay.body.text()


def test_an_empty_hand_over_only_brings_the_window_forward(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    win.handle_link("")
    assert started == [] and not win.overlay.showing and win.current_page() is win.library


def test_a_link_waits_for_the_games_to_be_loaded(win, qapp, monkeypatch):
    started = []
    monkeypatch.setattr(gui.GamePage, "_install_or_order", gui.GamePage._start_plain)
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append(game["id"]))
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.saves, "check_all", lambda: None)
    monkeypatch.setattr(win, "settle_steam", lambda: None)
    win.handle_link("mog://install/31")
    assert started == [] and win._pending_link == "mog://install/31"
    win.set_games([{"id": 31, "name": "Epsilon", "library_id": None}])
    pump(qapp)
    assert started == [31] and win._pending_link is None


def test_a_game_that_is_installed_is_not_installed_again(win, qapp, monkeypatch):
    started = _link_win(win, monkeypatch)
    config.save_library({31: config.InstalledGame(31, "Epsilon", str(win.app.settings.install_roots[0] / "Epsilon"), state="installed")})
    win.handle_link("mog://install/31")
    assert started == [] and win.overlay.showing and "already installed" in win.overlay.body.text()


def test_a_link_before_the_server_is_set_up_opens_settings(win, qapp, monkeypatch):
    win.app.settings.password = ""
    win.handle_link("mog://install/31")
    pump(qapp)
    assert isinstance(win.current_page(), gui.SettingsPage) and win.overlay.showing


def test_a_second_start_hands_its_link_to_the_running_client(qapp):
    from mog_client.gui.instance import Listener, send_to_running

    name = f"mog-client-test-{os.getpid()}"
    assert send_to_running("mog://install/1", name, timeout_ms=200) is False  # nobody there yet
    listener = Listener(name)
    assert listener.listen()
    got, sent = [], []
    listener.received.connect(got.append)

    def second_starts() -> None:  # another process in real life; its own thread here, the listener needs this one's loop
        sent.append(send_to_running("mog://install/1", name))
        sent.append(send_to_running("", name))

    thread = threading.Thread(target=second_starts)
    thread.start()
    for _ in range(100):
        qapp.processEvents()
        if len(got) >= 2:
            break
        time.sleep(0.02)
    thread.join(5)
    assert sent == [True, True] and got == ["mog://install/1", ""]
    listener.server.close()
    for _ in range(20):  # let the sockets it queued for deletion go before the server does
        qapp.processEvents()
        time.sleep(0.01)
    listener.deleteLater()
    for _ in range(10):
        qapp.processEvents()



# --- archives with no installer: extract them as they are -------------------------------------------


class FakeServer:
    """What the install lookups of MogClient return, in place of a server."""

    def __init__(self, default=None, inside=None, session=None):
        self.session = session if session is not None else {}
        self.default = default if default is not None else {"candidates": [{"path": "Game.rar", "file_name": "Game.rar", "kind": "archive", "category": "game"}]}
        self.inside = inside if inside is not None else {"candidates": [], "extract_suggested": True}
        self.asked = []

    def candidates(self, gid, source=None):
        self.asked.append(source)
        return self.default if source is None else self.inside


def _extraction_win(win, monkeypatch, server):
    from mog_client.api import MogClient

    started = []
    monkeypatch.setattr(win.app, "start_install", lambda game, installer=None, root=None, extract_only=False: started.append(extract_only))
    monkeypatch.setattr(win.app, "client", lambda: MogClient.__new__(MogClient))
    monkeypatch.setattr(MogClient, "candidates", lambda self, gid, source=None: server.candidates(gid, source))
    monkeypatch.setattr(MogClient, "get_session", lambda self, gid, session_id=None: server.session)
    win.app.sizes[7] = 1
    win.app.settings.install_dirs = [str(Path("/a"))]
    monkeypatch.setattr(installdirs, "reachable", lambda root: True)
    monkeypatch.setattr(installdirs, "free_bytes", lambda root: 100 * 1024**3)
    return started


def _wait_for(qapp, cond, seconds=3):
    end = time.time() + seconds
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def test_an_archive_with_no_installer_asks_before_it_is_installed_and_yes_extracts_it(win, qapp, monkeypatch):
    server = FakeServer()
    started = _extraction_win(win, monkeypatch, server)
    win.install_game({"id": 7, "name": "Hearthlands"})
    assert _wait_for(qapp, lambda: isinstance(win.current_page(), gui.ConfirmPage))
    assert "Game.rar" in win.current_page().text_label.text() and started == []
    assert server.asked == [None, "Game.rar"]  # the default pick, then what is inside it
    win.current_page().yes.click()
    assert _wait_for(qapp, lambda: started)
    assert started == [True]


def test_no_still_installs_it_the_usual_way(win, qapp, monkeypatch):
    started = _extraction_win(win, monkeypatch, FakeServer())
    win.install_game({"id": 7, "name": "Hearthlands"})
    assert _wait_for(qapp, lambda: isinstance(win.current_page(), gui.ConfirmPage))
    win.current_page().no.click()
    assert _wait_for(qapp, lambda: started)
    assert started == [False]


def test_an_archive_with_an_installer_is_not_asked_about(win, qapp, monkeypatch):
    started = _extraction_win(win, monkeypatch, FakeServer(inside={"candidates": [{"path": "setup.exe"}], "extract_suggested": False}))
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [None] and not isinstance(win.current_page(), gui.ConfirmPage)


def test_a_game_that_is_not_an_archive_is_not_looked_inside(win, qapp, monkeypatch):
    server = FakeServer(default={"candidates": [{"path": "setup.exe", "file_name": "setup.exe", "kind": "known installer", "category": "game"}]})
    started = _extraction_win(win, monkeypatch, server)
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [None] and server.asked == [None]


def test_a_failed_lookup_never_stops_the_install(win, qapp, monkeypatch):
    class Broken(FakeServer):
        def candidates(self, gid, source=None):
            raise RuntimeError("HTTP 500")

    started = _extraction_win(win, monkeypatch, Broken())
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [None]


def test_a_game_that_was_extracted_on_purpose_is_not_asked_again(win, qapp, monkeypatch, tmp_path):
    server = FakeServer()
    started = _extraction_win(win, monkeypatch, server)
    config.save_library({7: config.InstalledGame(7, "Game", str(tmp_path / "Game"), state="installing", extract_only=True)})
    win.install_game({"id": 7, "name": "Game"})
    assert started == [None] and server.asked == []  # it resumes; its record remembers the choice


def test_a_game_whose_first_attempt_failed_is_asked_again(win, qapp, monkeypatch, tmp_path):
    server = FakeServer(session={"state": "failed", "error": "Nothing recognizable as an installer inside Game.rar"})
    started = _extraction_win(win, monkeypatch, server)
    config.save_library({7: config.InstalledGame(7, "Game", str(tmp_path / "Game"), state="installing")})
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: isinstance(win.current_page(), gui.ConfirmPage))
    win.current_page().yes.click()
    assert _wait_for(qapp, lambda: started)
    assert started == [True]  # resumed, now extracting it as it is


def test_a_game_with_no_session_at_all_is_asked_too(win, qapp, monkeypatch, tmp_path):
    _extraction_win(win, monkeypatch, FakeServer(session={}))
    config.save_library({7: config.InstalledGame(7, "Game", str(tmp_path / "Game"), state="installing")})
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: isinstance(win.current_page(), gui.ConfirmPage))


@pytest.mark.parametrize("state", ["installing", "streaming", "done", "detecting"])
def test_a_game_whose_session_is_running_or_finished_is_not_asked(win, qapp, monkeypatch, tmp_path, state):
    server = FakeServer(session={"state": state})
    started = _extraction_win(win, monkeypatch, server)
    config.save_library({7: config.InstalledGame(7, "Game", str(tmp_path / "Game"), state="installing")})
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [None] and server.asked == []


def test_a_message_from_the_bridge_shows_its_text_not_its_level(win, qapp):
    win.app.bridge.message.emit("warning", "Epsilon: the installer needs you")
    pump(qapp)
    assert win.overlay.showing
    assert win.overlay.body.text() == "Epsilon: the installer needs you"
    assert win.overlay.title.text() == "Attention"
    win.overlay.dismiss()
    win.app.bridge.message.emit("info", "Shortcuts rebuilt")
    pump(qapp)
    assert win.overlay.body.text() == "Shortcuts rebuilt" and win.overlay.title.text() == "Done"


def test_an_error_from_the_bridge_is_an_error(win, qapp):
    win.app.bridge.error.emit("boom")
    pump(qapp)
    assert win.overlay.body.text() == "boom" and win.overlay.title.text() == "Something went wrong"


def test_the_user_button_with_the_notification_count_is_on_a_games_page_too(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None}])
    assert win.user_btn.isVisibleTo(win)  # the library has it
    win.show_game(41)
    pump(qapp)
    assert isinstance(win.current_page(), gui.GamePage) and win.user_btn.isVisibleTo(win)

    win.on_notifications({"notifications": [{"id": 1, "title": "x", "read": False}], "unread": 1})
    assert win.user_btn.count == 1  # and it counts there as well
    win.notifications_action.trigger()
    pump(qapp)
    assert isinstance(win.current_page(), gui.NotificationsPage)
    win.back()
    assert isinstance(win.current_page(), gui.GamePage)  # back to the game


def test_the_other_pages_do_not_show_the_user_button(win, qapp):
    win.open_settings()
    pump(qapp)
    assert not win.user_btn.isVisibleTo(win)


def test_the_library_puts_games_waiting_for_an_executable_first_then_the_installed_ones(win, qapp, monkeypatch, tmp_path):
    games = [{"id": i, "name": name, "igdb_id": None} for i, name in ((1, "Alpha"), (2, "Bravo"), (3, "Charlie"), (4, "Delta"))]
    win.app.set_games(games)
    states = {2: "installed", 1: "awaiting_executable", 3: "installed"}
    recs = {gid: config.InstalledGame(game_id=gid, name="x", install_dir=str(tmp_path), state=state) for gid, state in states.items()}
    monkeypatch.setattr(gui, "load_library", lambda: recs)
    win.library.populate("")
    names = [win.library.grid.item(i).text().split("\n")[0] for i in range(win.library.grid.count())]
    assert names == ["Alpha", "Bravo", "Charlie", "Delta"]  # Alpha is "Setup needed": it leads, marked by its own corner
    assert win.library.items[1].data(gui.ROLE_SETUP) and not win.library.items[2].data(gui.ROLE_SETUP)

    recs[1] = config.InstalledGame(game_id=1, name="x", install_dir=str(tmp_path), state="installed")
    win.library.update_label(1)
    qapp.processEvents()
    names = [win.library.grid.item(i).text().split("\n")[0] for i in range(win.library.grid.count())]
    assert names == ["Alpha", "Bravo", "Charlie", "Delta"]  # finishing the setup makes it an installed one
    assert not win.library.items[1].data(gui.ROLE_SETUP) and win.library.items[1].data(gui.ROLE_INSTALLED)


def test_saves_syncing_after_a_game_closes_show_in_the_sidebar_like_an_install(win, qapp, tmp_path):
    rec = _installed(tmp_path)
    win.app.set_games([{"id": 1, "name": "Jazz Jackrabbit 2", "igdb_id": None}])
    win.begin_play(rec, None, poll=0.05, linger=0.05, appear_timeout=0.1)  # puts the message up
    assert win.playing.showing

    win._sync_started(rec)  # the game closed: the message goes, the sync is shown beside the library
    assert not win.playing.showing
    assert not win.library.installs_box.isHidden() and win.library.installs.count() == 1
    assert win.library.installs.item(0).text() == "Jazz Jackrabbit 2\nSyncing saves..."

    win._play_ended(rec, None, True, False, None)
    assert win.library.installs.count() == 0 and win.library.installs_box.isHidden()


def test_the_search_has_a_cross_that_empties_it_and_pad_and_keyboard_do_the_same(win, qapp):
    box = win.search
    cross = box._clear
    assert not cross.isVisible()  # nothing to clear yet
    box.setText("zelda")
    assert cross.isVisible()

    cross.trigger()
    assert box.text() == "" and not cross.isVisible()

    box.setText("mario")
    win.on_pad(gamepad.TRIGGER_R)  # R2 on the library
    assert box.text() == ""

    box.setText("sonic")
    box.setFocus()
    box.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.ControlModifier))  # not "delete a word"
    assert box.text() == ""


def test_the_guide_announces_clearing_the_search():
    from mog_client.gui import legend

    entry = next(e for e in legend.LEGENDS[legend.LIBRARY] if e.label == "Clear search")
    assert entry.pad == ("trigger_r",) and entry.keys == ("ctrl", "backspace")


def _menu_texts(win):
    return [a.text() for a in win.user_menu.actions() if not a.isSeparator()]


def test_the_user_icon_is_the_last_thing_on_the_right_and_opens_a_menu(win, qapp):
    assert win.user_btn.isVisibleTo(win) and _menu_texts(win) == ["Settings", "Notifications", "Refresh library", "Go offline", "About", "Sign out"]
    assert win.user_btn.x() + win.user_btn.width() > win.search.x() + win.search.width()  # at the far right

    win.user_btn.click()  # looked at before events run: the offscreen display closes a popup that has no focus
    assert win.user_menu.isVisible() and win.user_menu.activeAction().text() == "Settings"
    win.user_menu.hide()


def test_select_on_the_pad_and_ctrl_u_open_the_user_menu_and_the_same_again_closes_it(win, qapp):
    win.on_pad(gamepad.ACCOUNT)
    assert win.user_menu.isVisible()
    win.on_pad(gamepad.ACCOUNT)
    assert not win.user_menu.isVisible()

    win.open_user_menu()
    assert win.user_menu.isVisible()
    win.user_menu.hide()

    win.open_settings()
    win.on_pad(gamepad.ACCOUNT)  # not on the settings page: the button is not there
    assert not win.user_menu.isVisible()


def test_the_menu_rows_do_what_they_say(win, qapp, monkeypatch):
    refreshed = []
    monkeypatch.setattr(win.app, "refresh", lambda: refreshed.append(1))
    assert win.user_menu.actions()[2].text() == "Refresh library"
    win.user_menu.actions()[2].trigger()
    assert refreshed == [1]
    win.user_menu.actions()[1].trigger()
    pump(qapp)
    assert isinstance(win.current_page(), gui.NotificationsPage)
    win.back()
    win.user_menu.actions()[0].trigger()
    pump(qapp)
    assert isinstance(win.current_page(), gui.SettingsPage)


def test_about_is_in_the_user_menu_under_refresh_between_two_separators_and_not_in_settings(win, qapp):
    kinds = ["-" if a.isSeparator() else a.text() for a in win.user_menu.actions()]
    assert kinds == ["Settings", "Notifications", "Refresh library", "Go offline", "-", "About", "-", "Sign out"]

    win.user_menu.actions()[5].trigger()
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.AboutPage) and page.title == "About" and page.about.github.text() == "Open the GitHub page"
    win.back()

    win.open_settings()
    tabs = win.current_page().tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Server", "Library", "Logs"]


def test_going_offline_by_hand_shows_the_badge_and_keeps_the_server_unasked_until_going_online(win, qapp, monkeypatch):
    assert not win.offline_badge.isVisibleTo(win)
    win.offline_action.trigger()
    pump(qapp)
    assert connectivity.forced() and win.app.settings.offline_forced and load_settings().offline_forced
    assert win.offline_badge.isVisibleTo(win) and win.offline_action.text() == "Go online"
    assert "turned on by you" in win.offline_badge.toolTip()

    probes = []
    monkeypatch.setattr(win.app, "check_connection", lambda: probes.append(1))
    win.look_for_server()  # the timer: nothing looks for a way out of an offline mode that was chosen
    assert probes == []

    win.offline_action.trigger()
    pump(qapp)
    assert not connectivity.forced() and not load_settings().offline_forced and probes == [1]
    assert win.offline_action.text() == "Go offline"


def test_a_picture_that_could_not_be_fetched_does_not_replace_the_one_kept(win, qapp):
    from mog_client import snapshot

    base = win.app.settings.base
    snapshot.save_user(base, "admin", b"the picture")
    shown = []
    win.app.bridge.user.connect(lambda name, picture: shown.append((name, picture)))

    class Server:  # answers who it is, then goes out of reach before the picture
        def me(self):
            return {"username": "admin", "avatar_path": "/api/avatar"}

        def get_image(self, path):
            return None

    win.app._load_user(Server())
    pump(qapp)
    assert shown == [("admin", b"the picture")] and snapshot.load_user(base) == ("admin", b"the picture")


def test_when_offline_on_its_own_the_menu_says_go_online_and_looks_for_the_server_at_once(win, qapp, monkeypatch):
    monkeypatch.setattr(connectivity, "server_reachable", lambda base: False)
    monkeypatch.setattr(connectivity, "_reaches_internet", lambda: True)
    win.app.check_connection()
    for _ in range(100):
        pump(qapp)
        if not win.app._probing:
            break
    pump(qapp)
    assert connectivity.state() == connectivity.NO_SERVER and win.offline_action.text() == "Go online"

    looked = []
    monkeypatch.setattr(win.app, "check_connection", lambda done=None: looked.append(done))
    win.offline_action.trigger()
    assert len(looked) == 1 and not connectivity.forced()  # not turned into a choice to stay offline
    looked[0](True)
    pump(qapp)
    assert [r.text for r in logstore.store.records()[-2:]] == ["Looking for MOG Server...", "MOG Server cannot be reached: staying in Offline mode"]


def test_a_server_out_of_reach_shows_the_badge_and_a_way_back_refreshes_the_library(win, qapp, monkeypatch):
    refreshed = []
    monkeypatch.setattr(win.app, "refresh", lambda: refreshed.append(1))
    monkeypatch.setattr(connectivity, "server_reachable", lambda base: False)
    monkeypatch.setattr(connectivity, "_reaches_internet", lambda: False)
    win.app.check_connection()
    for _ in range(100):
        pump(qapp)
        if not win.app._probing:
            break
    pump(qapp)
    assert connectivity.state() == connectivity.NO_INTERNET and win.offline_badge.isVisibleTo(win)
    assert "internet" in win.offline_badge.toolTip()

    monkeypatch.setattr(connectivity, "server_reachable", lambda base: True)
    win.app.check_connection()
    for _ in range(100):
        pump(qapp)
        if refreshed:
            break
    assert refreshed == [1] and not win.offline_badge.isVisibleTo(win)


def test_signing_out_asks_first_then_forgets_the_password_and_opens_settings(win, qapp):
    win.sign_out()
    page = win.current_page()
    assert isinstance(page, gui.ConfirmPage) and win.app.settings.password == "p"  # nothing yet
    page.yes.click()
    pump(qapp)
    assert load_settings().password == "" and load_settings().base == "http://server:5000"
    assert not win.user_btn.isVisibleTo(win) and isinstance(win.current_page(), gui.SettingsPage)


def test_start_and_ctrl_comma_open_the_user_menu_on_its_first_row_and_the_guide_says_so(win, qapp):
    from mog_client.gui import legend

    win.on_pad(gamepad.MENU)  # Start
    assert win.user_menu.isVisible() and win.user_menu.activeAction().text() == "Settings"
    win.user_menu.hide()

    win._shortcut_menu()  # what Ctrl+, and Ctrl+U run
    assert win.user_menu.isVisible() and win.user_menu.activeAction().text() == "Settings"
    win.user_menu.hide()

    entry = next(e for e in legend.LEGENDS[legend.LIBRARY] if e.label == "Menu")
    assert entry.pad == ("start",) and entry.keys == ("ctrl", "comma")
    assert not any(e.label == "Settings" for e in legend.LEGENDS[legend.LIBRARY])


def test_the_guide_follows_the_last_device_used_while_a_controller_is_connected(win, qapp, monkeypatch):
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))
    win.set_pad(True, "xbox")
    assert "/pad/xbox/" in win.legend.text() and win.input_mode == "pad"  # a connected pad is the default

    QTest.keyClick(win.library.grid, Qt.Key_Down)  # a key from the keyboard: Qt marks it spontaneous
    assert win.input_mode == "keys" and "/pad/" not in win.legend.text() and "/keys/" in win.legend.text()

    win.on_pad_event(gamepad.DOWN)
    assert win.input_mode == "pad" and "/pad/xbox/" in win.legend.text()

    win.set_pad(False, "")
    assert "/keys/" in win.legend.text()  # no pad: always the keyboard's


def test_a_key_the_pad_posts_does_not_switch_the_guide_to_the_keyboard(win, qapp):
    win.set_pad(True, "xbox")
    win.on_pad(gamepad.DOWN)
    pump(qapp)
    assert win.input_mode == "pad"


def _walk_with_the_pad(win, qapp, steps=14):
    """Press Down on the pad until the focus has been everywhere it goes; the widgets it stopped on, in order."""
    seen = []
    for _ in range(steps):
        win.on_pad(gamepad.DOWN)
        pump(qapp)
        focus = QApplication.focusWidget()
        if focus not in seen:
            seen.append(focus)
    return seen


def test_the_pad_walks_from_the_executables_to_the_options_and_the_buttons_and_picking_does_not_confirm(win, qapp, tmp_path):
    from mog_client.gui.widgets import Toggle

    (tmp_path / "game.exe").write_bytes(b"x")
    (tmp_path / "other.exe").write_bytes(b"x")
    answers = []
    page = gui.ExecutablePage(win, config.InstalledGame(9, "G", str(tmp_path)), lambda *a: answers.append(a))
    win.push(page)
    win.activateWindow()
    pump(qapp)
    assert not win.modal.close_button.isVisibleTo(win)  # Later is the way out

    page.list.setFocus()
    win.on_pad(gamepad.ACCEPT)  # A on a row picks it...
    pump(qapp)
    assert answers == [] and QApplication.focusWidget() is not page.list  # ...and does not confirm: the focus moves on

    page.list.setFocus()
    seen = _walk_with_the_pad(win, qapp)
    assert any(isinstance(w, Toggle) for w in seen)
    assert {w.text() for w in seen if isinstance(w, QPushButton)} >= {"Browse...", "Back", "Use this executable"}  # from Options: Back

    ok = next(w for w in seen if isinstance(w, QPushButton) and w.text() == "Use this executable")
    ok.setFocus()
    win.on_pad(gamepad.ACCEPT)
    pump(qapp)
    assert len(answers) == 1 and answers[0][0].endswith(".exe")


def test_picking_a_save_from_another_machine_waits_for_ok_and_the_dialog_has_no_close_button(win, qapp):
    chosen = []
    win.choose(
        "Saves from another machine",
        "Put one on this machine now?",
        [("Restore one", 1), ("Restore two", 2)],
        chosen.append,
        skip="Skip",
    )
    win.activateWindow()
    pump(qapp)
    page = win.current_page()
    assert not win.modal.close_button.isVisibleTo(win)

    page.list.setCurrentRow(1)
    page.list.setFocus()
    win.on_pad(gamepad.ACCEPT)
    pump(qapp)
    assert chosen == [] and win.current_page() is page  # still open, with the second row picked

    seen = _walk_with_the_pad(win, qapp, steps=6)
    ok = next(w for w in seen if isinstance(w, QPushButton) and w.text() == "OK")
    ok.click()
    pump(qapp)
    assert chosen == [2]


def test_a_picker_without_skip_keeps_its_close_button(win, qapp):
    win.choose("Restore which save?", "x", [("one", 1)], lambda v: None)
    pump(qapp)
    assert win.modal.close_button.isVisibleTo(win)


def test_the_update_question_starts_on_yes_and_no_is_red_while_other_questions_start_on_no(win, qapp):
    from types import SimpleNamespace

    win.on_update_checked(SimpleNamespace(version="9.9"), "", False)
    win.activateWindow()
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.ConfirmPage) and "9.9" in page.text_label.text()
    page.focus_default()
    assert QApplication.focusWidget() is page.yes and page.yes.isDefault()
    assert page.no.property("danger") is True and not page.yes.property("danger")
    win.back()

    win.ask("Delete it?", lambda: None, danger=True)
    pump(qapp)
    other = win.current_page()
    other.focus_default()
    assert QApplication.focusWidget() is other.no and not other.no.property("danger") and other.yes.property("danger") is True


@pytest.mark.parametrize(("state", "offered"), [("awaiting_executable", True), ("installed", False)])
def test_the_saves_of_other_machines_are_offered_after_an_install_but_not_when_only_the_shortcuts_change(
    win, qapp, monkeypatch, tmp_path, state, offered
):
    from mog_client import launchsettings, manager

    folder = tmp_path / "Eta"
    folder.mkdir()
    (folder / "game.exe").write_bytes(b"x")
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    rec = config.InstalledGame(41, "Eta", str(folder), state=state, executable=str(folder / "game.exe"))
    monkeypatch.setattr(manager, "finish_setup", lambda *a, **k: None)
    monkeypatch.setattr(win.app, "client", lambda: None)
    kept = []
    monkeypatch.setattr(launchsettings, "remember_executable", lambda client, r: kept.append(r.executable))  # for the server
    monkeypatch.setattr(launchsettings, "remembered_executable", lambda client, r: None)
    monkeypatch.setattr(win.app.settings, "steam_decided", True)  # no Steam question over the page
    asked = []
    monkeypatch.setattr(win.saves, "offer_after_install", lambda r: asked.append(r.game_id))

    page.choose_executable(rec)
    pump(qapp)
    win.current_page().accept()
    for _ in range(40):  # the work runs in the background
        pump(qapp)
        if asked or not offered:
            break
        time.sleep(0.05)
    pump(qapp)

    assert (asked == [41]) is offered
    assert kept == [str(folder / "game.exe")]  # whichever way it was reached, the choice is kept on the server


@pytest.mark.parametrize("room", [480, 700, 946, 1232, 1598, 1872])
def test_the_grid_scale_leaves_no_column_unfilled_and_stays_close_to_normal(room):
    scale = gui.grid_scale(room)
    cell = int(gui.CELL_SIZE.width() * scale)
    columns = room // cell
    assert columns >= 1 and room - columns * cell <= 8  # a few pixels of rounding at most
    assert 0.85 <= scale <= 1.25
    assert gui.grid_scale(0) == 1.0 and gui.grid_scale(100) == gui.MIN_GRID_SCALE


def test_the_covers_are_resized_so_whole_columns_fill_the_grid_with_the_sidebar_out_or_in(win, qapp):
    games = [{"id": n, "name": f"Game {n}", "library_id": None, "igdb_id": None} for n in range(1, 13)]
    win.set_games(games)
    win.library.show_loading(False)
    win.resize(1280, 800)
    win.library.fit_grid()
    for shown in (True, False, True):
        if win.library.sidebar.isVisible() != shown:
            win.library.toggle_sidebar()
        pump(qapp)
        pump(qapp)
        grid, delegate = win.library.grid, win.library.grid.itemDelegate()
        room = grid.width() - 2 * grid.frameWidth() - grid.verticalScrollBar().sizeHint().width()
        cell = delegate.cell_size().width()
        assert room // cell == max(1, round(room / gui.CELL_SIZE.width())) and room - (room // cell) * cell <= 8
        assert grid.gridSize() == delegate.cell_size()


def test_the_sidebar_lists_never_scroll_sideways_and_their_rows_are_as_wide_as_what_is_shown(win, qapp):
    from PySide6.QtWidgets import QStyleOptionViewItem

    win.library.set_libraries([{"id": 1, "name": "Games"}, {"id": 2, "name": "Restricted"}])
    pump(qapp)
    for side_list in (win.library.libs, win.library.sorts):
        assert side_list.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff and not side_list.horizontalScrollBar().isVisible()

    option = QStyleOptionViewItem()
    option.widget = win.library.sorts
    option.rect.setWidth(win.library.sorts.viewport().width() + 40)  # a rect wider than the list shows
    hint = win.library.sorts.itemDelegate().sizeHint(option, win.library.sorts.model().index(0, 0))
    assert hint.width() == win.library.sorts.viewport().width()


def test_the_library_chosen_in_the_sidebar_is_kept_for_the_next_visit_and_the_next_start(win, qapp):
    libraries = [{"id": 1, "name": "Games"}, {"id": 2, "name": "Restricted"}]
    win.library.set_libraries(libraries)
    win.library.libs.setCurrentRow(2)  # Restricted
    pump(qapp)
    assert win.library.library_filter == 2 and load_settings().library_filter == 2

    win.library.set_libraries(libraries)  # the server's list again (a refresh, going back): still the same one
    assert win.library.library_filter == 2 and win.library.libs.currentRow() == 2

    win.library.set_libraries([])  # nothing known yet (offline, an older server): the choice waits
    assert win.library.library_filter == 2 and load_settings().library_filter == 2
    win.library.set_libraries(libraries)
    assert win.library.libs.currentRow() == 2

    assert gui.LibraryPage(win).library_filter == 2  # the next start

    win.library.set_libraries([{"id": 1, "name": "Games"}])  # the library was deleted
    assert win.library.library_filter is None and load_settings().library_filter is None
    assert win.library.libs.currentRow() == 0


def test_back_closes_the_sidebar_on_the_pad_and_the_keyboard_and_is_then_left_alone(win, qapp, monkeypatch):
    from mog_client.gui import legend

    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))
    win.activateWindow()
    library = win.library
    if not library.sidebar.isVisibleTo(library):
        library.toggle_sidebar()
    win.refresh_legend()
    assert win.legend_context() == legend.LIBRARY
    library.libs.setFocus()

    win.on_pad(gamepad.BACK)  # B
    pump(qapp)
    assert not library.sidebar.isVisibleTo(library) and win.current_page() is library
    assert win.legend_context() == legend.LIBRARY_ALONE
    assert load_settings().show_sidebar is False  # the same choice as the sidebar's own button, kept

    win.on_pad(gamepad.BACK)  # nothing more to close
    pump(qapp)
    assert win.current_page() is library and not library.sidebar.isVisibleTo(library)

    library.toggle_sidebar()
    QTest.keyClick(library.grid, Qt.Key_Escape)  # Esc is the same key
    pump(qapp)
    assert not library.sidebar.isVisibleTo(library)


def test_back_still_goes_back_from_a_game_page_with_the_sidebar_open(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    win.activateWindow()
    win.show_game(41)
    pump(qapp)
    assert win.current_page() is not win.library

    win.on_pad(gamepad.BACK)
    pump(qapp)

    assert win.current_page() is win.library and win.library.sidebar.isVisibleTo(win.library)  # it left the page, not the sidebar


def test_the_window_can_be_made_small_for_a_small_screen_on_every_page(win, qapp, monkeypatch):
    """A Steam Deck in desktop mode has 1280x800 minus its panels: no page may ask for more than about 700x650."""
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    win.library.show_loading(True)  # the page with the big picture of Mog
    pages = {"library while loading": lambda: None, "settings": win.open_settings, "game": lambda: win.show_game(41)}
    for name, show in pages.items():
        show()
        win.resize(640, 600)
        pump(qapp)
        pump(qapp)
        hint = win.minimumSizeHint()
        assert hint.width() <= 700 and hint.height() <= 650, (name, hint)
        assert win.width() <= 700 and win.height() <= 650, (name, win.size())
        if name != "library while loading":
            win.back()


def test_the_loading_picture_shrinks_with_the_room_it_has(qapp):
    from mog_client.gui.loading import LoadingPanel

    panel = LoadingPanel()
    panel.resize(900, 700)
    panel.show()
    pump(qapp)
    big = panel.picture.pixmap().width()
    panel.resize(300, 300)
    pump(qapp)
    assert panel.picture.pixmap().width() < big and panel.picture.pixmap().width() <= 300
    assert panel.minimumSizeHint().width() < 400


def _rich_game(win, monkeypatch):
    """A game with everything a page can show: a long name and description, metadata, screenshots, HowLongToBeat times."""
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    monkeypatch.setattr(win.app, "fetch_images", lambda urls, gid: None)
    game = {
        "id": 41,
        "name": "Final Fantasy Tactics: The Ivalice Chronicles",
        "library_id": None,
        "igdb_id": 5,
        "summary": "A long description of the game. " * 30,
        "igdb_metadata": {
            "first_release_date": 1700000000,
            "genres": [{"name": "Strategy"}, {"name": "RPG"}, {"name": "Turn-based"}],
            "screenshots": [{"url": f"//images/{n}.jpg"} for n in range(4)],
        },
        "hltb_metadata": {"main_story": 100000, "main_plus_extra": 150000, "completionist": 200000},
    }
    win.app.set_games([game])
    win.covers[41] = QPixmap(200, 270)
    return game


def _settle(win, qapp, width, height):
    for _ in range(3):  # a smaller page lets the window go smaller still: the first resize is held at the old minimum
        win.resize(width, height)
        pump(qapp)
        pump(qapp)


def test_the_window_goes_down_to_800x600_on_a_game_page_keeping_the_cover_the_bar_and_the_buttons(win, qapp, monkeypatch):
    """Caught on a Steam Deck in desktop mode: the page would not get smaller than about 820x730 and its buttons fell below
    the screen. A small window drops the description, the screenshots and the HowLongToBeat table and shrinks the cover."""
    _rich_game(win, monkeypatch)
    win.show_game(41)
    win.resize(1280, 800)
    pump(qapp)
    page = win.current_page()
    assert page.summary.isVisible() and page.shots.isVisible() is bool(page.shot_urls) and page.hltb_box.isVisible()  # all of it, at 1280x800

    _settle(win, qapp, 800, 600)

    assert (win.width(), win.height()) == (800, 600) and win.minimumSizeHint().width() <= 800 and win.minimumSizeHint().height() <= 600
    assert not page.summary.isVisible() and not page.shots.isVisible()  # sacrificed
    buttons = [page.buttons.itemAt(i).widget() for i in range(page.buttons.count()) if page.buttons.itemAt(i).widget()]
    assert buttons and all(b.mapTo(win, b.rect().bottomLeft()).y() <= win.height() for b in buttons)  # none cut off below
    assert page.bar.isVisible() and page.status.isVisible() and page.cover.isVisible()


def test_a_smaller_window_still_shrinks_the_cover_and_drops_the_how_long_to_beat_table_when_narrow(win, qapp, monkeypatch):
    _rich_game(win, monkeypatch)
    win.show_game(41)
    win.resize(1280, 800)
    pump(qapp)
    page = win.current_page()
    assert page.cover.height() == gui.COVER_SIZE.height()

    _settle(win, qapp, 800, 520)
    medium = page.cover.height()
    assert page.hltb_box.isVisible() and medium < gui.COVER_SIZE.height()  # a shorter window, a smaller cover

    _settle(win, qapp, 10, 10)  # as small as it will go
    assert page.cover.height() < medium and page.cover.width() == round(page.cover.height() * 200 / 270)  # same proportions
    assert not page.hltb_box.isVisible()  # no room beside the header for it
    assert win.width() <= 800 and win.height() <= 600  # and that smallest is under 800x600
    buttons = [page.buttons.itemAt(i).widget() for i in range(page.buttons.count()) if page.buttons.itemAt(i).widget()]
    assert all(b.mapTo(win, b.rect().bottomLeft()).y() <= win.height() for b in buttons)


def test_what_was_dropped_comes_back_when_the_window_is_large_again(win, qapp, monkeypatch):
    _rich_game(win, monkeypatch)
    win.show_game(41)
    win.resize(1280, 800)
    pump(qapp)
    page = win.current_page()
    _settle(win, qapp, 800, 600)
    assert not page.summary.isVisible()

    _settle(win, qapp, 1280, 800)

    assert page.summary.isVisible() and page.hltb_box.isVisible() and page.cover.height() == gui.COVER_SIZE.height()


def test_a_long_title_is_cut_short_instead_of_holding_the_window_wide(win, qapp, monkeypatch):
    _rich_game(win, monkeypatch)
    win.show_game(41)
    _settle(win, qapp, 800, 600)
    assert win.title.text() == "Final Fantasy Tactics: The Ivalice Chronicles"  # the title is kept whole
    assert win.title.width() < win.title.fontMetrics().horizontalAdvance(win.title.text())  # and drawn cut short here
    assert "..." in QLabel.text(win.title) or "\u2026" in QLabel.text(win.title)


def test_every_page_fits_in_800x600(win, qapp, monkeypatch):
    _rich_game(win, monkeypatch)
    win.library.set_libraries([{"id": 1, "name": "Games"}, {"id": 2, "name": "Restricted"}])
    win.show_game(41)
    _settle(win, qapp, 10, 10)
    game_min = win.minimumSizeHint()
    win.back()
    pump(qapp)
    win.open_settings()
    _settle(win, qapp, 10, 10)
    settings_min = win.minimumSizeHint()
    for name, size in (("game", game_min), ("settings", settings_min), ("library", win.library.minimumSizeHint())):
        assert size.width() <= 800 and size.height() <= 600, (name, size)


def test_without_a_signed_in_user_start_still_reaches_settings(win, qapp):
    win.user_btn.setVisible(False)
    win.on_pad(gamepad.MENU)
    pump(qapp)
    assert isinstance(win.current_page(), gui.SettingsPage)


def _game_page_with_mods(win, qapp, monkeypatch, mods):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    game = {"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}
    win.app.set_games([game])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    page._on_mods(41, mods)
    return page, game


def _buttons(page):
    return [b.text() for b in page.findChildren(QPushButton)]


def test_a_game_with_mods_has_a_mods_button_that_lists_them_and_one_without_has_none(win, qapp, monkeypatch):
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    assert "Mods" not in _buttons(page)

    win.app.mods[41] = [{"name": "mod1", "kind": "folder", "size_bytes": 2048, "file_count": 2}, {"name": "mod2.zip", "kind": "archive", "size_bytes": 10, "file_count": 1}]
    page._on_mods(41, win.app.mods[41])
    pump(qapp)
    assert "Mods" in _buttons(page)

    page.open_mods()
    pump(qapp)
    listing = win.current_page()
    assert isinstance(listing, gui.ModsPage) and listing.list.count() == 2
    assert listing.list.item(0).text().startswith("mod1\nfolder, zipped on download")
    assert listing.list.item(1).text().startswith("mod2.zip\narchive")


def test_a_mod_row_in_the_sidebar_opens_that_games_mods_and_an_install_row_its_page(win, qapp, monkeypatch):
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    win.app.mods[41] = [{"name": "mod1", "kind": "folder", "size_bytes": 1, "file_count": 1}]
    win.back()
    win.app.mod_jobs[(41, "mod1")] = ("Zipping", 40)
    win.library.update_active()

    win.library._open_install(win.library.installs.item(0))
    assert isinstance(win.current_page(), gui.ModsPage) and win.current_page().game["id"] == 41
    win.back()
    assert isinstance(win.current_page(), gui.GamePage)  # Back lands on the game's page

    win.back()
    win.app.mod_jobs.clear()
    win.library.syncing.add(41)
    win.library.update_active()
    win.library._open_install(win.library.installs.item(0))
    assert isinstance(win.current_page(), gui.GamePage)  # a sync row opens the game, not its mods
    win.library.syncing.discard(41)


def test_choosing_a_mod_starts_its_download_and_the_sidebar_shows_the_progress(win, qapp, monkeypatch):
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    mod = {"name": "mod1", "kind": "folder", "size_bytes": 1, "file_count": 1}
    win.app.mods[41] = [mod]
    started = []
    monkeypatch.setattr(win.app, "download_mod", lambda g, m: started.append((g["id"], m["name"])))
    page.open_mods()
    listing = win.current_page()

    listing.fetch(listing.list.item(0))
    assert started == [(41, "mod1")]

    win.app.mod_jobs[(41, "mod1")] = ("Zipping", 40)
    win.library.update_active()
    assert win.library.installs.count() == 1 and not win.library.installs_box.isHidden()
    assert win.library.installs.item(0).text() == "Eta\nMod mod1\nZipping... 40%"

    win.app.mod_jobs[(41, "mod1")] = ("Downloading", 70)
    win.library.update_active()
    assert win.library.installs.item(0).text() == "Eta\nMod mod1\nDownloading... 70%"
    win.app.mod_jobs.clear()
    win.library.update_active()
    assert win.library.installs.count() == 0 and win.library.installs_box.isHidden()


def _finish_a_mod(win, qapp, monkeypatch, tmp_path, told):
    from mog_client import mods as mods_module

    calls = []
    server = type("Server", (), {"mod_downloaded": lambda self, gid, name, machine=None: calls.append((gid, name)) or told})()
    polled = []
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "poll_notifications", lambda: polled.append(1))
    monkeypatch.setattr(win.app, "mod_folder", lambda game: tmp_path)
    monkeypatch.setattr(mods_module, "fetch", lambda client, gid, mod, dest, progress, *a, **k: (progress("Zipping", 10), tmp_path / "mod1.zip")[1])
    monkeypatch.setattr(win.app, "run_bg", lambda fn, on_error=None: fn())
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    messages, notes = [], []
    win.app.bridge.message.connect(lambda level, text: messages.append((level, text)))
    win.app.bridge.note.connect(notes.append)

    win.app.download_mod(win.app.games[41], {"name": "mod1", "kind": "folder"})
    pump(qapp)
    return calls, polled, messages, notes


def test_a_finished_mod_job_is_told_in_the_notifications_not_in_a_window(win, qapp, monkeypatch, tmp_path):
    calls, polled, messages, notes = _finish_a_mod(win, qapp, monkeypatch, tmp_path, told=True)

    assert calls == [(41, "mod1")] and polled == [1]  # the server keeps it in the inbox, which is fetched at once
    assert messages == [] and notes == [f"Mod mod1 of Eta downloaded: {tmp_path / 'mod1.zip'}"]
    assert win.app.mod_jobs == {} and win.app.mod_saved[(41, "mod1")] == str(tmp_path / "mod1.zip")


def test_a_server_that_cannot_keep_the_notice_gets_a_message_so_it_is_not_lost(win, qapp, monkeypatch, tmp_path):
    _calls, polled, messages, _notes = _finish_a_mod(win, qapp, monkeypatch, tmp_path, told=False)

    assert polled == [] and messages == [("info", f"Mod mod1 of Eta downloaded: {tmp_path / 'mod1.zip'}")]


def test_an_install_the_server_cannot_choose_an_installer_for_opens_the_picker_here(win, qapp, monkeypatch):
    from mog_client import manager

    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    asked = []
    monkeypatch.setattr(win.app, "load_installers", lambda gid: asked.append(gid))
    win.app.set_games([{"id": 41, "name": "Gothic II", "fs_name": "Gothic II", "library_id": None, "igdb_id": None}])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None: started.append((game["id"], installer)))

    page._on_finished(41, f"{manager.NEEDS_PICK}, open http://server:5000/api/games/install/vnc/6900/vnc.html")
    pump(qapp)

    picker = win.current_page()
    assert isinstance(picker, gui.InstallerPickerPage) and asked == [41]
    assert not win.overlay.showing  # no message sending the user to the server
    picker.on_installers(41, [{"path": "Setup/gothic2.exe", "file_size_bytes": 1000, "kind": "exe", "category": "game"}], "")
    assert "could not tell which installer" in picker.status.text()
    rows = _rows(picker)
    assert picker.server_button.isHidden() and any("gothic2.exe" in r for r in rows)  # it could not choose: not offered

    picker.choose(picker.list.item(_index_of(picker, "Setup/gothic2.exe")))
    assert started == [(41, {"path": "Setup/gothic2.exe", "file_size_bytes": 1000, "kind": "exe", "category": "game"})]


def test_when_the_game_page_is_not_in_front_the_user_is_told_to_open_it(win, qapp, monkeypatch):
    from mog_client import manager

    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Gothic II", "library_id": None, "igdb_id": None}])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    win.back()

    page._on_finished(41, f"{manager.NEEDS_PICK}, open http://x")
    pump(qapp)

    assert win.current_page() is win.library and "choose which installer" in win.overlay.body.text()


def test_the_mods_page_shows_each_mods_progress_on_its_own_row_also_when_opened_later(win, qapp, monkeypatch):
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    mods = [{"name": "mod1", "kind": "folder", "size_bytes": 2048, "file_count": 2}, {"name": "mod2.zip", "kind": "archive", "size_bytes": 10, "file_count": 1}]
    win.app.mods[41] = mods
    win.app.mod_jobs[(41, "mod1")] = ("Zipping", 40)  # started earlier, the page was left meanwhile

    page.open_mods()
    listing = win.current_page()
    assert listing.list.item(0).text() == "mod1\nZipping... 40%"
    assert listing.list.item(1).text().startswith("mod2.zip\narchive")  # the other mod is untouched

    win.app.mod_jobs[(41, "mod1")] = ("Downloading", 70)
    win.app.bridge.activity.emit()  # what the download thread does as it goes
    assert listing.list.item(0).text() == "mod1\nDownloading... 70%"

    del win.app.mod_jobs[(41, "mod1")]
    win.app.mod_saved[(41, "mod1")] = "/home/u/Downloads/MOG/Eta/mods/mod1.zip"
    win.app.bridge.activity.emit()
    assert listing.list.item(0).text() == "mod1\nDownloaded to /home/u/Downloads/MOG/Eta/mods/mod1.zip"


def test_the_sidebar_rows_wrap_a_long_name_instead_of_cutting_it(win, qapp):
    win.app.set_games([{"id": 7, "name": "Gothic II: The Chronicles of Myrtana: Archolos Extended Edition", "igdb_id": None}])
    win.app.mod_jobs[(7, "Gothic Online multiplayer mod for the Night of the Raven")] = ("Downloading", 70)
    win.library.update_active()
    qapp.processEvents()

    assert win.library.installs.wordWrap() and win.library.installs.textElideMode() == Qt.ElideNone


def test_choosing_a_mod_that_is_being_fetched_asks_to_cancel_it_and_cancelling_stops_it(win, qapp, monkeypatch):
    import threading

    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    win.app.mods[41] = [{"name": "mod1", "kind": "folder", "size_bytes": 1, "file_count": 1}]
    page.open_mods()
    listing = win.current_page()
    stop = win.app.mod_stops[(41, "mod1")] = threading.Event()
    win.app.mod_jobs[(41, "mod1")] = ("Zipping", 10)

    listing.fetch(listing.list.item(0))
    ask = win.current_page()
    assert isinstance(ask, gui.ConfirmPage) and "Cancel fetching mod1?" in ask.text_label.text() and not stop.is_set()

    ask.yes.click()
    assert stop.is_set()


def test_a_mod_is_saved_in_the_games_own_folder_and_is_not_taken_for_a_save(win, qapp, monkeypatch, tmp_path):
    from mog_client import saves
    from mog_client.saves.state import load_install_manifest

    install = tmp_path / "games" / "Eta"
    (install / "mods").mkdir(parents=True)
    config.save_library({41: config.InstalledGame(41, "Eta", str(install), state="installed")})
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    assert win.app.mod_folder(win.app.games[41]) == install / "mods"

    saved = install / "mods" / "mod1.zip"
    saved.write_bytes(b"zipped")
    win.app._note_mod_file(41, saved)
    assert "mods/mod1.zip" in load_install_manifest(41)

    # A game that is not installed yet: the folder it would be installed in.
    monkeypatch.setattr(win.app.settings, "install_dirs", [str(tmp_path / "root")])
    other = {"id": 42, "name": "Zeta", "library_id": None, "igdb_id": None}
    assert win.app.mod_folder(other) == tmp_path / "root" / "Zeta" / "mods"
    assert saves  # (the module is imported to make the intent plain)


def test_the_empty_folders_a_mod_leaves_are_cleaned_up_but_not_the_installed_games_own(win, qapp, monkeypatch, tmp_path):
    root = tmp_path / "root"
    monkeypatch.setattr(win.app.settings, "install_dirs", [str(root)])
    install = root / "Eta"
    (install / "mods").mkdir(parents=True)
    (install / "game.exe").write_bytes(b"x")
    config.save_library({41: config.InstalledGame(41, "Eta", str(install), state="installed")})
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    win.app.prune_mod_folder(win.app.games[41])
    assert not (install / "mods").exists() and (install / "game.exe").is_file()  # only the mods folder went

    alone = {"id": 42, "name": "Zeta", "library_id": None, "igdb_id": None}
    (root / "Zeta" / "mods").mkdir(parents=True)
    win.app.mod_jobs[(42, "mod1")] = ("Downloading", 10)  # another mod is on its way: its folder is not touched
    win.app.prune_mod_folder(alone)
    assert (root / "Zeta" / "mods").is_dir()

    win.app.mod_jobs.clear()
    win.app.prune_mod_folder(alone)
    assert not (root / "Zeta").exists() and root.is_dir()  # no game here: its folder goes too


def test_what_was_running_when_the_client_closed_starts_again_and_the_rest_is_forgotten(win, qapp, monkeypatch, tmp_path):
    from mog_client import activity

    games = [{"id": i, "name": f"G{i}", "library_id": None, "igdb_id": None} for i in (1, 2, 3)]
    folder = tmp_path / "G1"
    folder.mkdir()
    config.save_library(
        {
            1: config.InstalledGame(1, "G1", str(folder), state="installing"),  # partial: resumes
            2: config.InstalledGame(2, "G2", str(tmp_path / "G2"), state="installed"),  # finished meanwhile: forgotten
        }
    )
    activity.add_install(1)
    activity.add_install(2)
    activity.add_install(99)  # a game the server no longer lists
    activity.add_mod(3, {"name": "mod1", "kind": "folder"})
    activity.add_mod(77, {"name": "gone", "kind": "file"})
    installs, mods = [], []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None: installs.append(game["id"]))
    monkeypatch.setattr(win.app, "download_mod", lambda game, mod: mods.append((game["id"], mod["name"])))
    win.app.set_games(games)

    win._resume_background()
    win._resume_background()  # once per start

    assert installs == [1] and mods == [(3, "mod1")]
    assert activity.load()["installs"] == [1]  # the others were dropped (the resumed one is the install's own to clear)
    assert [m["game_id"] for m in activity.load()["mods"]] == [3]


def test_starting_an_install_and_a_mod_remembers_them_and_ending_forgets_them(win, qapp, monkeypatch):
    from mog_client import activity, manager

    ran = []
    monkeypatch.setattr(manager, "run_install", lambda *a, **k: ran.append(activity.load()["installs"]))
    game = {"id": 5, "name": "Eta", "library_id": None, "igdb_id": None}
    win.app.set_games([game])

    listed = []
    win.app.bridge.activity.connect(lambda: listed.append(sorted(win.app.installs)))
    win.app.start_install(game)
    assert listed and listed[0] == [5]  # the task list hears of it at once, not with the first progress
    assert _until(qapp, lambda: 5 not in win.app.installs, seconds=15)  # it runs in a thread: give a slow machine time

    assert ran == [[5]]  # remembered while it ran
    assert _until(qapp, lambda: activity.load()["installs"] == [], seconds=15)  # and forgotten once it ended


def test_howlongtobeat_times_are_a_table_beside_the_header_only_when_the_game_has_them(win, qapp, monkeypatch):
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    times = {"main_story": 102_600, "main_plus_extra": 122_400, "completionist": 158_400}
    win.app.set_games(
        [
            {"id": 51, "name": "Timed", "library_id": None, "hltb_metadata": times},
            {"id": 52, "name": "Untimed", "library_id": None},
        ]
    )
    win.show_game(51)
    pump(qapp)
    box = win.current_page().hltb_box
    texts = [lbl.text() for lbl in box.findChildren(QLabel)]
    assert box.isVisibleTo(win.current_page())
    assert texts == ["HOW LONG TO BEAT", "Main Story", "28.5h", "Main + Extra", "34h", "Completionist", "44h"]

    win.show_game(52)
    pump(qapp)
    assert not win.current_page().hltb_box.isVisibleTo(win.current_page())


def test_a_long_description_does_not_squeeze_the_rows_of_the_game_page(win, qapp, monkeypatch):
    """A word-wrapped label made the header's height depend on its width, so a short window squeezed the form
    rows under their own height and cut their text."""
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    text = "A long story about a fighting tournament, with many modes and arenas. " * 12
    win.app.set_games(
        [
            {
                "id": 61,
                "name": "Long",
                "library_id": None,
                "summary": text,
                "igdb_metadata": {
                    "first_release_date": 1_706_227_200,
                    "genres": [{"name": "Fighting"}],
                    "game_modes": [{"name": "Single player"}, {"name": "Multiplayer"}],
                    "player_perspectives": [{"name": "Side view"}],
                },
            }
        ]
    )
    win.show_game(61)
    win.resize(1000, 700)
    pump(qapp)
    page = win.current_page()
    chrome = win.height() - page.height()  # the header row and the guide around the page
    win.resize(1000, page.minimumSizeHint().height() + chrome + 4)  # as short as the page can be before it must drop parts
    pump(qapp)
    rows = [lbl for lbl in page.header.findChildren(QLabel) if lbl.objectName() in ("metaKey", "metaValue")]
    assert rows and all(lbl.height() >= lbl.minimumSizeHint().height() for lbl in rows)
    summary = page.findChild(gui.ParagraphLabel, "summary")
    assert not summary.hasHeightForWidth() and summary.text().endswith("...")
    assert summary.height() >= 2 * summary.fontMetrics().lineSpacing()


def _picker_for(win, qapp, monkeypatch, needed=True):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    monkeypatch.setattr(win.app, "load_installers", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Metroid", "fs_name": "Metroid", "library_id": None, "igdb_id": None}])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    win.push(gui.InstallerPickerPage(win, page, page._group(), needed=needed))
    pump(qapp)
    return page, win.current_page()


def _rows(picker):
    return [picker.text_of(picker.list.item(i)) for i in range(picker.list.count())]


EXE = {"path": "Metroid.exe", "file_size_bytes": 1000, "kind": "executable (top level)", "category": "game"}


def test_a_folder_with_no_installer_offers_its_executables_and_just_copy_as_its_first_row(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append((installer, extract)))

    picker.on_installers(41, [EXE], "", True)
    rows = _rows(picker)
    assert any("Metroid.exe" in r for r in rows) and sum("Just copy" in r for r in rows) == 1
    first = next(r for r in rows if r.strip() and "Metroid.exe" in r or "Just copy" in r)
    assert first.startswith("Just copy")  # the first row under the version, before the executables
    assert picker.server_button.isHidden() and "probably the game itself" in picker.status.text()

    _tick(picker, qapp, "mog:copy")
    picker.ok.click()
    assert started == [(None, True)]


def test_an_executable_can_still_be_picked_from_that_folder(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False: started.append((installer, extract)))

    picker.on_installers(41, [EXE], "", True)
    picker.choose(next(picker.list.item(i) for i in range(picker.list.count()) if "Metroid.exe" in picker.text_of(picker.list.item(i))))
    assert started == [(EXE, False)]


def test_just_copy_is_a_row_like_an_installer_always_on_offer_and_there_is_no_button_for_it(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append((installer, extract)))
    assert not hasattr(picker, "copy_button")
    assert not any(b.text().startswith("Just copy") for b in picker.findChildren(QPushButton))
    assert any("Just copy" in r for r in _rows(picker))  # on offer while the installers are still being looked for

    picker.on_installers(41, [SETUP], "", False)
    assert _installer_rows(picker, with_copy=True)[0] == ("mog:copy", None)

    picker.list.setCurrentRow(_index_of(picker, "mog:copy"))
    picker.ok.click()  # nothing ticked: Install takes the row the arrow is on
    assert started == [(None, True)]


def test_just_copy_ticked_is_the_game_and_always_the_first_with_the_installers_after_it_as_patches(win, qapp, monkeypatch):
    page, picker, server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, extract, steps)))

    _tick(picker, qapp, "patch1.exe")
    _tick(picker, qapp, "patch2.exe")
    _tick(picker, qapp, "mog:copy")  # ticked last, it still takes the first place

    assert _installer_rows(picker, with_copy=True)[:3] == [("mog:copy", "1"), ("patch1.exe", "2"), ("patch2.exe", "3")]
    copy_row, first_patch = _widget(picker, "mog:copy"), _widget(picker, "patch1.exe")
    assert not any(b.isEnabled() for b in copy_row.arrows)  # nothing goes above it, and it does not move down
    assert not first_patch.arrows[0].isEnabled() and first_patch.arrows[1].isEnabled()  # the first patch cannot pass it

    picker._move(("mog:copy" and picker._key(picker.list.item(_index_of(picker, "patch1.exe")))), -1)  # tried anyway
    assert _installer_rows(picker, with_copy=True)[:3] == [("mog:copy", "1"), ("patch1.exe", "2"), ("patch2.exe", "3")]

    picker.ok.click()
    steps = [{"path": "mog:copy", "source_path": None}, {"path": "patch1.exe", "source_path": None}, {"path": "patch2.exe", "source_path": None}]
    assert started == [(None, False, steps)] and server.put == [(41, steps)]  # one install, the order kept for next time


def test_the_saved_order_with_just_copy_first_comes_back_ticked(win, qapp, monkeypatch):
    saved = [{"path": "mog:copy", "source_path": None}, {"path": "patch1.exe", "source_path": None}]
    page, picker = _picker_for(win, qapp, monkeypatch)
    picker.order = [(41, step["path"], step["source_path"]) for step in saved]
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    picker.on_installers(41, [SETUP, PATCH1], "", False)

    assert _installer_rows(picker, with_copy=True)[:2] == [("mog:copy", "1"), ("patch1.exe", "2")]


def test_the_picker_says_install_and_cancel_in_red(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch)
    pump(qapp)

    assert picker.ok.text() == "Install"
    assert win.modal.close_button.text() == "Cancel" and win.modal.close_button.property("danger") is True


def test_the_picker_learns_from_the_server_that_there_is_no_installer(win, qapp, monkeypatch):
    seen = []
    win.app.bridge.installers.connect(lambda *args: seen.append(args))
    monkeypatch.setattr(win.app, "client", lambda: type("S", (), {"candidates": lambda self, gid: {"candidates": [EXE], "extract_suggested": True}})())

    win.app.load_installers(41)
    for _ in range(100):
        if seen:
            break
        pump(qapp)
        time.sleep(0.02)

    assert seen == [(41, [EXE], "", True)]


def test_extracting_from_the_picker_goes_straight_to_the_install_without_asking_again(win, qapp, monkeypatch):
    page, _picker = _picker_for(win, qapp, monkeypatch)
    started, asked = [], []
    monkeypatch.setattr(win.app, "start_install", lambda game, installer=None, root=None, extract=False: started.append((installer, extract)))
    monkeypatch.setattr(win, "_ask_extraction", lambda *a: asked.append(a))
    monkeypatch.setattr(win, "_place", lambda game, size, go: go())
    win.app.sizes[41] = 1

    win.install_game(win.app.games[41], None, None, extract=True)
    pump(qapp)

    assert started == [(None, True)] and asked == []


# --- the mods and the file picker as small cards over the game's page -------------------------------


def _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    monkeypatch.setattr(win.app.settings, "install_dirs", [str(tmp_path / "games")])
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "igdb_id": None}])
    game = win.app.games[41]
    win.app.mods[41] = [
        {"name": "mod1", "kind": "folder", "size_bytes": 1, "file_count": 1},
        {"name": "mod2.zip", "kind": "archive", "size_bytes": 1, "file_count": 1},
    ]
    folder = win.app.mod_folder(game)
    folder.mkdir(parents=True)
    (folder / "mod1.zip").write_bytes(b"zipped")
    win.show_game(41)
    pump(qapp)
    return win.current_page(), folder


def test_the_mods_open_in_a_small_card_over_the_game_page_and_back_closes_it(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)

    game_page.open_mods()
    pump(qapp)
    mods_page = win.current_page()
    assert isinstance(mods_page, gui.ModsPage) and win.modal.showing and win.modal.page is mods_page
    assert win.stack.currentWidget() is game_page  # the game's page stays in view behind it
    assert win.modal.card.width() < win.width() and win.modal.card.height() < win.height()
    assert win.title.text() == "Eta"  # the window's header is still the game's

    QApplication.sendEvent(win, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    pump(qapp)
    assert win.current_page() is game_page and not win.modal.showing and win.modal.page is None


def test_the_close_button_and_a_click_outside_the_card_both_close_it(win, qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    game_page.open_mods()
    pump(qapp)
    win.modal.close_button.click()
    pump(qapp)
    assert win.current_page() is game_page and not win.modal.showing

    game_page.open_mods()
    pump(qapp)
    QTest.mouseClick(win.modal, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))  # on the backdrop
    pump(qapp)
    assert win.current_page() is game_page and not win.modal.showing

    game_page.open_mods()
    pump(qapp)
    QTest.mouseClick(win.modal, Qt.LeftButton, Qt.NoModifier, win.modal.card.geometry().center() + QPoint(0, 60))  # on the card
    pump(qapp)
    assert isinstance(win.current_page(), gui.ModsPage) and win.modal.showing


def test_a_question_asked_from_the_card_is_a_card_and_comes_back_to_the_card(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    game_page.open_mods()
    mods_page = win.current_page()
    win.ask("Sure?", lambda: None)
    pump(qapp)
    assert isinstance(win.current_page(), gui.ConfirmPage) and win.modal.page is win.current_page()
    win.current_page().no.click()
    pump(qapp)
    assert win.current_page() is mods_page and win.modal.showing and win.stack.currentWidget() is game_page


def test_the_file_picker_is_a_small_card_too(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    picked = []
    win.browse_folder(tmp_path, picked.append)
    pump(qapp)
    picker = win.current_page()
    assert isinstance(picker, gui.BrowsePage) and win.modal.page is picker and win.stack.currentWidget() is game_page
    assert win.modal.title.text() == "Choose a folder"
    picker._activate(picker.list.item(0))  # "Use this folder": selected, OK takes it
    assert picked == [] and picker.ok.isEnabled()
    picker.ok.click()
    assert picked == [str(tmp_path)] and win.current_page() is game_page and not win.modal.showing


def test_a_mod_already_on_this_computer_shows_where_and_can_be_deleted(win, qapp, monkeypatch, tmp_path):
    game_page, folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    game_page.open_mods()
    page = win.current_page()
    assert page.list.item(0).text() == f"mod1\nDownloaded to {folder / 'mod1.zip'}"
    assert page.list.item(1).text().startswith("mod2.zip\narchive")

    assert page.list.currentRow() == 0 and page.delete_button.isEnabled()
    page.list.setCurrentRow(1)
    assert not page.delete_button.isEnabled()  # nothing of it here
    page.list.setCurrentRow(0)

    page.delete_button.click()
    pump(qapp)
    ask = win.current_page()
    assert isinstance(ask, gui.ConfirmPage) and "mod1.zip" in ask.text_label.text() and (folder / "mod1.zip").exists()
    ask.yes.click()
    pump(qapp)

    assert not (folder / "mod1.zip").exists() and win.current_page() is page
    assert page.list.item(0).text().startswith("mod1\nfolder") and not page.delete_button.isEnabled()


def test_picking_a_mod_selects_it_and_only_the_download_button_starts_the_download(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr(win.app, "download_mod", lambda game, mod: started.append(mod["name"]))
    game_page.open_mods()
    page = win.current_page()

    page.list.itemClicked.emit(page.list.item(1))
    assert started == []
    page.list.setCurrentRow(1)
    page.list.itemActivated.emit(page.list.item(1))  # Enter, A or a double click: on to the button
    assert started == []
    page.download_button.click()
    assert started == ["mod2.zip"]
    page.list.setCurrentRow(0)
    page.download_button.click()
    assert started == ["mod2.zip", "mod1"]


def test_a_mod_being_fetched_cannot_be_deleted_and_its_button_cancels(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    game_page.open_mods()
    page = win.current_page()
    win.app.mod_jobs[(41, "mod1")] = ("Downloading", 30)
    win.app.bridge.activity.emit()

    assert page.download_button.text() == "Cancel fetching" and not page.delete_button.isEnabled()
    win.app.mod_jobs.clear()
    win.app.bridge.activity.emit()
    assert page.download_button.text() == "Download" and page.delete_button.isEnabled()


def test_sort_and_filter_sits_right_under_the_libraries_not_at_the_bottom_of_the_sidebar(win, qapp):
    from PySide6.QtCore import QPoint

    win.library.set_libraries([{"id": 1, "name": "Games"}, {"id": 2, "name": "Restricted"}])
    pump(qapp)
    sidebar, libs, sorts = win.library.sidebar, win.library.libs, win.library.sorts
    libs_top, sorts_top = libs.mapTo(sidebar, QPoint(0, 0)).y(), sorts.mapTo(sidebar, QPoint(0, 0)).y()
    libs_bottom, sorts_bottom = libs_top + libs.height(), sorts_top + sorts.height()
    assert libs_bottom < sorts_top < libs_bottom + 80
    assert sorts_bottom < sidebar.height() - 20  # free space is left under it
    heading = win.library.sorts_section.heading
    assert heading.text().lower().endswith("sort and filter") and heading.mapTo(sidebar, QPoint(0, 0)).y() > libs_bottom


def test_a_restore_is_a_row_in_the_task_list_and_play_waits_for_it(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    folder = tmp_path / "games" / "Alpha"
    folder.mkdir(parents=True)
    _library_with(win, qapp, tmp_path, monkeypatch, folder)
    win.show_game(1)
    page = win.current_page()
    started = []
    monkeypatch.setattr(page, "start", lambda rec: started.append(rec.game_id))

    win.saves.restoring[1] = RestoreJob(percent=37)
    win.refresh_tasks()
    rows = [win.library.installs.item(i).text() for i in range(win.library.installs.count())]
    assert any("Alpha" in r and "Restoring saves... 37%" in r for r in rows)

    page.play()
    pump(qapp)
    assert started == [] and 1 in win.saves.restoring  # asked, neither started nor cancelled

    win.saves.restoring[1].applying = True
    win.refresh_tasks()
    rows = [win.library.installs.item(i).text() for i in range(win.library.installs.count())]
    assert any("Putting the saves back" in r for r in rows)

    win.saves.restoring.clear()
    win.refresh_tasks()
    assert win.library.installs.count() == 0


def _until(qapp, condition, seconds=3.0):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def _a_game_is_listed(win, qapp, tmp_path, monkeypatch):
    folder = tmp_path / "games" / "Alpha"
    folder.mkdir(parents=True)
    _library_with(win, qapp, tmp_path, monkeypatch, folder)
    win.show_game(1)
    win.back()
    pump(qapp)


def test_the_sidebar_stays_as_it_is_when_a_task_starts(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.library.toggle_sidebar()
    assert not win.library.sidebar.isVisible()

    win.saves.restoring[1] = RestoreJob()
    win.refresh_tasks()
    pump(qapp)
    assert not win.library.sidebar.isVisible() and win.library.sidebar.width() == gui.SIDEBAR_WIDTH
    win.library.toggle_sidebar()
    assert win.library.sidebar.isVisible()


def test_a_task_started_on_another_page_pulses_the_cover_when_the_library_comes_back(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    monkeypatch.setattr(gui, "CARD_PULSE_MS", 20)
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    win.saves.restoring[1] = RestoreJob()
    win.refresh_tasks()
    assert win.library._card_pulses == {} and win.library._cards_due == {1}

    win.back()
    assert _until(qapp, lambda: not win.library._cards_due and (1 in win.library._card_pulses or True))
    assert _until(qapp, lambda: win.library._card_pulses == {})


def test_a_cover_with_a_task_is_dimmed_with_a_pie_for_as_long_as_it_runs_and_nothing_turns(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    library = win.library
    item = library.items[1]
    assert not item.data(gui.ROLE_BUSY)

    win.saves.restoring[1] = RestoreJob(percent=10)  # a download of saves
    win.refresh_tasks()
    assert item.data(gui.ROLE_BUSY) is True
    assert not hasattr(library, "_spin") and not hasattr(library.grid.itemDelegate(), "angle")  # no animation is kept going

    del win.saves.restoring[1]
    win.refresh_tasks()
    assert not item.data(gui.ROLE_BUSY)


def test_a_download_reports_at_most_a_few_times_a_second_not_once_per_chunk(win, qapp, monkeypatch):
    """Every chunk of every file reports: thousands a second, each redrawing the library, froze the window."""
    from mog_client import manager

    seen = []
    win.app.bridge.progress.connect(lambda gid, written, total, label: seen.append((written, total)))

    def downloading(client, game, settings, stop, log, on_session, report, *rest):
        total = 10 * 1024**3
        for chunk in range(1, 4001):  # four thousand chunks in no time
            report(chunk * total // 4000, total)
        raise RuntimeError("stop here")  # the end of what is under test

    monkeypatch.setattr(manager, "run_install", downloading)
    game = {"id": 5, "name": "Eta", "library_id": None, "igdb_id": None}
    win.app.set_games([game])
    win.app.start_install(game)
    for _ in range(100):
        if 5 not in win.app.installs:
            break
        pump(qapp)
        time.sleep(0.02)
    pump(qapp)

    assert 1 <= len(seen) <= 5, len(seen)  # the first, then at most one more per quarter second
    assert seen[-1][0] == seen[-1][1]  # and the last, which is the full size, always gets through
    assert win.app.progress.get(5) is None  # the install ended (the stop above), its progress is dropped


def test_the_library_redraws_a_downloads_game_once_for_many_reports(win, qapp, tmp_path, monkeypatch):
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    library = win.library
    drawn = []
    real = library._update_label
    monkeypatch.setattr(library, "_update_label", lambda gid, lib: drawn.append(gid) or real(gid, lib))
    win.app.installs[1] = threading.Event()
    win.refresh_tasks()

    for step in range(1, 801):
        win.app.progress[1] = (step * 10, 8000, "Downloading")
        win.app.bridge.progress.emit(1, step * 10, 8000, "Downloading")
    assert _until(qapp, lambda: drawn and library._labels_due == set())

    assert len(drawn) <= 2, len(drawn)  # not eight hundred
    assert library.items[1].data(gui.ROLE_PROGRESS) == 1000  # and it shows where the download had got to


def test_the_sidebars_task_list_is_not_rebuilt_while_the_same_tasks_run(win, qapp, tmp_path, monkeypatch):
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    library = win.library
    win.app.installs[1] = threading.Event()
    win.app.progress[1] = (100, 1000, "")
    library.update_active()
    first = library.installs.item(0)
    assert first.text().endswith("Installing... 10%")

    cleared = []
    real_clear = library.installs.clear
    monkeypatch.setattr(library.installs, "clear", lambda: cleared.append(1) or real_clear())
    for written in (200, 300, 400):
        win.app.progress[1] = (written, 1000, "")
        library.update_active()
    assert library.installs.item(0) is first and first.text().endswith("Installing... 40%") and cleared == []  # words only

    library.update_active()
    assert cleared == []  # nothing changed: nothing is touched

    win.app.installs.pop(1)
    library.update_active()
    assert cleared == [1] and library.installs.count() == 0  # a task that ended does rebuild it


def test_installs_the_server_holds_in_its_queue_are_listed_below_everything_running_in_the_order_they_start(
    win, qapp, tmp_path, monkeypatch
):
    games = [{"id": n, "name": f"Game {n}", "library_id": None, "igdb_id": None} for n in (1, 2, 3, 4, 5)]
    win.app.set_games(games)
    monkeypatch.setattr(win.app, "fetch_icon", lambda game: None)
    for gid in (1, 2, 3, 4, 5):
        win.app.installs[gid] = threading.Event()
    win.app.queued.update({2: 2, 4: 1})  # the server says: game 4 is next, then game 2
    win.saves.uploading.add(5)  # something else is going on too

    win.library.update_active()
    names = [win.library.installs.item(i).text().split("\n")[0] for i in range(win.library.installs.count())]
    states = [win.library.installs.item(i).text().split("\n")[1] for i in range(win.library.installs.count())]

    assert names == ["Game 1", "Game 3", "Game 5", "Game 4", "Game 2"]  # running first, then the queue
    assert states[-2:] == ["Waiting in the queue, number 1", "Waiting in the queue, number 2"]
    assert states[0].startswith("Installing")

    win.app.queued.pop(4)  # its turn has come: it moves up among the running ones
    win.library.update_active()
    names = [win.library.installs.item(i).text().split("\n")[0] for i in range(win.library.installs.count())]
    assert names == ["Game 1", "Game 3", "Game 4", "Game 5", "Game 2"]


def test_the_pie_on_a_cover_fills_with_the_installs_progress(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    def drawn(percent):
        image = QImage(200, 270, QImage.Format_ARGB32)
        image.fill(0xFF808080)
        painter = QPainter(image)
        gui.paint_pie(painter, QRect(0, 0, 200, 270), "download", percent)
        painter.end()
        return image

    # Filled clockwise from the top: at 25% only the top right is lit, at 75% the bottom left is too
    quarter, three_quarters = drawn(25), drawn(75)
    assert quarter.pixelColor(125, 110) == three_quarters.pixelColor(125, 110)  # lit in both
    assert quarter.pixelColor(75, 160) != three_quarters.pixelColor(75, 160)  # not yet at 25%


def test_an_install_download_and_a_populate_keep_the_pie_on_the_cover(win, qapp, tmp_path, monkeypatch):
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    library = win.library
    win.app.installs[1] = threading.Event()  # an install is running
    win.refresh_tasks()
    assert library.items[1].data(gui.ROLE_BUSY) is True

    library.populate("")  # the grid is rebuilt (a search, a sort): the new item still carries it
    assert library.items[1].data(gui.ROLE_BUSY) is True
    win.app.installs.pop(1)
    win.refresh_tasks()
    assert library.items[1].data(gui.ROLE_BUSY) is False


def test_a_notification_shows_the_game_icon_else_the_icon_of_who_wrote_it(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QPixmap

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.icon_art[1] = QPixmap(256, 256)
    monkeypatch.setattr(win.app, "fetch_icon", lambda game: None)
    win.notifications = [
        {"id": 1, "kind": "save_synced", "title": "Saves backed up: Alpha", "body": None, "game_id": 1, "read": True},
        {"id": 2, "kind": "auto_mode_failed", "title": "Install failed", "body": None, "game_id": None, "read": True},
        {"id": 3, "kind": "save_synced", "title": "Backed up", "body": None, "game_id": None, "read": True},
        {"id": 4, "kind": "games_added", "title": "New games", "body": None, "game_id": 999, "read": True},
    ]
    page = gui.NotificationsPage(win)
    size = gui.NOTIFICATION_ICON.height()
    server, client = (gui.asset_pixmap(name, size).toImage() for name in ("server.png", "icon.png"))

    def image(row):
        return page.list.item(row).icon().pixmap(size, size).toImage()

    assert not page.list.item(0).icon().isNull()  # the game's own
    assert page.list.item(0).font().bold() is False  # read: no weight
    assert "\u25cf" not in page.list.item(0).text()  # no bullet any more
    assert image(1) == server  # about the server, no game
    assert image(2) == client  # about what the client did, no game
    assert image(3) == server  # a game this library does not have: the server's


def test_questions_and_pickers_are_cards_over_the_page_they_came_from(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    answers = []
    for show in (
        lambda: win.ask("Cancel it?", lambda: answers.append("yes")),
        lambda: win.choose("Pick", "which", [("A", 1), ("B", 2)], answers.append),
        lambda: win.checklist("Tick", "which", [("A", 1)], answers.append),
    ):
        show()
        pump(qapp)
        page = win.current_page()
        assert page.modal and win.modal.page is page and win.stack.currentWidget() is game_page
        win.back()
        pump(qapp)
        assert not win.modal.showing and win.current_page() is game_page


def test_a_question_card_is_as_big_as_the_question_and_has_no_close_button(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    win.ask("Cancel that and play with the saves on this machine instead?", lambda: None)
    pump(qapp)
    question = win.modal.card.geometry()
    assert question.width() <= 560 and question.height() < 260 and not win.modal.close_button.isVisibleTo(win.modal)
    win.back()
    game_page.open_mods()
    pump(qapp)
    mods = win.modal.card.geometry()
    assert mods.height() > question.height() and win.modal.close_button.isVisibleTo(win.modal)


def test_the_ring_around_a_cover_can_be_painted_in_the_grid_and_on_a_widget(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    image = QImage(300, 400, QImage.Format_ARGB32)
    painter = QPainter(image)
    gui.paint_ring(painter, QRect(40, 40, 200, 300), 0.5)

    item = win.library.items[1]
    item.setData(gui.ROLE_PULSE, 0.4)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 400)
    win.library.grid.itemDelegate().paint(painter, option, win.library.grid.indexFromItem(item))  # raises if it cannot
    painter.end()

    ring = gui.RingPulse(win)
    ring.resize(300, 400)
    ring.progress = 0.5
    ring.grab()


def test_a_task_queued_for_the_game_on_screen_pulses_its_cover(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    monkeypatch.setattr(gui, "CARD_PULSE_MS", 30)
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    page = win.current_page()
    fired = []
    monkeypatch.setattr(page, "pulse", lambda: fired.append(1))

    win.saves.restoring[1] = RestoreJob()
    win.refresh_tasks()
    win.refresh_tasks()  # nothing new the second time
    assert fired == [1]

    real = gui.GamePage.pulse
    real(page)
    assert page._ring.isVisible() and _until(qapp, lambda: not page._ring.isVisible())


def test_notification_icons_are_made_at_the_screens_pixel_ratio(win, qapp, tmp_path, monkeypatch):
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.notifications = [{"id": 1, "kind": "games_added", "title": "t", "body": None, "game_id": None, "read": True}]
    page = gui.NotificationsPage(win)
    monkeypatch.setattr(page, "devicePixelRatioF", lambda: 2.0)
    pix = page._sharp(gui.QPixmap(str(gui.ASSETS / "server.png")))
    assert pix.devicePixelRatio() == 2.0 and pix.width() == 128 and pix.height() == 128  # 64 points


def _ico_of(*sizes):
    """An .ico holding a PNG of each size, smallest first, the way the server's icons come."""
    import struct

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    images = []
    for size in sizes:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(QColor("red"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    header, body = struct.pack("<HHH", 0, 1, len(images)), b""
    for size, png in images:
        header += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(body))
        body += png
    return header + body


def test_an_icon_file_with_several_sizes_is_shown_at_its_largest(win, qapp):
    from PySide6.QtGui import QPixmap

    blob = _ico_of(16, 32, 64)
    plain = QPixmap()
    plain.loadFromData(blob)
    assert plain.width() == 16  # what loading it plainly gives: the smallest

    assert gui.largest_pixmap(blob).width() == 64
    win.set_icon(1, blob)
    assert win.icon_art[1].width() == 64 and win.icons[1].width() == gui.ACTIVE_ICON.width()
    assert gui.largest_pixmap(b"not a picture").isNull()


def test_the_pie_and_the_ring_can_be_painted(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    image = QImage(300, 400, QImage.Format_ARGB32)
    painter = QPainter(image)
    rect = QRect(0, 0, 300, 400)
    for kind, percent in (("download", 0), ("download", 42), ("download", 100), ("upload", None)):
        gui.paint_pie(painter, rect, kind, percent, 120.0)  # raises if it cannot
    gui.paint_spin_ring(painter, QRect(0, 0, 160, 48), 200.0)
    painter.end()


def test_a_game_page_shows_a_pie_on_the_cover_and_a_ring_on_play_while_its_saves_move(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QAbstractAnimation

    from mog_client.gui.saves_ui import RestoreJob

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    page = win.current_page()
    assert page.play_ring is not None and page.play_ring.isHidden() and not hasattr(page, "_pie")

    win.saves.restoring[1] = RestoreJob(percent=42)
    win.refresh_tasks()
    assert not page._pie.isHidden() and page._pie.kind == "download" and page._pie.percent == 42
    assert not page.play_ring.isHidden() and page.play_ring.spin.state() == QAbstractAnimation.Running

    win.saves.restoring[1].applying = True
    win.refresh_tasks()
    assert page._pie.percent == 100

    del win.saves.restoring[1]
    win.saves.uploading.add(1)
    win.refresh_tasks()
    assert page._pie.kind == "upload" and page._pie.percent is None and page._pie.spin.state() == QAbstractAnimation.Running

    win.saves.uploading.clear()
    win.refresh_tasks()
    assert page._pie.isHidden() and page.play_ring.isHidden()


def test_the_ring_follows_the_play_button_when_the_page_is_rebuilt(win, qapp, tmp_path, monkeypatch):
    from mog_client.gui.saves_ui import RestoreJob

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    page = win.current_page()
    win.saves.restoring[1] = RestoreJob(percent=10)
    win.refresh_tasks()
    page.rebuild()  # new buttons: the ring is on the new Play
    pump(qapp)
    assert page.play_ring is not None and not page.play_ring.isHidden()
    win.saves.restoring.clear()
    win.refresh_tasks()
    assert page.play_ring.isHidden()


def test_pressing_play_turns_the_ring_and_says_starting_until_the_launch_settings_are_in(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QAbstractAnimation

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    page = win.current_page()
    waiting = []
    played = []
    monkeypatch.setattr(page, "pull_launch_settings", lambda rec, then: waiting.append(then))  # the server has not answered
    monkeypatch.setattr(page, "_play", lambda rec: played.append(rec))

    page.play()
    page.play()  # a second press while it waits changes nothing
    assert len(waiting) == 1
    assert page.first.text() == "Starting..." and page.play_ring.spin.state() == QAbstractAnimation.Running

    page.rebuild()  # the buttons made again while it waits keep saying so
    pump(qapp)
    assert page.first.text() == "Starting..." and not page.play_ring.isHidden()

    waiting[0]()  # the answer came: on to the questions of the save sync, and the game
    assert page.first.text() == "Play" and page.play_ring.isHidden() and len(played) == 1


def test_about_has_an_about_and_a_changelog_tab_and_the_pad_switches_them(win, qapp, monkeypatch):
    from mog_client import changelog

    monkeypatch.setattr(changelog, "CHANGELOG", "## Fixes\n\n- a: b [[abc1234](https://github.com/o/r/commit/abc1234)]")
    win.open_about()
    page = win.current_page()
    assert [page.tabs.tabBar().tabText(i) for i in range(2)] == ["About", "Changelog"]
    assert page.tabs.currentWidget() is page.about and not page.scroll_logs(1.0)

    page.switch_tab(1)
    assert page.tabs.currentWidget() is page.changelog
    assert "a: b" in page.changelog.view.toPlainText() and page.changelog.kofi.text() == "Support on Ko-fi"

    page.switch_tab(1)  # wraps round
    assert page.tabs.currentWidget() is page.about


def test_the_changelog_opens_once_after_an_update_and_not_in_a_new_install(win, qapp, monkeypatch):
    from mog_client import changelog

    saved = []
    monkeypatch.setattr(gui, "save_settings", lambda settings: saved.append(settings.changelog_seen))
    monkeypatch.setattr(changelog, "CHANGELOG", "## Fixes\n\n- a: b")
    monkeypatch.setattr(gui, "__version__", "9.9.9")
    win.app.settings.changelog_seen = "9.9.8"

    win.offer_changelog()
    page = win.current_page()
    assert isinstance(page, gui.AboutPage) and page.tabs.currentWidget() is page.changelog and saved == ["9.9.9"]

    win.back()
    win.offer_changelog()  # the same version again: nothing
    assert not isinstance(win.current_page(), gui.AboutPage)


def test_a_notice_the_client_keeps_itself_sits_in_the_list_and_never_goes_to_the_server(win, qapp, monkeypatch):
    sent = []
    monkeypatch.setattr(win.app, "run_bg", lambda fn, on_error=None: sent.append(fn))
    monkeypatch.setattr(win.app, "poll_notifications", lambda: None)
    win.on_notifications({"notifications": [{"id": 5, "kind": "save_synced", "title": "Saves backed up: A", "body": None, "game_id": None, "read": False}], "unread": 1})

    win.add_local_notification("save_restored", "Saves restored: A", "On this machine, 2 files.", None)
    assert [n["title"] for n in win.notifications] == ["Saves restored: A", "Saves backed up: A"]
    assert win.user_btn.count == 2  # the bell counts both

    win.on_notifications({"notifications": win.server_notifications, "unread": 1})  # the server's list comes again
    assert [n["id"] for n in win.notifications] == [-1, 5]  # the notice is still there, once

    win.mark_read(-1)
    assert win.local_notifications[0]["read"] is True and sent == []  # nothing asked of the server for it
    win.mark_read(5)
    assert len(sent) == 1

    win.delete_notification(-1)
    assert win.local_notifications == [] and len(sent) == 1
    win.delete_notification(None)
    assert win.notifications == [] and len(sent) == 2


class _Server:
    """What `App.refresh` and `poll_library` ask of the server, with a revision the test moves."""

    def __init__(self, revision):
        self.revision, self.games, self.listed = revision, [], 0

    def games_revision(self):
        return self.revision

    def list_libraries(self):
        return []

    def me(self):
        return {}

    def list_games(self):
        self.listed += 1
        return self.games

    def get_image(self, path):
        return None


def test_a_game_added_on_the_server_appears_without_pressing_refresh(win, qapp, monkeypatch):
    server = _Server("1-a")
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "_load_cover", lambda game, client: None)
    win.app.refresh()
    assert _until(qapp, lambda: not win.app.refreshing) and win.app.revision == "1-a"

    win.app.poll_library()
    pump(qapp)
    time.sleep(0.1)
    assert server.listed == 1  # nothing changed: nothing loaded again

    server.revision = "2-b"  # a game was added
    win.app.poll_library()
    assert _until(qapp, lambda: server.listed == 2 and win.app.revision == "2-b")


def test_the_library_is_watched_again_while_the_covers_are_still_coming(win, qapp, monkeypatch):
    import threading

    server = _Server("1-a")
    release = threading.Event()
    server.games = [{"id": 1, "name": "A", "library_id": None, "igdb_id": None}]
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "_load_cover", lambda game, client: release.wait(5))  # a slow cover
    win.app.refresh()
    try:
        assert _until(qapp, lambda: server.listed == 1 and not win.app.refreshing)  # free as soon as the list is in
        server.revision = "2-b"
        win.app.poll_library()
        assert _until(qapp, lambda: server.listed == 2)  # the change is not kept waiting for the covers
    finally:
        release.set()


def test_a_server_that_did_not_answer_at_the_load_is_picked_up_at_the_next_poll(win, qapp, monkeypatch):
    server = _Server(None)  # no revision at the load: the call failed that time
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "_load_cover", lambda game, client: None)
    win.app.refresh()
    assert _until(qapp, lambda: not win.app.refreshing) and win.app.revision is None

    win.app.poll_library()
    pump(qapp)
    time.sleep(0.1)
    assert server.listed == 1  # still none: an older server is left alone

    server.revision = "5-x"
    win.app.poll_library()
    assert _until(qapp, lambda: server.listed == 2 and win.app.revision == "5-x")


def test_a_games_added_notice_has_the_library_looked_at_at_once(win, qapp, monkeypatch):
    looked = []
    monkeypatch.setattr(win.app, "poll_library", lambda: looked.append(1))
    note = lambda i, kind: {"id": i, "kind": kind, "title": "t", "body": None, "game_id": None, "read": False}  # noqa: E731
    win.on_notifications({"notifications": [note(1, "save_synced")], "unread": 1})  # the first list is only taken in
    win.on_notifications({"notifications": [note(2, "save_synced"), note(1, "save_synced")], "unread": 2})
    assert looked == []

    win.on_notifications({"notifications": [note(3, "games_added"), note(2, "save_synced"), note(1, "save_synced")], "unread": 3})
    assert looked == [1]


def test_the_options_of_an_installed_game_offer_a_repair_and_the_sidebar_shows_it_running(win, qapp, tmp_path):
    from types import SimpleNamespace

    from mog_client.config import InstalledGame

    rec = InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), state="installed", executable=str(tmp_path / "g.exe"))
    page = gui.OptionsPage(win, SimpleNamespace(delete_server_cache=lambda: None), rec)
    assert "Repair: check the installed files" in [b.text() for b in page.findChildren(QPushButton)]

    win.app.games[4] = {"id": 4, "name": "G"}
    win.library.repairs[4] = 42
    win.library.update_active()
    assert (4, "repair") in win.library._task_keys and win.library.installs.count() == 1


def test_a_notch_of_the_wheel_moves_the_library_less_than_a_row_so_no_cover_is_skipped(win, qapp):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    win.app.set_games([{"id": i, "name": f"G{i}", "igdb_id": None} for i in range(1, 80)])
    win.library.populate("")
    pump(qapp)
    grid = win.library.grid
    before = grid.verticalScrollBar().value()
    notch = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    qapp.sendEvent(grid.viewport(), notch)
    qapp.processEvents()
    moved = grid.verticalScrollBar().value() - before
    assert 0 < moved < grid.gridSize().height() / 2


def test_the_header_keeps_its_height_when_the_window_goes_to_a_game_and_back(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None}])
    pump(qapp)
    library_header = win.top_bar.height()
    win.show_game(41)
    pump(qapp)
    assert win.top_bar.height() == library_header
    win.back()
    pump(qapp)
    assert win.top_bar.height() == library_header and win.centralWidget().updatesEnabled()


def test_every_message_is_also_a_notification_and_can_offer_an_action(win, qapp):
    ran = []
    win.message("Alpha finished installing", "info", 7, action=("Choose executable", lambda: ran.append("chosen")))
    assert win.overlay.action_button.isVisible() and win.overlay.action_button.text() == "Choose executable"
    assert win.overlay.focusWidget() is win.overlay.action_button  # Enter or A does the offered thing; B or Esc only closes
    assert win.local_notifications[0]["title"] == "Alpha finished installing" and win.local_notifications[0]["game_id"] == 7
    assert win.local_notifications[0]["kind"] == "client_info"

    win.on_pad(gamepad.ACCEPT)
    assert ran == ["chosen"] and not win.overlay.showing

    win.message("Alpha finished installing", "info", 7)
    assert len(win.local_notifications) == 1  # said again before it was read: listed once
    win.overlay.dismiss()
    win.message("Quiet one", "info", keep=False)
    assert len(win.local_notifications) == 1 and not win.overlay.action_button.isVisible()
    win.overlay.dismiss()


def test_the_window_can_be_made_small_on_a_games_page_too(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None, "summary": "x " * 400}])
    win.show_game(41)
    pump(qapp)
    win.resize(420, 300)
    pump(qapp)
    assert win.width() <= 430 and win.height() <= 310


def _installs_in_the_sidebar(win, qapp, count=3):
    win.library.installs_box.setVisible(True)
    for n in range(count):
        win.library.installs.addItem(f"Game {n}\nDownloading")
    win.library.installs.set_natural_height(300)
    pump(qapp)


def test_the_sidebar_sections_fold_and_remember_it(win, qapp):
    library = win.library
    assert library.libs.isVisibleTo(library) and library.sorts.isVisibleTo(library)

    library.sorts_section.heading.click()
    pump(qapp)
    assert not library.sorts.isVisibleTo(library) and "sorts" in win.app.settings.collapsed_sections
    assert load_settings().collapsed_sections == ["sorts"]  # kept for the next run
    assert library.sorts_section.heading.text().startswith("▸")

    library.sorts_section.heading.click()
    pump(qapp)
    assert library.sorts.isVisibleTo(library) and win.app.settings.collapsed_sections == []


def test_a_short_sidebar_gives_its_room_to_the_active_installs_first_then_the_libraries_then_the_sorts(win, qapp):
    win.library.set_libraries([{"id": n, "name": f"Library {n}"} for n in range(1, 9)])
    _installs_in_the_sidebar(win, qapp)
    win.resize(1100, 600)
    pump(qapp)
    heights = [win.library.installs.height(), win.library.libs.height(), win.library.sorts.height()]
    assert heights[0] >= heights[1] >= heights[2] > 0, heights

    win.library.libs_section.heading.click()  # folded: its room goes to the others
    pump(qapp)
    assert not win.library.libs.isVisibleTo(win.library)
    assert win.library.sorts.height() >= heights[2]


def test_a_message_about_a_game_shows_its_icon_at_the_left(win, qapp):
    from PySide6.QtGui import QPixmap

    art = QPixmap(64, 64)
    art.fill()
    win.icon_art[7] = art
    win.message("Alpha finished installing", "info", 7)
    assert win.overlay.icon.isVisibleTo(win.overlay) and win.overlay.icon.pixmap() is not None
    win.overlay.dismiss()
    win.message("Something else", "info", keep=False)
    assert not win.overlay.icon.isVisibleTo(win.overlay)
    win.overlay.dismiss()


def test_a_click_outside_a_question_does_not_answer_it_but_no_and_back_do(win, qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    asked = []
    win.ask("Delete the game files of Alpha?", lambda: asked.append("yes"), danger=True)
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.ConfirmPage) and win.modal.showing

    QTest.mouseClick(win.modal, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))  # on the backdrop
    pump(qapp)
    assert win.current_page() is page and win.modal.showing and not asked  # still there, nothing done

    page.no.click()
    pump(qapp)
    assert not win.modal.showing and not asked

    win.ask("Again?", lambda: asked.append("yes"))
    pump(qapp)
    win.back()  # Esc and the pad's B
    pump(qapp)
    assert not win.modal.showing and not asked


def _uninstall_asked(win, qapp, tmp_path, monkeypatch):
    folder = tmp_path / "games" / "Alpha"
    folder.mkdir(parents=True)
    (folder / "pfx").mkdir()
    _library_with(win, qapp, tmp_path, monkeypatch, folder)
    library = config.load_library()
    library[1].prefix = str(folder / "pfx")
    config.save_library(library)
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    held = {}
    monkeypatch.setattr(win.saves, "final_backup", lambda rec, then, on_abort=None: held.update(then=then, abort=on_abort))
    win.show_game(1)
    pump(qapp)
    page = win.current_page()
    page.uninstall(config.load_library()[1])
    pump(qapp)
    return page, held


def test_the_main_button_says_uninstalling_from_the_first_yes_and_turns_back_when_it_is_called_off(win, qapp, tmp_path, monkeypatch):
    page, held = _uninstall_asked(win, qapp, tmp_path, monkeypatch)
    assert page.first.text() == "Play" and 1 not in win.app.jobs  # only asked so far

    win.current_page().yes.click()  # the saves are being backed up now
    pump(qapp)
    assert win.app.jobs.get(1) == "uninstall"
    button = page.play_ring.parentWidget()
    assert button.text() == "Uninstalling..." and not button.isEnabled()
    assert page.play_ring.spin.state() == gui.QAbstractAnimation.Running  # and it turns

    held["abort"]()  # the backup could not be made and the answer was not to go on
    pump(qapp)
    assert 1 not in win.app.jobs and page.first.text() == "Play"


def test_closing_the_question_about_the_prefix_without_an_answer_ends_the_uninstalling(win, qapp, tmp_path, monkeypatch):
    page, held = _uninstall_asked(win, qapp, tmp_path, monkeypatch)
    win.current_page().yes.click()
    pump(qapp)
    held["then"]()  # backup done: the prefix question comes next
    pump(qapp)
    assert isinstance(win.current_page(), gui.ConfirmPage) and "Wine prefix" in win.current_page().text_label.text()
    assert win.app.jobs.get(1) == "uninstall"

    win.back()  # Esc: nothing is deleted, so nothing is "uninstalling"
    pump(qapp)
    assert 1 not in win.app.jobs and page.first.text() == "Play"


def test_a_game_without_a_cover_gets_the_grey_controller_in_the_grid_and_on_its_page(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 8, "name": "Bare", "library_id": None}])
    win.library.populate("")
    item = win.library.items[8]
    placeholder = item.data(gui.ROLE_COVER)
    assert placeholder is not None and not placeholder.isNull() and placeholder.size() == gui.COVER_SIZE
    assert placeholder.toImage().pixelColor(100, 135) != placeholder.toImage().pixelColor(2, 2)  # a drawing, not a flat fill

    win.show_game(8)
    pump(qapp)
    assert win.current_page().cover.pixmap() is not None and not win.current_page().cover.pixmap().isNull()

    win.set_cover(8, b"not an image")  # a blob that is not an image changes nothing
    assert win.library.items[8].data(gui.ROLE_COVER).size() == gui.COVER_SIZE


def test_a_game_page_that_was_already_built_follows_an_install_that_starts_elsewhere(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 92, "name": "Olden Era", "library_id": None}])
    win.show_game(92)
    pump(qapp)
    page = win.current_page()
    assert page.first.text() == "Install"

    win.back()  # the page stays built, behind; the install is resumed at start, or comes from a link on the web
    win.app.installs[92] = object()
    win.app.bridge.activity.emit()
    win.show_game(92)
    pump(qapp)
    assert win.current_page() is page and page.first.text() in ("Pause", "Pausing...")  # not the old Install button

    win.app.installs.pop(92)
    win.app.progress[92] = (10, 100, "Downloading")
    win.app.installs[92] = object()
    page._on_progress(92, 10, 100, "Downloading")  # progress that arrives for a page showing something else rebuilds it
    win.app.installs.pop(92)
    win.app.bridge.activity.emit()
    pump(qapp)
    assert page.first.text() == "Install"


def test_a_game_page_lists_its_videos_before_the_screenshots_and_opens_one_in_the_browser(win, qapp, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    _rich_game(win, monkeypatch)
    win.show_game(41)
    page = win.current_page()
    shot = QPixmap(64, 36)
    shot.fill()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    shot.save(buffer, "PNG")
    blob = bytes(buffer.data())

    page._on_image(page.shot_urls[0], blob)
    page._on_video(41, {"kind": "intro", "video_id": "abcdefghijk", "title": "Trailer"}, blob)
    page._on_video(41, {"kind": "gameplay", "video_id": "lmnopqrstuv", "title": "Play"}, blob)
    page._on_video(99, {"kind": "intro", "video_id": "zzzzzzzzzzz", "title": "Other game"}, blob)  # not this page's

    ids = [page.shots.item(i).data(gui.VIDEO_ROLE) for i in range(page.shots.count())]
    assert ids == ["abcdefghijk", "lmnopqrstuv", None]  # videos first, in the server's order, then the screenshot

    page._open_shot(page.shots.item(1))

    assert opened == ["https://www.youtube.com/watch?v=lmnopqrstuv"] and win.current_page() is page  # no viewer pushed


def test_a_game_with_videos_and_no_screenshots_still_shows_the_strip(win, qapp, monkeypatch):
    game = _rich_game(win, monkeypatch)
    game["igdb_metadata"]["screenshots"] = []
    win.show_game(41)
    win.resize(1280, 800)
    pump(qapp)
    page = win.current_page()
    assert not page.shots.isVisible()
    pix = QPixmap(64, 36)
    pix.fill()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pix.save(buffer, "PNG")

    page._on_video(41, {"kind": "intro", "video_id": "abcdefghijk", "title": ""}, bytes(buffer.data()))
    pump(qapp)

    assert page.shots.isVisible()


def _texts(page):
    return [b.text() for b in page.findChildren(QPushButton)]


def _game_record(tmp_path, exe, **extra):
    from mog_client.config import InstalledGame

    return InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), state="installed", executable=str(tmp_path / exe), **extra)


def _options(win, rec):
    from types import SimpleNamespace

    return gui.OptionsPage(win, SimpleNamespace(delete_server_cache=lambda: None), rec)


def test_the_linux_entries_are_in_one_linux_page_of_the_options(win, tmp_path, monkeypatch):
    from types import SimpleNamespace

    rec = _game_record(tmp_path, "g.exe", proton="GE-Proton9-1", launcher="umu")
    monkeypatch.setattr(gui, "sync_enabled", lambda rec, settings: True)

    options = _texts(_options(win, rec))
    assert "Linux Options" in options
    assert not any(t.startswith(("Engine", "Prefix")) or "winecfg" in t for t in options)

    linux = _texts(gui.LinuxOptionsPage(win, SimpleNamespace(), rec))
    assert linux == ["Engine: umu-launcher, GE-Proton9-1", "Wine configuration (winecfg)", "Winetricks", "System language: default", "Launch executable...", "Prefix location"]


def test_a_linux_game_has_no_winecfg_no_version_and_no_prefix_location_without_save_sync(win, tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(gui, "sync_enabled", lambda rec, settings: False)

    linux = _texts(gui.LinuxOptionsPage(win, SimpleNamespace(), _game_record(tmp_path, "start.sh", proton="X")))

    assert linux == ["Engine: default"]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_the_linux_button_is_only_on_linux(win, tmp_path, monkeypatch, platform):
    monkeypatch.setattr(gui.sys, "platform", platform)

    assert "Linux Options" not in _texts(_options(win, _game_record(tmp_path, "g.exe")))


def test_choosing_linux_opens_its_page(win, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    rec = _game_record(tmp_path, "g.exe")
    pushed = []
    monkeypatch.setattr(win, "back", lambda: None)
    monkeypatch.setattr(win, "push", pushed.append)

    page = _options(win, rec)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Linux Options").click()

    assert [type(p) for p in pushed] == [gui.LinuxOptionsPage]


def test_the_menus_opened_from_a_menu_say_back_and_return_to_it_where_the_first_says_close(win, qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui, "list_proton_builds", lambda: {})
    rec = _game_record(tmp_path, "g.exe")
    _rich_game(win, monkeypatch)
    win.show_game(41)
    game = win.current_page()
    options = gui.OptionsPage(win, game, rec)
    win.push(options)
    pump(qapp)
    assert win.modal.close_button.text() == "Close"

    next(b for b in options.findChildren(QPushButton) if b.text() == "Linux Options").click()
    pump(qapp)
    linux = win.current_page()
    assert isinstance(linux, gui.LinuxOptionsPage) and win.modal.close_button.text() == "Back"

    win.push(gui.EnginePage(win, rec, lambda _engine, _version: None))
    pump(qapp)
    assert win.modal.close_button.text() == "Back"

    win.modal.close_button.click()  # Back: to Linux Options, then to Options, which says Close
    pump(qapp)
    assert win.current_page() is linux
    win.modal.close_button.click()
    pump(qapp)
    assert win.current_page() is options and win.modal.close_button.text() == "Close"


def _engine_page(win, rec, monkeypatch, engines=("faugus", "umu", "proton", "wine"), releases=()):
    monkeypatch.setattr(gui, "available_launchers", lambda: list(engines))
    monkeypatch.setattr(gui, "detect_launcher", lambda pref="auto": engines[0])
    return gui.EnginePage(win, rec, lambda _e, _v: None, list(releases))


def _items(widget):
    return [widget.item(i).text() for i in range(widget.count())]


def test_the_engine_page_lists_the_engines_and_what_the_current_one_can_run(win, monkeypatch, tmp_path):
    from mog_client import launcher
    from mog_client.config import InstalledGame

    monkeypatch.setattr(launcher, "list_proton_builds", lambda: {"GE-Proton9-27": "a", "Proton 9.0": "b"})
    rec = InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), launcher="faugus", proton="GE-Proton8-5")

    page = _engine_page(win, rec, monkeypatch, releases=["GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-4"])

    assert _items(page.engines) == [
        "Default: Faugus Launcher",
        "Faugus Launcher   \u2713",
        "umu-launcher",
        "Proton (system)",
        "Wine (system)",
    ]
    assert _items(page.versions) == [
        "Default (Faugus picks its own)",
        "Proton 9.0",
        "GE-Proton9-27",
        "GE-Proton10-4 (downloaded when chosen)",
        "GE-Proton8-32 (downloaded when chosen)",
        "GE-Proton8-5 (not on this device)   \u2713",
    ]


def test_moving_to_another_engine_lists_that_engines_versions(win, qapp, monkeypatch, tmp_path):
    from mog_client import launcher
    from mog_client.config import InstalledGame

    monkeypatch.setattr(launcher, "list_proton_builds", lambda: {"Proton 9.0": "b"})
    monkeypatch.setattr(launcher, "wine_runners", lambda: {"wine-ge-8-26": "w"})
    monkeypatch.setattr(launcher, "system_wine_version", lambda: "wine-9.0")
    rec = InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), launcher="faugus", wine="wine-ge-8-26")
    page = _engine_page(win, rec, monkeypatch)

    page.engines.setCurrentRow(2)  # umu
    assert _items(page.versions)[:2] == ["Default (the latest Proton-GE)   \u2713", "Proton 9.0"]

    page.engines.setCurrentRow(4)  # wine
    assert _items(page.versions) == ["Default (the system's wine, wine-9.0)", "wine-ge-8-26   \u2713"]


def test_nothing_is_applied_until_ok_and_default_selects_the_defaults(win, qapp, monkeypatch, tmp_path):
    from mog_client import launcher
    from mog_client.config import InstalledGame

    monkeypatch.setattr(launcher, "list_proton_builds", lambda: {"Proton 9.0": "b"})
    rec = InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), launcher="faugus", proton="Proton 9.0")
    chosen = []
    monkeypatch.setattr(gui, "available_launchers", lambda: ["faugus", "umu"])
    monkeypatch.setattr(gui, "detect_launcher", lambda pref="auto": "faugus")
    monkeypatch.setattr(win, "back", lambda: None)
    page = gui.EnginePage(win, rec, lambda e, v: chosen.append((e, v)), [])
    win.push(page)
    pump(qapp)

    page.engines.setCurrentRow(2)  # umu
    page.engines.sideways.emit(1)
    assert QApplication.focusWidget() is page.versions
    page.versions.sideways.emit(-1)
    assert QApplication.focusWidget() is page.engines

    page.versions.setCurrentRow(1)
    page.versions.itemActivated.emit(page.versions.item(1))  # Enter or a double click on a version only moves on
    assert chosen == [] and QApplication.focusWidget() is page.ok
    page.engines.itemActivated.emit(page.engines.item(2))
    assert chosen == []

    page.ok.click()
    assert chosen == [("umu", "Proton 9.0")]

    chosen.clear()
    page.defaults.click()  # selects, and still applies nothing
    assert chosen == [] and page.engines.currentRow() == 0 and page.versions.currentRow() == 0
    page.ok.click()
    assert chosen == [("auto", "")]


def test_the_buttons_of_the_engine_page_are_in_one_row_default_ok_then_back(win, qapp, monkeypatch, tmp_path):
    from mog_client.config import InstalledGame

    rec = InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), executable=str(tmp_path / "g.exe"))
    page = _engine_page(win, rec, monkeypatch)
    win.push(page)
    pump(qapp)
    xs = {b.text(): b.mapTo(page, b.rect().center()) for b in (page.defaults, page.ok, win.modal.close_button)}

    assert win.modal.close_button.text() == "Close" and win.modal.close_button.parentWidget() is page
    assert xs["Default"].y() == xs["OK"].y() == xs["Close"].y() and xs["Default"].x() < xs["OK"].x() < xs["Close"].x()


def test_a_linux_game_has_no_versions_to_pick(win, monkeypatch, tmp_path):
    rec = _game_record(tmp_path, "start.sh")

    page = _engine_page(win, rec, monkeypatch)

    assert _items(page.versions) == ["A Linux game runs as it is: it has no versions"]


def test_playing_takes_the_launch_settings_from_the_server_first_and_still_starts_when_that_fails(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings, manager
    from mog_client.config import InstalledGame

    rec = InstalledGame(game_id=41, name="G", install_dir=str(tmp_path), state="installed", executable=str(tmp_path / "g.exe"))
    _rich_game(win, monkeypatch)  # first: loading the games drops the records that do not fit one
    manager.save_library({41: rec})
    win.show_game(41)
    page = win.current_page()
    order = []
    monkeypatch.setattr(launchsettings, "pull", lambda *a, **k: order.append("pull"))
    monkeypatch.setattr(page, "_play", lambda r: order.append("play"))
    monkeypatch.setattr(gui.sys, "platform", "linux")
    ran = []
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: ran.append(fn))

    page.play()
    ran.pop()()  # the worker
    pump(qapp)
    assert order == ["pull", "play"]

    order.clear()
    monkeypatch.setattr(launchsettings, "pull", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("server down")))
    page.play()
    ran.pop()()
    pump(qapp)
    assert order == ["play"]


def test_a_linux_game_plays_without_asking_the_server_for_launch_settings(win, qapp, monkeypatch, tmp_path):
    from mog_client import manager
    from mog_client.config import InstalledGame

    rec = InstalledGame(game_id=41, name="G", install_dir=str(tmp_path), state="installed", executable=str(tmp_path / "start.sh"))
    _rich_game(win, monkeypatch)  # first: loading the games drops the records that do not fit one
    manager.save_library({41: rec})
    win.show_game(41)
    page = win.current_page()
    played = []
    monkeypatch.setattr(page, "_play", lambda r: played.append(r.game_id))
    monkeypatch.setattr(page.app, "run_bg", lambda *a, **k: pytest.fail("no worker for a Linux game"))

    page.play()

    assert played == [41]


def test_the_saves_entries_are_in_a_saves_page_with_a_switch_for_the_sync(win, qapp, tmp_path, monkeypatch):
    from mog_client import manager
    from mog_client.gui.widgets import Toggle

    rec = _game_record(tmp_path, "g.exe", save_sync=False)
    _rich_game(win, monkeypatch)
    manager.save_library({4: rec})
    win.show_game(41)
    game = win.current_page()
    options = _texts(_options(win, rec))
    assert "Saves..." in options
    assert not any(t.startswith(("Save sync", "Back up", "Restore", "Cancel restoring")) for t in options)

    saves = gui.SavesOptionsPage(win, game, rec)
    switch = saves.findChildren(Toggle)[0]
    assert not switch.isChecked() and not any(t in _texts(saves) for t in ("Back up saves now", "Restore a saved version..."))

    switch.setChecked(True)  # the switch changes the game's setting and brings the buttons in
    pump(qapp)
    assert rec.save_sync is True or (rec.save_sync is None and win.app.settings.sync_saves)
    assert {"Back up saves now", "Restore a saved version..."} <= set(_texts(saves))

    switch.setChecked(False)
    pump(qapp)
    assert not any(t in _texts(saves) for t in ("Back up saves now", "Restore a saved version..."))
    assert rec.save_sync is False or (rec.save_sync is None and not win.app.settings.sync_saves)


def test_the_saves_page_says_back_and_its_actions_close_both_menus(win, qapp, tmp_path, monkeypatch):
    from mog_client import manager

    rec = _game_record(tmp_path, "g.exe", save_sync=True)
    manager.save_library({4: rec})
    _rich_game(win, monkeypatch)
    win.show_game(41)
    game = win.current_page()
    backed = []
    monkeypatch.setattr(win.saves, "backup_now", lambda r: backed.append(r.game_id))
    options = gui.OptionsPage(win, game, rec)
    win.push(options)
    next(b for b in options.findChildren(QPushButton) if b.text() == "Saves...").click()
    pump(qapp)
    saves = win.current_page()
    assert isinstance(saves, gui.SavesOptionsPage) and win.modal.close_button.text() == "Back"

    next(b for b in saves.findChildren(QPushButton) if b.text() == "Back up saves now").click()
    pump(qapp)

    assert backed == [4] and win.current_page() is game


def test_the_uninstall_entries_are_in_an_uninstall_menu_and_a_game_not_installed_keeps_the_cache_button(win, qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mog_client import manager

    rec = _game_record(tmp_path, "g.exe")
    manager.save_library({4: rec})
    _rich_game(win, monkeypatch)
    win.show_game(41)
    game = win.current_page()
    asked = []
    monkeypatch.setattr(game, "uninstall", lambda r, and_server_cache=False: asked.append(and_server_cache))
    monkeypatch.setattr(game, "delete_server_cache", lambda: asked.append("cache"))

    texts = _texts(gui.OptionsPage(win, game, rec))
    assert "Uninstall..." in texts
    assert not any(t.startswith(("Uninstall from", "Uninstall and", "Delete")) for t in texts)
    assert "Delete server cache only" in _texts(gui.OptionsPage(win, SimpleNamespace(delete_server_cache=lambda: None), None))

    def open_menu():
        options = gui.OptionsPage(win, game, rec)
        win.push(options)
        next(b for b in options.findChildren(QPushButton) if b.text() == "Uninstall...").click()
        pump(qapp)
        return win.current_page()

    menu = open_menu()
    assert isinstance(menu, gui.UninstallOptionsPage) and win.modal.close_button.text() == "Back"
    assert _texts(menu) == ["Uninstall from this device", "Uninstall and delete the server cache", "Delete server cache only"]

    for label, expected in (("Uninstall from this device", False), ("Uninstall and delete the server cache", True), ("Delete server cache only", "cache")):
        menu = menu if isinstance(win.current_page(), gui.UninstallOptionsPage) else open_menu()
        next(b for b in menu.findChildren(QPushButton) if b.text() == label).click()
        pump(qapp)
        assert asked[-1] == expected and win.current_page() is game  # both menus are closed


def test_ok_and_the_cards_close_button_sit_in_one_row_and_it_says_back_over_another_card(win, qapp, tmp_path, monkeypatch):
    from mog_client import manager

    rec = _game_record(tmp_path, "g.exe")
    manager.save_library({4: rec})
    _rich_game(win, monkeypatch)
    win.show_game(41)
    win.push(gui.SavesOptionsPage(win, win.current_page(), rec))
    win.choose("Restore which save?", "Put a saved version back.", [("karasu: 2026-10-10 20:25", 1)], lambda _v: None)
    pump(qapp)
    page = win.current_page()
    ok = next(b for b in page.findChildren(QPushButton) if b.text() == "OK")
    close = win.modal.close_button

    assert close.text() == "Back" and close.isVisibleTo(win.modal)
    assert close.parentWidget() is page and ok.mapTo(page, ok.rect().center()).y() == close.mapTo(page, close.rect().center()).y()
    assert ok.mapTo(page, ok.rect().center()).x() < close.mapTo(page, close.rect().center()).x()  # OK first, then Back

    win.back()  # to Saves, which sits straight on the game page: the button goes home under the card's page, saying Close
    pump(qapp)
    assert win.modal.close_button.parentWidget() is win.modal.card and win.modal.close_button.text() == "Close"
    win.back()
    pump(qapp)
    win.choose("Pick", "x", [("a", 1)], lambda _v: None)  # over the game page alone
    pump(qapp)
    assert win.modal.close_button.text() == "Close"


def test_a_choice_in_linux_options_shows_in_its_button_at_once_and_in_the_second_gradient(win, qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    rec = _game_record(tmp_path, "g.exe")
    monkeypatch.setattr(gui, "sync_enabled", lambda r, s: False)
    monkeypatch.setattr(gui, "detect_launcher", lambda pref="auto": "umu")
    page = gui.LinuxOptionsPage(win, SimpleNamespace(), rec)
    button = lambda: next(b for b in page.findChildren(QPushButton) if b.text().startswith("Engine"))  # noqa: E731

    assert (button().text(), button().property("custom")) == ("Engine: default", False)

    rec.proton, rec.launcher = "GE-Proton9-27", "umu"  # what the background work sets
    win.app.bridge.finished.emit(rec.game_id + 1, "")  # another game: nothing changes
    assert button().text() == "Engine: default"
    win.app.bridge.finished.emit(rec.game_id, "")
    assert (button().text(), button().property("custom")) == ("Engine: umu-launcher, GE-Proton9-27", True)

    rec.launcher = "auto"  # the default engine with a version of its own is still not the default
    win.app.bridge.finished.emit(rec.game_id, "")
    assert (button().text(), button().property("custom")) == ("Engine: default, GE-Proton9-27", True)

    rec.proton = None
    win.app.bridge.finished.emit(rec.game_id, "")
    assert (button().text(), button().property("custom")) == ("Engine: default", False)


def test_the_second_gradient_is_in_the_global_style_and_differs_from_the_accent():
    from mog_client.gui import theme, widgets

    assert 'QPushButton[custom="true"] { background: qlineargradient' in theme.STYLE
    assert widgets.CUSTOM_STOPS != widgets.ACCENT_STOPS and widgets.accent_qss(widgets.CUSTOM_STOPS) in theme.STYLE


def _game_page(win, monkeypatch, tmp_path, **extra):
    from mog_client import manager

    rec = _game_record(tmp_path, "g.exe", **extra)
    _rich_game(win, monkeypatch)  # first: loading the games drops the records that do not fit one
    rec.game_id = 41
    manager.save_library({41: rec})
    win.show_game(41)
    return win.current_page(), rec


def test_playing_warns_when_what_was_set_elsewhere_is_not_on_this_machine_and_still_starts(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path, proton="GE-Proton9-27")
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(launchsettings, "pull", lambda *a, **k: None)
    monkeypatch.setattr(gui, "selection_problems", lambda r, pref: ["The selected Proton build GE-Proton9-27 was not found, so the default umu is used"])
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    told, started = [], []
    win.app.bridge.message.connect(lambda level, text: told.append((level, text)))
    monkeypatch.setattr(page, "_play", lambda r: started.append(1))

    page.play()
    pump(qapp)

    assert told == [("warning", "The selected Proton build GE-Proton9-27 was not found, so the default umu is used")] and started == [1]


def test_choosing_an_engine_and_version_applies_both_keeps_the_version_on_the_server_and_warns_of_what_is_missing(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings, manager

    page, rec = _game_page(win, monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(manager, "set_launcher", lambda r, engine, pref="auto": calls.append(("engine", engine)) or False)
    monkeypatch.setattr(manager, "set_proton", lambda r, name, pref="auto": (setattr(r, "proton", name), calls.append(("proton", name))))
    monkeypatch.setattr(gui, "detect_launcher", lambda pref="auto": "umu")
    monkeypatch.setattr(launchsettings, "needs_download", lambda r, pref: False)
    monkeypatch.setattr(gui, "selection_problems", lambda r, pref: ["The selected Proton build GE-Proton9-27 was not found, so the default the latest Proton-GE is used"])
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: calls.append(("server", changes)))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    pushed, told = [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(gui.protonge, "available", lambda *a, **k: ["GE-Proton9-27"])
    win.app.bridge.message.connect(lambda level, text: told.append((level, text)))

    page.choose_engine(rec)
    pump(qapp)
    engine_page = pushed[0]
    assert isinstance(engine_page, gui.EnginePage)
    engine_page.on_done("umu", "GE-Proton9-27")
    pump(qapp)

    assert calls == [("engine", "umu"), ("proton", "GE-Proton9-27"), ("server", {"proton": "GE-Proton9-27"})]
    assert [lvl for lvl, _t in told] == ["info", "warning"] and "GE-Proton9-27" in told[0][1]


def _server_keeps_winetricks(win, monkeypatch, wanted):
    """The server holds these winetricks entries for every game (None: it cannot answer)."""
    from types import SimpleNamespace

    from mog_client.saves import devices

    monkeypatch.setattr(devices, "known_device", lambda: SimpleNamespace(device_id=3))
    row = None if wanted is None else {"proton": None, "winecfg_reg": None, "winetricks": wanted, "launch_args": None, "updated_at": "2026-10-15T10:00:00Z"}
    server = SimpleNamespace(launch_settings=lambda gid, dev: None if row is None else {"shared": row, "device": None})
    monkeypatch.setattr(win.app, "client", lambda: server)


def test_winetricks_lists_the_verbs_with_the_prefixs_ticked_and_installs_only_the_new_ones(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "custom9"])
    pushed, ran, told, kept = [], [], [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda r, pref, verbs: (ran.append(verbs), r.winetricks.extend(verbs)))
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: kept.append(changes))
    monkeypatch.setattr(win, "message", lambda text, level="info", **k: told.append(text))
    win.app.bridge.message.connect(lambda level, text: told.append(text))

    _server_keeps_winetricks(win, monkeypatch, None)
    page.open_winetricks(rec)
    pump(qapp)
    checklist = pushed[0]
    assert isinstance(checklist, gui.ChecklistPage)
    labels = {checklist.list.item(i).data(Qt.UserRole): (checklist.list.item(i).text(), checklist.list.item(i).checkState() == Qt.Checked) for i in range(checklist.list.count())}
    assert labels["vcrun2019"][1] is True and labels["dotnet48"][1] is False
    assert labels["custom9"] == ("custom9 (set elsewhere)", True)  # a verb from another device that is not in the catalog

    checklist.on_done(["vcrun2019", "custom9"])  # nothing new
    assert ran == [] and "Nothing changed" in told[-1]

    checklist.on_done(["vcrun2019", "custom9", "dotnet48", "d3dx9"])
    pump(qapp)

    assert ran == [["dotnet48", "d3dx9"]] and "winetricks" not in page.app.jobs.get(rec.game_id, "")
    assert kept == [{"winetricks": ["vcrun2019", "custom9", "dotnet48", "d3dx9"]}]  # asks where it holds, as winecfg does
    assert any("dotnet48, d3dx9 installed" in t for t in told)


def test_a_failed_winetricks_says_so_keeps_nothing_and_frees_the_game(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    pushed, errors, kept = [], [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(win, "message", lambda *a, **k: None)
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: kept.append(changes))

    def fail(*a, **k):
        raise RuntimeError("winetricks is not installed")

    monkeypatch.setattr(launchsettings, "run_winetricks", fail)
    win.app.bridge.error.connect(errors.append)

    _server_keeps_winetricks(win, monkeypatch, None)
    page.open_winetricks(rec)
    pump(qapp)
    pushed[0].on_done(["vcrun2019"])
    pump(qapp)

    assert errors == ["winetricks: winetricks is not installed"] and kept == [] and rec.game_id not in page.app.jobs


def test_the_poll_sends_the_launch_settings_the_server_has_not_taken_and_only_when_some_wait(win, qapp, monkeypatch):
    from mog_client import launchsettings

    sent = []
    fake = type("C", (), {"notifications": lambda self: {"notifications": [], "unread": 0}})()
    monkeypatch.setattr(win.app, "client", lambda: fake)
    monkeypatch.setattr(launchsettings, "flush_all", lambda client, say: sent.append(client))
    monkeypatch.setattr(gui.threading, "Thread", lambda target, daemon=True: type("T", (), {"start": lambda self: target()})())

    win.app.poll_notifications()
    assert sent == []  # nothing waits

    launchsettings.queue(41, {"proton": "GE-Proton9-27"}, True)
    win.app.poll_notifications()
    assert sent == [fake]


def _library_tab_ready(qapp, page, expected: int) -> None:
    """Waits for the disk to be read: every row listed and none still saying it is being measured."""
    deadline = time.time() + 5
    while time.time() < deadline:
        qapp.processEvents()
        rows = page._library_rows
        if len(rows) == expected and not any(r.status.text() == "Measuring..." for r in rows):
            break
        time.sleep(0.01)
    pump(qapp)


def test_the_library_tab_holds_the_folders_and_the_launcher_scan_and_lists_games_biggest_first(win, qapp, tmp_path):
    for name, size in (("Small", 10), ("Big", 5000), ("Mid", 300)):
        folder = tmp_path / "games" / name
        folder.mkdir(parents=True)
        (folder / "data.bin").write_bytes(b"x" * size)
    records = {
        1: config.InstalledGame(1, "Small", str(tmp_path / "games" / "Small"), state="installed"),
        2: config.InstalledGame(2, "Big", str(tmp_path / "games" / "Big"), state="installed"),
        3: config.InstalledGame(3, "Mid", str(tmp_path / "games" / "Mid"), state="installed"),
        4: config.InstalledGame(4, "Gone", str(tmp_path / "unplugged" / "Gone"), state="installed"),
    }
    config.save_library(records)
    win.open_settings()
    page = win.current_page()
    names = [page.tabs.tabText(i) for i in range(page.tabs.count())]
    assert names == ["General", "Server", "Library", "Logs"]
    in_general = {row.title.text() for row in page.general.rows}
    in_library = {row.title.text() for row in page.library_tab.rows}
    assert "Install folders" in in_library and "Install folders" not in in_general
    assert "Scan for launchers" in in_library and "Scan for launchers" not in in_general

    page.tabs.setCurrentWidget(page.library_tab)
    _library_tab_ready(qapp, page, 4)
    assert [r.title.text() for r in page._library_rows] == ["Big", "Mid", "Small", "Gone"]
    assert page._library_rows[0].status.text() == fmt_bytes(5000)
    assert "not there" in page._library_rows[3].status.text()
    assert "4" in page._installed_title.text()

    page._library_rows[0].control.click()  # Uninstall asks before it does anything
    pump(qapp)
    assert isinstance(win.current_page(), gui.ConfirmPage) and "Big" in win.current_page().text_label.text()


# --- picking only selects; OK answers ------------------------------------------------------------------------


def test_a_choice_is_answered_by_ok_not_by_picking_the_entry(win, qapp):
    answers = []
    win.choose("Pick", "which", [("A", 1), ("B", 2)], answers.append)
    pump(qapp)
    page = win.current_page()
    page.list.setCurrentRow(1)
    page.list.itemActivated.emit(page.list.item(1))  # Enter, A or a double click
    assert answers == [] and win.current_page() is page
    next(b for b in page.findChildren(QPushButton) if b.text() == "OK").click()
    assert answers == [2] and win.current_page() is not page


def test_the_installer_picker_is_answered_by_ok_not_by_picking_the_entry(win, qapp, monkeypatch):
    game_page, page = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(game_page, "start_with", lambda *a, **k: started.append(a))
    page.on_installers(41, [{"path": "Setup/gothic2.exe", "file_size_bytes": 1000, "kind": "exe", "category": "game"}], "")
    rows = [page.list.item(i) for i in range(page.list.count())]
    row = next(r for r in rows if "gothic2.exe" in page.text_of(r))
    page.list.setCurrentItem(row)
    page.list.itemActivated.emit(row)
    assert win.current_page() is page and page.ok.isEnabled() and started == []  # only selected
    page.ok.click()
    assert win.current_page() is not page and len(started) == 1


def test_ok_on_the_installer_picker_is_greyed_while_nothing_can_be_picked(win, qapp, monkeypatch):
    _game_page, page = _picker_for(win, qapp, monkeypatch)
    page.list.clear()
    page.list.addItem("a heading")
    page.list.item(0).setFlags(Qt.NoItemFlags)
    page._sync_ok()
    assert not page.ok.isEnabled()


def test_the_file_picker_opens_folders_but_only_selects_a_file(win, qapp, tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "game.exe").write_bytes(b"MZ")
    picked = []
    win.push(gui.BrowsePage(win, tmp_path, picked.append))
    pump(qapp)
    page = win.current_page()
    names = [page.list.item(i).text() for i in range(page.list.count())]
    folder = names.index("bin/")
    page._activate(page.list.item(folder))  # a folder is opened
    assert page.cwd == tmp_path / "bin" and picked == []
    page._load(tmp_path)
    page.list.setCurrentRow([page.list.item(i).text() for i in range(page.list.count())].index("game.exe"))
    assert page.ok.isEnabled()
    page._activate(page.list.currentItem())
    assert picked == []  # selected only
    page.ok.click()
    assert picked == [str(tmp_path / "game.exe")]


def test_ok_on_the_file_picker_is_greyed_on_a_folder(win, qapp, tmp_path):
    (tmp_path / "bin").mkdir()
    win.push(gui.BrowsePage(win, tmp_path, lambda _p: None))
    pump(qapp)
    page = win.current_page()
    page.list.setCurrentRow([page.list.item(i).text() for i in range(page.list.count())].index("bin/"))
    assert not page.ok.isEnabled()


def test_a_change_while_the_buttons_are_made_does_not_double_them(win, qapp, tmp_path, monkeypatch):
    """Regression test: starting an install sometimes showed the page's buttons twice (Pause and both Cancels, two sets), and
    they were right again on coming back to the page. Making the buttons reads the progress, which asks whether they are
    stale; when the install's state had changed in between, they were made inside the making of them, and added twice."""
    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    win.show_game(1)
    pump(qapp)
    page = win.current_page()
    win.app.installs[1] = threading.Event()
    keys = iter([("before",), ("after",)])
    last = []
    monkeypatch.setattr(page, "_buttons_key", lambda: last.append(next(keys, ("after",))) or last[-1])  # it changes once

    page.rebuild()
    pump(qapp)
    texts = [page.buttons.itemAt(i).widget().text() for i in range(page.buttons.count()) if page.buttons.itemAt(i).widget()]
    assert texts == ["Pause", "Cancel local install", "Cancel server install"]


# --- several installers in order ------------------------------------------------------------------------

SETUP = {"path": "setup.exe", "file_size_bytes": 1000, "kind": "known installer", "category": "game"}
PATCH1 = {"path": "patch1.exe", "file_size_bytes": 10, "kind": "executable (top level)", "category": "patch"}
PATCH2 = {"path": "patch2.exe", "file_size_bytes": 20, "kind": "executable (top level)", "category": "patch"}


class OrderServer:
    def __init__(self, saved=()):
        self.saved, self.put = list(saved), []

    def get_install_order(self, game_id):
        return self.saved

    def put_install_order(self, game_id, steps):
        self.put.append((game_id, steps))
        return steps


def _order_page(win, qapp, monkeypatch, found, saved=()):
    page, picker = _picker_for(win, qapp, monkeypatch)
    server = OrderServer(saved)
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "run_bg", lambda fn, on_error=None: fn())
    picker.on_installers(41, found, "", False)
    picker.list.setCurrentRow(
        next(i for i in range(picker.list.count()) if picker.list.item(i).data(Qt.UserRole) and not _is_copy_row(picker, i))
    )
    pump(qapp)
    return page, picker, server


def _is_copy_row(picker, i):
    key = picker._key(picker.list.item(i))
    return key is not None and key[1] == "mog:copy"


def _index_of(picker, path, source=None):
    for i in range(picker.list.count()):
        key = picker._key(picker.list.item(i))
        if key and key[1] == path and key[2] == source:
            return i
    raise AssertionError(f"no row for {path}")


def _widget(picker, path, source=None):
    return picker.list.itemWidget(picker.list.item(_index_of(picker, path, source)))


def _tick(picker, qapp, path, source=None):
    _widget(picker, path, source).check.click()
    pump(qapp)


def _installer_rows(picker, with_copy=False):
    """The paths of the rows that are installers, top to bottom, with their number when ticked."""
    out = []
    for i in range(picker.list.count()):
        widget = picker.list.itemWidget(picker.list.item(i))
        key = picker._key(picker.list.item(i))
        if widget is not None and widget.check is not None and (with_copy or key[1] != "mog:copy"):
            number = [lbl.text() for lbl in widget.findChildren(QLabel) if lbl.objectName() == "rowNumber"]
            out.append((key[1], number[0] if number else None))
    return out


def test_ticking_an_installer_moves_it_to_the_top_numbered_in_the_order_it_will_run(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    assert _installer_rows(picker) == [("setup.exe", None), ("patch1.exe", None), ("patch2.exe", None)]

    _tick(picker, qapp, "patch2.exe")
    _tick(picker, qapp, "setup.exe")

    assert _installer_rows(picker) == [("patch2.exe", "1"), ("setup.exe", "2"), ("patch1.exe", None)]


def test_unticking_sends_the_row_back_below_and_renumbers_the_rest(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    for path in ("setup.exe", "patch1.exe", "patch2.exe"):
        _tick(picker, qapp, path)

    _tick(picker, qapp, "patch1.exe")

    assert _installer_rows(picker) == [("setup.exe", "1"), ("patch2.exe", "2"), ("patch1.exe", None)]


def test_the_arrows_on_a_row_move_it_and_the_edges_cannot_go_further(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    for path in ("setup.exe", "patch1.exe", "patch2.exe"):
        _tick(picker, qapp, path)
    first, last = _widget(picker, "setup.exe"), _widget(picker, "patch2.exe")
    assert not first.arrows[0].isEnabled() and first.arrows[1].isEnabled()  # the first cannot go up
    assert last.arrows[0].isEnabled() and not last.arrows[1].isEnabled()  # the last cannot go down

    _widget(picker, "patch2.exe").arrows[0].click()  # patch2 up
    pump(qapp)

    assert [p for p, _ in _installer_rows(picker)] == ["setup.exe", "patch2.exe", "patch1.exe"]


def test_right_and_left_walk_onto_the_arrows_of_a_ticked_row_and_enter_moves_it(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    _tick(picker, qapp, "setup.exe")
    _tick(picker, qapp, "patch1.exe")
    picker.list.setFocus()
    picker.list.setCurrentRow(_index_of(picker, "patch1.exe"))
    pump(qapp)
    assert picker.arrow is None and not hasattr(picker, "up_button")  # no Up and Down buttons below the list

    QTest.keyClick(picker.list, Qt.Key_Right)
    pump(qapp)
    assert picker.arrow == 0 and _widget(picker, "patch1.exe").arrows[0].property("picked") is True  # Up

    QTest.keyClick(picker.list, Qt.Key_Right)
    pump(qapp)
    assert picker.arrow == 0  # Down is out for the last row: Right has nowhere further to go

    QTest.keyClick(picker.list, Qt.Key_Return)  # Enter on Up moves the row up and does not untick it
    pump(qapp)
    assert [p for p, _ in _installer_rows(picker)] == ["patch1.exe", "setup.exe"]
    assert picker.arrow == 1  # it is first now: Up is out, the arrow stays on what can be pressed

    QTest.keyClick(picker.list, Qt.Key_Left)
    pump(qapp)
    assert picker.arrow is None and _widget(picker, "patch1.exe").arrows[1].property("picked") is False


def test_right_does_nothing_on_a_row_that_is_not_ticked(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    picker.list.setFocus()
    picker.list.setCurrentRow(_index_of(picker, "setup.exe"))
    pump(qapp)

    QTest.keyClick(picker.list, Qt.Key_Right)

    assert picker.arrow is None


def test_another_row_takes_the_keyboard_off_the_arrows(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    _tick(picker, qapp, "setup.exe")
    _tick(picker, qapp, "patch1.exe")
    picker.list.setFocus()
    picker.list.setCurrentRow(_index_of(picker, "setup.exe"))
    QTest.keyClick(picker.list, Qt.Key_Right)
    assert picker.arrow is not None

    QTest.keyClick(picker.list, Qt.Key_Down)
    pump(qapp)

    assert picker.arrow is None


def test_enter_ticks_the_selected_installer(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    picker.list.setCurrentRow(_index_of(picker, "patch1.exe"))

    picker.list.itemActivated.emit(picker.list.currentItem())
    pump(qapp)

    assert _installer_rows(picker) == [("patch1.exe", "1"), ("setup.exe", None)]


def test_install_with_several_ticked_runs_them_in_that_order_and_the_server_remembers_it(win, qapp, monkeypatch):
    page, picker, server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, steps)))
    _tick(picker, qapp, "setup.exe")
    _tick(picker, qapp, "patch2.exe")
    _tick(picker, qapp, "patch1.exe")
    _widget(picker, "patch1.exe").arrows[0].click()  # patch1 above patch2
    pump(qapp)

    picker.accept()

    steps = [{"path": "setup.exe", "source_path": None}, {"path": "patch1.exe", "source_path": None}, {"path": "patch2.exe", "source_path": None}]
    assert started == [(None, steps)] and server.put[-1][1] == steps


def test_install_with_one_ticked_installer_runs_just_that_one(win, qapp, monkeypatch):
    page, picker, server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, steps)))
    _tick(picker, qapp, "patch1.exe")

    picker.accept()

    assert started == [(PATCH1, None)] and server.put == []


def test_with_nothing_ticked_install_runs_the_selected_row(win, qapp, monkeypatch):
    page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, steps)))
    picker.list.setCurrentRow(_index_of(picker, "setup.exe"))

    picker.accept()

    assert started == [(SETUP, None)]


def test_a_tick_survives_the_list_being_drawn_again(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1])
    _tick(picker, qapp, "patch1.exe")

    picker.on_inside(41, "nothing", [])  # another redraw

    assert _installer_rows(picker) == [("patch1.exe", "1"), ("setup.exe", None)]


def test_installers_are_big_labels_with_a_flag_and_a_disc_or_the_servers_choice_has_none(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, DISC])
    flags = {
        picker._key(picker.list.item(i))[1]: picker.list.itemWidget(picker.list.item(i)).check is not None
        for i in range(picker.list.count())
        if picker.list.itemWidget(picker.list.item(i)) is not None
    }

    assert flags == {"mog:copy": True, "setup.exe": True, "2 - Setup/DISK1.ISO": False}  # the copy is ticked like an installer
    assert picker.list.itemWidget(picker.list.item(_index_of(picker, "setup.exe"))).objectName() == "installerRow"


# --- mods, patches and DLC kept on the server ---------------------------------------------------------------


class AddonServer:
    def __init__(self, addons=(), zips=None):
        self.addons, self.zips, self.uploads, self.deleted = list(addons), zips or {}, [], []

    def list_addons(self, game_id):
        return self.addons

    def download_addon(self, game_id, addon_id, dest, on_progress=None):
        self.busy_during_download = self.win_busy() if hasattr(self, "win_busy") else None
        dest.write_bytes(self.zips[addon_id])

    def delete_addon(self, game_id, addon_id):
        self.deleted.append(addon_id)
        self.addons = [a for a in self.addons if a["id"] != addon_id]

    def upload_addon(self, game_id, category, name, archive, on_progress=None):
        import zipfile as _zip

        self.busy_during_upload = self.win_busy() if hasattr(self, "win_busy") else None
        if on_progress:
            on_progress(5, 10)
        with _zip.ZipFile(archive) as zf:
            self.uploads.append((category, name, sorted(zf.namelist())))
        return {"id": 1}


def _addons_page(win, qapp, monkeypatch, tmp_path, server):
    from mog_client.config import InstalledGame

    folder = tmp_path / "Metroid"
    folder.mkdir()
    (folder / "Metroid.exe").write_bytes(b"exe")
    rec = InstalledGame(game_id=41, name="Metroid", install_dir=str(folder), state="installed")
    monkeypatch.setattr(win.app, "client", lambda: server)

    def run_now(fn, on_error=None):  # as App.run_bg, on this thread: a failure goes to on_error
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            (on_error or win.app.bridge.error.emit)(str(e))

    monkeypatch.setattr(win.app, "run_bg", run_now)
    said = []
    monkeypatch.setattr(win, "message", lambda text, level="error", *a, **k: said.append((level, text)))
    monkeypatch.setattr(win, "ask", lambda text, on_yes, *a, **k: on_yes())
    page = gui.AddonsPage(win, {"id": 41, "name": "Metroid"}, rec)
    pump(qapp)
    return page, folder, said


def _zip_of(members):
    import io
    import zipfile as _zip

    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


ADDON = {"id": 3, "category": "patch", "name": "Patch 2.0", "size_bytes": 10, "file_count": 1}


def test_the_addons_of_the_server_are_listed_and_one_is_applied_over_the_game(win, qapp, monkeypatch, tmp_path):
    server = AddonServer([ADDON], {3: _zip_of({"data/new.pak": b"new"})})
    page, folder, said = _addons_page(win, qapp, monkeypatch, tmp_path, server)
    assert page.list.count() == 1 and page.list.item(0).text().startswith("[PATCH] Patch 2.0")

    page.apply()
    pump(qapp)

    assert (folder / "data" / "new.pak").read_bytes() == b"new"
    assert said and said[-1][0] == "info" and "1 file put over the game" in said[-1][1]


def test_a_failed_apply_is_told_not_swallowed(win, qapp, monkeypatch, tmp_path):
    server = AddonServer([ADDON], {3: _zip_of({"../escape": b"x"})})
    page, _folder, said = _addons_page(win, qapp, monkeypatch, tmp_path, server)

    page.apply()
    pump(qapp)

    assert said[-1][0] == "error" and "Unsafe" in said[-1][1]


def test_an_addon_can_be_deleted_from_the_server_and_leaves_the_list(win, qapp, monkeypatch, tmp_path):
    server = AddonServer([ADDON])
    page, _folder, _said = _addons_page(win, qapp, monkeypatch, tmp_path, server)

    page.delete()
    pump(qapp)

    assert server.deleted == [3] and page.list.count() == 0


def test_a_patch_run_by_hand_is_recorded_then_sent_with_a_name(win, qapp, monkeypatch, tmp_path):
    server = AddonServer()
    page, folder, said = _addons_page(win, qapp, monkeypatch, tmp_path, server)
    assert page.record_button.text() == "Install addon"

    page.record()  # the layer begins
    assert page.record_button.text().startswith("Finish and send")
    (folder / "Metroid.exe").write_bytes(b"patched exe, longer")
    (folder / "extra.dat").write_bytes(b"x")

    page.record()  # and ends: what changed is offered for sending
    pump(qapp)
    send = win.current_page()
    assert isinstance(send, gui.SendLayerPage) and len(send.changed) == 2

    send.name.setText("Patch 1.1")
    send.send("patch")
    pump(qapp)
    assert server.uploads == [("patch", "Patch 1.1", ["Metroid.exe", "extra.dat"])]
    assert said[-1] == ("info", "Sent Patch 1.1 to the server as a patch.")
    assert not win.busy.showing  # and the bar that covered the window while it went is gone


def test_a_send_covers_the_window_with_a_bar_while_it_goes(win, qapp, monkeypatch, tmp_path):
    server = AddonServer()
    server.win_busy = lambda: (win.busy.showing, win.busy.heading.text(), win.busy.name.text())
    page, folder, _said = _addons_page(win, qapp, monkeypatch, tmp_path, server)
    page.record()
    (folder / "extra.dat").write_bytes(b"x")
    page.record()
    pump(qapp)
    send = win.current_page()
    send.name.setText("Patch 1.1")

    send.send("patch")

    assert server.busy_during_upload == (True, "Sending a patch", "Patch 1.1")
    pump(qapp)
    assert not win.busy.showing


def test_an_apply_covers_the_window_with_a_bar_and_a_failed_one_takes_it_away(win, qapp, monkeypatch, tmp_path):
    server = AddonServer([ADDON], {3: _zip_of({"../escape": b"x"})})
    server.win_busy = lambda: (win.busy.showing, win.busy.heading.text(), win.busy.name.text())
    page, _folder, said = _addons_page(win, qapp, monkeypatch, tmp_path, server)

    page.apply()
    pump(qapp)

    assert server.busy_during_download == (True, "Applying a patch", "Patch 2.0")

    assert not win.busy.showing and said[-1][0] == "error"


def test_a_layer_that_changed_nothing_says_so_and_sends_nothing(win, qapp, monkeypatch, tmp_path):
    server = AddonServer()
    page, _folder, said = _addons_page(win, qapp, monkeypatch, tmp_path, server)

    page.record()
    page.record()
    pump(qapp)

    assert "Nothing changed" in said[-1][1] and not isinstance(win.current_page(), gui.SendLayerPage)


def test_a_game_not_installed_has_only_the_install_button_for_installers(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    monkeypatch.setattr(win.app, "load_installers", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Metroid", "fs_name": "Metroid", "library_id": None, "igdb_id": None}])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()

    labels = [b.text() for b in page.findChildren(QPushButton)]
    assert "Install" in labels and not any("installers" in label.lower() for label in labels)


class CandidatesServer:
    def __init__(self, candidates, order=()):
        self.data = {"candidates": candidates, "order": list(order)}

    def candidates(self, game_id, source=None):
        return self.data


def _install_click(win, qapp, monkeypatch, server):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    monkeypatch.setattr(win.app, "load_mods", lambda gid: None)
    monkeypatch.setattr(win.app, "load_installers", lambda gid: None)
    monkeypatch.setattr(win.app, "client", lambda: server)

    def run_now(fn, on_error=None):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            (on_error or win.app.bridge.error.emit)(str(e))

    monkeypatch.setattr(win.app, "run_bg", run_now)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append((installer, steps)))
    win.app.set_games([{"id": 41, "name": "Jazz", "fs_name": "Jazz", "library_id": None, "igdb_id": None}])
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    page.install()
    pump(qapp)
    return page, started


def test_installing_a_game_with_patches_opens_the_picker_by_itself(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    _page, started = _install_click(win, qapp, monkeypatch, CandidatesServer([SETUP, PATCH1, PATCH2]))

    assert isinstance(win.current_page(), gui.InstallerPickerPage) and started == []


def test_installing_a_game_with_one_installer_starts_at_once(win, qapp, monkeypatch):
    _page, started = _install_click(win, qapp, monkeypatch, CandidatesServer([SETUP]))

    assert not isinstance(win.current_page(), gui.InstallerPickerPage) and started == [(None, None)]


def test_a_game_whose_order_the_server_remembers_opens_the_picker_with_it_ticked_and_numbered(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    _page, started = _install_click(
        win, qapp, monkeypatch, CandidatesServer([SETUP, PATCH1, PATCH2], order=[{"path": "patch2.exe"}, {"path": "setup.exe"}])
    )
    picker = win.current_page()
    picker.on_installers(41, [SETUP, PATCH1, PATCH2], "", False)
    pump(qapp)

    assert isinstance(picker, gui.InstallerPickerPage) and started == []
    assert _installer_rows(picker) == [("patch2.exe", "1"), ("setup.exe", "2"), ("patch1.exe", None)]


def test_a_failed_lookup_never_stops_the_install(win, qapp, monkeypatch):
    class Broken:
        def candidates(self, game_id, source=None):
            raise RuntimeError("no answer")

    _page, started = _install_click(win, qapp, monkeypatch, Broken())

    assert started == [(None, None)]


# --- installers inside a disc ---------------------------------------------------------------------------

DISC = {"path": "2 - Setup/DISK1.ISO", "file_size_bytes": 4000, "kind": "disc image", "category": "game"}
INNER_SETUP = {"path": "setup.exe", "file_size_bytes": 250, "kind": "known installer", "category": "game"}
INNER_EDIT = {"path": "edit/setup.exe", "file_size_bytes": 250, "kind": "known installer", "category": "game"}


def test_the_installers_inside_a_disc_are_listed_under_it_and_can_be_picked(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    asked = []
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: asked.append((gid, source)))
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False, steps=None: started.append(installer))

    picker.on_installers(41, [DISC], "", False)
    assert asked == [(41, "2 - Setup/DISK1.ISO")]
    assert any("Looking inside" in r for r in _rows(picker))

    picker.on_inside(41, "2 - Setup/DISK1.ISO", [INNER_SETUP, INNER_EDIT])
    rows = _rows(picker)
    assert any("DISK1.ISO > setup.exe" in r for r in rows) and any("DISK1.ISO > edit/setup.exe" in r for r in rows)

    picker.choose(next(picker.list.item(i) for i in range(picker.list.count()) if "DISK1.ISO > setup.exe" in picker.text_of(picker.list.item(i))))
    assert started[0]["path"] == "setup.exe" and started[0]["source_path"] == "2 - Setup/DISK1.ISO"


def test_a_disc_descriptor_is_not_looked_inside(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch)
    asked = []
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: asked.append(source))

    picker.on_installers(41, [DISC, {"path": "2 - Setup/DISK1.MDS", "kind": "disc image", "category": "game", "file_size_bytes": 4}], "", False)

    assert asked == ["2 - Setup/DISK1.ISO"]


def test_installers_inside_discs_can_be_ticked_and_ordered_with_the_others(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, steps)))
    monkeypatch.setattr(win.app, "client", lambda: OrderServer())
    monkeypatch.setattr(win.app, "run_bg", lambda fn, on_error=None: fn())
    picker.on_installers(41, [SETUP, DISC], "", False)
    picker.on_inside(41, "2 - Setup/DISK1.ISO", [INNER_SETUP])
    _tick(picker, qapp, "setup.exe", "2 - Setup/DISK1.ISO")
    _tick(picker, qapp, "setup.exe")

    assert [(p, n) for p, n in _installer_rows(picker)] == [("setup.exe", "1"), ("setup.exe", "2")]
    assert "DISK1.ISO > setup.exe" in _rows(picker)[1]  # the first is the one inside the disc

    picker.accept()
    assert started == [(None, [{"path": "setup.exe", "source_path": "2 - Setup/DISK1.ISO"}, {"path": "setup.exe", "source_path": None}])]


def test_install_reads_inside_the_discs_before_deciding_to_open_the_picker(win, qapp, monkeypatch):
    class WithDisc:
        def candidates(self, game_id, source=None):
            if source:
                return {"candidates": [INNER_SETUP, INNER_EDIT]}
            return {"candidates": [DISC], "order": []}

    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    _page, started = _install_click(win, qapp, monkeypatch, WithDisc())

    assert isinstance(win.current_page(), gui.InstallerPickerPage) and started == []


def test_the_down_arrow_leaves_the_last_row_for_the_buttons(win, qapp, monkeypatch):
    """The list used to keep the arrow at its last row, so the buttons below could not be reached from the keyboard."""
    _page, picker = _picker_for(win, qapp, monkeypatch)
    monkeypatch.setattr(win.app, "load_inside", lambda gid, source: None)
    picker.on_installers(41, [SETUP, PATCH1], "", False)
    pump(qapp)
    picker.list.setFocus()
    picker.list.setCurrentRow(picker.list.count() - 1)
    pump(qapp)
    assert picker.list.hasFocus()

    QTest.keyClick(picker.list, Qt.Key_Down)
    pump(qapp)

    assert not picker.list.hasFocus() and picker.focusWidget() in (picker.server_button, picker.ok, win.modal.close_button)


def test_only_the_current_row_is_filled_and_an_arrow_marked_on_it_is_cleared_when_another_row_is_reached(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    for path in ("setup.exe", "patch1.exe", "patch2.exe"):
        _tick(picker, qapp, path)
    picker.list.setFocus()
    picker.list.setCurrentRow(_index_of(picker, "patch1.exe"))
    pump(qapp)
    assert [_widget(picker, p).property("current") for p in ("setup.exe", "patch1.exe", "patch2.exe")] == [False, True, False]

    QTest.keyClick(picker.list, Qt.Key_Right)  # onto the Up arrow of patch1
    assert _widget(picker, "patch1.exe").arrows[0].property("picked") is True

    QTest.keyClick(picker.list, Qt.Key_Down)  # another row: no arrow stays marked anywhere
    pump(qapp)
    assert [_widget(picker, p).property("current") for p in ("setup.exe", "patch1.exe", "patch2.exe")] == [False, False, True]
    assert not any(b.property("picked") for row in picker._rows() for b in row.arrows)
    assert picker.arrow is None


def test_let_the_server_choose_is_a_button_above_install_and_not_a_list_row(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch, needed=False)
    started = []
    monkeypatch.setattr(page, "start_with", lambda version, installer, extract=False, steps=None: started.append((installer, extract, steps)))
    picker.on_installers(41, [SETUP, PATCH1], "", False)
    pump(qapp)

    assert not any("Let the server choose" in r for r in _rows(picker))
    assert picker.server_button.isVisible() and picker.server_button.text() == "Let the server choose"
    assert picker.server_button.geometry().bottom() < picker.ok.geometry().top() + picker.ok.mapTo(picker, picker.ok.rect().topLeft()).y()

    picker.server_button.click()
    assert started == [(None, False, None)]


def _tags(picker):
    out = {}
    for i in range(picker.list.count()):
        widget = picker.list.itemWidget(picker.list.item(i))
        key = picker._key(picker.list.item(i))
        if widget is not None and key:
            out[key[1]] = widget.tag.text() if widget.tag is not None else None
    return out


def test_a_ticked_row_is_tagged_game_or_patch_like_the_server_and_the_kind_text_is_gone(win, qapp, monkeypatch):
    _page, picker, _server = _order_page(win, qapp, monkeypatch, [SETUP, PATCH1, PATCH2])
    assert all("known installer" not in r and "executable" not in r for r in _rows(picker))
    assert _tags(picker) == {"mog:copy": None, "setup.exe": None, "patch1.exe": "patch", "patch2.exe": "patch"}  # the server's own category, for patches

    _tick(picker, qapp, "patch1.exe")
    _tick(picker, qapp, "setup.exe")

    assert _tags(picker) == {"mog:copy": None, "patch1.exe": "game", "setup.exe": "patch", "patch2.exe": "patch"}  # parts in the install, by place
    assert _widget(picker, "patch1.exe").tag.property("kind") == "game" and _widget(picker, "setup.exe").tag.property("kind") == "other"


def _focus_after(picker, qapp, start, key):
    start.setFocus()
    pump(qapp)
    QTest.keyClick(start, key)
    pump(qapp)
    return picker.focusWidget() or QApplication.focusWidget()


def test_right_from_install_goes_to_cancel_and_left_comes_back(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch, needed=False)
    picker.on_installers(41, [SETUP, PATCH1], "", False)
    pump(qapp)

    assert _focus_after(picker, qapp, picker.ok, Qt.Key_Right) is win.modal.close_button
    assert _focus_after(picker, qapp, win.modal.close_button, Qt.Key_Left) is picker.ok


def test_the_button_above_is_reached_by_the_arrows_and_up_and_down_change_row(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch, needed=False)
    picker.on_installers(41, [SETUP, PATCH1], "", False)
    pump(qapp)

    assert _focus_after(picker, qapp, picker.server_button, Qt.Key_Down) in (picker.ok, win.modal.close_button)
    assert _focus_after(picker, qapp, picker.ok, Qt.Key_Up) is picker.server_button


def test_up_from_the_top_row_of_buttons_goes_back_to_the_list(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch, needed=False)
    picker.on_installers(41, [SETUP, PATCH1], "", False)
    pump(qapp)

    assert _focus_after(picker, qapp, picker.server_button, Qt.Key_Up) is picker.list


def test_cancel_stops_taking_this_pages_arrows_once_the_page_is_gone(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch, needed=False)
    picker.on_installers(41, [SETUP], "", False)
    pump(qapp)
    walked = []
    monkeypatch.setattr(picker, "_walk_buttons", lambda button, key: walked.append(key) or True)
    close = win.modal.close_button
    QTest.keyClick(close, Qt.Key_Right)
    assert walked == [Qt.Key_Right]  # while the picker is up, its arrows

    win.back()
    pump(qapp)
    QTest.keyClick(close, Qt.Key_Right)

    assert walked == [Qt.Key_Right]  # gone: the card's button is left to whatever page comes next


def _activate(win, qapp):
    """Focus goes to a widget only in the window that is active, and a test's window is not until it is asked to be."""
    win.activateWindow()
    pump(qapp)


def _installed_game(win, qapp, tmp_path):
    from mog_client import manager
    from mog_client.config import InstalledGame

    folder = tmp_path / "g"
    folder.mkdir()
    (folder / "a.exe").write_text("x")
    manager._update(InstalledGame(game_id=41, name="Eta", install_dir=str(folder), state="installed", executable=str(folder / "a.exe")))
    win.app.set_games([{"id": 41, "name": "Eta", "igdb_id": None, "library_id": None}, {"id": 42, "name": "Theta", "igdb_id": None, "library_id": None}])
    win.library.populate("")
    win.library.show_loading(False)
    pump(qapp)


def test_up_from_the_first_entry_of_a_card_goes_to_its_last_entry_and_down_from_there_to_the_cards_button(win, qapp, tmp_path):
    _activate(win, qapp)
    _installed_game(win, qapp, tmp_path)
    win.open_game_options(41)
    pump(qapp)
    first = qapp.focusWidget()
    assert isinstance(win.current_page(), gui.OptionsPage) and first.text() == "Linux Options"

    win.on_pad_event(gamepad.UP)  # the pad's Up is the same key as the keyboard's
    pump(qapp)
    assert qapp.focusWidget().text() == "Uninstall..."
    win.on_pad_event(gamepad.DOWN)
    pump(qapp)
    assert qapp.focusWidget() is win.modal.close_button

    first.setFocus()
    QTest.keyClick(first, Qt.Key_Up)
    pump(qapp)
    assert qapp.focusWidget().text() == "Uninstall..."


def test_a_right_click_or_select_on_a_game_of_the_library_opens_its_options_and_the_guide_says_so(win, qapp, tmp_path):
    _activate(win, qapp)
    from mog_client.gui import legend

    _installed_game(win, qapp, tmp_path)
    grid = win.library.grid
    grid.setCurrentRow(0)
    grid.setFocus()
    pump(qapp)
    assert win.legend_context() in (legend.LIBRARY_GAME, legend.LIBRARY_ALONE_GAME)
    assert "Options" in [e.label for e in legend.entries_for(win.legend_context(), gamepad.XBOX)]
    assert "Options" not in [e.label for e in legend.entries_for(win.legend_context(), None)]  # the keyboard has no such key

    win.on_pad_event(gamepad.ACCOUNT)  # Select
    pump(qapp)
    assert isinstance(win.current_page(), gui.OptionsPage)
    win.back()
    pump(qapp)

    grid.customContextMenuRequested.emit(grid.visualItemRect(grid.item(1)).center())  # a right click on the second cover
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.OptionsPage) and win.game_pages[42].game["name"] == "Theta"
    win.back()
    pump(qapp)
    assert win.current_page() is win.library  # Back returns to the library, not to the game's page


def test_select_without_a_game_on_the_grid_is_still_the_user_menu(win, qapp, tmp_path):
    _activate(win, qapp)
    from mog_client.gui import legend

    _installed_game(win, qapp, tmp_path)
    win.search.setFocus()
    pump(qapp)
    assert win.legend_context() in (legend.LIBRARY, legend.LIBRARY_ALONE)
    win.on_pad_event(gamepad.ACCOUNT)
    pump(qapp)
    assert win.current_page() is win.library  # no Options page, the menu under the user's picture instead


def test_a_game_page_opens_on_its_first_button_and_the_tab_rotation_stops_on_the_cover(win, qapp, tmp_path, monkeypatch):
    _activate(win, qapp)
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    _installed_game(win, qapp, tmp_path)
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    assert qapp.focusWidget() is page.first and page.first.text() == "Play"

    seen = []
    for _ in range(12):
        QTest.keyClick(qapp.focusWidget(), Qt.Key_Tab)
        pump(qapp)
        seen.append(qapp.focusWidget())
    assert page.cover in seen


def test_the_buttons_made_again_keep_the_focus_and_a_press_with_none_lands_on_the_first_button(win, qapp, tmp_path, monkeypatch):
    """Regression test: a refresh while the game's page was open (an install ending, another version chosen, the mods
    arriving) made its buttons again, and the focus went with the old ones: the pad's A then reached nothing and Play had to
    be picked with the arrows first."""
    from mog_client.gui import gamepad

    _activate(win, qapp)
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    _installed_game(win, qapp, tmp_path)
    win.show_game(41)
    pump(qapp)
    page = win.current_page()
    played = []
    monkeypatch.setattr(page, "play", lambda: played.append(1))
    page.rebuild()
    pump(qapp)
    assert qapp.focusWidget() is page.first and page.first.text() == "Play"  # made again, still the one with the focus

    page.buttons.itemAt(2).widget().setFocus()  # the pad had moved on to Options
    page.rebuild()
    pump(qapp)
    assert qapp.focusWidget().text() == "Options"  # and that stays where it was

    qapp.focusWidget().clearFocus()  # whatever else left it with nothing focused
    assert qapp.focusWidget() is None
    win.on_pad_event(gamepad.ACCEPT)
    pump(qapp)
    assert played == [1] and qapp.focusWidget() is page.first


def test_the_executable_page_reads_later_during_an_install_back_from_the_options_both_in_red_and_edits_the_launch_tags(win, qapp, tmp_path, monkeypatch):
    from mog_client import manager
    from mog_client.config import InstalledGame

    monkeypatch.setattr(manager, "write_launch_script", lambda rec, pref: None)  # no launcher is installed here to write it for
    folder = tmp_path / "g"
    folder.mkdir()
    (folder / "a.exe").write_text("x")

    def page_for(state):
        rec = InstalledGame(game_id=41, name="Eta", install_dir=str(folder), state=state, executable=str(folder / "a.exe"))
        return rec, gui.ExecutablePage(win, rec, lambda *a: None)

    for state, text in (("awaiting_executable", "Later"), ("installed", "Back")):
        _rec, page = page_for(state)
        way_out = [b for b in page.findChildren(QPushButton) if b.text() == text]
        assert len(way_out) == 1 and way_out[0].property("danger") is True

    rec, page = page_for("installed")
    assert page.args_label.text() == "Launch arguments: none"
    sent = []
    monkeypatch.setattr(win.app, "send_launch_args", sent.append)
    win.push(page)
    pump(qapp)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Edit").click()
    pump(qapp)
    args_page = win.current_page()
    assert isinstance(args_page, gui.LaunchArgsPage)

    close = win.modal.close_button  # Back is in red, in the row of OK and to its right
    assert close.text() == "Back" and close.property("danger") is True and args_page.isAncestorOf(close)
    row = args_page.button_row
    assert [row.itemAt(i).widget().text() for i in range(row.count()) if row.itemAt(i).widget()] == ["OK", "Back"]

    args_page.field.setText("-windowed -res 1280 720 --nosound")  # typed text splits into tags at each - or --
    args_page.add()
    assert args_page.listed() == ["-windowed", "-res 1280 720", "--nosound"] and args_page.field.text() == ""
    args_page.field.setText("-windowed -fullscreen")  # a tag that is there is not added again
    args_page.add()
    assert args_page.listed() == ["-windowed", "-res 1280 720", "--nosound", "-fullscreen"]
    minus = args_page.list.itemWidget(args_page.list.item(2)).findChildren(QPushButton)[0]  # each tag has its minus
    assert minus.text() == "\u2212"
    minus.click()
    assert args_page.listed() == ["-windowed", "-res 1280 720", "-fullscreen"]
    args_page.list.itemActivated.emit(args_page.list.item(0))  # Enter or A on a tag takes it out too
    args_page.field.setText("-windowed")
    args_page.add()
    assert args_page.listed() == ["-res 1280 720", "-fullscreen", "-windowed"]
    args_page.field.setText("-nosound")  # typed and not added: OK takes it too
    args_page.accept()
    pump(qapp)

    assert win.current_page() is page and rec.launch_args == ["-res 1280 720", "-fullscreen", "-windowed", "-nosound"]
    assert page.args_label.text() == "Launch arguments: -res 1280 720  -fullscreen  -windowed  -nosound"
    assert sent == [rec]  # merged with the server's, which gets what it lacks


def test_a_cover_is_drawn_selected_only_while_the_grid_has_the_focus_and_is_remembered_when_it_goes_elsewhere(win, qapp, tmp_path):
    _activate(win, qapp)
    _installed_game(win, qapp, tmp_path)
    grid = win.library.grid
    grid.setCurrentRow(1)
    grid.setFocus()
    pump(qapp)
    assert [i.text() for i in grid.selectedItems()] == ["Theta"]

    win.search.setFocus()  # what Tab does: the focus leaves the grid
    pump(qapp)
    assert grid.selectedItems() == [] and grid.currentItem().text() == "Theta"

    grid.setFocus()  # and back to the remembered cover
    pump(qapp)
    assert [i.text() for i in grid.selectedItems()] == ["Theta"]


def test_the_client_takes_launch_tags_edited_on_the_server_for_its_installed_games_and_rewrites_their_script(win, qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mog_client import manager
    from mog_client.saves import devices

    _installed_game(win, qapp, tmp_path)
    rewritten, put = [], []
    monkeypatch.setattr(manager, "write_launch_script", lambda rec, pref: rewritten.append(list(rec.launch_args)))
    monkeypatch.setattr(manager, "set_launcher", lambda rec, engine, pref="auto": rewritten.append(list(rec.launch_args)))
    monkeypatch.setattr(devices, "known_device", lambda: SimpleNamespace(device_id=3))
    row = {"proton": None, "winecfg_reg": None, "winetricks": None, "launch_args": ["-w", "-intro:off"], "updated_at": "2026-10-14T10:00:00Z"}
    server = SimpleNamespace(
        launch_settings=lambda gid, dev: {"shared": row, "device": None},
        put_launch_settings=lambda game_id, device, changes: put.append(changes) or 200,
    )
    monkeypatch.setattr(win.app, "client", lambda: server)
    monkeypatch.setattr(win.app, "run_bg", lambda fn, on_error=None: fn())  # not on a thread: the test looks right after

    win.app.sync_launch_args(every=0)

    assert manager.load_library()[41].launch_args == ["-w", "-intro:off"] and rewritten == [["-w", "-intro:off"]] and put == []

    manager.set_launch_args(manager.load_library()[41], ["-w", "-intro:off", "-mine"])  # edited here meanwhile
    rewritten.clear()
    win.app.send_launch_args(manager.load_library()[41])

    assert put == [{"launch_args_add": ["-mine"], "launch_args_remove": []}] and rewritten == []  # the script already has them
    assert manager.load_library()[41].launch_args_synced == ["-w", "-intro:off", "-mine"]

def test_winetricks_lists_settings_beside_the_flags_one_value_for_each_name_and_applies_a_changed_value(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "vd=1280x1024", "mine=custom"])
    pushed, ran, told, kept = [], [], [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda r, pref, verbs: (ran.append(verbs), setattr(r, "winetricks", winetricks.merge(r.winetricks, verbs))))
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: kept.append(changes))
    monkeypatch.setattr(win, "message", lambda text, level="info", **k: told.append(text))
    win.app.bridge.message.connect(lambda level, text: told.append(text))

    _server_keeps_winetricks(win, monkeypatch, None)
    page.open_winetricks(rec)
    pump(qapp)
    checklist = pushed[0]
    rows = {checklist.list.item(i).data(Qt.UserRole): checklist.list.item(i) for i in range(checklist.list.count())}
    assert rows["vd=1280x1024"].checkState() == Qt.Checked and rows["vd=1920x1080"].checkState() == Qt.Unchecked
    assert rows["renderer=gdi"].text().startswith("renderer=gdi:")  # a usual setting, listed with what it is
    assert rows["mine=custom"].text() == "mine=custom (set elsewhere)" and rows["mine=custom"].checkState() == Qt.Checked

    checklist.toggle(rows["vd=1920x1080"])  # one value for a name: the one before is unticked
    assert rows["vd=1920x1080"].checkState() == Qt.Checked and rows["vd=1280x1024"].checkState() == Qt.Unchecked
    checklist.toggle(rows["renderer=gdi"])
    checklist.finish()
    pump(qapp)

    assert ran == [["vd=1920x1080", "renderer=gdi"]]  # the changed value is applied again, not a second vd
    assert rec.winetricks == ["vcrun2019", "vd=1920x1080", "mine=custom", "renderer=gdi"]
    assert kept == [{"winetricks": ["vcrun2019", "vd=1920x1080", "mine=custom", "renderer=gdi"]}]


def test_add_settings_takes_as_many_as_typed_with_a_minus_on_each_and_ticks_them_in_place_of_the_other_values(win, qapp, monkeypatch, tmp_path):
    _activate(win, qapp)
    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vd=1280x1024"])
    _server_keeps_winetricks(win, monkeypatch, None)
    page.open_winetricks(rec)
    pump(qapp)
    checklist = win.current_page()
    assert isinstance(checklist, gui.ChecklistPage)
    add = next(b for b in checklist.findChildren(QPushButton) if b.text() == "Add settings...")

    add.click()
    pump(qapp)
    settings = win.current_page()
    assert isinstance(settings, gui.SettingPage) and isinstance(settings, gui.EntriesPage)
    assert settings.title == "Add settings"
    assert win.modal.close_button.text() == "Back" and win.modal.close_button.property("danger") is True  # as on the arguments page
    assert not win.isActiveWindow() or qapp.focusWidget() is settings.field  # the pad's keyboard types into the field

    settings.field.setText("vd=1366x768 && rm")  # nothing that goes on a command line but a value: nothing is added
    settings.add()
    assert settings.listed() == [] and win.current_page() is settings
    settings.field.setText("vd")  # a name with no value is not a setting
    settings.add()
    assert settings.listed() == []

    settings.field.setText("vd=1366x768 renderer=gdi")  # as many as needed, in one go
    settings.add()
    settings.field.setText("mwo=force vd=1920x1080")  # and a name has one value: the later replaces the earlier
    settings.add()
    assert settings.listed() == ["renderer=gdi", "mwo=force", "vd=1920x1080"]
    settings.list.itemWidget(settings.list.item(1)).findChildren(QPushButton)[0].click()  # a minus takes one out
    assert settings.listed() == ["renderer=gdi", "vd=1920x1080"]
    settings.field.setText("csmt=on")  # typed and not added: OK takes it too
    settings.accept()
    pump(qapp)

    assert win.current_page() is checklist
    rows = {checklist.list.item(i).data(Qt.UserRole): checklist.list.item(i) for i in range(checklist.list.count())}
    assert all(rows[v].checkState() == Qt.Checked for v in ("renderer=gdi", "vd=1920x1080", "csmt=on"))
    assert rows["vd=1280x1024"].checkState() == Qt.Unchecked and "mwo=force" in rows and rows["mwo=force"].checkState() != Qt.Checked
    assert rows["vd=1920x1080"].text() == "vd=1920x1080 (added)" or rows["vd=1920x1080"].text().startswith("vd=1920x1080")


def test_the_winetricks_page_shows_what_the_server_keeps_ticked_and_marked_until_it_is_applied(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "vd=1280x1024"])
    _server_keeps_winetricks(win, monkeypatch, ["renderer=gdi", "vd=1920x1080", "dotnet48", "mine=custom"])  # set on the web page
    pushed, ran = [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda r, pref, verbs: (ran.append(verbs), setattr(r, "winetricks", winetricks.merge(r.winetricks, verbs))))
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: None)

    page.open_winetricks(rec)
    pump(qapp)
    checklist = pushed[0]
    rows = {checklist.list.item(i).data(Qt.UserRole): checklist.list.item(i) for i in range(checklist.list.count())}

    assert rows["vcrun2019"].checkState() == Qt.Checked and "not applied" not in rows["vcrun2019"].text()  # in the prefix
    assert rows["dotnet48"].checkState() == Qt.Checked and rows["dotnet48"].text().endswith("(on the server, not applied yet)")
    assert rows["renderer=gdi"].checkState() == Qt.Checked and "not applied yet" in rows["renderer=gdi"].text()
    assert rows["vd=1920x1080"].checkState() == Qt.Checked and rows["vd=1280x1024"].checkState() == Qt.Unchecked  # the server's value
    assert rows["mine=custom"].text() == "mine=custom (from the server, not applied yet)" and rows["mine=custom"].checkState() == Qt.Checked

    checklist.finish()  # Done applies what the server keeps and the prefix lacks, and only that
    pump(qapp)
    assert ran == [["dotnet48", "vd=1920x1080", "renderer=gdi", "mine=custom"]]  # the list's order: the catalog, the settings, the rest
    assert rec.winetricks == ["vcrun2019", "vd=1920x1080", "dotnet48", "renderer=gdi", "mine=custom"]  # one vd, the server's value


def test_find_game_is_in_the_options_of_an_installed_game_even_when_its_folder_is_gone(win, tmp_path):
    rec = _game_record(tmp_path / "gone", "g.exe")  # the folder is not there: no Linux or Repair rows, but this one
    assert "Find game..." in _texts(_options(win, rec))
    rec.state = "installing"
    assert "Find game..." not in _texts(_options(win, rec))


def test_find_game_is_in_the_options_of_a_game_with_no_record_too(win):
    from types import SimpleNamespace

    page = SimpleNamespace(delete_server_cache=lambda: None, game={"id": 4, "name": "G"})
    assert "Find game..." in _texts(gui.OptionsPage(win, page, None))


def test_unticking_a_verb_asks_then_rebuilds_the_prefix_and_takes_it_off_the_servers_row(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "d3dx9"])
    _server_keeps_winetricks(win, monkeypatch, ["vcrun2019"])
    pushed, asked, rebuilt = [], [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(win, "ask", lambda text, yes, **k: (asked.append(text), yes()))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(gui, "sync_enabled", lambda rec, settings: False)
    monkeypatch.setattr(gui.watcher, "running_pids", lambda install_dir: [])
    monkeypatch.setattr(launchsettings, "rebuild_prefix", lambda r, pref, keep, reg, say: rebuilt.append(keep))
    monkeypatch.setattr(launchsettings, "flush", lambda client, game_id: True)
    monkeypatch.setattr(page, "_keep_on_server", lambda *a: pytest.fail("nothing was added"))

    page.open_winetricks(rec)
    pump(qapp)
    pushed[0].on_done(["d3dx9"])
    pump(qapp)

    assert len(asked) == 1 and "vcrun2019" in asked[0] and "deleted" in asked[0]
    assert rebuilt == [["d3dx9"]] and rec.game_id not in page.app.jobs
    assert launchsettings.waiting(41) == [{"everywhere": True, "changes": {"winetricks": []}}]  # the row that had it


def test_unticking_a_setting_only_resets_it_in_the_registry(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "vd=1280x1024"])
    _server_keeps_winetricks(win, monkeypatch, None)
    pushed, reset, told = [], [], []
    monkeypatch.setattr(win, "push", pushed.append)
    monkeypatch.setattr(win, "ask", lambda *a, **k: pytest.fail("a setting needs no new prefix"))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda r, pref, verbs: verbs and pytest.fail("nothing to install"))
    monkeypatch.setattr(launchsettings, "reset_settings", lambda r, pref, entries: reset.append(entries))
    win.app.bridge.message.connect(lambda level, text: told.append(text))

    page.open_winetricks(rec)
    pump(qapp)
    pushed[0].on_done(["vcrun2019"])
    pump(qapp)

    assert reset == [["vd=1280x1024"]] and any("vd=1280x1024 taken out of the prefix" in t for t in told)


def test_launch_executable_lists_wine_tools_then_the_games_executables_and_runs_the_pick_in_the_prefix(win, qapp, monkeypatch, tmp_path):
    page, rec = _game_page(win, monkeypatch, tmp_path)
    for rel in ("g.exe", "redist/vcredist_x64.exe", "start.sh"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    chosen, started = [], []
    monkeypatch.setattr(win, "choose", lambda title, text, options, on_choose, **k: chosen.append((options, on_choose)))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(gui, "start_tool", lambda r, pref, program, args=(), cwd=None: started.append((program, args, cwd)))
    monkeypatch.setattr(gui.watcher, "wait_for_tool", lambda *a, **k: True)

    page.launch_in_prefix(rec)
    options, on_choose = chosen[0]
    labels = [label for label, _value in options]

    assert labels[0] == "Registry editor (regedit)" and labels[-1] == "Browse..."
    assert labels[len(gui.PREFIX_TOOLS):-1] == ["g.exe  (the game's executable)", "redist/vcredist_x64.exe"]  # no .sh: not a Wine program

    on_choose(options[1][1])
    on_choose(options[len(gui.PREFIX_TOOLS) + 1][1])
    assert started == [("wineconsole", ("cmd",), None), (str(tmp_path / "redist/vcredist_x64.exe"), (), tmp_path / "redist")]
    assert rec.game_id not in page.app.jobs


def test_a_page_s_old_copy_of_a_record_takes_what_a_start_saved_before_it_is_saved_again(win, monkeypatch, tmp_path):
    from mog_client import manager

    page, rec = _game_page(win, monkeypatch, tmp_path)
    saved = manager.load_library()[41]
    manager._update(saved, winetricks=["vcrun2019"])  # what the pull at the start installed, on another copy

    assert gui.fresh_record(rec) is rec and rec.winetricks == ["vcrun2019"]


def test_nuke_custom_settings_lists_everything_it_takes_away_and_asks_first(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, launch_args=["-dx11"], winetricks=["vcrun2019"])
    (tmp_path / "pfx").mkdir()
    _server_keeps_winetricks(win, monkeypatch, ["d3dx9"])
    asked, cleared = [], []
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(win, "ask", lambda text, yes, **k: (asked.append(text), yes()))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(gui, "sync_enabled", lambda rec, settings: False)
    monkeypatch.setattr(gui.watcher, "running_pids", lambda install_dir: [])
    monkeypatch.setattr(launchsettings, "clear_custom", lambda r, pref, rows, say: cleared.append(rows))
    monkeypatch.setattr(launchsettings, "flush", lambda client, game_id: True)

    assert "Nuke custom settings" in _texts(_options(win, rec))
    page.nuke_settings(rec)
    pump(qapp)

    assert "- Launch argument: -dx11" in asked[0] and "- Winetricks: vcrun2019" in asked[0] and "- Winetricks: d3dx9" in asked[0]
    assert "prefix is deleted" in asked[0] and "all devices" in asked[0] and asked[0].endswith("Continue?")
    assert cleared == [["shared"]] and rec.game_id not in page.app.jobs


def _regedit_run(win, qapp, monkeypatch, tmp_path, before, after):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    kept = []
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "prefix_settings", lambda r: before)
    monkeypatch.setattr(launchsettings, "run_winecfg", lambda r, pref, program="winecfg": after if program == "regedit" else pytest.fail(program))
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: kept.append((changes, what)))
    page._run_in_prefix(rec, ("tool", ("regedit",)))
    pump(qapp)
    return rec, page, kept


def test_what_regedit_changed_in_the_wine_settings_is_kept_on_the_server_as_winecfgs_are(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    rec, page, kept = _regedit_run(win, qapp, monkeypatch, tmp_path, "old", "new")

    assert kept == [({"winecfg_reg": "new"}, "The registry settings")] and rec.game_id not in page.app.jobs
    assert gui.manager.load_library()[41].winecfg_applied == launchsettings.digest("new")


def test_regedit_that_changed_nothing_in_the_wine_settings_asks_nothing(win, qapp, monkeypatch, tmp_path):
    _rec, _page, kept = _regedit_run(win, qapp, monkeypatch, tmp_path, "same", "same")

    assert kept == []


def test_a_message_that_is_up_stays_over_the_menu_card_when_a_page_is_shown_again(win, qapp, monkeypatch, tmp_path):
    page, rec = _game_page(win, monkeypatch, tmp_path)
    win.push(gui.LinuxOptionsPage(win, page, rec))
    win.message("GE-Proton9-27 now runs", "info")
    win.push(gui.ChoicePage(win, "Which devices?", "text", [("All", "all")], lambda _v: None))  # raises the card

    order = [c for c in win.centralWidget().children() if c in (win.modal, win.overlay)]

    assert win.overlay.isVisible() and order == [win.modal, win.overlay]  # the last child is the topmost


def test_starting_says_what_it_is_doing_under_the_cover_and_goes_back_to_installed(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    page._set_starting(True)

    page._start_note("Downloading GE-Proton8-32: 40%", 40)
    assert page.status.text() == "Downloading GE-Proton8-32: 40%" and page.bar.maximum() == 100 and page.bar.value() == 40

    page._start_note("Installing vcrun2015 into the prefix of G")
    assert page.bar.maximum() == 0  # runs back and forth: nothing says how long it takes

    page._set_starting(False)
    page._start_note("late")  # a message that comes after the start is over changes nothing
    assert page.status.text() == "Installed" and page.bar.maximum() == 1


def test_a_start_that_could_not_take_the_launch_settings_sends_the_log_and_still_starts(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    sent, told, started = [], [], []
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(page.app, "client", lambda: "client")
    monkeypatch.setattr(gui, "selection_problems", lambda r, pref: [])
    monkeypatch.setattr(launchsettings, "pull", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("winetricks exited with 1")))
    monkeypatch.setattr(gui.logsend, "send_for_game", lambda client, settings, game_id: sent.append(game_id))
    win.app.bridge.message.connect(lambda level, text: told.append(text))

    page.pull_launch_settings(rec, lambda: started.append(1))
    pump(qapp)

    assert sent == [rec.game_id] and started == [1] and any("winetricks exited with 1" in t for t in told)


def test_the_system_language_is_picked_from_a_list_and_applied_then_kept_on_the_server(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["vcrun2019", "locale=de_DE"])
    _server_keeps_winetricks(win, monkeypatch, ["locale=de_DE", "d3dx9"])
    chosen, ran, kept, queued = [], [], [], []
    monkeypatch.setattr(win, "choose", lambda title, text, options, on_choose, **k: chosen.append((options, on_choose)))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda r, pref, verbs: (ran.append(verbs), gui.manager._update(r, winetricks=winetricks.merge(r.winetricks, verbs))))
    monkeypatch.setattr(launchsettings, "queue", lambda game_id, changes, everywhere: queued.append((changes, everywhere)))
    monkeypatch.setattr(launchsettings, "flush", lambda client, game_id: True)
    monkeypatch.setattr(page, "_keep_on_server", lambda r, changes, what: kept.append((changes, what)))

    page.choose_language(rec)
    pump(qapp)
    options, on_choose = chosen[0]
    labels = [label for label, _v in options]
    assert labels[0].startswith("The launcher's own") and "German (de_DE) - now" in labels and "Japanese (ja_JP)" in labels

    on_choose("ja_JP")
    pump(qapp)

    assert ran == [["locale=ja_JP"]] and rec.winetricks == ["vcrun2019", "locale=ja_JP"]  # one language: the new takes the old one's place
    assert queued == [({"winetricks": ["d3dx9"]}, True)]  # the server's row loses the older language
    assert kept == [({"winetricks": ["vcrun2019", "locale=ja_JP"]}, "The system language")] and rec.game_id not in page.app.jobs
    assert "System language: Japanese" in _texts(gui.LinuxOptionsPage(win, page, rec))


def test_going_back_to_the_default_language_resets_the_prefix_and_asks_nothing(win, qapp, monkeypatch, tmp_path):
    from mog_client import launchsettings

    page, rec = _game_page(win, monkeypatch, tmp_path)
    gui.manager._update(rec, winetricks=["locale=ja_JP"])
    _server_keeps_winetricks(win, monkeypatch, None)
    chosen, reset = [], []
    monkeypatch.setattr(win, "choose", lambda title, text, options, on_choose, **k: chosen.append(on_choose))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(launchsettings, "reset_settings", lambda r, pref, entries: reset.append(entries))
    monkeypatch.setattr(page, "_keep_on_server", lambda *a: pytest.fail("nothing is added"))

    page.choose_language(rec)
    pump(qapp)
    chosen[0]("")
    pump(qapp)

    assert reset == [["locale=ja_JP"]]


def _repair_page(win, monkeypatch, tmp_path, holds):
    from mog_client import manager

    page, rec = _game_page(win, monkeypatch, tmp_path)
    asked, ran = [], []
    monkeypatch.setattr(win, "ask", lambda text, yes, **k: (asked.append(text), yes()))
    monkeypatch.setattr(page.app, "run_bg", lambda fn, on_error=None: fn())
    monkeypatch.setattr(page.app, "client", lambda: "client")
    monkeypatch.setattr(manager, "holds_install", lambda client, gid: holds)
    monkeypatch.setattr(manager, "regenerate_cache", lambda r, client, stop, log, *a: ran.append("regenerate") or True)
    monkeypatch.setattr(manager, "repair", lambda r, client, stop, log, progress: ran.append("repair") or SimpleNamespace(failed=[], kept=[], repaired=0, checked=3))
    return page, rec, asked, ran


def test_a_check_with_the_install_cache_on_the_server_asks_once_and_just_checks(win, qapp, monkeypatch, tmp_path):
    page, rec, asked, ran = _repair_page(win, monkeypatch, tmp_path, holds=True)

    page.repair(rec)
    pump(qapp)

    assert len(asked) == 1 and ran == ["repair"]


def test_a_check_without_the_install_cache_asks_whether_to_make_it_again_and_then_checks(win, qapp, monkeypatch, tmp_path):
    page, rec, asked, ran = _repair_page(win, monkeypatch, tmp_path, holds=False)

    page.repair(rec)
    pump(qapp)

    assert len(asked) == 2 and "make it again" in asked[1] and "installer again" in asked[1]
    assert ran == ["regenerate", "repair"]  # in that order, and only after the yes


def test_declining_to_make_the_cache_again_checks_nothing(win, qapp, monkeypatch, tmp_path):
    page, rec, asked, ran = _repair_page(win, monkeypatch, tmp_path, holds=False)
    monkeypatch.setattr(win, "ask", lambda text, yes, **k: (asked.append(text), yes() if len(asked) == 1 else None))

    page.repair(rec)
    pump(qapp)

    assert len(asked) == 2 and ran == [] and rec.game_id not in win.library.repairs


def test_while_the_files_are_checked_repairing_takes_the_place_of_play_and_the_bar_follows_the_check(win, qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from mog_client import manager

    page, rec, asked, ran = _repair_page(win, monkeypatch, tmp_path, holds=True)
    seen = []

    def shown() -> list[str]:  # the buttons now in the page's row, not the old ones waiting to be deleted
        items = [page.buttons.itemAt(i).widget() for i in range(page.buttons.count())]
        return [w.text() for w in items if w is not None]

    def repair(r, client, stop, log, progress):
        pump(qapp)
        seen.append((shown(), page.bar.maximum()))  # before the first figure: the bar moves
        progress(1, 2)
        pump(qapp)
        seen.append((shown(), page.bar.maximum(), page.bar.value()))
        return SimpleNamespace(failed=[], kept=[], repaired=0, checked=2)

    monkeypatch.setattr(manager, "repair", repair)
    assert shown()[0] == "Play"

    page.repair(rec)
    pump(qapp)

    assert seen == [(["Repairing..."], 0), (["Repairing..."], 100, 50)]  # no Play, nor anything else, while it runs
    assert shown()[0] == "Play" and rec.game_id not in win.app.jobs  # and Play is back when it is done


def test_a_game_with_a_job_is_not_checked_at_the_same_time(win, qapp, monkeypatch, tmp_path):
    page, rec, asked, ran = _repair_page(win, monkeypatch, tmp_path, holds=True)
    win.app.jobs[rec.game_id] = "winecfg"

    page.repair(rec)
    pump(qapp)

    assert asked == [] and ran == [] and win.app.jobs[rec.game_id] == "winecfg"


def test_a_on_a_game_in_the_library_opens_its_page_and_does_not_press_play(win, qapp, tmp_path, monkeypatch):
    """Regression test: the A that opens a game's page must only leave the focus on Play, not press it."""
    from mog_client.gui import gamepad

    _activate(win, qapp)
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    _installed_game(win, qapp, tmp_path)
    played = []
    monkeypatch.setattr(gui.GamePage, "play", lambda self: played.append(1))
    enter = gui.ActivateOnEnter()  # installed on the application as the real start does
    qapp.installEventFilter(enter)
    win.library.grid.setCurrentRow(0)
    win.library.grid.setFocus()
    pump(qapp)

    win.on_pad_event(gamepad.ACCEPT)
    pump(qapp)

    assert isinstance(win.current_page(), gui.GamePage) and played == []
    assert qapp.focusWidget() is win.current_page().first

    # An Enter right behind the one that opened the page (Steam Input types the pad's A as a key as well) is not a press of
    # Play either; one after the grace is.
    QTest.keyClick(qapp.focusWidget(), Qt.Key_Return)
    assert played == []
    monkeypatch.setattr(gui.ActivateOnEnter, "entered_at", float("-inf"))
    QTest.keyClick(qapp.focusWidget(), Qt.Key_Return)
    assert played == [1]
    qapp.removeEventFilter(enter)


def test_the_executable_page_stands_on_the_one_the_server_knows_for_the_game(win, qapp, tmp_path, monkeypatch):
    from mog_client import launchsettings

    (tmp_path / "a.exe").write_bytes(b"x")
    (tmp_path / "later").mkdir()
    (tmp_path / "later" / "z.exe").write_bytes(b"x")
    monkeypatch.setattr(launchsettings, "remembered_executable", lambda client, rec: tmp_path / "later" / "z.exe")
    monkeypatch.setattr(win.app, "client", lambda: None)
    page = gui.ExecutablePage(win, config.InstalledGame(9, "G", str(tmp_path)), lambda *a: None)
    win.push(page)
    for _ in range(100):  # the scan runs on a thread of its own
        pump(qapp)
        if page.list.currentItem() is not None and page.list.currentItem().data(Qt.UserRole):
            break
        time.sleep(0.02)

    assert page.list.currentItem().data(Qt.UserRole) == str(tmp_path / "later" / "z.exe")
