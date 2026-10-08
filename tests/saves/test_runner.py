from types import SimpleNamespace

import pytest

from mog_client.saves import runner, sync


def result(status, folders=()):
    return sync.BackupResult(status, folders=list(folders))


def test_every_status_has_a_line_for_the_window():
    assert runner.describe(result("uploaded")) == ("Saves backed up", True)
    assert runner.describe(result("unchanged")) == ("Saves already up to date", True)
    text, ok = runner.describe(result("needs-confirmation"))
    assert not ok and "open MOG" in text
    assert runner.describe(result("something-new")) == ("Save sync: something-new", False)
    assert runner.describe(None)[1] is False


def test_the_exit_code_follows_the_status():
    lines = []
    assert runner.report_sync(result("uploaded"), lines.append) == 0
    assert runner.report_sync(result("failed"), lines.append) == 1
    assert runner.report_sync(None, lines.append) == 1
    assert runner.report_sync(result("needs-confirmation", ["/a", "/b"]), lines.append) == 2
    assert "  /a\n  /b" in lines


def test_no_window_without_a_display(monkeypatch):
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert runner.window_available() is False
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    assert runner.window_available() is False


def test_the_work_runs_plainly_when_no_window_is_wanted_or_possible(monkeypatch):
    rec = SimpleNamespace(name="G")
    monkeypatch.setattr(runner, "window_available", lambda: False)
    assert runner.with_window(rec, True, lambda set_visible: "done") == "done"
    monkeypatch.setattr(runner, "window_available", lambda: True)
    assert runner.with_window(rec, False, lambda set_visible: "done") == "done"


def test_a_failing_backup_is_still_an_error_for_the_caller(monkeypatch):
    monkeypatch.setattr(runner, "window_available", lambda: False)

    def boom(set_visible):
        raise RuntimeError("server down")

    with pytest.raises(RuntimeError, match="server down"):
        runner.with_window(SimpleNamespace(name="G"), True, boom)


def _game(tmp_path):
    return SimpleNamespace(install_dir=str(tmp_path), native=False)


def test_a_game_that_stays_gone_leaves_the_window_up(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.watcher, "running_pids", lambda *a, **k: [])
    monkeypatch.setattr(runner, "END_POLL", 0.02)
    seen = []
    runner.confirm_ended(_game(tmp_path), seen.append, linger=0.1)
    assert seen == []


def test_a_game_that_comes_back_hides_the_window_until_it_really_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.watcher, "running_pids", lambda *a, **k: [1])
    monkeypatch.setattr(runner, "wait_for_game", lambda *a, **k: True)
    seen = []
    runner.confirm_ended(_game(tmp_path), seen.append, linger=1.0)
    assert seen == [False, True]


def _conflict():
    version = {"id": 9, "created_at": "2026-10-08T10:00:00+00:00"}
    return sync.StartupOutcome("conflict", "steamdeck", version)


class Asker:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def ask(self, title, text, options):
        self.asked.append((title, text, [label for label, _ in options]))
        return next((value for _, value in options if value == self.answer), None)


def _ctx(name="G"):
    return SimpleNamespace(rec=SimpleNamespace(name=name))


def test_a_newer_save_is_taken_without_asking_when_nothing_changed_here(monkeypatch):
    restored = sync.StartupOutcome("restored", "steamdeck", {"id": 9}, 3)
    monkeypatch.setattr(runner.sync, "apply_newer", lambda ctx: restored)
    ui = Asker("use")
    assert runner.take_newer(_ctx(), ui) is restored and ui.asked == []


def test_when_both_sides_changed_the_user_chooses(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.sync, "apply_newer", lambda ctx: _conflict())
    monkeypatch.setattr(runner.sync, "restore", lambda ctx, vid: calls.append(("restore", vid)) or SimpleNamespace(status="restored", restored=["a", "b"]))
    monkeypatch.setattr(runner.sync, "decline", lambda ctx, vid: calls.append(("decline", vid)))

    ui = Asker("use")
    assert runner.take_newer(_ctx("Jazz"), ui).status == "restored" and calls == [("restore", 9)]
    _title, text, labels = ui.asked[0]
    assert "steamdeck" in text and "Jazz" in text and labels == ["Use it", "Keep this machine's saves", "Ask me next time"]

    calls.clear()
    assert runner.take_newer(_ctx(), Asker("keep")).status == "kept" and calls == [("decline", 9)]


def test_an_undecided_conflict_is_left_for_the_next_start(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.sync, "apply_newer", lambda ctx: _conflict())
    monkeypatch.setattr(runner.sync, "restore", lambda *a: calls.append("restore"))
    monkeypatch.setattr(runner.sync, "decline", lambda *a: calls.append("decline"))
    log = []

    for ui in (Asker("later"), runner.NoWindow()):
        assert runner.take_newer(_ctx(), ui, log.append).status == "conflict"
    assert calls == [] and len(log) == 2


def test_the_start_outcomes_have_a_line_each():
    assert runner.describe_start(None) == ("Saves are up to date", True)
    assert runner.describe_start(sync.StartupOutcome("restored", "deck")) == ("Saves from deck restored", True)
    assert runner.describe_start(sync.StartupOutcome("setup"))[1] is False
