import subprocess
import sys
import time
from pathlib import Path

import pytest

from mog_client import gameplay
from mog_client.saves import watcher

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")


def start_game(folder: Path, trap_term: bool = False) -> subprocess.Popen:
    """A process whose command line looks like a game running from `folder`; returns once it is set up."""
    ready = folder / "ready"
    ready.unlink(missing_ok=True)
    code = (
        "import signal,sys,time\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if trap_term else "")
        + f"open({str(ready)!r}, 'w').close()\ntime.sleep(60)"
    )
    game = subprocess.Popen([sys.executable, "-c", code, str(folder / "Game" / "game.exe")], start_new_session=True)
    assert wait_until(ready.exists)
    return game


def wait_until(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_stopping_a_game_ends_its_processes(tmp_path):
    game = start_game(tmp_path)
    try:
        assert wait_until(lambda: watcher.running_pids(tmp_path))
        signalled = gameplay.stop_game(tmp_path, game, grace=3)
        assert game.pid in signalled
        assert wait_until(lambda: game.poll() is not None) and watcher.running_pids(tmp_path) == []
    finally:
        game.kill()


def test_a_game_that_ignores_the_polite_request_is_killed_after_the_grace_period(tmp_path):
    game = start_game(tmp_path, trap_term=True)
    try:
        assert wait_until(lambda: watcher.running_pids(tmp_path))
        started = time.time()
        gameplay.stop_game(tmp_path, game, grace=0.6)
        assert wait_until(lambda: game.poll() is not None)
        assert time.time() - started >= 0.5 and game.returncode < 0  # killed by a signal
    finally:
        game.kill()


def test_stopping_what_is_not_running_does_nothing(tmp_path):
    assert gameplay.stop_game(tmp_path, None, grace=0.2) == []


def test_a_launcher_that_failed_before_the_game_showed_up_ends_the_wait_at_once(tmp_path):
    launcher = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    launcher.wait()
    started = time.time()
    assert gameplay.watch(tmp_path, launcher, lambda: False, appear_timeout=30, poll=0.05) is False
    assert time.time() - started < 5


def test_the_wait_ends_when_the_user_says_stop(tmp_path):
    flag = []
    started = time.time()
    result = gameplay.watch(tmp_path, None, lambda: bool(flag) or flag.append(1) is None and False, appear_timeout=30, poll=0.05)
    assert result is False and time.time() - started < 5


def test_a_game_that_runs_and_ends_is_followed_to_its_end(tmp_path):
    game = start_game(tmp_path)
    try:
        assert wait_until(lambda: watcher.running_pids(tmp_path))
        killer = subprocess.Popen([sys.executable, "-c", f"import os,signal,time; time.sleep(0.5); os.kill({game.pid}, signal.SIGKILL)"])
        assert gameplay.watch(tmp_path, game, lambda: False, appear_timeout=10, linger=0.3, poll=0.1) is True
        killer.wait()
    finally:
        game.kill()
