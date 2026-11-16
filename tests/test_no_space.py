"""A full disk pauses the download (and says so) instead of retrying for hours; and an install that will not fit is
announced before it starts."""

import errno
import threading
from types import SimpleNamespace

from mog_client import spacecheck, transfer
from mog_client.api import Client, MogClient
from mog_client.transfer import download_all_files

# --- the download waits for room ---


class _Disk:
    """A client whose file fails with a full disk until `room` is set, then goes through."""

    def __init__(self):
        self.room = False
        self.calls = 0

    def download_workers(self):
        return 1

    def stream_manifest(self, game_id, session_id=None):
        return {"files": [{"path": "a.bin", "size_bytes": 1000, "complete": True}]}

    def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
        self.calls += 1
        if not self.room:
            raise OSError(errno.ENOSPC, "No space left on device")
        (out_dir / path).write_bytes(b"x" * 1000)
        return 1000


def test_a_full_disk_pauses_the_download_and_it_goes_on_when_room_is_back(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    monkeypatch.setattr(transfer, "NO_SPACE_POLL", 0.01)
    client = _Disk()
    events, warned = [], []
    done = threading.Event()
    done.set()

    def on_space(short):
        events.append(short)
        if short:  # the person freed some space
            client.room = True

    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _p: SimpleNamespace(free=transfer.NO_SPACE_RESUME))
    total, finished = download_all_files(
        client, 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=warned.append,
        server_done=done, on_space=on_space,
    )

    assert finished and total == 1000
    assert events == [True, False]  # paused once, then released once
    assert client.calls == 2  # not asked again while there was no room
    assert sum("no space left" in w for w in warned) == 1  # said once, not at every look


def test_nothing_is_asked_while_the_disk_stays_full(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    monkeypatch.setattr(transfer, "NO_SPACE_POLL", 0.01)
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _p: SimpleNamespace(free=0))
    client = _Disk()
    stop = threading.Event()
    done = threading.Event()
    done.set()
    paused = threading.Event()

    def on_space(short):
        if short:
            paused.set()

    worker = threading.Thread(
        target=download_all_files,
        args=(client, 1, tmp_path, stop, None),
        kwargs={"log": lambda m: None, "warn": lambda m: None, "server_done": done, "on_space": on_space},
        daemon=True,
    )
    worker.start()
    assert paused.wait(5)
    calls = client.calls
    stop.wait(0.2)
    assert client.calls == calls  # waiting, not hammering the server
    stop.set()
    worker.join(5)
    assert not worker.is_alive()


def test_a_full_disk_while_writing_reaches_the_caller_as_it_is(tmp_path, monkeypatch):
    class Resp:
        status = 200
        headers = {"Content-Length": "10"}

        def read1(self, _n):
            return b"x" * 10

    class Failing:
        def __enter__(self):
            raise OSError(errno.ENOSPC, "No space left on device")

        def __exit__(self, *_a):
            return False

    from mog_client import api, net

    client = MogClient(Client("http://server:5000", "u", "p"))
    monkeypatch.setattr(net, "pooled_request", lambda *a, **k: Resp())
    monkeypatch.setattr(net, "discard", lambda _url: None)
    monkeypatch.setattr("builtins.open", lambda *a, **k: Failing())
    try:
        client.stream_file(1, "a.bin", tmp_path, session_id=1)
    except OSError as e:
        assert e.errno == errno.ENOSPC  # not a RuntimeError("connection lost ..."): the caller can tell
    else:
        raise AssertionError("the full disk was swallowed")
    assert api  # the module under test


# --- will it fit ---


def test_a_finished_cache_is_what_the_install_takes():
    assert spacecheck.estimate({"state": "done", "bytes_total": 71_000}, 5_000) == (71_000, spacecheck.BASIS_CACHE)


def test_without_a_finished_cache_the_installer_weight_is_used_unless_the_cache_so_far_is_bigger():
    assert spacecheck.estimate(None, 5_000) == (5_000, spacecheck.BASIS_INSTALLER)
    assert spacecheck.estimate({"state": "installing", "bytes_total": 2_000}, 5_000) == (5_000, spacecheck.BASIS_INSTALLER)
    assert spacecheck.estimate({"state": "installing", "bytes_total": 9_000}, 5_000) == (9_000, spacecheck.BASIS_INSTALLER)
    assert spacecheck.estimate({"state": "done", "bytes_total": 0}, 5_000) == (5_000, spacecheck.BASIS_INSTALLER)
    assert spacecheck.estimate(None, None)[0] == 0


def test_what_is_already_downloaded_is_not_counted_again_and_nothing_known_is_no_shortage():
    assert spacecheck.shortage(100, "cache", 0, 99).missing == 100
    assert spacecheck.shortage(100, "cache", 0, 100) is None  # fits exactly
    assert spacecheck.shortage(100, "cache", 60, 40) is None  # a resume only needs the rest
    assert spacecheck.shortage(100, "cache", 60, 39).missing == 40
    assert spacecheck.shortage(0, "installer", 0, 0) is None


def test_the_files_already_on_disk_are_counted(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"x" * 30)
    (tmp_path / "y.bin").write_bytes(b"x" * 12)
    assert spacecheck.local_bytes(tmp_path) == 42
    assert spacecheck.local_bytes(tmp_path / "missing") == 0


def test_a_disk_with_room_is_not_walked_and_the_wine_prefix_is_never_counted(tmp_path, monkeypatch):
    (tmp_path / "pfx" / "drive_c").mkdir(parents=True)
    (tmp_path / "pfx" / "drive_c" / "junk.dll").write_bytes(b"x" * 900)
    (tmp_path / "game.bin").write_bytes(b"x" * 40)
    assert spacecheck.local_bytes(tmp_path) == 40  # the prefix is not what a resume downloads

    class Client:
        def get_session(self, game_id):
            return {"state": "done", "bytes_total": 500}

    walked = []
    monkeypatch.setattr(spacecheck, "local_bytes", lambda folder: walked.append(folder) or 40)
    monkeypatch.setattr(spacecheck.installdirs, "free_bytes", lambda _folder: 1_000)
    assert spacecheck.check(Client(), 1, tmp_path) is None and walked == []  # it fits: no file of the folder is read

    monkeypatch.setattr(spacecheck.installdirs, "free_bytes", lambda _folder: 100)
    assert spacecheck.check(Client(), 1, tmp_path).have == 40 and walked == [tmp_path]  # only when it might not fit


def test_check_reads_the_session_the_installer_and_the_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(spacecheck.installdirs, "free_bytes", lambda _folder: 1_000)
    (tmp_path / "part.bin").write_bytes(b"x" * 500)

    class Client:
        def get_session(self, game_id):
            return {"state": "done", "bytes_total": 5_000}

        def game_size(self, game_id):
            raise AssertionError("a finished cache makes the folder size useless")

    found = spacecheck.check(Client(), 1, tmp_path)
    assert (found.needed, found.have, found.free, found.basis) == (5_000, 500, 1_000, spacecheck.BASIS_CACHE)

    class NoCache(Client):
        def get_session(self, game_id):
            raise RuntimeError("no session")

        def game_size(self, game_id):
            return 3_000

    found = spacecheck.check(NoCache(), 1, tmp_path)
    assert (found.needed, found.basis) == (3_000, spacecheck.BASIS_INSTALLER)
    assert spacecheck.check(NoCache(), 1, tmp_path, installer={"file_size_bytes": 100}) is None  # fits: 100 - 500 on disk
