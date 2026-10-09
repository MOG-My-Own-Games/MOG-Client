import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mog_client import config, manager
from mog_client.config import Settings
from mog_client.saves import installed, state
from mog_client.saves.locations import install_candidates


def put(root: Path, name: str, content: bytes = b"x", mtime_ns: int | None = None) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def entry(path: str, content: bytes) -> dict:
    return {"path": path, "size_bytes": len(content), "sha1": hashlib.sha1(content).hexdigest()}  # noqa: S324


def sha1(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()  # noqa: S324


@pytest.fixture
def folder(tmp_path):
    """A game's folder: not tmp_path itself, where the tests keep the client's own data."""
    path = tmp_path / "Game"
    path.mkdir()
    return path


# --- the manifest adds up -----------------------------------------------------------------------------


def test_a_later_installers_files_are_added_to_the_manifest_not_put_in_its_place():
    state.save_install_manifest(7, [entry("game.exe", b"exe"), entry("data/a.pak", b"a")])
    state.save_install_manifest(7, [entry("patch/b.pak", b"patch")])  # a list that only names the new files
    assert set(state.load_install_manifest(7)) == {"game.exe", "data/a.pak", "patch/b.pak"}


def test_a_file_an_installer_replaced_takes_the_new_entry():
    state.save_install_manifest(7, [entry("game.exe", b"v1")])
    state.save_install_manifest(7, [entry("game.exe", b"version 2")])
    assert state.load_install_manifest(7) == {"game.exe": (len(b"version 2"), sha1(b"version 2"))}


def test_the_first_manifest_is_just_the_list():
    assert state.load_install_manifest(7) is None
    state.save_install_manifest(7, [entry("a", b"1")])
    assert state.load_install_manifest(7) == {"a": (1, sha1(b"1"))}


def test_forgetting_a_game_drops_the_manifest_and_the_kept_starting_picture(folder):
    state.save_install_manifest(7, [entry("a", b"1")])
    installed.begin(7, folder / "none")
    state.forget_game(7)
    assert state.load_install_manifest(7) is None and not state.baseline_path(7).exists()


# --- the folder itself ---------------------------------------------------------------------------------


def test_the_snapshot_leaves_out_links_the_prefix_and_what_mog_writes(folder):
    put(folder, "game.exe")
    put(folder, "data/a.pak")
    put(folder, "pfx/drive_c/x.dll")
    put(folder, ".mog-icon")
    (folder / "link").symlink_to(folder / "game.exe")
    assert set(installed.snapshot(folder)) == {"game.exe", "data/a.pak"}
    assert installed.snapshot(folder / "missing") == {}


def test_what_an_install_adds_is_recorded_even_when_the_servers_list_was_not(folder):
    put(folder, "profile.sav", b"earlier save")  # there before: the game wrote it
    before = installed.begin(7, folder)
    put(folder, "patch/new.pak", b"patch")  # the install put it there
    assert installed.finish(7, folder, before) == 1
    assert state.load_install_manifest(7) == {"patch/new.pak": (5, sha1(b"patch"))}


def test_a_file_the_install_changed_is_recorded_with_its_new_content(folder):
    put(folder, "game.exe", b"old", mtime_ns=10**18)
    state.save_install_manifest(7, [entry("game.exe", b"old")])
    before = installed.begin(7, folder)
    put(folder, "game.exe", b"patched exe", mtime_ns=2 * 10**18)
    installed.finish(7, folder, before)
    assert state.load_install_manifest(7)["game.exe"] == (len(b"patched exe"), sha1(b"patched exe"))


def test_a_file_the_server_already_listed_is_not_added_twice(folder):
    before = installed.begin(7, folder)
    put(folder, "a.pak", b"a")
    state.save_install_manifest(7, [entry("a.pak", b"a")])
    assert installed.finish(7, folder, before) == 0


def test_the_starting_picture_survives_a_pause_so_the_first_part_is_still_counted(folder):
    put(folder, "profile.sav", b"save")
    first = installed.begin(7, folder)  # an install starts...
    put(folder, "part1.pak", b"one")  # ...downloads some, is paused: finish() is never called
    second = installed.begin(7, folder)  # resumed: the same starting line, not a new look
    assert second == first and "part1.pak" not in second
    put(folder, "part2.pak", b"two")
    installed.finish(7, folder, second)
    assert set(state.load_install_manifest(7)) == {"part1.pak", "part2.pak"}  # not profile.sav
    assert not state.baseline_path(7).exists()
    assert set(installed.begin(7, folder)) == {"profile.sav", "part1.pak", "part2.pak"}  # the next install starts afresh


def test_the_installed_files_are_never_saves_afterwards(folder):
    put(folder, "profile.sav", b"save")
    before = installed.begin(7, folder)
    put(folder, "mod/extra.pak", b"extra")
    put(folder, "data.pak", b"data")
    installed.finish(7, folder, before)
    found = {c.key for c in install_candidates(folder, state.load_install_manifest(7))}
    assert found == {"game/profile.sav"}


# --- the whole install ---------------------------------------------------------------------------------


class Server:
    def __init__(self):
        self.listing_works = True

    c = SimpleNamespace(base="http://s")

    def get_session(self, gid):
        return {"id": 5, "state": "done"}

    def list_files(self, gid, session_id=None):
        return {"files": [{"path": "a", "size_bytes": 1}]}


def run(tmp_path, monkeypatch, writes, complete=True, listing=True):
    """One call of manager.run_install with the downloading stood in for: `writes` are what it puts in the folder."""
    monkeypatch.setattr(manager, "poll_session", lambda *a, **k: {"state": "done"})

    def download(client, gid, out_dir, *a, **k):
        for name, content in writes.items():
            put(out_dir, name, content)
        return 1, complete

    def verify(client, gid, out_dir, *, on_manifest=None, **k):
        if listing and on_manifest:
            on_manifest([entry(n, c) for n, c in writes.items()])

    monkeypatch.setattr(manager, "download_all_files", download)
    monkeypatch.setattr(manager, "verify_and_repair", verify)
    settings = Settings(install_dirs=[str(tmp_path / "games")])
    return manager.run_install(Server(), {"id": 7, "name": "Game"}, settings, SimpleNamespace(is_set=lambda: False), lambda m: None, lambda s: None, lambda a, b: None)


def test_a_second_installer_run_into_the_same_game_keeps_the_first_ones_files_out_of_the_saves(tmp_path, monkeypatch):
    rec = run(tmp_path, monkeypatch, {"game.exe": b"exe", "data/a.pak": b"a"})
    run(tmp_path, monkeypatch, {"dlc/b.pak": b"b"})  # the server's list names only the new one this time
    folder = Path(rec.install_dir)
    put(folder, "profile.sav", b"save")
    found = {c.key for c in install_candidates(folder, state.load_install_manifest(7))}
    assert found == {"game/profile.sav"}


def test_files_installed_when_the_server_could_not_list_them_are_still_not_saves(tmp_path, monkeypatch):
    rec = run(tmp_path, monkeypatch, {"game.exe": b"exe", "extra/c.pak": b"c"}, listing=False)
    assert set(state.load_install_manifest(7)) == {"game.exe", "extra/c.pak"}
    assert Path(rec.install_dir).is_dir()


def test_a_paused_install_records_nothing_and_the_resume_records_the_whole_run(tmp_path, monkeypatch):
    rec = run(tmp_path, monkeypatch, {"part1.pak": b"one"}, complete=False, listing=False)
    assert state.load_install_manifest(7) is None and state.baseline_path(7).exists()
    run(tmp_path, monkeypatch, {"part2.pak": b"two"}, listing=False)
    assert set(state.load_install_manifest(7)) == {"part1.pak", "part2.pak"}
    assert not state.baseline_path(7).exists() and config.load_library()[7].state == "awaiting_executable"
    assert rec.game_id == 7


def test_a_save_written_before_a_reinstall_is_not_taken_for_installed_data(tmp_path, monkeypatch):
    rec = run(tmp_path, monkeypatch, {"game.exe": b"exe"}, listing=False)
    put(Path(rec.install_dir), "profile.sav", b"played")  # the game, later
    run(tmp_path, monkeypatch, {"patch.pak": b"p"}, listing=False)
    assert set(state.load_install_manifest(7)) == {"game.exe", "patch.pak"}
