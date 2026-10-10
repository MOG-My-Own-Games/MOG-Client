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


def test_size_puts_the_unmeasured_last_in_both_directions():
    def sized(gid, name, size):
        g = game(gid, name)
        g.game["size_bytes"] = size
        return g

    groups = [sized(1, "Small", 10), sized(2, "Unknown", None), sized(3, "Big", 900), sized(4, "Also", 10)]
    assert order(groups, order="largest") == ["Big", "Also", "Small", "Unknown"]
    assert order(groups, order="smallest") == ["Also", "Small", "Big", "Unknown"]


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


def test_a_game_was_last_played_where_the_newest_start_or_save_is():
    server = {"id": 7, "name": "G", "last_played": "2026-10-05T10:00:00Z", "last_played_on": "deck"}
    group = Group(members=[server], versions=[server])
    saved_at = ordering.iso_to_epoch("2026-10-05T10:00:00Z")

    assert ordering.last_played_on(group, {}, "karasu") == (saved_at, "deck")  # only the server saw it
    assert ordering.last_played_on(group, {7: saved_at + 60}, "karasu") == (saved_at + 60, "karasu")  # started here since
    assert ordering.last_played_on(group, {7: saved_at - 60}, "karasu") == (saved_at, "deck")  # the other machine is newer
    assert ordering.last_played_on(group, {7: saved_at + 60}, None) == (saved_at + 60, None)  # this machine has no name yet

    never = Group(members=[{"id": 8, "name": "N"}], versions=[{"id": 8, "name": "N"}])
    assert ordering.last_played_on(never, {}, "karasu") == (None, None)


def extra(gid, name, **fields):
    g = {"id": gid, "name": name, **fields}
    return Group(members=[g], versions=[g])


def test_recently_added_puts_the_newest_first_and_the_unknown_last():
    groups = [
        extra(1, "Old", created_at="2026-01-01T10:00:00Z"),
        extra(2, "Unknown"),
        extra(3, "New", created_at="2026-10-01T10:00:00Z"),
    ]
    assert order(groups, order="added") == ["New", "Old", "Unknown"]
    assert order(groups, order="added_old") == ["Old", "New", "Unknown"]


def test_cached_first_puts_the_games_the_server_holds_ahead_of_the_rest():
    groups = [extra(1, "A"), extra(2, "B", installed=True), extra(3, "C")]
    assert order(groups, cached_first=True) == ["B", "A", "C"]


def test_installed_ones_stay_ahead_of_the_cached_ones():
    groups = [extra(1, "A", installed=True), extra(2, "B"), extra(3, "C")]
    assert order(groups, cached_first=True, installed_first=True, installed=lambda g: g.game["name"] == "C") == ["C", "A", "B"]


def test_saves_and_mods_are_read_from_any_version_of_the_title():
    one, two = {"id": 1, "name": "X", "last_played": "2026-10-01T10:00:00Z"}, {"id": 2, "name": "X", "has_mods": True}
    group = Group(members=[one, two], versions=[one, two])
    assert ordering.has_saves(group) and ordering.has_mods(group)
    assert not ordering.has_saves(extra(3, "Y")) and not ordering.has_mods(extra(3, "Y"))


def test_a_game_waiting_for_its_executable_comes_before_the_installed_and_the_played():
    groups = [extra(1, "A"), extra(2, "B"), extra(3, "C")]
    assert order(
        groups,
        installed_first=True,
        installed=lambda g: g.game["name"] == "A",
        awaiting=lambda g: g.game["name"] == "C",
        last_played_first=True,
        played=lambda g: 5 if g.game["name"] == "B" else None,
    ) == ["C", "B", "A"]
