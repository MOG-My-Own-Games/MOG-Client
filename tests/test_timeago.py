from datetime import datetime, timedelta, timezone

import pytest

from mog_client import timeago

NOW = datetime(2026, 10, 28, 13, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "delta, words",
    [
        (timedelta(seconds=5), "just now"),
        (timedelta(seconds=-30), "just now"),  # a clock a little ahead of ours
        (timedelta(minutes=1), "1 minute ago"),
        (timedelta(minutes=10), "10 minutes ago"),
        (timedelta(hours=1, minutes=20), "1 hour ago"),
        (timedelta(hours=5), "5 hours ago"),
        (timedelta(days=1), "1 day ago"),
        (timedelta(days=5), "5 days ago"),
        (timedelta(days=7), "last week"),
        (timedelta(days=13), "last week"),
        (timedelta(days=14), "2 weeks ago"),
        (timedelta(days=29), "4 weeks ago"),
        (timedelta(days=30), "one month ago"),
        (timedelta(days=59), "one month ago"),
        (timedelta(days=60), "2 months ago"),
        (timedelta(days=364), "11 months ago"),
        (timedelta(days=365), "a year ago"),
        (timedelta(days=800), "2 years ago"),
    ],
)
def test_ago_in_words(delta, words):
    assert timeago.ago(NOW - delta, NOW) == words


def test_parse_reads_the_servers_times_as_utc():
    assert timeago.parse("2026-10-28T13:06:00Z") == NOW
    assert timeago.parse("2026-10-28T13:06:00") == NOW  # no zone: the server's UTC
    assert timeago.parse("2026-10-28T15:06:00+02:00") == NOW
    assert timeago.parse(None) is None and timeago.parse("") is None and timeago.parse("yesterday") is None


def test_full_names_the_day_and_the_time():
    text = timeago.full(NOW)
    assert "2026" in text and "October" in text and ":" in text
