from pathlib import Path

import pytest

from mog_client import manager, steaminput
from mog_client.config import InstalledGame

EXISTING = (
    '"controller_config"\n{\n'
    '\t"413080"\n\t{\n\t\t"workshop"\t\t"734376083"\n\t}\n'
    '\t"zero zone"\n\t{\n\t\t"autosave"\t\t"1"\n\t}\n'
    '\t"the simpsons hit &amp; run"\n\t{\n\t\t"template"\t\t"RetroDECK_controller_steamdeck_neptune_full.vdf"\n\t}\n'
    "}\n"
)


@pytest.fixture
def steam(tmp_path):
    root = tmp_path / "steam"
    user = root / "userdata/1234"
    (user / "config").mkdir(parents=True)
    (root / "steamapps").mkdir()
    return user


def _file(user: Path) -> Path:
    return steaminput.configset_path(user)


def test_steam_names_a_block_by_the_shortcut_name_lowercased_without_punctuation():
    assert steaminput.key_of("Setup.exe") == "setupexe"
    assert steaminput.key_of("Living With Sister: Monochrome Fantasy") == "living with sister monochrome fantasy"
    assert steaminput.key_of("Baldur's Gate 3") == "baldur's gate 3"
    assert steaminput.key_of("Pokémon Infinite Fusion") == "pokémon infinite fusion"


def test_the_template_is_added_as_text_and_what_steam_wrote_stays_as_it_was(steam):
    path = _file(steam)
    path.parent.mkdir(parents=True)
    path.write_text(EXISTING)
    assert steaminput.set_template(steam, "Videodrome") is True
    text = path.read_text()
    assert text.startswith(EXISTING[:-2])  # everything before the closing brace is untouched
    assert text.endswith('\t"videodrome"\n\t{\n\t\t"template"\t\t"controller_neptune_wasd.vdf"\n\t}\n}\n')
    assert path.with_name(path.name + ".mog-backup").read_text() == EXISTING
    assert steaminput.has_block(text, "videodrome") and steaminput.has_block(text, "413080")


def test_a_game_that_has_a_choice_already_keeps_it_and_a_second_run_changes_nothing(steam):
    path = _file(steam)
    path.parent.mkdir(parents=True)
    path.write_text(EXISTING)
    assert steaminput.set_template(steam, "Zero Zone") is False  # a layout the person made
    assert steaminput.set_template(steam, "Videodrome") is True
    assert steaminput.set_template(steam, "Videodrome") is False
    assert path.read_text().count('"videodrome"') == 1 and '"autosave"\t\t"1"' in path.read_text()


def test_a_steam_that_has_no_file_yet_gets_one_with_the_block_and_one_that_never_ran_gets_nothing(steam, tmp_path):
    assert steaminput.set_template(steam, "Game") is True
    assert _file(steam).read_text() == '"controller_config"\n{\n\t"game"\n\t{\n\t\t"template"\t\t"controller_neptune_wasd.vdf"\n\t}\n}\n'
    lonely = tmp_path / "other/userdata/9"
    lonely.mkdir(parents=True)
    assert steaminput.set_template(lonely, "Game") is False and not (tmp_path / "other/steamapps").exists()


def test_a_file_that_is_not_a_controller_config_is_left_alone(steam):
    path = _file(steam)
    path.parent.mkdir(parents=True)
    path.write_text("garbage")
    assert steaminput.set_template(steam, "Game") is False and path.read_text() == "garbage"


def _game(steam, name="Videodrome") -> InstalledGame:
    entry = {"shortcuts_path": str(steam / "config/shortcuts.vdf"), "appid": 1}
    return InstalledGame(game_id=7, name=name, install_dir="/g", executable="/g/a.exe", state="installed", steam_entries=[entry])


def test_only_a_game_the_server_says_has_no_gamepad_support_gets_the_keyboard_and_mouse_template(steam):
    rec = _game(steam)
    assert manager.apply_keyboard_profile(rec, {"controller_support": "full"}) is False
    assert manager.apply_keyboard_profile(rec, {"controller_support": None}) is False  # unknown is not "none"
    assert not _file(steam).exists()
    assert manager.apply_keyboard_profile(rec, {"controller_support": "none"}) is True
    assert steaminput.has_block(_file(steam).read_text(), "videodrome")


def test_with_the_gamepad_support_switch_off_no_template_is_set(steam):
    from dataclasses import replace

    from mog_client.config import load_settings, save_settings

    rec = _game(steam)
    save_settings(replace(load_settings(), controller_support=False))
    assert manager.apply_keyboard_profile(rec, {"controller_support": "none"}) is False and not _file(steam).exists()
    save_settings(replace(load_settings(), controller_support=True))  # on is the default
    assert manager.apply_keyboard_profile(rec, {"controller_support": "none"}) is True
    assert load_settings().controller_support is True
