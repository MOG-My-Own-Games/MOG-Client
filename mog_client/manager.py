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
from mog_client.config import InstalledGame, Settings, load_library, load_settings, save_library
from mog_client.installdirs import default_root, present, prune_empty
from mog_client.launcher import (
    create_desktop_entry,
    desktop_file_path,
    entry_command,
    icon_path,
    pfx_dir,
    remove_entry_files,
    shortcut_lnk_path,
    steam_import_path,
    write_desktop_file,
    write_directory_file,
    write_launch_script,
)
from mog_client.saves import installed as ledger
from mog_client.saves.state import forget_game, save_install_manifest
from mog_client.scrape import fetch_artwork
from mog_client.transfer import download_all_files, poll_session, verify_and_repair

# What run_install's error starts with when the server cannot choose an installer: the window tells it apart from a
# failure and offers the list of installers instead of sending the person to the server.
NEEDS_PICK = "needs a manual installer pick"

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
QUEUE_RETRY_SECONDS = 15.0  # how often a start an older server refused (its places all taken) is asked again


def holds_nothing(client, game_id: int, session_id: int) -> bool:
    """Whether a finished session's file list has no file with content in it (an installer that stopped at its first
    file leaves an empty one). A list that cannot be read is given the benefit of the doubt."""
    try:
        files = client.list_files(game_id, session_id=session_id).get("files", [])
    except RuntimeError:
        return False
    return all(not e.get("size_bytes") for e in files)


def is_loose(files: list[dict]) -> bool:
    """Whether an install's files are not all inside one folder of their own: some are at the top, or there are
    several folders there."""
    return any("/" not in e["path"] for e in files) or len({e["path"].split("/", 1)[0] for e in files}) > 1


def tuck_into_folder(root: Path, files: list[dict], name: str) -> tuple[Path, list[dict]]:
    """Move what an install put straight into `root` into a folder of its own, so the game's files do not sit among MOG's
    (the prefix, the launch script, the entries). Returns that folder and the files with their paths from `root`."""
    tops = {e["path"].split("/", 1)[0] for e in files}
    folder = root / name
    if name in tops:  # a game folder of that very name is among the files
        folder = root / f"{name} (game)"
    folder.mkdir(exist_ok=True)
    for top in sorted(tops):
        if (root / top).exists() or (root / top).is_symlink():
            shutil.move(str(root / top), str(folder / top))
    return folder, [{**e, "path": f"{folder.name}/{e['path']}"} for e in files]


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
    extract_only: bool | None = None,
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
    out_dir = Path(rec.files_dir or rec.install_dir)

    existing = client.get_session(gid)
    session_id = existing.get("id") if existing and existing.get("state") == "done" else None
    if session_id is not None and holds_nothing(client, gid, session_id):
        log("the server's finished install has no file with content in it: running it again")
        session_id = None
    if session_id is None:
        archive = installer is not None and installer.get("kind") in ARCHIVE_SOURCE_KINDS
        while True:
            try:
                session = client.start_session(
                    gid,
                    None if archive or installer is None else installer["path"],
                    None,
                    None,
                    source_path=installer["path"] if archive else None,
                    extract_only=True if rec.extract_only else extract_only,  # None leaves it to the server
                )
                break
            except RuntimeError as e:
                # An older server refuses a start while its places are taken, where a newer one queues it: wait and ask again.
                if "429" not in str(e) and "Too many concurrent installs" not in str(e):
                    raise
                on_session({"state": "queued"})
                log("the server is running as many installs as it can: waiting for a place")
                if stop.wait(QUEUE_RETRY_SECONDS):
                    return rec  # cancelled while waiting: not a failure
        session_id = session.get("id")
        if session.get("state") == "queued":
            on_session(session)  # the window shows it waiting at once, not at the first poll
        if session.get("state") == "awaiting_installer":
            raise RuntimeError(f"{NEEDS_PICK}, open {client.c.base}{session.get('vnc_url') or ''}")
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
    before = ledger.begin(gid, Path(rec.install_dir))  # what was there before this install, to tell it from what it adds
    # The download, not the server's installer, decides when we're finished.
    _, finished = download_all_files(
        client, gid, out_dir, stop, session_id, log=log, warn=log, on_bytes=on_bytes, server_done=server_done,
    )
    watcher.join(timeout=10)
    if not finished:
        state = server_final.get("state")
        if state == "awaiting_installer":
            raise RuntimeError(f"{NEEDS_PICK}, open {client.c.base}{server_final.get('vnc_url') or ''}")
        if state == "failed":
            raise RuntimeError(f"install failed: {server_final.get('error')}")
        return rec  # paused or cancelled: not a failure, the buttons already say so
    listed: list[dict] = []
    verify_and_repair(client, gid, out_dir, session_id=session_id, log=log, warn=log, on_manifest=listed.extend)
    if listed and rec.files_dir is None and rec.executable is None and is_loose(listed):
        folder, listed = tuck_into_folder(out_dir, listed, safe_dirname(rec.name))
        log(f"the game came without a folder of its own: its files are in {folder.name}/")
        _update(rec, files_dir=str(folder))
    if listed:
        save_install_manifest(gid, listed)
    ledger.finish(gid, Path(rec.install_dir), before)  # whatever the server's list missed, the folder itself shows
    _update(rec, state="awaiting_executable")
    return rec


def finish_setup(
    rec: InstalledGame,
    game_meta: dict,
    executable: str,
    steam_users: list[Path],
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

    kept, steam_changed, pending = _sync_steam_entries(rec, steam_users, command, art)
    prefix = None if sys.platform == "win32" or rec.native else str(pfx_dir(rec))  # a native game has no prefix
    _update(
        rec,
        executable=executable,
        prefix=prefix,
        state="installed",
        desktop_entry=desktop_path,
        steam_entries=kept,
        steam_users=[str(d) for d in steam_users],
        steam_pending=pending,
    )
    return steam_changed


def _sync_steam_entries(
    rec: InstalledGame, steam_users: list[Path], command: list[str], art: dict
) -> tuple[list[dict], bool, bool]:
    """Bring the game's Steam shortcuts in line, one in each account wanted: (entries to keep, whether one changed,
    whether it has to be looked at again).

    With Steam running, the account signed in to it gets its shortcut through Steam itself (as SteamOS's "Add to
    Steam" does), which shows at once and which Steam keeps. The others are written to their file, which Steam is
    said to undo when it quits, so `pending` asks for the next start with Steam closed to check them."""
    running = steam.steam_running()
    active = steam.active_account() if running else None
    wanted_files = {str(steam.shortcuts_path(d)): d for d in steam_users}
    start_dir, options = str(Path(rec.executable).parent), shlex.join(command[1:])
    kept: list[dict] = []
    changed = wrote_file = False
    for entry in rec.steam_entries:
        if entry["shortcuts_path"] not in wanted_files:
            steam.remove_shortcut(entry)  # the user no longer wanted it there
            continue
        outcome = steam.update_shortcut(entry, command[0], start_dir, options, name=rec.name, artwork=art)
        if outcome is None:  # deleted from Steam by hand: put it back
            continue
        kept.append(entry)
        changed |= outcome
        wrote_file |= bool(outcome)
    have = {e["shortcuts_path"] for e in kept}
    for path, user_dir in wanted_files.items():
        if path in have:
            continue
        entry = _add_through_steam(rec, user_dir, command, art) if running and user_dir.name == active else None
        if entry is None:
            entry = steam.add_shortcut(user_dir, rec.name, command[0], start_dir, options, artwork=art)
            wrote_file = True
        kept.append(entry)
        changed = True
    return kept, changed, wrote_file and running


def _add_through_steam(rec: InstalledGame, user_dir: Path, command: list[str], art: dict) -> dict | None:
    """Have the running Steam add the game for its signed-in account, then give the entry its artwork. None when Steam
    did not take it (it is then written to the file instead)."""
    desktop = steam_import_path(rec)
    try:
        write_desktop_file(desktop, rec, command)
    except OSError as e:
        logstore.warning(f"Could not write {desktop} for Steam: {e}")
        return None
    if not steam.add_through_steam(desktop):
        logstore.warning(f"Steam did not take {rec.name} through its add-a-game request: writing its shortcuts file instead")
        return None
    entry = steam.wait_for_shortcut(user_dir, rec.name)
    if entry is None:
        logstore.warning(f"{rec.name} did not show in Steam's shortcuts: writing its shortcuts file instead")
        return None
    entry["artwork"] = steam.write_artwork(user_dir, entry["appid"], art)
    logstore.info(f"{rec.name} was added to Steam ({user_dir.name}) through Steam itself")
    return entry


def regenerate_entries(
    rec: InstalledGame, game_meta: dict, preference: str = "auto", client: MogClient | None = None
) -> bool:
    """Rebuild the launch script, the desktop entry and the Steam shortcut (with fresh artwork)
    from the game's current settings. The Steam shortcut is edited in place; returns True when
    it changed and needs a Steam restart to show."""
    steam_users = [Path(d) for d in rec.steam_users]
    if not steam_users:  # an entry made before the accounts were recorded
        steam_users = [Path(e["shortcuts_path"]).parent.parent for e in rec.steam_entries]
    return finish_setup(
        rec, game_meta, rec.executable, steam_users, desktop=bool(rec.desktop_entry), launcher=preference, client=client
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
    for entry in rec.steam_entries:
        steam_changed |= bool(
            steam.update_shortcut(entry, command[0], str(Path(rec.executable).parent), shlex.join(command[1:]))
        )
    _update(rec, launcher=engine, steam_pending=rec.steam_pending or (steam_changed and steam.steam_running()))
    return steam_changed


def refresh_launch_files(settings: Settings) -> None:
    """Write every installed game's launch script (a batch file on Windows) and entries again, so they
    hold what this version knows. Run once after an update. On Linux the entries already run the script, so
    only it is rewritten; on Windows they pointed at the exe, so they and the Steam shortcuts are edited in place."""
    for rec in load_library().values():
        if not (rec.state == "installed" and rec.executable and present(rec)):
            continue
        try:
            if sys.platform == "win32":
                set_launcher(rec, rec.launcher, settings.launcher)
            else:
                write_launch_script(rec, settings.launcher)
        except (RuntimeError, OSError) as e:
            logstore.warning(f"Could not rewrite the launch files of {rec.name}: {e}")


def settle_steam_shortcuts(client: MogClient, preference: str = "auto") -> list[str]:
    """With Steam closed, check the shortcuts written while it ran are still there, and write again those it undid.
    Returns the names of the games whose shortcut had to be written again."""
    if steam.steam_running():
        return []
    done = []
    for rec in load_library().values():
        if not (rec.steam_pending and rec.state == "installed" and rec.executable and present(rec)):
            continue
        if rec.steam_entries and all(steam.has_shortcut(e) for e in rec.steam_entries):
            _update(rec, steam_pending=False)
            continue
        try:
            refresh_metadata(rec, client, preference)
        except (RuntimeError, OSError) as e:
            logstore.warning(f"Could not update the Steam shortcut of {rec.name}: {e}")
            continue
        if not load_library()[rec.game_id].steam_pending:
            done.append(rec.name)
    return done


def apply_steam_accounts(accounts: list[Path], client: MogClient, preference: str = "auto") -> list[str]:
    """Make every installed game's Steam shortcuts match the accounts wanted, now. Returns the names of the games
    that changed."""
    wanted = [str(d) for d in accounts]
    wanted_files = {str(steam.shortcuts_path(d)) for d in accounts}
    done = []
    for rec in load_library().values():
        if not (rec.state == "installed" and rec.executable and present(rec)):
            continue
        if wanted_files == {e["shortcuts_path"] for e in rec.steam_entries}:
            continue
        _update(rec, steam_users=wanted)
        try:
            refresh_metadata(rec, client, preference)
        except (RuntimeError, OSError) as e:
            logstore.warning(f"Could not update the Steam shortcut of {rec.name}: {e}")
            continue
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
        prune_empty(install, load_settings().install_roots)  # a prefix kept with nothing in it, or a folder left empty
    return leftovers

