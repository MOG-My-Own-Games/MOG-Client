from pathlib import Path

from mog_client.saves import watcher


def _proc(root: Path, pid: int, cmdline: str, environ: str = "") -> None:
    """A process; `environ` is `NAME=value` entries separated by a bar (a value may hold spaces)."""
    folder = root / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())
    (folder / "environ").write_bytes(environ.replace("|", "\0").encode())


def _gone(root: Path, pid: int) -> None:
    import shutil

    shutil.rmtree(root / str(pid))


INSTALL = Path("/home/x/mog-installed/Some Game")
WIN_CMD = "Z:\\home\\x\\mog-installed\\Some\0Game\\game.exe"


def test_a_wine_process_running_an_exe_from_the_install_folder_is_the_game(tmp_path):
    _proc(tmp_path, 100, "Z:\\home\\x\\mog-installed\\Some Game\\game.exe")
    # NUL-separated arguments become spaces when read, so a path with a space still matches
    (tmp_path / "101").mkdir()
    (tmp_path / "101/cmdline").write_bytes(b"/usr/bin/umu-run\0/home/x/mog-installed/Some Game/game.exe\0")
    _proc(tmp_path, 102, "vim /home/x/mog-installed/Some Game/notes.txt")  # not an exe
    _proc(tmp_path, 103, "wine /home/x/other/game.exe")  # another folder
    _proc(tmp_path, 104, "/usr/bin/bash")
    _proc(tmp_path, 105, "wine /home/x/mog-installed/Some Game 2/game.exe")  # a folder that only starts alike
    (tmp_path / "self").mkdir()

    assert sorted(watcher.running_pids(INSTALL, tmp_path)) == [100, 101]
    assert sorted(watcher.running_pids(INSTALL, tmp_path, exclude=(101,))) == [100]


def test_the_prefix_comes_from_the_environment_of_the_process(tmp_path):
    _proc(tmp_path, 1, "x", "HOME=/home/x|WINEPREFIX=/home/x/Faugus/g/pfx|PATH=/bin")
    assert watcher.prefix_from_environ(1, tmp_path) == Path("/home/x/Faugus/g/pfx")

    compat = tmp_path / "compat"
    (compat / "pfx").mkdir(parents=True)
    _proc(tmp_path, 2, "x", f"STEAM_COMPAT_DATA_PATH={compat}")
    assert watcher.prefix_from_environ(2, tmp_path) == compat / "pfx"

    _proc(tmp_path, 3, "x", "HOME=/home/x")
    assert watcher.prefix_from_environ(3, tmp_path) is None
    assert watcher.prefix_from_environ(99, tmp_path) is None


def test_a_prefix_path_with_spaces_is_read_whole(tmp_path):
    _proc(tmp_path, 4, "x", "HOME=/home/x|WINEPREFIX=/home/x/mog-installed/Jazz Jackrabbit 2_ The Secret Files/pfx|PATH=/bin")
    assert watcher.prefix_from_environ(4, tmp_path) == Path("/home/x/mog-installed/Jazz Jackrabbit 2_ The Secret Files/pfx")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.script = {}  # time -> action

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for at in sorted(k for k in self.script if k <= self.now):
            self.script.pop(at)()


def test_it_waits_for_the_game_to_start_and_then_to_end(tmp_path):
    clock = Clock()
    clock.script[10] = lambda: _proc(tmp_path, 100, WIN_CMD.replace("\0", " "), "WINEPREFIX=/pfx/g")
    clock.script[60] = lambda: _gone(tmp_path, 100)
    seen = []

    finished = watcher.wait_for_game(
        INSTALL, seen.append, proc_root=tmp_path, appear_timeout=100, linger=6, poll=2,
        sleep=clock.sleep, clock=clock.time,
    )  # fmt: skip

    assert finished is True and seen == [Path("/pfx/g")]
    assert 66 <= clock.now <= 70  # ended at 60, plus the linger


def test_a_launcher_handing_over_to_the_game_is_not_the_end(tmp_path):
    clock = Clock()
    clock.script[2] = lambda: _proc(tmp_path, 100, "faugus-run Z:\\home\\x\\mog-installed\\Some Game\\game.exe")
    clock.script[10] = lambda: (_gone(tmp_path, 100), None)[1]
    clock.script[12] = lambda: _proc(tmp_path, 200, "Z:\\home\\x\\mog-installed\\Some Game\\game.exe")
    clock.script[40] = lambda: _gone(tmp_path, 200)

    finished = watcher.wait_for_game(
        INSTALL, proc_root=tmp_path, appear_timeout=100, linger=6, poll=2, sleep=clock.sleep, clock=clock.time
    )

    assert finished is True and clock.now >= 46


def test_a_game_that_never_shows_up_is_given_up_on(tmp_path):
    clock = Clock()
    assert (
        watcher.wait_for_game(INSTALL, proc_root=tmp_path, appear_timeout=30, poll=5, sleep=clock.sleep, clock=clock.time)
        is False
    )
    assert 30 <= clock.now <= 35


def test_it_can_be_stopped(tmp_path):
    clock = Clock()
    ticks = iter([False, False, True])
    assert watcher.wait_for_game(
        INSTALL, proc_root=tmp_path, sleep=clock.sleep, clock=clock.time, stop=lambda: next(ticks)
    ) is False


def test_a_native_game_is_a_process_started_from_the_install_folder(tmp_path):
    proc = tmp_path / "proc"
    _proc(proc, 200, "/bin/sh /home/x/mog-installed/Some Game/start.sh")  # named by its command line
    _proc(proc, 201, "./game/Some.x86_64")  # started as `./game`: only its program file shows where it is
    (proc / "201/exe").symlink_to("/home/x/mog-installed/Some Game/game/Some.x86_64")
    _proc(proc, 202, "vim /home/x/mog-installed/Some Game/notes.txt")  # an editor on a file of the game
    _proc(proc, 203, "/usr/bin/bash")
    (proc / "203/exe").symlink_to("/usr/bin/bash")
    _proc(proc, 204, "./other")
    (proc / "204/exe").symlink_to("/home/x/mog-installed/Some Game 2/other")  # a folder that only starts alike

    assert sorted(watcher.running_pids(INSTALL, proc, native=True)) == [200, 201, 202]
    assert watcher.running_pids(INSTALL, proc) == []  # no .exe, so not a Windows game
    assert sorted(watcher.running_pids(INSTALL, proc, exclude=(200,), native=True)) == [201, 202]


def test_waiting_for_a_native_game_sees_it_start_and_end(tmp_path):
    proc = tmp_path / "proc"
    _proc(proc, 300, "./game")
    (proc / "300/exe").symlink_to("/home/x/mog-installed/Some Game/game")
    clock = [0.0]
    steps = []

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        steps.append(clock[0])
        if len(steps) == 3:
            _gone(proc, 300)

    done = watcher.wait_for_game(
        INSTALL, proc_root=proc, appear_timeout=30, linger=4, poll=2, sleep=sleep, clock=lambda: clock[0], native=True
    )

    assert done is True and clock[0] >= 8


def test_the_wait_says_what_it_sees(tmp_path):
    clock = Clock()
    clock.script[2] = lambda: _proc(tmp_path, 100, "umu-run Z:\\home\\x\\mog-installed\\Some Game\\game.exe")
    clock.script[20] = lambda: _gone(tmp_path, 100)
    said = []

    watcher.wait_for_game(
        INSTALL, proc_root=tmp_path, appear_timeout=100, linger=6, poll=2, sleep=clock.sleep, clock=clock.time, log=said.append
    )

    assert "game.exe" in said[0] and said[0].startswith("game seen")
    assert said[1:] == ["game processes are gone", said[-1]] and said[-1].startswith("game ended")


def test_a_game_that_keeps_running_is_said_to_be_every_few_minutes(tmp_path):
    clock = Clock()
    clock.script[2] = lambda: _proc(tmp_path, 100, "umu-run Z:\\home\\x\\mog-installed\\Some Game\\game.exe")
    clock.script[700] = lambda: _gone(tmp_path, 100)
    said = []

    watcher.wait_for_game(
        INSTALL, proc_root=tmp_path, appear_timeout=100, linger=6, poll=2, sleep=clock.sleep, clock=clock.time, log=said.append
    )

    assert sum(line.startswith("game still running") for line in said) == 2


def test_giving_up_is_said_with_the_reason(tmp_path):
    clock = Clock()
    said = []
    watcher.wait_for_game(INSTALL, proc_root=tmp_path, appear_timeout=30, poll=5, sleep=clock.sleep, clock=clock.time, log=said.append)
    assert said and "giving up" in said[-1] and "timeout" in said[-1]
