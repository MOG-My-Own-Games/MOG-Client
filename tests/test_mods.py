from pathlib import Path

import pytest

from mog_client import mods
from mog_client.api import MogClient, safe_dirname


class FakeServer:
    """The three mod calls, with a folder that takes a few polls to zip."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []
        self.downloaded = []

    def prepare_mod(self, game_id, name):
        self.calls.append(("prepare", game_id, name))
        return self.states.pop(0)

    def mod_status(self, game_id, name):
        self.calls.append(("status", name))
        return self.states.pop(0)

    def download_mod(self, game_id, name, dest, on_progress=None):
        self.calls.append(("download", name))
        on_progress(50, 100)
        on_progress(100, 100)
        dest.write_bytes(b"zip")
        self.downloaded.append(dest)


def test_a_folder_is_followed_while_zipped_then_downloaded_as_a_zip(tmp_path):
    server = FakeServer(
        [
            {"state": "zipping", "bytes_done": 0, "bytes_total": 200},
            {"state": "zipping", "bytes_done": 100, "bytes_total": 200},
            {"state": "ready"},
        ]
    )
    seen = []

    saved = mods.fetch(server, 5, {"name": "mod1", "kind": "folder"}, tmp_path / "out", lambda stage, pct: seen.append((stage, pct)), sleep=lambda s: None)

    assert saved == tmp_path / "out" / "mod1.zip" and saved.read_bytes() == b"zip"
    assert seen == [("Zipping", 0), ("Zipping", 50), ("Downloading", 0), ("Downloading", 50), ("Downloading", 100)]
    assert [c[0] for c in server.calls] == ["prepare", "status", "status", "download"]


def test_an_archive_is_downloaded_at_once_under_its_own_name(tmp_path):
    server = FakeServer([{"state": "ready"}])
    saved = mods.fetch(server, 5, {"name": "mod2.zip", "kind": "archive"}, tmp_path, lambda *a: None, sleep=lambda s: None)
    assert saved.name == "mod2.zip" and [c[0] for c in server.calls] == ["prepare", "download"]


def test_a_failed_zip_or_a_stop_is_an_error_and_nothing_is_downloaded(tmp_path):
    server = FakeServer([{"state": "failed", "error": "disk full"}])
    with pytest.raises(mods.ModError, match="disk full"):
        mods.fetch(server, 5, {"name": "m", "kind": "folder"}, tmp_path, lambda *a: None, sleep=lambda s: None)
    assert server.downloaded == []

    server = FakeServer([{"state": "zipping", "bytes_total": 10}])
    with pytest.raises(mods.ModError, match="stopped"):
        mods.fetch(server, 5, {"name": "m", "kind": "folder"}, tmp_path, lambda *a: None, stopped=lambda: True, sleep=lambda s: None)


def test_a_file_already_there_is_never_overwritten(tmp_path):
    (tmp_path / "mod.zip").write_bytes(b"old")
    (tmp_path / "mod (2).zip").write_bytes(b"old")
    assert mods.free_path(tmp_path, "mod.zip") == tmp_path / "mod (3).zip"
    assert mods.free_path(tmp_path, "other.zip") == tmp_path / "other.zip"


def test_mods_go_in_downloads_under_the_game_name(tmp_path):
    assert mods.download_dir("Half/Life: 2", home=tmp_path) == tmp_path / "MOG" / safe_dirname("Half/Life: 2") / "mods"  # no Downloads: the home
    (tmp_path / "Downloads").mkdir()
    assert mods.download_dir("G", home=tmp_path) == tmp_path / "Downloads" / "MOG" / "G" / "mods"


class Recorder:
    def __init__(self, status=200, data=None):
        self.status, self.data, self.paths = status, data, []

    def get_json(self, path, **kw):
        self.paths.append(("GET", path))
        return self.status, self.data

    def post_json(self, path, body, **kw):
        self.paths.append(("POST", path))
        return self.status, self.data

    def download_to(self, path, dest, timeout=0, on_progress=None):
        self.paths.append(("DOWNLOAD", path))
        return self.status


def test_the_mod_calls_quote_the_name_and_an_older_server_has_no_mods():
    ok = Recorder(200, {"mods": [{"name": "my mod", "kind": "folder"}, {"junk": 1}]})
    assert MogClient(ok).mods(5) == [{"name": "my mod", "kind": "folder"}]
    assert MogClient(Recorder(404, {"detail": "Not Found"})).mods(5) == []

    api = Recorder(200, {"state": "ready"})
    client = MogClient(api)
    client.prepare_mod(5, "a/b mod")
    client.mod_status(5, "a/b mod")
    client.download_mod(5, "a/b mod", Path("/tmp/x"))
    assert api.paths == [
        ("POST", "/api/games/5/mods/a%2Fb%20mod/prepare"),
        ("GET", "/api/games/5/mods/a%2Fb%20mod/status"),
        ("DOWNLOAD", "/api/games/5/mods/a%2Fb%20mod/download"),
    ]
    with pytest.raises(RuntimeError, match="HTTP 409"):
        MogClient(Recorder(409)).download_mod(5, "m", Path("/tmp/x"))
