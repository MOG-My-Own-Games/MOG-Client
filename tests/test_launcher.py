import sys
import time
from pathlib import Path

from mog_client import launcher
from mog_client.config import InstalledGame


def _game(tmp_path: Path) -> InstalledGame:
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    return InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), prefix=str(tmp_path / "pfx"))


def test_flatpak_faugus_runs_the_game_through_the_launchers_run_flag(monkeypatch, tmp_path):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    runner = ["flatpak", "run", "--command=/app/bin/faugus-launcher", launcher.FAUGUS_FLATPAK, "--run"]
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (runner, str(umu), True))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")

    cmd, extra = launcher.launch_command(_game(tmp_path))

    assert cmd[:-1] == runner and extra == {}
    command = cmd[-1]
    assert command.startswith("WINEPREFIX=") and "GAMEID=umu-mog-7" in command
    assert command.endswith(f"{umu} {tmp_path / 'Game' / 'game.exe'}")


def test_native_faugus_2x_has_no_faugus_run_so_launcher_run_is_used(monkeypatch):
    found = {"faugus-launcher": "/usr/bin/faugus-launcher"}
    monkeypatch.setattr(launcher.shutil, "which", lambda name: found.get(name))
    runner, _, flatpak = launcher.faugus_invocation()
    assert runner == ["faugus-launcher", "--run"] and flatpak is False
