from mog_client import logstore, scrape


class Server:
    """Serves only the kinds a game has chosen."""

    def __init__(self, **kinds):
        self.kinds = kinds

    def get_image(self, path):
        return self.kinds.get(path.rsplit("/", 1)[1])


GAME = {
    "id": 138,
    "name": "Jazz",
    "cover_path": "https://cdn/cover.jpg",
    "igdb_metadata": {"screenshots": [{"url": "https://cdn/shot1.jpg"}, {"url": "https://cdn/shot2.jpg"}]},
}


def fetch(monkeypatch, urls_ok=True):
    fetched = []

    def fake_fetch(url):
        if not urls_ok:
            raise RuntimeError("offline")
        fetched.append(url)
        return f"bytes of {url}".encode()

    monkeypatch.setattr(scrape, "fetch_url", fake_fetch)
    return fetched


def test_everything_the_server_has_is_taken_from_it_and_nothing_else_is_fetched(monkeypatch):
    fetched = fetch(monkeypatch)
    server = Server(cover=b"c", banner=b"b", hero=b"h", logo=b"l", icon=b"i")
    assert scrape.fetch_artwork(GAME, server) == {"portrait": b"c", "wide": b"b", "hero": b"h", "logo": b"l", "icon": b"i"}
    assert fetched == []


def test_a_game_with_only_a_cover_on_the_server_gets_banner_hero_and_icon_from_igdb(monkeypatch):
    fetched = fetch(monkeypatch)
    art = scrape.fetch_artwork(GAME, Server(cover=b"c"))

    assert art["portrait"] == b"c" and art["icon"] == b"c"  # the cover stands in for the icon
    assert art["hero"] == b"bytes of https://cdn/shot1.jpg" and art["wide"] == b"bytes of https://cdn/shot2.jpg"
    assert "logo" not in art and "https://cdn/cover.jpg" not in fetched  # the server's cover is not fetched twice


def test_what_is_still_missing_is_said_in_the_log(monkeypatch):
    fetch(monkeypatch)
    logstore.store.clear()
    scrape.fetch_artwork({"id": 1, "name": "Bare"}, Server(cover=b"c"))
    records = [(r.level, r.text) for r in logstore.store.records()]
    assert ("warning", "Bare has no banner, hero, logo artwork: choose it in the server's scrape dialog") in records
    assert any(level == "info" and text.startswith("Artwork of Bare: from the server cover") for level, text in records)
    logstore.store.clear()


def test_an_older_server_without_media_endpoints_still_gets_the_cover_from_igdb(monkeypatch):
    fetch(monkeypatch)
    art = scrape.fetch_artwork(GAME, Server())
    assert art["portrait"] == b"bytes of https://cdn/cover.jpg" and art["icon"] == art["portrait"]


def test_an_unreachable_provider_leaves_the_server_artwork_alone(monkeypatch):
    fetch(monkeypatch, urls_ok=False)
    assert scrape.fetch_artwork(GAME, Server(cover=b"c")) == {"portrait": b"c", "icon": b"c"}
    assert scrape.fetch_artwork({"id": 2, "name": "X"}, None) == {}


def test_the_release_date_is_written_out_in_english_whatever_the_locale():
    from mog_client.scrape import long_date, metadata_lines

    assert long_date(782_956_800) == "24 October 1994"
    assert long_date(865_036_800) == "31 May 1997"
    assert long_date(1_704_067_200) == "1 January 2024"  # no zero-padded day
    assert metadata_lines({"igdb_metadata": {"first_release_date": 782_956_800}}) == [("Released", "24 October 1994")]


def test_a_duration_is_minutes_under_an_hour_and_half_hours_above():
    from mog_client.scrape import format_duration

    assert [format_duration(s) for s in (1800, 2700, 3599, 3600, 36_000, 45_000, 43_200)] == [
        "30m", "45m", "1h", "1h", "10h", "12.5h", "12h",
    ]


def test_howlongtobeat_rows_list_only_the_times_the_server_has():
    from mog_client.scrape import hltb_lines

    meta = {"main_story": 36_000, "main_plus_extra": 54_000, "completionist": 0, "all_styles": 50_000}
    assert hltb_lines({"hltb_metadata": meta}) == [("Main Story", "10h"), ("Main + Extra", "15h")]


def test_no_howlongtobeat_rows_without_times_or_from_an_older_server():
    from mog_client.scrape import hltb_lines

    assert hltb_lines({}) == []
    assert hltb_lines({"hltb_metadata": None}) == []
    assert hltb_lines({"hltb_id": 0, "hltb_metadata": {}}) == []
    assert hltb_lines({"hltb_metadata": {"main_story": "n/a"}}) == []
