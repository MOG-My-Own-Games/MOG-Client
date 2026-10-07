from pathlib import Path

import pytest

from mog_client import protocol


def test_a_link_from_the_web_ui_names_the_game_and_the_server():
    assert protocol.parse("mog://install/12?server=http%3A%2F%2Fnas%3A5566") == protocol.Link("install", 12, "http://nas:5566")
    assert protocol.parse("mog://install/7") == protocol.Link("install", 7, None)
    assert protocol.parse("MOG://INSTALL/7/") == protocol.Link("install", 7, None)


@pytest.mark.parametrize(
    "url",
    ["", "http://install/7", "mog://install/", "mog://install/abc", "mog://install/0", "mog://install/-3", "mog://launch/7", "mog:///7"],
)
def test_anything_else_is_not_a_link(url):
    assert protocol.parse(url) is None


def test_making_a_link_and_reading_it_back():
    link = protocol.make_link(5, "https://games.example:8443")
    assert protocol.parse(link) == protocol.Link("install", 5, "https://games.example:8443")
    assert protocol.make_link(5) == "mog://install/5"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("http://nas:5566", "http://NAS:5566/", True),
        ("http://nas", "http://nas:80", True),
        ("https://nas", "https://nas:443/some/path", True),
        ("nas:5566", "http://nas:5566", True),
        ("http://nas:5566", "http://nas:5567", False),
        ("http://nas:5566", "https://nas:5566", False),
        ("http://nas:5566", "http://192.168.1.2:5566", False),
        ("", "http://nas", False),
        (None, None, False),
    ],
)
def test_two_addresses_of_the_same_server(a, b, same):
    assert protocol.same_server(a, b) is same


def test_registering_on_linux_writes_the_handler_once(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(protocol.subprocess, "run", lambda cmd, **kw: ran.append(cmd[0]))
    assert protocol.register("/opt/MOG Client/mog.AppImage", "linux", tmp_path) is True
    entry = (tmp_path / ".local/share/applications" / protocol.DESKTOP_NAME).read_text()
    assert 'Exec="/opt/MOG Client/mog.AppImage" %u' in entry
    assert "MimeType=x-scheme-handler/mog;" in entry and "NoDisplay" not in entry and "Categories=Game;" in entry
    icon = tmp_path / ".local/share/icons/hicolor/256x256/apps/mog-client.png"
    assert icon.is_file() and f"Icon={icon}" in entry  # shown in the menu with its icon even from an AppImage
    assert ran == ["xdg-mime", "update-desktop-database"]

    ran.clear()
    assert protocol.register("/opt/MOG Client/mog.AppImage", "linux", tmp_path) is False  # nothing changed: nothing run
    assert ran == []
    assert protocol.register("/home/me/MOG.AppImage", "linux", tmp_path) is True  # moved: written again
    assert "/home/me/MOG.AppImage" in (tmp_path / ".local/share/applications" / protocol.DESKTOP_NAME).read_text()


def test_the_hidden_entry_of_an_earlier_version_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(protocol.subprocess, "run", lambda cmd, **kw: None)
    old = tmp_path / ".local/share/applications" / protocol.OLD_DESKTOP_NAME
    old.parent.mkdir(parents=True)
    old.write_text("[Desktop Entry]\nNoDisplay=true\n")
    protocol.register("/usr/bin/mog", "linux", tmp_path)
    assert not old.exists()


def test_a_missing_xdg_tool_does_not_get_in_the_way(tmp_path, monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(protocol.subprocess, "run", missing)
    assert protocol.register("/usr/bin/mog", "linux", tmp_path) is True
    assert (tmp_path / ".local/share/applications" / protocol.DESKTOP_NAME).is_file()


def test_other_systems_are_left_alone(tmp_path):
    assert protocol.register("/usr/bin/mog", "darwin", tmp_path) is False
    assert not list(Path(tmp_path).rglob("*.desktop"))
