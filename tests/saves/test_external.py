"""Import external saves: the old saves of a game, found in the same places as the fresh ones, take their place."""

import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client.config import InstalledGame, Settings
from mog_client.saves import external, sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.devices import DeviceRecord
from mog_client.saves.state import SaveState, load_state, save_install_manifest

NOW = 2_000_000_000


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    install = tmp_path / "games" / "Lost Ruins"
    install.mkdir(parents=True)
    exe = install / "game.exe"
    exe.write_bytes(b"exe")
    rec = InstalledGame(game_id=7, name="Lost Ruins", install_dir=str(install), executable=str(exe), state="installed")
    own = tmp_path / "home" / "Faugus" / "lost-ruins"
    games = tmp_path / "games.json"
    games.write_text(json.dumps([{"path": str(exe), "prefix": str(own)}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    server = FakeServer()
    ctx = sync.Context(rec, Settings(), server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), engine="faugus")
    return SimpleNamespace(tmp=tmp_path, rec=rec, install=install, server=server, ctx=ctx, own=own)


def user_dir(prefix: Path) -> Path:
    return prefix / "pfx/drive_c/users/steamuser"


def write(path: Path, data: bytes, mtime: int = NOW) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def play_once(world) -> None:
    """The fresh game writes a first save, and the sync learns where."""
    write(user_dir(world.own) / "Documents/Lost Ruins/slot1.sav", b"fresh", NOW - 1000)
    assert sync.backup(world.ctx, sync.SYNC).status == "uploaded"


def old_prefix(world) -> Path:
    return world.tmp / "home" / "old-wine"


def test_nothing_can_be_matched_before_the_game_has_saved(world):
    assert not external.has_structure(load_state(7))


def test_the_structure_is_the_game_sized_folder_not_the_whole_profile_folder():
    state = SaveState()
    state.tracked = {
        "users/USER/Documents/Lost Ruins/a.sav": None,
        "users/USER/Documents/Lost Ruins/more/b.sav": None,
        "users/USER/Documents/loose.cfg": None,
        "game/saves/c.dat": None,
        "game/root.sav": None,
        "xdg-config/LostRuins/x": None,
    }
    assert external.units(state) == [
        "game/root.sav",
        "game/saves",
        "users/USER/Documents/Lost Ruins",
        "users/USER/Documents/loose.cfg",
    ]
    assert external.sources(state) == {external.PREFIX, external.FOLDER}


def test_the_old_saves_in_another_prefix_replace_the_fresh_ones_and_go_up(world):
    play_once(world)
    old = user_dir(old_prefix(world))
    write(old / "Documents/Lost Ruins/slot1.sav", b"old progress", NOW)
    write(old / "Documents/Lost Ruins/slot2.sav", b"slot two", NOW)
    write(old / "Documents/Other Game/x.sav", b"not this game's", NOW)

    matches = external.find(world.ctx, prefix=old_prefix(world))

    assert {(m.key, m.status) for m in matches} == {
        ("users/USER/Documents/Lost Ruins/slot1.sav", external.DIFFERS),
        ("users/USER/Documents/Lost Ruins/slot2.sav", external.NEW),
    }
    result = external.apply(world.ctx, matches)

    mine = user_dir(world.own) / "Documents/Lost Ruins"
    assert (mine / "slot1.sav").read_bytes() == b"old progress" and (mine / "slot2.sav").read_bytes() == b"slot two"
    assert not (mine.parent / "Other Game").exists()
    with zipfile.ZipFile(result.backup) as z:  # what was replaced is kept
        assert z.read("users/USER/Documents/Lost Ruins/slot1.sav") == b"fresh"
    sent = sync.backup(world.ctx, sync.MANUAL, force=True)
    assert sent.status == "uploaded"
    assert world.server.uploads[-1][1] == [
        "users/USER/Documents/Lost Ruins/slot1.sav",
        "users/USER/Documents/Lost Ruins/slot2.sav",
    ]


def test_files_that_are_the_same_are_left_out_and_older_ones_can_be_skipped(world):
    play_once(world)
    mine = user_dir(world.own) / "Documents/Lost Ruins"
    write(mine / "same.sav", b"same", NOW - 500)
    write(mine / "kept.sav", b"progress made here", NOW)
    old = user_dir(old_prefix(world)) / "Documents/Lost Ruins"
    write(old / "same.sav", b"same", NOW - 500)
    write(old / "kept.sav", b"older progress", NOW - 9000)
    write(old / "fresh.sav", b"only there", NOW - 9000)

    matches = external.find(world.ctx, prefix=old_prefix(world))

    assert sorted(m.key.rpartition("/")[2] for m in matches) == ["fresh.sav", "kept.sav"]
    assert [m.key.rpartition("/")[2] for m in external.only_newer(matches)] == ["fresh.sav"]


def test_the_old_game_folder_gives_the_saves_kept_inside_the_game(world):
    save_install_manifest(7, [{"path": "game.exe", "size_bytes": 3, "sha1": hashlib.sha1(b"exe").hexdigest()}])
    write(world.install / "saves/profile.dat", b"fresh", NOW - 1000)
    assert sync.backup(world.ctx, sync.SYNC).status == "uploaded"
    old = world.tmp / "itch" / "Lost Ruins"
    write(old / "saves/profile.dat", b"old", NOW)
    write(old / "saves/profile2.dat", b"old two", NOW)
    write(old / "data/art.pak", b"art", NOW)

    matches = external.find(world.ctx, folder=old)

    assert [m.key for m in matches] == ["game/saves/profile.dat", "game/saves/profile2.dat"]
    external.apply(world.ctx, matches)
    assert (world.install / "saves/profile.dat").read_bytes() == b"old"
    assert not (world.install / "data").exists()


def test_a_prefix_is_not_searched_for_the_games_own_folder_and_the_other_way_round(world):
    play_once(world)
    write(user_dir(old_prefix(world)) / "Documents/Lost Ruins/slot9.sav", b"x")
    assert external.find(world.ctx, folder=old_prefix(world)) == []
    assert external.find(world.ctx) == []


# --- the questions ---------------------------------------------------------------------------------------------------


class FakeWin:
    def __init__(self, world):
        self.world = world
        self.choices, self.asks, self.messages, self.errors = [], [], [], []
        self.app = SimpleNamespace(
            bridge=SimpleNamespace(
                call=SimpleNamespace(emit=lambda fn: fn()), error=SimpleNamespace(emit=self.errors.append)
            ),
            run_bg=lambda fn, on_error=None: fn(),
        )
        self.saves = SimpleNamespace(with_context=lambda rec, run, otherwise=None, ask=False: run(world.ctx))

    def notify(self, text, level="info"):
        pass

    def message(self, text, level="error", game_id=None):
        self.messages.append(text)

    def choose(self, title, text, options, on_choose, skip=None, game=None):
        self.choices.append((title, options, on_choose))

    def ask(self, text, on_yes, on_no=None, danger=False, yes_default=False, game=None, on_cancel=None):
        self.asks.append((text, on_yes))

    def browse_folder(self, start, on_pick):
        self.on_pick = on_pick


def test_the_button_asks_to_play_first_when_the_game_has_not_saved_yet(world):
    from mog_client.gui.extsaves import ExternalSaves

    win = FakeWin(world)
    ExternalSaves(win).start(world.rec)
    assert "save at least once" in win.messages[0] and not win.choices


def test_the_questions_end_with_the_old_saves_in_place(world):
    from mog_client.gui.extsaves import ExternalSaves

    play_once(world)
    old = old_prefix(world)
    write(user_dir(old) / "Documents/Lost Ruins/slot1.sav", b"old progress", NOW)
    win = FakeWin(world)
    flow = ExternalSaves(win)

    flow.start(world.rec)  # a prefix is what holds these saves: it goes straight to asking which
    title, options, chosen = win.choices[-1]
    assert title == "In which prefix are your old saves?"
    chosen("*another*")
    win.on_pick(str(old))

    text, yes = win.asks[-1]
    assert "1 file in" in text
    yes()
    mine = user_dir(world.own) / "Documents/Lost Ruins/slot1.sav"
    assert mine.read_bytes() == b"old progress" and win.errors == []
    assert "in place" in win.messages[-1] and "on the server" in win.messages[-1]
