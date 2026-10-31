from mog_client.api import MogClient
from mog_client.grouping import group_games


def _game(id, igdb_id=None, missing=False, name="G"):
    return {"id": id, "igdb_id": igdb_id, "missing_from_fs": missing, "name": name}


def test_games_sharing_an_igdb_id_form_one_group():
    groups = group_games([_game(1, 10), _game(2, 20), _game(3, 10)])
    assert [[g["id"] for g in grp.members] for grp in groups] == [[1, 3], [2]]


def test_games_the_server_tied_to_one_title_are_one_group_whatever_their_igdb_id():
    """A game IGDB does not know is tied to another by hand (`mog:<id>`), and a wrong match is split off the same way."""
    tied = [dict(_game(1), title_key="mog:1"), dict(_game(2), title_key="mog:1"), dict(_game(3, 10), title_key="mog:3"), _game(4, 10)]

    groups = group_games(tied)

    assert [[g["id"] for g in grp.members] for grp in groups] == [[1, 2], [3], [4]]


def test_a_server_that_does_not_send_the_title_still_groups_by_the_igdb_id():
    assert [[g["id"] for g in grp.members] for grp in group_games([_game(1, 10), _game(2, 10)])] == [[1, 2]]


def test_unmatched_games_stay_separate():
    groups = group_games([_game(1), _game(2)])
    assert len(groups) == 2


def test_missing_versions_are_not_installable_unless_nothing_else_is_left():
    mixed = group_games([_game(1, 10, missing=True), _game(2, 10)])[0]
    assert [g["id"] for g in mixed.versions] == [2] and mixed.game["id"] == 2
    only_missing = group_games([_game(1, 10, missing=True)])[0]
    assert [g["id"] for g in only_missing.versions] == [1]


class _Fake:
    def __init__(self):
        self.body = None

    def post_json(self, path, body, **kw):
        self.body = body
        return 200, {"id": 1}


def test_start_session_sends_the_chosen_installer_or_archive():
    fake = _Fake()
    MogClient(fake).start_session(5, "setup.exe", None, None)
    assert fake.body["installer_path"] == "setup.exe" and "source_path" not in fake.body
    MogClient(fake).start_session(5, None, None, None, source_path="game.iso")
    assert fake.body["source_path"] == "game.iso"


def test_the_server_state_that_gets_a_corner_badge():
    from mog_client.grouping import ADDONS_ONLY, SAVES_IMPORTED, SAVES_ONLY, corner_state

    assert corner_state({"saves_only": True, "missing_from_fs": True}) == SAVES_ONLY
    assert corner_state({"addons_only": True}) == ADDONS_ONLY
    assert corner_state({"addons_only": True, "missing_from_fs": True}) is None  # gone: nothing to flag
    assert corner_state({"missing_from_fs": True}) is None
    assert corner_state({"saves_only": True, "addons_only": True}) == SAVES_ONLY
    assert corner_state({"saves_only": True, "imported": True, "missing_from_fs": True}) == SAVES_IMPORTED  # never had files here
    assert corner_state({"imported": True, "missing_from_fs": True}) is None  # no saves yet: nothing to flag
    assert corner_state({}) is None


def test_a_game_with_nothing_to_install_from_is_told_from_one_that_has_files():
    from mog_client.grouping import has_no_files

    assert has_no_files({"imported": True}) and has_no_files({"saves_only": True, "missing_from_fs": True})
    assert not has_no_files({"library_id": None}) and not has_no_files({"missing_from_fs": True})  # still listed, maybe back soon
