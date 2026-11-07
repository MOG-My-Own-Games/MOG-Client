"""Games of the same title (the server says which: the same IGDB match, or tied by hand) are versions of one game."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    members: list[dict]  # every game of the title
    versions: list[dict]  # the ones still on the server's disk, i.e. installable

    @property
    def game(self) -> dict:
        return self.versions[0]


def title_key(game: dict) -> object:
    """The title a game is a version of: the server's `title_key`, else (a server that does not send it) the IGDB id."""
    return game.get("title_key") or game.get("igdb_id") or ("single", game["id"])


def group_games(games: list[dict]) -> list[Group]:
    """One group per title, in the order its first game appears; a game without a
    match is its own group. Games missing from the server's disk are never offered
    as versions unless nothing else is left."""
    buckets: dict[object, list[dict]] = {}
    for game in games:
        key = title_key(game)
        buckets.setdefault(key, []).append(game)
    groups = []
    for members in buckets.values():
        present = [g for g in members if not g.get("missing_from_fs")]
        groups.append(Group(members=members, versions=present or members))
    return groups


SAVES_ONLY, SAVES_IMPORTED, ADDONS_ONLY, GONE = "saves-only", "saves-imported", "addons-only", "gone"


def gone_game(rec) -> dict:
    """The stand-in for a game this computer has a record of and the server has no entry for at all (deleted there): it is
    listed so it can still be played or removed, never removed on its own (this computer may yet send its saves or mods, and
    the entry is made again then). Saves or mods on the server keep the entry, so those are other states."""
    return {"id": rec.game_id, "name": rec.name, "fs_name": rec.server_name or rec.name, "library_id": None, "igdb_id": None, "gone": True}


def has_no_files(game: dict) -> bool:
    """Whether the server has nothing to install the game from: one added by a client only to sync its saves (it never had files
    there), or gone from disk with only its saves kept."""
    return bool(game.get("imported") or game.get("saves_only") or game.get("gone"))


def corner_state(game: dict) -> str | None:
    """What the server says is special about a game's files, for a badge on its cover: gone from
    disk but its saves are kept, only there to sync the saves of a game added from outside MOG (it never had files here),
    or only add-ons (mods, DLC) and nothing that installs the game. A game the server has no entry for is `GONE`."""
    if game.get("gone"):
        return GONE
    if game.get("saves_only"):
        return SAVES_IMPORTED if game.get("imported") else SAVES_ONLY
    if game.get("addons_only") and not game.get("missing_from_fs"):
        return ADDONS_ONLY
    return None
