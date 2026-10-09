"""The order of the library: a game being installed first, then (when on) the most recently played, then
(when on) the installed ones, then the chosen order (stdlib only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mog_client.grouping import Group

ORDERS = {
    "newest": "Newest release",
    "oldest": "Oldest release",
    "az": "A to Z",
    "za": "Z to A",
    "largest": "Largest first",
    "smallest": "Smallest first",
}


def iso_to_epoch(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_played(group: Group, local: dict[int, float]) -> float | None:
    """The newest of when this client started a version of the title and when the server saw its saves change."""
    times = []
    for member in group.members:
        times.append(local.get(member["id"]))
        times.append(iso_to_epoch(member.get("last_played")))
    known = [t for t in times if t is not None]
    return max(known, default=None)


def last_played_on(group: Group, local: dict[int, float], here: str | None) -> tuple[float | None, str | None]:
    """When the title was last played and on which machine: this one (`here`, its name) when its own start is the
    newest, else the machine the server says made the newest save."""
    best: tuple[float | None, str | None] = (None, None)
    for member in group.members:
        candidates = ((local.get(member["id"]), here), (iso_to_epoch(member.get("last_played")), member.get("last_played_on")))
        for when, name in candidates:
            if when is not None and (best[0] is None or when > best[0]):
                best = (when, name)
    return best


def released(group: Group) -> float | None:
    return ((group.game.get("igdb_metadata") or {}).get("first_release_date")) or None


def size_of(group: Group) -> int | None:
    return group.game.get("size_bytes")


def sort_groups(
    groups: list[Group],
    *,
    order: str,
    installing: Callable[[Group], bool],
    installed: Callable[[Group], bool],
    played: Callable[[Group], float | None],
    last_played_first: bool,
    installed_first: bool,
) -> list[Group]:
    """Stable passes from the least to the most important rule, so each later rule only breaks the ties of the
    ones after it. A game without a release date or a size, or never played, goes after the rest."""
    items = sorted(groups, key=lambda g: g.game["name"].lower(), reverse=order == "za")
    if order in ("newest", "oldest"):
        dated = sorted((g for g in items if released(g) is not None), key=released, reverse=order == "newest")
        items = dated + [g for g in items if released(g) is None]
    if order in ("largest", "smallest"):
        sized = sorted((g for g in items if size_of(g) is not None), key=size_of, reverse=order == "largest")
        items = sized + [g for g in items if size_of(g) is None]
    if installed_first:
        items.sort(key=lambda g: not installed(g))
    if last_played_first:
        items.sort(key=lambda g: -(played(g) or 0) if played(g) else float("inf"))
    items.sort(key=lambda g: not installing(g))
    return items
