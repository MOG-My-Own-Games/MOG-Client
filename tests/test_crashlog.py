import time

import pytest

from mog_client import crashlog


@pytest.fixture
def log(monkeypatch, tmp_path):
    monkeypatch.setattr(crashlog, "data_dir", lambda: tmp_path)
    yield crashlog.crash_path()
    crashlog.stop()


def stuck_here(seconds: float) -> None:
    time.sleep(seconds)


def test_a_stalled_interface_leaves_its_stack(log):
    assert crashlog.install(stall_seconds=0.2)
    stuck_here(0.6)
    crashlog.stop()
    text = log.read_text()
    assert "stuck_here" in text
    assert text.startswith("--- started")


def test_a_beating_interface_writes_nothing(log):
    assert crashlog.install(stall_seconds=0.4)
    for _ in range(6):
        time.sleep(0.1)
        crashlog.beat(0.4)
    crashlog.stop()
    assert "stuck_here" not in log.read_text()
    assert "Thread" not in log.read_text()


def test_an_oversized_log_starts_over(log):
    log.parent.mkdir(parents=True)
    log.write_text("x" * (crashlog.MAX_BYTES + 1))
    assert crashlog.install()
    crashlog.stop()
    assert log.stat().st_size < 1000


def test_beat_without_install_is_harmless():
    crashlog.beat()
