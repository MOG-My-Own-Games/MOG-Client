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
    assert world.server.restored_notices == [(version["id"], 1, 3)]  # the server keeps a notice of it
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
    assert world.server.restored_notices == [(version["id"], 1, 1)]  # told when it really went in, not before


def test_a_server_that_cannot_keep_the_notice_does_not_undo_the_restore(world):
    version = world.server.add_foreign_version({"game/profile.sav": b"p"})

    def broken(*args):
        raise RuntimeError("older server")

    world.server.save_restored = broken
    assert sync.restore(world.ctx, version["id"]).status == "restored"
    assert (world.install / "profile.sav").read_bytes() == b"p"


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


def test_a_prefix_seen_while_the_game_runs_never_replaces_the_one_the_game_has(world, monkeypatch):
    from mog_client.saves import runner
    from mog_client.saves.state import FileInfo, SaveState, save_state

    pfx = world.install / "pfx"
    pfx.mkdir()
    world.rec.prefix = str(pfx)
    save_state(7, SaveState(prefix=str(pfx), prefix_source="game-folder", tracked={"k": FileInfo(1, 2, "h")}))
    monkeypatch.setattr(runner.devices, "known_device", lambda: world.ctx.device)
    monkeypatch.setattr(runner, "wait_for_game", lambda install, on_prefix, stop, native: on_prefix(Path("/home/x/Jazz")) or True)
    monkeypatch.setattr(sync, "backup", lambda ctx, trigger, since_ns=None, force=False: sync.BackupResult("unchanged"))

    runner.watch_session(world.rec, world.ctx.settings, world.server, since_ns=0, log=lambda m: None)

    state = load_state(7)
    assert state.prefix == str(pfx) and state.prefix_source == "game-folder" and list(state.tracked) == ["k"]


# --- looking over the saves when the client starts ---


def _synced_game(world, monkeypatch):
    """A game with its own prefix whose saves were backed up from this machine (device 1)."""
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    save = write(prefix / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav", b"mine")
    assert sync.backup(world.ctx, sync.QUIT).status == "uploaded"
    return save


def test_nothing_new_anywhere_means_nothing_to_do_and_a_local_change_is_sent(world, monkeypatch):
    save = _synced_game(world, monkeypatch)
    assert sync.startup_check(world.ctx).status == "unchanged"

    write(save, b"mine, played since", mtime=save.stat().st_mtime + 10)
    outcome = sync.startup_check(world.ctx)

    assert outcome.status == "uploaded" and world.server.uploads[-1][0] == "sync"


def test_a_newer_save_from_another_machine_is_taken_when_nothing_changed_here(world, monkeypatch):
    save = _synced_game(world, monkeypatch)
    newer = world.server.add_foreign_version({"users/USER/Saved Games/slot1.sav": b"from karasu-2"}, device_id=2)

    outcome = sync.startup_check(world.ctx)

    assert (outcome.status, outcome.from_device, outcome.files) == ("restored", "karasu-2", 1)
    assert save.read_bytes() == b"from karasu-2"
    assert load_state(7).seen_version_id == newer["id"]
    backups = list((world.tmp / "data/saves/7/backups").glob("*.zip"))
    assert len(backups) == 1 and zipfile.ZipFile(backups[0]).read("users/USER/Saved Games/slot1.sav") == b"mine"
    assert sync.startup_check(world.ctx).status == "unchanged"  # and it does not do it twice


def test_when_both_sides_changed_nothing_is_overwritten_and_the_user_is_told(world, monkeypatch):
    save = _synced_game(world, monkeypatch)
    write(save, b"played here since", mtime=save.stat().st_mtime + 10)
    world.server.add_foreign_version({"users/USER/Saved Games/slot1.sav": b"played there too"}, device_id=2)
    uploads = len(world.server.uploads)

    outcome = sync.startup_check(world.ctx)

    assert (outcome.status, outcome.from_device) == ("conflict", "karasu-2")
    assert save.read_bytes() == b"played here since" and len(world.server.uploads) == uploads
    assert sync.newer_elsewhere(world.ctx) is not None  # still offered when the game is started from here


def test_a_machine_that_does_not_know_where_the_saves_go_waits_for_setup(world):
    world.server.add_foreign_version({"users/USER/Saved Games/slot1.sav": b"theirs"}, device_id=2)
    outcome = sync.startup_check(world.ctx)
    assert (outcome.status, outcome.from_device) == ("setup", "karasu-2")
    assert world.server.uploads == [] and not (world.install / "pfx").exists()


def test_a_preview_changes_nothing_and_sends_nothing(world, monkeypatch):
    save = _synced_game(world, monkeypatch)
    write(save, b"changed", mtime=save.stat().st_mtime + 10)
    tracked_before = dict(load_state(7).tracked)
    uploads = len(world.server.uploads)

    result = sync.backup(world.ctx, sync.SYNC, upload=False)

    assert result.status == "changed" and result.changed == ["users/USER/Saved Games/slot1.sav"]
    assert len(world.server.uploads) == uploads and load_state(7).tracked == tracked_before
    assert sync.backup(world.ctx, sync.SYNC).status == "uploaded"  # the real thing still sees the change


def _game_with_a_save(world):
    save_install_manifest(7, [{"path": "game.exe", "size_bytes": 3, "sha1": hashlib.sha1(b"exe").hexdigest()}])
    write(world.install / "profile.sav", b"progress")


def test_every_backup_leaves_a_note_of_when_and_how_it_went_even_when_nothing_changed(world):
    _game_with_a_save(world)
    assert load_state(7).last_check_at is None

    sync.backup(world.ctx, sync.QUIT, since_ns=0)
    first = load_state(7)
    assert first.last_check_at and first.last_check_trigger == "quit" and first.last_check_status == "uploaded"

    sync.backup(world.ctx, sync.SYNC)
    again = load_state(7)
    assert again.last_check_status == "unchanged" and again.last_check_trigger == "sync"

    # A backup that only looks (nothing sent) is not a sync and leaves no note.
    stamp = again.last_check_at
    sync.backup(world.ctx, sync.SYNC, upload=False)
    assert load_state(7).last_check_at == stamp


def test_a_backup_that_fails_is_noted_as_failed(world, monkeypatch):
    _game_with_a_save(world)

    def boom(*a, **k):
        raise RuntimeError("connection error")

    monkeypatch.setattr(world.server, "upload_save", boom)
    with pytest.raises(RuntimeError):
        sync.backup(world.ctx, sync.QUIT, since_ns=0)
    assert load_state(7).last_check_status == "failed"


@pytest.mark.parametrize(
    ("status", "sent"), [("uploaded", True), ("needs-prefix", True), ("unchanged", False), ("nothing", False), ("duplicate", False)]
)
def test_the_log_goes_along_when_a_backup_has_something_to_explain(world, monkeypatch, status, sent):
    calls = []
    monkeypatch.setattr(sync, "_backup", lambda *a: sync.BackupResult(status))
    monkeypatch.setattr(sync.logsend, "send", lambda *args: calls.append(args))

    sync.backup(world.ctx, sync.QUIT)

    assert bool(calls) is sent
    if sent:
        assert calls[0][1:] == (1, world.ctx.settings, 7)


def test_the_log_goes_along_when_a_backup_fails(world, monkeypatch):
    calls = []

    def broken(*args):
        raise RuntimeError("server down")

    monkeypatch.setattr(sync, "_backup", broken)
    monkeypatch.setattr(sync.logsend, "send", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError):
        sync.backup(world.ctx, sync.QUIT)
    assert len(calls) == 1


# --- saves from another machine waiting for the prefix are never buried by this machine's first backup ---


def _waiting(world):
    """The game's own pfx folder is its prefix, not made yet, and the other machine's saves wait for it."""
    from mog_client.launcher import pfx_dir

    world.rec.prefix = str(pfx_dir(world.rec))
    pfx_dir(world.rec).mkdir()
    version = world.server.add_foreign_version({"users/USER/Saved Games/s.sav": b"theirs"})
    assert sync.restore(world.ctx, version["id"]).status == "needs-prefix"
    return pfx_dir(world.rec)


def test_a_backup_sends_nothing_while_saves_from_another_machine_wait_for_the_prefix(world):
    _waiting(world)

    result = sync.backup(world.ctx, sync.QUIT)

    assert result.status == "restore-pending" and world.server.uploads == []
    assert load_state(7).pending_restore  # still there, for the next start
    assert load_state(7).last_check_status == "restore-pending"


def test_once_the_prefix_is_made_the_waiting_saves_go_in_before_the_backup_looks(world):
    pfx = _waiting(world)
    (pfx / "drive_c/users/steamuser").mkdir(parents=True)

    result = sync.backup(world.ctx, sync.QUIT)

    assert (pfx / "drive_c/users/steamuser/Saved Games/s.sav").read_bytes() == b"theirs"
    assert load_state(7).pending_restore is None
    assert result.status in ("unchanged", "uploaded", "duplicate", "nothing")  # an ordinary backup, of what is there now


def test_a_file_of_the_games_own_in_the_way_keeps_the_restore_waiting_and_the_upload_held_back(world):
    pfx = _waiting(world)
    mine = pfx / "drive_c/users/steamuser/Saved Games/s.sav"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"fresh")

    result = sync.backup(world.ctx, sync.QUIT)

    assert result.status == "restore-pending" and mine.read_bytes() == b"fresh" and world.server.uploads == []


def test_a_manual_backup_is_the_users_own_choice_and_is_not_held_back(world):
    _waiting(world)
    result = sync.backup(world.ctx, sync.MANUAL, force=True)
    assert result.status != "restore-pending"
