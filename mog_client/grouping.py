"""Games the server matched to the same IGDB title are versions of one game."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    members: list[dict]  # every game of the title
    versions: list[dict]  # the ones still on the server's disk, i.e. installable

    @property
    def game(self) -> dict:
        return self.versions[0]


def group_games(games: list[dict]) -> list[Group]:
    """One group per IGDB id, in the order its first game appears; a game without a
    match is its own group. Games missing from the server's disk are never offered
    as versions unless nothing else is left."""
    buckets: dict[object, list[dict]] = {}
    for game in games:
        key = game.get("igdb_id") or ("single", game["id"])
        buckets.setdefault(key, []).append(game)
    groups = []
    for members in buckets.values():
        present = [g for g in members if not g.get("missing_from_fs")]
        groups.append(Group(members=members, versions=present or members))
    return groups
