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
