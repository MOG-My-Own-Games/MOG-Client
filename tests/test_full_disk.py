"""With the disk full the client still opens (removing games is how someone gets room again), and an install that ends on
a full disk still tells the window it ended."""

import errno
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mog_client import activity, config, logstore, manager  # noqa: E402
from mog_client.config import Settings  # noqa: E402
from mog_client.gui import app as gui  # noqa: E402


def full(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.STYLE)
    return app


def test_the_client_opens_when_none_of_its_own_files_can_be_written(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    config.save_settings(Settings(base="http://server:5000", user="u", password="p", first_run_done=False))

    class Reuse(QApplication):  # the one the tests already have: a second one cannot be made
        def __new__(cls, *_args):
            return qapp

    monkeypatch.setattr(gui, "QApplication", Reuse)
    monkeypatch.setattr(QApplication, "exec", lambda *_a: 0)
    monkeypatch.setattr(gui, "send_to_running", lambda _link: False)
    monkeypatch.setattr(gui.Listener, "listen", lambda _self: False)
    monkeypatch.setattr(gui.App, "refresh", lambda _self: None)
    monkeypatch.setattr(gui.MainWindow, "show", lambda _self: None)
    monkeypatch.setattr(gui.protocol, "register", full)
    monkeypatch.setattr(gui.crashlog, "install", lambda: False)
    monkeypatch.setattr(gui.logstore, "write_to_file", lambda _path: None)
    monkeypatch.setattr(gui, "ensure_scanned", full)
    monkeypatch.setattr(gui.selfsteam, "settle", full)
    monkeypatch.setattr(gui, "remember_client", full)
    monkeypatch.setattr(gui, "save_settings", full)

    assert gui.run_gui() == 0

    assert any("No space left" in r.text for r in logstore.store.records())  # said in the log, not thrown


def test_a_failed_write_leaves_no_half_written_copy_behind(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text('{"kept": true}')
    monkeypatch.setattr(config.Path, "replace", full)

    with pytest.raises(OSError):
        config._write_json(target, {"new": True})

    assert target.read_text() == '{"kept": true}' and not (tmp_path / "state.json.tmp").exists()


def test_the_list_of_what_is_running_is_skipped_on_a_full_disk(monkeypatch):
    monkeypatch.setattr(activity, "_write_json", full)
    activity.add_install(3)  # does not raise
    activity.remove_install(3)


def test_an_install_that_ends_on_a_full_disk_still_tells_the_window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    window = gui.MainWindow(gui.App())
    monkeypatch.setattr(manager, "run_install", lambda *a, **k: full())
    monkeypatch.setattr(gui.activity, "remove_install", full)
    game = {"id": 5, "name": "Eta", "library_id": None, "igdb_id": None}
    window.app.set_games([game])
    ended = []
    window.app.bridge.finished.connect(lambda gid, err: ended.append((gid, err)))

    window.app.start_install(game)
    import time

    deadline = time.monotonic() + 15
    while not ended and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert ended and ended[0][0] == 5 and "disk is full" in ended[0][1]
    assert 5 not in window.app.installs  # not left "installing" for good
    window.pad_stop.set()
    window.close()


def test_the_sort_and_filter_choices_work_when_the_settings_cannot_be_saved(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    window = gui.MainWindow(gui.App())
    window.app.set_games(
        [
            {"id": 1, "name": "Played", "library_id": None, "last_played": "2026-10-01T10:00:00"},
            {"id": 2, "name": "Never", "library_id": None},
        ]
    )
    monkeypatch.setattr(gui, "save_settings", full)
    library = window.library
    item = next(library.sorts.item(i) for i in range(library.sorts.count()) if library.sorts.item(i).text() == "Got saves")

    library._sort_chosen(item)  # the disk is full: the choice still shows

    assert window.app.settings.filter_saves is True and library.grid.count() == 1
    library.sorts_section.heading.click()  # folding a section does not fail either
    assert not library.sorts.isVisibleTo(library)
    window.app.settings.filter_saves = False
    window.pad_stop.set()
    window.close()
