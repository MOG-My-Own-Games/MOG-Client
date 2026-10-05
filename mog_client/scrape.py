"""Artwork for a Steam entry, taken from what the server already scraped."""

from __future__ import annotations

from datetime import datetime, timezone

from mog_client import logstore
from mog_client.api import fetch_url, warn


def artwork_urls(game: dict) -> dict[str, str]:
    urls: dict[str, str] = {}
    meta = game.get("igdb_metadata") or {}
    cover = game.get("cover_path") or (meta.get("cover") or {}).get("url")
    if cover:
        urls["portrait"] = cover
    shots = [s.get("url") for s in meta.get("screenshots") or [] if s.get("url")]
    if shots:
        urls["hero"] = shots[0]
        urls["wide"] = shots[1] if len(shots) > 1 else shots[0]
    return urls


# Steam's names for the artwork of a shortcut, and the server's name for each kind.
STEAM_KINDS = {"portrait": "cover", "wide": "banner", "hero": "hero", "logo": "logo", "icon": "icon"}


def fetch_artwork(game: dict, client=None) -> dict[str, bytes]:
    """The images for a Steam shortcut. The server chooses, caches and serves each kind (cover,
    banner, hero, title logo, icon), so they come from it. What it has not chosen is filled from the
    game's IGDB data where there is something to use (the cover, screenshots as banner and hero art,
    the cover again as the icon), and what is still missing is said in the log."""
    out: dict[str, bytes] = {}
    if client is not None:
        for steam_kind, kind in STEAM_KINDS.items():
            blob = client.get_image(f"/api/games/{game['id']}/media/{kind}")
            if blob:
                out[steam_kind] = blob
    from_server = set(out)
    for kind, url in artwork_urls(game).items():
        if kind not in out:
            try:
                out[kind] = fetch_url(url)
            except RuntimeError as e:
                warn(f"{game.get('name', game['id'])}: could not fetch the {kind} image: {e}")
    if "icon" not in out and "portrait" in out:
        out["icon"] = out["portrait"]
    missing = [kind for steam_kind, kind in STEAM_KINDS.items() if steam_kind not in out]  # the server's names
    name = game.get("name", game.get("id"))
    named = lambda kinds: ", ".join(sorted(STEAM_KINDS[k] for k in kinds)) or "nothing"  # noqa: E731
    logstore.info(f"Artwork of {name}: from the server {named(from_server)}; filled in: {named(set(out) - from_server)}")
    if missing:
        warn(f"{name} has no {', '.join(missing)} artwork: choose it in the server's scrape dialog")
    return out


def screenshot_urls(game: dict) -> list[str]:
    meta = game.get("igdb_metadata") or {}
    return [s["url"] for s in meta.get("screenshots") or [] if s.get("url")]


def _names(items) -> str:
    return ", ".join(i["name"] for i in items or [] if i.get("name"))


def metadata_lines(game: dict) -> list[tuple[str, str]]:
    """(label, value) rows for the details page; rows with no data are skipped."""
    meta = game.get("igdb_metadata") or {}
    companies = meta.get("involved_companies") or []
    release = meta.get("first_release_date")
    rows = [
        ("Released", datetime.fromtimestamp(release, timezone.utc).strftime("%Y-%m-%d") if release else ""),
        ("Genres", _names(meta.get("genres"))),
        ("Developer", ", ".join(c["company"]["name"] for c in companies if c.get("developer") and c.get("company"))),
        ("Publisher", ", ".join(c["company"]["name"] for c in companies if c.get("publisher") and c.get("company"))),
        ("Modes", _names(meta.get("game_modes"))),
        ("Perspective", _names(meta.get("player_perspectives"))),
    ]
    return [(k, v) for k, v in rows if v]
