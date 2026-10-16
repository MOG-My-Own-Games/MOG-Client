"""What changed in the version that is running: the notes the release build bundled (scripts/release_notes.py writes
_changelog.py). Qt-free."""

from __future__ import annotations

from mog_client.config import Settings

try:
    from mog_client._changelog import CHANGELOG
except ImportError:  # a dev run or a build made by hand: no notes
    CHANGELOG = None


def text() -> str | None:
    """The notes as Markdown: "" when the version has nothing to list, None when this build carries no notes."""
    return None if CHANGELOG is None else CHANGELOG.strip()


def should_show(settings: Settings, version: str) -> bool:
    """The notes are worth opening now: this build has some, and the user has not been shown them for this version (so an
    update brings them once)."""
    return bool(text()) and version != "dev" and settings.changelog_seen != version
