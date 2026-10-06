from pathlib import Path

from mog_client import config, installdirs
from mog_client.config import InstalledGame, Settings
from mog_client.installdirs import MARGIN_BYTES, choose

GB = 1024**3


def fake(connected: dict[str, int]):
    """Folders that are connected, with their free bytes."""
    return (lambda root: str(root) in connected), (lambda root: connected[str(root)])


def where(connected, needed, roots=("/a", "/b", "/c")):
    usable, free_of = fake(connected)
    return choose([Path(r) for r in roots], needed, usable, free_of)


def test_the_first_folder_with_room_takes_the_game_without_asking():
    plan = where({"/a": 100 * GB, "/b": 100 * GB}, 10 * GB)
    assert [s.path for s in plan.ready] == [Path("/a"), Path("/b")]
    assert plan.blocked_by is None


def test_a_disconnected_first_folder_is_passed_over_silently():
    plan = where({"/b": 100 * GB}, 10 * GB)
    assert plan.slots[0].state == "unavailable"
    assert plan.ready[0].path == Path("/b")
    assert plan.blocked_by is None


def test_a_full_first_folder_means_asking_before_the_next():
    plan = where({"/a": 5 * GB, "/b": 100 * GB}, 10 * GB)
    assert plan.blocked_by is not None and plan.blocked_by.path == Path("/a")
    assert [s.path for s in plan.ready] == [Path("/b")]


def test_a_full_folder_after_the_one_that_fits_does_not_matter():
    plan = where({"/a": 100 * GB, "/b": 1 * GB}, 10 * GB)
    assert plan.blocked_by is None and plan.ready[0].path == Path("/a")


def test_nothing_fits():
    plan = where({"/a": 5 * GB, "/b": 6 * GB}, 10 * GB)
    assert plan.ready == []


def test_the_margin_counts():
    assert where({"/a": 10 * GB}, 10 * GB).ready == []
    assert where({"/a": 10 * GB + MARGIN_BYTES}, 10 * GB).ready


def test_an_unreadable_disk_counts_as_not_connected():
    def broken(root):
        raise OSError("gone")

    plan = choose([Path("/a")], 0, lambda r: True, broken)
    assert plan.slots[0].state == "unavailable"


def test_reachable_needs_the_folder_or_its_parent(tmp_path):
    assert installdirs.reachable(tmp_path)
    assert installdirs.reachable(tmp_path / "new")  # created on first use
    assert not installdirs.reachable(tmp_path / "unplugged" / "mog-games")


def test_free_bytes_of_a_folder_that_does_not_exist_yet(tmp_path):
    assert installdirs.free_bytes(tmp_path / "a" / "b") > 0


def test_a_game_is_present_while_its_folder_is(tmp_path):
    folder = tmp_path / "Game"
    rec = InstalledGame(1, "Game", str(folder))
    assert not installdirs.present(rec)
    folder.mkdir()
    assert installdirs.present(rec)


def test_resuming_needs_the_drive_back(tmp_path):
    assert installdirs.resumable(InstalledGame(1, "G", str(tmp_path / "G")))
    assert not installdirs.resumable(InstalledGame(1, "G", str(tmp_path / "gone" / "mog" / "G")))


def test_default_root_is_the_first_with_room_or_else_the_first(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    settings = Settings(install_dirs=[str(tmp_path / "unplugged" / "x"), str(one), str(two)])
    assert installdirs.default_root(settings) == one
    assert installdirs.default_root(Settings(install_dirs=[str(tmp_path / "unplugged" / "x")])) == tmp_path / "unplugged" / "x"


def test_no_folders_means_the_default_one(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    assert Settings().install_roots == [tmp_path / "games"]


def test_the_single_folder_of_older_versions_becomes_the_list(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text('{"base": "http://s", "games_dir": "/media/games"}')
    settings = config.load_settings()
    assert settings.install_dirs == ["/media/games"]
    config.save_settings(settings)
    assert "games_dir" not in (tmp_path / "config.json").read_text()
