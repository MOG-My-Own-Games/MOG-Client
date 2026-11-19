"""Games that run in the browser (webgame.py): how they are told, how their browser is opened, what of its profile is saved."""

import socket
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mog_client import launcher, webgame
from mog_client.config import InstalledGame
from mog_client.saves import native
from mog_client.saves.locations import walk

# What Magic Dice: Waifu Roll ships (launcher.json, the .bat and the part of launch.py that matters here).
LAUNCH_PY = '''
origin = json.loads((ROOT / "launcher.json").read_text(encoding="utf-8"))["origin"]
if not re.fullmatch(r"http://127\\.0\\.0\\.1:[0-9]+", origin):
    raise ValueError("Invalid local game address in launcher.json.")
        if "--no-browser" not in sys.argv:
            os.startfile(origin + "/")
'''


def magic_dice(tmp_path: Path) -> Path:
    game = tmp_path / "Magic Dice"
    (game / "game").mkdir(parents=True)
    (game / "runtime").mkdir()
    (game / "launcher.json").write_text('{"origin":"http://127.0.0.1:41765"}')
    (game / "launch.py").write_text(LAUNCH_PY)
    (game / "Play Magic Dice.bat").write_text('@echo off\n"%~dp0runtime\\python.exe" -I -B -u "%~dp0launch.py" %*\n')
    (game / "game" / "index.html").write_text("<html></html>")
    (game / "runtime" / "python.exe").write_bytes(b"MZ")
    return game


def record(game: Path, **fields) -> InstalledGame:
    return InstalledGame(game_id=260, name="Magic Dice", install_dir=str(game), executable=str(game / "Play Magic Dice.bat"), **fields)


# --- telling a browser game --------------------------------------------------------------------------------------


def test_magic_dice_is_found_by_its_launcher_json_and_its_no_browser_option(tmp_path):
    found = webgame.detect(magic_dice(tmp_path))
    assert found == webgame.WebGame("http://127.0.0.1:41765", "--no-browser")


def test_a_game_with_no_option_to_keep_the_browser_closed_is_not_taken_for_one(tmp_path):
    """MOG could not stop it opening the person's browser, so there would be nothing to save."""
    game = magic_dice(tmp_path)
    (game / "launch.py").write_text("os.startfile('http://127.0.0.1:41765/')")
    assert webgame.detect(game) is None


def test_an_address_with_no_page_to_serve_is_not_a_browser_game(tmp_path):
    game = magic_dice(tmp_path)
    (game / "game" / "index.html").unlink()
    assert webgame.detect(game) is None


@pytest.mark.parametrize("origin", ["http://127.0.0.1:80", "http://127.0.0.1:0", "https://127.0.0.1:41765", "http://example.com:41765"])
def test_only_a_local_http_address_on_a_fixed_port_counts(tmp_path, origin):
    game = magic_dice(tmp_path)
    (game / "launcher.json").write_text('{"origin":"%s"}' % origin)
    (game / "launch.py").write_text("if '--no-browser' in sys.argv: pass")
    assert webgame.detect(game) is None


def test_an_ordinary_game_is_not_one(tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    (game / "Game.exe").write_bytes(b"MZ")
    (game / "run.bat").write_text("Game.exe")
    assert webgame.detect(game) is None


def test_the_profile_of_the_game_is_not_read_as_its_launcher(tmp_path):
    game = tmp_path / "Game"
    (game / webgame.PROFILE_DIR).mkdir(parents=True)
    (game / webgame.PROFILE_DIR / "a.json").write_text("http://127.0.0.1:41765 --no-browser")
    (game / "index.html").write_text("x")
    assert webgame.detect(game) is None


# --- the browser -------------------------------------------------------------------------------------------------


@pytest.fixture
def system(monkeypatch):
    """No browsers, no flatpaks, no default browser, until a test adds them."""
    installed: dict[str, str] = {}
    monkeypatch.setattr(webgame.shutil, "which", lambda name: installed.get(name))
    monkeypatch.setattr(webgame, "_default_browser_id", lambda: "")
    monkeypatch.setattr(webgame, "_flatpak_installed", lambda app_id: False)
    return installed


def test_the_system_browser_comes_first_when_it_is_chromium_based(system, monkeypatch, tmp_path):
    system.update({"brave-browser": "/usr/bin/brave-browser", "chromium": "/usr/bin/chromium"})
    monkeypatch.setattr(webgame, "_default_browser_id", lambda: "brave-browser")
    assert webgame.find_browser(tmp_path) == ["/usr/bin/brave-browser"]


def test_a_firefox_default_is_not_used_and_the_next_chromium_is(system, monkeypatch, tmp_path):
    system.update({"firefox": "/usr/bin/firefox", "chromium": "/usr/bin/chromium"})
    monkeypatch.setattr(webgame, "_default_browser_id", lambda: "firefox")
    assert webgame.find_browser(tmp_path) == ["/usr/bin/chromium"]


def test_a_flatpak_browser_is_given_the_profile_folder(system, monkeypatch, tmp_path):
    system["flatpak"] = "/usr/bin/flatpak"
    monkeypatch.setattr(webgame, "_flatpak_installed", lambda app_id: app_id == "com.google.Chrome")
    assert webgame.find_browser(tmp_path / "p") == ["/usr/bin/flatpak", "run", f"--filesystem={tmp_path / 'p'}", "com.google.Chrome"]


def test_no_browser_means_the_game_is_left_to_open_its_own(system, tmp_path):
    rec = record(magic_dice(tmp_path), web_origin="http://127.0.0.1:41765", web_no_open_arg="--no-browser")
    assert webgame.browser_command(rec) is None and webgame.takes_over(rec) is False


def test_the_browser_is_opened_on_the_game_with_a_profile_in_its_folder(system, tmp_path):
    system["chromium"] = "/usr/bin/chromium"
    game = magic_dice(tmp_path)
    rec = record(game, web_origin="http://127.0.0.1:41765", web_no_open_arg="--no-browser")
    command = webgame.browser_command(rec)
    assert command[0] == "/usr/bin/chromium"
    assert f"--user-data-dir={game / 'mog-browser'}" in command and "--app=http://127.0.0.1:41765/" in command


def test_the_game_is_started_without_its_own_browser_only_when_mog_has_one_to_open(system, monkeypatch, tmp_path):
    """Magic Dice: `Play Magic Dice.bat` passes its arguments on to launch.py, which then opens nothing."""
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    game = record(magic_dice(tmp_path), web_origin="http://127.0.0.1:41765", web_no_open_arg="--no-browser")

    assert launcher.launch_command(game)[0][-1] != "--no-browser"  # no browser to open: the game opens its own

    system["chromium"] = "/usr/bin/chromium"
    assert launcher.launch_command(game)[0][-2:] == [game.executable, "--no-browser"]


def test_an_ordinary_game_is_started_as_before(system, monkeypatch, tmp_path):
    system["chromium"] = "/usr/bin/chromium"
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    assert launcher.launch_command(record(magic_dice(tmp_path)))[0][-1] == str(tmp_path / "Magic Dice" / "Play Magic Dice.bat")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_browser_opens_when_the_server_answers_and_is_closed_with_the_game(system, monkeypatch, tmp_path):
    port = free_port()
    if port < 10000:
        pytest.skip("needs a five digit port")
    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen()
    system["chromium"] = "sleep"
    rec = record(magic_dice(tmp_path), web_origin=f"http://127.0.0.1:{port}", web_no_open_arg="--no-browser")
    opened = []
    monkeypatch.setattr(webgame, "browser_command", lambda r: ["sleep", "30"])
    real_popen = subprocess.Popen
    monkeypatch.setattr(webgame.subprocess, "Popen", lambda *a, **k: opened.append(real_popen(*a, **k)) or opened[-1])
    said = []
    companion = webgame.Companion(rec, said.append)
    try:
        companion.start()
        deadline = time.monotonic() + 10
        while not opened and time.monotonic() < deadline:
            time.sleep(0.05)
        assert opened and opened[0].poll() is None
        companion.close()
        assert opened[0].poll() is not None  # waited for: its profile is on disk when the backup starts
        assert (tmp_path / "Magic Dice" / "mog-browser").is_dir()
    finally:
        server.close()
        for proc in opened:
            if proc.poll() is None:
                proc.kill()


def test_a_server_that_never_answers_leaves_the_browser_unopened(system, monkeypatch, tmp_path):
    system["chromium"] = "/usr/bin/chromium"
    monkeypatch.setattr(webgame, "PORT_TIMEOUT", 0.3)
    rec = record(magic_dice(tmp_path), web_origin="http://127.0.0.1:59999", web_no_open_arg="--no-browser")
    said = []
    companion = webgame.Companion(rec, said.append)
    companion._open()
    assert companion._proc is None and "did not answer" in said[0]


def test_another_game_has_no_companion(tmp_path):
    assert webgame.Companion(record(magic_dice(tmp_path))).active is False


# --- what of the profile is saved --------------------------------------------------------------------------------


def leveldb(folder: Path, **extra: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in {"000003.log": b"newest", "000005.ldb": b"table", "CURRENT": b"MANIFEST-000004\n", "LOG": b"x", **extra}.items():
        (folder / name).write_bytes(data)


def test_the_log_files_of_the_storage_are_taken_whole(tmp_path):
    """000003.log is where leveldb keeps what was written last: with the usual rules for logs, the newest progress was left out."""
    game = tmp_path / "Game"
    leveldb(game / "mog-browser/Default/Local Storage/leveldb")
    keys = sorted(c.key for c in native.included_files(list(webgame.STORAGE_KEYS), game))
    assert "game/mog-browser/Default/Local Storage/leveldb/000003.log" in keys and len(keys) == 4


def test_the_usual_rules_still_leave_logs_out_of_other_folders(tmp_path):
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "a.sav").write_bytes(b"x")
    (folder / "output.log").write_bytes(b"x")
    assert [c.key for c in walk(folder, "game/saves")] == ["game/saves/a.sav"]
    assert len([c.key for c in walk(folder, "game/saves", deny=False)]) == 2


def test_nothing_of_the_profile_but_its_storage_is_a_save(tmp_path):
    game = tmp_path / "Game"
    leveldb(game / "mog-browser/Default/Local Storage/leveldb")
    (game / "mog-browser/Default/Preferences").write_bytes(b"x")
    (game / "mog-browser/Default/Cookies").write_bytes(b"x")
    keys = {c.key for c in native.included_files(list(webgame.STORAGE_KEYS), game)}
    assert all(k.startswith("game/mog-browser/Default/Local Storage/") for k in keys)


def test_a_restore_takes_out_the_files_the_backup_did_not_hold(tmp_path):
    game = tmp_path / "Game"
    folder = game / "mog-browser/Default/Local Storage/leveldb"
    leveldb(folder, **{"000009.log": b"left from before"})
    restored = {f"game/mog-browser/Default/Local Storage/leveldb/{n}" for n in ("000003.log", "000005.ldb", "CURRENT", "LOG")}
    backup = tmp_path / "before-restore.zip"

    removed = webgame.drop_stale(game, restored, backup)

    assert removed == ["game/mog-browser/Default/Local Storage/leveldb/000009.log"]
    assert not (folder / "000009.log").exists() and (folder / "000003.log").exists()
    with zipfile.ZipFile(backup) as z:
        assert z.read("game/mog-browser/Default/Local Storage/leveldb/000009.log") == b"left from before"


def test_a_storage_folder_the_backup_never_wrote_to_is_left_alone(tmp_path):
    game = tmp_path / "Game"
    leveldb(game / "mog-browser/Default/Local Storage/leveldb")
    indexed = game / "mog-browser/Default/IndexedDB/x"
    indexed.mkdir(parents=True)
    (indexed / "a.ldb").write_bytes(b"x")
    restored = {"game/mog-browser/Default/Local Storage/leveldb/CURRENT"}

    webgame.drop_stale(game, restored, tmp_path / "b.zip")

    assert (indexed / "a.ldb").exists()


def test_a_browser_game_has_no_prefix_files_among_its_candidates(tmp_path, monkeypatch):
    from mog_client.saves import sync

    game = tmp_path / "Game"
    leveldb(game / "mog-browser/Default/Local Storage/leveldb")
    drive_c = tmp_path / "pfx" / "drive_c"
    (drive_c / "users/steamuser/AppData/Roaming/Other").mkdir(parents=True)
    (drive_c / "users/steamuser/AppData/Roaming/Other/save.dat").write_bytes(b"x")
    rec = record(game, web_origin="http://127.0.0.1:41765", explicit_saves=True)
    ctx = SimpleNamespace(rec=rec)

    keys = {c.key for c in sync._candidates(ctx, drive_c, list(webgame.STORAGE_KEYS))}

    assert keys and all(k.startswith("game/mog-browser/") for k in keys)
