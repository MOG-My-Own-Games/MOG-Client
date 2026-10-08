#!/usr/bin/env python3
"""mog: minimal CLI client to drive a MOG-Server's stream-install feature.

Pure stdlib (urllib, json, argparse, sys). No third-party deps; the GUI
(`mog` with no action, or `--gui`) needs the `gui` extra.

Usage:
  mog --base http://localhost:5000 --user admin --pass <password> \\
      --game-id 1 --out /tmp/installed

Just run it - the server does the rest, the same way starting an install
through any future client would (single source of truth: POST /{gameId}/install
resolves everything server-side):
  - Already installed (a cache from a prior run is still on disk)? Streamed
    immediately, nothing is (re)installed.
  - Not installed? The server starts it: top-ranked candidate, and for an
    archive or disc image it unpacks it and picks the executable inside
    (progress shows as "extracting <file>" / "mounting <file>").
  - No candidate at all - "manual mode": nobody (human, or auto mode) can
    say which file to run. The session sits in AWAITING_INSTALLER; this CLI
    prints the VNC URL and stops. Finish the install there, then re-run this
    same command to stream the result.

Flow:
  1. GET /api/games/{id}/install - already DONE? skip straight to streaming
  2. GET /api/games/{id}/install/candidates (informational only - the server
     does its own auto-pick, this is just to print the options)
  3. POST /api/games/{id}/install {installer_path, proton_build, ttl_seconds}
     - installer_path is optional; omit it and let the server decide.
     AWAITING_INSTALLER in the response means manual mode - stop and print
     the VNC URL (this outcome never touches the sandbox at all).
  4. poll GET /api/games/{id}/install/stream/manifest every 3s, concurrently
     with step 5's own polling - works while an install is still running too
     (that's the whole "stream install" point), not just once it's DONE.
  5. poll GET /api/games/{id}/install every 3s until state != active
  6. stream each file via Range GET /api/games/{id}/install/stream/{path}
  7. cancel: POST /api/games/{id}/install/cancel
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from mog_client.api import Client, MogClient, log, safe_dirname, warn
from mog_client.config import load_library, load_settings
from mog_client.transfer import download_all_files, poll_session, verify_and_repair

def launch_installed(game_id: int) -> int:
    from mog_client.launcher import ensure_scanned, launch, launch_failure

    rec = load_library().get(game_id)
    if rec is None or rec.state != "installed":
        warn(f"game {game_id} is not installed")
        return 1
    settings = load_settings()
    ensure_scanned(settings)
    try:
        proc = launch(rec, settings.launcher)
    except (RuntimeError, OSError) as e:
        warn(str(e))
        return 1
    code = proc.wait()
    if failure := launch_failure(proc):
        warn(failure)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive a MOG-Server install from the command line.")
    saved = load_settings()
    parser.add_argument("--base", default=saved.base, help="MOG-Server base URL, e.g. http://localhost:5000 (default: saved in the GUI settings)")
    parser.add_argument("--user", default=saved.user)
    parser.add_argument("--pass", dest="password", default=saved.password)
    parser.add_argument("--game-id", type=int, default=None)
    parser.add_argument("--list", action="store_true", help="List the games on the server and exit")
    parser.add_argument("--launch", type=int, metavar="GAME_ID", default=None, help="Launch an installed game and exit")
    for action, text in (
        ("pre", "Before a shortcut starts the game: restore saves that were waiting for the prefix"),
        ("watch", "Follow a started game and back its saves up when it ends"),
        ("sync", "Back up a game's saves now"),
    ):
        parser.add_argument(f"--save-{action}", type=int, metavar="GAME_ID", default=None, help=text)
    parser.add_argument("url", nargs="?", help="A mog:// link from the web UI to open in the graphical client")
    parser.add_argument("--gui", action="store_true", help="Open the graphical client (default when no other action is given)")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: ./<game name>)")
    parser.add_argument("--installer-path", default=None, help="Specific installer file to run, instead of auto-pick")
    parser.add_argument("--proton-build", default=None)
    parser.add_argument("--ttl", type=int, default=None, help="Install cache TTL in seconds (<=0 means unlimited)")
    parser.add_argument("--auto-mode", action=argparse.BooleanOptionalAction, default=None, help="Force auto mode on/off for this install (default: the server's own Settings)")
    parser.add_argument("--extract-only", action="store_true", help="Extract the game's archive as it is into the install cache instead of running an installer from it")
    parser.add_argument("--manual-mode", action="store_true", help="Force manual pick even if a candidate was auto-detected")
    parser.add_argument("--no-download", action="store_true", help="Just start/watch the install, don't stream files")
    parser.add_argument("--cancel", action="store_true", help="Cancel the running session for this game and exit")
    parser.add_argument("--clear", action="store_true", help="Delete the install cache for this game and exit")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Max seconds to wait for the install to finish")
    args = parser.parse_args()

    if args.launch is not None:
        return launch_installed(args.launch)
    for action in ("pre", "watch", "sync"):
        game_id = getattr(args, f"save_{action}")
        if game_id is not None:
            from mog_client.saves.runner import run_command

            if not (args.base and args.user and args.password):
                warn("save sync needs the server settings saved from the GUI")
                return 1
            return run_command(action, game_id, saved, MogClient(Client(args.base, args.user, args.password)))
    if args.url:
        from mog_client.protocol import SCHEME

        if not args.url.lower().startswith(f"{SCHEME}://"):
            parser.error(f"{args.url!r} is not a {SCHEME}:// link")
    if args.gui or args.url or (args.game_id is None and not args.list):
        from mog_client.gui.app import run_gui

        return run_gui(args.url)

    if not (args.base and args.user and args.password):
        parser.error("--base, --user and --pass are required (or save them from the GUI settings)")
    client = MogClient(Client(args.base, args.user, args.password))

    if args.list:
        installed = load_library()
        for g in client.list_games():
            mark = "installed" if g["id"] in installed and installed[g["id"]].state == "installed" else ""
            log(f"{g['id']:>5}  {g['name']}  {mark}".rstrip())
        return 0
    if args.game_id is None:
        parser.error("--game-id is required")
    if args.cancel:
        client.cancel_session(args.game_id)
        log("cancelled")
        return 0
    if args.clear:
        client.clear_cache(args.game_id)
        log("cache cleared")
        return 0

    existing = client.get_session(args.game_id)
    session_id = existing.get("id") if existing and existing.get("state") == "done" else None

    if session_id is None:
        try:
            candidates = client.candidates(args.game_id)
            log(f"{len(candidates.get('candidates', []))} installer candidate(s) found")
        except RuntimeError as e:
            warn(str(e))

        session = client.start_session(
            args.game_id, args.installer_path, args.proton_build, args.ttl, args.auto_mode, args.manual_mode or None,
            extract_only=True if args.extract_only else None,
        )
        session_id = session.get("id")
        if session.get("state") == "awaiting_installer":
            poll_session(client, args.game_id, session_id=session_id)
            return 1

    out_dir = args.out or Path(safe_dirname(f"game-{args.game_id}"))

    if args.no_download:
        poll_session(client, args.game_id, timeout=args.timeout, session_id=session_id)
        return 0

    from mog_client.saves import installed as ledger

    before = ledger.begin(args.game_id, out_dir)
    stop_event = threading.Event()
    server_done = threading.Event()
    outcome: dict = {}

    def download() -> None:
        outcome["result"] = download_all_files(
            client, args.game_id, out_dir, stop_event, session_id, server_done=server_done
        )

    downloader = threading.Thread(target=download)
    downloader.start()
    try:
        final_session = poll_session(client, args.game_id, timeout=args.timeout, session_id=session_id)
        if final_session.get("state") == "done":
            server_done.set()
            # The server's installer ending doesn't end the download.
            log("server install finished, finishing the download...")
            downloader.join()
        else:
            stop_event.set()
            downloader.join(timeout=10)
    except KeyboardInterrupt:
        stop_event.set()
        downloader.join(timeout=10)
        warn("interrupted: re-run the same command to resume")
        return 130

    if final_session.get("state") == "done":
        if not outcome.get("result", (0, False))[1]:
            warn("download did not complete: re-run the same command to resume")
            return 1
        verify_and_repair(client, args.game_id, out_dir, session_id=session_id)
        ledger.finish(args.game_id, out_dir, before)
        log(f"done: {out_dir}")
        return 0
    if final_session.get("state") == "failed":
        warn(f"install failed: {final_session.get('error')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())