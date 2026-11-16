"""After the last byte is downloaded every file is read again (sha1): the window is told, or the bar sits at its end for
as long as that takes and the person takes it for a hang."""

from types import SimpleNamespace

from mog_client import config, manager
from mog_client.config import Settings, load_library
from mog_client.transfer import RepairResult


def _run(tmp_path, monkeypatch, verify, stages):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(manager, "download_all_files", lambda *a, **k: ([], True))
    monkeypatch.setattr(manager, "verify_and_repair", verify)
    monkeypatch.setattr(manager, "poll_session", lambda *a, **k: {"state": "done"})

    class Server:
        c = SimpleNamespace(base="http://s")

        def get_session(self, gid):
            return {"id": 9, "state": "done"}

        def list_files(self, gid, session_id=None):
            return {"files": [{"path": "a", "size_bytes": 1}]}

    settings = Settings(install_dirs=[str(tmp_path / "games")])
    return manager.run_install(
        Server(), {"id": 5, "name": "Eta"}, settings, SimpleNamespace(is_set=lambda: False),
        lambda m: None, lambda s: None, lambda a, b: None, on_stage=lambda *a: stages.append(a),
    )


def test_the_window_hears_the_check_then_the_finish(tmp_path, monkeypatch):
    stages = []

    def verify(client, gid, out_dir, on_manifest=None, on_bytes=None, **k):
        on_manifest([{"path": "Eta/a.exe", "size_bytes": 10, "sha1": "ab" * 20}])
        on_bytes(4, 10)
        on_bytes(10, 10)
        return RepairResult()

    rec = _run(tmp_path, monkeypatch, verify, stages)

    assert stages == [
        (manager.STAGE_VERIFY, 0, 0), (manager.STAGE_VERIFY, 4, 10), (manager.STAGE_VERIFY, 10, 10), (manager.STAGE_FINISH, 0, 0),
    ]
    assert rec.state == "awaiting_executable"


def test_a_check_that_is_stopped_leaves_the_game_installing_to_be_checked_again(tmp_path, monkeypatch):
    stages = []

    def verify(client, gid, out_dir, on_manifest=None, **k):
        return RepairResult(stopped=True)

    rec = _run(tmp_path, monkeypatch, verify, stages)

    assert stages == [(manager.STAGE_VERIFY, 0, 0)]  # no "finishing" for something that did not finish
    assert rec.state == "installing" and load_library()[5].state == "installing"
