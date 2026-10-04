import sys
import time
from pathlib import Path

import pytest

from mog_client import launcher
from mog_client.config import InstalledGame


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")


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


def test_host_environ_drops_what_a_bundled_build_added(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/tmp/_MEI123/plugins")
    env = launcher.host_environ()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env and "QT_PLUGIN_PATH" not in env


def test_host_environ_removes_the_bundle_library_path_when_there_was_none_before(monkeypatch):
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in launcher.host_environ()


def test_a_launcher_that_dies_is_reported_with_the_end_of_its_log(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(
        launcher,
        "launch_command",
        lambda game, pref="auto": ([sys.executable, "-c", "import sys; print('no such runtime'); sys.exit(3)"], {}),
    )
    proc = launcher.launch(_game(tmp_path))
    proc.wait(timeout=10)
    time.sleep(0.1)
    message = launcher.launch_failure(proc)
    assert "code 3" in message and "no such runtime" in message and "launch-7.log" in message


def test_a_running_or_clean_launcher_is_not_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "launch_command", lambda game, pref="auto": ([sys.executable, "-c", "pass"], {}))
    proc = launcher.launch(_game(tmp_path))
    proc.wait(timeout=10)
    assert launcher.launch_failure(proc) is None


def test_standalone_command_carries_its_own_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    argv = launcher.standalone_command(_game(tmp_path))
    assert argv[0].endswith("env") and argv[1].startswith("WINEPREFIX=")
    assert argv[-2].endswith("wine") and argv[-1].endswith("game.exe")


def test_standalone_faugus_command_does_not_need_umu_to_exist_yet(monkeypatch, tmp_path):
    runner = ["flatpak", "run", "--command=/app/bin/faugus-launcher", launcher.FAUGUS_FLATPAK, "--run"]
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (runner, str(tmp_path / "missing-umu"), True))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    argv = launcher.standalone_command(_game(tmp_path))
    assert argv[:5] == runner[:5] and "missing-umu" in argv[-1]


def test_desktop_exec_quotes_what_needs_it():
    assert launcher.desktop_exec(["flatpak", "run", "a b", "100%", 'x"y']) == 'flatpak run "a b" 100%% "x\\"y"'


def test_desktop_entry_runs_the_game_without_mog(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    game = _game(tmp_path)
    path = Path(launcher.create_desktop_entry(game))
    text = path.read_text()
    assert "--launch" not in text and "mog-client" not in text.lower()
    assert f"Path={Path(game.executable).parent}" in text
    script = launcher.launch_script_path(game)
    assert str(script) in text and "game.exe" in script.read_text()


def test_steam_shortcut_runs_the_launch_script_which_execs_the_launcher(monkeypatch, tmp_path):
    from mog_client import manager

    runner = ["flatpak", "run", "--command=/app/bin/faugus-launcher", launcher.FAUGUS_FLATPAK, "--run"]
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (runner, str(tmp_path / "umu-run"), True))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "flatpak" else None)
    monkeypatch.setattr(manager, "fetch_artwork", lambda meta: {})
    monkeypatch.setattr(manager, "_update", lambda rec, **changes: None)
    seen = {}
    monkeypatch.setattr(manager.steam, "add_shortcut", lambda user, name, exe, start_dir, options, artwork=None: seen.update(exe=exe, dir=start_dir, options=options) or {})
    game = _game(tmp_path)

    manager.finish_setup(game, {}, game.executable, tmp_path / "steamuser", desktop=False)

    script = launcher.launch_script_path(game)
    assert seen["exe"] == str(script) and seen["options"] == ""
    assert seen["dir"] == str(Path(game.executable).parent)
    body = script.read_text()
    assert "exec /usr/bin/flatpak run" in body and "--run" in body and "mog-client" not in body.lower()
