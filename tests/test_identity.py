"""A record that sits on another game's id (the server handed the number out again) is moved or set aside."""

import json

import pytest

from mog_client import config, identity
from mog_client.config import InstalledGame, load_library, save_library
from mog_client.saves import state


@pytest.fixture(autouse=True)
def folders(monkeypatch, tmp_path):
    monkeypatch.setattr(identity, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(state, "data_dir", lambda: tmp_path / "data")


def game(gid, name, fs_name=None):
    return {"id": gid, "name": name, "fs_name": fs_name or name}


def record(gid, name, **kw):
    return InstalledGame(game_id=gid, name=name, install_dir=f"/games/{name}", **kw)


def test_a_record_on_the_number_of_another_game_is_set_aside_and_the_game_shows_as_not_installed():
    save_library({17: record(17, "Zelmoor Ruins", state="awaiting_executable")})

    said = identity.reconcile([game(17, "Harbor Light: The Keeper of Tides", "Harbor Light [GOG]")])

    assert load_library() == {} and len(said) == 1 and "Zelmoor Ruins" in said[0] and "Harbor Light" in said[0]
    kept = json.loads(identity.orphans_path().read_text())
    assert kept[0]["name"] == "Zelmoor Ruins" and kept[0]["was_game_id"] == 17


def test_a_game_the_scrape_renamed_is_still_its_own_record():
    save_library({5: record(5, "Harbor Light [GOG]")})

    assert identity.reconcile([game(5, "Harbor Light: The Keeper of Tides", "Harbor Light [GOG]")]) == []

    assert load_library()[5].server_name == "Harbor Light [GOG]"  # remembered from now on


def test_a_record_whose_game_has_another_number_now_goes_to_it_with_its_saves_state(tmp_path):
    save_library({17: record(17, "Zelmoor Ruins", server_name="Zelmoor Ruins")})
    state.saves_dir(17).mkdir(parents=True)
    (state.saves_dir(17) / "state.json").write_text("{}")

    said = identity.reconcile([game(17, "Harbor Light"), game(40, "Zelmoor Ruins")])

    library = load_library()
    assert list(library) == [40] and library[40].game_id == 40 and "Zelmoor Ruins" in said[0]
    assert (state.saves_dir(40) / "state.json").is_file() and not state.saves_dir(17).exists()


def test_a_record_whose_game_the_server_does_not_list_is_left_alone():
    save_library({17: record(17, "Zelmoor Ruins")})
    assert identity.reconcile([game(1, "Other")]) == [] and 17 in load_library()


def test_a_game_being_installed_is_not_touched():
    save_library({17: record(17, "Zelmoor Ruins")})
    assert identity.reconcile([game(17, "Harbor Light")], busy={17}) == [] and 17 in load_library()


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Harbor Light [GOG]", "Harbor Light: The Keeper of Tides & the Far Shore", True),
        ("Zelmoor Ruins", "Harbor Light: The Keeper of Tides & the Far Shore", False),
        ("The Ravenwood 3 (GOG)", "The Ravenwood 3: Pale Hunt", True),
        ("", "Anything", False),
    ],
)
def test_names_of_the_same_game_are_told_from_names_of_two_games(a, b, same):
    assert identity.alike(a, b) is same


def test_a_game_added_from_outside_mog_follows_the_server_when_a_scan_takes_its_entry_over():
    save_library({5: record(5, "Silent Beetle", imported=True, target="linux", server_name="Silent Beetle")})

    said = identity.reconcile([game(5, "Silent Beetle", "Silent_Beetle")])  # the folder was found: the entry has its name now

    assert said == [] and load_library()[5].server_name == "Silent_Beetle"


def test_a_game_added_from_outside_mog_on_a_number_the_server_gave_to_another_game_is_still_set_aside():
    save_library({5: record(5, "Silent Beetle", imported=True, target="linux", server_name="Silent Beetle")})

    said = identity.reconcile([game(5, "Celeste")])

    assert load_library() == {} and len(said) == 1
