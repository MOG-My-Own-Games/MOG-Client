"""The window driven the way the pad and keyboard drive it, on an offscreen display."""

import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from mog_client import config, installdirs, launcher, logstore  # noqa: E402
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
    assert page.sync_start_box in chain and page.launcher in chain and page.folders_button in chain

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
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None: started.append(game["id"]))
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
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
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None: started.append(game["id"]))
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


def test_the_library_puts_installed_games_first_and_not_the_ones_only_waiting_for_an_executable(win, qapp, monkeypatch, tmp_path):
    games = [{"id": i, "name": name, "igdb_id": None} for i, name in ((1, "Alpha"), (2, "Bravo"), (3, "Charlie"), (4, "Delta"))]
    win.app.set_games(games)
    states = {2: "installed", 1: "awaiting_executable", 3: "installed"}
    recs = {gid: config.InstalledGame(game_id=gid, name="x", install_dir=str(tmp_path), state=state) for gid, state in states.items()}
    monkeypatch.setattr(gui, "load_library", lambda: recs)
    win.library.populate("")
    names = [win.library.grid.item(i).text().split("\n")[0] for i in range(win.library.grid.count())]
    assert names == ["Bravo", "Charlie", "Alpha", "Delta"]  # Alpha is "Setup needed": no better than Delta

    recs[1] = config.InstalledGame(game_id=1, name="x", install_dir=str(tmp_path), state="installed")
    win.library.update_label(1)
    qapp.processEvents()
    names = [win.library.grid.item(i).text().split("\n")[0] for i in range(win.library.grid.count())]
    assert names == ["Alpha", "Bravo", "Charlie", "Delta"]  # finishing the setup moves it into the installed ones


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
    assert win.user_btn.isVisibleTo(win) and _menu_texts(win) == ["Settings", "Notifications", "Refresh library", "About", "Sign out"]
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
    assert kinds == ["Settings", "Notifications", "Refresh library", "-", "About", "-", "Sign out"]

    win.user_menu.actions()[4].trigger()
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.AboutPage) and page.title == "About" and page.about.github.text() == "Open the GitHub page"
    win.back()

    win.open_settings()
    tabs = win.current_page().tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Server", "Logs"]


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
    assert {w.text() for w in seen if isinstance(w, QPushButton)} >= {"Browse...", "Later", "Use this executable"}

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
        confirm=True,
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
    win.choose("Restore which save?", "x", [("one", 1)], lambda v: None, confirm=True)
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
    from mog_client import manager

    folder = tmp_path / "Eta"
    folder.mkdir()
    (folder / "game.exe").write_bytes(b"x")
    page, game = _game_page_with_mods(win, qapp, monkeypatch, [])
    rec = config.InstalledGame(41, "Eta", str(folder), state=state, executable=str(folder / "game.exe"))
    monkeypatch.setattr(manager, "finish_setup", lambda *a, **k: None)
    monkeypatch.setattr(win.app, "client", lambda: None)
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
    rows = [picker.list.item(i).text() for i in range(picker.list.count())]
    assert not any("Let the server choose" in r for r in rows) and any("gothic2.exe" in r for r in rows)

    picker.choose(next(picker.list.item(i) for i in range(picker.list.count()) if picker.list.item(i).data(Qt.UserRole)))
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
    for _ in range(100):
        if 5 not in win.app.installs:
            break
        pump(qapp)
        time.sleep(0.02)

    assert ran == [[5]]  # remembered while it ran
    assert activity.load()["installs"] == []  # and forgotten once it ended


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
    win.resize(1000, 450)
    pump(qapp)
    page = win.current_page()
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
    return [picker.list.item(i).text() for i in range(picker.list.count())]


EXE = {"path": "Metroid.exe", "file_size_bytes": 1000, "kind": "executable (top level)", "category": "game"}


def test_a_folder_with_no_installer_offers_its_executables_and_just_extract(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False: started.append((installer, extract)))

    picker.on_installers(41, [EXE], "", True)
    rows = _rows(picker)
    assert any("Just extract" in r for r in rows) and any("Metroid.exe" in r for r in rows)
    assert not any("Let the server choose" in r for r in rows) and "probably the game itself" in picker.status.text()

    picker.choose(next(picker.list.item(i) for i in range(picker.list.count()) if "Just extract" in picker.list.item(i).text()))
    assert started == [(None, True)]


def test_an_executable_can_still_be_picked_from_that_folder(win, qapp, monkeypatch):
    page, picker = _picker_for(win, qapp, monkeypatch)
    started = []
    monkeypatch.setattr(win, "install_game", lambda game, installer=None, then=None, extract=False: started.append((installer, extract)))

    picker.on_installers(41, [EXE], "", True)
    picker.choose(next(picker.list.item(i) for i in range(picker.list.count()) if "Metroid.exe" in picker.list.item(i).text()))
    assert started == [(EXE, False)]


def test_just_extract_is_only_offered_when_the_server_found_no_installer(win, qapp, monkeypatch):
    _page, picker = _picker_for(win, qapp, monkeypatch)
    picker.on_installers(41, [EXE], "", False)
    assert not any("Just extract" in r for r in _rows(picker))


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
    picker._activate(picker.list.item(0))  # "Use this folder"
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


def test_a_click_selects_a_mod_and_does_not_start_its_download(win, qapp, monkeypatch, tmp_path):
    game_page, _folder = _game_with_mods_and_a_copy(win, qapp, monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr(win.app, "download_mod", lambda game, mod: started.append(mod["name"]))
    game_page.open_mods()
    page = win.current_page()

    page.list.itemClicked.emit(page.list.item(1))
    assert started == []
    page.list.itemActivated.emit(page.list.item(1))  # Enter
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


def test_sort_by_sits_right_under_the_libraries_not_at_the_bottom_of_the_sidebar(win, qapp):
    win.library.set_libraries([{"id": 1, "name": "Games"}, {"id": 2, "name": "Restricted"}])
    pump(qapp)
    libs, sorts = win.library.libs, win.library.sorts
    assert libs.geometry().bottom() < sorts.geometry().top() < libs.geometry().bottom() + 80
    assert sorts.geometry().bottom() < win.library.sidebar.height() - 20  # free space is left under it
    heading = [lbl for lbl in win.library.sidebar.findChildren(QLabel) if lbl.text().lower() == "sort by"]
    assert heading and heading[0].geometry().top() > libs.geometry().bottom()


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


def test_a_cover_with_a_task_carries_a_turning_ring_for_as_long_as_it_runs(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QAbstractAnimation

    from mog_client.gui.saves_ui import RestoreJob

    _a_game_is_listed(win, qapp, tmp_path, monkeypatch)
    library = win.library
    item = library.items[1]
    assert not item.data(gui.ROLE_BUSY) and library._spin.state() != QAbstractAnimation.Running

    win.saves.restoring[1] = RestoreJob(percent=10)  # a download of saves
    win.refresh_tasks()
    assert item.data(gui.ROLE_BUSY) is True and library._spin.state() == QAbstractAnimation.Running
    before = library.grid.itemDelegate().angle
    assert _until(qapp, lambda: library.grid.itemDelegate().angle != before)  # it turns

    del win.saves.restoring[1]
    win.refresh_tasks()
    assert not item.data(gui.ROLE_BUSY) and library._spin.state() != QAbstractAnimation.Running


def test_an_install_download_and_a_populate_keep_the_ring_on_the_cover(win, qapp, tmp_path, monkeypatch):
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
