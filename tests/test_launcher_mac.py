from pathlib import Path

import pytest

from mog_client import launcher
from mog_client.config import InstalledGame


@pytest.fixture(autouse=True)
def _mac(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "_known", None)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "_MAC_WINE_PATHS", {})


def _install(monkeypatch, tmp_path, engine: str, rel: str = "wine") -> Path:
    """Put a `wine` program for the engine where mac_wine looks for it."""
    program = tmp_path / "apps" / engine / rel
    program.parent.mkdir(parents=True, exist_ok=True)
    program.write_text("")
    program.chmod(0o755)
    monkeypatch.setitem(launcher._MAC_WINE_PATHS, engine, (str(program),))
    return program


def _game(tmp_path: Path, **kwargs) -> InstalledGame:
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    return InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), prefix="", **kwargs)


def test_a_mac_only_looks_for_the_wine_based_runners_in_order(monkeypatch, tmp_path):
    assert launcher.scan_launchers() == []

    _install(monkeypatch, tmp_path, "wine")
    _install(monkeypatch, tmp_path, "crossover")
    _install(monkeypatch, tmp_path, "sikarugir")

    assert launcher.scan_launchers() == ["crossover", "wine", "sikarugir"]


def test_a_program_that_cannot_run_is_not_a_runner(monkeypatch, tmp_path):
    program = _install(monkeypatch, tmp_path, "gptk", "wine64")
    program.chmod(0o644)

    assert launcher.mac_wine("gptk") is None


def test_the_newest_engine_of_a_glob_is_used(monkeypatch, tmp_path):
    for name in ("WS11", "WS12"):
        program = tmp_path / "engines" / name / "bin" / "wine64"
        program.parent.mkdir(parents=True)
        program.write_text("")
        program.chmod(0o755)
    monkeypatch.setitem(launcher._MAC_WINE_PATHS, "sikarugir", (str(tmp_path / "engines/*/bin/wine64"),))

    assert launcher.mac_wine("sikarugir") == tmp_path / "engines/WS12/bin/wine64"


@pytest.mark.parametrize("engine", ["crossover", "gptk", "sikarugir"])
def test_a_mac_engine_runs_the_game_with_its_own_wine_in_the_games_prefix(monkeypatch, tmp_path, engine):
    program = _install(monkeypatch, tmp_path, engine, "wine64")
    monkeypatch.setattr(launcher, "_known", [engine])
    game = _game(tmp_path)

    cmd, env = launcher.launch_command(game)

    assert cmd == [str(program), game.executable]
    assert env["WINEPREFIX"] == str(tmp_path / "Game" / "pfx")


def test_game_porting_toolkit_gets_esync(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, "gptk", "wine64")
    monkeypatch.setattr(launcher, "_known", ["gptk"])

    assert launcher.launch_command(_game(tmp_path))[1]["WINEESYNC"] == "1"


def test_wine_from_an_app_bundle_is_found_when_it_is_not_on_path(monkeypatch, tmp_path):
    program = _install(monkeypatch, tmp_path, "wine", "wine64")
    monkeypatch.setattr(launcher, "_known", ["wine"])

    cmd, env = launcher.launch_command(_game(tmp_path))

    assert cmd[0] == str(program)
    assert env["WINEESYNC"] == "1"


def test_a_runner_removed_since_the_scan_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_known", ["crossover"])

    with pytest.raises(RuntimeError, match="CrossOver is not installed any more"):
        launcher.launch_command(_game(tmp_path))


def test_with_nothing_installed_the_message_names_what_runs_on_a_mac(tmp_path):
    with pytest.raises(RuntimeError, match="CrossOver"):
        launcher.launch_command(_game(tmp_path))


def test_winetricks_is_pointed_at_the_engines_wine(monkeypatch, tmp_path):
    program = _install(monkeypatch, tmp_path, "crossover")
    monkeypatch.setattr(launcher, "_known", ["crossover"])
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/opt/homebrew/bin/winetricks")

    cmd, env = launcher.launch_command(_game(tmp_path), program="winetricks", args=("vcrun2019",))

    assert cmd == ["winetricks", "vcrun2019"]
    assert env["WINE"] == str(program)


def test_the_bundles_library_path_is_not_handed_to_the_runner(monkeypatch):
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/Applications/MOG Client.app/Contents/Frameworks")
    monkeypatch.setenv("DYLD_LIBRARY_PATH_ORIG", "/opt/homebrew/lib")

    assert launcher.host_environ()["DYLD_LIBRARY_PATH"] == "/opt/homebrew/lib"

    monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG")

    assert "DYLD_LIBRARY_PATH" not in launcher.host_environ()
