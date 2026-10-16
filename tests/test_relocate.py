import shutil
import sys
from pathlib import Path

import pytest

from mog_client import config, installdirs, launcher, manager, steam
from mog_client.config import InstalledGame, Settings
from mog_client.installdirs import find_moved, rebase
from mog_client.saves import state


@pytest.fixture
def moved(monkeypatch, tmp_path):
    """A game installed under `old/`, with its entries, whose folder is then moved to `new/`."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(launcher, "available_launchers", lambda: ["wine"])
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("wine", "env") else None)
    old, new = tmp_path / "old", tmp_path / "new"
    folder = old / "Game"
    exe = folder / "bin" / "game.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    (folder / ".mog-icon").write_bytes(b"\x89PNG")
    rec = InstalledGame(
        game_id=7, name="Game", install_dir=str(folder), executable=str(exe), prefix=str(folder / "pfx"), state="installed"
    )
    rec.launcher = "wine"
    rec.desktop_entry = launcher.create_desktop_entry(rec, folder / ".mog-icon", "wine")
    user = tmp_path / "steam" / "userdata" / "1"
    rec.steam_entries = [steam.add_shortcut(user, "Game", launcher.write_launch_script(rec, "wine"), str(exe.parent), "")]
    config.save_library({7: rec})
    state.save_state(7, state.SaveState(prefix=str(folder / "pfx")))
    new.mkdir()
    shutil.move(str(folder), str(new / "Game"))
    return rec, Settings(install_dirs=[str(new)]), new / "Game", user


def test_a_game_whose_folder_moved_is_found_again_and_its_files_are_written_for_the_new_place(moved):
    rec, settings, folder, user = moved
    assert manager.relocate_moved(settings) == ["Game"]

    found = config.load_library()[7]
    assert found.install_dir == str(folder)
    assert found.executable == str(folder / "bin" / "game.exe")
    assert found.prefix == str(folder / "pfx")
    assert state.load_state(7).prefix == str(folder / "pfx")  # the save sync reads the same prefix

    assert str(folder) in launcher.launch_script_path(found).read_text()
    menu = Path(found.desktop_entry)
    assert menu.resolve() == (folder / "Game.desktop").resolve() and menu.exists()  # the menu link is not dangling
    assert f"Icon={folder / '.mog-icon'}" in (folder / "Game.desktop").read_text()  # the icon, as it was the old path
    assert str(folder) in (folder / ".directory").read_text()
    (entry,) = steam.load_shortcuts(Path(found.steam_entries[0]["shortcuts_path"]))["shortcuts"].values()
    assert str(folder) in entry["StartDir"] and str(folder) in entry["exe"]


def test_a_game_that_is_where_it_was_recorded_is_left_alone(moved):
    rec, settings, folder, _ = moved
    shutil.move(str(folder), rec.install_dir)
    assert manager.relocate_moved(settings) == []
    assert config.load_library()[7].install_dir == rec.install_dir


def test_a_folder_with_the_same_name_but_not_the_game_is_not_taken_for_it(moved):
    rec, settings, folder, _ = moved
    (folder / "bin" / "game.exe").unlink()
    assert find_moved(rec, [folder.parent]) is None
    assert manager.relocate_moved(settings) == []
    assert config.load_library()[7].install_dir == rec.install_dir


def test_a_path_is_rebased_only_when_it_is_under_the_old_folder():
    assert rebase("/a/Game/bin/g.exe", "/a/Game", "/b/Game") == "/b/Game/bin/g.exe"
    assert rebase("/a/Game", "/a/Game", "/b/Game") == "/b/Game"
    assert rebase("/elsewhere/g.exe", "/a/Game", "/b/Game") == "/elsewhere/g.exe"
    assert rebase(None, "/a/Game", "/b/Game") is None


def test_a_folder_is_taken_for_the_game_only_when_it_holds_the_executable_where_it_was(moved):
    rec, _, folder, _ = moved
    assert installdirs.misfit(rec, folder) is None
    assert "game.exe" in installdirs.misfit(rec, folder.parent)  # the folder above the game
    assert "not a folder" in installdirs.misfit(rec, folder / "nope")
    rec.executable = None  # still waiting for its executable: any folder will do
    assert installdirs.misfit(rec, folder.parent) is None


def test_the_user_can_say_where_a_renamed_game_is_and_its_entries_follow(moved, tmp_path):
    rec, _, folder, _ = moved
    renamed = folder.parent / "Game (renamed on the server)"
    folder.rename(renamed)
    assert installdirs.misfit(rec, renamed) is None

    manager.point_to(rec, renamed, "wine")

    found = config.load_library()[7]
    assert found.install_dir == str(renamed) and found.executable == str(renamed / "bin" / "game.exe")
    assert str(renamed) in launcher.launch_script_path(found).read_text()
    assert Path(found.desktop_entry).resolve() == (renamed / "Game.desktop").resolve()
    assert state.load_state(7).prefix == str(renamed / "pfx")


def test_a_game_with_no_record_is_taken_from_a_folder_and_waits_for_its_executable(moved):
    rec, _, folder, _ = moved
    config.save_library({})  # the record was set aside, or the game came from another machine
    assert installdirs.misfit(None, folder) is None
    empty = folder.parent / "empty"
    empty.mkdir()
    assert "empty" in installdirs.misfit(None, empty)
    claimed = manager.claim_folder({"id": 7, "name": "Game", "fs_name": "Game"}, folder, "wine")
    assert claimed.state == "awaiting_executable" and claimed.install_dir == str(folder) and claimed.server_name == "Game"
    assert config.load_library()[7].install_dir == str(folder)


def test_a_renamed_game_takes_the_server_name_and_its_files_get_it_too(moved):
    rec, _, folder, _ = moved
    renamed = folder.parent / "Game 2"
    folder.rename(renamed)
    manager.claim_folder({"id": 7, "name": "Game 2", "fs_name": "Game 2"}, renamed, "wine")

    found = config.load_library()[7]
    assert found.name == "Game 2" and found.server_name == "Game 2"
    assert (renamed / "Game 2.sh").is_file() and (renamed / "Game 2.desktop").is_file()
    assert not (renamed / "Game.sh").exists() and not (renamed / "Game.desktop").exists()  # the old names are not left over
    assert Path(found.desktop_entry).resolve() == (renamed / "Game 2.desktop").resolve()
