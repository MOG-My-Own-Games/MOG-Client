"""Install, finish-setup and uninstall workflows used by the GUI."""

from __future__ import annotations

import shlex
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable

from mog_client import logstore, steam
from mog_client.api import Client, MogClient, safe_dirname
from mog_client.config import InstalledGame, Settings, load_library, save_library
from mog_client.installdirs import default_root, present
from mog_client.launcher import (
    create_desktop_entry,
    desktop_file_path,
    entry_command,
    icon_path,
    pfx_dir,
    remove_entry_files,
    shortcut_lnk_path,
    write_directory_file,
)
from mog_client.saves import installed as ledger
from mog_client.saves.state import forget_game, save_install_manifest
from mog_client.scrape import fetch_artwork
from mog_client.transfer import download_all_files, poll_session, verify_and_repair

Log = Callable[[str], None]


def make_client(s: Settings) -> MogClient:
    return MogClient(Client(s.base, s.user, s.password))


_library_lock = threading.RLock()


def _update(rec: InstalledGame, **changes) -> None:
    with _library_lock:  # installs run on several threads, and read-modify-write must not interleave
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
    root: Path | None = None,
    extract_only: bool = False,
) -> InstalledGame:
    """Start-or-resume the server-side install and stream it to disk.

    `installer` is one of the server's candidates (path and kind) to run instead of
    the one it would pick itself. `root` is the install folder a new game goes into (the first one
    with room when not given); a game already begun stays where it is. `extract_only` unpacks the game's
    archive as it is into the install cache instead of running an installer from it, and is remembered
    for the game.

    Blocks until done, failed, stopped or the server needs a manual pick
    (raises RuntimeError with a message in the last two cases)."""
    gid = game["id"]
    lib = load_library()
    rec = lib.get(gid) or InstalledGame(
        game_id=gid, name=game["name"], install_dir=str((root or default_root(settings)) / safe_dirname(game["name"]))
    )
    _update(rec, state="installing", **({"extract_only": True} if extract_only else {}))
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
            extract_only=rec.extract_only,
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
    before = ledger.begin(gid, out_dir)  # what was there before this install, to tell it from what it adds
    # The download, not the server's installer, decides when we're finished.
    _, finished = download_all_files(
        client, gid, out_dir, stop, session_id, log=log, warn=log, on_bytes=on_bytes, server_done=server_done,
    )
    watcher.join(timeout=10)
    if not finished:
        state = server_final.get("state")
        if state == "awaiting_installer":
            raise RuntimeError(f"needs a manual installer pick, open {client.c.base}{server_final.get('vnc_url') or ''}")
        if state == "failed":
            raise RuntimeError(f"install failed: {server_final.get('error')}")
        return rec  # paused or cancelled: not a failure, the buttons already say so
    verify_and_repair(
        client,
        gid,
        out_dir,
        session_id=session_id,
        log=log,
        warn=log,
        on_manifest=lambda files: save_install_manifest(gid, files),
    )
    ledger.finish(gid, out_dir, before)  # whatever the server's list missed, the folder itself shows
    _update(rec, state="awaiting_executable")
    return rec


def finish_setup(
    rec: InstalledGame,
    game_meta: dict,
    executable: str,
    steam_user: Path | None,
    desktop: bool = True,
    launcher: str = "auto",
    client: MogClient | None = None,
) -> bool:
    """Record the chosen executable and bring the game's entries in line with it. The entries run
    the game through its launch script on their own; MOG is not involved when they start.

    Existing entries are edited, never deleted and recreated: a recreated Steam shortcut would
    get a new id and show up as a new game. Only an entry the user no longer wants is removed,
    and one is created only when there was none. Returns True when a Steam shortcut changed or
    was added (Steam sees that after a restart)."""
    rec.executable = executable
    command = entry_command(rec, launcher)
    art = fetch_artwork(game_meta, client)

    # The folder's icon is kept when the server has none now, so a failed download never wipes it.
    picture = art.get("icon") or art.get("portrait")
    if picture:
        icon_path(rec).write_bytes(picture)
    write_directory_file(rec)

    desktop_path = None
    if desktop:
        desktop_path = create_desktop_entry(rec, icon_path(rec) if icon_path(rec).exists() else None, launcher)
    else:
        for stale in (rec.desktop_entry, desktop_file_path(rec), shortcut_lnk_path(rec)):
            if stale:
                Path(stale).unlink(missing_ok=True)

    kept, steam_changed, pending = _sync_steam_entries(rec, steam_user, command, art)
    prefix = None if sys.platform == "win32" or rec.native else str(pfx_dir(rec))  # a native game has no prefix
    _update(
        rec,
        executable=executable,
        prefix=prefix,
        state="installed",
        desktop_entry=desktop_path,
        steam_entries=kept,
        steam_user=str(steam_user) if steam_user is not None else None,
        steam_pending=pending,
    )
    return steam_changed


def _sync_steam_entries(
    rec: InstalledGame, steam_user: Path | None, command: list[str], art: dict
) -> tuple[list[dict], bool, bool]:
    """Bring the game's Steam shortcut in line: (entries to keep, whether one changed, whether it had to wait).

    Steam keeps its shortcuts in memory and writes the file when it quits, which undoes anything
    changed in the meantime, so while it runs nothing is touched and `pending` says it is left for later."""
    if (steam_user is not None or rec.steam_entries) and steam.steam_running():
        logstore.warning(f"Steam is running: the shortcut of {rec.name} is left for when it is closed")
        return rec.steam_entries, False, True
    wanted_file = str(steam.shortcuts_path(steam_user)) if steam_user is not None else None
    start_dir, options = str(Path(rec.executable).parent), shlex.join(command[1:])
    kept: list[dict] = []
    changed = False
    for entry in rec.steam_entries:
        if entry["shortcuts_path"] != wanted_file:
            steam.remove_shortcut(entry)  # the user no longer wants it there
            continue
        outcome = steam.update_shortcut(entry, command[0], start_dir, options, name=rec.name, artwork=art)
        if outcome is None:  # deleted from Steam by hand: put it back
            continue
        kept.append(entry)
        changed |= outcome
    if steam_user is not None and not kept:
        kept.append(steam.add_shortcut(steam_user, rec.name, command[0], start_dir, options, artwork=art))
        changed = True
    return kept, changed, False


def regenerate_entries(
    rec: InstalledGame, game_meta: dict, preference: str = "auto", client: MogClient | None = None
) -> bool:
    """Rebuild the launch script, the desktop entry and the Steam shortcut (with fresh artwork)
    from the game's current settings. The Steam shortcut is edited in place; returns True when
    it changed and needs a Steam restart to show."""
    steam_user = Path(rec.steam_user) if rec.steam_user else None
    if steam_user is None and rec.steam_entries:
        steam_user = Path(rec.steam_entries[0]["shortcuts_path"]).parent.parent
    return finish_setup(
        rec, game_meta, rec.executable, steam_user, desktop=bool(rec.desktop_entry), launcher=preference, client=client
    )


def refresh_metadata(rec: InstalledGame, client: MogClient, preference: str = "auto") -> tuple[dict, bool]:
    """Fetch the game's metadata and artwork choice from the server again and rebuild what depends on
    them (icons, entries, the folder's icon), editing the Steam shortcut in place. Returns the fresh
    metadata and whether the Steam shortcut changed (Steam sees that after a restart)."""
    game = client.get_game(rec.game_id)
    return game, regenerate_entries(rec, game, preference, client)


def set_launcher(rec: InstalledGame, engine: str, preference: str = "auto") -> bool:
    """Switch the game to another launch engine, updating its entries in place: the launch
    script is rewritten, the desktop entry keeps its file, and a Steam shortcut is edited (never
    deleted and recreated, so its artwork and play time stay). Returns True when a Steam
    shortcut had to change, which Steam only notices after a restart."""
    previous = rec.launcher
    rec.launcher = engine
    try:
        command = entry_command(rec, preference)
    except RuntimeError:
        rec.launcher = previous
        raise
    if rec.desktop_entry:
        icon = Path(rec.install_dir) / ".mog-icon"
        create_desktop_entry(rec, icon if icon.exists() else None, preference)
    steam_changed = False
    pending = rec.steam_pending
    if rec.steam_entries and steam.steam_running():  # Steam would undo the change when it quits
        logstore.warning(f"Steam is running: the shortcut of {rec.name} is left for when it is closed")
        pending = True
    else:
        for entry in rec.steam_entries:
            steam_changed |= bool(
                steam.update_shortcut(entry, command[0], str(Path(rec.executable).parent), shlex.join(command[1:]))
            )
    _update(rec, launcher=engine, steam_pending=pending)
    return steam_changed


def settle_steam_shortcuts(client: MogClient, preference: str = "auto") -> list[str]:
    """Apply the shortcut changes that waited for Steam to be closed, if it is closed now. Returns the
    names of the games whose shortcut was brought up to date."""
    if steam.steam_running():
        return []
    done = []
    for rec in load_library().values():
        if not (rec.steam_pending and rec.state == "installed" and rec.executable and present(rec)):
            continue
        try:
            refresh_metadata(rec, client, preference)
        except (RuntimeError, OSError) as e:
            logstore.warning(f"Could not update the Steam shortcut of {rec.name}: {e}")
            continue
        if not load_library()[rec.game_id].steam_pending:
            done.append(rec.name)
    return done


def remove_entries(rec: InstalledGame) -> None:
    remove_entry_files(rec)
    for entry in rec.steam_entries:
        steam.remove_shortcut(entry)
    rec.desktop_entry = None
    rec.steam_entries = []


def _rmtree(path: str) -> bool:
    """Remove a file or a tree, retrying read-only entries. Returns True when it is gone."""

    def make_writable(func, p, _exc):
        Path(p).chmod(0o700)
        func(p)

    target = Path(path)
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=True)
    elif target.exists():
        shutil.rmtree(path, onerror=make_writable)
    return not target.exists() and not target.is_symlink()


def uninstall(rec: InstalledGame, delete_prefix: bool = False) -> list[str]:
    """Remove the game files and shortcuts, returning the paths that could not be deleted.

    The prefix lives in the game's folder and may hold save files, so it is only deleted when the
    caller got the user's consent; without it everything else in the folder goes and `pfx` stays."""
    remove_entries(rec)
    install = Path(rec.install_dir)
    keep = pfx_dir(rec)
    keeping = not delete_prefix and rec.prefix is not None and Path(rec.prefix) == keep and keep.is_dir()
    paths = [str(c) for c in install.iterdir() if c != keep] if keeping and install.is_dir() else [rec.install_dir]
    if delete_prefix and rec.prefix and not Path(rec.prefix).is_relative_to(install):
        paths.append(rec.prefix)
    leftovers = []
    for path in paths:
        try:
            if not _rmtree(path):
                leftovers.append(path)
        except OSError:
            leftovers.append(path)
    if not leftovers:
        lib = load_library()
        lib.pop(rec.game_id, None)
        save_library(lib)
        forget_game(rec.game_id)
    return leftovers
