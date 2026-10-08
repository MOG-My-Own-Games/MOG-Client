from mog_client import snapshot

SERVER = "http://server:5000"


def test_the_last_library_comes_back_for_the_same_server_only(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path)
    assert snapshot.load_library(SERVER) == ([], [])

    snapshot.save_library(SERVER, [{"id": 1, "name": "A"}], [{"id": 2, "name": "Games"}])

    assert snapshot.load_library(SERVER) == ([{"id": 1, "name": "A"}], [{"id": 2, "name": "Games"}])
    assert snapshot.load_library("http://another:5000") == ([], [])  # another server's games are not shown


def test_the_user_and_the_picture_are_kept_and_a_missing_picture_is_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path)
    assert snapshot.load_user(SERVER) is None

    snapshot.save_user(SERVER, "admin", b"png bytes")
    assert snapshot.load_user(SERVER) == ("admin", b"png bytes")

    snapshot.save_user(SERVER, "admin", None)  # the picture was removed on the server
    assert snapshot.load_user(SERVER) == ("admin", None)
    assert snapshot.load_user("http://another:5000") is None


def test_a_damaged_file_is_ignored_and_forget_clears_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path)
    (tmp_path / "library.json").write_text("{not json")
    assert snapshot.load_library(SERVER) == ([], [])

    snapshot.save_library(SERVER, [{"id": 1}], [])
    snapshot.save_user(SERVER, "admin", b"x")
    snapshot.forget()
    assert snapshot.load_library(SERVER) == ([], []) and snapshot.load_user(SERVER) is None


def test_the_notifications_come_back_for_the_same_server_only_and_forget_clears_them(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path)
    assert snapshot.load_notifications(SERVER) is None

    data = {"notifications": [{"id": 3, "title": "Stuck", "read": False}], "unread": 1}
    snapshot.save_notifications(SERVER, data)

    assert snapshot.load_notifications(SERVER) == data
    assert snapshot.load_notifications("http://another:5000") is None
    (tmp_path / "notifications.json").write_text('{"server": "%s", "notifications": 5}' % SERVER)
    assert snapshot.load_notifications(SERVER) is None  # a damaged file is ignored

    snapshot.save_notifications(SERVER, data)
    snapshot.forget()
    assert snapshot.load_notifications(SERVER) is None
