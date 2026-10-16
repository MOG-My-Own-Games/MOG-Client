"""What the end-to-end test asks one client container to do, through MOG Client's own modules.

Run inside a client container with the source on the path (see client.Dockerfile): `python3 driver.py ...` on the Linux
client, `wine python.exe driver.py ...` on the Windows one. The steps a person takes in the window (sign in, install,
choose the executable, start the game) are the same calls the window makes; the save sync is left to the real
`mog --save-pre` / `--save-sync` commands, which the test runs itself. The last line printed is `RESULT <json>`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from mog_client import launcher, manager
from mog_client.config import Settings, load_library, load_settings, save_settings
from mog_client.saves import devices
from mog_client.saves.state import load_state


def result(data) -> int:
    print("RESULT " + json.dumps(data), flush=True)
    return 0


def setup(args) -> int:
    """Sign in as the first-start guide does, and register this machine for the save sync (the window does it at start)."""
    settings = Settings(base=args.base, user=args.user, password=args.password, first_run_done=True)
    save_settings(settings)
    launcher.ensure_scanned(settings, rescan=True)
    device = devices.register(manager.make_client(settings))
    return result({"device": asdict(device), "launchers": settings.launchers, "platform": sys.platform})


def install(args) -> int:
    """Install the game as the Install button does, then pick its executable as "Choose executable" does (no menu entry
    and no Steam shortcut: the container has neither)."""
    settings = load_settings()
    launcher.ensure_scanned(settings)
    client = manager.make_client(settings)
    game = client.get_game(args.game_id)
    started = time.monotonic()
    sessions: list[int] = []

    def on_session(session: dict) -> None:
        if session.get("id") is not None and session["id"] not in sessions:
            sessions.append(session["id"])
            print(f"session {session['id']}: {session.get('state')}", flush=True)

    rec = manager.run_install(client, game, settings, threading.Event(), print, on_session, lambda _done, _total: None)
    if rec.state != "awaiting_executable":
        print(f"install ended in state {rec.state}", file=sys.stderr)
        return 1
    root = Path(rec.files_dir or rec.install_dir)
    found = [p for p in launcher.list_executables(root) if p.name.lower() == args.exe.lower()]
    if not found:
        print(f"{args.exe} is not among the executables found: {launcher.list_executables(root)}", file=sys.stderr)
        return 1
    manager.finish_setup(rec, game, str(found[0]), [], desktop=False, launcher=settings.launcher, client=client)
    rec = load_library()[args.game_id]
    return result({"record": asdict(rec), "seconds": round(time.monotonic() - started), "sessions": sessions})


def play(args) -> int:
    """Start the game the way Play does, with these launch arguments (set as the game's Options would), and wait for it."""
    settings = load_settings()
    launcher.ensure_scanned(settings)
    rec = load_library()[args.game_id]
    manager.set_launch_args(rec, args.arg, settings.launcher)
    proc = launcher.launch(load_library()[args.game_id], settings.launcher)
    try:
        code = proc.wait(timeout=args.timeout)
    except Exception:
        proc.kill()
        raise
    log = Path(proc.log_path).read_text(errors="replace")  # type: ignore[attr-defined]
    print(log, flush=True)
    if failure := launcher.launch_failure(proc):
        print(failure, file=sys.stderr)
        return 1
    return result({"code": code})


def _turn(path: Path) -> int | None:
    match = re.search(r"^turn=(\d+)$", path.read_text(errors="replace"), re.MULTILINE)
    return int(match.group(1)) if match else None


def saves(args) -> int:
    """The game's save files as they are on this machine (in the folder the game was told to save into), and the save
    sync's state for it."""
    rec = load_library()[args.game_id]
    folder = Path(rec.executable).parent / args.folder
    files = {}
    for path in sorted(folder.glob("*.sav")) if folder.is_dir() else []:
        files[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "turn": _turn(path)}
    state = load_state(args.game_id)
    return result({"folder": str(folder), "files": files, "state": asdict(state), "record": asdict(rec)})


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--base", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.set_defaults(run=setup)
    p = sub.add_parser("install")
    p.add_argument("game_id", type=int)
    p.add_argument("--exe", required=True, help="File name of the executable to choose")
    p.set_defaults(run=install)
    p = sub.add_parser("play")
    p.add_argument("game_id", type=int)
    p.add_argument("--arg", action="append", default=[], help="A launch argument tag (repeat)")
    p.add_argument("--timeout", type=float, default=900)
    p.set_defaults(run=play)
    p = sub.add_parser("saves")
    p.add_argument("game_id", type=int)
    p.add_argument("--folder", default="save")
    p.set_defaults(run=saves)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
