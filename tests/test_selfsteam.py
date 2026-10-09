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
    assert selfsteam.apply(world.settings) == "added"

    (entry,) = entries(world.user)
    assert entry["appname"] == "MOG - My Own Games" and entry["exe"] == '"/opt/MOG/MOG-Client.AppImage"'
    assert entry["StartDir"] == '"/opt/MOG"'
    appid = world.settings.steam_clients[0]["appid"]
    grid = world.user / "config" / "grid"
    assert {p.name for p in grid.iterdir()} == {f"{appid}p.png", f"{appid}.png", f"{appid}_hero.png", f"{appid}_logo.png", f"{appid}_icon.png"}
    assert selfsteam.added(world.settings) and config.load_settings().steam_clients == world.settings.steam_clients


def test_asking_twice_leaves_one_entry(world):
    selfsteam.apply(world.settings)
    selfsteam.apply(world.settings)
    assert len(entries(world.user)) == 1


def test_with_steam_running_the_entry_is_written_all_the_same(world):
    world.state["running"] = True
    assert selfsteam.apply(world.settings) == "added-open"
    assert len(entries(world.user)) == 1 and world.settings.steam_client_verify is True


def test_an_entry_steam_undid_when_it_quit_is_put_back_at_the_next_start(world):
    world.state["running"] = True
    selfsteam.apply(world.settings)
    assert selfsteam.settle(world.settings) is None  # still running: nothing to look at yet

    steam.save_shortcuts(steam.shortcuts_path(world.user), {"shortcuts": {}})  # what Steam wrote when it quit
    world.state["running"] = False
    assert selfsteam.settle(world.settings) == "added"
    assert len(entries(world.user)) == 1 and world.settings.steam_client_verify is False


def test_an_entry_steam_kept_is_left_alone_and_not_looked_at_again(world):
    world.state["running"] = True
    selfsteam.apply(world.settings)
    world.state["running"] = False
    assert selfsteam.settle(world.settings) is None and world.settings.steam_client_verify is False
    steam.save_shortcuts(steam.shortcuts_path(world.user), {"shortcuts": {}})
    assert selfsteam.settle(world.settings) is None  # it was seen to stay once: not checked again


@pytest.fixture
def two_accounts(world, monkeypatch):
    second = world.user.parent / "2002"
    (second / "config").mkdir(parents=True)
    monkeypatch.setattr(steam, "steam_user_dirs", lambda: [world.user, second])
    world.second = second
    return world


def test_by_default_the_client_goes_into_every_account(two_accounts):
    selfsteam.apply(two_accounts.settings)

    assert len(entries(two_accounts.user)) == 1 and len(entries(two_accounts.second)) == 1
    assert len(two_accounts.settings.steam_clients) == 2


def test_only_the_accounts_picked_get_it_and_a_change_moves_it(two_accounts):
    two_accounts.settings.steam_accounts = ["2002"]
    selfsteam.apply(two_accounts.settings)
    assert entries(two_accounts.user) == [] and len(entries(two_accounts.second)) == 1

    two_accounts.settings.steam_accounts = ["1001"]
    assert selfsteam.apply(two_accounts.settings) == "added"
    assert len(entries(two_accounts.user)) == 1 and entries(two_accounts.second) == []

    two_accounts.settings.steam_accounts = []
    assert selfsteam.apply(two_accounts.settings) == "removed"
    assert entries(two_accounts.user) == [] and selfsteam.accounts(two_accounts.settings) == []


def test_settling_puts_back_the_entry_of_each_account_steam_undid(two_accounts):
    two_accounts.state["running"] = True
    selfsteam.apply(two_accounts.settings)
    steam.save_shortcuts(steam.shortcuts_path(two_accounts.second), {"shortcuts": {}})
    two_accounts.state["running"] = False

    assert selfsteam.settle(two_accounts.settings) == "added"
    assert len(entries(two_accounts.user)) == 1 and len(entries(two_accounts.second)) == 1
    assert len(two_accounts.settings.steam_clients) == 2


def test_settings_of_an_earlier_version_keep_their_account(world, monkeypatch):
    world.settings.steam_clients = []
    selfsteam.apply(world.settings)
    record = world.settings.steam_clients[0]
    config.save_settings(world.settings)
    raw = config._read_json(config.settings_path(), {})
    raw.pop("steam_clients"), raw.pop("steam_accounts"), raw.pop("steam_decided")
    raw["steam_client"] = record
    config._write_json(config.settings_path(), raw, private=True)

    loaded = config.load_settings()

    assert loaded.steam_clients == [record] and loaded.steam_accounts == ["1001"] and loaded.steam_decided is True


def test_an_earlier_never_ask_turns_the_integration_off(world):
    config._write_json(config.settings_path(), {"steam_never_ask": True}, private=True)
    loaded = config.load_settings()
    assert loaded.steam_accounts == [] and loaded.steam_decided is True and selfsteam.accounts(loaded) == []


def test_a_request_an_earlier_version_left_waiting_is_carried_out(world):
    world.settings.steam_client_pending = str(world.user)
    assert selfsteam.settle(world.settings) == "added"
    assert len(entries(world.user)) == 1 and world.settings.steam_client_pending == ""


def test_a_client_that_moved_is_followed_by_its_shortcut(world, monkeypatch):
    selfsteam.apply(world.settings)
    assert selfsteam.settle(world.settings) is None  # nothing changed

    monkeypatch.setattr(selfsteam, "client_command", lambda: "/home/me/Games/mog.AppImage")
    assert selfsteam.settle(world.settings) == "updated"
    (entry,) = entries(world.user)
    assert entry["exe"] == '"/home/me/Games/mog.AppImage"' and entry["StartDir"] == '"/home/me/Games"'


def test_the_client_can_be_taken_out_again(world):
    selfsteam.apply(world.settings)
    world.state["running"] = True
    assert selfsteam.remove(world.settings) is True
    assert entries(world.user) == [] and world.settings.steam_clients == []
    assert selfsteam.remove(world.settings) is False


def test_without_steam_there_is_nothing_to_add_or_ask(world, monkeypatch):
    monkeypatch.setattr(steam, "steam_user_dirs", lambda: [])
    assert selfsteam.apply(world.settings) == "no-steam" and not selfsteam.available()
    assert selfsteam.should_ask(world.settings, "1.0") is False


def test_the_question_comes_once_per_version_and_never_once_answered(world):
    assert selfsteam.should_ask(world.settings, "1.0") is True
    world.settings.steam_asked_for = "1.0"
    assert selfsteam.should_ask(world.settings, "1.0") is False
    assert selfsteam.should_ask(world.settings, "1.1") is True  # an update asks again

    world.settings.steam_decided = True
    assert selfsteam.should_ask(world.settings, "1.2") is False
    world.settings.steam_decided = False
    selfsteam.apply(world.settings)
    world.settings.steam_decided = True
    assert selfsteam.should_ask(world.settings, "1.3") is False


LOGINUSERS = '''"users"
{
	"76561198025058569"
	{
		"AccountName"		"xargon_login"
		"PersonaName"		"Xargon"
		"RememberPassword"		"1"
	}
	"76561197960265729"
	{
		"AccountName"		"other"
		"PersonaName"		"Say \\"Hi\\""
	}
}
'''


def test_a_steam_account_is_called_by_its_name(tmp_path):
    root = tmp_path / "Steam"
    (root / "config").mkdir(parents=True)
    (root / "config" / "loginusers.vdf").write_text(LOGINUSERS)
    xargon = root / "userdata" / str(76561198025058569 - steam.STEAMID64_BASE)
    other = root / "userdata" / "1"
    stranger = root / "userdata" / "99"
    for d in (xargon, other, stranger):
        (d / "config").mkdir(parents=True)

    assert steam.persona_name(xargon) == "Xargon" and steam.persona_name(other) == 'Say "Hi"'
    assert steam.persona_name(stranger) is None
    assert selfsteam.label(xargon, [xargon, other]) == "Xargon"
    assert selfsteam.label(stranger, [xargon, stranger]) == "Account 99"


def test_the_account_s_own_settings_name_it_when_the_login_list_does_not(tmp_path):
    user = tmp_path / "Steam" / "userdata" / "5"
    (user / "config").mkdir(parents=True)
    (user / "config" / "localconfig.vdf").write_text('"UserLocalConfigStore"\n{\n\t"PersonaName"\t\t"Xargon"\n}\n')
    assert steam.persona_name(user) == "Xargon"


def test_two_accounts_with_one_name_are_told_apart_by_their_number(tmp_path):
    root = tmp_path / "Steam"
    users = []
    for number in ("7", "8"):
        user = root / "userdata" / number
        (user / "config").mkdir(parents=True)
        (user / "config" / "localconfig.vdf").write_text('"PersonaName"\t\t"Same"')
        users.append(user)
    assert [selfsteam.label(u, users) for u in users] == ["Same (7)", "Same (8)"]
