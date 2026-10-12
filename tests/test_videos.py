import pytest

from mog_client import videos


def test_entries_keep_the_servers_order_and_only_what_a_url_can_carry():
    payload = {
        "videos": [
            {"kind": "intro", "video_id": "abcdefghijk", "title": "Trailer", "source": "igdb"},
            {"kind": "gameplay", "video_id": "lmno-pq_rst", "title": None, "source": "youtube"},
            {"kind": "intro", "video_id": "short", "title": "too short"},
            {"kind": "intro", "video_id": "a b c d e f g", "title": "spaces"},
            {"kind": "intro", "video_id": "abc/../defgh", "title": "path"},
            {"kind": "review", "video_id": "uvwxyzabcde", "title": "unknown kind"},
            "not a video",
        ]
    }

    assert videos.entries(payload) == [
        {"kind": "intro", "video_id": "abcdefghijk", "title": "Trailer"},
        {"kind": "gameplay", "video_id": "lmno-pq_rst", "title": ""},
    ]


@pytest.mark.parametrize("payload", [None, [], {}, {"videos": None}, {"videos": "x"}, {"_raw": "<html>"}])
def test_an_answer_without_videos_is_an_empty_list(payload):
    assert videos.entries(payload) == []


def test_urls():
    assert videos.watch_url("abcdefghijk") == "https://www.youtube.com/watch?v=abcdefghijk"
    assert videos.thumbnail_url("abcdefghijk") == "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"
