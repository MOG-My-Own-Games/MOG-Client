from datetime import datetime, timezone

from mog_client import ordering, played
from mog_client.grouping import Group


def game(gid, name, release=None, last_played=None):
    g = {"id": gid, "name": name, "igdb_metadata": {"first_release_date": release} if release else None}
    if last_played:
        g["last_played"] = last_played
    return Group(members=[g], versions=[g])


def order(groups, **kw):
    settings = dict(
        order="az",
        installing=lambda g: False,
        installed=lambda g: False,
        played=lambda g: None,
        last_played_first=False,
        installed_first=False,
    )
    settings.update(kw)
    return [g.game["name"] for g in ordering.sort_groups(groups, **settings)]


def test_alphabetical_either_way():
    groups = [game(1, "Beta"), game(2, "alpha"), game(3, "Gamma")]
    assert order(groups) == ["alpha", "Beta", "Gamma"]
    assert order(groups, order="za") == ["Gamma", "Beta", "alpha"]


def test_release_date_puts_the_undated_last_in_both_directions():
    groups = [game(1, "Old", 100), game(2, "None"), game(3, "New", 900)]
    assert order(groups, order="newest") == ["New", "Old", "None"]
    assert order(groups, order="oldest") == ["Old", "New", "None"]


def test_last_played_beats_installed_and_installed_beats_the_order_even_when_not_installed():
    groups = [game(1, "A"), game(2, "B"), game(3, "C"), game(4, "D")]
    installed = {1, 2}
    stamps = {4: 50.0, 3: 90.0}
    result = order(
        groups,
        installed=lambda g: g.game["id"] in installed,
        played=lambda g: stamps.get(g.game["id"]),
        last_played_first=True,
        installed_first=True,
    )
    assert result == ["C", "D", "A", "B"]  # played ones first (not installed or not), then installed, then A to Z


def test_a_game_being_installed_is_always_first():
    groups = [game(1, "A"), game(2, "B")]
    assert order(groups, installing=lambda g: g.game["id"] == 2, played=lambda g: 5.0 if g.game["id"] == 1 else None, last_played_first=True) == ["B", "A"]


def test_last_played_takes_the_newest_of_this_client_and_the_server():
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    group = game(1, "A", last_played=stamp.isoformat())
    assert ordering.last_played(group, {}) == stamp.timestamp()
    assert ordering.last_played(group, {1: stamp.timestamp() + 60}) == stamp.timestamp() + 60
    assert ordering.last_played(game(2, "B"), {}) is None


def test_the_launch_times_are_kept_between_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(played, "data_dir", lambda: tmp_path)
    played.record(7, 1000.0)
    played.record(9, 2000.0)
    assert played.load() == {7: 1000.0, 9: 2000.0}


def test_the_history_also_counts_when_a_games_saves_were_last_uploaded(tmp_path, monkeypatch):
    from mog_client.saves import state as save_state

    monkeypatch.setattr(played, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(save_state, "data_dir", lambda: tmp_path)
    uploaded = datetime(2026, 10, 1, tzinfo=timezone.utc)
    started = uploaded.timestamp() + 60
    played.record(7, started)
    for gid, stamp in ((7, datetime(2020, 1, 1, tzinfo=timezone.utc)), (9, uploaded)):
        state = save_state.SaveState(last_synced_at=stamp.isoformat())
        save_state.save_state(gid, state)

    history = played.history()
    assert history[7] == started  # the newer of a seen start and an upload (here the start)
    assert history[9] == uploaded.timestamp()  # no start was seen, but its saves went up then


def test_the_release_orders_come_first_and_newest_is_the_default():
    from mog_client.config import Settings

    assert list(ordering.ORDERS)[:2] == ["newest", "oldest"]
    assert Settings().sort_order == "newest"
