import os
import time

import pytest

from mog_client import config, launcher, umudb
from mog_client.config import InstalledGame

CSV = (
    "TITLE,STORE,CODENAME,UMU_ID,COMMON ACRONYM (Optional),NOTE (Optional),EXE_STRINGS (Optional)\n"
    "Zero Zone,none,none,umu-zerozone,,Zero Zone (1998) by Cryo Interactive Entertainment,ZeroZone.exe\n"
    "Heroes of Might and Magic III,gog,1207658787,umu-1207658787,HoMM3,,\n"
    "Some Game,none,none,umu-somegame,,,game.exe\n"
    "No Fix Known,none,none,,,,\n"
)


@pytest.fixture
def database(tmp_path):
    path = umudb.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CSV)
    return path


def test_a_game_is_found_by_its_title_however_it_is_written(database):
    assert umudb.find("Zero Zone") == "umu-zerozone"
    assert umudb.find("zero  zone!") == "umu-zerozone"
    assert umudb.find("Heroes of Might & Magic III") == "umu-1207658787"
    assert umudb.find("HoMM3") == "umu-1207658787"  # its common acronym


def test_a_game_with_another_title_is_found_by_the_name_of_its_exe(database):
    assert umudb.find("Zero Zone [Italian]", "/games/ZZone/ZeroZone.exe") == "umu-zerozone"


def test_an_exe_name_that_is_too_short_to_tell_games_apart_is_not_enough(database):
    assert umudb.find("Another Game", "/games/x/game.exe") is None
    assert umudb.find("Not In There", "/games/x/other.exe") is None
    assert umudb.find("No Fix Known") is None  # a row with no id of its own


def test_without_the_database_nothing_is_found(tmp_path):
    assert umudb.find("Zero Zone") is None


def _game(tmp_path, name="Zero Zone", exe="ZeroZone.exe"):
    return InstalledGame(game_id=7, name=name, install_dir=str(tmp_path / "Game"), executable=str(tmp_path / "Game" / exe))


def test_umu_starts_a_game_the_database_knows_under_its_own_id_and_any_other_under_one_of_ours(database, monkeypatch, tmp_path):
    assert launcher.umu_id(_game(tmp_path)) == "umu-zerozone"
    assert launcher.umu_id(_game(tmp_path, "Mine", "mine.exe")) == "umu-mog-7"
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    cmd, extra = launcher.launch_command(_game(tmp_path))
    assert extra["GAMEID"] == "umu-zerozone"


def test_the_database_is_fetched_when_missing_or_old_and_says_whether_it_changed(tmp_path, monkeypatch):
    fetched = []
    monkeypatch.setattr(umudb, "fetch_url", lambda url, timeout=30.0: fetched.append(url) or CSV.encode())

    assert umudb.refresh() is True and len(fetched) == 1 and umudb.find("Zero Zone") == "umu-zerozone"
    assert umudb.refresh() is False and len(fetched) == 1  # a week has not passed
    old = time.time() - umudb.MAX_AGE_SECONDS - 60
    os.utime(umudb.cache_path(), (old, old))
    assert umudb.refresh() is False and len(fetched) == 2  # fetched again, the same
    monkeypatch.setattr(umudb, "fetch_url", lambda url, timeout=30.0: (CSV + "New,none,none,umu-new,,,\n").encode())
    assert umudb.refresh(force=True) is True and umudb.find("New") == "umu-new"


def test_a_failed_or_wrong_download_leaves_the_copy_there_is(database, monkeypatch):
    def broken(url, timeout=30.0):
        raise RuntimeError("no network")

    monkeypatch.setattr(umudb, "fetch_url", broken)
    assert umudb.refresh(force=True) is False and umudb.find("Zero Zone") == "umu-zerozone"
    monkeypatch.setattr(umudb, "fetch_url", lambda url, timeout=30.0: b"<html>not found</html>")
    assert umudb.refresh(force=True) is False and umudb.find("Zero Zone") == "umu-zerozone"
