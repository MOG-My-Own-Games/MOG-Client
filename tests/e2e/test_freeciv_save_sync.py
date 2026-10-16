"""End to end: a real game's real installer, one MOG Server, a Linux client and a Windows client.

1. The server gets a library with Freeciv's Windows installer and runs it in its sandbox, auto mode reading the wizard.
2. Both clients install the game at the same time, from the one install on the server.
3. Linux plays a new game (an AI game run by the game's own server program) and backs its saves up.
4. Windows takes them before starting the game, loads Linux's save, plays on and backs up.
5. The test checks what each machine sent, that Windows really carried on Linux's game, and that Linux gets Windows'
   saves back.

Needs Docker and a few GB, and takes several minutes; it runs only with MOG_E2E=1. See tests/e2e/README.md.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
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

    box = harness.Stack(docker, run_dir)
    box.failed = True  # until the setup is through; the test's own outcome then decides (see _record_failure)
    try:
        say(f"starting the server ({image}{f', code from {server_src}' if server_src else ''})")
        box.start_server(image, library, server_src)
        box.start_client("linux")
        box.start_client("windows")
        box.failed = False
        yield box
    finally:
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

    # --- Linux: a new game, three turns, then the saves go up
    play(linux, game_id, "--read Z:/e2e/freeciv/new-game.serv", "--saves save", "--exit-on-end")
    on_linux = linux.driver("saves", str(game_id))["files"]
    assert sorted(f["turn"] for f in on_linux.values()) == [1, 2, 3], on_linux
    say(f"linux: played, saves {sorted(on_linux)}")
    synced = linux.mog("--save-sync", str(game_id))
    assert "save sync: uploaded" in synced.stdout, synced.stdout

    linux_version = newest_version(api, game_id, "e2e-linux")
    linux_files = api.save_archive(linux_version["id"])
    linux_prefix = save_prefix(records["linux"])
    assert set(linux_files) == {linux_prefix + name for name in on_linux}, (
        "Linux's backup holds other files than the game's saves: " + ", ".join(sorted(set(linux_files) - {linux_prefix + n for n in on_linux}))
    )
    say(f"server: Linux's version {linux_version['id']} holds {len(linux_files)} files")

    # --- Windows: takes Linux's saves before the game starts, loads the last one and plays on
    windows.mog("--save-pre", str(game_id))
    before_play = windows.driver("saves", str(game_id))["files"]
    assert {n: f["sha256"] for n, f in before_play.items()} == {n: f["sha256"] for n, f in on_linux.items()}, (
        f"Windows did not get Linux's saves before starting the game: it has {sorted(before_play)}; "
        f"sync state {windows.driver('saves', str(game_id))['state']}"
    )
    last_linux = max(on_linux, key=lambda n: on_linux[n]["turn"])
    play(windows, game_id, f"--file save/{last_linux}", "--read Z:/e2e/freeciv/continue.serv", "--saves save", "--exit-on-end")
    on_windows = windows.driver("saves", str(game_id))["files"]
    own = {n: f for n, f in on_windows.items() if n.startswith("e2e-windows-")}
    assert sorted(f["turn"] for f in own.values()) == [4, 5], on_windows
    say(f"windows: played on from {last_linux}, saves {sorted(own)}")
    synced = windows.mog("--save-sync", str(game_id))
    assert "save sync: uploaded" in synced.stdout, synced.stdout

    windows_version = newest_version(api, game_id, "e2e-windows")
    windows_files = api.save_archive(windows_version["id"])
    windows_prefix = save_prefix(records["windows"])
    assert set(windows_files) == {windows_prefix + name for name in on_windows}, (
        "Windows' backup holds other files than the game's saves: "
        + ", ".join(sorted(set(windows_files) - {windows_prefix + n for n in on_windows}))
    )

    # --- Windows really carried on Linux's game, not a new one
    # Each Freeciv writes its own line endings (CRLF on Windows): compared as text.
    linux_save = linux_files[linux_prefix + last_linux].decode().replace("\r\n", "\n")
    windows_save = windows_files[windows_prefix + max(own, key=lambda n: own[n]["turn"])].decode().replace("\r\n", "\n")
    assert turn_of(linux_save) == 3 and turn_of(windows_save) == 5
    assert game_identity(windows_save) == game_identity(linux_save), "Windows' turn 5 is not the game Linux saved at turn 3"

    # --- and Linux gets Windows' saves back at its next start
    linux.mog("--save-pre", str(game_id))
    back_on_linux = linux.driver("saves", str(game_id))["files"]
    assert {n: f["sha256"] for n, f in back_on_linux.items()} == {n: f["sha256"] for n, f in on_windows.items()}, sorted(back_on_linux)
    say("linux: has Windows' saves back")
