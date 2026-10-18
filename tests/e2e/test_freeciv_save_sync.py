"""End to end: a real game's real installer, one MOG Server, a Linux client and a Windows client.

1. The server gets a library with Freeciv's Windows installer and runs it in its sandbox, auto mode reading the wizard.
2. Both clients install the game at the same time, from the one install on the server.
3. The game goes back and forth (an AI game run by the game's own server program, saved every turn): Linux plays turns
   1-3 of a new game, Windows takes those saves, loads turn 3 and plays 4-6, Linux takes them back and plays 7-9.
   Each machine backs up after playing and takes the other's saves before it starts, as a game's shortcut does.
4. The test checks each backup is exactly the game's saves, that every machine carried on the same game, and that Windows
   ends up with the whole game.

Needs Docker and a few GB, and takes several minutes; it runs only with MOG_E2E=1. See tests/e2e/README.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import signal
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

import harness

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("MOG_E2E") != "1", reason="end-to-end test: set MOG_E2E=1 to run it (needs Docker)"),
]

INSTALL_TIMEOUT = float(os.environ.get("MOG_E2E_INSTALL_TIMEOUT", "2400"))
PLAY_TIMEOUT = 900


def say(text: str) -> None:
    print(f"[e2e {time.strftime('%H:%M:%S')}] {text}", flush=True)


def server_image(docker: harness.Docker) -> tuple[str, Path | None]:
    """The server image, and the checkout to mount over its code (None: run the image as it is).

    MOG_E2E_SERVER_IMAGE names an image to use; with MOG_E2E_SERVER_MOUNT=1 the MOG-Server checkout's code is mounted
    over it (no rebuild; its dependencies must still match). Otherwise the image is built from the checkout next to
    this repo (MOG_E2E_SERVER_SRC to point elsewhere), so what is checked out is what gets tested; without a checkout,
    the published image."""
    src = Path(os.environ.get("MOG_E2E_SERVER_SRC") or harness.CLIENT_SRC.parent / "MOG-Server")
    have_src = (src / "docker" / "Dockerfile").is_file()
    if image := os.environ.get("MOG_E2E_SERVER_IMAGE"):
        mount = os.environ.get("MOG_E2E_SERVER_MOUNT") == "1"
        if mount and not have_src:
            pytest.fail(f"MOG_E2E_SERVER_MOUNT=1 but there is no MOG-Server checkout at {src}")
        return image, src if mount else None
    if not have_src:
        return "xargonwan/mog-server:latest", None
    say(f"building the server image from {src}")
    docker.build(harness.SERVER_IMAGE, src / "docker" / "Dockerfile", src, "--build-arg", "MOG_VERSION=e2e")
    return harness.SERVER_IMAGE, None


def _exit_on_signal(signum, _frame):
    raise SystemExit(128 + signum)


@pytest.fixture(scope="module")
def stack():
    command = harness.docker_command()
    if not command:
        pytest.fail("MOG_E2E=1 but no docker command was found (set MOG_E2E_DOCKER)")
    docker = harness.Docker(command)
    image, server_src = server_image(docker)
    if os.environ.get("MOG_E2E_REBUILD") == "1" or not docker.image_exists(harness.CLIENT_IMAGE):
        say("building the client image")
        docker.build(harness.CLIENT_IMAGE, harness.E2E_DIR / "client.Dockerfile", harness.E2E_DIR)

    cache = harness.cache_dir()
    installer = harness.fetch(harness.FREECIV_URL, harness.FREECIV_SHA256, cache / "assets" / harness.FREECIV_URL.rsplit("/", 1)[1])
    run_dir = cache / "runs" / (time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2))
    library = run_dir / "library"
    (library / harness.GAME_FOLDER).mkdir(parents=True)
    os.link(installer, library / harness.GAME_FOLDER / installer.name)

    if left := harness.sweep(docker):
        say(f"removed what an earlier run left behind: {', '.join(left)}")
    box = harness.Stack(docker, run_dir)
    box.failed = True  # until the setup is through; the test's own outcome then decides (see _record_failure)
    # A kill or a closed terminal ends the process without running the finally below, and the containers would go on
    # (and keep the machine awake): turn those signals into an exit that does run it.
    previous = {sig: signal.signal(sig, _exit_on_signal) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        say(f"starting the server ({image}{f', code from {server_src}' if server_src else ''})")
        box.start_server(image, library, server_src)
        box.start_client("linux")
        box.start_client("windows")
        box.failed = False
        yield box
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if (box.failed and not (run_dir / "diagnostics").is_dir()) or os.environ.get("MOG_E2E_DIAGNOSTICS") == "1":
            say(f"diagnostics in {box.keep_diagnostics()}")
        if os.environ.get("MOG_E2E_KEEP") == "1":
            say(f"containers kept: {box.tag}-* (network {box.network}), server API {box.api.base if box.api else '?'}")
        else:
            box.down()
            if not box.failed and os.environ.get("MOG_E2E_DIAGNOSTICS") != "1":
                shutil.rmtree(run_dir, ignore_errors=True)  # two installed games and a server's cache: gigabytes


@pytest.fixture(autouse=True)
def _record_failure(request, stack):
    """The module fixture keeps diagnostics only when its own setup fails; a failing step of the test keeps them too."""
    yield
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        stack.failed = True
        say(f"diagnostics in {stack.keep_diagnostics()}")


def add_library(api: harness.Api) -> int:
    status, library = api.request("POST", "/api/libraries", {"name": "e2e", "root_path": "/library"})
    assert status == 201, library
    status, scan = api.request("POST", f"/api/libraries/{library['id']}/scan")
    assert status == 200 and scan["added"] == 1, scan
    games = [g for g in api.get("/api/games") if g["fs_name"] == harness.GAME_FOLDER]
    assert len(games) == 1, games
    return games[0]["id"]


def install_both(stack: harness.Stack, game_id: int) -> dict[str, dict]:
    """Both clients press Install at the same moment; the server's progress is printed while they download."""
    results: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}
    start = threading.Barrier(2)

    def work(role: str) -> None:
        start.wait()
        try:
            results[role] = stack.clients[role].driver("install", str(game_id), "--exe", harness.GAME_EXE, timeout=INSTALL_TIMEOUT)
        except BaseException as e:  # noqa: BLE001 - reported below, with the other client's outcome
            errors[role] = e

    threads = [threading.Thread(target=work, args=(role,), daemon=True) for role in ("linux", "windows")]
    for t in threads:
        t.start()
    last = None
    while any(t.is_alive() for t in threads):
        time.sleep(10)
        status, active = stack.api.request("GET", "/api/games/install/active")
        line = (
            "; ".join(f"session {s['id']} {s['state']} {s.get('phase') or ''} auto={s.get('auto_status')} {s.get('auto_detail') or ''}".strip() for s in active)
            if status == 200 and isinstance(active, list)
            else f"HTTP {status}"
        )
        if line != last:
            say(f"server: {line or 'no install running'}")
            last = line
    if errors:
        raise AssertionError("install failed: " + "; ".join(f"{role}: {e}" for role, e in errors.items()))
    return results


def play(box: harness.ClientBox, game_id: int, *tags: str) -> None:
    """Start the game with these launch arguments (Freeciv's server: a script to read, where to save) and wait for it."""
    box.driver("play", str(game_id), *(f"--arg={tag}" for tag in tags), timeout=PLAY_TIMEOUT)


def save_prefix(record: dict) -> str:
    """The archive key of the folder the game saves into: `game/<executable's folder, from the install folder>/save/`."""
    path = PureWindowsPath if "\\" in record["install_dir"] else PurePosixPath
    rel = path(record["executable"]).parent.relative_to(path(record["install_dir"])).as_posix()
    return "game/save/" if rel == "." else f"game/{rel}/save/"


def newest_version(api: harness.Api, game_id: int, device_name: str) -> dict:
    saves = api.get(f"/api/games/{game_id}/saves")
    for entry in saves["devices"]:
        if entry["device"]["name"] == device_name:
            assert entry["versions"], f"{device_name} has no saved version"
            return max(entry["versions"], key=lambda v: (v["created_at"], v["id"]))
    raise AssertionError(f"no saves from {device_name}: {[e['device']['name'] for e in saves['devices']]}")


def game_identity(save: str) -> dict:
    """What tells one game from another in a Freeciv save, whatever turn it is at: the map's terrain and the leaders
    playing it. (Not `[map] random_seed`: a loaded game can save another one.)"""
    return {
        "terrain": re.findall(r"^t\d{4}=.*$", save, re.MULTILINE),
        "leaders": re.findall(r'^name="([^"]+)"$', save, re.MULTILINE)[:4],
    }


def turn_of(save: str) -> int:
    return int(re.search(r"^turn=(\d+)$", save, re.MULTILINE).group(1))


def as_text(data: bytes) -> str:
    return data.decode().replace("\r\n", "\n")  # each Freeciv writes its own line endings (CRLF on Windows)


def hashes(files: dict) -> dict[str, str]:
    return {name: f["sha256"] for name, f in files.items()}


def take_saves(box: harness.ClientBox, game_id: int, expected: dict) -> None:
    """`mog --save-pre`, as the game's shortcut runs it before the game: afterwards this machine has exactly the saves the
    other one left."""
    box.mog("--save-pre", str(game_id))
    here = box.driver("saves", str(game_id))
    assert hashes(here["files"]) == hashes(expected), (
        f"{box.role} did not get the other machine's saves before starting the game: it has {sorted(here['files'])}, "
        f"expected {sorted(expected)}; sync state {here['state']}"
    )
    say(f"{box.role}: took the other machine's {len(expected)} saves")


def leg(
    stack: harness.Stack, box: harness.ClientBox, record: dict, game_id: int, script: str, turns: list[int], load: str | None
) -> tuple[dict, str]:
    """One machine's turn at the game: play (a new game, or on from the save `load`), back up, and check the backup is
    exactly the saves on this machine. Returns this machine's saves and the text of its newest one."""
    before = box.driver("saves", str(game_id))["files"]
    tags = [f"--file save/{load}"] if load else []
    play(box, game_id, *tags, f"--read Z:/e2e/freeciv/{script}", "--saves save", "--exit-on-end")
    files = box.driver("saves", str(game_id))["files"]
    new = {name: f for name, f in files.items() if name not in before}
    assert sorted(f["turn"] for f in new.values()) == turns, f"{box.role} should have saved turns {turns}: {sorted(files)}"
    assert all(name.startswith(f"e2e-{box.role}-") for name in new), sorted(new)
    say(f"{box.role}: played turns {turns[0]}-{turns[-1]}" + (f" on from {load}" if load else "") + f", saves {sorted(new)}")

    synced = box.mog("--save-sync", str(game_id))
    assert "save sync: uploaded" in synced.stdout, synced.stdout
    version = newest_version(stack.api, game_id, f"e2e-{box.role}")
    archive = stack.api.save_archive(version["id"])
    prefix = save_prefix(record)
    wanted = {prefix + name for name in files}
    assert set(archive) == wanted, (
        f"{box.role}'s backup is not exactly the game's saves: extra {sorted(set(archive) - wanted)}, "
        f"missing {sorted(wanted - set(archive))}"
    )
    assert {key[len(prefix) :]: hashlib.sha256(data).hexdigest() for key, data in archive.items()} == hashes(files)
    say(f"server: {box.role}'s version {version['id']} holds {len(archive)} files")
    newest = max(new, key=lambda name: new[name]["turn"])
    return files, as_text(archive[prefix + newest])


def test_install_play_and_sync_saves_between_linux_and_windows(stack: harness.Stack):
    api, linux, windows = stack.api, stack.clients["linux"], stack.clients["windows"]

    game_id = add_library(api)
    say(f"library scanned: {harness.GAME_FOLDER} is game {game_id}")
    for box in (linux, windows):
        setup = box.driver("setup", "--base", stack.internal_base, "--user", harness.ADMIN, "--password", stack.password)
        say(f"{box.role}: registered as {setup['device']['name']} (platform {setup['platform']}, launchers {setup['launchers']})")

    # --- both install at once, from one install on the server
    installed = install_both(stack, game_id)
    records = {role: r["record"] for role, r in installed.items()}
    for role, r in installed.items():
        say(f"{role}: installed in {r['seconds']}s, executable {r['record']['executable']}")
        assert r["record"]["state"] == "installed"
    assert records["linux"]["session_id"] == records["windows"]["session_id"], (
        f"the two clients installed from different sessions: {records['linux']['session_id']} and "
        f"{records['windows']['session_id']} (the server ran the installer twice)"
    )
    assert records["windows"]["prefix"] is None and records["linux"]["prefix"], records

    # --- the game goes back and forth: Linux turns 1-3, Windows 4-6, Linux 7-9, each taking the other's saves first
    on_linux, linux_t3 = leg(stack, linux, records["linux"], game_id, "new-game.serv", [1, 2, 3], load=None)

    take_saves(windows, game_id, on_linux)
    last = max(on_linux, key=lambda name: on_linux[name]["turn"])
    on_windows, windows_t6 = leg(stack, windows, records["windows"], game_id, "continue-on-windows.serv", [4, 5, 6], load=last)

    take_saves(linux, game_id, on_windows)
    last = max(on_windows, key=lambda name: on_windows[name]["turn"])
    on_linux, linux_t9 = leg(stack, linux, records["linux"], game_id, "continue-on-linux.serv", [7, 8, 9], load=last)

    # --- one game all along, never a new one: every machine carried on the one before it
    assert [turn_of(linux_t3), turn_of(windows_t6), turn_of(linux_t9)] == [3, 6, 9]
    assert game_identity(windows_t6) == game_identity(linux_t3), "Windows' turn 6 is not the game Linux saved at turn 3"
    assert game_identity(linux_t9) == game_identity(linux_t3), "Linux's turn 9 is not the game Windows saved at turn 6"

    # --- and Windows, at its next start, has the whole game back
    take_saves(windows, game_id, on_linux)
