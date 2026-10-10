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
    save_library({17: record(17, "Lost Ruins", state="awaiting_executable")})

    said = identity.reconcile([game(17, "Atelier Yumia: The Alchemist of Memories", "Atelier Yumia [GOG]")])

    assert load_library() == {} and len(said) == 1 and "Lost Ruins" in said[0] and "Atelier Yumia" in said[0]
    kept = json.loads(identity.orphans_path().read_text())
    assert kept[0]["name"] == "Lost Ruins" and kept[0]["was_game_id"] == 17


def test_a_game_the_scrape_renamed_is_still_its_own_record():
    save_library({5: record(5, "Atelier Yumia [GOG]")})

    assert identity.reconcile([game(5, "Atelier Yumia: The Alchemist of Memories", "Atelier Yumia [GOG]")]) == []

    assert load_library()[5].server_name == "Atelier Yumia [GOG]"  # remembered from now on


def test_a_record_whose_game_has_another_number_now_goes_to_it_with_its_saves_state(tmp_path):
    save_library({17: record(17, "Lost Ruins", server_name="Lost Ruins")})
    state.saves_dir(17).mkdir(parents=True)
    (state.saves_dir(17) / "state.json").write_text("{}")

    said = identity.reconcile([game(17, "Atelier Yumia"), game(40, "Lost Ruins")])

    library = load_library()
    assert list(library) == [40] and library[40].game_id == 40 and "Lost Ruins" in said[0]
    assert (state.saves_dir(40) / "state.json").is_file() and not state.saves_dir(17).exists()


def test_a_record_whose_game_the_server_does_not_list_is_left_alone():
    save_library({17: record(17, "Lost Ruins")})
    assert identity.reconcile([game(1, "Other")]) == [] and 17 in load_library()


def test_a_game_being_installed_is_not_touched():
    save_library({17: record(17, "Lost Ruins")})
    assert identity.reconcile([game(17, "Atelier Yumia")], busy={17}) == [] and 17 in load_library()


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Atelier Yumia [GOG]", "Atelier Yumia: The Alchemist of Memories & the Envisioned Land", True),
        ("Lost Ruins", "Atelier Yumia: The Alchemist of Memories & the Envisioned Land", False),
        ("The Witcher 3 (GOG)", "The Witcher 3: Wild Hunt", True),
        ("", "Anything", False),
    ],
)
def test_names_of_the_same_game_are_told_from_names_of_two_games(a, b, same):
    assert identity.alike(a, b) is same
