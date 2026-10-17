import json
from pathlib import Path

import pytest

from mog_client import protonge

NOW = 1_800_000_000.0


def test_the_last_release_of_each_major_is_kept_and_the_finished_majors_are_always_there():
    tags = ["GE-Proton10-3", "GE-Proton10-12", "GE-Proton10-9", "GE-Proton11-1", "GE-Proton9-20", "Proton-9.0-x", "latest"]

    assert protonge.latest_per_major(tags) == ["GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-12", "GE-Proton11-1"]
    assert protonge.latest_per_major([]) == ["GE-Proton8-32", "GE-Proton9-27"]


def test_a_release_list_from_github_is_read_for_its_tags_and_nothing_else():
    assert protonge.tags_from_releases([{"tag_name": "GE-Proton10-1"}, {"name": "x"}, "junk"]) == ["GE-Proton10-1"]
    assert protonge.tags_from_releases({"message": "API rate limit exceeded"}) == []


def _fetch(tags, calls):
    def fetch(url):
        calls.append(url)
        return json.dumps([{"tag_name": t} for t in tags]).encode()

    return fetch


def test_the_list_is_fetched_once_a_day_and_kept_in_the_cache(tmp_path):
    cache, calls = tmp_path / "ge.json", []

    first = protonge.available(cache, _fetch(["GE-Proton10-4"], calls), clock=lambda: NOW)
    again = protonge.available(cache, _fetch(["GE-Proton10-9"], calls), clock=lambda: NOW + 3600)
    next_day = protonge.available(cache, _fetch(["GE-Proton10-9"], calls), clock=lambda: NOW + 2 * 24 * 3600)

    assert first == again == [*protonge.EXTRA, "GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-4"]
    assert next_day[-1] == "GE-Proton10-9" and len(calls) == 2


def test_offline_the_old_cache_serves_and_without_one_the_finished_majors_do(tmp_path):
    def offline(url):
        raise RuntimeError("offline")

    cache = tmp_path / "ge.json"
    assert protonge.available(cache, offline, clock=lambda: NOW) == [*protonge.EXTRA, "GE-Proton8-32", "GE-Proton9-27"]

    cache.write_text(json.dumps({"at": NOW - 10 * 24 * 3600, "tags": ["GE-Proton10-4"]}))
    assert protonge.available(cache, offline, clock=lambda: NOW)[-1] == "GE-Proton10-4"


def test_a_cache_that_is_not_json_is_ignored(tmp_path):
    cache = tmp_path / "ge.json"
    cache.write_text("{not json")

    assert protonge.available(cache, _fetch(["GE-Proton10-4"], []), clock=lambda: NOW)[-1] == "GE-Proton10-4"


def test_the_builds_some_games_need_are_offered_after_the_majors_in_the_list_the_page_shows_newest_first():
    assert "UMU-Proton-9.0-2" in protonge.EXTRA
    offered = protonge.available(Path("/nonexistent/ge.json"), lambda url: (_ for _ in ()).throw(RuntimeError("offline")), clock=lambda: NOW)
    assert offered.index("UMU-Proton-9.0-2") < offered.index("GE-Proton8-32")  # the page lists them reversed: these come last
