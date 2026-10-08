"""The save-sync flows of the window, with a stand-in window that answers the questions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client.config import InstalledGame, Settings, save_library
from mog_client.gui import saves_ui
from mog_client.gui.saves_ui import SaveSync
from mog_client.launcher import pfx_dir
from mog_client.saves import sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.devices import DeviceRecord, save_device
from mog_client.saves.state import load_state


class FakeWin:
    def __init__(self, server):
        self.server = server
        self.settings = Settings(base="http://x", user="u", password="p")
        self.app = SimpleNamespace(
            settings=self.settings,
            client=lambda: self.server,
            bridge=SimpleNamespace(call=SimpleNamespace(emit=lambda fn: fn())),
            run_bg=self.run_bg,
        )
        self.notes: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.choices: list[tuple] = []
        self.skips: list[str | None] = []
        self.checklists: list[tuple] = []
        self.asks: list[tuple] = []
        self.browsed: list[Path] = []
        self.errors: list[str] = []
        self.busy = None
        self.task_refreshes = 0
        self.local_notices: list[tuple] = []
        self.busy_log: list[str] = []
        self.progress: list[tuple] = []

    def run_bg(self, fn, on_error=None):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - what the real run_bg does
            self.errors.append(str(e))
            if on_error:
                on_error(str(e))

    def show_busy(self, title, name, game_id=None, on_cancel=None):
        self.busy = (title, name, on_cancel)
        self.busy_log.append(title)

    def busy_progress(self, done, total, detail=None):
        self.progress.append((done, total, detail))

    def hide_busy(self):
        self.busy = None

    def refresh_tasks(self):
        self.task_refreshes += 1

    def add_local_notification(self, kind, title, body, game_id=None):
        self.local_notices.append((kind, title, body, game_id))

    def notify(self, text, level="info"):
        self.notes.append(text)

    def message(self, text, level="error"):
        self.messages.append((level, text))
        self.notes.append(text)

    def choose(self, title, text, options, on_choose, skip=None):
        self.choices.append((title, options, on_choose))
        self.skips.append(skip)

    def checklist(self, title, text, items, on_done):
        self.checklists.append((title, items, on_done))

    def ask(self, text, on_yes, on_no=None, danger=False):
        self.asks.append((text, on_yes, on_no))

    def browse_folder(self, start, on_pick):
        self.browsed.append(start)
        self.on_pick = on_pick


class Server(FakeServer):
    def __init__(self):
        super().__init__()
        self.register_replies = []
        self.registered = []

    def register_device(self, uid, host, plat, os_id, adopt=None, name=None):
        self.registered.append((adopt, name))
        return self.register_replies.pop(0)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    monkeypatch.setattr(saves_ui, "detect_launcher", lambda pref="auto": "faugus")
    install = tmp_path / "G"
    install.mkdir()
    exe = install / "game.exe"
    exe.write_bytes(b"x")
    rec = InstalledGame(game_id=7, name="Some Game", install_dir=str(install), executable=str(exe), state="installed")
    save_library({7: rec})
    server = Server()
    save_device(DeviceRecord("uid-aaaaaaaa", 1, "karasu"))
    win = FakeWin(server)
    return SimpleNamespace(win=win, ui=SaveSync(win), rec=rec, server=server, tmp=tmp_path, install=install)


def _prefix(tmp: Path) -> Path:
    prefix = tmp / "home/Faugus/g"
    (prefix / "pfx/drive_c/users/steamuser/Saved Games").mkdir(parents=True)
    return prefix


def test_with_sync_off_the_game_just_starts(ui):
    ui.win.settings.sync_saves = False
    started = []
    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    assert started == [1] and ui.win.choices == []


def test_a_newer_save_elsewhere_is_offered_before_the_game_starts(ui):
    started = []
    version = ui.server.add_foreign_version({"game/p.sav": b"theirs"})

    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    title, options, answer = ui.win.choices[0]

    assert title == "A newer save exists" and [v for _, v in options] == ["use", "keep", "later"] and started == []
    answer("use")
    assert (ui.install / "p.sav").read_bytes() == b"theirs" and started == [1]
    assert load_state(7).seen_version_id == version["id"]
    assert any("restored" in n for n in ui.win.notes)


def test_keeping_this_machines_saves_is_remembered_and_asking_later_is_not(ui):
    started = []
    version = ui.server.add_foreign_version({"game/p.sav": b"theirs"})
    ui.ui.before_launch(ui.rec, lambda: started.append("a"))
    ui.win.choices[0][2]("later")
    assert started == ["a"] and load_state(7).seen_version_id is None

    ui.ui.before_launch(ui.rec, lambda: started.append("b"))
    ui.win.choices[1][2]("keep")
    assert started == ["a", "b"] and load_state(7).seen_version_id == version["id"]

    ui.ui.before_launch(ui.rec, lambda: started.append("c"))
    assert started == ["a", "b", "c"] and len(ui.win.choices) == 2  # nothing newer is left to offer


def test_a_server_that_cannot_be_reached_never_stops_the_game(ui):
    def broken(game_id):
        raise RuntimeError("connection error")

    ui.server.list_saves = broken
    started = []
    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    assert started == [1]


def test_a_machine_whose_name_is_taken_is_asked_who_it_is_then_carries_on(ui):
    save_device(DeviceRecord("uid-aaaaaaaa", None, None))
    taken = {"code": "hostname_taken", "devices": [{"id": 4, "name": "karasu"}], "suggested_names": ["karasu-fedora"]}
    ui.server.register_replies = [(409, {"detail": taken}), (200, {"id": 4, "name": "karasu"})]
    seen = []

    ui.ui.with_context(ui.rec, lambda ctx: seen.append(ctx.device.device_id), ask=True)

    title, options, answer = ui.win.choices[0]
    assert title == "Is this machine one of these?"
    assert [v for _, v in options] == [("adopt", 4), ("name", "karasu-fedora")]
    assert seen == []
    answer(("adopt", 4))
    assert ui.server.registered == [(None, None), (4, None)]  # asked as itself, then "this is device 4"
    assert seen == [4]


def test_without_permission_to_ask_the_name_question_is_left_for_later(ui):
    save_device(DeviceRecord("uid-aaaaaaaa", None, None))
    ui.server.register_replies = [(409, {"detail": {"code": "hostname_taken", "devices": [], "suggested_names": []}})]
    reasons = []
    ui.ui.with_context(ui.rec, lambda ctx: None, otherwise=reasons.append)
    assert ui.win.choices == [] and len(reasons) == 1 and "no name" in reasons[0]


def test_a_manual_backup_asks_which_folders_then_tries_again(ui, monkeypatch):
    home = ui.tmp / "home"
    wine = home / ".wine/drive_c/users/me"
    (wine / "Documents/Game").mkdir(parents=True)
    (wine / "Documents/Game/s.sav").write_bytes(b"s")
    (wine / "AppData/Roaming/Other").mkdir(parents=True)
    (wine / "AppData/Roaming/Other/o.json").write_bytes(b"o")
    monkeypatch.setattr(saves_ui, "detect_launcher", lambda pref="auto": "wine")

    ui.ui.backup_now(ui.rec)
    title, items, done = ui.win.checklists[0]
    assert title == "Which folders are this game's saves?"
    assert [v for _, v in items] == ["users/USER/AppData/Roaming/Other", "users/USER/Documents/Game"]

    done(["users/USER/Documents/Game"])
    assert ui.server.uploads == [("manual", ["users/USER/Documents/Game/s.sav"])]
    assert any("backed up" in n for n in ui.win.notes)


def test_picking_the_prefix_tells_sync_where_to_look_without_touching_how_the_game_starts(ui):
    prefix = _prefix(ui.tmp)
    done = []
    ui.ui.choose_prefix(ui.rec, then=lambda: done.append(1))
    title, options, answer = ui.win.choices[0]
    assert title == "Where is this game's Wine prefix?" and options[1][1] == prefix and options[-1][1] == "browse"
    assert options[0] == ("Game's Folder (Default)", pfx_dir(ui.rec))
    assert ui.win.skips[0] is None  # nothing to skip: the question was asked on purpose

    answer(prefix)

    state = load_state(7)
    assert (state.prefix, state.prefix_source) == (str(prefix), "chosen") and done == [1]
    assert ui.rec.prefix is None  # the launcher override is untouched
    assert sync.find_prefix(sync.Context(ui.rec, Settings(), None, None, "faugus"), state).prefix == prefix

    answer("browse")
    assert ui.win.browsed == [ui.tmp / "home"]
    ui.win.on_pick(str(ui.tmp / "not-a-prefix"))
    assert "does not look like a Wine prefix" in ui.win.notes[-1] and load_state(7).prefix == str(prefix)


def test_saves_waiting_for_a_prefix_ask_for_one_before_the_game_starts_then_apply(ui):
    version = ui.server.add_foreign_version({"users/USER/Saved Games/s.sav": b"theirs"})
    ctx = sync.Context(ui.rec, ui.win.settings, ui.server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), "faugus")
    assert sync.restore(ctx, version["id"]).status == "needs-prefix"
    sync.decline(ctx, version["id"])  # already offered: only the pending restore remains
    started = []

    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    title, options, answer = ui.win.choices[0]
    assert title == "Where is this game's Wine prefix?" and options[-1][1] == "browse" and started == []
    assert ui.win.skips[0] == "Not now"

    prefix = _prefix(ui.tmp)
    answer(prefix)  # remembers the prefix and asks again from the start

    assert started == [1]
    assert (prefix / "pfx/drive_c/users/steamuser/Saved Games/s.sav").read_bytes() == b"theirs"


def test_not_now_still_starts_the_game(ui):
    version = ui.server.add_foreign_version({"users/USER/Saved Games/s.sav": b"x"})
    ctx = sync.Context(ui.rec, ui.win.settings, ui.server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), "faugus")
    sync.restore(ctx, version["id"])
    sync.decline(ctx, version["id"])
    started = []
    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    ui.win.choices[0][2](None)
    assert started == [1]


def test_after_an_install_the_newest_save_of_each_machine_can_be_restored_or_skipped(ui):
    ui.ui.offer_after_install(ui.rec)
    assert ui.win.choices == []  # nothing on the server

    ui.server.add_foreign_version({"game/a.sav": b"old"}, device_id=2)
    newer = ui.server.add_foreign_version({"game/a.sav": b"new"}, device_id=2)
    ui.ui.offer_after_install(ui.rec)

    title, options, answer = ui.win.choices[0]
    assert title == "Saves from another machine" and [v for _, v in options] == [newer["id"]]
    assert ui.win.skips[0] == "Skip"  # a red button of its own, not an entry of the list
    answer(newer["id"])
    assert (ui.install / "a.sav").read_bytes() == b"new"
    answer(None)  # Skip does nothing


def test_a_chosen_version_can_be_any_of_the_listed_ones(ui):
    first = ui.server.add_foreign_version({"game/a.sav": b"1"}, device_id=2)
    ui.server.add_foreign_version({"game/a.sav": b"2"}, device_id=1)
    ui.ui.restore_pick(ui.rec)
    title, options, answer = ui.win.choices[0]
    assert len(options) == 2 and options[1][1] == first["id"]
    answer(first["id"])
    assert (ui.install / "a.sav").read_bytes() == b"1"


def test_uninstalling_waits_for_a_final_backup_and_asks_when_it_cannot_be_made(ui):
    done = []
    ui.ui.final_backup(ui.rec, lambda: done.append(1))  # no prefix and no manifest: nothing identifies the saves
    assert done == [] and "could not be backed up" in ui.win.asks[0][0]
    ui.win.asks[0][1]()
    assert done == [1]

    ui.win.settings.sync_saves = False
    ui.ui.final_backup(ui.rec, lambda: done.append(2))
    assert done == [1, 2]


def test_a_final_backup_that_works_lets_the_uninstall_go_on(ui, monkeypatch):
    prefix = _prefix(ui.tmp)
    games = ui.tmp / "games.json"
    games.write_text(json.dumps([{"path": ui.rec.executable, "prefix": str(prefix)}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    (prefix / "pfx/drive_c/users/steamuser/Saved Games/s.sav").write_bytes(b"s")
    done = []
    ui.ui.final_backup(ui.rec, lambda: done.append(1))
    assert done == [1] and ui.server.uploads == [("uninstall", ["users/USER/Saved Games/s.sav"])]


def test_the_startup_check_runs_once_and_says_what_needs_the_user_in_one_message(ui):
    ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)  # and nothing is known locally yet
    ui.ui.check_all()
    ui.ui.check_all()
    assert len(ui.win.messages) == 1
    level, text = ui.win.messages[0]
    assert level == "warning" and text == "Saves: needs setup: Some Game (from karasu-2)"


def test_the_startup_check_can_be_switched_off_and_does_nothing_when_saves_are_not_synced(ui):
    ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.win.settings.sync_on_start = False
    ui.ui.check_all()
    ui.win.settings.sync_on_start = True
    ui.win.settings.sync_saves = False
    ui.ui.check_all()
    assert ui.win.messages == [] and ui.win.notes == []

    ui.win.settings.sync_saves = True  # both on: it runs (a switched-off check did not use up its one go)
    ui.ui.check_all()
    assert len(ui.win.messages) == 1


def test_a_quiet_startup_only_goes_to_the_log(ui, monkeypatch):
    prefix = _prefix(ui.tmp)
    games = ui.tmp / "games.json"
    games.write_text(json.dumps([{"path": ui.rec.executable, "prefix": str(prefix)}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    (prefix / "pfx/drive_c/users/steamuser/Saved Games/s.sav").write_bytes(b"s")

    ui.ui.check_all()

    assert ui.win.messages == [] and ui.win.notes == ["Saves: backed up: Some Game"]


def test_labels_name_the_machine_the_time_and_the_size(ui):
    label = saves_ui.version_label(
        {"created_at": "2026-10-04T20:51:30Z", "trigger": "quit", "file_count": 1}, {"name": "karasu"}
    )
    assert label.startswith("karasu: 2026-10-04") or label.startswith("karasu: 2026-10-05")
    assert label.endswith("(quit, 1 file)")


def test_the_games_folder_is_the_first_choice_and_is_taken_even_before_the_prefix_exists(ui):
    done = []
    ui.ui.choose_prefix(ui.rec, then=lambda: done.append(1))
    _, options, answer = ui.win.choices[0]
    own = pfx_dir(ui.rec)
    assert options[0][0] == "Game's Folder (Default)" and not own.exists()

    answer(options[0][1])

    state = load_state(7)
    assert (state.prefix, state.prefix_source) == (str(own), "chosen") and done == [1]
    ctx = sync.Context(ui.rec, Settings(), None, None, "faugus")
    assert sync.find_prefix(ctx, state).prefix == own


def test_use_it_then_the_games_folder_starts_the_game_and_never_asks_again(ui):
    version = ui.server.add_foreign_version({"users/USER/Saved Games/s.sav": b"theirs"})
    started = []

    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    ui.win.choices[0][2]("use")  # "A newer save exists" -> Use it
    title, options, answer = ui.win.choices[1]
    assert title == "Where is this game's Wine prefix?"
    answer(options[0][1])  # Game's Folder (Default), which the game has not made yet

    assert started == [1] and not pfx_dir(ui.rec).exists()
    assert load_state(7).pending_restore and load_state(7).seen_version_id == version["id"]

    # The next start: not offered again, not asked again, and the game still starts.
    ui.ui.before_launch(ui.rec, lambda: started.append(2))
    assert len(ui.win.choices) == 2 and started == [1, 2]

    # Once the game has made its prefix, the waiting save goes in.
    (pfx_dir(ui.rec) / "drive_c/users/steamuser").mkdir(parents=True)
    ui.ui.before_launch(ui.rec, lambda: started.append(3))
    assert started == [1, 2, 3] and len(ui.win.choices) == 2
    assert (pfx_dir(ui.rec) / "drive_c/users/steamuser/Saved Games/s.sav").read_bytes() == b"theirs"


def test_a_conflict_found_at_startup_is_only_logged_the_game_asks_when_it_is_opened(ui, monkeypatch):
    monkeypatch.setattr(sync, "startup_check", lambda ctx: sync.StartupOutcome("conflict", from_device="steamdeck"))
    ui.ui.check_all()
    assert ui.win.messages == []
    assert any("changed here and elsewhere" in n and "steamdeck" in n for n in ui.win.notes)


@pytest.fixture
def native_ui(ui, monkeypatch):
    import os

    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    script = ui.install / "start.sh"
    script.write_bytes(b"#!/bin/sh")
    ui.rec.executable = str(script)
    save_library({7: ui.rec})
    home = ui.tmp / "home"

    def wrote(name: str, data: bytes = b"s") -> None:
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, None)

    ui.home, ui.wrote = home, wrote
    return ui


def test_a_native_game_asks_for_the_folders_it_wrote_to_with_the_likely_ones_ticked(native_ui):
    ui = native_ui
    ui.wrote(".local/share/SomeGame/slot.sav")
    ui.wrote(".config/browser/state.json")
    result = sync.backup(sync.Context(ui.rec, ui.win.settings, ui.server, DeviceRecord("uid-aaaaaaaa", 1, "karasu")), sync.QUIT, since_ns=0)
    assert result.status == "needs-confirmation"

    ui.ui.backup_now(ui.rec)
    title, items, done = ui.win.checklists[0]

    assert title == "Which folders are this game's saves?"
    assert items == [
        ("~/.local/share/SomeGame", "xdg-data/SomeGame", True),
        ("~/.config/browser", "xdg-config/browser", False),
        ("Another folder...", saves_ui.OTHER_FOLDER),
    ]
    done(["xdg-data/SomeGame"])
    assert ui.server.uploads == [("manual", ["xdg-data/SomeGame/slot.sav"])]


def test_ticking_another_folder_opens_the_folder_browser_and_remembers_the_pick(native_ui):
    ui = native_ui
    ui.wrote(".config/browser/state.json")
    ctx = sync.Context(ui.rec, ui.win.settings, ui.server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"))
    sync.backup(ctx, sync.QUIT, since_ns=0)
    ui.ui.backup_now(ui.rec)
    done = ui.win.checklists[0][2]
    ui.wrote(".somegame/profile.dat")

    done([saves_ui.OTHER_FOLDER])
    assert ui.win.browsed == [ui.home]
    ui.win.on_pick(str(ui.home / ".somegame"))

    assert load_state(7).includes == ["home/.somegame"]
    assert ui.server.uploads == [("manual", ["home/.somegame/profile.dat"])]


def test_a_native_game_that_wrote_nothing_is_asked_for_its_folder_by_hand(native_ui):
    ui = native_ui
    ui.ui.backup_now(ui.rec)
    assert ui.win.checklists == [] and ui.win.browsed == [ui.home]
    assert ui.win.messages and "Pick the folder" in ui.win.messages[0][1]

    ui.win.on_pick(str(ui.tmp / "elsewhere"))
    assert ui.win.messages[-1][0] == "error" and load_state(7).includes is None


def test_a_restore_is_a_task_that_ends_with_a_short_message_and_details_in_the_log(ui, monkeypatch):
    logged = []
    monkeypatch.setattr(saves_ui.logstore, "info", logged.append)
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])

    assert ui.win.busy_log == [] and ui.ui.restoring == {} and ui.win.task_refreshes >= 2  # listed, then gone
    assert ui.win.messages[-1] == ("info", "Saves of Some Game restored. Check the log for more information.")
    assert any("1 file(s)" in line and "a.sav" in line for line in logged)


def test_a_restore_before_a_game_starts_covers_the_window_and_then_starts_it(ui):
    version = ui.server.add_foreign_version({"game/p.sav": b"theirs"})
    started = []
    ui.ui.before_launch(ui.rec, lambda: started.append(1))
    ui.win.choices[0][2]("use")

    assert ui.win.busy_log == ["Restoring saves"] and ui.win.busy is None and ui.ui.restoring == {}
    assert ui.win.progress[-1][2] == "Putting the files back..." and started == [1] and version


def test_a_restore_that_fails_says_so_and_leaves_the_task_list(ui):
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)

    def broken(vid, dest, on_progress=None):
        raise RuntimeError("could not download save version 3: HTTP 404")

    ui.server.download_save = broken
    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])

    assert ui.ui.restoring == {}
    assert ui.win.messages[-1] == ("error", "Could not restore: could not download save version 3: HTTP 404")


def test_a_restore_can_be_cancelled_while_it_downloads(ui):
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    original = ui.server.download_save

    def cancelling(vid, dest, on_progress=None):
        assert ui.ui.cancel_restore(7)
        return original(vid, dest, on_progress)

    ui.server.download_save = cancelling
    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])

    assert ui.ui.restoring == {} and not (ui.install / "a.sav").exists()
    assert ui.win.messages == [] and "Restore cancelled" in ui.win.notes


def test_play_does_nothing_special_when_no_restore_is_running(ui):
    assert ui.ui.wait_for_restore(ui.rec, lambda: None) is False and ui.win.asks == []


def test_play_during_a_download_asks_whether_to_cancel_it_and_play_anyway(ui):
    job = saves_ui.RestoreJob(percent=42)
    ui.ui.restoring[7] = job
    played = []

    assert ui.ui.wait_for_restore(ui.rec, lambda: played.append(1)) is True
    text, yes, _no = ui.win.asks[0]
    assert "42%" in text and "Some Game" in text and played == []

    yes()
    assert job.cancel.is_set() and played == [1] and ui.ui.restoring == {}


def test_play_while_the_files_are_being_put_back_only_asks_to_wait(ui):
    ui.ui.restoring[7] = saves_ui.RestoreJob(percent=100, applying=True)
    assert ui.ui.wait_for_restore(ui.rec, lambda: None) is True
    assert ui.win.asks == [] and ui.win.messages[-1][0] == "warning" and ui.ui.cancel_restore(7) is False


def test_a_second_restore_of_the_same_game_is_refused(ui):
    ui.ui.restoring[7] = saves_ui.RestoreJob()
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])
    assert "already being restored" in ui.win.messages[-1][1] and not (ui.install / "a.sav").exists()


def test_asking_for_a_restore_or_backup_of_a_game_with_saving_off_is_answered(ui):
    ui.win.settings.sync_saves = False
    ui.ui.restore_pick(ui.rec)
    ui.ui.backup_now(ui.rec)
    assert [m[1] for m in ui.win.messages] == ["Save sync: saving is off for this game"] * 2


def test_a_backup_by_hand_is_a_task_while_it_runs_and_ends_with_its_answer(ui):
    seen = []
    ui.win.refresh_tasks = lambda: seen.append(sorted(ui.ui.uploading))
    ui.ui.backup_now(ui.rec)

    assert seen == [[7], []] and ui.ui.uploading == set()  # listed while it ran, and gone
    assert ui.win.busy_log == []  # nothing covers the window
    assert ui.win.choices[0][0] == "Where is this game's Wine prefix?"  # no prefix yet: the answer is a question


def test_a_restore_the_server_could_not_note_is_listed_by_the_client(ui):
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.server.save_restored = lambda *args: False  # an older server

    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])

    assert ui.win.local_notices == [("save_restored", "Saves restored: Some Game", "On this machine, 1 file.", 7)]


def test_a_restore_the_server_noted_is_not_listed_twice(ui):
    version = ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.ui.restore_pick(ui.rec)
    ui.win.choices[0][2](version["id"])
    assert ui.win.local_notices == [] and ui.server.restored_notices
