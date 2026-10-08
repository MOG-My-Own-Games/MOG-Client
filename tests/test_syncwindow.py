import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mog_client.config import InstalledGame
from mog_client.gui import syncwindow


@pytest.fixture
def rec(tmp_path):
    return InstalledGame(game_id=3, name="Jazz Jackrabbit 2", install_dir=str(tmp_path), state="installed")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(syncwindow, "CLOSE_OK_MS", 10)
    monkeypatch.setattr(syncwindow, "CLOSE_FAILED_MS", 10)


def test_the_window_shows_the_work_and_hands_back_its_result(rec):
    seen = []
    result = syncwindow.run(rec, lambda set_visible: "uploaded", lambda r: seen.append(r) or ("Saves backed up", True))
    assert result == "uploaded" and seen == ["uploaded"]


def test_what_the_work_raised_is_raised_after_the_window_closes(rec):
    def boom(set_visible):
        raise RuntimeError("server down")

    with pytest.raises(RuntimeError, match="server down"):
        syncwindow.run(rec, boom, lambda r: ("never", True))


def test_the_work_can_hide_and_show_the_window(rec):

    def work(set_visible):
        set_visible(False)
        set_visible(True)
        return "ok"

    assert syncwindow.run(rec, work, lambda r: ("done", True)) == "ok"


def test_the_cover_the_library_cached_is_preferred_to_the_small_icon(rec, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(syncwindow, "data_dir", lambda: tmp_path / "data")
    covers = tmp_path / "data" / "covers"
    covers.mkdir(parents=True)
    for path, size in ((covers / "3-abc.img", 300), (tmp_path / ".mog-icon", 32)):
        image = QImage(size, size, QImage.Format_RGB32)
        image.fill(QColor("red"))
        image.save(str(path), "PNG")
    assert syncwindow._cover(rec).width() == 300
    (covers / "3-abc.img").unlink()
    assert syncwindow._cover(rec).width() == 32


def test_the_window_tells_the_game_and_then_the_outcome(rec, tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    window = syncwindow.SyncWindow(rec.name, syncwindow._cover(rec))
    assert window.name.text() == "Jazz Jackrabbit 2" and window.heading.text() == "Syncing saves"
    window.show_result("Saves backed up", True)
    assert window.heading.text() == "Saves backed up" and window.card.property("level") == ""
    window.show_result("Save sync failed: x", False)
    assert window.card.property("level") == "error"


def test_the_runner_drives_the_real_window(rec, monkeypatch):
    from mog_client.saves import runner

    monkeypatch.setattr(runner, "window_available", lambda: True)
    seen = []

    def work(set_visible):
        seen.append(set_visible)
        return runner.sync.BackupResult("uploaded")

    assert runner.with_window(rec, True, work).status == "uploaded" and len(seen) == 1


def _answer_when_asked(pick):
    """Press the pick-th button of the question as soon as the window shows one (the GUI thread is busy in `run`)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def poll():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, syncwindow.SyncWindow) and widget._question is not None:
                pick(widget)
                return
        QTimer.singleShot(20, poll)

    QTimer.singleShot(20, poll)


def test_the_work_can_ask_and_gets_the_chosen_option(rec):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    _answer_when_asked(lambda w: w.buttons[1].click())
    asked = []

    def work(ui):
        asked.append(ui.ask("A newer save exists", "steamdeck saved it", [("Use it", "use"), ("Keep", "keep")]))
        return "done"

    assert syncwindow.run(rec, work, lambda r: ("ok", True)) == "done" and asked == ["keep"]


def test_back_on_the_pad_answers_nothing(rec):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    _answer_when_asked(lambda w: w.on_pad("back"))
    asked = []
    syncwindow.run(rec, lambda ui: asked.append(ui.ask("Q", "t", [("A", 1)])), lambda r: ("ok", True))
    assert asked == [None]
