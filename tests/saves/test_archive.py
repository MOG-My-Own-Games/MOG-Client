import os
import zipfile
from pathlib import Path

import pytest

from mog_client.saves.archive import ArchiveError, build_archive, extract_archive, read_keys


def _make(tmp_path: Path, files: dict[str, bytes]) -> Path:
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_files_are_packed_under_their_keys_and_unpacked_where_the_machine_says(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub/a.sav").write_bytes(b"alpha")
    (src / "b.ini").write_bytes(b"beta")
    archive = tmp_path / "out.zip"

    assert build_archive([("users/USER/Saved Games/a.sav", src / "sub/a.sav"), ("game/b.ini", src / "b.ini")], archive) == 2
    assert sorted(read_keys(archive)) == ["game/b.ini", "users/USER/Saved Games/a.sav"]

    dest = tmp_path / "dest"
    written = extract_archive(archive, lambda key: dest / key.replace("users/USER", "profile"))
    assert sorted(k for k, _ in written) == ["game/b.ini", "users/USER/Saved Games/a.sav"]
    assert (dest / "profile/Saved Games/a.sav").read_bytes() == b"alpha"
    assert (dest / "game/b.ini").read_bytes() == b"beta"
    assert not list(dest.rglob("*.mog-part"))


def test_a_file_that_vanished_is_skipped(tmp_path):
    archive = tmp_path / "out.zip"
    assert build_archive([("game/gone.sav", tmp_path / "missing")], archive) == 0


def test_what_a_restore_replaces_is_backed_up_first(tmp_path):
    archive = _make(tmp_path, {"game/a.sav": b"from the server", "game/new.sav": b"new"})
    dest = tmp_path / "dest"
    (dest / "game").mkdir(parents=True)
    (dest / "game/a.sav").write_bytes(b"mine")
    backup = tmp_path / "backup.zip"

    extract_archive(archive, lambda key: dest / key, backup=backup)

    assert (dest / "game/a.sav").read_bytes() == b"from the server"
    with zipfile.ZipFile(backup) as z:
        assert z.namelist() == ["game/a.sav"] and z.read("game/a.sav") == b"mine"


def test_no_backup_is_made_when_nothing_is_replaced(tmp_path):
    archive = _make(tmp_path, {"game/a.sav": b"x"})
    backup = tmp_path / "backup.zip"
    extract_archive(archive, lambda key: tmp_path / "d" / key, backup=backup)
    assert not backup.exists()


@pytest.mark.parametrize("name", ["../evil", "/abs", "C:/x", "a\\b"])
def test_an_unsafe_member_refuses_the_whole_archive_before_writing_anything(tmp_path, name):
    archive = _make(tmp_path, {"game/fine.sav": b"x", name: b"y"})
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError):
        extract_archive(archive, lambda key: dest / key)
    assert not dest.exists()


def test_a_target_the_machine_refuses_is_an_archive_error(tmp_path):
    from mog_client.saves.locations import UnsafeKey

    def refuse(key):
        raise UnsafeKey(key)

    with pytest.raises(ArchiveError):
        extract_archive(_make(tmp_path, {"game/a": b"x"}), refuse)


def test_a_symbolic_link_member_is_refused(tmp_path):
    import stat

    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("game/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveError, match="symbolic link"):
        extract_archive(path, lambda key: tmp_path / "d" / key)


def test_not_a_zip_is_an_archive_error(tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"nope")
    with pytest.raises(ArchiveError):
        extract_archive(junk, lambda key: tmp_path / key)
    with pytest.raises(ArchiveError):
        read_keys(junk)


def test_restored_files_keep_their_modification_time(tmp_path):
    src = tmp_path / "a.sav"
    src.write_bytes(b"x")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    archive = tmp_path / "o.zip"
    build_archive([("game/a.sav", src)], archive)
    (_, target), = extract_archive(archive, lambda key: tmp_path / "d" / key)
    assert abs(target.stat().st_mtime - 1_700_000_000) <= 2
