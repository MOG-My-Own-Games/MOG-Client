import threading

from mog_client import transfer
from mog_client.transfer import download_all_files


class _Fake:
    """A client whose first attempt at the file drops and whose second restarts from zero."""

    def __init__(self, size):
        self.size, self.calls = size, 0

    def download_workers(self):
        return 3

    def stream_manifest(self, game_id, session_id=None):
        return {"files": [{"path": "a.bin", "size_bytes": self.size, "complete": True}]}

    def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
        self.calls += 1
        if self.calls == 1:
            on_chunk(self.size // 2)
            (out_dir / path).write_bytes(b"x" * (self.size // 2))
            raise RuntimeError("connection lost")
        for written in (100, self.size // 2, self.size):  # restarted: counts from zero again
            on_chunk(written)
        (out_dir / path).write_bytes(b"x" * self.size)
        return self.size


def test_total_never_goes_negative_when_a_file_restarts(tmp_path, monkeypatch):
    seen = []
    client = _Fake(1000)
    stop = threading.Event()
    done = threading.Event()
    done.set()
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    total, finished = download_all_files(
        client, 1, tmp_path, stop, None, log=lambda m: None, warn=lambda m: None,
        on_bytes=lambda written, known: seen.append((written, known)), server_done=done,
    )
    assert finished and total == 1000
    assert all(w >= 0 for w, _ in seen)


def test_a_growing_file_that_is_caught_up_is_not_requested_again(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    (tmp_path / "grow.bin").write_bytes(b"x" * 500)

    class Client:
        calls = 0
        rounds = 0

        def download_workers(self):
            return 2

        def stream_manifest(self, game_id, session_id=None):
            Client.rounds += 1
            complete = Client.rounds >= 3
            return {"files": [{"path": "grow.bin", "size_bytes": 800, "sealed_bytes": 500 if not complete else 800, "complete": complete}]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            Client.calls += 1
            (out_dir / path).write_bytes(b"x" * 800)
            return 800

    done = threading.Event()
    done.set()
    total, finished = download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)
    assert finished and total == 800
    assert Client.calls == 1  # rounds 1 and 2 had nothing new sealed, so no request


def test_workers_are_whatever_the_server_says(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class Client:
        def download_workers(self):
            return 2

        def stream_manifest(self, game_id, session_id=None):
            return {"files": [{"path": f"f{i}", "size_bytes": 10, "sealed_bytes": 10, "complete": True} for i in range(6)]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            import time as _t

            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            _t.sleep(0.05)
            with lock:
                live["now"] -= 1
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    done = threading.Event()
    done.set()
    download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)
    assert live["peak"] == 2


def test_a_server_that_does_not_say_gets_one_file_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.01)
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class Client:
        def download_workers(self):
            return None  # an older server

        def stream_manifest(self, game_id, session_id=None):
            return {"files": [{"path": f"f{i}", "size_bytes": 10, "sealed_bytes": 10, "complete": True} for i in range(4)]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            import time as _t

            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            _t.sleep(0.03)
            with lock:
                live["now"] -= 1
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    done = threading.Event()
    done.set()
    download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)
    assert live["peak"] == 1
