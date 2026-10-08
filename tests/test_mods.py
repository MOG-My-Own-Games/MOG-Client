from pathlib import Path

import pytest

from mog_client import mods
from mog_client.api import MogClient


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
    server.cancel_mod = lambda game_id, name: server.calls.append(("cancel", name))
    with pytest.raises(mods.ModCancelled):
        mods.fetch(server, 5, {"name": "m", "kind": "folder"}, tmp_path, lambda *a: None, stopped=lambda: True, sleep=lambda s: None)


def test_a_file_already_there_is_never_overwritten(tmp_path):
    (tmp_path / "mod.zip").write_bytes(b"old")
    (tmp_path / "mod (2).zip").write_bytes(b"old")
    assert mods.free_path(tmp_path, "mod.zip") == tmp_path / "mod (3).zip"
    assert mods.free_path(tmp_path, "other.zip") == tmp_path / "other.zip"


def test_mods_go_in_the_mods_folder_of_the_games_own_folder():
    assert mods.mods_dir(Path("/games/Gothic II")) == Path("/games/Gothic II/mods")


def test_a_cancel_during_the_zip_tells_the_server_and_leaves_nothing(tmp_path):
    server = FakeServer([{"state": "zipping", "bytes_done": 1, "bytes_total": 10}, {"state": "zipping", "bytes_done": 5, "bytes_total": 10}])
    server.cancel_mod = lambda game_id, name: server.calls.append(("cancel", name))
    stops = iter([False, True])

    with pytest.raises(mods.ModCancelled):
        mods.fetch(server, 5, {"name": "m", "kind": "folder"}, tmp_path, lambda *a: None, stopped=lambda: next(stops), sleep=lambda s: None)

    assert server.calls[-1] == ("cancel", "m") and server.downloaded == []


def test_a_cancel_during_the_download_stops_it_and_removes_the_half_file(tmp_path, monkeypatch):
    from mog_client import api, net

    class Response:
        headers = {"Content-Length": "6"}
        status = 200

        def __init__(self):
            self.parts = [b"aa", b"bb", b"cc"]

        def read(self, n):
            return self.parts.pop(0) if self.parts else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(net, "urlopen", lambda req, timeout=0: Response())
    client = api.Client("http://server:5000", "u", "p")
    seen = []

    def stop_after_first(written, total):
        seen.append(written)
        if written >= 2:
            raise mods.ModCancelled("stop")

    with pytest.raises(mods.ModCancelled):
        client.download_to("/x", tmp_path / "m.zip", on_progress=stop_after_first)

    assert seen == [2] and list(tmp_path.iterdir()) == []  # neither the file nor its .part


class Recorder:
    def __init__(self, status=200, data=None):
        self.status, self.data, self.paths = status, data, []

    def get_json(self, path, **kw):
        self.paths.append(("GET", path))
        return self.status, self.data

    def post_json(self, path, body, **kw):
        self.paths.append(("POST", path))
        return self.status, self.data

    def download_to(self, path, dest, timeout=0, on_progress=None, resume=False):
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


class Served:
    """A server that holds one file and answers Range and If-Range the way the real one does."""

    def __init__(self, data: bytes, etag='"v1"', cut_after=None):
        self.data, self.etag, self.cut_after, self.requests = data, etag, cut_after, []

    def urlopen(self, req, timeout=0):
        import io
        import urllib.error

        headers = {k.lower(): v for k, v in req.header_items()}
        self.requests.append((headers.get("range"), headers.get("if-range")))
        start, status, body = 0, 200, self.data
        if "range" in headers:
            start = int(headers["range"].split("=")[1].rstrip("-"))
            if start >= len(self.data):
                raise urllib.error.HTTPError(req.full_url, 416, "range", {}, io.BytesIO())
            if headers.get("if-range") == self.etag:
                status, body = 206, self.data[start:]
        served = self

        class Response:
            def __init__(self):
                self.status = status
                self.headers = {"ETag": served.etag, "Content-Length": str(len(body))}
                self.left = body if served.cut_after is None else body[: served.cut_after]

            def read(self, n):
                chunk, self.left = self.left[:2], self.left[2:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return Response()


def _client(monkeypatch, served):
    from mog_client import api, net

    monkeypatch.setattr(net, "urlopen", served.urlopen)
    monkeypatch.setattr(api, "STREAM_CHUNK", 2)
    return api.Client("http://server:5000", "u", "p")


def test_a_mod_download_cut_short_is_continued_from_the_same_half_file(tmp_path, monkeypatch):
    served = Served(b"0123456789", cut_after=4)
    client = _client(monkeypatch, served)
    dest = tmp_path / "m.zip"

    with pytest.raises(RuntimeError, match="cut short"):
        client.download_to("/x", dest, resume=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.zip.part", "m.zip.part.id"]  # one half file, not one per try

    served.cut_after = None
    assert client.download_to("/x", dest, resume=True) == 200

    assert dest.read_bytes() == b"0123456789" and list(tmp_path.iterdir()) == [dest]
    assert served.requests == [(None, None), ("bytes=4-", '"v1"')]


def test_a_changed_file_is_downloaded_again_from_the_start(tmp_path, monkeypatch):
    served = Served(b"0123456789", cut_after=4)
    client = _client(monkeypatch, served)
    dest = tmp_path / "m.zip"
    with pytest.raises(RuntimeError):
        client.download_to("/x", dest, resume=True)

    served.etag, served.cut_after = '"v2"', None  # the server's zip was made again
    client.download_to("/x", dest, resume=True)

    assert dest.read_bytes() == b"0123456789"


def test_a_half_file_longer_than_the_server_has_is_dropped_and_the_file_fetched_whole(tmp_path, monkeypatch):
    served = Served(b"0123")
    client = _client(monkeypatch, served)
    dest = tmp_path / "m.zip"
    (tmp_path / "m.zip.part").write_bytes(b"x" * 20)
    (tmp_path / "m.zip.part.id").write_text('"v1"')

    client.download_to("/x", dest, resume=True)

    assert dest.read_bytes() == b"0123" and list(tmp_path.iterdir()) == [dest]


def test_a_cancelled_resumable_download_leaves_nothing_and_old_attempt_files_are_cleaned(tmp_path, monkeypatch):
    served = Served(b"0123456789")
    client = _client(monkeypatch, served)
    dest = tmp_path / "m.zip"
    (tmp_path / "m.zip.1a2b3c4d.part").write_bytes(b"left by an earlier version")

    def stop(written, total):
        if written >= 4:
            raise mods.ModCancelled("stop")

    with pytest.raises(mods.ModCancelled):
        client.download_to("/x", dest, resume=True, on_progress=stop)

    assert list(tmp_path.iterdir()) == []
