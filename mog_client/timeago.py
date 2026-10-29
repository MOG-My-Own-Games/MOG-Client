"""When something happened, in words ("5 hours ago") and in full for a tooltip (Qt-free)."""

from __future__ import annotations

from datetime import datetime, timezone


def parse(text: str | None) -> datetime | None:
    """The moment of an ISO time as the server sends it (UTC, `Z` or none); None when it is missing or not a time."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def ago(when: datetime, now: datetime | None = None) -> str:
    seconds = ((now or datetime.now(timezone.utc)) - when).total_seconds()
    if seconds < 60:  # also a time a little ahead of this clock
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return _plural(minutes, "minute")
    hours = minutes // 60
    if hours < 24:
        return _plural(hours, "hour")
    days = hours // 24
    if days < 7:
        return _plural(days, "day")
    if days < 14:
        return "last week"
    if days < 30:
        return _plural(days // 7, "week")
    if days < 365:
        months = min(days // 30, 11)
        return "one month ago" if months == 1 else f"{months} months ago"
    years = days // 365
    return "a year ago" if years == 1 else f"{years} years ago"


def full(when: datetime) -> str:
    """The date in full, in this computer's time zone: `Wednesday 28 October 2026, 13:06`."""
    return when.astimezone().strftime("%A %d %B %Y, %H:%M")
