"""The newest Proton-GE release of each major version (GE-Proton7-55, GE-Proton8-32, GE-Proton9-27, GE-Proton10-N, ...), which umu and
Faugus download by name on first use, and the few other builds games are known to need (`EXTRA`). An old game often runs better on an old major, and the last release of a major is
the one that is usually right. Stdlib only."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from mog_client import protondl

RELEASES_URL = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases?per_page=100"
# Majors that are finished, so the list does not depend on the release list reaching back that far.
FINISHED = {7: "GE-Proton7-55", 8: "GE-Proton8-32", 9: "GE-Proton9-27"}
# Releases that are no major's last, offered too: some games need exactly one (UMU-Proton, from Open-Wine-Components, for the
# Proton 9.0 line). Oldest first, like the rest; they are downloaded when chosen.
EXTRA = ("UMU-Proton-9.0-2", "UMU-Proton-9.0-4e")
UMU_RELEASES_URL = "https://api.github.com/repos/Open-Wine-Components/umu-proton/releases?per_page=100"
CACHE_SECONDS = 24 * 3600
MAX_PAGES = 6  # of 100 releases each, per project: GE-Proton has about 200 and UMU-Proton fewer

_TAG = re.compile(r"GE-Proton(\d+)-(\d+)$")


def latest_per_major(tags: list[str]) -> list[str]:
    """The highest release of each major among `tags` (others ignored), plus the finished majors, oldest major first."""
    best: dict[int, tuple[int, str]] = {major: (int(tag.rsplit("-", 1)[1]), tag) for major, tag in FINISHED.items()}
    for tag in tags:
        match = _TAG.match(tag)
        if match:
            major, minor = int(match.group(1)), int(match.group(2))
            if major not in best or minor > best[major][0]:
                best[major] = (minor, tag)
    return [best[major][1] for major in sorted(best)]


def tags_from_releases(payload) -> list[str]:
    return [r["tag_name"] for r in payload if isinstance(r, dict) and isinstance(r.get("tag_name"), str)] if isinstance(payload, list) else []


def available(
    cache: Path,
    fetch: Callable[[str], bytes],
    refresh: bool = False,
    clock: Callable[[], float] = time.time,
) -> list[str]:
    """The newest release of each major: from the cache while it is under a day old, else from GitHub (saved for next time).
    Offline, an old cache is used, and with none the finished majors alone."""
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    tags = cached.get("tags") if isinstance(cached.get("tags"), list) else []
    fresh = isinstance(cached.get("at"), (int, float)) and clock() - cached["at"] < CACHE_SECONDS
    if refresh or not fresh:
        try:
            tags = tags_from_releases(json.loads(fetch(RELEASES_URL)))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"at": clock(), "tags": tags}))
        except (RuntimeError, OSError, ValueError):
            pass  # offline or rate limited: what is known stays
    return [*EXTRA, *latest_per_major(tags)]


def sort_key(name: str) -> tuple:
    """Proton-GE first, then UMU-Proton, each by its numbers as numbers (GE-Proton7-9 before GE-Proton7-17)."""
    return (name.startswith("UMU-"), tuple(int(n) for n in re.findall(r"\d+", name)), name)


def _every_tag(fetch: Callable[[str], bytes]) -> list[str]:
    """Every release of both projects, page by page (a page that cannot be had ends that project: what came is kept)."""
    found: set[str] = set()
    for base in (RELEASES_URL, UMU_RELEASES_URL):
        for page in range(1, MAX_PAGES + 1):
            try:
                payload = json.loads(fetch(f"{base}&page={page}"))
            except (RuntimeError, OSError, ValueError):
                break
            found.update(tag for tag in tags_from_releases(payload) if protondl.is_fetchable(tag))
            if not isinstance(payload, list) or len(payload) < 100:
                break
    return sorted(found, key=sort_key)


def every(
    cache: Path,
    fetch: Callable[[str], bytes],
    refresh: bool = False,
    clock: Callable[[], float] = time.time,
) -> list[str]:
    """Every Proton-GE and UMU-Proton release there is, oldest first, for the list of all versions: from the cache while it is
    under a day old, else from GitHub (a few requests; saved for next time). Offline, an old cache is used, and with none the
    shortlist `available` knows."""
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    tags = cached.get("all") if isinstance(cached.get("all"), list) else []
    fresh = isinstance(cached.get("at"), (int, float)) and clock() - cached["at"] < CACHE_SECONDS
    if refresh or not fresh or not tags:
        fetched = _every_tag(fetch)
        if fetched:
            tags = fetched
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"at": clock(), "all": tags}))
            except OSError:
                pass
    return tags or sorted({*EXTRA, *FINISHED.values()}, key=sort_key)
