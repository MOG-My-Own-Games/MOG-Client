from pathlib import Path

import pytest

from mog_client import config, selfsteam, steam
from mog_client.config import Settings


@pytest.fixture
def world(tmp_path, monkeypatch):
    user = tmp_path / "Steam" / "userdata" / "1001"
    (user / "config").mkdir(parents=True)
    state = {"running": False}
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(steam, "steam_user_dirs", lambda: [user])
    monkeypatch.setattr(steam, "steam_running", lambda: state["running"])
    monkeypatch.setattr(selfsteam, "client_command", lambda: "/opt/MOG/MOG-Client.AppImage")
    return type("World", (), {"user": user, "state": state, "settings": Settings()})


def entries(user: Path) -> list[dict]:
    return list(steam.load_shortcuts(steam.shortcuts_path(user))["shortcuts"].values())


def test_the_artwork_made_for_the_client_is_in_the_build():
    art = selfsteam.artwork()
    assert set(art) == {"portrait", "wide", "hero", "logo", "icon"} and all(blob[:4] == b"\x89PNG" for blob in art.values())


def test_the_client_is_added_with_its_artwork_and_remembered(world):
    assert selfsteam.add(world.settings) == "added"

    (entry,) = entries(world.user)
    assert entry["appname"] == "MOG - My Own Games" and entry["exe"] == '"/opt/MOG/MOG-Client.AppImage"'
    assert entry["StartDir"] == '"/opt/MOG"'
    appid = world.settings.steam_client["appid"]
    grid = world.user / "config" / "grid"
    assert {p.name for p in grid.iterdir()} == {f"{appid}p.png", f"{appid}.png", f"{appid}_hero.png", f"{appid}_logo.png", f"{appid}_icon.png"}
    assert selfsteam.added(world.settings) and config.load_settings().steam_client == world.settings.steam_client


def test_asking_twice_leaves_one_entry(world):
    selfsteam.add(world.settings)
    selfsteam.add(world.settings)
    assert len(entries(world.user)) == 1


def test_with_steam_running_nothing_is_written_until_it_is_closed(world):
    world.state["running"] = True
    assert selfsteam.add(world.settings) == "pending"
    assert not steam.shortcuts_path(world.user).exists() and world.settings.steam_client_pending == str(world.user)
    assert selfsteam.settle(world.settings) is None  # still running

    world.state["running"] = False
    assert selfsteam.settle(world.settings) == "added"
    assert len(entries(world.user)) == 1 and world.settings.steam_client_pending == ""


def test_a_client_that_moved_is_followed_by_its_shortcut(world, monkeypatch):
    selfsteam.add(world.settings)
    assert selfsteam.settle(world.settings) is None  # nothing changed

    monkeypatch.setattr(selfsteam, "client_command", lambda: "/home/me/Games/mog.AppImage")
    assert selfsteam.settle(world.settings) == "updated"
    (entry,) = entries(world.user)
    assert entry["exe"] == '"/home/me/Games/mog.AppImage"' and entry["StartDir"] == '"/home/me/Games"'


def test_the_client_can_be_taken_out_again_but_not_while_steam_runs(world):
    selfsteam.add(world.settings)
    world.state["running"] = True
    assert selfsteam.remove(world.settings) is False and len(entries(world.user)) == 1
    world.state["running"] = False
    assert selfsteam.remove(world.settings) is True
    assert entries(world.user) == [] and world.settings.steam_client is None
    assert selfsteam.remove(world.settings) is False


def test_without_steam_there_is_nothing_to_add_or_ask(world, monkeypatch):
    monkeypatch.setattr(steam, "steam_user_dirs", lambda: [])
    assert selfsteam.add(world.settings) == "no-steam" and not selfsteam.available()
    assert selfsteam.should_ask(world.settings, "1.0") is False


def test_the_question_comes_once_per_version_and_never_when_refused_or_done(world):
    assert selfsteam.should_ask(world.settings, "1.0") is True
    world.settings.steam_asked_for = "1.0"
    assert selfsteam.should_ask(world.settings, "1.0") is False
    assert selfsteam.should_ask(world.settings, "1.1") is True  # an update asks again

    world.settings.steam_never_ask = True
    assert selfsteam.should_ask(world.settings, "1.2") is False
    world.settings.steam_never_ask = False
    selfsteam.add(world.settings)
    assert selfsteam.should_ask(world.settings, "1.3") is False


def test_steam_is_closed_by_its_own_command_and_waited_for(world, monkeypatch):
    ran = []
    monkeypatch.setattr(selfsteam, "steam_command", lambda: ["steam"])
    monkeypatch.setattr(selfsteam.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    world.state["running"] = True
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 3:
            world.state["running"] = False

    assert selfsteam.close_steam(wait=10, sleep=sleep, clock=lambda: clock[0]) is True
    assert ran == [["steam", "-shutdown"]]

    world.state["running"] = True
    clock[0] = 0
    assert selfsteam.close_steam(wait=2, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0]) is False


def test_steam_is_not_closed_from_inside_steam_or_without_a_way_to_do_it(world, monkeypatch):
    monkeypatch.setattr(selfsteam, "steam_command", lambda: ["steam"])
    for var in ("SteamGameId", "SteamAppId", "SteamGamepadUI"):
        monkeypatch.delenv(var, raising=False)
    assert selfsteam.can_close_steam() is True

    monkeypatch.setenv("SteamGamepadUI", "1")  # Game Mode: closing Steam would close this client too
    assert selfsteam.can_close_steam() is False
    monkeypatch.delenv("SteamGamepadUI")
    monkeypatch.setattr(selfsteam, "steam_command", lambda: None)
    assert selfsteam.can_close_steam() is False and selfsteam.close_steam() is False and selfsteam.start_steam() is False


def test_steam_is_started_apart_from_the_client(world, monkeypatch):
    started = []
    monkeypatch.setattr(selfsteam, "steam_command", lambda: ["flatpak", "run", "com.valvesoftware.Steam"])
    monkeypatch.setattr(selfsteam.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw["start_new_session"])))
    assert selfsteam.start_steam() is True
    assert started == [(["flatpak", "run", "com.valvesoftware.Steam"], True)]


def test_a_shortcut_made_under_the_old_name_is_renamed_where_it_stands(world):
    selfsteam.add(world.settings)
    path = steam.shortcuts_path(world.user)
    data = steam.load_shortcuts(path)
    data["shortcuts"]["0"]["appname"] = "MOG Client"  # what an earlier version called it
    steam.save_shortcuts(path, data)
    appid = world.settings.steam_client["appid"]

    assert selfsteam.settle(world.settings) == "updated"
    (entry,) = entries(world.user)
    assert entry["appname"] == "MOG - My Own Games" and world.settings.steam_client["appid"] == appid  # art stays


def test_an_entry_made_before_the_logo_existed_gets_it_at_the_next_start(world):
    selfsteam.add(world.settings)
    appid = world.settings.steam_client["appid"]
    logo = world.user / "config" / "grid" / f"{appid}_logo.png"
    logo.unlink()
    world.settings.steam_client["artwork"] = [f for f in world.settings.steam_client["artwork"] if not f.endswith("_logo.png")]

    assert selfsteam.settle(world.settings) == "updated"
    assert logo.is_file() and logo.read_bytes()[:4] == b"\x89PNG"
    assert any(f.endswith("_logo.png") for f in world.settings.steam_client["artwork"])
    assert selfsteam.settle(world.settings) is None  # nothing left to add: the pictures are not written every start
