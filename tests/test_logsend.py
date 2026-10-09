from types import SimpleNamespace

import pytest

from mog_client import config, logsend
from mog_client.config import Settings


class Server:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def upload_device_log(self, device_id, text):
        self.sent.append((device_id, text))
        return self.ok


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(logsend, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(logsend, "_last_sent", 0.0)
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "client.log").write_text("newer line\n")
    (folder / "client.log.1").write_text("older line\n")
    (folder / "launch-5.log").write_text("game output\n")
    return folder


def test_the_log_holds_the_client_log_oldest_first_and_the_games_own(logs):
    text = logsend.collect(5)

    assert text.index("older line") < text.index("newer line") < text.index("game output")
    assert "--- launch-5.log ---" in text and "MOG Client" in text.splitlines()[0]
    assert "launch-" not in logsend.collect(None)


def test_a_long_log_is_cut_at_a_line_keeping_the_newest(logs, monkeypatch):
    monkeypatch.setattr(logsend, "CLIENT_LOG_BYTES", 30)
    (logs / "client.log").write_text("".join(f"line {n:02d}\n" for n in range(20)))
    (logs / "client.log.1").unlink()

    text = logsend.collect()

    assert "line 19" in text and "line 00" not in text
    assert all(line.startswith("line ") or line.startswith(("MOG", "---")) for line in text.splitlines() if line)


def test_nothing_is_sent_unless_the_user_allowed_it(logs):
    server = Server()
    assert logsend.send(server, 3, Settings(upload_logs=False), 5) is False
    assert server.sent == []

    assert logsend.send(server, 3, Settings(upload_logs=True), 5) is True
    assert server.sent[0][0] == 3 and "game output" in server.sent[0][1]


def test_a_second_send_within_the_gap_is_skipped_unless_forced(logs):
    server, settings = Server(), Settings(upload_logs=True)
    assert logsend.send(server, 3, settings) is True
    assert logsend.send(server, 3, settings) is False
    assert logsend.send(server, 3, settings, force=True) is True
    assert len(server.sent) == 2


def test_a_server_that_fails_never_breaks_the_caller(logs):
    class Broken:
        def upload_device_log(self, *args):
            raise OSError("down")

    assert logsend.send(Broken(), 3, Settings(upload_logs=True)) is False
    assert logsend.send(Server(ok=False), 3, Settings(upload_logs=True), force=True) is False
