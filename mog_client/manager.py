"""Install, finish-setup and uninstall workflows used by the GUI (and `--launch`)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable

from mog_client import steam
from mog_client.api import Client, MogClient, safe_dirname
from mog_client.config import InstalledGame, Settings, load_library, save_library
from mog_client.launcher import client_command, create_desktop_entry
from mog_client.scrape import fetch_artwork
from mog_client.transfer import download_all_files, poll_session, verify_and_repair

Log = Callable[[str], None]


def make_client(s: Settings) -> MogClient:
    return MogClient(Client(s.base, s.user, s.password))


def _update(rec: InstalledGame, **changes) -> None:
    for k, v in changes.items():
        setattr(rec, k, v)
    lib = load_library()
    lib[rec.game_id] = rec
    save_library(lib)


ARCHIVE_SOURCE_KINDS = ("disc image", "archive")


def run_install(
    client: MogClient,
    game: dict,
    settings: Settings,
    stop: threading.Event,
    log: Log,
    on_session: Callable[[dict], None],
    on_bytes: Callable[[int, int], None],
    installer: dict | None = None,
) -> InstalledGame:
    """Start-or-resume the server-side install and stream it to disk.

    `installer` is one of the server's candidates (path and kind) to run instead of
    the one it would pick itself.

    Blocks until done, failed, stopped or the server needs a manual pick
    (raises RuntimeError with a message in the last two cases)."""
    gid = game["id"]
    lib = load_library()
    rec = lib.get(gid) or InstalledGame(
        game_id=gid, name=game["name"], install_dir=str(settings.games_path / safe_dirname(game["name"]))
    )
    _update(rec, state="installing")
    out_dir = Path(rec.install_dir)

    existing = client.get_session(gid)
    session_id = existing.get("id") if existing and existing.get("state") == "done" else None
    if session_id is None:
        archive = installer is not None and installer.get("kind") in ARCHIVE_SOURCE_KINDS
        session = client.start_session(
            gid,
            None if archive or installer is None else installer["path"],
            None,
            None,
            source_path=installer["path"] if archive else None,
        )
        session_id = session.get("id")
        if session.get("state") == "awaiting_installer":
            raise RuntimeError(f"needs a manual installer pick, open {client.c.base}{session.get('vnc_url') or ''}")
    _update(rec, session_id=session_id)

    server_done = threading.Event()
    server_final: dict = {}

    def watch_server() -> None:
        final = poll_session(
            client, gid, timeout=24 * 3600, session_id=session_id, log=log, warn=log, on_session=on_session, stop_event=stop
        )
        server_final.update(final or {})
        if server_final.get("state") == "done":
            server_done.set()
        else:
            # failed, needs a manual pick or paused: nothing more will arrive.
            stop.set()

    watcher = threading.Thread(target=watch_server, daemon=True)
    watcher.start()
    # The download, not the server's installer, decides when we're finished.
    _, finished = download_all_files(
        client, gid, out_dir, stop, session_id, log=log, warn=log, on_bytes=on_bytes, server_done=server_done
    )
    watcher.join(timeout=10)
    if not finished:
        state = server_final.get("state")
        if state == "awaiting_installer":
            raise RuntimeError(f"needs a manual installer pick, open {client.c.base}{server_final.get('vnc_url') or ''}")
        if state == "failed":
            raise RuntimeError(f"install failed: {server_final.get('error')}")
        return rec  # paused or cancelled: not a failure, the buttons already say so
    verify_and_repair(client, gid, out_dir, session_id=session_id, log=log, warn=log)
    _update(rec, state="awaiting_executable")
    return rec


def finish_setup(
    rec: InstalledGame, game_meta: dict, executable: str, steam_user: Path | None, desktop: bool = True
) -> InstalledGame:
    """Record the chosen executable, then create the desktop and Steam entries."""
    art = fetch_artwork(game_meta)
    desktop_path = None
    if desktop:
        icon = Path(rec.install_dir) / ".mog-icon"
        if "portrait" in art:
            icon.write_bytes(art["portrait"])
        desktop_path = create_desktop_entry(rec, icon if icon.exists() else None)
    entries = []
    if steam_user is not None:
        entries.append(
            steam.add_shortcut(
                steam_user,
                rec.name,
                client_command(),
                str(Path(client_command()).parent),
                f"--launch {rec.game_id}",
                artwork=art,
            )
        )
    _update(rec, executable=executable, state="installed", desktop_entry=desktop_path, steam_entries=entries)
    return rec


def remove_entries(rec: InstalledGame) -> None:
    if rec.desktop_entry:
        Path(rec.desktop_entry).unlink(missing_ok=True)
    for entry in rec.steam_entries:
        steam.remove_shortcut(entry)
    rec.desktop_entry = None
    rec.steam_entries = []


def _rmtree(path: str) -> bool:
    """Remove a tree, retrying read-only entries. Returns True when it is gone."""

    def make_writable(func, p, _exc):
        Path(p).chmod(0o700)
        func(p)

    if Path(path).exists():
        shutil.rmtree(path, onerror=make_writable)
    return not Path(path).exists()


def uninstall(rec: InstalledGame, delete_prefix: bool = False) -> list[str]:
    """Remove the game files and shortcuts, returning the paths that could not
    be deleted. A local Wine prefix may hold save files, so it is only deleted
    when the caller got the user's consent."""
    remove_entries(rec)
    leftovers = []
    for path in [rec.install_dir] + ([rec.prefix] if delete_prefix and rec.prefix else []):
        try:
            if not _rmtree(path):
                leftovers.append(path)
        except OSError:
            leftovers.append(path)
    if not leftovers:
        lib = load_library()
        lib.pop(rec.game_id, None)
        save_library(lib)
    return leftovers
