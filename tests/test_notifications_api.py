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


def test_game_sizes_reads_the_breakdown_or_none():
    class Ok:
        def get_json(self, path, **kw):
            return 200, {"installer_bytes": 3_677_174_794, "cache_bytes": 5, "saves_bytes": 2, "total_bytes": 3_677_174_801}

    class Old:
        def get_json(self, path, **kw):
            return 404, {"detail": "Not Found"}

    held = MogClient(Ok()).game_sizes(5)
    assert held == {"installer": 3_677_174_794, "cache": 5, "saves": 2, "total": 3_677_174_801}
    assert MogClient(Old()).game_sizes(5) is None
