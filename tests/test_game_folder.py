"""The prefix, launch script, entries and folder icon all live in the game's own folder."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mog_client import config, launcher, manager
from mog_client.config import InstalledGame, load_library, save_library


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "libdata")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wine" else None)
    monkeypatch.setattr(launcher, "client_command", lambda: "/opt/mog/mog")
    art = {"icon": b"PNG"}  # what the server's artwork endpoint returns; tests change it in place
    monkeypatch.setattr(manager, "fetch_artwork", lambda meta, client=None: dict(art))
    folder = tmp_path / "games" / "Jazz Jackrabbit 2_ The Secret Files"
    (folder / "Jazz Jackrabbit 2").mkdir(parents=True)
    exe = folder / "Jazz Jackrabbit 2" / "Jazz2.exe"
    exe.write_bytes(b"exe")
    rec = InstalledGame(game_id=138, name="Jazz Jackrabbit 2: The Secret Files", install_dir=str(folder), executable=str(exe))
    save_library({138: rec})
    return SimpleNamespace(rec=rec, folder=folder, exe=exe, tmp=tmp_path, art=art)


def finish(h, desktop=True):
    return manager.finish_setup(h.rec, {"id": 138}, str(h.exe), None, desktop, "auto", None)


def test_everything_the_game_needs_sits_in_its_folder(home):
    finish(home)

    stem = "Jazz Jackrabbit 2_ The Secret Files"  # the name, safe as a file name
    assert (home.folder / "pfx").is_dir()
    script = home.folder / f"{stem}.sh"
    assert script.is_file() and os.access(script, os.X_OK) and launcher.launch_script_path(home.rec) == script
    body = script.read_text()
    assert f"WINEPREFIX={home.folder / 'pfx'}" in body and body.splitlines()[-1].startswith("exec ")

    desktop = home.folder / f"{stem}.desktop"
    text = desktop.read_text()
    assert f"Exec={launcher.desktop_exec([str(script)])}" in text
    assert f"Path={home.exe.parent}" in text and f"Icon={home.folder / '.mog-icon'}" in text

    menu = home.tmp / "xdg/applications/mog-138.desktop"
    assert menu.is_symlink() and menu.resolve() == desktop.resolve()
    assert (home.folder / ".directory").read_text() == f"[Desktop Entry]\nIcon={home.folder / '.mog-icon'}\n"
    assert not (home.tmp / "libdata" / "launchers").exists()


def test_the_game_records_its_prefix_and_the_menu_link(home):
    finish(home)
    rec = load_library()[138]
    assert rec.prefix == str(home.folder / "pfx") and rec.desktop_entry == str(home.tmp / "xdg/applications/mog-138.desktop")


def test_a_game_without_artwork_gets_no_folder_icon(home):
    home.art.clear()
    finish(home)
    assert not (home.folder / ".directory").exists() and not (home.folder / ".mog-icon").exists()
    assert "Icon=" not in (home.folder / "Jazz Jackrabbit 2_ The Secret Files.desktop").read_text()


def test_an_icon_already_there_survives_a_refresh_that_finds_no_artwork(home):
    finish(home)
    home.art.clear()
    finish(home)
    assert (home.folder / ".mog-icon").read_bytes() == b"PNG" and (home.folder / ".directory").is_file()


def test_without_a_desktop_entry_only_the_script_remains(home):
    finish(home)
    finish(home, desktop=False)
    stem = "Jazz Jackrabbit 2_ The Secret Files"
    assert (home.folder / f"{stem}.sh").is_file()
    assert not (home.folder / f"{stem}.desktop").exists() and not (home.tmp / "xdg/applications/mog-138.desktop").is_symlink()


def test_the_menu_link_is_replaced_not_duplicated_when_the_entries_are_rebuilt(home):
    finish(home)
    finish(home)
    assert (home.tmp / "xdg/applications/mog-138.desktop").is_symlink()


def test_uninstalling_removes_the_menu_link_and_with_consent_everything(home):
    finish(home)
    leftovers = manager.uninstall(load_library()[138], delete_prefix=True)
    assert leftovers == [] and not home.folder.exists() and not (home.tmp / "xdg/applications/mog-138.desktop").is_symlink()
    assert 138 not in load_library()


def test_uninstalling_without_consent_leaves_only_the_prefix(home):
    finish(home)
    (home.folder / "pfx/drive_c/users/steamuser/Saved Games").mkdir(parents=True)
    (home.folder / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav").write_bytes(b"progress")

    leftovers = manager.uninstall(load_library()[138], delete_prefix=False)

    assert leftovers == []
    assert sorted(p.name for p in home.folder.iterdir()) == ["pfx"]
    assert (home.folder / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav").read_bytes() == b"progress"
    assert not (home.tmp / "xdg/applications/mog-138.desktop").is_symlink() and 138 not in load_library()


def test_a_game_installed_before_this_has_everything_removed(home):
    rec = home.rec  # no prefix recorded, no pfx folder
    assert manager.uninstall(rec, delete_prefix=False) == [] and not home.folder.exists()


def test_refreshing_metadata_rebuilds_from_the_servers_new_data_and_edits_the_steam_shortcut(home, monkeypatch):
    finish(home)
    rec = load_library()[138]
    rec.steam_entries = [{"shortcuts_path": str(home.tmp / "steam/userdata/1/config/shortcuts.vdf"), "appid": 1}]
    calls = {}
    monkeypatch.setattr(manager.steam, "update_shortcut", lambda entry, exe, start, opts, name=None, artwork=None: calls.update(artwork=artwork, name=name) or True)
    monkeypatch.setattr(manager.steam, "add_shortcut", lambda *a, **k: pytest.fail("the shortcut must not be recreated"))
    home.art.clear()
    home.art["icon"] = b"NEW ICON"
    server = SimpleNamespace(get_game=lambda gid: {"id": gid, "name": "Jazz 2", "igdb_metadata": {"changed": True}})

    game, changed = manager.refresh_metadata(rec, server)

    assert game["igdb_metadata"] == {"changed": True} and changed is True
    assert (home.folder / ".mog-icon").read_bytes() == b"NEW ICON"
    assert calls["artwork"] == {"icon": b"NEW ICON"} and rec.steam_entries[0]["appid"] == 1


def test_a_windows_shortcut_is_made_with_powershell(monkeypatch, tmp_path):
    script = launcher.windows_shortcut_script(tmp_path / "G O'Brien.lnk", "C:\\Games\\G\\g.exe", "C:\\Games\\G")
    assert "CreateShortcut('" in script and "G O''Brien.lnk" in script
    assert "$s.TargetPath='C:\\Games\\G\\g.exe'" in script and script.endswith("$s.Save()")
    assert "$s.WindowStyle=7" in script and "IconLocation" not in script
    with_icon = launcher.windows_shortcut_script(tmp_path / "x.lnk", "g.cmd", ".", icon="C:\\Games\\G\\g.exe")
    assert "$s.IconLocation='C:\\Games\\G\\g.exe,0'" in with_icon

    def no_powershell(*args, **kwargs):
        raise FileNotFoundError("powershell")

    monkeypatch.setattr(launcher.subprocess, "run", no_powershell)
    with pytest.raises(RuntimeError, match="could not create the shortcut"):
        launcher.create_windows_shortcut(tmp_path / "x.lnk", "g.exe", ".")


def test_on_windows_the_entry_is_a_lnk_next_to_the_game(home, monkeypatch):
    made = []
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "create_windows_shortcut", lambda lnk, target, workdir, icon=None: made.append((lnk, target, workdir, icon)))
    recorded = launcher.create_desktop_entry(home.rec)
    stem = "Jazz Jackrabbit 2_ The Secret Files"
    assert recorded == str(home.folder / f"{stem}.lnk")
    assert made == [(home.folder / f"{stem}.lnk", str(home.folder / f"{stem}.cmd"), str(home.exe.parent), str(home.exe))]


# --- Steam shortcuts: Steam's own casing, and nothing touched while it runs ---


def _entry(path, appid):
    from mog_client import steam

    return next(e for e in steam.load_shortcuts(path)["shortcuts"].values() if e["appid"] & 0xFFFFFFFF == appid)


@pytest.fixture
def steam_home(home, monkeypatch):
    from mog_client import steam

    user = home.tmp / "steam/userdata/1"
    home.steam_user, home.vdf = user, user / "config/shortcuts.vdf"
    home.steam = SimpleNamespace(running=False)
    monkeypatch.setattr(steam, "steam_running", lambda: home.steam.running)
    return home


def test_a_new_shortcut_is_written_the_way_steam_writes_its_own_and_gets_its_icon(steam_home):
    manager.finish_setup(steam_home.rec, {"id": 138}, str(steam_home.exe), steam_home.steam_user, True, "auto", None)

    rec = load_library()[138]
    entry = _entry(steam_home.vdf, rec.steam_entries[0]["appid"])
    assert entry["appname"] == rec.name and entry["exe"].endswith('.sh"')
    assert "Exe" not in entry and "AppName" not in entry
    assert Path(entry["icon"]).stem.endswith("_icon") and Path(entry["icon"]).read_bytes() == b"PNG"
    assert rec.steam_user == str(steam_home.steam_user) and rec.steam_pending is False


def test_a_shortcut_steam_has_rewritten_is_still_updated_and_not_given_a_second_copy_of_its_keys(steam_home):
    from mog_client import steam

    manager.finish_setup(steam_home.rec, {"id": 138}, str(steam_home.exe), steam_home.steam_user, True, "auto", None)
    appid = load_library()[138].steam_entries[0]["appid"]
    data = steam.load_shortcuts(steam_home.vdf)  # what Steam leaves when it has rewritten the file
    entry = next(iter(data["shortcuts"].values()))
    entry["exe"], entry["appname"] = '"/where/it/was.sh"', "Old name"
    entry["Exe"], entry["AppName"] = '"/stale.sh"', "Stale"  # copies an earlier update added beside Steam's
    steam.save_shortcuts(steam_home.vdf, data)

    manager.regenerate_entries(load_library()[138], {"id": 138}, "auto", None)

    entry = _entry(steam_home.vdf, appid)
    assert entry["exe"].endswith('Secret Files.sh"') and entry["appname"] == steam_home.rec.name
    assert sorted(k for k in entry if k.lower() in ("exe", "appname")) == ["appname", "exe"]  # one of each


def test_while_steam_runs_its_shortcut_is_left_alone_and_settled_later(steam_home):
    steam_home.steam.running = True
    manager.finish_setup(steam_home.rec, {"id": 138}, str(steam_home.exe), steam_home.steam_user, True, "auto", None)

    rec = load_library()[138]
    assert rec.steam_entries == [] and rec.steam_pending is True and rec.steam_user == str(steam_home.steam_user)
    assert not steam_home.vdf.exists()  # Steam would have undone it when it quits

    assert manager.settle_steam_shortcuts(SimpleNamespace(get_game=lambda gid: {"id": gid})) == []  # still running
    steam_home.steam.running = False
    done = manager.settle_steam_shortcuts(SimpleNamespace(get_game=lambda gid: {"id": gid}))

    rec = load_library()[138]
    assert done == [rec.name] and rec.steam_pending is False and len(rec.steam_entries) == 1
    assert _entry(steam_home.vdf, rec.steam_entries[0]["appid"])["appname"] == rec.name


def test_changing_the_engine_while_steam_runs_waits_too(steam_home, monkeypatch):
    manager.finish_setup(steam_home.rec, {"id": 138}, str(steam_home.exe), steam_home.steam_user, True, "auto", None)
    rec = load_library()[138]
    before = steam_home.vdf.read_bytes()
    steam_home.steam.running = True
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")

    assert manager.set_launcher(rec, "umu") is False

    assert steam_home.vdf.read_bytes() == before and load_library()[138].steam_pending is True
