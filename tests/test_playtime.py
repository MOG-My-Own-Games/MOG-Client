from types import SimpleNamespace

import pytest

from mog_client import connectivity, ordering, playtime
from mog_client.api import MogClient
from mog_client.grouping import Group
from mog_client.saves import devices

BEGAN = 1_793_000_000  # 2026-10-26 07:33:20 UTC


class Server:
    """What the client asks of the server about play time, with the statuses it answers (200 once they are used up)."""

    def __init__(self, statuses=()):
        self.statuses, self.sent = list(statuses), []

    def add_playtime(self, game_id, started_at, seconds, device_id=None):
        self.sent.append((game_id, started_at, seconds, device_id))
        return self.statuses.pop(0) if self.statuses else 200


@pytest.fixture(autouse=True)
def _this_machine_is_device_3(monkeypatch):
    monkeypatch.setattr(devices, "known_device", lambda: SimpleNamespace(device_id=3))


def test_a_run_is_kept_until_it_is_sent():
    assert playtime.record(7, BEGAN, 3720) is True

    assert playtime.waiting() == [{"game_id": 7, "started_at": "2026-10-26T07:33:20Z", "seconds": 3720}]
    assert playtime.outbox_path().is_file()  # on disk: closing the client does not lose it


def test_a_game_that_closed_within_moments_was_not_played():
    assert playtime.record(7, BEGAN, 4) is False and playtime.waiting() == []


def test_a_run_longer_than_the_server_takes_is_cut_in_several():
    playtime.record(7, BEGAN, 24 * 3600 + 600)

    assert [(e["started_at"], e["seconds"]) for e in playtime.waiting()] == [
        ("2026-10-26T07:33:20Z", 86400),
        ("2026-10-27T07:33:20Z", 600),
    ]


def test_the_same_run_is_not_kept_twice():
    playtime.record(7, BEGAN, 600)
    playtime.record(7, BEGAN, 600)

    assert len(playtime.waiting()) == 1


def test_what_is_waiting_is_sent_oldest_first_with_this_machine_and_then_forgotten():
    playtime.record(7, BEGAN, 600)
    playtime.record(8, BEGAN + 9000, 120)
    server = Server()

    assert playtime.flush(server) is True

    assert server.sent == [(7, "2026-10-26T07:33:20Z", 600, 3), (8, "2026-10-26T10:03:20Z", 120, 3)]
    assert playtime.waiting() == []


def test_a_server_that_cannot_be_reached_keeps_everything_and_nothing_goes_past_the_first():
    playtime.record(7, BEGAN, 600)
    playtime.record(8, BEGAN + 9000, 120)
    server = Server([None])

    assert playtime.flush(server) is False

    assert len(server.sent) == 1 and len(playtime.waiting()) == 2


@pytest.mark.parametrize("status", [500, 503, 401, 429])
def test_a_status_that_may_be_put_right_keeps_the_run(status):
    playtime.record(7, BEGAN, 600)

    assert playtime.flush(Server([status])) is False and len(playtime.waiting()) == 1


def test_a_run_the_server_refuses_for_good_is_dropped_so_it_does_not_block_the_next():
    playtime.record(7, BEGAN, 600)
    playtime.record(8, BEGAN + 9000, 120)
    said = []
    server = Server([400])

    assert playtime.flush(server, say=said.append) is True

    assert len(server.sent) == 2 and playtime.waiting() == [] and "400" in said[0]


def test_an_older_server_without_the_route_keeps_the_runs_and_the_others_still_go():
    """A client updated before its server gets a 404 for every run: they wait for the server, not lost."""
    import time

    now = int(time.time()) - 3600
    playtime.record(7, now, 600)
    playtime.record(8, now + 1000, 120)
    server = Server([404, 200])  # the first is unknown to the server, the second is taken

    assert playtime.flush(server) is False

    assert [e["game_id"] for e in playtime.waiting()] == [7]


def test_a_run_the_server_has_not_known_for_a_month_is_let_go():
    playtime.record(7, BEGAN - 60 * 24 * 3600, 600)  # two months ago

    assert playtime.flush(Server([404])) is True and playtime.waiting() == []


def test_nothing_is_sent_while_offline():
    playtime.record(7, BEGAN, 600)
    connectivity.set_forced(True)
    server = Server()

    assert playtime.flush(server) is False and server.sent == []
    assert len(playtime.waiting()) == 1


def test_a_machine_the_server_does_not_know_yet_sends_without_one(monkeypatch):
    monkeypatch.setattr(devices, "known_device", lambda: None)
    playtime.record(7, BEGAN, 600)
    server = Server()

    playtime.flush(server)

    assert server.sent[0][3] is None


def test_the_time_is_said_in_hours_and_minutes():
    assert playtime.describe(0) == "Not played" and playtime.describe(None) == "Not played"
    assert playtime.describe(30) == "Less than a minute"
    assert playtime.describe(12 * 60) == "12 min"
    assert playtime.describe(3 * 3600 + 20 * 60) == "3 h 20 min"
    assert playtime.describe(65 * 3600 + 5 * 60) == "65 h 05 min"


def test_a_title_adds_up_the_time_of_its_versions():
    versions = [{"id": 1, "playtime_seconds": 600}, {"id": 2, "playtime_seconds": 60}, {"id": 3}]

    assert ordering.playtime(Group(members=versions, versions=versions)) == 660


def test_the_run_is_told_to_the_server_as_json():
    class Raw:
        def __init__(self):
            self.calls = []

        def request(self, method, path, body=None):
            self.calls.append((method, path, body))
            return 200, b"{}", {}

    raw = Raw()

    assert MogClient(raw).add_playtime(7, "2026-10-26T07:33:20Z", 600, 3) == 200
    assert raw.calls == [("POST", "/api/games/7/playtime", {"started_at": "2026-10-26T07:33:20Z", "seconds": 600, "device_id": 3})]
    raw.calls.clear()
    MogClient(raw).add_playtime(7, "2026-10-26T07:33:20Z", 600)
    assert "device_id" not in raw.calls[0][2]


def test_an_unreachable_server_is_none_not_an_error():
    class Down:
        def request(self, *args, **kwargs):
            raise RuntimeError("offline")

    assert MogClient(Down()).add_playtime(7, "2026-10-26T07:33:20Z", 600) is None
