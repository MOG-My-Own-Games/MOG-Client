import hashlib
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mog_client.config import InstalledGame, Settings
from mog_client.saves import prefix as prefixes
from mog_client.saves import sync
from mog_client.saves.devices import DeviceRecord
from fakes import FakeServer
from mog_client.saves.state import load_state, save_install_manifest


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    install = tmp_path / "games" / "G"
    install.mkdir(parents=True)
    exe = install / "game.exe"
    exe.write_bytes(b"exe")
    rec = InstalledGame(game_id=7, name="G", install_dir=str(install), executable=str(exe), state="installed")
    server = FakeServer()
    ctx = sync.Context(rec, Settings(), server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), engine="faugus")
    return SimpleNamespace(tmp=tmp_path, rec=rec, server=server, ctx=ctx, install=install)


def make_prefix(tmp_path: Path, name="Faugus/g") -> Path:
    prefix = tmp_path / "home" / name
    user = prefix / "pfx/drive_c/users/steamuser"
    (user / "Saved Games").mkdir(parents=True)
    (user / "Documents").mkdir()
    (user / "AppData/Roaming").mkdir(parents=True)
    return prefix


def write(path: Path, data: bytes, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def faugus_owns(world, prefix: Path, monkeypatch):
    games = world.tmp / "games.json"
    import json

    games.write_text(json.dumps([{"path": world.rec.executable, "prefix": str(prefix)}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])


def test_a_game_prefix_of_its_own_is_backed_up_then_left_alone_until_it_changes(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    save = write(user / "Saved Games/slot1.sav", b"one")
    write(user / "AppData/Local/Temp/noise.tmp", b"x")

    first = sync.backup(world.ctx, sync.SYNC)
    assert first.status == "uploaded" and first.changed == ["users/USER/Saved Games/slot1.sav"]
    assert world.server.uploads == [("sync", ["users/USER/Saved Games/slot1.sav"])]
    state = load_state(7)
    assert state.last_version_id == 1 and state.prefix == str(prefix) and state.prefix_source == "faugus"

    assert sync.backup(world.ctx, sync.SYNC).status == "unchanged"
    assert len(world.server.uploads) == 1

    write(save, b"two, longer", mtime=save.stat().st_mtime + 10)
    write(user / "Saved Games/slot2.sav", b"new")
    again = sync.backup(world.ctx, sync.QUIT)
    assert again.status == "uploaded"
    assert world.server.uploads[1] == ("quit", ["users/USER/Saved Games/slot1.sav", "users/USER/Saved Games/slot2.sav"])


def test_a_prefix_shared_with_other_programs_asks_which_folders_are_the_games(world, monkeypatch):
    home = world.tmp / "home"
    wine = home / ".wine/drive_c/users/me"
    write(wine / "Documents/Game/save.sav", b"s")
    write(wine / "AppData/Roaming/OtherApp/settings.json", b"o")
    (home / ".wine/drive_c/users/me").mkdir(exist_ok=True)
    world.ctx.engine = "wine"

    asked = sync.backup(world.ctx, sync.SYNC)
    assert asked.status == "needs-confirmation"
    assert asked.folders == ["users/USER/AppData/Roaming/OtherApp", "users/USER/Documents/Game"]
    assert world.server.uploads == []

    sync.confirm_folders(7, ["users/USER/Documents/Game"])
    done = sync.backup(world.ctx, sync.SYNC)
    assert done.status == "uploaded" and world.server.uploads == [("sync", ["users/USER/Documents/Game/save.sav"])]


def test_a_session_attributes_only_what_was_written_while_it_ran(world):
    home = world.tmp / "home"
    wine = home / ".wine/drive_c/users/me"
    old = write(wine / "Documents/Other/old.sav", b"old", mtime=1_000)
    started_ns = 2_000_000_000 * 10**9
    new = write(wine / "Documents/Game/new.sav", b"new", mtime=2_000_000_100)
    world.ctx.engine = "wine"

    result = sync.backup(world.ctx, sync.QUIT, since_ns=started_ns)

    assert result.status == "uploaded" and world.server.uploads == [("quit", ["users/USER/Documents/Game/new.sav"])]
    assert old.exists() and new.exists()


def test_without_a_prefix_and_without_the_install_manifest_there_is_nothing_to_look_at(world):
    assert sync.backup(world.ctx, sync.SYNC).status == "needs-prefix"


def test_files_the_game_wrote_next_to_its_exe_need_no_prefix(world):
    shipped = write(world.install / "data.pak", b"shipped")
    save_install_manifest(
        7,
        [
            {"path": "data.pak", "size_bytes": 7, "sha1": hashlib.sha1(b"shipped").hexdigest()},
            {"path": "game.exe", "size_bytes": 3, "sha1": hashlib.sha1(b"exe").hexdigest()},
        ],
    )
    write(world.install / "profile.sav", b"progress")
    write(world.install / ".mog-icon", b"icon made by MOG")

    result = sync.backup(world.ctx, sync.QUIT, since_ns=0)

    assert result.status == "uploaded" and world.server.uploads == [("quit", ["game/profile.sav"])]
    assert shipped.exists()


def test_a_forced_backup_uploads_even_when_nothing_changed(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    write(prefix / "pfx/drive_c/users/steamuser/Saved Games/a.sav", b"a")
    sync.backup(world.ctx, sync.SYNC)
    assert sync.backup(world.ctx, sync.MANUAL, force=True).status == "uploaded"
    assert len(world.server.uploads) == 2


def test_a_server_version_is_put_back_under_this_machines_user_with_a_backup_of_what_it_replaces(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    mine = write(user / "Saved Games/slot1.sav", b"mine")
    version = world.server.add_foreign_version(
        {"users/USER/Saved Games/slot1.sav": b"theirs", "users/USER/Documents/cfg.ini": b"cfg", "game/extra.sav": b"g"}
    )

    result = sync.restore(world.ctx, version["id"])

    assert result.status == "restored" and len(result.restored) == 3
    assert mine.read_bytes() == b"theirs" and (user / "Documents/cfg.ini").read_bytes() == b"cfg"
    assert (world.install / "extra.sav").read_bytes() == b"g"
    assert result.backup is not None and zipfile.ZipFile(result.backup).read("users/USER/Saved Games/slot1.sav") == b"mine"
    assert load_state(7).seen_version_id == version["id"]
    assert sync.backup(world.ctx, sync.SYNC).status == "unchanged"  # the restored files are not "changes"


def test_a_restore_that_needs_a_prefix_waits_for_one(world, monkeypatch):
    version = world.server.add_foreign_version({"users/USER/Saved Games/slot1.sav": b"theirs"})
    assert sync.restore(world.ctx, version["id"]).status == "needs-prefix"
    assert load_state(7).pending_restore and sync.apply_pending(world.ctx) is None

    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    mine = write(prefix / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav", b"made by the game since")
    assert sync.apply_pending(world.ctx, only_if_free=True) is None  # never over what is there
    assert mine.read_bytes() == b"made by the game since" and load_state(7).pending_restore

    mine.unlink()
    result = sync.apply_pending(world.ctx, only_if_free=True)

    assert result.status == "restored"
    assert mine.read_bytes() == b"theirs"
    assert load_state(7).pending_restore is None


def test_a_game_only_save_is_restored_without_any_prefix(world):
    version = world.server.add_foreign_version({"game/profile.sav": b"p"})
    assert sync.restore(world.ctx, version["id"]).status == "restored"
    assert (world.install / "profile.sav").read_bytes() == b"p"


def test_a_newer_save_from_another_machine_is_offered_until_applied_or_declined(world):
    assert sync.newer_elsewhere(world.ctx) is None
    world.server.upload_save(7, 1, _zip(world.tmp, {"game/a.sav": b"a"}), "quit")  # this machine's own
    assert sync.newer_elsewhere(world.ctx) is None

    other = world.server.add_foreign_version({"game/a.sav": b"b"})
    version, device = sync.newer_elsewhere(world.ctx)
    assert (version["id"], device["name"]) == (other["id"], "karasu-2")

    sync.decline(world.ctx, other["id"])
    assert sync.newer_elsewhere(world.ctx) is None
    newer = world.server.add_foreign_version({"game/a.sav": b"c"})
    assert sync.newer_elsewhere(world.ctx)[0]["id"] == newer["id"]


def test_versions_are_listed_newest_first_with_their_device(world):
    a = world.server.add_foreign_version({"game/a": b"1"}, device_id=2)
    b = world.server.add_foreign_version({"game/a": b"2"}, device_id=1)
    assert [(v["id"], d["name"]) for v, d in sync.versions_of(world.ctx)] == [(b["id"], "karasu"), (a["id"], "karasu-2")]


def _zip(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as z:
        for key, data in files.items():
            z.writestr(key, data)
    return path


def test_picking_another_prefix_forgets_what_was_tracked_and_confirmed(world):
    from mog_client.saves.state import FileInfo, SaveState, save_state

    state = SaveState(prefix="/a", prefix_source="runtime", includes=["x"], tracked={"k": FileInfo(1, 2, "h")})
    save_state(7, state)

    sync.remember_prefix(7, Path("/a"), "runtime")  # seen again: nothing changes
    assert load_state(7).includes == ["x"] and load_state(7).tracked

    sync.remember_prefix(7, Path("/b"), "runtime")
    assert load_state(7).prefix == "/b" and load_state(7).includes is None and load_state(7).tracked == {}

    save_state(7, SaveState(prefix="/b", prefix_source="chosen", includes=["x"]))
    sync.remember_prefix(7, Path("/b"), "chosen")  # picked again by hand: ask again
    assert load_state(7).includes is None


def test_a_game_with_its_prefix_in_its_own_folder_backs_up_without_asking_and_without_the_prefix_files(world):
    pfx = world.install / "pfx"
    user = pfx / "drive_c/users/steamuser"
    write(user / "Saved Games/slot1.sav", b"progress")
    write(user / "AppData/Roaming/Other/settings.json", b"also this game's: the prefix is its alone")
    world.rec.prefix = str(pfx)
    save_install_manifest(7, [{"path": "game.exe", "size_bytes": 3, "sha1": hashlib.sha1(b"exe").hexdigest()}])
    write(world.install / "Some Game.sh", b"#!/bin/sh")

    result = sync.backup(world.ctx, sync.SYNC)

    assert result.status == "uploaded"
    assert sorted(world.server.uploads[0][1]) == [
        "users/USER/AppData/Roaming/Other/settings.json",
        "users/USER/Saved Games/slot1.sav",
    ]
    assert load_state(7).prefix == str(pfx) and load_state(7).prefix_source == "game-folder"
