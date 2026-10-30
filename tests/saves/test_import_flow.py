"""The questions of adding a game from outside MOG, with a stand-in window that answers them."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client import launcher, manager
from mog_client.config import Settings, load_library
from mog_client.gui import importing
from mog_client.gui.importing import ImportFlow, key_for_folder
from mog_client.saves import importer
from mog_client.saves import prefix as prefixes
from mog_client.saves.devices import DeviceRecord, save_device
from mog_client.saves.state import load_state

STARTED = 2_000_000_000


class Server(FakeServer):
    def __init__(self):
        super().__init__()
        self.entries: list[dict] = []

    def import_game(self, name, library_id=None):
        game = {"id": 40 + len(self.entries), "name": name, "fs_name": name, "imported": True, "library_id": library_id}
        self.entries.append(game)
        return game

    libraries = [{"id": 1, "name": "Games", "root_path": "/library/games"}]
    made: list = []

    def list_libraries(self):
        return list(self.libraries)

    def create_library(self, name, root_path):
        library = {"id": 10 + len(self.made), "name": name, "root_path": root_path}
        self.made.append(library)
        return library

    def list_games(self):
        return list(self.entries)


class FakeWin:
    def __init__(self, server):
        self.server = server
        settings = Settings(base="http://x", user="u", password="p")
        self.games_signal: list = []
        self.app = SimpleNamespace(
            settings=settings,
            games={},
            client=lambda: self.server,
            bridge=SimpleNamespace(
                call=SimpleNamespace(emit=lambda fn: fn()),
                games=SimpleNamespace(emit=self.games_signal.append),
                error=SimpleNamespace(emit=lambda m: self.errors.append(m)),
            ),
            run_bg=self.run_bg,
            refresh=lambda: None,
        )
        self.library = SimpleNamespace(libraries=[])
        self.game_pages = {}
        self.choices, self.checklists, self.asks, self.texts, self.messages = [], [], [], [], []
        self.errors, self.shown = [], []
        self.browsed = []
        self.saves = SimpleNamespace(final_backup=lambda rec, then, on_abort=None, action="Uninstall": then())

    def run_bg(self, fn, on_error=None):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.errors.append(str(e))
            if on_error:
                on_error(str(e))

    def notify(self, text, level="info"):
        pass

    def message(self, text, level="error", game_id=None):
        self.messages.append((level, text))

    def choose(self, title, text, options, on_choose, skip=None, game=None):
        self.choices.append((title, text, options, on_choose))

    def checklist(self, title, text, items, on_done, exclusive=None, extra=None):
        self.checklists.append((title, items, on_done))

    def ask(self, text, on_yes, on_no=None, danger=False, yes_default=False, game=None, on_cancel=None):
        self.asks.append((text, on_yes, on_no))

    def ask_text(self, title, text, default, on_done):
        self.texts.append((title, default, on_done))

    def browse_folder(self, start, on_pick, text=""):
        self.browsed.append(start)
        self.on_pick = on_pick

    def refresh_items(self):
        pass

    def show_game(self, gid):
        self.shown.append(gid)

    def show_busy(self, *args, **kwargs):
        pass

    def hide_busy(self):
        pass


@pytest.fixture
def ui(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    save_device(DeviceRecord("uid-aaaaaaaa", 1, "karasu"))
    folder = tmp_path / "itch" / "Lost Ruins"
    folder.mkdir(parents=True)
    server = Server()
    win = FakeWin(server)
    return SimpleNamespace(win=win, flow=ImportFlow(win), server=server, home=home, folder=folder, tmp=tmp_path)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (STARTED - 5000, STARTED - 5000))
    return path



def test_adding_a_linux_game_asks_target_name_and_folders_and_sends_every_save(ui):
    for i in range(3):
        write(ui.home / f".local/share/LostRuins/slot{i}.sav", f"s{i}".encode())
    write(ui.folder / "start.sh", b"#!/bin/sh")

    ui.flow.start()
    assert ui.win.browsed == [ui.home]
    ui.win.on_pick(str(ui.folder))
    title, _text, options, answer = ui.win.choices[-1]
    assert title == "What is this game made for?" and options[0][1] == importer.LINUX  # found in the folder, so first
    answer(importer.LINUX)
    assert ui.win.texts[-1][1] == "Lost Ruins"  # the folder's name, ready to change
    ui.win.texts[-1][2]("Lost Ruins")
    title, _text, options, answer = ui.win.choices[-1]
    assert title == "Which library is Lost Ruins in?"
    assert [v for _l, v in options] == [1, importing.NEW_LIBRARY]
    answer(1)

    assert ui.server.entries[0]["name"] == "Lost Ruins" and ui.win.games_signal  # no like-named game: a new entry, listed at once
    assert ui.server.entries[0]["library_id"] == 1  # filed under the library picked
    title, items, done = ui.win.checklists[-1]
    assert [(v, checked) for _l, v, checked in items if v != importing.ANOTHER_FOLDER] == [("xdg-data/LostRuins", True)]
    done(["xdg-data/LostRuins"])

    rec = load_library()[40]
    assert (rec.imported, rec.target, rec.install_dir) == (True, "linux", str(ui.folder))
    assert ui.server.uploads == [("import", [f"xdg-data/LostRuins/slot{i}.sav" for i in range(3)])]
    assert load_state(40).includes == ["xdg-data/LostRuins"]
    assert ui.win.shown == [40] and "Its saves are on the server" in ui.win.messages[-1][1]
    assert sorted(p.name for p in ui.folder.iterdir()) == ["start.sh"]  # nothing written into the game's folder


def test_a_name_like_one_on_the_server_offers_it_and_picking_it_makes_no_new_entry(ui):
    ui.win.app.games = {9: {"id": 9, "name": "Lost Ruins: Director's Cut", "library_id": 1}}
    flow = ui.flow
    flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")

    title, _text, options, answer = ui.win.choices[-1]
    assert title == "Is it one of these games?" and options[0][1]["id"] == 9 and options[-1][1] == importing.NEW_ENTRY
    answer(options[0][1])

    assert ui.server.entries == [] and ui.win.checklists  # linked, and on to the folders


def test_already_added_folders_are_refused(ui):
    manager.import_game({"id": 5, "name": "Lost Ruins"}, ui.folder, importer.LINUX)

    ui.flow.folder_chosen(str(ui.folder))

    assert "already in this library" in ui.win.messages[-1][1] and ui.win.choices == []


def test_a_windows_game_asks_for_the_prefix_and_marks_default_ones(ui):
    shared = ui.home / ".wine"
    write(shared / "drive_c/users/me/Documents/other.sav", b"x")
    own = ui.home / "Faugus/lost"
    write(own / "pfx/drive_c/users/steamuser/Documents/My Games/Lost Ruins/s.sav", b"x")
    write(ui.folder / "lost.exe", b"MZ")

    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.WINDOWS)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)
    title, text, options, answer = ui.win.choices[-1]
    assert title == "Where is the Wine prefix of this game?" and "Do not pick a Default prefix" in text
    assert [v for _l, v in options] == [importing.BROWSE, importing.NEW_PREFIX]
    answer(importing.BROWSE)

    title, _text, options, answer = ui.win.choices[-1]
    labels = {str(v): label for label, v in options}
    assert "Default" in labels[str(shared)] and "Default" not in labels[str(own)]

    answer(shared)  # a default one: asked again before it is used
    assert "Default prefix" in ui.win.asks[-1][0] and ui.win.checklists == []
    answer(own)
    title, items, done = ui.win.checklists[-1]
    assert [v for _l, v, _c in items][:1] == ["users/USER/Documents/My Games/Lost Ruins"]
    done(["users/USER/Documents/My Games/Lost Ruins"])
    rec = load_library()[40]
    assert rec.prefix == str(own) and not rec.prefix_dedicated
    assert ui.server.uploads[0][1] == ["users/USER/Documents/My Games/Lost Ruins/s.sav"]


def test_a_new_prefix_warns_that_the_saves_may_be_in_the_old_one_and_takes_nothing_from_it(ui):
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.WINDOWS)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)
    ui.win.choices[-1][3](importing.NEW_PREFIX)

    title, _text, options, answer = ui.win.choices[-1]
    assert title == "Where should the new prefix be?" and str(ui.folder / "pfx") in options[0][0]
    answer(importing.IN_GAME_FOLDER)
    assert "may be in the prefix it runs in now" in ui.win.asks[-1][0]
    ui.win.asks[-1][1]()

    rec = load_library()[40]
    assert (rec.prefix, rec.prefix_dedicated) == (str(ui.folder / "pfx"), True)
    assert ui.win.checklists == [] and load_state(40).includes == []  # nothing to pick yet


def test_a_conflict_with_another_computers_saves_is_asked_and_keeping_ours_sends_them(ui):
    ui.server.add_foreign_version({"xdg-data/LostRuins/slot1.sav": b"theirs"})
    mine = write(ui.home / ".local/share/LostRuins/slot1.sav", b"mine")
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)
    ui.win.checklists[-1][2](["xdg-data/LostRuins"])

    text, use_theirs, keep_mine = ui.win.asks[-1]
    assert "left others on the server" in text and mine.read_bytes() == b"mine"
    keep_mine()

    assert mine.read_bytes() == b"mine" and ui.server.uploads[-1][0] == "import"


def test_a_folder_picked_by_hand_becomes_a_key(ui):
    assert key_for_folder(ui.folder / "saves", ui.folder) == "game/saves"
    assert key_for_folder(ui.home / ".config/Lost", ui.folder) == "xdg-config/Lost"
    assert key_for_folder(ui.home, ui.folder) is None and key_for_folder(ui.home / ".config", ui.folder) is None


def test_moving_into_mog_asks_twice_for_a_shared_prefix_and_then_moves(ui):
    root = ui.tmp / "MOG-games"
    root.mkdir()
    ui.win.app.settings.install_dirs = [str(root)]
    shared = ui.home / ".wine"
    write(shared / "drive_c/users/me/Documents/x.sav", b"x")
    write(ui.folder / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 5, "name": "Lost Ruins"}, ui.folder, importer.WINDOWS, shared)
    changed = []

    ui.flow.move_into_mog(rec, lambda: changed.append(1))
    text, go, _no = ui.win.asks[-1]
    assert "MOVES" in text and "deleted from where they are" in text
    go()
    ui.win.choices[-1][3](True)
    assert "Default or shared prefix" in ui.win.asks[-1][0] and not (root / "Lost Ruins").exists()
    ui.win.asks[-1][1]()

    moved = load_library()[5]
    assert moved.imported is False and (root / "Lost Ruins/pfx/drive_c/users/me/Documents/x.sav").is_file()
    assert not shared.exists() and changed == [1]


def test_removing_keeps_the_folder_and_the_prefix(ui):
    prefix = ui.home / "wine"
    write(prefix / "drive_c/users/me/Documents/x.sav", b"x")
    write(ui.folder / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 5, "name": "Lost Ruins"}, ui.folder, importer.WINDOWS, prefix)

    ui.flow.remove(rec, lambda: None)
    ui.win.asks[-1][1]()

    assert 5 not in load_library() and (ui.folder / "lost.exe").is_file() and (prefix / "drive_c/users/me/Documents/x.sav").is_file()


def test_a_new_library_is_asked_for_by_name_and_folder_and_the_game_is_listed_under_it(ui):
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")

    ui.win.choices[-1][3](importing.NEW_LIBRARY)
    title, default, named = ui.win.texts[-1]
    assert title == "New Library"
    named("Old games")
    title, default, made = ui.win.texts[-1]
    assert title == "Folder of the library" and default == "/library/Old games"  # next to the others, as the server sees it
    made("/library/old")

    assert ui.server.made == [{"id": 10, "name": "Old games", "root_path": "/library/old"}]
    assert ui.server.entries[0]["library_id"] == 10 and ui.win.checklists  # on to the folders


def test_a_library_the_server_refuses_is_told_and_the_choice_comes_back(ui):
    def refuse(name, root_path):
        raise RuntimeError("only an administrator can make a library")

    ui.server.create_library = refuse
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](importing.NEW_LIBRARY)
    ui.win.texts[-1][2]("Old games")
    ui.win.texts[-1][2]("/library/old")

    assert "only an administrator" in ui.win.messages[-1][1]
    assert ui.win.choices[-1][0] == "Which library is Lost Ruins in?" and ui.server.entries == []


def test_the_executable_is_asked_for_once_the_game_is_added_as_after_an_install(ui):
    asked = []
    ui.win.game_pages[40] = SimpleNamespace(choose_executable=asked.append)
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)
    ui.win.checklists[-1][2]([])

    assert [rec.game_id for rec in asked] == [40]


SANDBOXED = "flatpak:io.itch.itch:xdg-config/unity3d/Studio/Lost Ruins"


def test_a_game_found_saving_in_a_flatpak_apps_folders_is_added_with_that_sandbox(ui):
    write(ui.home / ".var/app/io.itch.itch/config/unity3d/Studio/Lost Ruins/slot1.sav", b"s1")
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)

    _title, items, done = ui.win.checklists[-1]
    row = next(i for i in items if i[1] == SANDBOXED)
    assert "Flatpak: io.itch.itch" in row[0] and row[2] is True  # named like the game: ticked, with the app said
    done([SANDBOXED])

    rec = load_library()[40]
    assert rec.sandbox == "io.itch.itch" and rec.in_sandbox == "io.itch.itch"
    assert load_state(40).includes == ["xdg-config/unity3d/Studio/Lost Ruins"]  # the usual key
    assert ui.server.uploads == [("import", ["xdg-config/unity3d/Studio/Lost Ruins/slot1.sav"])]


def test_folders_of_the_real_home_and_of_a_sandbox_cannot_be_taken_together(ui):
    write(ui.home / ".var/app/io.itch.itch/config/unity3d/Studio/Lost Ruins/slot1.sav", b"s1")
    write(ui.home / ".config/unity3d/Studio/Lost Ruins/slot1.sav", b"s2")
    ui.flow.folder_chosen(str(ui.folder))
    ui.win.choices[-1][3](importer.LINUX)
    ui.win.texts[-1][2]("Lost Ruins")
    ui.win.choices[-1][3](1)
    _title, items, done = ui.win.checklists[-1]
    both = [v for _l, v, _c in items if v.endswith("xdg-config/unity3d/Studio/Lost Ruins")]
    assert len(both) == 2

    done(both)

    assert "one place" in ui.win.messages[-1][1] and len(ui.win.checklists) == 2 and 40 not in load_library()


def test_another_folder_inside_a_sandbox_names_its_app(ui):
    folder = ui.home / ".var/app/io.itch.itch/config/unity3d/Studio/Lost Ruins"
    folder.mkdir(parents=True)
    rec = manager.import_game({"id": 5, "name": "Lost Ruins"}, ui.folder, importer.LINUX)
    ui.flow.browse_saves(rec, [], [], lambda keys, sandbox: None)

    ui.win.on_pick(str(folder))

    _title, items, _done = ui.win.checklists[-1]
    assert (SANDBOXED, True) in [(v, c) for _l, v, c in items]


def test_a_folder_that_was_a_mog_game_is_taken_back_without_the_questions_of_a_game_from_outside(ui):
    root = ui.tmp / "Games"
    write(root / "Lost Ruins" / "data.bin", b"x")
    ui.win.app.settings.install_dirs = [str(root)]
    ui.win.app.games = {5: {"id": 5, "name": "Lost Ruins", "fs_name": "Lost Ruins"}}
    asked = []
    pages = SimpleNamespace(choose_executable=lambda rec: asked.append(rec.game_id))
    ui.win.game_pages = {5: pages}

    ui.flow.folder_chosen(str(root / "Lost Ruins"))

    rec = load_library()[5]
    assert rec.state == "awaiting_executable" and not rec.imported and rec.install_dir == str(root / "Lost Ruins")
    assert asked == [5] and ui.win.shown == [5] and not ui.win.choices and not ui.win.texts
    assert "already a MOG game" in ui.win.messages[-1][1]


def test_any_other_folder_still_goes_through_the_questions(ui):
    ui.win.app.games = {5: {"id": 5, "name": "Lost Ruins", "fs_name": "Lost Ruins"}}
    write(ui.folder / "start.sh", b"#!/bin/sh")

    ui.flow.folder_chosen(str(ui.folder))  # in "itch", not in an install folder

    assert ui.win.choices[-1][0] == "What is this game made for?" and 5 not in load_library()
