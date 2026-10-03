import threading

from mog_client import transfer
from mog_client.transfer import download_all_files


class _Fake:
    """A client whose first attempt at the file drops and whose second restarts from zero."""

    def __init__(self, size):
        self.size, self.calls = size, 0

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
