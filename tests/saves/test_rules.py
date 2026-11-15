"""A game's save rules: what the server says is never backed up, and what is added by hand always is."""

from pathlib import Path

import pytest
from fakes import FakeServer
from test_sync import faugus_owns, make_prefix, world, write  # noqa: F401 - the fixture

from mog_client.saves import archive as archives
from mog_client.saves import rules as save_rules
from mog_client.saves import sync
from mog_client.saves.state import load_state

SHADERS = "users/USER/AppData/Local/Game/shadercache2"


def test_a_folder_rule_covers_its_files_and_not_a_sibling_with_the_same_start():
    rules = save_rules.Rules(["game/cache"], ["game/extra"])

    assert rules.blocked("game/cache/a.bin") and rules.blocked("game/cache")
    assert not rules.blocked("game/cache2/a.bin")
    assert rules.added("game/extra/x.sav") and not rules.added("game/other")
    assert rules.covering("game/cache/a.bin") == ["game/cache"]


def test_the_whitelist_is_stronger_than_the_blacklist_so_a_folder_is_left_out_but_the_saves_in_it():
    rules = save_rules.Rules(["game/thisgame", "*.png"], ["game/thisgame/saves"])

    assert rules.blocked("game/thisgame/shaders/a.bin") and rules.blocked("game/other/x.png")
    assert not rules.blocked("game/thisgame/saves/slot1.sav") and not rules.blocked("game/thisgame/saves/shot.png")
    assert rules.added("game/thisgame/saves/slot1.sav")
    assert rules.covering("game/thisgame/saves/slot1.sav") == [] and rules.covering("game/thisgame/a") == ["game/thisgame"]


def test_a_game_folder_blacklisted_still_backs_up_the_saves_added_inside_it(world):
    """Native game: everything under `game/` is left out except `game/saves`, which the whitelist names."""
    write(world.install / "shaders/a.bin", b"x" * 20)
    write(world.install / "saves/slot1.sav", b"s")
    world.rec.target = "linux"
    world.server.rules = {"blacklist": ["game"], "whitelist": ["game/saves"]}

    sync.backup(world.ctx, sync.SYNC, force=True)

    assert world.server.uploads == [("sync", ["game/saves/slot1.sav"])]


def test_a_blacklisted_shader_cache_is_not_uploaded_though_the_scan_takes_it(world, monkeypatch):
    """A port writes `shadercache2` beside its saves: none of the built-in names catches it, so the scan sent it."""
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    write(user / "Saved Games/slot1.sav", b"one")
    write(user / "AppData/Local/Game/shadercache2/a.bin", b"x" * 50)

    assert sorted(sync.backup(world.ctx, sync.SYNC).changed) == [SHADERS + "/a.bin", "users/USER/Saved Games/slot1.sav"]

    world.server.rules["blacklist"] = [SHADERS]
    write(user / "Saved Games/slot1.sav", b"two!", mtime=user.stat().st_mtime + 50)
    again = sync.backup(world.ctx, sync.QUIT)

    assert again.status == "uploaded"
    assert world.server.uploads[-1] == ("quit", ["users/USER/Saved Games/slot1.sav"])
    assert SHADERS + "/a.bin" not in load_state(7).tracked


def test_taking_a_path_off_the_blacklist_syncs_it_the_next_time_it_is_there(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    write(user / "Saved Games/slot1.sav", b"one")
    write(user / "AppData/Local/Game/shadercache2/a.bin", b"x")
    world.server.rules["blacklist"] = [SHADERS]
    sync.backup(world.ctx, sync.SYNC)
    assert world.server.uploads == [("sync", ["users/USER/Saved Games/slot1.sav"])]

    world.server.rules["blacklist"] = []
    sync.backup(world.ctx, sync.SYNC)

    assert world.server.uploads[-1] == ("sync", [SHADERS + "/a.bin", "users/USER/Saved Games/slot1.sav"])


def test_a_file_added_by_hand_is_backed_up_even_when_the_scan_would_not_find_it_and_a_session_says_nothing_of_it(world, monkeypatch):
    """A file in the prefix's drive_c outside the profile folders is not a candidate, and a session window excludes old files."""
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    drive_c = prefix / "pfx/drive_c"
    write(drive_c / "Games/G/config.ini", b"[x]", mtime=1_600_000_000)
    world.server.rules["whitelist"] = ["Games/G/config.ini"]

    result = sync.backup(world.ctx, sync.QUIT, since_ns=2_000_000_000 * 10**9)

    assert result.status == "uploaded"
    assert world.server.uploads == [("quit", ["Games/G/config.ini"])]


def test_a_game_with_a_server_that_does_not_know_rules_syncs_as_before(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    write(prefix / "pfx/drive_c/users/steamuser/Saved Games/slot1.sav", b"one")
    world.server.rules = None

    assert sync.backup(world.ctx, sync.SYNC).status == "uploaded"


def test_the_rules_last_read_are_used_when_the_server_cannot_be_asked(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    write(user / "Saved Games/slot1.sav", b"one")
    write(user / "AppData/Local/Game/shadercache2/a.bin", b"x")
    world.server.rules["blacklist"] = [SHADERS]
    sync.backup(world.ctx, sync.SYNC)
    assert load_state(7).rules_blacklist == [SHADERS]

    world.server.rules = None  # an older answer: it cannot say
    write(user / "Saved Games/slot1.sav", b"changed", mtime=user.stat().st_mtime + 50)
    sync.backup(world.ctx, sync.QUIT)

    assert world.server.uploads[-1][1] == ["users/USER/Saved Games/slot1.sav"]


def test_a_restore_leaves_blacklisted_files_in_the_archive(world, tmp_path):
    from mog_client.saves.state import SaveState, save_state

    save_state(7, SaveState(rules_blacklist=["game/cache"]))
    source = tmp_path / "src"
    write(source / "slot.sav", b"s")
    write(source / "cache/a.bin", b"c")
    zipped = tmp_path / "a.zip"
    archives.build_archive([("game/slot.sav", source / "slot.sav"), ("game/cache/a.bin", source / "cache/a.bin")], zipped)
    world.rec.target = "linux"

    result = sync._apply(world.ctx, load_state(7), zipped, None)

    assert result.restored == ["game/slot.sav"]
    assert (world.install / "slot.sav").exists() and not (world.install / "cache").exists()


@pytest.mark.parametrize(
    "where, key",
    [
        ("game_file", "game/saves/slot.sav"),
        ("profile", "users/USER/Documents/Game/slot.sav"),
        ("drive_c", "ProgramData/Game/slot.sav"),
    ],
)
def test_a_picked_path_becomes_the_key_the_archive_uses(world, where, key):
    drive_c = world.tmp / "pfx/drive_c"
    (drive_c / "users/steamuser").mkdir(parents=True)
    path = {
        "game_file": world.install / "saves/slot.sav",
        "profile": drive_c / "users/steamuser/Documents/Game/slot.sav",
        "drive_c": drive_c / "ProgramData/Game/slot.sav",
    }[where]

    assert save_rules.key_for_path(path, drive_c, world.install) == key


def test_the_game_folder_itself_and_a_path_outside_every_root_have_no_key(world):
    assert save_rules.key_for_path(world.install, None, world.install) is None
    assert save_rules.key_for_path(Path("/usr/share/x"), None, world.install) is None


@pytest.mark.parametrize(
    "rule, key, hit",
    [
        ("*.png", "game/art/a.png", True),
        ("*.png", "users/USER/Documents/G/A.PNG", True),
        ("*.png", "game/a.pngx", False),
        ("cache*", "game/cache2/a.bin", True),
        ("game/*/shaders", "game/x/shaders/a.bin", True),
        ("game/*/shaders", "game/x/y/shaders", False),
        ("game/**.bin", "game/x/y/a.bin", True),
        ("slot[0-9].sav", "game/slot3.sav", True),
        ("slot[!0-9].sav", "game/slot3.sav", False),
        ("re:\\.(png|jpg)$", "game/a.jpg", True),
        ("re:^users/", "game/users/a", False),
        ("re:(", "game/a", False),  # a rule this client cannot read leaves nothing out
    ],
)
def test_a_pattern_rule_names_the_paths_it_fits_the_way_the_server_reads_it(rule, key, hit):
    assert save_rules.covers(rule, key) is hit


@pytest.mark.parametrize("bad", ["re:(", "re:", "../*.png", "/a*", "a/[z-a]*"])
def test_a_rule_that_cannot_work_says_why_before_it_is_sent(bad):
    assert save_rules.check_rule(bad)


def test_a_png_wildcard_keeps_every_picture_out_of_the_backup_and_the_saves_in(world, monkeypatch):
    """`*.png` as a path named nothing: a port's pictures went up with every save."""
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    user = prefix / "pfx/drive_c/users/steamuser"
    write(user / "Saved Games/slot1.sav", b"one")
    write(user / "Saved Games/thumb.PNG", b"p" * 20)
    write(user / "AppData/Local/Game/art/a.png", b"q" * 20)
    world.server.rules["blacklist"] = ["*.png"]

    sync.backup(world.ctx, sync.SYNC)

    assert world.server.uploads == [("sync", ["users/USER/Saved Games/slot1.sav"])]


def test_a_wildcard_on_the_whitelist_takes_the_files_the_scan_found_and_does_not_go_looking(world, monkeypatch):
    prefix = make_prefix(world.tmp)
    faugus_owns(world, prefix, monkeypatch)
    drive_c = prefix / "pfx/drive_c"
    write(drive_c / "Games/G/config.ini", b"[x]", mtime=1_600_000_000)
    write(drive_c / "users/steamuser/Saved Games/x.sav", b"s", mtime=1_600_000_000)
    world.server.rules["whitelist"] = ["*.sav", "*.ini"]

    result = sync.backup(world.ctx, sync.QUIT, since_ns=2_000_000_000 * 10**9)

    assert result.status == "uploaded"
    assert world.server.uploads == [("quit", ["users/USER/Saved Games/x.sav"])]  # config.ini is not a candidate: not looked for
