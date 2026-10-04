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


def test_a_slow_transfer_does_not_hold_back_files_that_turn_up_meanwhile(tmp_path, monkeypatch):
    import time as _t

    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.05)
    started = {}

    class Client:
        polls = 0

        def download_workers(self):
            return 2

        def stream_manifest(self, game_id, session_id=None):
            Client.polls += 1
            files = [{"path": "big.bin", "size_bytes": 10, "sealed_bytes": 10, "complete": True}]
            if Client.polls >= 2:  # the server produced another file while big.bin is still downloading
                files.append({"path": "late.bin", "size_bytes": 10, "sealed_bytes": 10, "complete": True})
            return {"files": files}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            started[path] = _t.monotonic()
            _t.sleep(1.5 if path == "big.bin" else 0.01)
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    done = threading.Event()
    done.set()
    total, finished = download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)

    assert finished and total == 20
    assert started["late.bin"] - started["big.bin"] < 1.0  # it did not wait for big.bin to end


def test_a_failing_file_is_retried_later_without_stopping_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.02)
    warnings = []

    class Client:
        attempts = {"bad.bin": 0}

        def download_workers(self):
            return 2

        def stream_manifest(self, game_id, session_id=None):
            return {"files": [{"path": p, "size_bytes": 10, "sealed_bytes": 10, "complete": True} for p in ("bad.bin", "good.bin")]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            if path == "bad.bin":
                Client.attempts["bad.bin"] += 1
                if Client.attempts["bad.bin"] == 1:
                    raise RuntimeError("connection lost")
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    done = threading.Event()
    done.set()
    total, finished = download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=warnings.append, server_done=done)
    assert finished and total == 20 and warnings == ["connection lost"]


def test_connections_are_replaced_once_when_the_server_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.02)
    calls = []
    monkeypatch.setattr(transfer.net, "reset_connections", lambda *a, **k: calls.append(1))
    done = threading.Event()

    class Client:
        rounds = 0

        def download_workers(self):
            return 1

        def stream_manifest(self, game_id, session_id=None):
            Client.rounds += 1
            if Client.rounds == 3:
                done.set()  # the server's install ends while the download is under way
            return {"files": [{"path": "a.bin", "size_bytes": 10, "sealed_bytes": 10, "complete": True}]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            import time as _t

            _t.sleep(0.15)
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lambda m: None, warn=lambda m: None, server_done=done)
    assert calls == [1]


def test_a_stall_is_reported_and_traced(tmp_path, monkeypatch):
    import time as _t

    from mog_client import trace

    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.02)
    monkeypatch.setattr(transfer, "STALL_SECONDS", 0.1)
    warnings, lines = [], []

    class Client:
        def download_workers(self):
            return 1

        def stream_manifest(self, game_id, session_id=None):
            return {"files": [{"path": "slow.bin", "size_bytes": 10, "sealed_bytes": 10, "complete": True}]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            _t.sleep(0.6)  # no byte arrives for a while
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    done = threading.Event()
    done.set()
    download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lines.append, warn=warnings.append, server_done=done)

    assert any("no data for" in w and "slow.bin" in w for w in warnings)
    text = trace.trace_path().read_text()
    assert "start slow.bin" in text and "stalled" in text and "end slow.bin" in text
    assert "server finished: replacing the connections one by one" in lines


def test_files_under_paths_the_final_manifest_does_not_list_neither_stall_nor_linger(tmp_path, monkeypatch):
    """The install ended with a different layout (live paths lacked a folder the final ones have)."""
    monkeypatch.setattr(transfer, "MANIFEST_INTERVAL", 0.02)
    done = threading.Event()
    fetched = []

    class Client:
        polls = 0

        def download_workers(self):
            return 1  # one worker: queued ghost transfers would delay every real one

        def stream_manifest(self, game_id, session_id=None):
            Client.polls += 1
            if Client.polls < 3:  # the install is running: paths as the live scan sees them
                return {"files": [
                    {"path": f"Game/f{i}.bin", "size_bytes": 10, "sealed_bytes": 5, "complete": False} for i in range(30)
                ]}
            done.set()
            return {"files": [  # the install ended: the same files, one folder deeper
                {"path": f"Vendor/Game/f{i}.bin", "size_bytes": 10, "sealed_bytes": 10, "complete": True} for i in range(30)
            ]}

        def stream_file(self, game_id, path, out_dir, session_id=None, on_chunk=None, stop=None):
            import time as _t

            fetched.append(path)
            _t.sleep(0.01)
            if not path.startswith("Vendor/"):
                if on_chunk:
                    on_chunk(5)
                (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
                (out_dir / path).write_bytes(b"x" * 5)
                return 5
            (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / path).write_bytes(b"x" * 10)
            return 10

    lines = []
    total, finished = download_all_files(Client(), 1, tmp_path, threading.Event(), None, log=lines.append, warn=lambda m: None, server_done=done)

    assert finished and total == 300  # only the final files count
    assert not (tmp_path / "Game").exists()  # the early partial files and their folder are gone
    assert len(list((tmp_path / "Vendor" / "Game").glob("*.bin"))) == 30
    assert any("different file layout" in m for m in lines)
