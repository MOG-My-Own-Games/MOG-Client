import sys
from pathlib import Path

import pytest

from mog_client import launcher, manager, steam
from mog_client.config import InstalledGame


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    available = {"faugus", "wine"}
    monkeypatch.setattr(launcher, "available_launchers", lambda: sorted(available))
    runner = ["flatpak", "run", "--command=/app/bin/faugus-launcher", launcher.FAUGUS_FLATPAK, "--run"]
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (runner, str(tmp_path / "umu-run"), True))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("flatpak", "wine", "env") else None)
    monkeypatch.setattr(manager, "_update", lambda rec, **changes: [setattr(rec, k, v) for k, v in changes.items()])
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    game = InstalledGame(game_id=7, name="Game", install_dir=str(exe.parent), executable=str(exe), prefix=str(tmp_path / "pfx"), state="installed")
    user = tmp_path / "steam" / "userdata" / "1"
    record = steam.add_shortcut(user, "Game", "/placeholder", str(exe.parent), "", artwork={"portrait": b"\x89PNGdata"})
    game.steam_entries = [record]
    return game, record


def _steam_entry(record):
    data = steam.load_shortcuts(Path(record["shortcuts_path"]))
    (entry,) = data["shortcuts"].values()
    return entry


def test_changing_the_engine_rewrites_the_script_and_edits_the_steam_entry_in_place(setup):
    game, record = setup
    manager.set_launcher(game, "faugus", "wine")
    script = launcher.launch_script_path(game)
    assert "flatpak" in script.read_text() and "wine" not in script.read_text().split("exec", 1)[1].split("flatpak")[0]
    assert _steam_entry(record)["Exe"] == f'"{script}"'

    changed = manager.set_launcher(game, "wine", "faugus")

    assert changed is False  # the Steam entry runs the same script: nothing to edit
    assert " wine " in script.read_text() or "/usr/bin/wine" in script.read_text()
    assert "flatpak" not in script.read_text()
    entry = _steam_entry(record)
    assert entry["appid"] & 0xFFFFFFFF == record["appid"] and entry["AppName"] == "Game"
    assert all(Path(a).is_file() for a in record["artwork"])


def test_a_legacy_steam_entry_is_migrated_once_in_place(setup):
    game, record = setup
    before = _steam_entry(record)["appid"]
    assert manager.set_launcher(game, "wine", "faugus") is True
    assert _steam_entry(record)["appid"] == before
    assert manager.set_launcher(game, "faugus", "faugus") is False


def test_the_desktop_entry_keeps_its_file_and_points_at_the_script(setup, tmp_path):
    game, _ = setup
    game.desktop_entry = launcher.create_desktop_entry(game, None, "wine")
    manager.set_launcher(game, "faugus", "wine")
    text = Path(game.desktop_entry).read_text()
    assert str(launcher.launch_script_path(game)) in text and "flatpak" not in text


def test_the_games_own_engine_beats_the_global_setting(setup):
    game, _ = setup
    game.launcher = "wine"
    cmd, _ = launcher.launch_command(game, "faugus")
    assert cmd[-2:] == ["wine", game.executable] or cmd[0].endswith("wine")


def test_an_unusable_engine_leaves_the_game_as_it_was(setup, monkeypatch):
    game, _ = setup
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": None)
    with pytest.raises(RuntimeError):
        manager.set_launcher(game, "umu", "auto")
    assert game.launcher == "auto"


def test_removing_the_entries_also_removes_the_launch_script(setup):
    game, _ = setup
    manager.set_launcher(game, "wine", "faugus")
    script = launcher.launch_script_path(game)
    assert script.is_file()
    manager.remove_entries(game)
    assert not script.exists()


def _art(monkeypatch, blob=b"\x89PNGnew"):
    monkeypatch.setattr(manager, "fetch_artwork", lambda meta, client=None: {"portrait": blob})


def test_regenerating_edits_the_steam_entry_in_place_and_refreshes_its_artwork(setup, monkeypatch):
    game, record = setup
    _art(monkeypatch)
    appid_before = _steam_entry(record)["appid"]

    manager.regenerate_entries(game, {}, "wine")

    data = steam.load_shortcuts(Path(record["shortcuts_path"]))
    assert len(data["shortcuts"]) == 1
    entry = _steam_entry(record)
    assert entry["appid"] == appid_before and entry["Exe"] == f'"{launcher.launch_script_path(game)}"'
    assert Path(game.steam_entries[0]["artwork"][0]).read_bytes() == b"\x89PNGnew"


def test_regenerating_twice_changes_nothing_the_second_time(setup, monkeypatch):
    game, _ = setup
    _art(monkeypatch)
    assert manager.regenerate_entries(game, {}, "wine") is True  # the placeholder entry was legacy
    assert manager.regenerate_entries(game, {}, "wine") is False


def test_a_shortcut_deleted_in_steam_is_put_back_by_regenerating(setup, monkeypatch):
    game, record = setup
    _art(monkeypatch)
    steam.remove_shortcut(record)
    assert steam.load_shortcuts(Path(record["shortcuts_path"]))["shortcuts"] == {}

    assert manager.regenerate_entries(game, {}, "wine") is True

    assert len(steam.load_shortcuts(Path(record["shortcuts_path"]))["shortcuts"]) == 1


def test_choosing_not_to_have_a_steam_entry_removes_it_and_a_new_user_gets_one(setup, monkeypatch, tmp_path):
    game, record = setup
    _art(monkeypatch)
    manager.finish_setup(game, {}, game.executable, None, desktop=False, launcher="wine")
    assert game.steam_entries == []
    assert steam.load_shortcuts(Path(record["shortcuts_path"]))["shortcuts"] == {}

    other = tmp_path / "steam" / "userdata" / "2"
    assert manager.finish_setup(game, {}, game.executable, other, desktop=False, launcher="wine") is True
    assert len(game.steam_entries) == 1 and game.steam_entries[0]["shortcuts_path"].endswith("2/config/shortcuts.vdf")


def test_update_shortcut_says_when_the_entry_is_gone(setup):
    game, record = setup
    steam.remove_shortcut(record)
    assert steam.update_shortcut(record, "/x", "/d", "") is None


def test_the_entries_artwork_comes_from_the_server_kind_by_kind():
    from mog_client import scrape

    class Server:
        def __init__(self):
            self.asked = []

        def get_image(self, path):
            self.asked.append(path)
            kind = path.rsplit("/", 1)[1]
            return None if kind == "icon" else f"{kind}-bytes".encode()

    server = Server()
    art = scrape.fetch_artwork({"id": 7}, server)

    assert art == {"portrait": b"cover-bytes", "wide": b"banner-bytes", "hero": b"hero-bytes", "logo": b"logo-bytes"}
    assert server.asked == [f"/api/games/7/media/{k}" for k in ("cover", "banner", "hero", "logo", "icon")]


def test_an_older_server_that_serves_no_artwork_still_gives_the_cover(monkeypatch):
    from mog_client import scrape

    class Old:
        def get_image(self, path):
            return None

    monkeypatch.setattr(scrape, "fetch_url", lambda url: b"direct-cover")
    art = scrape.fetch_artwork({"id": 7, "cover_path": "https://cdn2.steamgriddb.com/p.png"}, Old())
    assert art == {"portrait": b"direct-cover"}
