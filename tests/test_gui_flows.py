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
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

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


def test_no_room_anywhere_says_so(win, qapp, monkeypatch):
    started, _ = _install(win, qapp, monkeypatch, ["/a"], {"/a": 1024**3})
    assert started == [] and win.overlay.showing and "No install folder has room" in win.overlay.body.text()


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


def test_unread_notifications_are_a_red_dot_with_the_number_on_the_bell_corner(win, qapp):
    button = win.notif_btn
    assert button.text() == "" and button.toolTip() == "Notifications"  # only the bell, like the server's
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

    win.on_notifications({"notifications": [], "unread": 0})
    pump(qapp)
    assert button.count == 0 and button.grab().toImage() == quiet


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
    assert started == [False] and not isinstance(win.current_page(), gui.ConfirmPage)


def test_a_game_that_is_not_an_archive_is_not_looked_inside(win, qapp, monkeypatch):
    server = FakeServer(default={"candidates": [{"path": "setup.exe", "file_name": "setup.exe", "kind": "known installer", "category": "game"}]})
    started = _extraction_win(win, monkeypatch, server)
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [False] and server.asked == [None]


def test_a_failed_lookup_never_stops_the_install(win, qapp, monkeypatch):
    class Broken(FakeServer):
        def candidates(self, gid, source=None):
            raise RuntimeError("HTTP 500")

    started = _extraction_win(win, monkeypatch, Broken())
    win.install_game({"id": 7, "name": "Game"})
    assert _wait_for(qapp, lambda: started)
    assert started == [False]


def test_a_game_that_was_extracted_on_purpose_is_not_asked_again(win, qapp, monkeypatch, tmp_path):
    server = FakeServer()
    started = _extraction_win(win, monkeypatch, server)
    config.save_library({7: config.InstalledGame(7, "Game", str(tmp_path / "Game"), state="installing", extract_only=True)})
    win.install_game({"id": 7, "name": "Game"})
    assert started == [False] and server.asked == []  # it resumes; its record remembers the choice


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
    assert started == [False] and server.asked == []


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


def test_the_notifications_button_is_on_a_games_page_too(win, qapp, monkeypatch):
    monkeypatch.setattr(win.app, "fetch_header_art", lambda game: None)
    monkeypatch.setattr(win.app, "load_size", lambda gid: None)
    win.app.set_games([{"id": 41, "name": "Eta", "library_id": None}])
    assert win.notif_btn.isVisibleTo(win)  # the library has it
    win.show_game(41)
    pump(qapp)
    assert isinstance(win.current_page(), gui.GamePage) and win.notif_btn.isVisibleTo(win)
    assert not win.settings_btn.isVisibleTo(win) and not win.reload_btn.isVisibleTo(win)  # only the bell joins it

    win.on_notifications({"notifications": [{"id": 1, "title": "x", "read": False}], "unread": 1})
    assert win.notif_btn.count == 1  # and it counts there as well
    win.notif_btn.click()
    pump(qapp)
    assert isinstance(win.current_page(), gui.NotificationsPage)
    win.back()
    assert isinstance(win.current_page(), gui.GamePage)  # back to the game


def test_the_other_pages_do_not_show_the_bell(win, qapp):
    win.open_settings()
    pump(qapp)
    assert not win.notif_btn.isVisibleTo(win)
