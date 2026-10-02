"""Artwork for a Steam entry, taken from what the server already scraped."""

from __future__ import annotations

from datetime import datetime, timezone

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


def fetch_artwork(game: dict) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for kind, url in artwork_urls(game).items():
        try:
            out[kind] = fetch_url(url)
        except RuntimeError as e:
            warn(str(e))
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
