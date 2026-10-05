"""The prefix, launch script, entries and folder icon all live in the game's own folder."""

import os
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


def test_a_windows_shortcut_is_made_with_powershell(monkeypatch, tmp_path):
    script = launcher.windows_shortcut_script(tmp_path / "G O'Brien.lnk", "C:\\Games\\G\\g.exe", "C:\\Games\\G")
    assert "CreateShortcut('" in script and "G O''Brien.lnk" in script
    assert "$s.TargetPath='C:\\Games\\G\\g.exe'" in script and script.endswith("$s.Save()")

    def no_powershell(*args, **kwargs):
        raise FileNotFoundError("powershell")

    monkeypatch.setattr(launcher.subprocess, "run", no_powershell)
    with pytest.raises(RuntimeError, match="could not create the shortcut"):
        launcher.create_windows_shortcut(tmp_path / "x.lnk", "g.exe", ".")


def test_on_windows_the_entry_is_a_lnk_next_to_the_game(home, monkeypatch):
    made = []
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "create_windows_shortcut", lambda lnk, target, workdir: made.append((lnk, target, workdir)))
    recorded = launcher.create_desktop_entry(home.rec)
    stem = "Jazz Jackrabbit 2_ The Secret Files"
    assert recorded == str(home.folder / f"{stem}.lnk")
    assert made == [(home.folder / f"{stem}.lnk", str(home.exe), str(home.exe.parent))]
