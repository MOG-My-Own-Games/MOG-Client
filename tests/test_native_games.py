import os
import shlex
import stat
from pathlib import Path

import pytest

from mog_client import launcher
from mog_client.config import InstalledGame, Settings, is_native_executable
from mog_client.saves import sync


def make(root: Path, *files: str) -> None:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (len(name) + 10))


def names(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in launcher.list_executables(root)]


GOG_LINUX = (
    "start.sh",
    "uninstall-Lost Ruins.sh",
    "gameinfo",
    "game/LostRuins.x86_64",
    "game/LostRuins_Data/data.bin",
    "support/gog-system-report.sh",
    "support/postinst.sh",
    "support/preuninst.sh",
    "support/gog_com.shlib",
    "support/yad/yad.sh",
    "support/xdg-utils/xdg-open",
    ".mojosetup/mojosetup",
    ".mojosetup/scripts/helper.sh",
    "docs/readme.txt",
)


def test_a_gog_linux_game_offers_its_start_script_and_nothing_else(tmp_path):
    make(tmp_path, *GOG_LINUX)
    assert names(tmp_path) == ["start.sh"]


@pytest.mark.parametrize(
    "script",
    ["gog-system-report.sh", "postinst.sh", "preuninst.sh", "reuninst.sh", "yad.sh", "uninstall-Some Game.sh", "UNINSTALL-x.SH", "Yad.sh"],
)
def test_the_installers_helper_scripts_are_never_offered(tmp_path, script):
    make(tmp_path, "start.sh", script, f"bin/{script}")
    assert names(tmp_path) == ["start.sh"]


def test_other_scripts_are_offered_shallowest_first(tmp_path):
    make(tmp_path, "tools/run_editor.sh", "launch.sh", "tools/deep/x.sh")
    assert names(tmp_path) == ["launch.sh", "tools/run_editor.sh", "tools/deep/x.sh"]


def test_on_linux_the_scripts_come_before_the_windows_executables_and_the_start_script_first(tmp_path):
    make(tmp_path, "game.exe", "Big Game.exe", "setup_extra.exe", "run.sh", "start.sh")
    (tmp_path / "Big Game.exe").write_bytes(b"x" * 9000)
    assert names(tmp_path) == ["start.sh", "run.sh", "Big Game.exe", "game.exe"]


def test_a_script_below_the_top_still_comes_before_the_executables(tmp_path):
    make(tmp_path, "Game_1.0/Game.sh", "Game_1.0/Game.exe", "Game_1.0/lib/windows-i686/python.exe")
    found = names(tmp_path)
    assert found[0] == "Game_1.0/Game.sh" and set(found[1:]) == {"Game_1.0/Game.exe", "Game_1.0/lib/windows-i686/python.exe"}


def test_a_windows_game_is_listed_as_before(tmp_path):
    make(tmp_path, "Game.exe", "unins000.exe", "redist/vcredist_x64.exe", "pfx/drive_c/x.exe")
    assert names(tmp_path) == ["Game.exe"]


def test_scripts_are_not_offered_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    make(tmp_path, "start.sh", "Game.exe")
    assert names(tmp_path) == ["Game.exe"]


def test_which_executables_run_as_they_are():
    assert is_native_executable("/g/start.sh") and is_native_executable("/g/RUN.SH")
    assert not is_native_executable("/g/Game.exe") and not is_native_executable(None) and not is_native_executable("")
    assert InstalledGame(1, "G", "/g", executable="/g/start.sh").native
    assert not InstalledGame(1, "G", "/g", executable="/g/Game.exe").native
    assert not InstalledGame(1, "G", "/g").native


def native_game(tmp_path, mode=0o644):
    folder = tmp_path / "Lost Ruins"
    make(folder, "start.sh")
    (folder / "start.sh").chmod(mode)
    return InstalledGame(17, "Lost Ruins", str(folder), state="installed", executable=str(folder / "start.sh"))


def test_a_native_game_needs_no_launcher_and_runs_its_script_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_known", [])  # no Faugus, umu, Proton or Wine at all
    game = native_game(tmp_path)
    command, env = launcher.launch_command(game)
    assert command == [game.executable] and env == {}
    assert not launcher.pfx_dir(game).exists()  # and no Wine prefix was made for it


def test_its_script_is_made_executable_for_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_known", [])
    game = native_game(tmp_path, mode=0o644)
    launcher.launch_command(game)
    assert os.stat(game.executable).st_mode & stat.S_IXUSR


def test_a_game_set_to_another_engine_still_runs_a_script_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_known", ["wine"])
    game = native_game(tmp_path)
    game.launcher = "wine"
    assert launcher.launch_command(game, "wine")[0] == [game.executable]


def test_the_launch_script_of_a_native_game_just_runs_it(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_known", [])
    monkeypatch.setattr(launcher, "client_command", lambda: "/opt/mog")
    game = native_game(tmp_path)
    path = Path(launcher.write_launch_script(game))
    text = path.read_text()
    assert f"exec {shlex.quote(game.executable)}" in text
    assert "wine" not in text.lower() and "proton" not in text.lower() and "umu" not in text.lower()
    assert not launcher.pfx_dir(game).exists()


def test_save_sync_follows_the_settings_for_a_native_game_too(tmp_path):
    settings = Settings(sync_saves=True)
    assert sync.enabled(InstalledGame(1, "G", "/g", executable="/g/Game.exe"), settings) is True
    assert sync.enabled(native_game(tmp_path), settings) is True
    off = native_game(tmp_path)
    off.save_sync = False
    assert sync.enabled(off, settings) is False


def _unmarked(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o644)  # what a download leaves
    return path


def test_the_programs_a_download_left_unmarked_get_their_executable_bit_back(tmp_path):
    folder = tmp_path / "Game"
    script = _unmarked(folder / "Game.sh", b"#!/bin/sh\nexec lib/linux-x86_64/Game\n")
    binary = _unmarked(folder / "lib/linux-x86_64/Game", b"\x7fELF" + b"\0" * 60)
    helper = _unmarked(folder / "lib/tools/run.py", b"#!/usr/bin/env python3\n")
    library = _unmarked(folder / "lib/linux-x86_64/libfoo.so.1", b"\x7fELF" + b"\0" * 60)
    data = _unmarked(folder / "game/script.rpy", b"label start:\n")
    pfx = _unmarked(folder / "pfx/drive_c/x.exe", b"\x7fELF")
    already = _unmarked(folder / "ok", b"\x7fELF")
    already.chmod(0o755)

    assert launcher.fix_native_permissions(folder) == 3
    assert all(p.stat().st_mode & 0o111 for p in (script, binary, helper))
    assert not library.stat().st_mode & 0o111 and not data.stat().st_mode & 0o111 and not pfx.stat().st_mode & 0o111


def test_a_native_game_is_given_its_permissions_once_when_it_is_launched(tmp_path):
    game = native_game(tmp_path, mode=0o644)
    folder = Path(game.install_dir)
    binary = _unmarked(folder / "lib/Lost Ruins", b"\x7fELF" + b"\0" * 8)

    command, extra = launcher.launch_command(game)

    assert command == [game.executable] and binary.stat().st_mode & 0o111
    assert (folder / launcher.PERMISSIONS_MARK).exists()
    binary.chmod(0o644)
    launcher.launch_command(game)  # the folder is not walked again
    assert not binary.stat().st_mode & 0o111
