import http.server
import json
import threading
import zipfile

import pytest

from mog_client.api import Client, MogClient


class Handler(http.server.BaseHTTPRequestHandler):
    seen: dict = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.seen = {"path": self.path, "type": self.headers["Content-Type"], "auth": self.headers["Authorization"], "body": body}
        reply = json.dumps({"version": {"id": 9}, "created": True}).encode()
        self.send_response(200 if "device_id=1" in self.path else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def do_GET(self):
        if self.path.endswith("/missing/download"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = b"zip bytes " * 200000
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield MogClient(Client(f"http://127.0.0.1:{httpd.server_port}", "u", "p"))
    httpd.shutdown()


def test_a_save_is_uploaded_as_a_multipart_file_with_its_exact_length(server, tmp_path):
    archive = tmp_path / "save.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("game/a.sav", b"x" * 5000)

    result = server.upload_save(5, 1, archive, "quit")

    assert result["created"] is True
    seen = Handler.seen
    assert seen["path"] == "/api/games/5/saves?device_id=1&trigger=quit" and seen["auth"].startswith("Basic ")
    boundary = seen["type"].split("boundary=")[1]
    assert seen["body"].startswith(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="save.zip"'.encode())
    assert seen["body"].endswith(f"\r\n--{boundary}--\r\n".encode())
    assert archive.read_bytes() in seen["body"]


def test_an_upload_the_server_refuses_is_an_error(server, tmp_path):
    archive = tmp_path / "save.zip"
    archive.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="404"):
        server.upload_save(5, 2, archive, "quit")


def test_a_version_is_downloaded_to_a_file_and_a_missing_one_leaves_nothing(server, tmp_path):
    dest = tmp_path / "out" / "v.zip"
    server.download_save(3, dest)
    assert dest.read_bytes() == b"zip bytes " * 200000 and not list(dest.parent.glob("*.part"))

    with pytest.raises(RuntimeError, match="404"):
        server.download_save("missing", tmp_path / "out" / "nope.zip")
    assert not (tmp_path / "out" / "nope.zip").exists() and not list(dest.parent.glob("*.part"))
