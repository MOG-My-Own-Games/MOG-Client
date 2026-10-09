from pathlib import Path, PurePosixPath

import pytest

from mog_client.saves import locations
from mog_client.saves.locations import UnsafeKey, check_key, install_candidates, profile_candidates, target_for_key


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def drive_c(tmp_path):
    root = tmp_path / "pfx" / "drive_c"
    _touch(root / "users/steamuser/Saved Games/Game/slot1.sav")
    _touch(root / "users/steamuser/Documents/My Games/Game/config.ini")
    _touch(root / "users/steamuser/AppData/Roaming/Game/profile.dat")
    _touch(root / "users/steamuser/AppData/Local/Game/progress.bin")
    _touch(root / "ProgramData/Game/shared.dat")
    _touch(root / "users/Public/Documents/Game/public.sav")
    return root


def keys(candidates):
    return sorted(c.key for c in candidates)


def test_the_profile_and_shared_folders_are_scanned_with_user_independent_keys(drive_c):
    assert keys(profile_candidates(drive_c)) == [
        "ProgramData/Game/shared.dat",
        "users/Public/Documents/Game/public.sav",
        "users/USER/AppData/Local/Game/progress.bin",
        "users/USER/AppData/Roaming/Game/profile.dat",
        "users/USER/Documents/My Games/Game/config.ini",
        "users/USER/Saved Games/Game/slot1.sav",
    ]


def test_cache_log_and_system_noise_is_skipped(drive_c):
    for noise in (
        "users/steamuser/AppData/Local/Temp/x.tmp",
        "users/steamuser/AppData/Local/Microsoft/Windows/y.dat",
        "users/steamuser/AppData/Local/Game/Cache/blob.bin",
        "users/steamuser/AppData/Local/Game/GPUCache/data_0",
        "users/steamuser/AppData/Roaming/Game/debug.log",
        "users/steamuser/AppData/Local/CrashDumps/g.dmp",
        "ProgramData/Microsoft/Windows/z.dat",
        "windows/system32/kernel32.dll",
    ):
        _touch(drive_c / noise)
    assert not [k for k in keys(profile_candidates(drive_c)) if any(n in k for n in ("Temp", "Microsoft", "Cache", ".log", ".dmp"))]
    assert len(keys(profile_candidates(drive_c))) == 6


@pytest.mark.parametrize(
    "path",
    [
        "users/USER/AppData/Local/PokemonEmerald/Saved/Config/CrashReportClient/UECC-Windows-08C5/CrashReportClient.ini",
        "ProgramData/Package Cache/{b49c10dd-4d54-45f8-ad13-fa25704456a4}/state.rsm",
        "game/PokemonEmerald/Binaries/Win64/vkd3d-proton.cache",
        "users/USER/AppData/Local/Game/shader_Cache.bin",
        "users/USER/AppData/LocalLow/Eek/House Party/Unity/08e3df3f/Analytics/ArchivedEvents/1791.52393e08/c",
    ],
)
def test_launcher_telemetry_installer_and_shader_files_are_never_saves(path):
    assert locations.is_denied(tuple(PurePosixPath(path).parts))


def test_real_saves_next_to_that_noise_are_still_saves():
    for path in ("users/USER/Saved Games/Game/slot1.sav", "users/USER/AppData/Local/PokemonEmerald/Saved/SaveGames/slot0.sav"):
        assert not locations.is_denied(tuple(PurePosixPath(path).parts))


def test_log_files_are_never_saves_whatever_their_case(drive_c):
    for log in (
        "users/steamuser/AppData/Roaming/Game/debug.log",
        "users/steamuser/AppData/Roaming/Game/Player.LOG",
        "users/steamuser/AppData/Roaming/Game/output_log.txt",
        "users/steamuser/AppData/Roaming/Game/Crash-Log-2026.TXT",
        "users/steamuser/AppData/Roaming/Game/logs_backup.txt",
        "users/steamuser/Documents/Game/session log.log",
    ):
        _touch(drive_c / log)
    _touch(drive_c / "users/steamuser/Documents/Game/notes.txt")  # a text file that is not a log stays
    _touch(drive_c / "users/steamuser/Documents/Game/dialogue.dat")

    found = keys(profile_candidates(drive_c))

    assert not [k for k in found if k.lower().endswith((".log", "log.txt", "log-2026.txt", "logs_backup.txt"))]
    assert not [k for k in found if "debug" in k or "Player" in k or "output_log" in k or "Crash-Log" in k or "session log" in k]
    assert "users/USER/Documents/Game/notes.txt" in found and "users/USER/Documents/Game/dialogue.dat" in found


def test_log_files_in_the_games_own_folder_are_not_offered_either(tmp_path):
    game = tmp_path / "game"
    _touch(game / "Saves/profile.sav")
    _touch(game / "game.log")
    _touch(game / "Logs_old.txt")
    _touch(game / "sub/engine_log.txt")
    assert keys(install_candidates(game, {})) == ["game/Saves/profile.sav"]


def test_compiled_python_the_game_writes_beside_its_files_is_not_a_save(tmp_path):
    game = tmp_path / "game"
    _touch(game / "saves/persistent")
    _touch(game / "renpy/__init__.pyo")
    _touch(game / "renpy/angle/__init__.pyc")
    _touch(game / "tools/__pycache__/helper.cpython-312.opt-1.pyc")
    _touch(game / "tools/__pycache__/notes.txt")  # nothing in a __pycache__ folder is
    _touch(game / "scripts/data.py")  # the source itself is not compiled output
    assert sorted(keys(install_candidates(game, {}))) == ["game/saves/persistent", "game/scripts/data.py"]


def test_symbolic_links_are_never_followed(drive_c, tmp_path):
    home_documents = tmp_path / "home" / "Documents"
    _touch(home_documents / "private.txt")
    user = drive_c / "users/steamuser"
    (user / "Documents" / "Linked").symlink_to(home_documents)
    (drive_c / "users/xargon").symlink_to(user)  # Wine and Proton name the user twice this way
    (user / "My Documents").symlink_to(user / "Documents")
    _touch(user / "Documents/real.txt")
    link_file = user / "Saved Games" / "elsewhere.sav"
    link_file.symlink_to(home_documents / "private.txt")

    found = keys(profile_candidates(drive_c))

    assert not any("private" in k or "Linked" in k or "elsewhere" in k for k in found)
    assert "users/USER/Documents/real.txt" in found
    assert len([k for k in found if k.endswith("slot1.sav")]) == 1  # xargon -> steamuser is not scanned twice


def test_a_documents_folder_that_is_itself_a_link_is_not_scanned(tmp_path):
    root = tmp_path / "drive_c"
    home = tmp_path / "home" / "Documents"
    _touch(home / "secret.txt")
    (root / "users/steamuser").mkdir(parents=True)
    (root / "users/steamuser/Documents").symlink_to(home)
    assert keys(profile_candidates(root)) == []


def test_the_profile_folder_to_restore_into_prefers_steamuser(tmp_path):
    root = tmp_path / "drive_c"
    (root / "users/zed").mkdir(parents=True)
    (root / "users/steamuser").mkdir()
    (root / "users/Public").mkdir()
    assert locations.user_dir_name(root) == "steamuser"
    (root / "users/steamuser").rmdir()
    assert locations.user_dir_name(root) == "zed"


def test_install_folder_files_are_offered_only_when_the_installer_did_not_write_them(tmp_path):
    import hashlib

    game = tmp_path / "game"
    shipped = _touch(game / "Data/level1.dat", b"level")
    _touch(game / "Saves/slot1.sav", b"progress")
    edited = _touch(game / "settings.ini", b"edited by the game")
    manifest = {
        "Data/level1.dat": (5, hashlib.sha1(b"level").hexdigest()),
        "settings.ini": (3, hashlib.sha1(b"old").hexdigest()),
    }

    assert keys(install_candidates(game, manifest)) == ["game/Saves/slot1.sav", "game/settings.ini"]
    assert keys(install_candidates(game, None)) == []
    assert shipped.exists() and edited.exists()


@pytest.mark.parametrize("key", ["", "../x", "a/../../x", "/etc/passwd", "C:/x", "a\\b", "users/USER/../../x"])
def test_unsafe_keys_are_refused(key):
    with pytest.raises(UnsafeKey):
        check_key(key)


def test_keys_map_back_to_this_machines_folders(drive_c, tmp_path):
    install = tmp_path / "game"
    assert target_for_key("users/USER/Saved Games/a.sav", drive_c, install) == drive_c / "users/steamuser/Saved Games/a.sav"
    assert target_for_key("ProgramData/G/a.dat", drive_c, install) == drive_c / "ProgramData/G/a.dat"
    assert target_for_key("game/Saves/a.sav", drive_c, install) == install / "Saves/a.sav"
    assert target_for_key("game/Saves/a.sav", None, install) == install / "Saves/a.sav"
    with pytest.raises(UnsafeKey):
        target_for_key("users/USER/a.sav", None, install)  # no prefix to put it in


def test_a_key_cannot_be_written_through_a_link_that_leaves_the_prefix(drive_c, tmp_path):
    outside = tmp_path / "home"
    outside.mkdir()
    (drive_c / "users/steamuser/Documents/Out").symlink_to(outside)
    with pytest.raises(UnsafeKey):
        target_for_key("users/USER/Documents/Out/x.sav", drive_c, tmp_path / "game")


def test_the_prefix_and_the_files_mog_writes_are_not_the_games_files(tmp_path):
    import hashlib

    game = tmp_path / "game"
    _touch(game / "data.pak", b"shipped")
    _touch(game / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav", b"in the prefix")
    _touch(game / "Game Name.sh", b"#!/bin/sh")
    _touch(game / "Game Name.desktop", b"[Desktop Entry]")
    _touch(game / "Game Name.lnk", b"lnk")
    _touch(game / ".directory", b"[Desktop Entry]")
    _touch(game / ".mog-icon", b"png")
    _touch(game / "Saves/profile.sav", b"progress")
    _touch(game / "tools/run.sh", b"#!/bin/sh")  # a script deeper down is the game's own
    manifest = {"data.pak": (7, hashlib.sha1(b"shipped").hexdigest())}

    assert keys(install_candidates(game, manifest)) == ["game/Saves/profile.sav", "game/tools/run.sh"]
