"""Files the installer makes for itself and then deletes (a decompressor's gigabytes of scratch space) cost the client
disk and bandwidth for nothing: they are fetched last, and dropped at once, partial or not, when the server says they
are gone."""

import threading
import time

import pytest

from mog_client import transfer
from mog_client.transfer import download_all_files, is_scratch


@pytest.mark.parametrize(
    "path, scratch",
    [
        ("Game/srep-virtual-memory.tmp", True),
        ("Game/freearc123.TMP", True),
        ("Game/cache.temp", True),
        ("Game/x.part", True),
        ("Game/backup~", True),
        ("Game/Temp/blob.bin", True),
        ("Game/tmp/blob.bin", True),
        ("Game/data/pak.bin", False),
        ("Game/attempt.bin", False),
        ("Game/template.dat", False),
        ("Game/Temperature.exe", False),
    ],
)
def test_what_looks_like_a_temporary_file(path, scratch):
    assert is_scratch(path) is scratch


class _Client:
    def __init__(self, manifests):
        self.manifests = list(manifests)
        self.order = []

    def download_workers(self):
        return 1

    def stream_manifest(self, game_id, session_id=None):
        return self.manifests.pop(0) if len(self.manifests) > 1 else self.manifests[0]

    def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
        self.order.append(path)
        (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / path).write_bytes(b"x" * 10)
        return 10


def _file(path, size=10):
    return {"path": path, "size_bytes": size, "sealed_bytes": size, "complete": True}


def _run(client, out_dir, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    done = threading.Event()
    done.set()
    return download_all_files(
        client, 1, out_dir, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done
    )


def test_temporary_files_are_fetched_after_the_others(tmp_path, monkeypatch):
    files = [_file("g/a.tmp"), _file("g/data1.bin"), _file("g/Temp/b.bin"), _file("g/data2.bin"), _file("g/z.exe")]
    client = _Client([{"files": files}])

    total, finished = _run(client, tmp_path, monkeypatch)

    assert finished and total == 50
    assert client.order[:3] == ["g/data1.bin", "g/data2.bin", "g/z.exe"]  # the server's order among the rest
    assert sorted(client.order[3:]) == ["g/Temp/b.bin", "g/a.tmp"]


def test_a_partial_file_the_installer_deleted_is_removed_and_not_asked_for_again(tmp_path, monkeypatch):
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "scratch.tmp").write_bytes(b"x" * 7)  # a part of it came before the installer deleted it
    (tmp_path / "g" / "other.bin").write_bytes(b"y")
    client = _Client([{"files": [_file("g/a.bin")], "removed": ["g/scratch.tmp"]}])

    total, finished = _run(client, tmp_path, monkeypatch)

    assert finished
    assert not (tmp_path / "g" / "scratch.tmp").exists()
    assert (tmp_path / "g" / "other.bin").exists()  # only what the server named
    assert client.order == ["g/a.bin"]  # never asked for


def test_a_queued_transfer_the_installer_deletes_before_it_starts_never_starts(tmp_path, monkeypatch):
    listed = {"files": [_file("g/a.bin"), _file("g/b.bin"), _file("g/scratch.tmp", 100)]}
    gone = {"files": [_file("g/a.bin"), _file("g/b.bin")], "removed": ["g/scratch.tmp"]}
    release = threading.Event()

    class Held(_Client):
        def stream_manifest(self, game_id, session_id=None):
            manifest = super().stream_manifest(game_id, session_id)
            if "removed" in manifest:  # the worker is let go only once the client has been told
                release.set()
            return manifest

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            release.wait(5)  # the single worker is busy: the scratch file waits in the queue
            return super().stream_file(game_id, path, out_dir, session_id, on_chunk, stop)

    client = Held([listed, gone])

    _, finished = _run(client, tmp_path, monkeypatch)

    assert finished and "g/scratch.tmp" not in client.order


def test_a_file_being_fetched_when_the_installer_deletes_it_is_stopped_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    started = threading.Event()

    class Slow(_Client):
        def stream_manifest(self, game_id, session_id=None):
            if not started.is_set():
                return {"files": [_file("g/big.tmp", 10**9)]}
            return {"files": [_file("g/a.bin")], "removed": ["g/big.tmp"]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            if path != "g/big.tmp":
                return super().stream_file(game_id, path, out_dir, session_id, on_chunk, stop)
            (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / path).write_bytes(b"x" * 500)  # some of it is already on disk
            started.set()
            while not stop.is_set():  # the way a real transfer ends between two chunks
                time.sleep(0.005)
            return 500

    client = Slow([])
    done = threading.Event()
    done.set()
    box = {}
    worker = threading.Thread(
        target=lambda: box.update(
            r=download_all_files(client, 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)
        ),
        daemon=True,
    )
    worker.start()
    worker.join(10)

    assert not worker.is_alive()
    assert box["r"][1] is True  # finished: it went on to the real files
    assert not (tmp_path / "g" / "big.tmp").exists()
    assert (tmp_path / "g" / "a.bin").exists()


def test_a_removed_path_that_leaves_the_install_folder_is_ignored(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("mine")
    install = tmp_path / "install"
    install.mkdir()
    manifest = {"files": [_file("g/a.bin")], "removed": ["../outside.txt", "/etc/hostname", 5]}

    _, finished = _run(_Client([manifest]), install, monkeypatch)

    assert finished and outside.read_text() == "mine"


def test_a_deleted_file_the_list_keeps_repeating_is_not_looked_for_on_disk_again(tmp_path, monkeypatch):
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "scratch.tmp").write_bytes(b"x")
    asked = []
    real = transfer.Path.is_file
    monkeypatch.setattr(transfer.Path, "is_file", lambda self: asked.append(self.name) or real(self))
    manifest = {"files": [_file("g/a.bin")], "removed": ["g/scratch.tmp"]}
    polls = [0]

    class Client(_Client):
        def stream_manifest(self, game_id, session_id=None):
            polls[0] += 1
            return manifest if polls[0] < 6 else {**manifest, "files": [_file("g/a.bin"), _file("g/later.bin")]}

    client = Client([manifest])
    _run(client, tmp_path, monkeypatch)

    assert not (tmp_path / "g" / "scratch.tmp").exists()
    assert asked.count("scratch.tmp") <= 1  # looked at when it was dropped, not at every poll after it
