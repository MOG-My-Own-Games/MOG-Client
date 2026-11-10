"""Quiver Launcher's games in the window: a library of their own, started from here for the first time."""

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mog_client import config, launcher, manager, quiver  # noqa: E402
from mog_client.config import Settings, load_library  # noqa: E402
from mog_client.gui import app as gui  # noqa: E402

QID = "github:Zelda64Recomp/Zelda64Recomp"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.STYLE)
    return app


@pytest.fixture
def quiver_data(tmp_path):
    data = tmp_path / "quiver"
    program = data / "apps" / "Zelda64Recompiled" / "Zelda64Recompiled"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"\x7fELF....")
    (data / "library.json").write_text(json.dumps([{"id": QID, "overrides": {"name": "Zelda 64: Recompiled"}}]))
    (data / "installs.json").write_text(json.dumps({QID: {"folder": "Zelda64Recompiled", "executables": ["Zelda64Recompiled"]}}))
    return data


def make_window(qapp, monkeypatch, tmp_path, **settings):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(launcher, "_known", ["faugus", "umu", "wine"])
    config.save_settings(Settings(base="http://server:5000", user="u", password="p", **settings))
    app = gui.App()
    monkeypatch.setattr(app, "fetch_videos", lambda game_id: None)
    window = gui.MainWindow(app)
    window.resize(1100, 640)
    window.show()
    return window


@pytest.fixture
def win(qapp, monkeypatch, tmp_path, quiver_data):
    window = make_window(qapp, monkeypatch, tmp_path, quiver_library=True, quiver_dir=str(quiver_data))
    yield window
    window.pad_stop.set()
    window.close()


def pump(qapp):
    for _ in range(4):
        qapp.processEvents()


def wait_for(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(qapp)
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_the_games_quiver_installed_are_a_library_of_their_own_next_to_the_servers(win, qapp):
    win.set_games([{"id": 5, "name": "Other", "fs_name": "Other", "library_id": 1, "igdb_id": None}])
    win.library.set_libraries([{"id": 1, "name": "Games"}])
    pump(qapp)

    names = [win.library.libs.item(i).text() for i in range(win.library.libs.count())]
    assert names == ["All games", "Games", "Quiver Launcher"]
    stand = next(g for g in win.app.games.values() if g.get("quiver"))
    assert stand["id"] < 0 and stand["library_id"] == quiver.LIBRARY_ID

    win.library.libs.setCurrentRow(2)
    pump(qapp)
    assert list(win.library.items) == [stand["id"]]


def test_with_the_library_off_nothing_of_quiver_is_listed(qapp, monkeypatch, tmp_path, quiver_data):
    window = make_window(qapp, monkeypatch, tmp_path, quiver_dir=str(quiver_data))
    try:
        window.set_games([])
        window.library.set_libraries([{"id": 1, "name": "Games"}])
        assert [window.library.libs.item(i).text() for i in range(window.library.libs.count())] == ["All games", "Games"]
        assert not [g for g in window.app.games.values() if g.get("quiver")]
    finally:
        window.pad_stop.set()
        window.close()


def test_turning_the_library_on_adds_its_games_and_off_takes_them_away_but_keeps_the_choice(win, qapp):
    win.use_quiver(False)
    assert not [g for g in win.app.games.values() if g.get("quiver")] and config.load_settings().quiver_library is False
    win.use_quiver(True)
    assert [g["name"] for g in win.app.games.values() if g.get("quiver")] == ["Zelda 64: Recompiled"]
    assert config.load_settings().quiver_library is True


def test_the_first_start_from_here_makes_the_server_entry_and_a_record_and_then_plays(win, qapp, monkeypatch):
    win.set_games([])
    win.library.set_libraries([])
    stand = next(g for g in win.app.games.values() if g.get("quiver"))
    asked = []

    class Server:
        def import_game(self, name, library_id=None):
            asked.append(name)
            return {"id": 77, "name": name, "fs_name": name, "library_id": None, "igdb_id": None}

        def game_sizes(self, game_id):
            return {"installer": 0, "installed": 0}

        def __getattr__(self, name):  # whatever else the new game's page asks the server for: nothing
            def nothing(*args, **kwargs):
                raise RuntimeError("not in this test")

            return nothing

    monkeypatch.setattr(win.app, "client", lambda: Server())
    monkeypatch.setattr(manager, "finish_setup", lambda *a, **k: False)
    played = []
    monkeypatch.setattr(gui.GamePage, "_launch", lambda self, rec, starting=False: played.append(rec.game_id))
    win.show_game(stand["id"])
    page = win.game_pages[stand["id"]]

    page.play_quiver()

    assert wait_for(qapp, lambda: played)
    assert asked == ["Zelda 64: Recompiled"] and played == [77]
    rec = load_library()[77]
    assert (rec.quiver_id, rec.imported, rec.explicit_saves) == (QID, True, True)
    assert 77 in win.app.games and win.app.games[77]["library_id"] == quiver.LIBRARY_ID
    assert not [g for g in win.app.games.values() if g["id"] < 0]  # the stand-in is gone: the record's entry took its place


def test_a_server_that_cannot_be_reached_leaves_the_game_listed_and_says_why(win, qapp, monkeypatch):
    win.set_games([])
    stand = next(g for g in win.app.games.values() if g.get("quiver"))

    class Server:
        def import_game(self, name, library_id=None):
            raise RuntimeError("out of reach")

    monkeypatch.setattr(win.app, "client", lambda: Server())
    win.show_game(stand["id"])
    win.game_pages[stand["id"]].play_quiver()

    assert wait_for(qapp, lambda: win.overlay.showing)
    assert "out of reach" in win.overlay.body.text() and not load_library()
    assert stand["id"] in win.app.games


def test_a_quiver_game_cannot_be_moved_into_mog(win):
    rec = config.InstalledGame(game_id=9, name="Z", install_dir="/x", imported=True, quiver_id=QID)
    with pytest.raises(manager.MoveError, match="Quiver"):
        manager.move_into_mog(rec, Settings())
