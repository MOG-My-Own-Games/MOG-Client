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


# --- saves waiting for the prefix are put in as soon as it is made ---

from pathlib import Path  # noqa: E402

from fakes import FakeServer  # noqa: E402

from mog_client.config import InstalledGame, Settings  # noqa: E402
from mog_client.launcher import pfx_dir  # noqa: E402
from mog_client.saves import prefix as prefixes  # noqa: E402
from mog_client.saves.devices import DeviceRecord  # noqa: E402
from mog_client.saves.state import load_state  # noqa: E402


class Clock:
    """A clock the waits advance by sleeping, so nothing really waits."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


def make_prefix(root: Path) -> None:
    (root / "drive_c/users/steamuser").mkdir(parents=True, exist_ok=True)
    for name in prefixes.MADE_MARKERS:
        (root / name).write_text("x")


def test_waiting_ends_once_the_prefix_is_made_and_has_stayed_still(tmp_path):
    clock, prefix = Clock(), tmp_path / "pfx"
    polls = []

    def sleep(seconds):
        polls.append(clock.now)
        clock.sleep(seconds)
        if len(polls) == 3:
            make_prefix(prefix)  # the launcher finishes while we wait

    assert runner.wait_until_made(prefix, timeout=60, quiet=3, poll=1, sleep=sleep, clock=clock) is True
    assert clock.now >= 3 + 3  # made at 3, then still for the quiet time


def test_waiting_goes_on_while_the_profile_folder_is_still_changing(tmp_path):
    clock, prefix = Clock(), tmp_path / "pfx"
    make_prefix(prefix)
    steps = []

    def sleep(seconds):
        clock.sleep(seconds)
        if len(steps) < 4:
            (prefix / f"drive_c/users/steamuser/f{len(steps)}").write_text("x")  # still being filled
            steps.append(clock.now)

    assert runner.wait_until_made(prefix, timeout=60, quiet=3, poll=1, sleep=sleep, clock=clock) is True
    assert clock.now > steps[-1] + 2  # it waited for the folder to settle after the last change


def test_waiting_gives_up_when_the_prefix_never_comes_or_stop_says_so(tmp_path):
    clock = Clock()
    assert runner.wait_until_made(tmp_path / "none", timeout=10, poll=1, sleep=clock.sleep, clock=clock) is False
    assert clock.now >= 10
    assert runner.wait_until_made(tmp_path / "none", stop=lambda: True, sleep=clock.sleep, clock=clock) is False


@pytest.fixture
def waiting(tmp_path, monkeypatch):
    """A game whose saves from another machine wait for its (empty) pfx folder."""
    from mog_client import config
    from mog_client.saves import state as state_module

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(state_module, "data_dir", lambda: tmp_path / "data")
    install = tmp_path / "G"
    install.mkdir()
    rec = InstalledGame(game_id=7, name="G", install_dir=str(install), executable=str(install / "g.exe"), state="installed")
    rec.prefix = str(pfx_dir(rec))
    pfx_dir(rec).mkdir()
    server = FakeServer()
    ctx = sync.Context(rec, Settings(), server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), "faugus")
    version = server.add_foreign_version({"users/USER/Saved Games/s.sav": b"theirs"})
    assert sync.restore(ctx, version["id"]).status == "needs-prefix"
    return SimpleNamespace(ctx=ctx, rec=rec, server=server, prefix=pfx_dir(rec))


def test_the_saves_go_in_as_soon_as_the_launcher_has_made_the_prefix(waiting):
    clock, said = Clock(), []

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.now == 2:
            make_prefix(waiting.prefix)

    thread = runner.restore_when_made(waiting.ctx, said.append, sleep=sleep, clock=clock, timeout=60, quiet=3, poll=1)
    thread.join(5)

    assert (waiting.prefix / "drive_c/users/steamuser/Saved Games/s.sav").read_bytes() == b"theirs"
    assert load_state(7).pending_restore is None
    assert len(said) == 1 and "close it and start it again" in said[0]


def test_a_file_the_game_made_first_is_not_overwritten_and_the_saves_keep_waiting(waiting):
    make_prefix(waiting.prefix)
    target = waiting.prefix / "drive_c/users/steamuser/Saved Games/s.sav"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fresh")
    said = []

    clock = Clock()
    thread = runner.restore_when_made(waiting.ctx, said.append, sleep=clock.sleep, clock=clock, timeout=30)
    thread.join(5)
    assert not thread.is_alive()

    assert target.read_bytes() == b"fresh" and load_state(7).pending_restore and said == []


def test_nothing_waits_when_there_is_no_pending_restore(waiting):
    from mog_client.saves.state import save_state

    state = load_state(7)
    state.pending_restore = None
    save_state(7, state)
    assert runner.restore_when_made(waiting.ctx, print) is None
