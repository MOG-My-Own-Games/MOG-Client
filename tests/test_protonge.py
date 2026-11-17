import json
from pathlib import Path

import pytest

from mog_client import protonge

NOW = 1_800_000_000.0


def test_the_last_release_of_each_major_is_kept_and_the_finished_majors_are_always_there():
    tags = ["GE-Proton10-3", "GE-Proton10-12", "GE-Proton10-9", "GE-Proton11-1", "GE-Proton9-20", "Proton-9.0-x", "latest"]

    assert protonge.latest_per_major(tags) == ["GE-Proton7-55", "GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-12", "GE-Proton11-1"]
    assert protonge.latest_per_major([]) == ["GE-Proton7-55", "GE-Proton8-32", "GE-Proton9-27"]


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

    assert first == again == [*protonge.EXTRA, "GE-Proton7-55", "GE-Proton8-32", "GE-Proton9-27", "GE-Proton10-4"]
    assert next_day[-1] == "GE-Proton10-9" and len(calls) == 2


def test_offline_the_old_cache_serves_and_without_one_the_finished_majors_do(tmp_path):
    def offline(url):
        raise RuntimeError("offline")

    cache = tmp_path / "ge.json"
    assert protonge.available(cache, offline, clock=lambda: NOW) == [*protonge.EXTRA, "GE-Proton7-55", "GE-Proton8-32", "GE-Proton9-27"]

    cache.write_text(json.dumps({"at": NOW - 10 * 24 * 3600, "tags": ["GE-Proton10-4"]}))
    assert protonge.available(cache, offline, clock=lambda: NOW)[-1] == "GE-Proton10-4"


def test_a_cache_that_is_not_json_is_ignored(tmp_path):
    cache = tmp_path / "ge.json"
    cache.write_text("{not json")

    assert protonge.available(cache, _fetch(["GE-Proton10-4"], []), clock=lambda: NOW)[-1] == "GE-Proton10-4"


def test_the_builds_some_games_need_are_offered_after_the_majors_in_the_list_the_page_shows_newest_first():
    assert "UMU-Proton-9.0-2" in protonge.EXTRA
    offered = protonge.available(Path("/nonexistent/ge.json"), lambda url: (_ for _ in ()).throw(RuntimeError("offline")), clock=lambda: NOW)
    assert offered.index("UMU-Proton-9.0-2") < offered.index("GE-Proton7-55")  # the page lists them reversed: these come last


def _pages(by_url):
    calls = []

    def fetch(url):
        calls.append(url)
        for part, payload in by_url.items():
            if part in url:
                return json.dumps(payload).encode()
        raise RuntimeError("no such page")

    return fetch, calls


def test_every_release_of_both_projects_is_listed_oldest_first_by_the_numbers(tmp_path):
    ge = [{"tag_name": t} for t in ("GE-Proton7-17", "GE-Proton10-2", "GE-Proton7-9", "latest")]
    umu = [{"tag_name": t} for t in ("UMU-Proton-9.0-4e", "UMU-Proton-10.0-1", "UMU-Proton-9.0-2")]
    fetch, _ = _pages({"proton-ge-custom": ge, "umu-proton": umu})

    got = protonge.every(tmp_path / "all.json", fetch, clock=lambda: NOW)

    assert got == ["GE-Proton7-9", "GE-Proton7-17", "GE-Proton10-2", "UMU-Proton-9.0-2", "UMU-Proton-9.0-4e", "UMU-Proton-10.0-1"]


def test_a_full_page_of_releases_is_followed_by_the_next(tmp_path):
    first = [{"tag_name": f"GE-Proton7-{n}"} for n in range(1, 101)]
    second = [{"tag_name": "GE-Proton8-1"}]
    calls = []

    def fetch(url):
        calls.append(url)
        if "umu-proton" in url:
            return b"[]"
        return json.dumps(first if url.endswith("page=1") else second).encode()

    got = protonge.every(tmp_path / "all.json", fetch, clock=lambda: NOW)

    assert len(got) == 101 and got[-1] == "GE-Proton8-1" and "GE-Proton7-100" in got
    assert any(u.endswith("page=2") and "ge-custom" in u for u in calls)


def test_the_full_list_is_cached_a_day_and_survives_being_offline(tmp_path):
    cache = tmp_path / "all.json"
    fetch, calls = _pages({"proton-ge-custom": [{"tag_name": "GE-Proton7-17"}], "umu-proton": []})
    protonge.every(cache, fetch, clock=lambda: NOW)
    protonge.every(cache, fetch, clock=lambda: NOW + 3600)
    assert len(calls) == 2  # one page of each project, once

    def offline(url):
        raise RuntimeError("offline")

    assert protonge.every(cache, offline, clock=lambda: NOW + 3 * 24 * 3600) == ["GE-Proton7-17"]
    assert "GE-Proton7-55" in protonge.every(tmp_path / "none.json", offline, clock=lambda: NOW)
