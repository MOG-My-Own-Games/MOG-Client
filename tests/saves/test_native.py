import os
from pathlib import Path

import pytest

from mog_client.config import InstalledGame
from mog_client.saves import native
from mog_client.saves.locations import (
    UnsafeKey,
    describe_key,
    is_native_key,
    native_key,
    native_root,
    target_for_key,
)

NOW = 2_000_000_000


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    return home


def touch(path: Path, data: bytes = b"x", mtime: int = NOW + 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def game(install="/g/Zelmoor Ruins", exe="/g/Zelmoor Ruins/start.sh", name="Zelmoor Ruins") -> InstalledGame:
    return InstalledGame(1, name, install, executable=exe, state="installed")


def test_the_roots_follow_the_xdg_variables_and_fall_back_to_the_usual_folders(home, monkeypatch):
    assert native_root("xdg-config") == home / ".config"
    assert native_root("xdg-data") == home / ".local/share"
    assert native_root("xdg-state") == home / ".local/state"
    monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/is/ignored")
    assert native_root("xdg-data") == Path("/data/xdg")
    assert native_root("xdg-config") == home / ".config"
    assert native_root("home") == home and native_root("users") is None


def test_a_path_gets_the_key_of_the_most_specific_root(home, monkeypatch):
    assert native_key(home / ".local/share/Game/a.sav") == "xdg-data/Game/a.sav"
    assert native_key(home / ".config/Game/b.ini") == "xdg-config/Game/b.ini"
    assert native_key(home / ".zelmoorruins/c.dat") == "home/.zelmoorruins/c.dat"
    assert native_key(Path("/etc/passwd")) is None
    monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
    assert native_key(Path("/data/xdg/Game/a.sav")) == "xdg-data/Game/a.sav"
    assert is_native_key("xdg-data/x") and is_native_key("home/x") and not is_native_key("users/USER/x")


def test_a_key_is_shown_as_the_path_the_user_knows(home):
    assert describe_key("xdg-config/Game") == "~/.config/Game"
    assert describe_key("home/Documents/Game") == "~/Documents/Game"
    assert describe_key("game/x.sav") == "game/x.sav"


def test_a_native_key_goes_back_below_its_root_and_never_out_of_it(home, tmp_path):
    assert target_for_key("xdg-data/Game/a.sav", None, tmp_path) == home / ".local/share/Game/a.sav"
    assert target_for_key("home/.zelmoorruins/c.dat", None, tmp_path) == home / ".zelmoorruins/c.dat"
    with pytest.raises(UnsafeKey):
        target_for_key("xdg-data/../../etc/x", None, tmp_path)
    with pytest.raises(UnsafeKey):
        target_for_key("xdg-data", None, tmp_path)  # the root itself
    with pytest.raises(UnsafeKey):
        target_for_key("users/USER/Documents/x", None, tmp_path)  # a Windows key with no prefix


def test_a_game_is_known_by_its_title_folder_and_executable_but_not_by_generic_names():
    names = native.game_names(game(install="/g/zelmoor-ruins-gog", exe="/g/zelmoor-ruins-gog/ZelmoorRuins.x86_64"))
    assert "zelmoorruins" in names.whole and "zelmoorruinsgog" in names.whole
    assert "ruins" in names.words and "zelmoor" in names.words
    assert "start" not in native.game_names(game()).whole  # start.sh says nothing about the game
    assert "the" not in native.game_names(game(name="The Game of the Edition")).words


def test_the_users_own_name_is_no_name_of_a_game_unless_it_is_the_title(monkeypatch):
    """A game in the home folder (or in one named after the user) must not make every folder called like the user a save
    folder of it. The title is what the user typed, so it is kept even when it is the same name."""
    monkeypatch.setenv("USER", "player")
    names = native.game_names(game(name="Zelmoor Ruins", install="/home/player", exe="/home/player/player.x86_64"))
    assert "player" not in names.whole and "player" not in names.words and "zelmoorruins" in names.whole
    assert "player" in native.game_names(game(name="Player", install="/g/x")).whole


def test_a_folder_named_like_the_game_scores_high_a_word_in_common_scores_low():
    names = native.game_names(game())
    assert native.affinity("ZelmoorRuins", names) == 2
    assert native.affinity("zelmoor-ruins", names) == 2
    assert native.affinity("com.studio.zelmoorruins", names) == 2
    assert native.affinity("Ruins of Something", names) == 1
    assert native.affinity("unity3d", names) == 0
    assert native.affinity("ab", names) == 0  # too short to mean anything


def test_the_session_search_only_offers_files_written_since_the_start(home):
    since = NOW * 10**9
    new = touch(home / ".local/share/ZelmoorRuins/slot1.sav")
    touch(home / ".local/share/ZelmoorRuins/old.sav", mtime=NOW - 100)
    touch(home / ".config/ZelmoorRuins/prefs.ini")
    touch(home / "Documents/My Games/ZelmoorRuins/a.sav")
    touch(home / ".local/state/ZelmoorRuins/state.json")

    keys = sorted(c.key for c in native.session_files(since))

    assert keys == [
        "home/Documents/My Games/ZelmoorRuins/a.sav",
        "xdg-config/ZelmoorRuins/prefs.ini",
        "xdg-data/ZelmoorRuins/slot1.sav",
        "xdg-state/ZelmoorRuins/state.json",
    ]
    assert new.exists()


def test_other_programs_caches_and_logs_are_not_offered(home):
    since = NOW * 10**9
    for noise in (
        ".config/google-chrome/Default/Cookies",
        ".config/Code/User/settings.json",
        ".local/share/Steam/steamapps/x.acf",
        ".local/share/mog-client/installed.json",
        ".config/ZelmoorRuins/Cache/blob",
        ".local/share/ZelmoorRuins/output_log.txt",
        ".local/share/ZelmoorRuins/debug.log",
        ".cache/ZelmoorRuins/x.sav",
        "Downloads/x.sav",
        "Documents/../Pictures/y.png",
    ):
        touch(home / noise)
    touch(home / ".config/ZelmoorRuins/Settings.ini")

    assert [c.key for c in native.session_files(since)] == ["xdg-config/ZelmoorRuins/Settings.ini"]


def test_a_huge_file_a_link_and_the_install_folder_are_left_out(home, tmp_path):
    since = NOW * 10**9
    touch(home / ".local/share/ZelmoorRuins/small.sav")
    big = touch(home / ".local/share/ZelmoorRuins/big.bin")
    os.truncate(big, native.MAX_FILE_BYTES + 1)
    os.utime(big, (NOW + 100, NOW + 100))
    (home / ".local/share/ZelmoorRuins/link.sav").symlink_to(home / ".local/share/ZelmoorRuins/small.sav")
    install = home / ".local/share/games/Zelmoor Ruins"
    touch(install / "profile.sav")

    found = [c.key for c in native.session_files(since, [install])]

    assert found == ["xdg-data/ZelmoorRuins/small.sav"]


def test_folders_are_game_sized_and_the_likeliest_come_first():
    names = native.game_names(game())
    keys = [
        "xdg-config/unity3d/Studio/ZelmoorRuins/prefs",
        "xdg-config/unity3d/Studio/ZelmoorRuins/slots/one.sav",
        "xdg-config/OtherApp/settings.json",
        "xdg-data/Paradox/Zelmoor Ruins/save.sav",
        "xdg-data/loose.sav",
        "home/Documents/My Games/Zelmoor Ruins/a.sav",
    ]
    folders = native.suggest_folders(keys, names)
    assert [(f.key, f.score) for f in folders] == [
        ("home/Documents/My Games/Zelmoor Ruins", 2),
        ("xdg-config/unity3d/Studio/ZelmoorRuins", 2),
        ("xdg-data/Paradox/Zelmoor Ruins", 2),
        ("xdg-config/OtherApp", 0),
        ("xdg-data/loose.sav", 0),
    ]


def test_a_restored_file_keeps_its_own_folder_so_siblings_follow():
    assert native.restored_folder("xdg-config/unity3d/Studio/Game/prefs") == "xdg-config/unity3d/Studio/Game"
    assert native.restored_folder("xdg-data/loose.sav") == "xdg-data/loose.sav"
    assert native.restored_folder("home/Documents/Game/a.sav") == "home/Documents/Game"


def test_confirmed_folders_and_single_files_are_listed_whole(home, tmp_path):
    touch(home / ".local/share/ZelmoorRuins/a.sav")
    touch(home / ".local/share/ZelmoorRuins/Cache/c.sav")
    touch(home / ".config/single.ini")
    got = sorted(
        c.key for c in native.included_files(["xdg-data/ZelmoorRuins", "xdg-config/single.ini", "xdg-data/gone"], tmp_path)
    )
    assert got == ["xdg-config/single.ini", "xdg-data/ZelmoorRuins/a.sav"]
    assert list(native.included_files(["xdg-data/../../etc"], tmp_path)) == []


def test_manifest_paths_become_keys_of_the_folders_they_name(home, tmp_path, monkeypatch):
    install = tmp_path / "games" / "Zelmoor Ruins"
    paths = [
        "<xdgConfig>/MoonfieldValley/Saves",
        "<xdgData>/0ad/saves",
        "<home>/.zelmoorruins",
        "<base>/saves",
        "<xdgData>/Game/slot*.sav",  # a wildcard names its folder
        "<xdgData>/Game/*/profile",
        "<storeUserId>/slots",  # not resolvable here
        "<winAppData>/Game",
        "<home>",  # the whole home folder
        "<xdgConfig>",
        "<base>",
        "<home>/../etc",
        "<xdgData>/0ad/saves",  # a repeat
    ]

    assert native.manifest_folders(paths, install) == [
        "xdg-config/MoonfieldValley/Saves",
        "xdg-data/0ad/saves",
        "home/.zelmoorruins",
        "game/saves",
        "xdg-data/Game",
    ]
    monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
    assert native.manifest_folders(["<xdgData>/0ad"], install) == ["xdg-data/0ad"]
