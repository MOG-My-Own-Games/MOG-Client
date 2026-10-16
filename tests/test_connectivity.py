import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mog_client import api, connectivity


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status": "ok"}'
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


@pytest.fixture
def closed_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"  # nothing listens there once the socket is closed


def test_a_server_that_answers_its_health_check_is_online(server):
    assert connectivity.probe(server) == connectivity.ONLINE
    assert not connectivity.is_offline()


def test_a_server_out_of_reach_with_the_internet_up_is_no_server(closed_port, monkeypatch):
    monkeypatch.setattr(connectivity, "_reaches_internet", lambda: True)
    assert connectivity.probe(closed_port) == connectivity.NO_SERVER
    assert connectivity.is_offline() and connectivity.internet_ok()


def test_nothing_reachable_is_no_internet_and_downloads_are_not_tried(closed_port, monkeypatch):
    monkeypatch.setattr(connectivity, "_reaches_internet", lambda: False)
    assert connectivity.probe(closed_port) == connectivity.NO_INTERNET
    assert not connectivity.internet_ok()


def test_probe_does_not_leave_offline_when_the_user_forced_it(server):
    connectivity.set_forced(True)
    assert connectivity.probe(server) == connectivity.ONLINE  # what it found
    assert connectivity.is_offline() and connectivity.forced()


def test_listeners_hear_only_of_changes(closed_port, monkeypatch):
    monkeypatch.setattr(connectivity, "_reaches_internet", lambda: True)
    heard = []
    connectivity.subscribe(lambda state, forced: heard.append((state, forced)))
    connectivity.probe(closed_port)
    connectivity.probe(closed_port)
    connectivity.set_forced(True)
    assert heard == [(connectivity.NO_SERVER, False), (connectivity.NO_SERVER, True)]


def test_a_failed_request_marks_the_server_unreachable_only_while_tracking(closed_port):
    client = api.Client(closed_port, "u", "p", timeout=1)
    with pytest.raises(RuntimeError, match="connection error"):
        client.post_json("/api/x", {})  # a POST is not retried
    assert connectivity.state() == connectivity.ONLINE  # the CLI's way: nothing is tracked
    connectivity.enable()
    with pytest.raises(RuntimeError, match="connection error"):
        client.post_json("/api/x", {})
    assert connectivity.state() == connectivity.NO_SERVER


def test_requests_fail_at_once_while_offline_and_no_connection_is_made(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a connection was tried")

    monkeypatch.setattr(api.net, "urlopen", refuse)
    connectivity.set_forced(True)
    with pytest.raises(RuntimeError, match="offline"):
        api.Client("127.0.0.1:1", "u", "p").get_json("/api/games")
    with pytest.raises(RuntimeError, match="offline"):
        api.MogClient(api.Client("127.0.0.1:1", "u", "p")).me()


def test_a_call_that_tests_the_server_goes_through_while_offline(server):
    connectivity.set_forced(True)
    status, _ = api.Client(server, "u", "p", offline_aware=False).get_json("/api/health")
    assert status == 200
