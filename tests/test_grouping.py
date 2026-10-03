from mog_client.api import MogClient
from mog_client.grouping import group_games


def _game(id, igdb_id=None, missing=False, name="G"):
    return {"id": id, "igdb_id": igdb_id, "missing_from_fs": missing, "name": name}


def test_games_sharing_an_igdb_id_form_one_group():
    groups = group_games([_game(1, 10), _game(2, 20), _game(3, 10)])
    assert [[g["id"] for g in grp.members] for grp in groups] == [[1, 3], [2]]


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
