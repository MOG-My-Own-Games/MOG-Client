"""The save-sync flows of the window, with a stand-in window that answers the questions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client.config import InstalledGame, Settings, save_library
from mog_client.gui import saves_ui
from mog_client.gui.saves_ui import SaveSync
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
        self.choices: list[tuple] = []
        self.skips: list[str | None] = []
        self.checklists: list[tuple] = []
        self.asks: list[tuple] = []
        self.browsed: list[Path] = []
        self.errors: list[str] = []

    def run_bg(self, fn, on_error=None):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - what the real run_bg does
            self.errors.append(str(e))
            if on_error:
                on_error(str(e))

    def notify(self, text):
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
    assert title == "Where is this game's Wine prefix?" and options[0][1] == prefix and options[-1][1] == "browse"
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


def test_the_startup_check_runs_once_and_reports_in_one_line(ui):
    ui.server.add_foreign_version({"game/a.sav": b"theirs"}, device_id=2)
    ui.ui.check_all()
    ui.ui.check_all()
    assert len(ui.win.notes) == 1 and "newer saves elsewhere: Some Game (karasu-2)" in ui.win.notes[0]
    assert "needs setup" in ui.win.notes[0]


def test_labels_name_the_machine_the_time_and_the_size(ui):
    label = saves_ui.version_label(
        {"created_at": "2026-10-04T20:51:30Z", "trigger": "quit", "file_count": 1}, {"name": "karasu"}
    )
    assert label.startswith("karasu: 2026-10-04") or label.startswith("karasu: 2026-10-05")
    assert label.endswith("(quit, 1 file)")
