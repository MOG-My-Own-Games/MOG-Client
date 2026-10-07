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


def released(group: Group) -> float | None:
    return ((group.game.get("igdb_metadata") or {}).get("first_release_date")) or None


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
    ones after it. A game without a release date, or never played, goes after the rest."""
    items = sorted(groups, key=lambda g: g.game["name"].lower(), reverse=order == "za")
    if order in ("newest", "oldest"):
        dated = sorted((g for g in items if released(g) is not None), key=released, reverse=order == "newest")
        items = dated + [g for g in items if released(g) is None]
    if installed_first:
        items.sort(key=lambda g: not installed(g))
    if last_played_first:
        items.sort(key=lambda g: -(played(g) or 0) if played(g) else float("inf"))
    items.sort(key=lambda g: not installing(g))
    return items
