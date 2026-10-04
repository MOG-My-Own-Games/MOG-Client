import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mog_client.api import Client, MogClient

PAYLOAD = bytes(range(256)) * 12000  # ~3 MB


def _make_handler(drop_after: int | None, honor_range: bool):
    state = {"drops": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            start = 0
            rng = self.headers.get("Range")
            if rng and honor_range:
                start = int(rng.split("=")[1].split("-")[0])
            body = PAYLOAD[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if drop_after is not None and state["drops"] == 0:
                state["drops"] += 1
                self.wfile.write(body[:drop_after])
                self.wfile.flush()
                self.connection.close()  # the link dies mid-body
                return
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_dropped_connection_keeps_what_arrived_and_resumes(tmp_path):
    httpd = _serve(_make_handler(drop_after=700_000, honor_range=True))
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    seen = []
    with pytest.raises(RuntimeError, match="connection lost"):
        client.stream_file(1, "game.bin", tmp_path, on_chunk=seen.append)
    partial = (tmp_path / "game.bin").stat().st_size
    assert 0 < partial < len(PAYLOAD) and seen and seen[-1] == partial

    assert client.stream_file(1, "game.bin", tmp_path) == len(PAYLOAD)
    assert (tmp_path / "game.bin").read_bytes() == PAYLOAD
    httpd.shutdown()


def test_server_ignoring_range_restarts_instead_of_corrupting(tmp_path):
    httpd = _serve(_make_handler(drop_after=None, honor_range=False))
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    (tmp_path / "game.bin").write_bytes(PAYLOAD[:1000])
    assert client.stream_file(1, "game.bin", tmp_path) == len(PAYLOAD)
    assert (tmp_path / "game.bin").read_bytes() == PAYLOAD
    httpd.shutdown()


def test_unreachable_server_is_a_clear_error():
    client = MogClient(Client("http://127.0.0.1:1", "u", "p", timeout=1))
    with pytest.raises(RuntimeError, match="connection error"):
        client.c.get_json("/api/games")


def test_stop_interrupts_a_download_between_chunks(tmp_path):
    import threading as _t

    httpd = _serve(_make_handler(drop_after=None, honor_range=True))
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    stop = _t.Event()
    seen = []

    def on_chunk(written):
        seen.append(written)
        stop.set()

    got = client.stream_file(1, "game.bin", tmp_path, on_chunk=on_chunk, stop=stop)

    assert got == (tmp_path / "game.bin").stat().st_size
    assert len(seen) == 1 and got < len(PAYLOAD)
    httpd.shutdown()


def test_files_are_fetched_over_one_persistent_connection(tmp_path):
    connections = []

    class Keepalive(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            connections.append(1)
            super().setup()

        def do_GET(self):
            body = b"data" * 1000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = _serve(Keepalive)
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    for i in range(5):
        assert client.stream_file(1, f"f{i}.bin", tmp_path) == 4000
    assert len(connections) == 1
    httpd.shutdown()


def test_a_connection_left_idle_is_not_reused(tmp_path, monkeypatch):
    from mog_client import net

    connections = []

    class Keepalive(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            connections.append(1)
            super().setup()

        def do_GET(self):
            body = b"data" * 1000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = _serve(Keepalive)
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    now = {"t": 1000.0}
    monkeypatch.setattr(net.time, "monotonic", lambda: now["t"])

    client.stream_file(1, "a.bin", tmp_path)
    now["t"] += 1  # a second later: the same connection is fine
    client.stream_file(1, "b.bin", tmp_path)
    assert len(connections) == 1
    now["t"] += net.MAX_IDLE_SECONDS + 1  # left alone long enough that a proxy may have dropped it
    client.stream_file(1, "c.bin", tmp_path)
    assert len(connections) == 2
    httpd.shutdown()


def test_resetting_connections_frees_a_transfer_stuck_on_a_dead_one(tmp_path):
    import time as _t

    from mog_client import net

    payload = bytes(range(256)) * 16  # 4096 bytes
    state = {"requests": 0}

    class Stalls(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            state["requests"] += 1
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
            body = payload[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if state["requests"] == 1:
                self.wfile.write(body[:1000])
                self.wfile.flush()
                _t.sleep(20)  # the peer goes quiet mid-body, as a dropped connection does
                return
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = _serve(Stalls)
    client = MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    result = {}
    worker = threading.Thread(target=lambda: result.update(size=client.stream_file(1, "big.bin", tmp_path)), daemon=True)
    started = _t.monotonic()
    worker.start()
    _t.sleep(0.5)  # it is now waiting for the rest of the body
    assert worker.is_alive()

    net.reset_connections(interval=0.05)
    worker.join(timeout=5)

    assert not worker.is_alive() and _t.monotonic() - started < 8
    assert result["size"] == len(payload) and (tmp_path / "big.bin").read_bytes() == payload
    assert state["requests"] == 2  # resumed with a Range request on a new connection
    httpd.shutdown()
