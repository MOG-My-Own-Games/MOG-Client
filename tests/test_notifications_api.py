from mog_client.api import MogClient


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, path, **kw):
        self.calls.append(("GET", path))
        return 200, {"notifications": [], "unread": 0}

    def post_json(self, path, body, **kw):
        self.calls.append(("POST", path))
        return 204, {}

    def delete_json(self, path, **kw):
        self.calls.append(("DELETE", path))
        return 200, {}


def test_notification_endpoints():
    fake = _FakeClient()
    client = MogClient(fake)
    assert client.notifications()["unread"] == 0
    client.mark_notifications_read()
    client.mark_notifications_read(4)
    client.delete_notifications()
    client.delete_notifications(4)
    assert fake.calls == [
        ("GET", "/api/notifications"),
        ("POST", "/api/notifications/read"),
        ("POST", "/api/notifications/4/read"),
        ("DELETE", "/api/notifications"),
        ("DELETE", "/api/notifications/4"),
    ]


def test_game_size_reads_the_servers_figure_or_none():
    class Ok:
        def get_json(self, path, **kw):
            return 200, {"size_bytes": 123456, "file_count": 3}

    class Old:
        def get_json(self, path, **kw):
            return 404, {"detail": "Not Found"}

    assert MogClient(Ok()).game_size(5) == 123456
    assert MogClient(Old()).game_size(5) is None
