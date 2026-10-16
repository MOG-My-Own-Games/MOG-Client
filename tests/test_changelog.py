"""The changelog opens once after an update, and only when the build carries notes for it."""

from mog_client import changelog
from mog_client.config import Settings


def test_notes_are_shown_once_per_version(monkeypatch):
    monkeypatch.setattr(changelog, "CHANGELOG", "## Fixes\n\n- a: b")
    settings = Settings()
    assert changelog.should_show(settings, "1.2.3")
    settings.changelog_seen = "1.2.3"
    assert not changelog.should_show(settings, "1.2.3")
    assert changelog.should_show(settings, "1.2.4")  # the next update brings them back


def test_a_dev_run_or_a_build_without_notes_shows_nothing(monkeypatch):
    settings = Settings()
    monkeypatch.setattr(changelog, "CHANGELOG", "## Fixes\n\n- a: b")
    assert not changelog.should_show(settings, "dev")
    for notes in (None, "", "  \n"):
        monkeypatch.setattr(changelog, "CHANGELOG", notes)
        assert not changelog.should_show(settings, "1.2.3")


def test_text_tells_no_notes_from_nothing_to_list(monkeypatch):
    monkeypatch.setattr(changelog, "CHANGELOG", None)
    assert changelog.text() is None
    monkeypatch.setattr(changelog, "CHANGELOG", "")
    assert changelog.text() == ""
