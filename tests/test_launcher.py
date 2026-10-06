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
    assert command.startswith(f"WINEPREFIX={tmp_path / 'Game' / 'pfx'} GAMEID=umu-mog-7 ")
    assert command.endswith(f"{umu} {tmp_path / 'Game' / 'game.exe'}")


@pytest.mark.parametrize("engine", ["faugus", "umu", "wine"])
def test_every_engine_is_handed_the_prefix_inside_the_games_folder(monkeypatch, tmp_path, engine):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": engine)
    game = _game(tmp_path)
    pfx = tmp_path / "Game" / "pfx"

    cmd, extra = launcher.launch_command(game)

    assert f"WINEPREFIX={pfx}" in " ".join(cmd) or extra["WINEPREFIX"] == str(pfx)
    assert pfx.is_dir()  # made ready for the engine to fill
    assert launcher.pfx_dir(game) == pfx


def test_umu_gets_a_game_id_and_wine_only_the_prefix(monkeypatch, tmp_path):
    game = _game(tmp_path)
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    assert launcher.launch_command(game) == (
        ["umu-run", game.executable],
        {"WINEPREFIX": str(tmp_path / "Game/pfx"), "GAMEID": "umu-mog-7", "PROTONPATH": "GE-Proton"},
    )
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    assert launcher.launch_command(game) == (["wine", game.executable], {"WINEPREFIX": str(tmp_path / "Game/pfx")})


def test_proton_keeps_its_compat_data_in_the_games_folder(monkeypatch, tmp_path):
    proton = tmp_path / "steamapps" / "common" / "Proton 9" / "proton"
    proton.parent.mkdir(parents=True)
    proton.write_text("")
    monkeypatch.setattr(launcher, "find_proton", lambda: proton)
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "proton")
    game = _game(tmp_path)

    cmd, extra = launcher.launch_command(game)

    assert cmd == [str(proton), "run", game.executable]
    assert extra["STEAM_COMPAT_DATA_PATH"] == str(tmp_path / "Game/pfx")
    assert extra["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(tmp_path)


def test_windows_runs_the_executable_with_no_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "native")
    game = _game(tmp_path)
    assert launcher.launch_command(game) == ([game.executable], {})
    assert not (tmp_path / "Game" / "pfx").exists()


def _present(monkeypatch, faugus=False, umu=False, proton=False, wine=False):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "faugus_command", lambda: ["faugus-run"] if faugus else None)
    monkeypatch.setattr(launcher, "find_proton", lambda: Path("/steam/steamapps/common/P/proton") if proton else None)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if (name == "umu-run" and umu) or (name == "wine" and wine) else None)


def test_launchers_are_found_in_the_order_of_preference(monkeypatch):
    _present(monkeypatch, faugus=True, umu=True, proton=True, wine=True)
    assert launcher.scan_launchers() == ["faugus", "umu", "proton", "wine"]
    _present(monkeypatch, proton=True, wine=True)
    assert launcher.scan_launchers() == ["proton", "wine"]
    _present(monkeypatch)
    assert launcher.scan_launchers() == []
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    assert launcher.scan_launchers() == ["native"]


@pytest.fixture
def scanned(monkeypatch):
    monkeypatch.setattr(launcher, "_known", None)
    yield
    monkeypatch.setattr(launcher, "_known", None)


def test_the_scan_is_saved_and_only_repeated_after_an_update_or_on_request(monkeypatch, scanned):
    from mog_client.config import Settings, load_settings

    settings = Settings()
    _present(monkeypatch, faugus=True, wine=True)
    assert launcher.ensure_scanned(settings) == ["faugus", "wine"]
    assert load_settings().launchers == ["faugus", "wine"] and load_settings().launchers_scanned_for

    _present(monkeypatch, umu=True)  # installed since, but nothing asked for a rescan
    assert launcher.ensure_scanned(settings) == ["faugus", "wine"]
    assert launcher.available_launchers() == ["faugus", "wine"] and launcher.detect_launcher("auto") == "faugus"

    assert launcher.ensure_scanned(settings, rescan=True) == ["umu"]
    assert load_settings().launchers == ["umu"]

    _present(monkeypatch, wine=True)
    settings.launchers_scanned_for = "0.0.0-older"  # MOG was updated
    assert launcher.ensure_scanned(settings) == ["wine"]


def test_an_unavailable_preference_falls_back_to_the_first_launcher(monkeypatch, scanned):
    monkeypatch.setattr(launcher, "_known", ["umu", "wine"])
    assert launcher.detect_launcher("wine") == "wine"
    assert launcher.detect_launcher("faugus") == "umu"
    monkeypatch.setattr(launcher, "_known", [])
    assert launcher.detect_launcher("auto") is None


def test_the_executable_picker_does_not_look_inside_the_prefix(tmp_path):
    game = tmp_path / "Game"
    (game / "pfx/drive_c/windows").mkdir(parents=True)
    (game / "pfx/drive_c/windows/notepad.exe").write_bytes(b"x" * 500)
    (game / "bin").mkdir()
    (game / "bin/game.exe").write_bytes(b"x" * 100)
    (game / "sub/pfx").mkdir(parents=True)
    (game / "sub/pfx/other.exe").write_bytes(b"x")  # only the top-level `pfx` is the prefix

    assert [p.name for p in launcher.list_executables(game)] == ["game.exe", "other.exe"]


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
    monkeypatch.setattr(manager, "fetch_artwork", lambda meta, client=None: {})
    monkeypatch.setattr(manager, "_update", lambda rec, **changes: None)
    seen = {}
    monkeypatch.setattr(manager.steam, "add_shortcut", lambda user, name, exe, start_dir, options, artwork=None: seen.update(exe=exe, dir=start_dir, options=options) or {})
    game = _game(tmp_path)

    manager.finish_setup(game, {}, game.executable, tmp_path / "steamuser", desktop=False)

    script = launcher.launch_script_path(game)
    assert seen["exe"] == str(script) and seen["options"] == ""
    assert seen["dir"] == str(Path(game.executable).parent)
    body = script.read_text()
    assert "exec /usr/bin/flatpak run" in body and "--run" in body
    assert body.index("--save-pre") < body.index("exec ")


def _run_script(monkeypatch, tmp_path, client: Path):
    import subprocess

    monkeypatch.setattr(launcher, "client_command", lambda: str(client))
    monkeypatch.setattr(launcher, "standalone_command", lambda game, pref="auto": ["/bin/echo", "game started"])
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    game = _game(tmp_path)
    script = launcher.write_launch_script(game)
    return game, subprocess.run(["/bin/sh", script], capture_output=True, text=True, timeout=10)


def test_the_launch_script_runs_the_save_hooks_then_execs_the_game(monkeypatch, tmp_path):
    calls = tmp_path / "calls.log"
    client = tmp_path / "mog"
    client.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    client.chmod(0o755)

    game, done = _run_script(monkeypatch, tmp_path, client)

    assert done.returncode == 0 and done.stdout.strip() == "game started"
    deadline = time.time() + 5
    while time.time() < deadline and len(calls.read_text().splitlines() if calls.exists() else []) < 2:
        time.sleep(0.05)
    assert sorted(calls.read_text().splitlines()) == ["--save-pre 7", "--save-watch 7"]
    body = launcher.launch_script_path(game).read_text().splitlines()
    assert body[-1].startswith("exec ") and body[-3].endswith("&") and "--save-pre" in body[-4]


def test_the_launch_script_still_starts_the_game_when_the_client_is_gone(monkeypatch, tmp_path):
    _, done = _run_script(monkeypatch, tmp_path, tmp_path / "no-such-client")
    assert done.returncode == 0 and done.stdout.strip() == "game started" and done.stderr == ""


def test_host_environ_drops_bundle_directories_handed_on_as_the_original_path(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_MOG-Cabc/usr/bin/_internal:/usr/local/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/tmp/.mount_MOG-Cold/usr/bin/_internal:/tmp/.mount_MOG-Cold/usr/lib:/opt/x")
    monkeypatch.setattr(sys, "_MEIPASS", "/somewhere/_internal", raising=False)
    assert launcher.host_environ()["LD_LIBRARY_PATH"] == "/opt/x"
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/somewhere/_internal:/somewhere/_internal/lib")
    assert "LD_LIBRARY_PATH" not in launcher.host_environ()
