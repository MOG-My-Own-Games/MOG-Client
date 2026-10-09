import pytest
from types import SimpleNamespace

from mog_client import config, manager
from mog_client.api import MogClient
from mog_client.config import InstalledGame, Settings


class Posts:
    """A Client whose responses are canned, recording what is asked."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_json(self, path):
        self.calls.append(("GET", path))
        return 200, self.routes[path]

    def post_json(self, path, body):
        self.calls.append(("POST", path, body))
        return 200, {"id": 5, "state": "installing"}


ARCHIVE = {"path": "Game [v1].rar", "file_name": "Game [v1].rar", "kind": "archive", "category": "game"}
SOURCE = "/api/games/3/install/candidates?source=Game%20%5Bv1%5D.rar"
DEFAULT = "/api/games/3/install/candidates"


def client(inside):
    return MogClient(Posts({DEFAULT: {"candidates": [ARCHIVE]}, SOURCE: inside}))


def test_a_game_that_is_an_archive_with_no_installer_is_named():
    assert client({"candidates": [], "extract_suggested": True}).portable_archive(3) == "Game [v1].rar"


def test_the_listing_of_the_archive_is_asked_for_by_its_path():
    mog = client({"candidates": [], "extract_suggested": True})
    mog.portable_archive(3)
    assert mog.c.calls == [("GET", DEFAULT), ("GET", SOURCE)]


def test_an_archive_with_an_installer_is_not_named():
    assert client({"candidates": [{"path": "setup.exe"}], "extract_suggested": False}).portable_archive(3) is None


def test_an_older_server_that_does_not_say_is_taken_as_not():
    assert client({"candidates": []}).portable_archive(3) is None


def test_an_installer_the_user_picked_is_looked_at_instead_of_the_default():
    mog = client({"candidates": [], "extract_suggested": True})
    assert mog.portable_archive(3, ARCHIVE) == "Game [v1].rar"
    assert mog.c.calls == [("GET", SOURCE)]


def test_what_is_not_an_archive_is_not_looked_inside():
    exe = {"path": "setup.exe", "file_name": "setup.exe", "kind": "known installer", "category": "game"}
    mog = MogClient(Posts({DEFAULT: {"candidates": [exe]}}))
    assert mog.portable_archive(3) is None and mog.c.calls == [("GET", DEFAULT)]


def test_an_add_on_is_not_the_game():
    dlc = {**ARCHIVE, "category": "dlc"}
    mog = MogClient(Posts({DEFAULT: {"candidates": [dlc]}}))
    assert mog.portable_archive(3) is None


def test_an_error_from_the_server_means_no_question_not_no_install():
    class Failing:
        def get_json(self, path):
            return 500, {"detail": "boom"}

    assert MogClient(Failing()).portable_archive(3) is None


def test_the_session_is_started_with_the_choice():
    posts = Posts({})
    MogClient(posts).start_session(3, None, None, None, extract_only=True)
    assert posts.calls[0][2]["extract_only"] is True
    posts.calls.clear()
    MogClient(posts).start_session(3, None, None, None)
    assert "extract_only" not in posts.calls[0][2]


def test_a_game_that_is_extracted_remembers_it_and_a_restart_does_too(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    started = []

    class Server:
        c = SimpleNamespace(base="http://s")

        def get_session(self, gid):
            return {}

        def start_session(self, gid, installer_path, proton, ttl, **kw):
            started.append(kw)
            raise RuntimeError("stop here")  # what follows is the download, not under test

    settings = Settings(install_dirs=[str(tmp_path / "games")])
    game = {"id": 3, "name": "Hearthlands"}
    for extract in (True, False):
        try:
            manager.run_install(Server(), game, settings, SimpleNamespace(is_set=lambda: False), lambda m: None, lambda s: None, lambda a, b: None, None, None, extract)
        except RuntimeError:
            pass
    assert [kw["extract_only"] for kw in started] == [True, True]  # the second start did not ask, the record did
    assert config.load_library()[3].extract_only is True


def test_installed_game_records_default_to_not_extracted():
    assert InstalledGame(1, "G", "/x").extract_only is False


def test_saying_no_to_extracting_is_sent_outright_and_not_deciding_sends_nothing():
    posts = Posts({})
    MogClient(posts).start_session(3, None, None, None, extract_only=False)
    assert posts.calls[0][2]["extract_only"] is False
    posts.calls.clear()
    MogClient(posts).start_session(3, None, None, None, extract_only=None)
    assert "extract_only" not in posts.calls[0][2]


def test_an_install_nobody_decided_for_leaves_it_to_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    started = []

    class Server:
        c = SimpleNamespace(base="http://s")

        def get_session(self, gid):
            return {}

        def start_session(self, gid, installer_path, proton, ttl, **kw):
            started.append(kw["extract_only"])
            raise RuntimeError("stop here")

    settings = Settings(install_dirs=[str(tmp_path / "games")])
    for extract in (None, False):
        try:
            manager.run_install(Server(), {"id": 4, "name": "Metroid"}, settings, SimpleNamespace(is_set=lambda: False), lambda m: None, lambda s: None, lambda a, b: None, None, None, extract)
        except RuntimeError:
            pass
    assert started == [None, False]


# --- more installs than the server runs at once: they wait, they do not fail ---


def test_a_server_that_refuses_a_start_for_lack_of_places_is_asked_again_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    seen, waits = [], []

    class Stop:
        def is_set(self):
            return False

        def wait(self, seconds):
            waits.append(seconds)
            return False  # not cancelled

    class Server:
        c = SimpleNamespace(base="http://s")
        asked = 0

        def get_session(self, gid):
            return {}

        def start_session(self, gid, installer_path, proton, ttl, **kw):
            Server.asked += 1
            if Server.asked < 3:
                raise RuntimeError("HTTP 429: Too many concurrent installs")
            raise RuntimeError("stop here")  # what follows is the download, not under test

    settings = Settings(install_dirs=[str(tmp_path / "games")])
    with pytest.raises(RuntimeError, match="stop here"):
        manager.run_install(Server(), {"id": 4, "name": "Metroid"}, settings, Stop(), lambda m: None, seen.append, lambda a, b: None, None, None, None)

    assert Server.asked == 3 and waits == [manager.QUEUE_RETRY_SECONDS] * 2  # it waited, twice, and asked again
    assert seen == [{"state": "queued"}, {"state": "queued"}]  # and the window knew it was waiting


def test_cancelling_while_waiting_for_a_place_ends_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    class Stop:
        def is_set(self):
            return True

        def wait(self, seconds):
            return True

    class Server:
        c = SimpleNamespace(base="http://s")

        def get_session(self, gid):
            return {}

        def start_session(self, *a, **kw):
            raise RuntimeError("HTTP 429: Too many concurrent installs")

    settings = Settings(install_dirs=[str(tmp_path / "games")])
    rec = manager.run_install(Server(), {"id": 4, "name": "Metroid"}, settings, Stop(), lambda m: None, lambda s: None, lambda a, b: None, None, None, None)
    assert rec.game_id == 4  # no error


def test_a_queued_session_is_one_the_client_keeps_waiting_for():
    from mog_client.api import ACTIVE_STATES

    assert "queued" in ACTIVE_STATES
