"""Quiver Launcher's games: read from its files, kept as a library of their own, and their saves limited to the places named."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeServer

from mog_client import manager, quiver
from mog_client.config import InstalledGame, Settings, load_library
from mog_client.saves import importer, sync
from mog_client.saves.devices import DeviceRecord
from mog_client.saves.state import load_state, save_install_manifest

STARTED = 2_000_000_000
ZELDA = "github:Zelda64Recomp/Zelda64Recomp"


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "QUIVER_DATA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(quiver.sys, "platform", "linux")
    data = home / ".local/share/com.quiverlauncher.app"
    data.mkdir(parents=True)
    return SimpleNamespace(home=home, data=data, server=FakeServer(), tmp=tmp_path)


def put(path: Path, data: bytes = b"x", mtime: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def install(world, qid=ZELDA, folder="Zelda64Recompiled", exe="Zelda64Recompiled", name=None, **extra) -> Path:
    """One game in Quiver's files: its library and installs lists, and the program in its folder."""
    root = world.data / "apps" / folder
    program = put(root / exe, b"\x7fELF....")
    program.chmod(0o755)
    library = json.loads((world.data / "library.json").read_text()) if (world.data / "library.json").exists() else []
    library.append({"id": qid, "slug": folder, "overrides": {"name": name} if name else {}})
    installs = json.loads((world.data / "installs.json").read_text()) if (world.data / "installs.json").exists() else {}
    installs[qid] = {"folder": folder, "version": "1.2.3", "executables": [exe], **extra}
    (world.data / "library.json").write_text(json.dumps(library))
    (world.data / "installs.json").write_text(json.dumps(installs))
    return root


def test_the_data_folder_is_found_where_quiver_keeps_it_or_where_the_user_pointed(world, tmp_path):
    assert quiver.find() is None  # nothing installed yet
    install(world)
    assert quiver.find() == world.data
    portable = tmp_path / "Quiver" / "data"
    portable.mkdir(parents=True)
    (portable / "installs.json").write_text("{}")
    assert quiver.find(str(portable)) == portable


def test_the_games_quiver_installed_are_listed_with_their_folder_and_program(world):
    root = install(world, name="Zelda 64: Recompiled")
    install(world, qid="github:a/b", folder="Other-Port", exe="other.x86_64")

    games = quiver.installed(world.data)

    assert [g.name for g in games] == ["Zelda 64: Recompiled", "Other"]
    assert games[0].folder == root and games[0].executable == root / "Zelda64Recompiled"
    assert games[0].target == "linux" and games[0].prefix is None and games[0].version == "1.2.3"


def test_a_game_whose_folder_is_gone_or_that_was_removed_is_not_listed(world):
    install(world)
    install(world, qid="github:a/b", folder="Gone")
    import shutil

    shutil.rmtree(world.data / "apps" / "Gone")
    install(world, qid="github:c/d", folder="Removed")
    library = json.loads((world.data / "library.json").read_text())
    library[-1]["removed"] = True
    (world.data / "library.json").write_text(json.dumps(library))

    assert [g.id for g in quiver.installed(world.data)] == [ZELDA]


def test_the_apps_folder_the_user_moved_and_a_game_kept_outside_it_are_followed(world, tmp_path):
    elsewhere = tmp_path / "Games" / "Apps"
    (world.data / "apps-folder.json").write_text(json.dumps({"path": str(elsewhere)}))
    put(elsewhere / "Moved" / "moved.x86_64", b"\x7fELF")
    outside = put(tmp_path / "Own" / "Port" / "port.x86_64", b"\x7fELF")
    (world.data / "installs.json").write_text(
        json.dumps({"github:a/moved": {"folder": "Moved", "executables": ["moved.x86_64"]},
                    "github:a/own": {"folder": "Port", "dir": str(outside.parent), "executables": ["port.x86_64"]}})
    )

    folders = {g.id: g.folder for g in quiver.installed(world.data)}

    assert folders == {"github:a/moved": elsewhere / "Moved", "github:a/own": outside.parent}


def test_a_windows_program_on_linux_is_a_game_for_wine_in_quivers_own_prefix(world):
    install(world, qid="github:a/win", folder="WinPort", exe="winport.exe")

    game = quiver.installed(world.data)[0]

    assert game.target == "windows" and game.prefix == world.data / "proton" / "WinPort"


def test_the_saves_of_a_known_port_depend_on_whether_it_runs_portable(world):
    root = install(world)
    assert quiver.installed(world.data)[0].keys == ["xdg-config/Zelda64Recompiled/saves"]
    put(root / "portable.txt", b"")
    assert quiver.installed(world.data)[0].keys == ["game/saves"]
    install(world, qid="github:a/unknown", folder="Unknown")
    assert quiver.installed(world.data)[1].keys == []  # a port MOG does not know has no saves until the user names them


def test_a_game_with_no_record_is_listed_under_a_library_of_its_own_with_an_id_no_server_has(world):
    install(world)
    entry = quiver.standin(quiver.installed(world.data)[0])

    assert entry["id"] < 0 and entry["library_id"] == quiver.LIBRARY_ID and entry["quiver"] == ZELDA
    assert quiver.virtual_id(ZELDA) == entry["id"] and quiver.is_virtual(entry["id"])


def recorded(world, **extra):
    install(world, **extra)
    game = quiver.installed(world.data)[0]
    game.executable = None  # no launch files are written in a test
    rec = manager.adopt_quiver_game(game, {"id": 41, "name": "Zelda 64: Recompiled"})
    return game, rec


def test_the_first_start_makes_an_imported_record_that_keeps_quivers_folder_and_lists_the_saves(world):
    game, rec = recorded(world)

    assert (rec.imported, rec.explicit_saves, rec.quiver_id, rec.target) == (True, True, ZELDA, "linux")
    assert Path(rec.install_dir) == game.folder
    assert load_state(41).includes == ["xdg-config/Zelda64Recompiled/saves"]
    assert manager.record_of_quiver(ZELDA).game_id == 41 and manager.record_of_quiver("github:no/one") is None


def test_the_record_follows_quiver_when_an_update_moves_the_folder_or_renames_the_program(world):
    game, rec = recorded(world)
    (world.data / "apps" / "Zelda64Recompiled").rename(world.data / "apps" / "Zelda64RecompiledNew")
    install(world, qid=ZELDA, folder="Zelda64RecompiledNew", exe="Zelda64Recompiled2")
    # the file lists now hold the game twice; the library keeps the first entry's order, installs the last
    changed = manager.follow_quiver(quiver.installed(world.data))

    assert [r.game_id for r in changed] == [41]
    moved = load_library()[41]
    assert moved.install_dir.endswith("Zelda64RecompiledNew") and moved.executable.endswith("Zelda64Recompiled2")
    assert manager.follow_quiver(quiver.installed(world.data)) == []  # nothing left to change


def context(world, tmp_path_install: Path, **fields):
    rec = InstalledGame(
        game_id=41, name="Zelda 64", install_dir=str(tmp_path_install), state="installed", imported=True,
        target="linux", quiver_id=ZELDA, explicit_saves=True, **fields,
    )
    return sync.Context(rec, Settings(), world.server, DeviceRecord("uid-aaaaaaaa", 1, "karasu"), engine="auto")


def test_what_quiver_writes_into_the_game_folder_is_never_a_save_even_inside_a_session(world):
    root = install(world)
    ctx = context(world, root)
    importer.remember(ctx.rec, ["game/saves"])
    # a manifest of what was there at the first install is the one thing that would let an update's files pass for saves
    save_install_manifest(41, [{"path": "Zelda64Recompiled", "size_bytes": 9, "sha1": "0" * 40}])
    put(root / "saves" / "slot1.sav", b"s1", STARTED - 5000)
    for name in ("assets/shaders.bin", "Zelda64Recompiled", "mods/patch.o2r", "new-recomp.bin"):
        put(root / name, os.urandom(64), STARTED + 10)  # an update lands during the session

    importer.first_sync(ctx)
    sync.backup(ctx, sync.QUIT, since_ns=STARTED * 10**9)

    assert world.server.uploads[0] == ("import", ["game/saves/slot1.sav"])
    assert all(keys == ["game/saves/slot1.sav"] for _t, keys in world.server.uploads)


def test_the_servers_guess_of_a_native_games_saves_is_not_added_to_a_listed_game(world):
    root = install(world)
    world.server.manifest = ["<base>/assets"]
    ctx = context(world, root)
    importer.remember(ctx.rec, ["game/saves"])
    put(root / "saves/slot1.sav", b"s1", STARTED - 5000)
    put(root / "assets/shaders.bin", b"big", STARTED - 5000)

    sync.backup(ctx, sync.IMPORT, force=True)

    assert world.server.uploads[-1][1] == ["game/saves/slot1.sav"]
    assert world.server.manifest_asked == 0


def test_a_listed_game_whose_places_hold_too_much_is_refused_before_it_is_zipped(world, monkeypatch):
    root = install(world)
    ctx = context(world, root)
    importer.remember(ctx.rec, ["game/saves"])
    put(root / "saves/slot1.sav", b"x" * 2048, STARTED - 5000)
    monkeypatch.setattr(sync, "EXPLICIT_LIMIT", 1024)

    with pytest.raises(RuntimeError, match="more than the server takes"):
        sync.backup(ctx, sync.IMPORT, force=True)

    assert world.server.uploads == []


def test_the_saves_of_a_port_with_no_places_named_are_not_guessed_from_its_folder(world):
    root = install(world, qid="github:a/unknown", folder="Unknown")
    ctx = context(world, root)
    put(root / "saves/slot1.sav", b"s1", STARTED - 5000)
    put(root / "data/level.pak", b"art", STARTED + 10)

    result = sync.backup(ctx, sync.QUIT, since_ns=STARTED * 10**9)

    assert world.server.uploads == []
    assert result.status != "uploaded"


# --- Quiver 3.x: apps.json and settings.json beside the AppImage or in ~/.local/share/QuiverLauncher ---


def v3_install(world, folder="SoulcaliburII-SoulcaliburIIRecomp", repository="tmcleroy/soulcalibur2-recomp", exe="soulcalibur2-launcher",
               where=None, **entry):
    """One game as Quiver 3.x leaves it: the list in `apps.json`, the apps folder in `settings.json` (with the byte order mark it
    writes), and the markers in the game's folder."""
    data = world.v3
    apps = world.tmp / "Games" / "Quiver"
    root = Path(where) if where else apps / folder
    program = put(root / exe, b"\x7fELF....")
    program.chmod(0o755)
    put(root / "other-tool", b"\x7fELF....")
    (root / "selected_executable.txt").write_text(str(program))
    (root / "version.txt").write_text("v0.3.0-beta.1")
    listed = json.loads((data / "apps.json").read_text()) if (data / "apps.json").exists() else {"apps": []}
    listed["apps"].append(
        {"name": "Soulcalibur II", "folderName": folder, "installPath": str(where) if where else None, "repository": repository,
         "appIconUrl": "https://example.invalid/icon.png", **entry}
    )
    (data / "apps.json").write_text(json.dumps(listed))
    (data / "settings.json").write_text(json.dumps({"AppsPath": str(apps) + "/", "UserAppDisplayNames": {}}), encoding="utf-8-sig")
    return root


@pytest.fixture
def v3world(world):
    world.v3 = world.home / "Applications"
    world.v3.mkdir()
    return world


def test_quiver_3_is_found_beside_its_appimage_by_the_menu_entry_it_made(v3world):
    app = put(v3world.v3 / "QuiverLauncher-linux-x64.AppImage", b"\x7fELF")
    menu = v3world.home / ".local/share/applications"
    put(menu / "appimagekit_abc-QuiverLauncher.desktop", f"[Desktop Entry]\nExec={app}\nTryExec={app}\n".encode())
    v3_install(v3world)

    assert quiver.find() == v3world.v3
    assert quiver.default_dirs()[0] == v3world.v3


def test_quiver_3_is_found_by_the_appimage_in_a_usual_folder_or_in_the_user_data_folder(v3world, tmp_path):
    v3_install(v3world)
    assert quiver.find() is None  # no AppImage in sight and nothing in ~/.local/share: its folder is not guessed from a bare apps.json
    put(v3world.v3 / "QuiverLauncher-linux-x64.AppImage", b"\x7fELF")
    assert quiver.find() == v3world.v3

    other = v3world.home / ".local/share/QuiverLauncher"
    other.mkdir(parents=True)
    (other / "apps.json").write_text(json.dumps({"apps": [{"name": "X", "folderName": "X"}]}))
    assert quiver.find() == v3world.v3  # its own preference: beside the program when it holds the library


def test_an_apps_json_that_is_not_quivers_is_not_taken_for_it(v3world, tmp_path):
    (v3world.v3 / "apps.json").write_text(json.dumps({"apps": [{"title": "other launcher"}]}))
    put(v3world.v3 / "QuiverLauncher.AppImage", b"\x7fELF")
    assert quiver.find() is None
    assert quiver.find(str(v3world.v3)) is None


def test_the_games_of_quiver_3_are_listed_from_its_markers_and_the_folder_it_names(v3world):
    root = v3_install(v3world)

    game = quiver.installed(v3world.v3)[0]

    assert game.id == "tmcleroy/soulcalibur2-recomp" and game.name == "Soulcalibur II"
    assert game.folder == root and game.executable == root / "soulcalibur2-launcher"  # selected_executable.txt, not the other program
    assert game.version == "v0.3.0-beta.1" and game.cover.endswith("icon.png") and game.target == "linux"
    assert game.keys == ["xdg-data/Soulcalibur II/saves"]


def test_a_game_quiver_3_keeps_elsewhere_and_the_name_the_user_gave_it_are_followed(v3world, tmp_path):
    elsewhere = tmp_path / "Drive" / "Hookshot"
    v3_install(v3world, folder="Hookshot", repository="NotRexed/Hookshot", exe="Hookshot.AppImage", where=elsewhere,
               customDisplayName="Ocarina of Time")

    game = quiver.installed(v3world.v3)[0]

    assert game.folder == elsewhere and game.name == "Ocarina of Time" and game.keys == []


def test_a_game_on_a_drive_that_is_not_connected_is_left_out_until_it_is(v3world, tmp_path):
    v3_install(v3world, folder="Hookshot", repository="NotRexed/Hookshot", where=tmp_path / "Drive" / "Hookshot")
    import shutil

    shutil.rmtree(tmp_path / "Drive")
    assert quiver.installed(v3world.v3) == []


def test_a_windows_port_of_quiver_3_gets_a_prefix_of_mogs_own_never_one_in_quivers_folder(v3world):
    root = v3_install(v3world, exe="port.exe")
    (root / "selected_executable.txt").write_text(str(root / "port.exe"))
    game = quiver.installed(v3world.v3)[0]
    assert game.target == "windows" and game.prefix is None
    game.executable = None

    rec = manager.adopt_quiver_game(game, {"id": 52, "name": "Soulcalibur II"})

    assert Path(rec.prefix).is_relative_to(v3world.tmp) and not Path(rec.prefix).is_relative_to(root)
    assert Path(rec.prefix).name == "pfx" and rec.target == "windows"
