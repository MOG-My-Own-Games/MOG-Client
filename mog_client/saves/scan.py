"""Deciding which files belong to a game's saves, and which of them changed."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mog_client.saves.locations import Candidate
from mog_client.saves.state import FileInfo

# File times are coarse on some filesystems and a launcher takes a moment: a file written just
# before the process was seen starting still counts as this session's.
WINDOW_SLACK_NS = 2_000_000_000


@dataclass
class ScanResult:
    # The game's files after this scan, by key: what the next archive holds and the next scan compares to.
    tracked: dict[str, FileInfo] = field(default_factory=dict)
    # Keys whose content is new or different since the previous scan.
    changed: list[str] = field(default_factory=list)
    # Files that could be the game's but were not attributed (no session window, not confirmed).
    unattributed: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def attribute(
    candidates: Iterable[Candidate],
    tracked: dict[str, FileInfo],
    since_ns: int | None = None,
    accept: Callable[[str], bool] = lambda key: False,
) -> ScanResult:
    """Work out the game's files from what is on disk.

    A file already tracked stays the game's; when its size or time changed it is hashed again and
    counts as changed only if the content really differs. A file not tracked yet becomes the
    game's when it was written during the session (`since_ns`, the time the game started) or,
    with no session to go by, when `accept(key)` says it is this game's (a prefix only this game
    uses, or a folder the user confirmed)."""
    result = ScanResult()
    for candidate in candidates:
        try:
            stat = candidate.path.stat()
        except OSError:
            continue
        previous = tracked.get(candidate.key)
        if previous and (previous.size, previous.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
            result.tracked[candidate.key] = previous
            continue
        if previous is None:
            in_session = since_ns is not None and stat.st_mtime_ns >= since_ns - WINDOW_SLACK_NS
            if not (in_session or (since_ns is None and accept(candidate.key))):
                result.unattributed.append(candidate.key)
                continue
        try:
            digest = sha256_of(candidate.path)
        except OSError:
            continue
        result.tracked[candidate.key] = FileInfo(stat.st_size, stat.st_mtime_ns, digest)
        if previous is None or previous.sha256 != digest:
            result.changed.append(candidate.key)
    return result
