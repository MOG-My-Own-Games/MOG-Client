"""The newest Proton-GE release of each major version (GE-Proton8-32, GE-Proton9-27, GE-Proton10-N, ...), which umu and
Faugus download by name on first use. An old game often runs better on an old major, and the last release of a major is
the one that is usually right. Stdlib only."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

RELEASES_URL = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases?per_page=100"
# Majors that are finished, so the list does not depend on the release list reaching back that far.
FINISHED = {8: "GE-Proton8-32", 9: "GE-Proton9-27"}
CACHE_SECONDS = 24 * 3600

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
    return latest_per_major(tags)
