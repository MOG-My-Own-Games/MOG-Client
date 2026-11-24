"""A game added from outside MOG: where its saves are looked for, and that the first sync takes all of them."""

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client import launcher, manager
from mog_client.config import InstalledGame, Settings, load_library
from mog_client.saves import importer, sync
from mog_client.saves import prefix as prefixes
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
    install = tmp_path / "itch" / "Zelmoor Ruins"
    install.mkdir(parents=True)
    server = FakeServer()
    return SimpleNamespace(home=home, install=install, server=server, tmp=tmp_path)


def context(world, **rec_fields):
    rec = InstalledGame(
        game_id=7, name="Zelmoor Ruins", install_dir=str(world.install), state="installed", imported=True, **rec_fields
    )
    return sync.Context(rec, Settings(), world.server, DeviceRecord("uid-aaaaaaaa", 1, "desktop"), engine="auto")


def write(path: Path, data: bytes, mtime: int = STARTED - 5000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def test_the_first_sync_takes_every_existing_save_so_the_untouched_ones_are_not_lost(world):
    ctx = context(world, target="linux")
    slots = [write(world.home / f".local/share/ZelmoorRuins/slot{i}.sav", f"save {i}".encode()) for i in range(8)]
    names = importer.suggest(ctx.rec)
    assert [s.key for s in names] == ["xdg-data/ZelmoorRuins"] and names[0].files == 8
    importer.remember(ctx.rec, [names[0].key])

    first = importer.first_sync(ctx)

    assert first.status == "uploaded"
    assert world.server.uploads == [("import", [f"xdg-data/ZelmoorRuins/slot{i}.sav" for i in range(8)])]
    # a play session touches one of them: the next version still holds all eight
    write(slots[3], b"save 3, played", mtime=STARTED + 100)
    sync.backup(ctx, sync.QUIT, since_ns=STARTED * 10**9)
    assert len(world.server.uploads[1][1]) == 8


def test_the_saves_inside_the_games_own_folder_are_offered_and_taken(world):
    ctx = context(world, target="linux")
    write(world.install / "saves/profile1.dat", b"p1")
    write(world.install / "saves/profile2.dat", b"p2")
    write(world.install / "data/textures.pak", b"art")
    write(world.install / "slot.sav", b"s")

    offered = {s.key for s in importer.suggest(ctx.rec)}

    assert offered == {"game/saves", "game/slot.sav"}
    importer.remember(ctx.rec, sorted(offered))
    assert importer.first_sync(ctx).status == "uploaded"
    assert world.server.uploads[0][1] == ["game/saves/profile1.dat", "game/saves/profile2.dat", "game/slot.sav"]


def test_a_folder_named_like_the_game_ranks_above_other_programs_folders(world):
    ctx = context(world, target="linux")
    write(world.home / ".config/ZelmoorRuins/a.cfg", b"a")
    write(world.home / ".config/otherthing/b.cfg", b"b")

    keys = [s.key for s in importer.suggest(ctx.rec)]

    assert keys == ["xdg-config/ZelmoorRuins"]  # nothing links the other one to the game


def test_the_servers_manifest_hint_is_offered_when_the_folder_exists(world):
    ctx = context(world, target="linux")
    write(world.home / ".local/share/weird-name/s.sav", b"s")

    offered = importer.suggest(ctx.rec, hints=["xdg-data/weird-name", "xdg-data/not-there"])

    assert [(s.key, s.score) for s in offered] == [("xdg-data/weird-name", 2)]


def test_a_windows_game_in_a_wine_prefix_is_found_in_its_profile_and_the_container_folder_is_not_taken(world):
    prefix = world.tmp / "prefixes" / "lost"
    docs = prefix / "pfx/drive_c/users/steamuser/Documents"
    write(docs / "My Games/Zelmoor Ruins/slot1.sav", b"1")
    write(docs / "My Games/Other Game/slot.sav", b"2")
    write(docs / "notes.txt", b"mine")
    ctx = context(world, target="windows", prefix=str(prefix))

    offered = importer.suggest(ctx.rec, prefix)

    assert offered[0].key == "users/USER/Documents/My Games/Zelmoor Ruins" and offered[0].score == 2
    assert "users/USER/Documents/My Games/Other Game" in {s.key for s in offered}
    assert "users/USER/Documents/notes.txt" in {s.key for s in offered}  # a file in the root is offered as itself, never the root
    assert "users/USER/Documents" not in {s.key for s in offered}
    importer.remember(ctx.rec, [offered[0].key], prefix)
    assert importer.first_sync(ctx).status == "uploaded"
    assert world.server.uploads[0][1] == ["users/USER/Documents/My Games/Zelmoor Ruins/slot1.sav"]


def test_a_shared_prefix_is_not_taken_whole_and_a_new_one_is(world):
    shared = world.tmp / "wine"
    write(shared / "drive_c/users/me/Documents/other.sav", b"x")
    own = world.tmp / "own"
    write(own / "drive_c/users/me/Documents/mine.sav", b"x")

    shared_ctx = context(world, target="windows", prefix=str(shared))
    importer.remember(shared_ctx.rec, [], shared)
    assert sync.backup(shared_ctx, sync.MANUAL, force=True).status == "nothing"  # confirmed none: nothing of the others' goes up

    own_ctx = context(world, target="windows", prefix=str(own), prefix_dedicated=True)
    assert prefixes.resolve_prefix(own_ctx.rec, "auto").source == prefixes.FROM_GAME


def test_default_prefixes_are_recognised(world):
    assert prefixes.is_default(world.home / ".wine")
    assert prefixes.is_default(world.home / "Games/umu/umu-default")
    assert prefixes.is_default(world.home / "Faugus/default")
    assert not prefixes.is_default(world.home / "Faugus/zelmoor-ruins")


def test_a_second_machine_with_no_saves_takes_the_first_ones(world):
    world.server.add_foreign_version({"xdg-data/ZelmoorRuins/slot1.sav": b"from the first machine"})
    ctx = context(world, target="linux")
    importer.remember(ctx.rec, [])

    first = importer.first_sync(ctx)

    assert first.status == "restored"
    assert (world.home / ".local/share/ZelmoorRuins/slot1.sav").read_bytes() == b"from the first machine"


def test_saves_on_both_machines_are_a_conflict_that_keeping_ours_settles(world):
    foreign = world.server.add_foreign_version({"xdg-data/ZelmoorRuins/slot1.sav": b"theirs"})
    ctx = context(world, target="linux")
    mine = write(world.home / ".local/share/ZelmoorRuins/slot1.sav", b"mine")
    importer.remember(ctx.rec, ["xdg-data/ZelmoorRuins"])

    first = importer.first_sync(ctx)

    assert first.status == "conflict" and mine.read_bytes() == b"mine"  # nothing was overwritten
    settled = importer.settle_keeping_local(ctx, foreign["id"])
    assert settled.status == "uploaded" and mine.read_bytes() == b"mine"
    archive = world.server.blobs[world.server.versions[-1]["id"]]
    assert zipfile.ZipFile(__import__("io").BytesIO(archive)).read("xdg-data/ZelmoorRuins/slot1.sav") == b"mine"


def test_a_windows_game_on_windows_is_read_from_the_real_profile(world, monkeypatch):
    profile = world.tmp / "winhome"
    monkeypatch.setattr(prefixes, "has_prefixes", lambda: False)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("ProgramData", raising=False)
    monkeypatch.delenv("PUBLIC", raising=False)
    write(profile / "Saved Games/Zelmoor Ruins/slot1.sav", b"1")
    write(profile / "AppData/Local/Zelmoor Ruins/cfg.ini", b"c")
    ctx = context(world, target="windows")

    offered = [s.key for s in importer.suggest(ctx.rec)]

    assert offered == ["users/USER/Saved Games/Zelmoor Ruins", "users/USER/AppData/Local/Zelmoor Ruins"] or set(offered) == {
        "users/USER/Saved Games/Zelmoor Ruins",
        "users/USER/AppData/Local/Zelmoor Ruins",
    }
    importer.remember(ctx.rec, offered)
    assert importer.first_sync(ctx).status == "uploaded"
    # and a save from a Wine prefix lands in the Windows profile
    world.server.add_foreign_version({"users/USER/Saved Games/Zelmoor Ruins/slot2.sav": b"2"})
    sync.restore(ctx, world.server.versions[-1]["id"])
    assert (profile / "Saved Games/Zelmoor Ruins/slot2.sav").read_bytes() == b"2"


def test_targets_are_told_from_the_programs_in_the_folder(world):
    write(world.install / "game.exe", b"MZ")
    write(world.install / "start.sh", b"#!/bin/sh")
    write(world.install / "Thing.app/Contents/Info.plist", b"x")

    assert importer.detect_targets(world.install, "linux") == ["linux", "macos", "windows"]
    assert importer.detect_targets(world.install, "windows")[0] == "windows"
    assert importer.supported_targets("windows") == ["windows"] and not importer.needs_prefix("windows", "windows")
    assert importer.needs_prefix("windows", "linux") and not importer.needs_prefix("linux", "linux")


def test_a_mac_key_means_nothing_on_linux(world):
    from mog_client.saves.locations import is_native_key, native_root

    assert native_root("mac-support") is None and not is_native_key("mac-support/Game/s.sav")


def test_an_added_game_keeps_its_launch_files_and_its_prefix_out_of_its_folder(world, monkeypatch):
    prefix = world.tmp / "elsewhere" / "wineprefix"
    exe = write(world.install / "lost.exe", b"MZ")
    game = {"id": 7, "name": "Zelmoor Ruins", "fs_name": "Zelmoor Ruins"}
    rec = manager.import_game(game, world.install, importer.WINDOWS, prefix, executable=str(exe))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    monkeypatch.setattr(launcher, "resolve_wine", lambda name: (None, None))

    _cmd, env = launcher.launch_command(rec)
    script = Path(launcher.write_launch_script(rec))

    assert env["WINEPREFIX"] == str(prefix)
    assert script.parent == launcher.entries_dir(rec) and script.is_file()
    assert sorted(p.name for p in world.install.iterdir()) == ["lost.exe"]  # nothing written in the game's folder
    assert load_library()[7].imported is True


def test_removing_an_added_game_deletes_neither_its_folder_nor_its_prefix(world):
    prefix = world.tmp / "wineprefix"
    write(prefix / "drive_c/users/me/Documents/s.sav", b"s")
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix)
    write(launcher.entries_dir(rec) / "Zelmoor Ruins.sh", b"#!/bin/sh")

    assert manager.uninstall(rec) == []

    assert (world.install / "lost.exe").is_file() and (prefix / "drive_c/users/me/Documents/s.sav").is_file()
    assert not launcher.entries_dir(rec).exists() and 7 not in load_library()


def test_adding_a_game_twice_is_refused(world):
    manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)
    with pytest.raises(RuntimeError, match="already"):
        manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)


def test_moving_into_mog_takes_the_folder_and_the_prefix_and_makes_it_an_ordinary_game(world, monkeypatch):
    root = world.tmp / "MOG-games"
    root.mkdir()
    prefix = world.tmp / "wineprefix"
    write(prefix / "drive_c/users/me/Documents/Zelmoor Ruins/s.sav", b"s")
    exe = write(world.install / "bin/lost.exe", b"MZ")
    settings = Settings(install_dirs=[str(root)])
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix, executable=str(exe))
    sync.remember_prefix(7, prefix, prefixes.FROM_USER)

    notes = manager.move_into_mog(rec, settings)

    dest = root / "Zelmoor Ruins"
    assert notes == [] and not world.install.exists() and not prefix.exists()
    assert (dest / "bin/lost.exe").is_file() and (dest / "pfx/drive_c/users/me/Documents/Zelmoor Ruins/s.sav").is_file()
    moved = load_library()[7]
    assert (moved.imported, moved.install_dir, moved.prefix, moved.executable) == (
        False, str(dest), str(dest / "pfx"), str(dest / "bin/lost.exe"),
    )  # fmt: skip
    assert moved.prefix_shared  # it was shared: only the confirmed folders are the game's saves
    assert prefixes.resolve_prefix(moved, "auto").source == prefixes.FROM_USER
    assert load_state(7).prefix == str(dest / "pfx")


def test_moving_into_mog_leaves_a_link_where_the_folder_and_the_prefix_were_when_asked(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    prefix = world.tmp / "wineprefix"
    write(prefix / "drive_c/s.sav", b"s")
    exe = write(world.install / "bin/lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix, executable=str(exe))

    notes = manager.move_into_mog(rec, Settings(install_dirs=[str(root)]), link_back=True)

    dest = root / "Zelmoor Ruins"
    assert notes == []
    assert world.install.is_symlink() and world.install.resolve() == dest.resolve()
    assert prefix.is_symlink() and prefix.resolve() == (dest / "pfx").resolve()
    assert (world.install / "bin/lost.exe").is_file() and (prefix / "drive_c/s.sav").is_file()
    assert load_library()[7].install_dir == str(dest)  # the record keeps the real place, not the link


def test_the_link_left_for_a_game_with_loose_files_points_at_the_folder_they_were_put_in(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)

    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]), link_back=True)

    assert world.install.resolve() == (root / "Zelmoor Ruins/Zelmoor Ruins").resolve()
    assert (world.install / "lost.exe").is_file()


def test_moving_into_mog_leaves_no_link_unless_asked(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)

    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    assert not world.install.exists() and not world.install.is_symlink()


def test_moving_into_mog_refuses_to_overwrite_and_leaves_everything_as_it_was(world):
    root = world.tmp / "MOG-games"
    write(root / "Zelmoor Ruins/other.exe", b"MZ")
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)

    with pytest.raises(manager.MoveError, match="other things in it"):
        manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    assert (world.install / "lost.exe").is_file() and (root / "Zelmoor Ruins/other.exe").is_file()
    assert load_library()[7].imported is True


def test_moving_into_mog_takes_the_place_of_an_empty_folder(world):
    root = world.tmp / "MOG-games"
    (root / "Zelmoor Ruins").mkdir(parents=True)
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)

    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    assert (root / "Zelmoor Ruins/Zelmoor Ruins/lost.exe").is_file() and not world.install.exists()
    assert load_library()[7].imported is False


def test_moving_into_mog_puts_loose_game_files_in_a_folder_of_their_own_apart_from_mogs(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    prefix = world.install / "pfx"
    write(prefix / "drive_c/s.sav", b"s")
    exe = write(world.install / "Zelmoor Ruins.x86_64", b"ELF")
    write(world.install / "Zelmoor Ruins_Data/level0", b"d")
    write(world.install / ".itch/receipt", b"r")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX, prefix, executable=str(exe))

    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    dest = root / "Zelmoor Ruins"
    game = dest / "Zelmoor Ruins"
    assert {p.name for p in dest.iterdir()} >= {"Zelmoor Ruins", "pfx"}
    assert not {p.name for p in dest.iterdir()} & {"Zelmoor Ruins.x86_64", "Zelmoor Ruins_Data", ".itch"}  # none left among MOG's
    assert (game / "Zelmoor Ruins.x86_64").is_file() and (game / "Zelmoor Ruins_Data/level0").is_file() and (game / ".itch/receipt").is_file()
    assert not (dest / "Zelmoor Ruins.x86_64").exists() and (dest / "pfx/drive_c/s.sav").is_file()  # the prefix stays at the top
    moved = load_library()[7]
    assert (moved.files_dir, moved.executable, moved.prefix) == (str(game), str(game / "Zelmoor Ruins.x86_64"), str(dest / "pfx"))


def test_moving_into_mog_leaves_a_game_already_in_a_folder_of_its_own_as_it_is(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    exe = write(world.install / "Game v3/lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, executable=str(exe))

    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    moved = load_library()[7]
    assert moved.files_dir is None and moved.executable == str(root / "Zelmoor Ruins/Game v3/lost.exe")
    assert (root / "Zelmoor Ruins/Game v3/lost.exe").is_file()


def test_a_game_folder_is_tucked_apart_from_a_prefix_inside_it_that_has_another_name(world):
    write(world.install / "lost.exe", b"MZ")
    write(world.install / "wineprefix/drive_c/s.sav", b"s")
    rec = manager.import_game(
        {"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, world.install / "wineprefix",
        executable=str(world.install / "lost.exe"),
    )

    folder = manager.tuck_game_into_folder(rec)

    assert folder == world.install / "Zelmoor Ruins"
    assert (folder / "lost.exe").is_file() and rec.prefix == str(folder / "wineprefix") and rec.executable == str(folder / "lost.exe")


def test_moving_into_mog_keeps_the_prefix_a_folder_with_only_it_already_holds(world):
    root = world.tmp / "MOG-games"
    prefix = root / "Zelmoor Ruins/pfx"
    write(prefix / "drive_c/users/me/Documents/Zelmoor Ruins/s.sav", b"s")
    exe = write(world.install / "bin/lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix, executable=str(exe))

    notes = manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    dest = root / "Zelmoor Ruins"
    assert notes == [] and not world.install.exists()
    assert (dest / "bin/lost.exe").is_file() and (prefix / "drive_c/users/me/Documents/Zelmoor Ruins/s.sav").is_file()
    moved = load_library()[7]
    assert (moved.imported, moved.install_dir, moved.prefix, moved.prefix_shared) == (False, str(dest), str(prefix), False)
    assert [p.name for p in root.iterdir()] == ["Zelmoor Ruins"]  # the prefix was not left aside


def test_a_failed_move_puts_the_prefix_back_in_the_folder_it_was_in(world, monkeypatch):
    root = world.tmp / "MOG-games"
    prefix = root / "Zelmoor Ruins/pfx"
    write(prefix / "drive_c/s.sav", b"s")
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix)

    def boom(src, dst):
        raise manager.MoveError("disk full")

    monkeypatch.setattr(manager, "_move_tree", boom)
    with pytest.raises(manager.MoveError, match="disk full"):
        manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))

    assert (prefix / "drive_c/s.sav").is_file() and (world.install / "lost.exe").is_file()
    assert [p.name for p in root.iterdir()] == ["Zelmoor Ruins"] and load_library()[7].imported is True


def test_putting_back_returns_the_folder_the_prefix_and_the_imported_state_and_takes_the_link_away(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    prefix = world.tmp / "wineprefix"
    write(prefix / "drive_c/s.sav", b"s")
    exe = write(world.install / "lost.exe", b"MZ")
    write(world.install / "data/a.pak", b"p")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix, executable=str(exe))
    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]), link_back=True)
    assert world.install.is_symlink() and prefix.is_symlink() and load_library()[7].moved_from["dir"] == str(world.install)

    notes = manager.put_back(load_library()[7])

    assert notes == [] and not root.joinpath("Zelmoor Ruins").exists()
    assert not world.install.is_symlink() and (world.install / "lost.exe").is_file() and (world.install / "data/a.pak").is_file()
    assert not prefix.is_symlink() and (prefix / "drive_c/s.sav").is_file()
    back = load_library()[7]
    assert (back.imported, back.install_dir, back.files_dir, back.prefix, back.moved_from) == (
        True, str(world.install), None, str(prefix), None,
    )  # fmt: skip
    assert back.executable == str(world.install / "lost.exe")
    assert not (world.install / "Zelmoor Ruins.sh").exists()


def test_putting_back_a_game_that_was_in_one_folder_and_whose_prefix_stayed_outside(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    prefix = world.tmp / "wineprefix"
    write(prefix / "drive_c/s.sav", b"s")
    exe = write(world.install / "Game v3/lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.WINDOWS, prefix, executable=str(exe))
    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]), move_prefix=False)

    manager.put_back(load_library()[7])

    back = load_library()[7]
    assert (world.install / "Game v3/lost.exe").is_file() and back.executable == str(world.install / "Game v3/lost.exe")
    assert back.prefix == str(prefix) and (prefix / "drive_c/s.sav").is_file() and not root.joinpath("Zelmoor Ruins").exists()


def test_putting_back_keeps_a_prefix_made_after_the_move_where_it_is(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)
    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))
    made = root / "Zelmoor Ruins/pfx"
    write(made / "drive_c/new.sav", b"n")
    rec = load_library()[7]
    manager._update(rec, prefix=str(made))

    notes = manager.put_back(load_library()[7])

    assert (made / "drive_c/new.sav").is_file() and load_library()[7].prefix == str(made)
    assert any("made after the move" in n for n in notes) and (world.install / "lost.exe").is_file()


def test_putting_back_refuses_a_folder_that_is_taken_and_leaves_everything_as_it_was(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)
    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))
    write(world.install / "someone-else.txt", b"x")

    with pytest.raises(manager.MoveError, match="other things in it"):
        manager.put_back(load_library()[7])

    assert (root / "Zelmoor Ruins/Zelmoor Ruins/lost.exe").is_file() and load_library()[7].imported is False


def test_a_game_that_never_came_from_outside_cannot_be_put_back(world):
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)

    with pytest.raises(manager.MoveError, match="no place to put it back"):
        manager.put_back(rec)


def test_a_link_to_somewhere_else_is_not_taken_away_when_putting_back(world):
    root = world.tmp / "MOG-games"
    root.mkdir()
    write(world.install / "lost.exe", b"MZ")
    rec = manager.import_game({"id": 7, "name": "Zelmoor Ruins"}, world.install, importer.LINUX)
    manager.move_into_mog(rec, Settings(install_dirs=[str(root)]))
    elsewhere = world.tmp / "elsewhere"
    elsewhere.mkdir()
    world.install.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(manager.MoveError, match="link to somewhere else"):
        manager.put_back(load_library()[7])

    assert world.install.is_symlink() and (root / "Zelmoor Ruins/Zelmoor Ruins/lost.exe").is_file()


# --- a game that runs in a Flatpak app's sandbox ---------------------------------------------------------------------------


def flatpak(world, app="io.itch.itch"):
    return world.home / ".var/app" / app


def test_a_folder_in_a_flatpak_apps_sandbox_is_offered_with_the_app_and_a_usual_key(world):
    ctx = context(world, target="linux")
    for i in range(3):
        write(flatpak(world) / f"config/unity3d/Studio/Zelmoor Ruins/slot{i}.sav", f"s{i}".encode())
    write(flatpak(world) / "config/itch/db/noise.db", b"not the game's")

    found = importer.suggest(ctx.rec)

    assert [(s.key, s.sandbox, s.files) for s in found] == [("xdg-config/unity3d/Studio/Zelmoor Ruins", "io.itch.itch", 3)]
    assert importer.label(found[0].key, found[0].sandbox).endswith("(Flatpak: io.itch.itch)")
    assert ".var/app/io.itch.itch/config/unity3d" in importer.label(found[0].key, found[0].sandbox)


def test_the_same_folder_in_the_real_home_and_in_a_sandbox_are_two_rows_that_cannot_be_mixed(world):
    ctx = context(world, target="linux")
    write(world.home / ".config/unity3d/Studio/Zelmoor Ruins/a.sav", b"a")
    write(flatpak(world) / "config/unity3d/Studio/Zelmoor Ruins/b.sav", b"b")

    found = importer.suggest(ctx.rec)

    assert sorted(((s.key, s.sandbox) for s in found), key=lambda t: (t[0], t[1] or "")) == [
        ("xdg-config/unity3d/Studio/Zelmoor Ruins", None),
        ("xdg-config/unity3d/Studio/Zelmoor Ruins", "io.itch.itch"),
    ]
    assert len({s.token for s in found}) == 2
    with pytest.raises(RuntimeError, match="one place"):
        importer.resolve_choice([s.token for s in found])
    assert importer.resolve_choice([found[0].token]) == (["xdg-config/unity3d/Studio/Zelmoor Ruins"], found[0].sandbox)


def test_the_saves_of_a_sandboxed_game_go_up_under_the_usual_keys_and_come_back_into_the_sandbox(world):
    ctx = context(world, target="linux", sandbox="io.itch.itch")
    for i in range(2):
        write(flatpak(world) / f"config/unity3d/Studio/Zelmoor Ruins/slot{i}.sav", f"s{i}".encode())
    importer.remember(ctx.rec, ["xdg-config/unity3d/Studio/Zelmoor Ruins"])

    assert importer.first_sync(ctx).status == "uploaded"
    assert world.server.uploads == [
        ("import", [f"xdg-config/unity3d/Studio/Zelmoor Ruins/slot{i}.sav" for i in range(2)])
    ]  # keys that mean the same on a machine with the game outside Flatpak

    version = world.server.add_foreign_version({"xdg-config/unity3d/Studio/Zelmoor Ruins/slot1.sav": b"newer"})
    restored = sync.restore(ctx, version["id"])
    assert restored.status == "restored"
    assert (flatpak(world) / "config/unity3d/Studio/Zelmoor Ruins/slot1.sav").read_bytes() == b"newer"
    assert not (world.home / ".config/unity3d").exists()


def test_switching_the_sandbox_off_goes_back_to_the_usual_folders(world):
    ctx = context(world, target="linux", sandbox="io.itch.itch", sandbox_enabled=False)
    write(flatpak(world) / "config/unity3d/Studio/Zelmoor Ruins/slot.sav", b"sandboxed")
    write(world.home / ".config/unity3d/Studio/Zelmoor Ruins/slot.sav", b"real")
    importer.remember(ctx.rec, ["xdg-config/unity3d/Studio/Zelmoor Ruins"])

    assert ctx.rec.in_sandbox is None
    importer.first_sync(ctx)

    assert world.server.uploads[0][1] == ["xdg-config/unity3d/Studio/Zelmoor Ruins/slot.sav"]
    assert sync.target_for_key("xdg-config/a", None, world.install, ctx.rec.in_sandbox) == world.home / ".config/a"
    assert sync.target_for_key("xdg-config/a", None, world.install, "io.itch.itch") == flatpak(world) / "config/a"


def test_a_folder_picked_inside_a_sandbox_names_its_app(world):
    from mog_client.gui.importing import key_for_folder
    from mog_client.saves.locations import sandbox_of

    folder = flatpak(world) / "config/unity3d/Studio/Zelmoor Ruins"
    folder.mkdir(parents=True)

    assert sandbox_of(folder) == "io.itch.itch" and sandbox_of(flatpak(world)) is None
    assert sandbox_of(world.home / ".config/x") is None
    assert key_for_folder(folder, world.install, "io.itch.itch") == "xdg-config/unity3d/Studio/Zelmoor Ruins"
    assert key_for_folder(folder, world.install) == "home/.var/app/io.itch.itch/config/unity3d/Studio/Zelmoor Ruins"


def test_a_sandboxed_game_is_started_with_the_apps_folders_and_the_switch_takes_them_away(world):
    from mog_client import launcher

    (flatpak(world) / "config").mkdir(parents=True)
    rec = context(world, target="linux", sandbox="io.itch.itch").rec
    rec.executable = str(write(world.install / "Game.x86_64", b"\x7fELF" + b"\0" * 60))
    (world.install / "Game.x86_64").chmod(0o755)

    cmd, env = launcher.launch_command(rec)
    assert cmd[0] == rec.executable
    assert env == {
        "XDG_CONFIG_HOME": str(flatpak(world) / "config"),
        "XDG_DATA_HOME": str(flatpak(world) / "data"),
        "XDG_STATE_HOME": str(flatpak(world) / ".local/state"),
    }
    assert "XDG_DATA_HOME=" in " ".join(launcher.standalone_command(rec))  # the script, desktop entry and Steam carry them too

    rec.sandbox_enabled = False
    assert launcher.launch_command(rec)[1] == {}
    rec.sandbox_enabled, rec.sandbox = True, "gone.app"
    assert launcher.launch_command(rec)[1] == {}  # the app's folder is not there: nothing to point at


def test_switching_the_sandbox_is_kept_and_the_entries_are_written_again(world, monkeypatch):
    rec = context(world, target="linux", sandbox="io.itch.itch").rec
    manager._update(rec)
    rewritten = []
    monkeypatch.setattr(manager, "_rewrite_entries", lambda r, preference: rewritten.append(r.sandbox_enabled))

    manager.set_sandbox_enabled(rec, False)
    manager.set_sandbox_enabled(rec, False)  # nothing to do the second time
    assert load_library()[7].sandbox_enabled is False and rewritten == [False]

    manager.set_sandbox(rec, "io.itch.itch")  # found again by choosing its folders: on
    assert load_library()[7].sandbox_enabled is True


def test_adopting_a_sandbox_replaces_the_real_home_folders_and_keeps_the_game_folder_ones(world, monkeypatch):
    rec = context(world, target="linux").rec
    manager._update(rec)
    importer.remember(rec, ["xdg-config/unity3d/Studio/Zelmoor Ruins", "game/saves"])
    monkeypatch.setattr(manager, "_rewrite_entries", lambda r, preference: None)

    manager.adopt_sandbox(rec, "io.itch.itch", ["xdg-data/ZelmoorRuins"])

    assert load_state(7).includes == ["game/saves", "xdg-data/ZelmoorRuins"]
    saved = load_library()[7]
    assert (saved.sandbox, saved.sandbox_enabled, saved.in_sandbox) == ("io.itch.itch", True, "io.itch.itch")


def test_the_sandbox_suggestions_are_only_folders_named_like_the_game_in_an_apps_folders(world):
    ctx = context(world, target="linux")
    write(flatpak(world) / "config/unity3d/Studio/Zelmoor Ruins/a.sav", b"a")
    write(flatpak(world) / "config/other/thing.cfg", b"b")
    write(world.home / ".config/ZelmoorRuins/c.sav", b"c")  # the real home is not a sandbox

    found = importer.sandbox_suggestions(ctx.rec)

    assert [(s.key, s.sandbox) for s in found] == [("xdg-config/unity3d/Studio/Zelmoor Ruins", "io.itch.itch")]
    assert importer.sandbox_suggestions(context(world, target="windows").rec) == []
