import pytest

from mog_client import launcher, winetricks
from mog_client.config import InstalledGame


def test_the_catalog_holds_only_real_verbs_once_each():
    verbs = [v for v, _text in winetricks.CATALOG]

    assert all(winetricks.is_verb(v) for v in verbs) and len(verbs) == len(set(verbs))
    assert "vcrun2019" in verbs and "dotnet48" in verbs


@pytest.mark.parametrize("value", ["vcrun2019", "d3dx11_43", "dotnet48", "a", "xact_x64"])
def test_verbs(value):
    assert winetricks.is_verb(value)


@pytest.mark.parametrize("value", ["", "-q", "--force", "Vcrun", "a b", "a;b", "a/b", "$(x)", "x" * 41, None, 3])
def test_what_is_not_a_verb(value):
    assert not winetricks.is_verb(value)


def test_clean_keeps_verbs_once_each_in_order_and_what_is_not_a_list_is_empty():
    assert winetricks.clean(["b", "a", "b", "-q", "x y"]) == ["b", "a"]
    assert winetricks.clean(None) == [] and winetricks.clean("vcrun2019") == []
    assert len(winetricks.clean([f"v{n}" for n in range(500)])) == winetricks.MAX_VERBS


def test_missing_is_what_is_wanted_and_not_applied_yet():
    assert winetricks.missing(["a", "b", "c", "a"], ["b"]) == ["a", "c"]
    assert winetricks.missing([], ["b"]) == []


def _game(tmp_path, **extra):
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"x")
    return InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), **extra)


def test_umu_and_faugus_run_winetricks_as_the_program_like_any_other(monkeypatch, tmp_path):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    game = _game(tmp_path)

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    cmd, extra = launcher.launch_command(game, program="winetricks", args=("-q", "vcrun2019"))
    assert cmd == ["umu-run", "winetricks", "-q", "vcrun2019"] and extra["WINEPREFIX"].endswith("Game/pfx")

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    cmd, _ = launcher.launch_command(game, program="winetricks", args=("-q", "vcrun2019"))
    assert cmd[:2] == ["faugus-launcher", "--run"] and cmd[2].endswith(f"{umu} winetricks -q vcrun2019")


def test_wine_runs_winetricks_on_the_prefix_with_the_chosen_build(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/winetricks")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    program = tmp_path / "bin" / "wine"
    monkeypatch.setattr(launcher, "resolve_wine", lambda name: (program, None) if name else (None, None))

    cmd, extra = launcher.launch_command(_game(tmp_path, wine="ge"), program="winetricks", args=("-q", "d3dx9"))
    assert cmd == ["winetricks", "-q", "d3dx9"] and extra == {"WINEPREFIX": str(tmp_path / "Game/pfx"), "WINE": str(program)}

    assert "WINE" not in launcher.launch_command(_game(tmp_path), program="winetricks")[1]


def test_proton_runs_winetricks_with_its_own_wine_on_the_prefix_it_keeps_one_level_down(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/winetricks")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "proton")
    build = tmp_path / "steam" / "compatibilitytools.d" / "GE-Proton9-27"
    (build / "files/bin").mkdir(parents=True)
    (build / "proton").write_text("")
    (build / "files/bin/wine").write_text("")
    monkeypatch.setattr(launcher, "steam_roots", lambda: [tmp_path / "steam"])
    (tmp_path / "Game" / "pfx" / "pfx").mkdir(parents=True)

    cmd, extra = launcher.launch_command(_game(tmp_path, proton="GE-Proton9-27"), program="winetricks", args=("-q", "vcrun2019"))

    assert cmd == ["winetricks", "-q", "vcrun2019"]
    assert extra == {"WINEPREFIX": str(tmp_path / "Game/pfx/pfx"), "WINE": str(build / "files/bin/wine")}


def test_a_missing_winetricks_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")

    with pytest.raises(RuntimeError, match="winetricks is not installed"):
        launcher.launch_command(_game(tmp_path), program="winetricks")
