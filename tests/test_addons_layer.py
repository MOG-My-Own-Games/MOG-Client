import io
import zipfile
from pathlib import Path

import pytest

from mog_client import addons
from mog_client.saves import state
from mog_client.saves.state import load_install_manifest


class FakeClient:
    """The two addon calls, with the server keeping whatever it is sent."""

    def __init__(self, served: dict[int, bytes] | None = None):
        self.served = served or {}
        self.uploads = []

    def download_addon(self, game_id, addon_id, dest, on_progress=None):
        dest.write_bytes(self.served[addon_id])
        if on_progress:
            on_progress(len(self.served[addon_id]), len(self.served[addon_id]))

    def upload_addon(self, game_id, category, name, archive, on_progress=None):
        with zipfile.ZipFile(archive) as zf:
            self.uploads.append((category, name, sorted(zf.namelist())))
        if on_progress:
            size = archive.stat().st_size
            on_progress(size // 2, size)
            on_progress(size, size)
        return {"id": 9}


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def game(tmp_path):
    folder = tmp_path / "Game"
    (folder / "data").mkdir(parents=True)
    (folder / "Game.exe").write_bytes(b"exe")
    (folder / "data" / "level.pak").write_bytes(b"level")
    return folder


def test_a_layer_run_by_hand_is_sent_as_what_it_changed_and_is_not_a_save(game, tmp_path):
    before = addons.open_layer(4, game)
    (game / "data" / "level.pak").write_bytes(b"patched level, longer")
    (game / "data" / "new.dat").write_bytes(b"added")

    changed = addons.close_layer(4, game, before)
    fake = FakeClient()
    addons.upload_layer(fake, 4, game, changed, "patch", "Patch 1.1", tmp_path)

    assert [rel for rel, _ in changed] == ["data/level.pak", "data/new.dat"]
    assert fake.uploads == [("patch", "Patch 1.1", ["data/level.pak", "data/new.dat"])]
    manifest = load_install_manifest(4) or {}
    assert "data/new.dat" in manifest and "data/level.pak" in manifest


def test_an_empty_layer_is_not_sent(game, tmp_path):
    before = addons.open_layer(4, game)
    changed = addons.close_layer(4, game, before)

    with pytest.raises(addons.AddonError, match="no files"):
        addons.upload_layer(FakeClient(), 4, game, changed, "dlc", "DLC", tmp_path)


def test_an_unknown_kind_is_refused_before_anything_is_sent(game, tmp_path):
    with pytest.raises(addons.AddonError, match="Unknown kind"):
        addons.upload_layer(FakeClient(), 4, game, [("a", game / "Game.exe")], "save", "x", tmp_path)


def test_a_server_addon_is_extracted_over_the_game_and_its_files_are_not_saves(game, tmp_path):
    served = _zip_bytes({"data/mod.pak": b"mod", "Game.exe": b"patched exe"})
    addon = {"id": 3, "name": "Better Textures", "category": "mod"}

    written = addons.apply_addon(FakeClient({3: served}), 4, game, addon, tmp_path)

    assert written == 2
    assert (game / "Game.exe").read_bytes() == b"patched exe"
    assert (game / "data" / "mod.pak").read_bytes() == b"mod"
    assert "data/mod.pak" in (load_install_manifest(4) or {})
    assert not list(tmp_path.glob("addon-*.zip"))


def test_an_addon_whose_path_points_outside_the_game_is_refused_and_nothing_is_written(game, tmp_path):
    served = _zip_bytes({"../escape.txt": b"x"})

    with pytest.raises(addons.AddonError, match="Unsafe"):
        addons.apply_addon(FakeClient({3: served}), 4, game, {"id": 3}, tmp_path)

    assert not (game.parent / "escape.txt").exists()


@pytest.mark.parametrize("name", ["C:evil.dll", "C:/Windows/x.dll", "a\\b.txt", "/etc/x"])
def test_a_drive_a_backslash_or_a_root_in_a_name_is_refused(game, tmp_path, name):
    served = _zip_bytes({"data/ok.pak": b"ok", name: b"x"})

    with pytest.raises(addons.AddonError, match="Unsafe"):
        addons.apply_addon(FakeClient({3: served}), 4, game, {"id": 3}, tmp_path)

    assert not (game / "data" / "ok.pak").exists()  # the archive is checked whole before any file is written


def test_a_link_in_an_addon_is_refused(game, tmp_path):
    import io
    import stat
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("shortcut")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(addons.AddonError, match="link"):
        addons.apply_addon(FakeClient({3: buffer.getvalue()}), 4, game, {"id": 3}, tmp_path)


def test_an_addon_declaring_more_than_the_limit_is_refused(game, tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "MAX_UNCOMPRESSED_BYTES", 10)
    served = _zip_bytes({"big.bin": b"x" * 11})

    with pytest.raises(addons.AddonError, match="too large"):
        addons.apply_addon(FakeClient({3: served}), 4, game, {"id": 3}, tmp_path)


def cand(path, kind="known installer", category="game"):
    return {"path": path, "kind": kind, "category": category, "file_size_bytes": 1}


def test_an_archive_is_not_a_step_of_a_chain():
    found = [cand("setup.exe"), cand("game.zip", "archive"), cand("patch.exe", "executable (top level)", "patch")]

    assert [c["path"] for c in addons.chainable(found)] == ["setup.exe", "patch.exe"]


def test_the_saved_order_comes_first_and_is_checked_the_rest_follows_unchecked():
    found = [cand("a.exe"), cand("b.exe", "executable (top level)"), cand("c.exe", "executable (nested)")]

    arranged = addons.arrange(found, [{"path": "c.exe"}, {"path": "a.exe"}])

    assert [(c["path"], on) for c, on in arranged] == [("c.exe", True), ("a.exe", True), ("b.exe", False)]


def test_with_no_saved_order_only_the_first_installer_of_the_game_is_checked():
    found = [cand("setup.exe"), cand("patch.exe", "executable (top level)", "patch")]

    arranged = addons.arrange(found, [])

    assert [(c["path"], on) for c, on in arranged] == [("setup.exe", True), ("patch.exe", False)]


def test_a_saved_step_whose_file_has_gone_is_left_out():
    found = [cand("setup.exe")]

    arranged = addons.arrange(found, [{"path": "gone.exe"}])

    assert [(c["path"], on) for c, on in arranged] == [("setup.exe", True)]


def test_the_first_step_is_the_game_and_the_others_are_patches():
    assert [addons.role_of(i) for i in range(3)] == ["Game", "Patch", "Patch"]
    assert addons.steps_of([cand("setup.exe"), cand("p.exe")]) == [
        {"path": "setup.exe", "source_path": None},
        {"path": "p.exe", "source_path": None},
    ]


def test_a_layer_stays_open_on_disk_until_it_is_closed(game):
    assert not addons.layer_is_open(4)

    before = addons.open_layer(4, game)
    assert addons.layer_is_open(4)

    addons.close_layer(4, game, before)
    assert not addons.layer_is_open(4)


ISO = {"path": "2 - Setup/DISK1.ISO", "kind": "disc image", "category": "game", "file_size_bytes": 5}


def test_the_installers_inside_a_disc_keep_the_disc_they_are_in():
    inside = [cand("setup.exe"), cand("AAPlay.exe", "executable (nested)"), cand("readme.txt", "archive")]

    entries = addons.inside_entries(ISO, inside)

    assert [(e["path"], e["source_path"], e["kind"]) for e in entries] == [
        ("setup.exe", "2 - Setup/DISK1.ISO", "executable (inside)"),
        ("AAPlay.exe", "2 - Setup/DISK1.ISO", "executable (inside)"),
    ]


def test_what_is_inside_follows_its_disc_and_can_be_chained():
    found = [cand("a.exe"), ISO]

    flat = addons.with_inside(found, {ISO["path"]: [cand("setup.exe")]})

    assert [c["path"] for c in flat] == ["a.exe", "2 - Setup/DISK1.ISO", "setup.exe"]
    assert [c["path"] for c in addons.chainable(flat)] == ["a.exe", "setup.exe"]  # the disc itself is not a step


def test_a_disc_descriptor_is_not_looked_inside_and_only_a_few_sources_are():
    mds = {"path": "DISK1.MDS", "kind": "disc image"}
    many = [{"path": f"part{i}.rar", "kind": "archive"} for i in range(20)]

    assert addons.sources([ISO, mds]) == [ISO]
    assert len(addons.sources(many)) == addons.MAX_SOURCES


def test_a_step_inside_a_disc_is_sent_with_its_source_and_found_again_in_the_saved_order():
    inner = addons.inside_entries(ISO, [cand("setup.exe")])[0]
    outer = cand("setup.exe")  # a different file with the same name, not inside the disc

    assert addons.steps_of([inner]) == [{"path": "setup.exe", "source_path": "2 - Setup/DISK1.ISO"}]
    arranged = addons.arrange([outer, inner], [{"path": "setup.exe", "source_path": "2 - Setup/DISK1.ISO"}])
    assert arranged[0][0] is inner and arranged[0][1] is True and arranged[1] == (outer, False)


def test_just_copy_is_a_step_like_an_installer_that_goes_first_and_is_sent_as_the_copy_marker():
    copy = addons.copy_entry()

    assert addons.is_copy(copy) and not addons.is_copy({"path": "setup.exe", "kind": "known installer"})
    assert addons.chainable([copy, {"path": "setup.exe", "kind": "known installer"}, {"path": "d.iso", "kind": "disc image"}]) == [
        copy,
        {"path": "setup.exe", "kind": "known installer"},
    ]
    assert addons.label_of(copy) == "Just copy the files, run nothing"
    assert addons.steps_of([copy, {"path": "patch.exe", "kind": "executable (top level)"}]) == [
        {"path": "mog:copy", "source_path": None},
        {"path": "patch.exe", "source_path": None},
    ]
    assert addons.role_of(0) == "Game"


def test_a_send_tells_the_files_packed_then_the_bytes_sent(game, tmp_path):
    changed = [("Game.exe", game / "Game.exe"), ("data/level.pak", game / "data" / "level.pak")]
    heard = []

    addons.upload_layer(FakeClient(), 4, game, changed, "patch", "P", tmp_path, lambda *a: heard.append(a))

    assert heard[0] == ("Packing", 0, 2) and ("Packing", 2, 2) in heard
    sending = [h for h in heard if h[0] == "Sending"]
    assert sending and sending[-1][1] == sending[-1][2]
    assert heard.index(("Packing", 2, 2)) < heard.index(sending[0])


def test_a_send_stopped_while_packing_uploads_nothing(game, tmp_path):
    client = FakeClient()

    def stop(*_):
        raise addons.AddonCancelled

    with pytest.raises(addons.AddonCancelled):
        addons.upload_layer(client, 4, game, [("Game.exe", game / "Game.exe")], "mod", "M", tmp_path, stop)

    assert client.uploads == [] and not (tmp_path / "layer.zip").exists()


def test_an_apply_tells_the_download_then_the_files_put_over_the_game(game, tmp_path):
    served = _zip_bytes({"data/new.pak": b"new", "data/more.pak": b"more"})
    heard = []

    addons.apply_addon(FakeClient({3: served}), 4, game, {"id": 3}, tmp_path, lambda *a: heard.append(a))

    stages = [h[0] for h in heard]
    assert stages[0] == "Downloading" and stages[-1] == "Putting the files over the game"
    assert heard[-1][1:] == (2, 2)


def test_an_apply_stopped_before_it_writes_leaves_the_game_alone(game, tmp_path):
    served = _zip_bytes({"data/new.pak": b"new"})

    with pytest.raises(addons.AddonCancelled):
        addons.apply_addon(FakeClient({3: served}), 4, game, {"id": 3}, tmp_path, cancelled=lambda: True)

    assert not (game / "data" / "new.pak").exists() and not addons.layer_is_open(4)
