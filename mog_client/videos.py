"""A game's videos as the server lists them (`GET /api/games/{id}/videos`): the gallery shows each as a thumbnail that
opens the video in the browser."""

from __future__ import annotations

import re

LABELS = {"intro": "Intro", "gameplay": "Gameplay"}
# The id is put in a URL, so only what YouTube itself uses is accepted.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def entries(payload) -> list[dict]:
    """The videos of an answer of that endpoint, as {"kind", "video_id", "title"} in the server's order (intro first).
    Anything without a YouTube id, or of a kind this client does not know, is left out."""
    videos = payload.get("videos") if isinstance(payload, dict) else None
    out = []
    for v in videos if isinstance(videos, list) else []:
        if not isinstance(v, dict) or v.get("kind") not in LABELS:
            continue
        video_id = v.get("video_id")
        if isinstance(video_id, str) and VIDEO_ID.fullmatch(video_id):
            out.append({"kind": v["kind"], "video_id": video_id, "title": str(v.get("title") or "")})
    return out


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
