import json
from pathlib import Path

import pytest

from mog_client.config import InstalledGame
from mog_client.saves import prefix as prefixes
from mog_client.saves.prefix import FROM_ENGINE, FROM_FAUGUS, FROM_STATE, FROM_STEAM, FROM_USER, resolve_prefix


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()


def _prefix(path: Path, layout: str = "pfx") -> Path:
    (path / layout / "drive_c" if layout else path / "drive_c").mkdir(parents=True)
    return path


def _game(**kw) -> InstalledGame:
    defaults = dict(game_id=7, name="G", install_dir="/g", executable="/g/Game/game.exe")
    return InstalledGame(**{**defaults, **kw})


def test_the_three_layouts_of_a_prefix(tmp_path):
    for layout, expected in (("pfx", "pfx/drive_c"), ("", "drive_c"), ("pfx-data/pfx", "pfx-data/pfx/drive_c")):
        root = _prefix(tmp_path / (layout.replace("/", "_") or "plain"), layout)
        assert prefixes.drive_c_of(root) == root / expected


def test_a_prefix_the_user_chose_wins_over_everything(tmp_path):
    found = resolve_prefix(_game(prefix="/mine"), "wine", remembered="/old")
    assert (found.prefix, found.source) == (Path("/mine"), FROM_USER)


def test_a_remembered_prefix_is_used_when_no_launcher_knows_better(tmp_path, monkeypatch):
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    found = resolve_prefix(_game(), "wine", remembered=str(tmp_path / "old"))
    assert (found.prefix, found.source) == (tmp_path / "old", FROM_STATE)
    found = resolve_prefix(_game(), "wine", remembered=str(tmp_path / "old"), remembered_source="runtime")
    assert found.source == "runtime"


def test_a_prefix_picked_for_the_sync_wins_over_the_launchers_but_not_over_the_games_own(tmp_path, monkeypatch):
    games = tmp_path / "games.json"
    games.write_text(json.dumps([{"path": "/g/Game/game.exe", "prefix": "/faugus"}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    picked = resolve_prefix(_game(), "faugus", remembered="/picked", remembered_source=FROM_USER)
    assert (picked.prefix, picked.source) == (Path("/picked"), FROM_USER)
    assert resolve_prefix(_game(prefix="/own"), "faugus", "/picked", FROM_USER).prefix == Path("/own")


def test_what_faugus_records_now_beats_what_was_remembered(tmp_path, monkeypatch):
    games = tmp_path / "games.json"
    games.write_text(json.dumps([{"path": "/g/Game/game.exe", "prefix": "/new"}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    assert resolve_prefix(_game(), "faugus", remembered="/old").prefix == Path("/new")


def test_the_faugus_game_with_this_executable_gives_its_prefix(tmp_path, monkeypatch):
    games = tmp_path / "games.json"
    games.write_text(
        json.dumps(
            [
                {"gameid": "other", "path": "/other.exe", "prefix": "/home/x/Faugus/other"},
                {"gameid": "g", "path": "/g/Game/game.exe", "prefix": "/home/x/Faugus/g"},
            ]
        )
    )
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [tmp_path / "missing.json", games])
    found = resolve_prefix(_game(), "faugus")
    assert (found.prefix, found.source) == (Path("/home/x/Faugus/g"), FROM_FAUGUS)


def test_a_broken_faugus_file_is_ignored(tmp_path, monkeypatch):
    games = tmp_path / "games.json"
    games.write_text("{not json")
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [games])
    assert resolve_prefix(_game(), "faugus") is None


def test_steams_compatdata_for_the_shortcut_is_found_once_it_exists(tmp_path, monkeypatch):
    steam = tmp_path / "steam"
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [steam])
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    game = _game(steam_entries=[{"appid": 3081844425}])
    assert resolve_prefix(game, "faugus") is None
    _prefix(steam / "steamapps/compatdata/3081844425")
    found = resolve_prefix(game, "faugus")
    assert (found.prefix, found.source) == (steam / "steamapps/compatdata/3081844425", FROM_STEAM)


def test_wine_and_umu_have_defaults_and_proton_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    home = tmp_path / "home"
    assert resolve_prefix(_game(), "wine") is None  # ~/.wine does not exist yet
    _prefix(home / ".wine", "")
    found = resolve_prefix(_game(), "wine")
    assert (found.prefix, found.source) == (home / ".wine", FROM_ENGINE)
    _prefix(home / "Games/umu/umu-default")
    assert resolve_prefix(_game(), "umu").prefix == home / "Games/umu/umu-default"
    assert resolve_prefix(_game(), "proton") is None


def test_candidates_are_the_prefixes_that_exist(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [home / "steam"])
    _prefix(home / ".wine", "")
    _prefix(home / "Faugus/a")
    (home / "Faugus/not-a-prefix").mkdir()
    _prefix(home / "steam/steamapps/compatdata/123")
    assert prefixes.candidate_prefixes() == [
        home / ".wine",
        home / "Faugus/a",
        home / "steam/steamapps/compatdata/123",
    ]


def test_the_prefix_in_the_games_folder_is_found_first_and_is_the_games_alone():
    from mog_client.saves.sync import DEDICATED_SOURCES

    game = _game()
    game.prefix = str(Path(game.install_dir) / "pfx")
    found = resolve_prefix(game, "faugus", remembered="/old")
    assert (found.prefix, found.source) == (Path(game.prefix), prefixes.FROM_GAME)
    assert prefixes.FROM_GAME in DEDICATED_SOURCES

    elsewhere = _game(prefix="/somewhere-else")
    assert resolve_prefix(elsewhere, "faugus").source == FROM_USER


def test_both_layouts_an_engine_may_leave_in_the_prefix_folder_are_understood(tmp_path):
    wine_style = _prefix(tmp_path / "wine", "")  # WINEPREFIX=pfx: drive_c right inside
    proton_style = _prefix(tmp_path / "proton", "pfx")  # STEAM_COMPAT_DATA_PATH=pfx: pfx/drive_c inside
    assert prefixes.drive_c_of(wine_style) == wine_style / "drive_c"
    assert prefixes.drive_c_of(proton_style) == proton_style / "pfx/drive_c"


def _installed(tmp_path, **kw) -> InstalledGame:
    folder = tmp_path / "Games" / "G"
    folder.mkdir(parents=True)
    return InstalledGame(game_id=7, name="G", install_dir=str(folder), executable=str(folder / "game.exe"), **kw)


@pytest.fixture
def no_launchers(monkeypatch):
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])


@pytest.mark.parametrize("layout", ["", "pfx"])
def test_the_pfx_in_the_games_folder_is_used_even_when_the_record_does_not_name_it(tmp_path, no_launchers, layout):
    game = _installed(tmp_path)  # prefix None: an install from before the record kept it
    _prefix(Path(game.install_dir) / "pfx", layout)  # Wine's layout, and Proton's
    found = resolve_prefix(game, "faugus")
    assert (found.prefix, found.source) == (Path(game.install_dir) / "pfx", prefixes.FROM_GAME)


def test_a_pfx_that_exists_but_has_not_been_run_in_yet_means_no_question(tmp_path, no_launchers):
    game = _installed(tmp_path)
    (Path(game.install_dir) / "pfx").mkdir()
    found = resolve_prefix(game, "faugus")
    assert found is not None and found.source == prefixes.FROM_GAME and prefixes.drive_c_of(found.prefix) is None


def test_with_no_pfx_in_the_folder_and_nothing_else_the_user_is_still_asked(tmp_path, no_launchers):
    assert resolve_prefix(_installed(tmp_path), "faugus") is None


def test_a_pfx_that_was_run_in_beats_what_faugus_and_steam_record(tmp_path, monkeypatch):
    game = _installed(tmp_path)
    _prefix(Path(game.install_dir) / "pfx", "pfx")
    faugus = tmp_path / "faugus_games.json"
    faugus.write_text(json.dumps([{"path": game.executable, "prefix": str(tmp_path / "faugus-prefix")}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [faugus])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    assert resolve_prefix(game, "faugus").source == prefixes.FROM_GAME


def test_a_pfx_that_was_never_run_in_gives_way_to_the_prefix_faugus_records(tmp_path, monkeypatch):
    game = _installed(tmp_path)
    (Path(game.install_dir) / "pfx").mkdir()  # empty
    faugus = tmp_path / "faugus_games.json"
    faugus.write_text(json.dumps([{"path": game.executable, "prefix": str(tmp_path / "faugus-prefix")}]))
    monkeypatch.setattr(prefixes, "faugus_games_files", lambda: [faugus])
    monkeypatch.setattr(prefixes, "steam_roots", lambda: [])
    found = resolve_prefix(game, "faugus")
    assert (found.prefix, found.source) == (tmp_path / "faugus-prefix", FROM_FAUGUS)


def test_a_prefix_the_user_picked_for_the_sync_still_wins_over_the_games_pfx(tmp_path, no_launchers):
    game = _installed(tmp_path)
    _prefix(Path(game.install_dir) / "pfx", "pfx")
    found = resolve_prefix(game, "faugus", remembered="/picked", remembered_source=FROM_USER)
    assert (found.prefix, found.source) == (Path("/picked"), FROM_USER)


def test_a_native_game_has_no_prefix_to_look_for(tmp_path, no_launchers):
    game = _installed(tmp_path)
    game.executable = str(Path(game.install_dir) / "start.sh")
    (Path(game.install_dir) / "pfx").mkdir()  # a leftover
    assert resolve_prefix(game, "faugus") is None
