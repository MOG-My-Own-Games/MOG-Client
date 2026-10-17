import hashlib
import threading
from types import SimpleNamespace

import pytest

from mog_client import manager
from mog_client.config import InstalledGame
from mog_client.transfer import verify_and_repair


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Server:
    """Holds a finished install whose files are `files`; `download_file` hands them out."""

    def __init__(self, files: dict[str, bytes], state: str = "done"):
        self.files, self.state, self.downloaded = files, state, []

    def get_session(self, gid):
        return {"id": 7, "state": self.state} if self.state else {}

    def list_files(self, gid, session_id=None):
        return {"files": [{"path": p, "size_bytes": len(d), "sha1": sha(d)} for p, d in self.files.items()]}

    def download_file(self, gid, path, session_id=None):
        self.downloaded.append(path)
        return self.files[path]


def test_a_wrong_and_a_missing_file_are_fetched_again_and_a_right_one_is_left(tmp_path):
    server = Server({"a.bin": b"right", "sub/b.bin": b"wanted", "c.bin": b"fine"})
    (tmp_path / "a.bin").write_bytes(b"broken")
    (tmp_path / "c.bin").write_bytes(b"fine")

    result = verify_and_repair(server, 1, tmp_path, 7, log=lambda t: None, warn=lambda t: None, fetch_missing=True)

    assert (result.checked, result.repaired, result.failed) == (3, 2, [])
    assert sorted(server.downloaded) == ["a.bin", "sub/b.bin"]
    assert (tmp_path / "a.bin").read_bytes() == b"right" and (tmp_path / "sub/b.bin").read_bytes() == b"wanted"
    assert not list(tmp_path.rglob("*.mog-part"))


def test_a_missing_file_is_only_fetched_when_asked(tmp_path):
    server = Server({"a.bin": b"x"})
    result = verify_and_repair(server, 1, tmp_path, 7, log=lambda t: None, warn=lambda t: None)
    assert result.checked == 0 and server.downloaded == []


def test_a_file_that_stays_wrong_is_reported(tmp_path):
    server = Server({"a.bin": b"right"})
    server.download_file = lambda gid, path, session_id=None: b"still wrong"
    result = verify_and_repair(server, 1, tmp_path, 7, log=lambda t: None, warn=lambda t: None, fetch_missing=True)
    assert result.failed == ["a.bin"] and result.repaired == 0


def test_progress_is_reported_and_a_stop_ends_the_check(tmp_path):
    server = Server({"a": b"1", "b": b"2", "c": b"3"})
    seen, stop = [], threading.Event()

    def progress(done, total):
        seen.append((done, total))
        if done == 1:
            stop.set()

    result = verify_and_repair(
        server, 1, tmp_path, 7, log=lambda t: None, warn=lambda t: None, fetch_missing=True, on_progress=progress, stop=stop
    )
    assert result.stopped and seen == [(0, 3), (1, 3)] and server.downloaded == ["a", "b"]


def rec(tmp_path, **kw):
    return InstalledGame(game_id=4, name="G", install_dir=str(tmp_path), **kw)


def test_repair_checks_the_games_own_folder(tmp_path):
    (tmp_path / "G").mkdir()
    server = Server({"x.bin": b"data"})
    result = manager.repair(rec(tmp_path, files_dir=str(tmp_path / "G")), server, threading.Event(), lambda t: None)
    assert result.repaired == 1 and (tmp_path / "G" / "x.bin").read_bytes() == b"data"


@pytest.mark.parametrize("state", ["", "failed", "expired"])
def test_repair_needs_the_server_to_still_hold_the_install(tmp_path, state):
    with pytest.raises(RuntimeError, match="no longer holds"):
        manager.repair(rec(tmp_path), Server({}, state), threading.Event(), lambda t: None)


def test_repair_says_when_the_list_is_not_to_be_had(tmp_path):
    server = Server({})

    def refuse(gid, session_id=None):
        raise RuntimeError("HTTP 500")

    server.list_files = refuse
    with pytest.raises(RuntimeError, match="could not list"):
        manager.repair(rec(tmp_path), server, threading.Event(), lambda t: None)


class Regenerating(Server):
    """A server whose cache is gone; `start_session` makes it again, in the states `states` (the last one ends the wait)."""

    def __init__(self, states, refuse_first=0, error=None):
        super().__init__({}, "")
        self.states, self.started, self.refuse, self.error = list(states), [], refuse_first, error
        self.c = SimpleNamespace(base="http://server")

    def start_session(self, gid, installer_path, proton, ttl, **kw):
        if self.refuse:
            self.refuse -= 1
            raise RuntimeError("HTTP 429: Too many concurrent installs")
        self.started.append((gid, installer_path, kw))
        self.state = self.states[0]
        return {"id": 7, "state": self.states[0], "vnc_url": "/vnc"}

    def get_session(self, gid, session_id=None):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        self.state = state
        return {"id": 7, "state": state, "error": self.error} if state else {}


def test_the_install_is_said_to_be_held_only_while_a_finished_session_is(tmp_path):
    assert manager.holds_install(Server({}, "done"), 1) is True
    assert manager.holds_install(Server({}, "failed"), 1) is False and manager.holds_install(Server({}, ""), 1) is False


def test_the_cache_is_made_again_with_the_installers_the_server_has_saved_and_nothing_is_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr("mog_client.transfer.POLL_INTERVAL", 0)
    server = Regenerating(["installing", "streaming", "done"])

    assert manager.regenerate_cache(rec(tmp_path), server, threading.Event(), lambda t: None) is True

    assert server.started == [(4, None, {"extract_only": None})] and server.downloaded == []  # no installer named: the saved order is used


def test_a_busy_server_is_asked_again_and_a_stop_ends_the_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "QUEUE_RETRY_SECONDS", 0)
    server = Regenerating(["done"], refuse_first=1)
    seen = []
    assert manager.regenerate_cache(rec(tmp_path), server, threading.Event(), lambda t: None, seen.append) is True
    assert seen[0] == {"state": "queued"}

    stop = threading.Event()
    stop.set()
    assert manager.regenerate_cache(rec(tmp_path), Regenerating(["done"], refuse_first=5), stop, lambda t: None) is False


def test_an_install_that_needs_a_hand_or_fails_is_an_error_not_a_check(tmp_path, monkeypatch):
    monkeypatch.setattr("mog_client.transfer.POLL_INTERVAL", 0)
    with pytest.raises(RuntimeError, match="manual installer pick, open http://server/vnc"):
        manager.regenerate_cache(rec(tmp_path), Regenerating(["awaiting_installer"]), threading.Event(), lambda t: None)
    with pytest.raises(RuntimeError, match="could not make the install again: wine crashed"):
        manager.regenerate_cache(rec(tmp_path), Regenerating(["installing", "failed"], error="wine crashed"), threading.Event(), lambda t: None)
