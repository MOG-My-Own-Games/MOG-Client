import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fakes import FakeServer
from mog_client.config import InstalledGame, Settings
from mog_client.saves import prefix as prefixes
from mog_client.saves import sync
from mog_client.saves.devices import DeviceRecord
from mog_client.saves.state import load_state

STARTED = 2_000_000_000


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    install = tmp_path / "games" / "Lost Ruins"
    install.mkdir(parents=True)
    script = install / "start.sh"
    script.write_bytes(b"#!/bin/sh")
    rec = InstalledGame(game_id=7, name="Lost Ruins", install_dir=str(install), executable=str(script), state="installed")
    server = FakeServer()
    ctx = sync.Context(rec, Settings(), server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), engine="auto")
    return SimpleNamespace(home=home, rec=rec, server=server, ctx=ctx, install=install)


def write(path: Path, data: bytes, mtime: int = STARTED + 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def session_ns() -> int:
    return STARTED * 10**9


def test_a_session_offers_the_folders_the_game_wrote_to_and_uploads_nothing_unconfirmed(world):
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")
    write(world.home / ".config/browser-thing/state.json", b"other")
    write(world.home / ".local/share/LostRuins/old.sav", b"old", mtime=STARTED - 500)

    result = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert result.status == "needs-confirmation"
    assert result.folders == ["xdg-data/LostRuins", "xdg-config/browser-thing"]
    assert world.server.uploads == []
    assert load_state(7).pending_folders == result.folders


def test_the_offer_survives_until_the_user_answers_even_without_a_session(world):
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")
    sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    again = sync.backup(world.ctx, sync.SYNC)

    assert again.status == "needs-confirmation" and again.folders == ["xdg-data/LostRuins"]


def test_confirmed_folders_are_backed_up_and_then_left_alone_until_they_change(world):
    slot = write(world.home / ".local/share/LostRuins/slot1.sav", b"one")
    write(world.home / ".config/browser-thing/state.json", b"other")
    sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    sync.confirm_folders(7, ["xdg-data/LostRuins"])
    first = sync.backup(world.ctx, sync.MANUAL, force=True)

    assert first.status == "uploaded"
    assert world.server.uploads == [("manual", ["xdg-data/LostRuins/slot1.sav"])]
    assert load_state(7).pending_folders == []
    assert sync.backup(world.ctx, sync.SYNC).status == "unchanged"

    write(slot, b"two, longer", mtime=STARTED + 900)
    write(world.home / ".local/share/LostRuins/slot2.sav", b"new", mtime=STARTED + 900)
    again = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())
    assert again.status == "uploaded"
    assert world.server.uploads[1][1] == ["xdg-data/LostRuins/slot1.sav", "xdg-data/LostRuins/slot2.sav"]


def test_once_folders_are_confirmed_a_session_does_not_look_around_again(world):
    sync.confirm_folders(7, ["xdg-data/LostRuins"])
    write(world.home / ".config/browser-thing/state.json", b"other")

    result = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert result.status == "nothing" and load_state(7).pending_folders == []


def test_a_game_that_saves_in_its_own_folder_needs_no_question(world):
    import hashlib

    from mog_client.saves.state import save_install_manifest

    save_install_manifest(7, [{"path": "start.sh", "size_bytes": 9, "sha1": hashlib.sha1(b"#!/bin/sh").hexdigest()}])
    write(world.install / "profile.sav", b"progress")
    write(world.home / ".config/browser-thing/state.json", b"other")

    result = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert result.status == "uploaded" and world.server.uploads == [("quit", ["game/profile.sav"])]


def test_a_game_that_wrote_nothing_is_not_nagged_but_a_manual_backup_asks_for_the_folder(world):
    assert sync.backup(world.ctx, sync.QUIT, since_ns=session_ns()).status == "nothing"
    assert sync.backup(world.ctx, sync.SYNC).status == "nothing"
    assert sync.backup(world.ctx, sync.MANUAL, force=True).status == "needs-folder"


def test_a_folder_the_user_picks_is_remembered_by_its_key(world):
    write(world.home / ".lostruins/profile.dat", b"p")
    assert sync.add_folder(7, world.home / ".lostruins") == "home/.lostruins"

    result = sync.backup(world.ctx, sync.MANUAL, force=True)

    assert result.status == "uploaded" and world.server.uploads == [("manual", ["home/.lostruins/profile.dat"])]


def test_a_folder_outside_home_or_a_whole_root_is_refused(world, tmp_path):
    assert sync.add_folder(7, tmp_path / "elsewhere") is None
    assert sync.add_folder(7, world.home) is None
    assert sync.add_folder(7, world.home / ".config") is None
    assert load_state(7).includes is None


def test_a_save_from_another_machine_is_put_back_and_its_folders_become_the_games(world):
    version = world.server.add_foreign_version(
        {"xdg-config/unity3d/Studio/Game/prefs": b"p", "xdg-data/LostRuins/slot1.sav": b"s"}
    )

    result = sync.restore(world.ctx, version["id"])

    assert result.status == "restored"
    assert (world.home / ".config/unity3d/Studio/Game/prefs").read_bytes() == b"p"
    assert (world.home / ".local/share/LostRuins/slot1.sav").read_bytes() == b"s"
    assert load_state(7).includes == ["xdg-config/unity3d/Studio/Game", "xdg-data/LostRuins"]
    assert sync.backup(world.ctx, sync.SYNC).status == "unchanged"


def test_a_restore_keeps_what_it_replaces(world):
    mine = write(world.home / ".local/share/LostRuins/slot1.sav", b"mine")
    version = world.server.add_foreign_version({"xdg-data/LostRuins/slot1.sav": b"theirs"})

    result = sync.restore(world.ctx, version["id"])

    assert mine.read_bytes() == b"theirs" and result.backup is not None and result.backup.exists()


def test_a_save_made_for_the_other_kind_of_game_is_not_applied(world):
    windows = world.server.add_foreign_version({"users/USER/Documents/Game/a.sav": b"w"})
    assert sync.restore(world.ctx, windows["id"]).status == "incompatible"
    assert load_state(7).seen_version_id == windows["id"]

    wine = InstalledGame(game_id=8, name="W", install_dir=str(world.install), executable=str(world.install / "g.exe"), state="installed")
    ctx = sync.Context(wine, Settings(), world.server, world.ctx.device, engine="faugus")
    linux = world.server.add_foreign_version({"xdg-data/Game/a.sav": b"l"})
    assert sync.restore(ctx, linux["id"]).status == "incompatible"
    assert not (world.home / ".local/share/Game").exists()


def test_the_start_of_the_client_sends_a_native_games_confirmed_saves(world):
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")
    sync.confirm_folders(7, ["xdg-data/LostRuins"])

    outcome = sync.startup_check(world.ctx)

    assert outcome.status == "uploaded" and world.server.uploads == [("sync", ["xdg-data/LostRuins/slot1.sav"])]


def test_the_start_of_the_client_says_when_a_native_game_waits_for_its_folders(world):
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")
    sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert sync.startup_check(world.ctx).status == "setup"


def test_the_folders_the_server_knows_are_the_games_without_asking_anyone(world):
    world.server.manifest = ["<xdgData>/LostRuins/saves", "<storeUserId>/x"]
    write(world.home / ".local/share/LostRuins/saves/slot1.sav", b"one")
    write(world.home / ".local/share/LostRuins/cache.bin", b"not in the named folder")
    write(world.home / ".config/browser-thing/state.json", b"other")

    result = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert result.status == "uploaded"
    assert world.server.uploads == [("quit", ["xdg-data/LostRuins/saves/slot1.sav"])]
    assert load_state(7).manifest_folders == ["xdg-data/LostRuins/saves"] and load_state(7).pending_folders == []


def test_the_server_is_asked_once_and_an_answer_of_none_is_asked_again(world):
    sync.backup(world.ctx, sync.SYNC)
    sync.backup(world.ctx, sync.SYNC)
    assert world.server.manifest_asked == 1 and load_state(7).manifest_folders == []

    world.server.manifest, world.server.manifest_asked = None, 0
    sync.confirm_folders(7, [])
    from mog_client.saves.state import save_state

    state = load_state(7)
    state.manifest_folders = None
    save_state(7, state)
    sync.backup(world.ctx, sync.SYNC)
    sync.backup(world.ctx, sync.SYNC)
    assert world.server.manifest_asked == 2 and load_state(7).manifest_folders is None


def test_when_the_known_folder_holds_nothing_the_session_still_looks_around(world):
    world.server.manifest = ["<xdgData>/Wrong/saves"]
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")

    result = sync.backup(world.ctx, sync.QUIT, since_ns=session_ns())

    assert result.status == "needs-confirmation" and result.folders == ["xdg-data/LostRuins"]


def test_a_server_that_cannot_say_changes_nothing(world):
    world.server.manifest = None
    write(world.home / ".local/share/LostRuins/slot1.sav", b"one")

    assert sync.backup(world.ctx, sync.QUIT, since_ns=session_ns()).status == "needs-confirmation"
