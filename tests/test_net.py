import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mog_client.api import Client, normalize_base


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(301)
            self.send_header("Location", "https://example.invalid/api/games")
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_bare_host_means_plain_http():
    assert normalize_base("192.168.1.5:5000/") == "http://192.168.1.5:5000"
    assert normalize_base(" https://mog.example ") == "https://mog.example"
    assert normalize_base("") == ""


def test_plain_http_server_works_with_or_without_scheme(server):
    for base in (f"http://{server}", server):
        assert Client(base, "u", "p").get_json("/api/games") == (200, {"ok": True})


def test_http_to_https_redirect_is_reported_not_followed(server):
    with pytest.raises(RuntimeError, match="redirects to https"):
        Client(f"http://{server}", "u", "p").get_json("/redirect")
