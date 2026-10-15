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


def test_the_password_is_not_sent_on_to_another_origin_by_a_redirect():
    seen = {}

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/x")
            self.end_headers()

        def log_message(self, *args):
            pass

    origin = HTTPServer(("127.0.0.1", 0), Origin)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    try:
        Client(f"127.0.0.1:{origin.server_port}", "ann", "secret").get_json("/api/games")
    finally:
        origin.shutdown()
        target.shutdown()

    assert seen["auth"] is None  # another port is another origin


@pytest.mark.parametrize(
    "base", ["https://mog.example.com", "http://192.168.1.42:5566", "localhost:5000", "http://127.0.0.1:5000", "http://videodrome:5566", "http://nas.local"]
)
def test_no_warning_for_https_or_a_server_at_home(base):
    from mog_client import net

    assert net.plain_http_warning(base) is None


@pytest.mark.parametrize("base", ["http://mog.example.com", "93.184.216.34:5000"])
def test_plain_http_over_the_internet_is_warned_about(base):
    from mog_client import net

    assert "unprotected" in net.plain_http_warning(base)
