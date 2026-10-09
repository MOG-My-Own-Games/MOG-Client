"""Where a game's own files end up inside its folder: in a folder of their own, not among MOG's."""

from types import SimpleNamespace

import pytest

from mog_client import config, manager
from mog_client.config import Settings, load_library
from mog_client.saves.state import load_install_manifest


def entry(path, size=1):
    return {"path": path, "size_bytes": size, "sha1": "ab" * 20}


def test_files_at_the_top_or_in_several_folders_are_loose_one_folder_is_not():
    assert manager.is_loose([entry("Game.exe"), entry("data/a.pak")])
    assert manager.is_loose([entry("Engine/a.dll"), entry("PokemonEmerald/b.pak")])
    assert not manager.is_loose([entry("Mad Island/Mad Island.exe"), entry("Mad Island/data/a.pak")])
    assert not manager.is_loose([])


def test_loose_files_are_moved_into_a_folder_named_for_the_game(tmp_path):
    (tmp_path / "Engine").mkdir()
    (tmp_path / "Engine" / "a.dll").write_bytes(b"x")
    (tmp_path / "Game.exe").write_bytes(b"x")
    files = [entry("Engine/a.dll"), entry("Game.exe")]

    folder, moved = manager.tuck_into_folder(tmp_path, files, "Pokemon Gamma Emerald")

    assert folder == tmp_path / "Pokemon Gamma Emerald"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Pokemon Gamma Emerald"]
    assert (folder / "Engine" / "a.dll").is_file() and (folder / "Game.exe").is_file()
    assert [e["path"] for e in moved] == ["Pokemon Gamma Emerald/Engine/a.dll", "Pokemon Gamma Emerald/Game.exe"]


def test_a_folder_among_the_files_with_the_games_own_name_is_not_overwritten(tmp_path):
    (tmp_path / "Game").mkdir()
    (tmp_path / "Game" / "a.dat").write_bytes(b"x")
    (tmp_path / "Game.exe").write_bytes(b"x")

    folder, moved = manager.tuck_into_folder(tmp_path, [entry("Game/a.dat"), entry("Game.exe")], "Game")

    assert folder.name == "Game (game)" and (folder / "Game" / "a.dat").is_file()
    assert moved[0]["path"] == "Game (game)/Game/a.dat"


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")

    def run(files, game=None, prior=None):
        def download(client, gid, out_dir, *a, **k):
            for e in files:
                target = out_dir / e["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")
            return [], True

        def verify(client, gid, out_dir, on_manifest=None, **k):
            on_manifest(files)

        monkeypatch.setattr(manager, "download_all_files", download)
        monkeypatch.setattr(manager, "verify_and_repair", verify)
        monkeypatch.setattr(manager, "poll_session", lambda *a, **k: {"state": "done"})

        class Server:
            c = SimpleNamespace(base="http://s")

            def get_session(self, gid):
                return {"id": 9, "state": "done"}

            def list_files(self, gid, session_id=None):
                return {"files": [{"path": "a", "size_bytes": 1}]}

        settings = Settings(install_dirs=[str(tmp_path / "games")])
        if prior:
            config.save_library({5: prior})
        return manager.run_install(
            Server(), game or {"id": 5, "name": "Pokemon Gamma Emerald"}, settings, SimpleNamespace(is_set=lambda: False),
            lambda m: None, lambda s: None, lambda a, b: None,
        )

    return SimpleNamespace(run=run, root=tmp_path / "games" / "Pokemon Gamma Emerald")


def test_a_game_that_came_without_a_folder_is_kept_in_one_of_its_own(install):
    rec = install.run([entry("PokemonEmerald.exe"), entry("Engine/a.dll")])

    game_folder = install.root / "Pokemon Gamma Emerald"
    assert (game_folder / "PokemonEmerald.exe").is_file() and (game_folder / "Engine" / "a.dll").is_file()
    assert [p.name for p in install.root.iterdir()] == ["Pokemon Gamma Emerald"]
    assert rec.files_dir == str(game_folder) and load_library()[5].files_dir == str(game_folder)
    manifest = load_install_manifest(5)
    assert "Pokemon Gamma Emerald/PokemonEmerald.exe" in manifest and "PokemonEmerald.exe" not in manifest


def test_a_game_that_came_in_a_folder_of_its_own_is_left_as_it_is(install):
    rec = install.run([entry("Mad Island/Mad Island.exe"), entry("Mad Island/data/a.pak")])

    assert (install.root / "Mad Island" / "Mad Island.exe").is_file() and rec.files_dir is None
    assert "Mad Island/Mad Island.exe" in load_install_manifest(5)


def test_a_game_already_installed_is_not_moved_by_a_second_run(install):
    first = install.run([entry("PokemonEmerald.exe")])
    first.executable = str(install.root / "Pokemon Gamma Emerald" / "PokemonEmerald.exe")
    config.save_library({5: first})

    again = install.run([entry("PokemonEmerald.exe")], prior=first)

    assert again.files_dir == first.files_dir
    assert (install.root / "Pokemon Gamma Emerald" / "PokemonEmerald.exe").is_file()
    assert not (install.root / "PokemonEmerald.exe").exists()  # fetched into its folder, not beside MOG's files
