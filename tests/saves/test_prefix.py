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
