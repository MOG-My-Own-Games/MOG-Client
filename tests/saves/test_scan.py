import os
from pathlib import Path

from mog_client.saves.locations import Candidate
from mog_client.saves.scan import WINDOW_SLACK_NS, attribute, sha256_of

NOW = 2_000_000_000_000_000_000


def _file(tmp_path: Path, name: str, data: bytes, mtime_ns: int) -> Candidate:
    path = tmp_path / name
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return Candidate(f"users/USER/Documents/{name}", path)


def test_files_written_during_the_session_become_the_games(tmp_path):
    before = _file(tmp_path, "old.sav", b"old", NOW - 10 * 10**9)
    during = _file(tmp_path, "new.sav", b"new", NOW + 5 * 10**9)

    result = attribute([before, during], {}, since_ns=NOW)

    assert list(result.tracked) == [during.key] and result.changed == [during.key]
    assert result.unattributed == [before.key]
    assert result.tracked[during.key].sha256 == sha256_of(during.path)


def test_a_file_just_before_the_start_still_counts(tmp_path):
    close = _file(tmp_path, "a.sav", b"a", NOW - WINDOW_SLACK_NS + 1)
    assert attribute([close], {}, since_ns=NOW).changed == [close.key]


def test_without_a_session_only_accepted_files_are_taken(tmp_path):
    mine = _file(tmp_path, "mine.sav", b"1", NOW)
    other = _file(tmp_path, "other.sav", b"2", NOW)

    nothing = attribute([mine, other], {})
    assert nothing.tracked == {} and sorted(nothing.unattributed) == sorted([mine.key, other.key])

    some = attribute([mine, other], {}, accept=lambda key: key == mine.key)
    assert list(some.tracked) == [mine.key] and some.unattributed == [other.key]


def test_a_tracked_file_stays_the_games_and_is_reported_only_when_its_content_changes(tmp_path):
    save = _file(tmp_path, "a.sav", b"v1", NOW)
    first = attribute([save], {}, since_ns=NOW)

    again = attribute([save], first.tracked)
    assert again.changed == [] and again.tracked == first.tracked

    os.utime(save.path, ns=(NOW + 10**9, NOW + 10**9))  # touched, same bytes
    touched = attribute([save], first.tracked)
    assert touched.changed == [] and touched.tracked[save.key].mtime_ns == NOW + 10**9

    save.path.write_bytes(b"version two")
    os.utime(save.path, ns=(NOW + 2 * 10**9, NOW + 2 * 10**9))
    edited = attribute([save], touched.tracked)
    assert edited.changed == [save.key]


def test_a_file_that_is_gone_drops_out_of_the_tracked_set(tmp_path):
    save = _file(tmp_path, "a.sav", b"v1", NOW)
    first = attribute([save], {}, since_ns=NOW)
    save.path.unlink()
    assert attribute([], first.tracked).tracked == {}
