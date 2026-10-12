from pathlib import Path

import pytest

from mog_client import launcher, manager
from mog_client.config import InstalledGame


def _builds(monkeypatch, tmp_path, *names):
    """Proton builds in a fake Steam: the first name is in the library, the others in compatibilitytools.d."""
    root = tmp_path / "steam"
    for i, name in enumerate(names):
        folder = root / ("steamapps/common" if i == 0 else "compatibilitytools.d") / name
        folder.mkdir(parents=True)
        (folder / "proton").write_text("")
    monkeypatch.setattr(launcher, "steam_roots", lambda: [root])
    return root


def _game(tmp_path, **extra) -> InstalledGame:
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"x")
    return InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), **extra)


def test_builds_are_listed_oldest_first_reading_numbers_as_numbers(monkeypatch, tmp_path):
    _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton10-1", "GE-Proton9-1", "UMU-Proton-10.0-4")

    assert list(launcher.list_proton_builds()) == ["GE-Proton9-1", "GE-Proton10-1", "Proton 9.0", "UMU-Proton-10.0-4"]


def test_the_default_proton_is_the_last_listed(monkeypatch, tmp_path):
    root = _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-1", "GE-Proton10-1")

    assert launcher.find_proton() == root / "steamapps/common/Proton 9.0/proton"


def test_no_builds_means_no_default(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "steam_roots", lambda: [tmp_path / "nothing"])

    assert launcher.list_proton_builds() == {} and launcher.find_proton() is None


@pytest.mark.parametrize(
    ("engine", "name", "expected"),
    [
        ("proton", None, (None, None)),
        ("umu", "GE-Proton9-1", ("{folder}", None)),  # on this machine: its folder
        ("proton", "GE-Proton9-1", ("{script}", None)),
        ("umu", "GE-Proton8-5", ("GE-Proton8-5", None)),  # not here, but umu fetches Proton-GE by name
    ],
)
def test_a_chosen_build_is_resolved_for_the_engine(monkeypatch, tmp_path, engine, name, expected):
    root = _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-1")
    folder = root / "compatibilitytools.d/GE-Proton9-1"
    want = tuple(v.format(folder=folder, script=folder / "proton") if v else v for v in expected)

    assert launcher.resolve_proton(name, engine) == want


@pytest.mark.parametrize(
    ("engine", "name"),
    [("proton", "GE-Proton8-5"), ("umu", "Proton 7.0"), ("faugus", "Odd"), ("faugus", "GE-Proton8-5")],  # Faugus shows a message for a build it lacks
)
def test_a_build_this_device_cannot_get_falls_back_with_a_reason(monkeypatch, tmp_path, engine, name):
    _builds(monkeypatch, tmp_path, "Proton 9.0")

    value, problem = launcher.resolve_proton(name, engine)

    assert value is None and name in problem


def test_wine_has_no_proton_builds(monkeypatch, tmp_path):
    _builds(monkeypatch, tmp_path, "Proton 9.0")

    value, problem = launcher.resolve_proton("Proton 9.0", "wine")

    assert value is None and "Proton 9.0" in problem


def test_faugus_is_told_the_build_inline_and_umu_through_its_environment(monkeypatch, tmp_path):
    root = _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-1")
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    game = _game(tmp_path, proton="GE-Proton9-1")
    folder = root / "compatibilitytools.d/GE-Proton9-1"

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    cmd, extra = launcher.launch_command(game)
    assert cmd[:2] == ["faugus-launcher", "--run"] and f"PROTONPATH={folder}" in cmd[2] and extra == {}

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    cmd, extra = launcher.launch_command(game)
    assert cmd == ["umu-run", game.executable] and extra["PROTONPATH"] == str(folder)


def test_without_a_choice_faugus_is_left_to_pick_and_umu_uses_the_latest_ge(monkeypatch, tmp_path):
    _builds(monkeypatch, tmp_path, "Proton 9.0")
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    game = _game(tmp_path)

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    assert "PROTONPATH" not in launcher.launch_command(game)[0][2]
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    assert launcher.launch_command(game)[1]["PROTONPATH"] == "GE-Proton"


def test_the_proton_engine_runs_the_chosen_build(monkeypatch, tmp_path):
    root = _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-1")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "proton")
    game = _game(tmp_path, proton="Proton 9.0")

    cmd, _ = launcher.launch_command(game)

    assert cmd[0] == str(root / "steamapps/common/Proton 9.0/proton")


def test_a_program_runs_in_the_prefix_through_the_same_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    game = _game(tmp_path)

    cmd, extra = launcher.launch_command(game, program="winecfg")
    assert cmd == ["wine", "winecfg"] and extra["WINEPREFIX"].endswith("Game/pfx")

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    cmd, _ = launcher.launch_command(game, program="regedit", args=("/S", "/tmp/x.reg"))
    assert cmd == ["umu-run", "regedit", "/S", "/tmp/x.reg"]


def test_a_linux_game_has_no_prefix_to_open_a_program_in(tmp_path):
    exe = tmp_path / "start.sh"
    exe.write_text("#!/bin/sh\n")
    game = InstalledGame(game_id=1, name="N", install_dir=str(tmp_path), executable=str(exe))

    with pytest.raises(RuntimeError, match="no Wine prefix"):
        launcher.launch_command(game, program="winecfg")


def test_a_choice_this_machine_cannot_honour_is_told_with_what_is_used_instead(monkeypatch, tmp_path):
    _builds(monkeypatch, tmp_path, "Proton 9.0")
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    monkeypatch.setattr(launcher, "_known", ["faugus", "wine"])

    missing_build = launcher.selection_problems(_game(tmp_path, proton="GE-Proton8-5"))
    assert missing_build == ["The selected Proton build GE-Proton8-5 was not found, so the default Faugus's own Proton is used"]
    assert launcher.selection_problems(_game(tmp_path, proton="Proton 9.0")) == []  # here
    assert launcher.selection_problems(_game(tmp_path)) == []  # nothing chosen

    gone_engine = launcher.selection_problems(_game(tmp_path, launcher="umu"))
    assert gone_engine == ["The selected engine umu-launcher was not found, so the default Faugus Launcher is used"]

    wine = launcher.selection_problems(_game(tmp_path, launcher="wine", wine="lutris-ge-8-26"))
    assert wine == ["The selected Wine build lutris-ge-8-26 was not found, so the default the system's wine is used"]


def test_a_linux_game_and_other_systems_have_nothing_to_warn_about(monkeypatch, tmp_path):
    exe = tmp_path / "start.sh"
    exe.write_text("#!/bin/sh\n")
    native = InstalledGame(game_id=1, name="N", install_dir=str(tmp_path), executable=str(exe), launcher="umu", proton="X")
    assert launcher.selection_problems(native) == []

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    assert launcher.selection_problems(_game(tmp_path, launcher="umu", proton="X")) == []


def _wine_runner(home, folder, name):
    program = home / folder / name / "bin" / "wine"
    program.parent.mkdir(parents=True)
    program.write_text("")
    return program


def test_wine_builds_come_from_the_runner_folders_of_lutris_and_bottles(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    ge = _wine_runner(tmp_path, ".local/share/lutris/runners/wine", "wine-ge-8-26-x86_64")
    soda = _wine_runner(tmp_path, ".local/share/bottles/runners", "soda-9.0-1")

    assert launcher.wine_runners() == {"soda-9.0-1": soda, "wine-ge-8-26-x86_64": ge}


def test_wine_runs_the_chosen_build_and_else_the_system_wine(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    program = _wine_runner(tmp_path, ".local/share/lutris/runners/wine", "wine-ge-8-26")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")

    assert launcher.launch_command(_game(tmp_path, wine="wine-ge-8-26"))[0][0] == str(program)
    assert launcher.launch_command(_game(tmp_path, wine="gone"))[0][0] == "wine"
    assert launcher.launch_command(_game(tmp_path))[0][0] == "wine"


def test_each_engine_lists_what_it_can_run(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-27")
    _wine_runner(tmp_path, ".local/share/lutris/runners/wine", "wine-ge-8-26")
    monkeypatch.setattr(launcher, "system_wine_version", lambda: "wine-9.0")
    releases = ["GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-4"]

    for engine in ("faugus", "umu", "proton"):
        versions = launcher.engine_versions(engine, releases)
        assert [(v.key, v.here) for v in versions] == [
            ("", True), ("Proton 9.0", True), ("GE-Proton9-27", True), ("GE-Proton10-4", False), ("GE-Proton8-32", False),
        ]
    assert launcher.engine_versions("faugus", releases)[0].label == "Default (Faugus picks its own)"
    assert launcher.engine_versions("umu", releases)[0].label == "Default (the latest Proton-GE)"

    wine = launcher.engine_versions("wine", releases)
    assert [(v.key, v.label) for v in wine] == [("", "Default (the system's wine, wine-9.0)"), ("wine-ge-8-26", "wine-ge-8-26")]
    assert launcher.engine_versions("native", releases) == [] and launcher.engine_versions(None) == []


def test_choosing_a_build_rewrites_the_launch_script_the_entries_run(monkeypatch, tmp_path):
    root = _builds(monkeypatch, tmp_path, "Proton 9.0", "GE-Proton9-1")
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    monkeypatch.setattr(manager.sys, "platform", "linux")
    game = _game(tmp_path)

    manager.set_proton(game, "GE-Proton9-1")

    script = Path(launcher.launch_script_path(game)).read_text()
    assert f"PROTONPATH={root / 'compatibilitytools.d/GE-Proton9-1'}" in script
    assert manager.load_library()[7].proton == "GE-Proton9-1"
