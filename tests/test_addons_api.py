import json
import zipfile
from pathlib import Path

import pytest

from mog_client.api import MogClient


class FakeWire:
    """The HTTP layer under MogClient: records each call and answers with what the test sets."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def _answer(self, key, default=(200, {})):
        return self.answers.get(key, default)

    def get_json(self, path, **kw):
        self.calls.append(("GET", path))
        return self._answer(("GET", path))

    def request(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        status, data = self._answer((method, path), (200, {}))
        return status, json.dumps(data).encode(), {}

    def post_file(self, path, file_path, timeout=600.0, fields=None):
        self.calls.append(("POST_FILE", path, file_path.name, fields))
        return self._answer(("POST_FILE", path), (201, {"id": 7, "name": fields["name"]}))

    def post_json(self, path, body, **kw):
        self.calls.append(("POST", path, body))
        return self._answer(("POST", path), (200, {}))

    def delete_json(self, path, **kw):
        self.calls.append(("DELETE", path))
        return self._answer(("DELETE", path))

    def download_to(self, path, dest, on_progress=None):
        self.calls.append(("DOWNLOAD", path))
        dest.write_bytes(b"zip")
        return 200


@pytest.fixture
def wire():
    return FakeWire()


@pytest.fixture
def api(wire):
    return MogClient(wire)


def test_the_saved_order_is_read_in_its_order(api, wire):
    wire.answers[("GET", "/api/games/5/install/order")] = (200, {"game_id": 5, "steps": [{"position": 0, "path": "setup.exe", "source_path": None}]})

    assert api.get_install_order(5) == [{"position": 0, "path": "setup.exe", "source_path": None}]


def test_the_order_is_replaced_with_the_steps_sent(api, wire):
    wire.answers[("PUT", "/api/games/5/install/order")] = (200, {"steps": [{"position": 0, "path": "p1.exe", "source_path": None}]})

    result = api.put_install_order(5, [{"path": "p1.exe", "source_path": None}])

    assert wire.calls[-1] == ("PUT", "/api/games/5/install/order", {"steps": [{"path": "p1.exe", "source_path": None}]})
    assert result[0]["path"] == "p1.exe"


def test_a_refused_order_raises_with_the_server_reason(api, wire):
    wire.answers[("PUT", "/api/games/5/install/order")] = (400, {"detail": "Installer listed twice: x.exe"})

    with pytest.raises(RuntimeError, match="listed twice"):
        api.put_install_order(5, [{"path": "x.exe", "source_path": None}])


def test_an_addon_is_uploaded_with_its_category_and_name(api, wire, tmp_path):
    archive = tmp_path / "diff.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Game/a.pak", b"x")

    created = api.upload_addon(5, "patch", "Patch 2.0", archive)

    assert wire.calls[-1] == ("POST_FILE", "/api/games/5/addons", "diff.zip", {"category": "patch", "name": "Patch 2.0"})
    assert created["id"] == 7


def test_addons_are_listed_downloaded_and_deleted(api, wire, tmp_path):
    wire.answers[("GET", "/api/games/5/addons")] = (200, [{"id": 7, "category": "patch", "name": "Patch 2.0"}])
    wire.answers[("DELETE", "/api/games/5/addons/7")] = (200, None)

    assert api.list_addons(5)[0]["name"] == "Patch 2.0"
    dest = tmp_path / "out.zip"
    api.download_addon(5, 7, dest)
    assert dest.read_bytes() == b"zip"
    api.delete_addon(5, 7)
    assert ("DELETE", "/api/games/5/addons/7") in wire.calls


def test_a_start_with_steps_sends_them_in_the_body(api, wire):
    wire.answers[("POST", "/api/games/5/install")] = (200, {"id": 1})

    api.start_session(5, None, None, None, steps=[{"path": "setup.exe", "source_path": None}])

    body = [c for c in wire.calls if c[0] == "POST" and c[1] == "/api/games/5/install"][-1][2]
    assert body["steps"] == [{"path": "setup.exe", "source_path": None}]
