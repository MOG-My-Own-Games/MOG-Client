"""Games found in an install folder with no record here are taken into the library, and a folder picked for Add local game
that was a MOG game is taken back as one."""

import sys
from pathlib import Path

import pytest

from mog_client import config, installdirs, launcher, manager
from mog_client.config import InstalledGame, Settings


@pytest.fixture
def roots(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(launcher, "available_launchers", lambda: ["wine"])
    root = tmp_path / "Games"
    root.mkdir()
    return root, Settings(install_dirs=[str(root)])


def game(gid=7, name="Alpha Game", fs_name=None):
    return {"id": gid, "name": name, "fs_name": fs_name or name}


def folder(root: Path, name: str, files=("data/game.bin",)) -> Path:
    for f in files:
        (root / name / f).parent.mkdir(parents=True, exist_ok=True)
        (root / name / f).write_bytes(b"x")
    return root / name


def test_a_folder_named_like_the_game_is_taken_and_waits_for_its_executable(roots):
    root, settings = roots
    where = folder(root, "Alpha Game")

    assert manager.adopt_found_folders([game()], settings) == ["Alpha Game"]

    rec = config.load_library()[7]
    assert rec.state == "awaiting_executable" and rec.install_dir == str(where) and not rec.imported and rec.server_name == "Alpha Game"


def test_the_servers_folder_name_and_the_name_a_new_install_gets_both_count(roots):
    root, settings = roots
    folder(root, "Alpha_ Game")
    folder(root, "beta-folder")

    found = manager.adopt_found_folders([game(7, "Alpha: Game"), game(8, "Beta", "beta-folder")], settings)

    assert sorted(found) == ["Alpha: Game", "Beta"]


@pytest.mark.parametrize("name", ["Alpha Gam", "alpha game", "Alpha Game (GOG)", "Other"])
def test_a_name_that_is_only_alike_is_not_taken(roots, name):
    root, settings = roots
    folder(root, name)
    assert manager.adopt_found_folders([game()], settings) == [] and 7 not in config.load_library()


def test_a_folder_with_only_the_kept_prefix_and_mogs_files_is_not_a_game(roots):
    root, settings = roots
    folder(root, "Alpha Game", ["pfx/drive_c/users/x/save.dat", ".mog-icon", "Alpha Game.sh", "Alpha Game.desktop", ".directory"])
    assert manager.adopt_found_folders([game()], settings) == []


def test_a_folder_already_a_games_or_ignored_or_wanted_by_two_games_is_left_alone(roots):
    root, settings = roots
    mine = folder(root, "Alpha Game")
    config.save_library({3: InstalledGame(game_id=3, name="Other", install_dir=str(mine), state="installed")})
    assert manager.adopt_found_folders([game()], settings) == []

    config.save_library({})
    installdirs.ignore_folder(mine)
    assert manager.adopt_found_folders([game()], settings) == []

    (config.data_dir() / "ignored-folders.json").unlink()
    assert manager.adopt_found_folders([game(7), game(9, "Alpha: Game", "Alpha Game")], settings) == []  # both are "Alpha Game"
    assert config.load_library() == {}


def test_a_game_that_has_its_folder_or_is_being_installed_or_was_added_from_outside_is_not_touched(roots):
    root, settings = roots
    here = folder(root, "Alpha Game")
    config.save_library({7: InstalledGame(game_id=7, name="Alpha Game", install_dir=str(here), state="installed")})
    assert manager.adopt_found_folders([game()], settings) == []

    gone = root.parent / "elsewhere" / "Beta"
    config.save_library({8: InstalledGame(game_id=8, name="Beta", install_dir=str(gone), state="installed", imported=True)})
    folder(root, "Beta")
    assert manager.adopt_found_folders([game(8, "Beta")], settings) == []

    folder(root, "Gamma")
    assert manager.adopt_found_folders([game(9, "Gamma")], settings, busy={9}) == []


def test_a_game_whose_folder_was_renamed_on_the_server_follows_it(roots):
    root, settings = roots
    old = root / "Old Name"
    exe = old / "bin" / "g.bin"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    rec = InstalledGame(game_id=7, name="Old Name", install_dir=str(old), executable=str(exe), state="installed", server_name="Old Name")
    config.save_library({7: rec})
    exe.rename(exe.parent / "moved")  # the folder is renamed with its content below
    (exe.parent / "moved").rename(exe)
    old.rename(root / "New Name")

    assert manager.adopt_found_folders([game(7, "New Name")], settings) == ["New Name"]

    assert config.load_library()[7].install_dir == str(root / "New Name")
    assert config.load_library()[7].executable == str(root / "New Name" / "bin" / "g.bin")


def test_a_root_that_is_not_there_is_passed_over(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    settings = Settings(install_dirs=[str(tmp_path / "unplugged" / "Games")])
    assert manager.adopt_found_folders([game()], settings) == []


def test_a_folder_picked_for_add_local_game_is_a_mog_game_when_it_is_in_an_install_folder_or_has_the_icon(roots, tmp_path):
    root, settings = roots
    inside = folder(root, "Alpha Game")
    assert manager.found_game(inside, [game()], settings.install_roots) == game()

    outside = folder(tmp_path / "elsewhere", "Alpha Game")
    assert manager.found_game(outside, [game()], settings.install_roots) is None  # a game from outside MOG
    (outside / ".mog-icon").write_bytes(b"png")
    assert manager.found_game(outside, [game()], settings.install_roots) == game()
    assert manager.found_game(inside, [game(7, "Beta")], settings.install_roots) is None  # not its name


def test_a_game_with_a_folder_here_is_not_taken_back_and_removing_an_import_stops_it_being_taken(roots):
    root, settings = roots
    inside = folder(root, "Alpha Game")
    rec = InstalledGame(game_id=7, name="Alpha Game", install_dir=str(inside), state="installed", imported=True, target="linux")
    config.save_library({7: rec})
    assert manager.found_game(inside, [game()], settings.install_roots) is None

    config.save_settings(settings)
    manager.remove_import(rec)

    assert inside in installdirs.ignored_folders() and manager.adopt_found_folders([game()], settings) == []
