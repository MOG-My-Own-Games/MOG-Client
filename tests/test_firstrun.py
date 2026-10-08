"""The first-start guide and MOG Client's own Steam shortcut, in the window."""

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from mog_client import config, launcher, selfsteam, steam
from mog_client.config import Settings, load_settings
from mog_client.gui import app as gui
from mog_client.gui import firstrun
from mog_client.version import __version__


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.STYLE)
    return app


@pytest.fixture
def steam_world(tmp_path, monkeypatch):
    user = tmp_path / "Steam" / "userdata" / "1001"
    (user / "config").mkdir(parents=True)
    state = {"running": False, "users": [user]}
    monkeypatch.setattr(steam, "steam_user_dirs", lambda: state["users"])
    monkeypatch.setattr(steam, "steam_running", lambda: state["running"])
    monkeypatch.setattr(selfsteam, "client_command", lambda: "/opt/MOG/MOG.AppImage")
    return type("World", (), {"user": user, "state": state})


def make_window(qapp, monkeypatch, tmp_path, settings):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(launcher, "_known", ["wine"])
    config.save_settings(settings)
    window = gui.MainWindow(gui.App())
    window.resize(1100, 640)
    window.show()
    return window


@pytest.fixture
def win(qapp, monkeypatch, tmp_path, steam_world):
    window = make_window(qapp, monkeypatch, tmp_path, Settings(base="http://server:5000", user="u", password="p"))
    yield window
    window.pad_stop.set()
    window.close()


@pytest.fixture
def fresh(qapp, monkeypatch, tmp_path, steam_world):
    window = make_window(qapp, monkeypatch, tmp_path, Settings())
    yield window
    window.pad_stop.set()
    window.close()


def pump(qapp, times=6):
    for _ in range(times):
        qapp.processEvents()


def until(qapp, condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end and not condition():
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def buttons(window):
    return [b.text() for b in window.current_page().findChildren(QPushButton)]


# --- MOG Client in Steam ---


def test_the_settings_button_adds_and_removes_the_client(win, qapp, steam_world):
    win.open_settings()
    page = win.current_page()
    assert page.steam_button.text() == "Add to Steam" and page.steam_button.isEnabled()

    page.steam_button.click()
    pump(qapp)
    assert win.overlay.showing and "Steam library" in win.overlay.body.text()
    assert page.steam_button.text() == "Remove from Steam" and win.app.settings.steam_client
    win.overlay.dismiss()

    page.steam_button.click()
    pump(qapp)
    assert page.steam_button.text() == "Add to Steam" and win.app.settings.steam_client is None


def test_with_steam_open_the_user_is_asked_and_may_let_the_request_wait(win, qapp, steam_world, monkeypatch):
    steam_world.state["running"] = True
    monkeypatch.setattr(selfsteam, "can_close_steam", lambda: False)
    win.open_settings()
    page = win.current_page()
    page.steam_button.click()
    pump(qapp)
    asked = win.current_page()
    assert isinstance(asked, gui.ChoicePage) and asked.title == "Steam is open"
    assert [asked.list.item(i).text() for i in range(asked.list.count())] == [
        "I will close Steam myself: add it when MOG starts next",
        "Cancel",
    ]
    asked.accept()  # wait
    pump(qapp)
    assert win.overlay.showing and "next time MOG starts" in win.overlay.body.text()
    win.overlay.dismiss()
    assert page.steam_button.text() == "Add now" and not page.steam_cancel.isHidden()
    assert win.app.settings.steam_client is None and win.app.settings.steam_client_pending == str(steam_world.user)

    steam_world.state["running"] = False  # closed since
    page.steam_button.click()  # Add now
    pump(qapp)
    assert win.app.settings.steam_client and win.app.settings.steam_client_pending == ""


def test_a_waiting_request_can_be_cancelled(win, qapp, steam_world):
    win.app.settings.steam_client_pending = str(steam_world.user)
    win.open_settings()
    page = win.current_page()
    assert page.steam_button.text() == "Add now" and not page.steam_cancel.isHidden()
    page.steam_cancel.click()
    assert win.app.settings.steam_client_pending == "" and page.steam_button.text() == "Add to Steam" and page.steam_cancel.isHidden()


def test_steam_can_be_closed_for_the_user_and_opened_again(win, qapp, steam_world, monkeypatch):
    steam_world.state["running"] = True
    calls = []

    def close():
        calls.append("close")
        steam_world.state["running"] = False
        return True

    monkeypatch.setattr(selfsteam, "can_close_steam", lambda: True)
    monkeypatch.setattr(selfsteam, "close_steam", close)
    monkeypatch.setattr(selfsteam, "start_steam", lambda: calls.append("start") or True)
    win.add_client_to_steam()
    pump(qapp)
    asked = win.current_page()
    assert asked.list.item(0).text().startswith("Close Steam")
    asked.accept()  # the first answer
    assert until(qapp, lambda: win.overlay.showing)
    assert calls == ["close", "start"] and win.app.settings.steam_client
    assert "Steam is opening again" in win.overlay.body.text() and not win.busy.showing


def test_a_steam_that_will_not_close_is_said_so(win, qapp, steam_world, monkeypatch):
    steam_world.state["running"] = True
    monkeypatch.setattr(selfsteam, "can_close_steam", lambda: True)
    monkeypatch.setattr(selfsteam, "close_steam", lambda: False)
    monkeypatch.setattr(selfsteam, "start_steam", lambda: pytest.fail("Steam never closed, so it is not opened again"))
    win.add_client_to_steam()
    pump(qapp)
    win.current_page().accept()
    assert until(qapp, lambda: win.overlay.showing) and "did not close" in win.overlay.body.text()
    assert win.app.settings.steam_client is None


def test_without_steam_the_button_says_so(win, qapp, steam_world):
    steam_world.state["users"] = []
    win.open_settings()
    page = win.current_page()
    assert not page.steam_button.isEnabled() and "not found" in page._steam_row.status.text()


def test_several_steam_accounts_are_asked_which(win, qapp, steam_world, tmp_path):
    second = tmp_path / "Steam" / "userdata" / "2002"
    (second / "config").mkdir(parents=True)
    steam_world.state["users"] = [steam_world.user, second]
    win.add_client_to_steam()
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.ChoicePage) and page.title == "Which Steam account?"
    assert [page.list.item(i).text() for i in range(2)] == ["Account 1001", "Account 2002"]  # no name known: the number
    page.list.setCurrentRow(1)
    page.accept()
    assert str(second) in win.app.settings.steam_client["shortcuts_path"]


def test_an_update_asks_once_and_each_answer_is_kept(win, qapp, steam_world):
    win.offer_steam_client()
    pump(qapp)
    page = win.current_page()
    assert isinstance(page, gui.ChoicePage) and page.title == "Add MOG Client to Steam?"
    assert win.app.settings.steam_asked_for == __version__

    page.list.setCurrentRow(1)  # not now
    page.accept()
    pump(qapp)
    win.offer_steam_client()
    assert not isinstance(win.current_page(), gui.ChoicePage)  # not asked again for this version

    win.app.settings.steam_asked_for = "older"
    win.offer_steam_client()
    pump(qapp)
    win.current_page().list.setCurrentRow(2)  # never
    win.current_page().accept()
    assert win.app.settings.steam_never_ask is True
    win.app.settings.steam_asked_for = "older"
    win.offer_steam_client()
    assert not isinstance(win.current_page(), gui.ChoicePage)


def test_saying_yes_to_the_question_puts_the_client_in(win, qapp, steam_world):
    win.offer_steam_client()
    pump(qapp)
    win.current_page().accept()  # the first answer: Add to Steam
    pump(qapp)
    assert win.app.settings.steam_client and "Steam library" in win.overlay.body.text()


def test_nothing_is_asked_when_steam_is_not_there(win, qapp, steam_world):
    steam_world.state["users"] = []
    win.offer_steam_client()
    assert not isinstance(win.current_page(), gui.ChoicePage)


# --- the first-start guide ---


def test_the_guide_goes_through_its_steps_and_the_server_has_to_answer(fresh, qapp, monkeypatch):
    fresh.open_first_run()
    page = fresh.current_page()
    assert isinstance(page, firstrun.FirstRunPage) and page.steps == ["welcome", "server", "games", "saves", "steam", "done"]
    assert page.progress.text() == "Step 1 of 6" and not page.back_button.isVisibleTo(page)

    page.advance()
    assert page.step == "server" and not page.next_button.isEnabled()  # it must answer first

    page.url.setText("server:5000")
    page.user.setText("admin")
    page.password.setText("secret")
    monkeypatch.setattr(firstrun, "MogClient", lambda client: type("C", (), {"me": lambda self: (_ for _ in ()).throw(RuntimeError("HTTP 401"))})())
    page.test_connection()
    assert until(qapp, lambda: "Could not sign in: HTTP 401" in page.server_status.text())
    assert not page.next_button.isEnabled() and fresh.app.settings.base == ""  # nothing is taken that did not answer

    monkeypatch.setattr(firstrun, "MogClient", lambda client: type("C", (), {"me": lambda self: {"username": "admin"}})())
    page.test_connection()
    assert until(qapp, lambda: page.connected)
    assert page.server_status.text() == "Connected as admin." and page.next_button.isEnabled()
    saved = load_settings()
    assert (saved.base, saved.user, saved.password) == ("http://server:5000", "admin", "secret")

    page.url.setText("http://other:5000")
    page.url.textEdited.emit("x")  # editing the address undoes the answer
    assert not page.connected and not page.next_button.isEnabled()


def _through_the_server(page, monkeypatch, qapp):
    monkeypatch.setattr(firstrun, "MogClient", lambda client: type("C", (), {"me": lambda self: {"username": "admin"}})())
    page.advance()
    page.url.setText("http://server:5000")
    page.user.setText("admin")
    page.password.setText("secret")
    page.test_connection()
    assert until(qapp, lambda: page.connected)


def test_finishing_applies_the_choices_and_loads_the_library(fresh, qapp, monkeypatch, tmp_path):
    refreshed = []
    monkeypatch.setattr(fresh.app, "refresh", lambda: refreshed.append(1))
    fresh.open_first_run()
    page = fresh.current_page()
    _through_the_server(page, monkeypatch, qapp)

    page.advance()
    assert page.step == "games"
    chosen = tmp_path / "games"
    chosen.mkdir()
    page.install_dir, page.folder_chosen = str(chosen), True
    page._update_folder()
    assert page.folder_label.text() == str(chosen) and "free there" in page.space_label.text()

    page.advance()
    assert page.step == "saves"
    page.sync_saves.setChecked(False)
    assert not page.sync_start.isEnabled()
    page.advance()
    assert page.step == "steam"
    page.advance()
    assert page.step == "done" and page.next_button.text() == "Open my library"
    assert "http://server:5000 (admin)" in page.summary.text() and "kept on this computer only" in page.summary.text()

    page.advance()
    saved = load_settings()
    assert saved.first_run_done and saved.install_dirs == [str(chosen)] and saved.sync_saves is False
    assert saved.steam_asked_for == __version__ and refreshed == [1]
    assert fresh.current_page() is not page  # the guide is gone


def test_the_steam_step_is_left_out_when_steam_is_not_there(fresh, qapp, steam_world):
    steam_world.state["users"] = []
    fresh.open_first_run()
    page = fresh.current_page()
    assert "steam" not in page.steps and page.steps[-1] == "done"


def test_the_steam_step_adds_the_client_and_says_so(fresh, qapp, monkeypatch):
    fresh.open_first_run()
    page = fresh.current_page()
    page.go(page.steps.index("steam"))
    page.steam_button.click()
    pump(qapp)
    fresh.overlay.dismiss()
    page._update_steam()
    assert page.steam_status.text() == "MOG Client is in your Steam library." and fresh.app.settings.steam_client


def test_setting_up_later_marks_the_guide_done_and_opens_settings(fresh, qapp):
    fresh.open_first_run()
    page = fresh.current_page()
    page.skip()
    pump(qapp)
    assert load_settings().first_run_done and isinstance(fresh.current_page(), gui.SettingsPage)


def test_back_goes_to_the_step_before_and_not_past_the_first(fresh, qapp):
    fresh.open_first_run()
    page = fresh.current_page()
    page.advance()
    page.advance()
    page.previous()
    assert page.step == "server"
    page.previous()
    page.previous()
    assert page.step == "welcome" and not page.back_button.isVisibleTo(page)


# --- running the guide again from Settings ---


def test_the_guide_can_be_run_again_from_settings_and_starts_from_what_is_set(win, qapp, monkeypatch):
    win.app.settings.install_dirs = ["/games/a", "/games/b"]
    win.app.settings.sync_saves = False
    win.open_settings()
    win.current_page().findChildren(QPushButton)  # the settings page is up
    win.open_first_run(rerun=True)
    page = win.current_page()
    assert page.rerun and page.url.text() == "http://server:5000" and page.skip_button.text() == "Cancel"
    page.advance()
    assert page.step == "server" and page.connected and page.next_button.isEnabled()  # it was working: it is not asked again
    assert page.sync_saves.isChecked() is False and page.install_dir == "/games/a"

    page.url.textEdited.emit("x")
    assert not page.connected  # editing the address does undo it


def test_running_the_guide_again_changes_only_what_was_chosen(win, qapp, tmp_path):
    win.app.settings.install_dirs = ["/games/a", "/games/b"]
    win.open_settings()
    win.open_first_run(rerun=True)
    page = win.current_page()
    page.go(page.steps.index("saves"))
    page.sync_start.setChecked(False)
    page.go(page.steps.index("done"))
    page.advance()

    saved = load_settings()
    assert saved.install_dirs == ["/games/a", "/games/b"]  # no folder chosen: the list is left alone
    assert saved.sync_on_start is False and saved.base == "http://server:5000" and saved.user == "u"
    assert not isinstance(win.current_page(), (firstrun.FirstRunPage, gui.SettingsPage))  # both closed: Settings holds old values


def test_a_chosen_folder_goes_first_and_the_others_stay(win, qapp, tmp_path):
    win.app.settings.install_dirs = ["/games/a", "/games/b"]
    win.open_first_run(rerun=True)
    page = win.current_page()
    page.install_dir, page.folder_chosen = "/games/b", True
    page.finish()
    assert load_settings().install_dirs == ["/games/b", "/games/a"]


def test_cancelling_the_guide_from_settings_changes_nothing(win, qapp):
    win.open_settings()
    settings_page = win.current_page()
    win.open_first_run(rerun=True)
    page = win.current_page()
    page.sync_saves.setChecked(False)
    page.skip()
    assert win.current_page() is settings_page and load_settings().sync_saves is True


def test_the_settings_page_has_the_button(win, qapp):
    win.open_settings()
    texts = [b.text() for b in win.current_page().findChildren(QPushButton)]
    assert "Run again" in texts
