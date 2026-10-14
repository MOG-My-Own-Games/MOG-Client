from pathlib import Path

from mog_client import manager


def test_a_chain_is_sent_with_the_start_request_and_no_single_installer_is_picked(monkeypatch, tmp_path):
    sent = {}

    class FakeClient:
        c = type("C", (), {"base": "http://x"})()

        def get_session(self, game_id, session_id=None):
            return None

        def start_session(self, game_id, installer_path, proton_build, ttl, **kw):
            sent.update(installer_path=installer_path, **kw)
            # Refused for now, so run_install waits for a place and the stop below ends it before any download.
            raise RuntimeError("HTTP 429: Too many concurrent installs")

    class Stop:
        def is_set(self):
            return True

        def wait(self, timeout=None):
            return True

        def set(self):
            pass

    steps = [{"path": "setup.exe", "source_path": None}, {"path": "p1.exe", "source_path": None}]
    monkeypatch.setattr(manager, "load_library", lambda: {})
    monkeypatch.setattr(manager, "_update", lambda rec, **kw: None)
    monkeypatch.setattr(manager, "save_library", lambda lib: None)
    monkeypatch.setattr(manager, "default_root", lambda settings: tmp_path)

    manager.run_install(
        FakeClient(), {"id": 5, "name": "Game"}, object(), Stop(), lambda m: None,
        lambda s: None, lambda a, b: None, steps=steps,
    )

    assert sent["steps"] == steps
    assert sent["installer_path"] is None


def _start_with(installer, monkeypatch, tmp_path):
    sent = {}

    class FakeClient:
        c = type("C", (), {"base": "http://x"})()

        def get_session(self, game_id, session_id=None):
            return None

        def start_session(self, game_id, installer_path, proton_build, ttl, **kw):
            sent.update(installer_path=installer_path, **kw)
            raise RuntimeError("HTTP 429: Too many concurrent installs")

    class Stop:
        def is_set(self):
            return True

        def wait(self, timeout=None):
            return True

        def set(self):
            pass

    monkeypatch.setattr(manager, "load_library", lambda: {})
    monkeypatch.setattr(manager, "_update", lambda rec, **kw: None)
    monkeypatch.setattr(manager, "save_library", lambda lib: None)
    monkeypatch.setattr(manager, "default_root", lambda settings: tmp_path)
    manager.run_install(
        FakeClient(), {"id": 5, "name": "Game"}, object(), Stop(), lambda m: None,
        lambda s: None, lambda a, b: None, installer=installer,
    )
    return sent


def test_an_installer_picked_inside_a_disc_is_started_with_its_disc_as_the_source(monkeypatch, tmp_path):
    inner = {"path": "setup.exe", "kind": "executable (inside)", "source_path": "2 - Setup/DISK1.ISO"}

    sent = _start_with(inner, monkeypatch, tmp_path)

    assert sent["installer_path"] == "setup.exe" and sent["source_path"] == "2 - Setup/DISK1.ISO"


def test_a_disc_picked_as_the_installer_is_still_the_source_with_no_installer_named(monkeypatch, tmp_path):
    disc = {"path": "2 - Setup/DISK1.ISO", "kind": "disc image"}

    sent = _start_with(disc, monkeypatch, tmp_path)

    assert sent["installer_path"] is None and sent["source_path"] == "2 - Setup/DISK1.ISO"
