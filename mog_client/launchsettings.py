"""How a game is launched, shared through the server: its Proton build and what winecfg changed in its prefix. Stdlib only.

The server keeps them per game, for all of its devices or for one; a device takes its own entry first and the shared one
for what that lacks."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from mog_client import connectivity, launcher, launchargs, logstore, manager, protondl, winecfg, winetricks
from mog_client.api import MogClient
from mog_client import config
from mog_client.config import InstalledGame
from mog_client.launcher import detect_launcher, effective_launcher
from mog_client.saves import devices, watcher

WRITE_WAIT = 10.0  # seconds after winecfg ends that Wine is given to write its registry to disk


def digest(reg_text: str) -> str:
    return hashlib.sha1(reg_text.encode()).hexdigest()


def effective(answer: dict | None) -> dict:
    """{"proton", "winecfg_reg", "launch_args", "executable", "winetricks"} from the server's answer: the device's own value, else the shared one, else None;
    the winetricks entries of both rows together (a device adds to what all of them have), one for each key: for a setting both
    set, the device's value wins."""
    out: dict = {"proton": None, "winecfg_reg": None, "launch_args": None, "executable": None}
    verbs: list = []
    for row in ((answer or {}).get("shared"), (answer or {}).get("device")):  # the device's last: it wins
        for field in out:
            if isinstance(row, dict) and row.get(field) is not None:
                out[field] = row[field]
        if isinstance(row, dict):
            verbs += winetricks.clean(row.get("winetricks"))
    out["winetricks"] = winetricks.clean(verbs)
    if out["launch_args"] is not None:  # tags (an older server sent the text: it is split into tags)
        out["launch_args"] = launchargs.as_tags(out["launch_args"])
    return out


def device_id(client: MogClient) -> int | None:
    """This machine's id on the server, registering it if need be; None when that needs the person's answer or the server
    cannot do it (the settings then go to, and come from, the game's shared entry)."""
    record = devices.known_device()
    if record is None:
        try:
            record = devices.register(client)
        except (devices.HostnameTaken, devices.NameTaken, RuntimeError):
            return None
    return record.device_id


def prefix_of(rec: InstalledGame) -> Path:
    return launcher.pfx_dir(rec)


def needs_download(rec: InstalledGame, preference: str) -> bool:
    """Whether the game's Proton build is a Proton-GE or UMU-Proton release this device lacks and its engine can use."""
    name = rec.proton
    engine = detect_launcher(effective_launcher(rec, preference))
    return bool(name) and engine in ("umu", "faugus", "proton") and protondl.is_fetchable(name) and name not in launcher.list_proton_builds()


def ensure_build(
    rec: InstalledGame, preference: str, say: Callable[[str], None] = lambda _m: None, on_progress=None
) -> bool:
    """Fetch the game's Proton-GE build if this device lacks it, and rewrite the launch script to use it. Faugus does not
    fetch one itself: it shows a message that the build is missing. True when it was fetched; RuntimeError when it could not be."""
    if not needs_download(rec, preference):
        return False
    say(f"Downloading {rec.proton} (once; it is large)")
    protondl.install(rec.proton, launcher.proton_install_root(), on_progress=on_progress)
    manager.set_proton(rec, rec.proton, preference)
    return True


def pull(
    client: MogClient, rec: InstalledGame, preference: str, say: Callable[[str], None] = lambda _m: None, on_progress=None
) -> None:
    """Put what the server holds for the game on this machine: the Proton build (`on_progress(done, total)` follows its
    download), and the winecfg settings when they differ from the ones last applied. Nothing the server lacks is touched.
    Raises RuntimeError when applying the settings fails, once everything else that could be done has been (a verb that
    fails does not keep the winecfg settings, DLL overrides among them, from being put in: the game needs those most).
    With the server out of reach there is nothing to take: what was applied last time stays (and a build the game needs is
    still fetched from the internet, if that is there)."""
    if connectivity.is_offline():
        if connectivity.internet_ok():
            ensure_build(rec, preference, say, on_progress)
        return
    flush(client, rec.game_id)  # what was changed here and not sent yet goes first, and is not overwritten by the server's older value
    unsent = waiting_fields(rec.game_id)
    answer = client.launch_settings(rec.game_id, device_id(client))
    wanted = effective(answer)
    if answer is not None:  # an older server cannot say: nothing then is taken for deleted
        if answer.get("shared") or answer.get("device"):
            if not rec.launch_synced:
                manager._update(rec, launch_synced=True)
        elif rec.launch_synced:
            _align_with_empty_server(rec, preference, unsent, say)
    if "proton" in unsent:
        wanted["proton"] = None
    if "winecfg_reg" in unsent:
        wanted["winecfg_reg"] = None
    if "winetricks" in unsent:  # the list sent from here is newer: what it took out must not come back from the older one
        wanted["winetricks"] = []
    _sync_args(client, rec, answer, preference)
    if wanted["proton"] and wanted["proton"] != rec.proton:
        manager.set_proton(rec, wanted["proton"], preference)
        say(f"{rec.name} now runs under {wanted['proton']}")
    ensure_build(rec, preference, say, on_progress)
    pending = winetricks.missing(wanted["winetricks"], rec.winetricks)
    unknown = [v for v in pending if "=" in v and winetricks.registry_changes(v) is None]
    if unknown:  # winetricks would be left to deal with these, and it may open a window (a wrong name always does)
        say(f"{', '.join(unknown)}: the client cannot put that setting in the prefix by itself, so it is not applied at start; "
            "press Done in Winetricks to try it with winetricks")
    pending = [v for v in pending if v not in unknown]
    failure: RuntimeError | None = None
    todo = [v for v in pending if v not in rec.winetricks_failed]
    if len(todo) < len(pending):
        say(f"{', '.join(v for v in pending if v in rec.winetricks_failed)} could not be applied before and is not tried at each "
            "start: change it, or press Done in Winetricks, to try again")
    if todo:  # before the winecfg settings, which a verb may overwrite (DLL overrides)
        say(f"Installing {', '.join(todo)} into the prefix of {rec.name} with winetricks (the game starts when it is done)")
        try:
            run_winetricks(rec, preference, todo)
        except RuntimeError as e:
            manager._update(rec, winetricks_failed=list(dict.fromkeys([*rec.winetricks_failed, *todo])))
            failure = e
    reg = winecfg.clean_import(wanted["winecfg_reg"] or "")
    if reg and digest(reg) != rec.winecfg_applied:
        say(f"Applying the Wine settings of {rec.name} kept on the server")
        apply_reg(rec, preference, reg)
        manager._update(rec, winecfg_applied=digest(reg))
    if failure is not None:
        raise failure


def _sync_args(client: MogClient, rec: InstalledGame, answer: dict | None, preference: str) -> None:
    """Keep the launch arguments the same here and on the server by merging them tag by tag: the tags the server holds (edited
    on its web page, or on another device) and the tags this side changed since they last agreed (`launch_args_synced`) both
    stay, and neither editor's change is lost. The launch script follows when the list changed here, and the server is sent
    what it lacks, as tags to add and to remove (so it merges them into its own list). A server that cannot be reached, or an
    older one, changes nothing: the next look tries again, since what is not agreed yet is what differs from the last
    agreement."""
    if answer is None:
        return
    mine_on_device = (answer.get("device") or {}).get("launch_args") is not None
    theirs = effective(answer)["launch_args"] or []
    ours, base = list(rec.launch_args), list(rec.launch_args_synced or [])
    merged = launchargs.merge(base, ours, theirs)
    if merged != ours:
        manager.set_launch_args(rec, merged, preference)  # the launch script follows: a shortcut starts the game with them
    if merged != theirs:
        change = {"launch_args_add": [t for t in merged if t not in theirs], "launch_args_remove": [t for t in theirs if t not in merged]}
        status = client.put_launch_settings(rec.game_id, device_id(client) if mine_on_device else None, change)
        if status == 403 and not mine_on_device:  # only an admin sets what all devices share: this device's own list then
            own = device_id(client)
            status = client.put_launch_settings(rec.game_id, own, change) if own is not None else status
        if status != 200:
            return
        _mark_synced(rec.game_id)
    manager.set_launch_args_agreed(rec, merged)


def executable_key(rec: InstalledGame) -> str | None:
    """The game's executable as the server keeps it: a path with "/" from the folder of the game's own files (paths from the
    install differ between machines). None when there is no executable or it is not inside that folder."""
    if not rec.executable:
        return None
    try:
        return Path(rec.executable).relative_to(rec.files_dir or rec.install_dir).as_posix()
    except ValueError:
        return None


def remember_executable(client: MogClient, rec: InstalledGame) -> None:
    """Keep the chosen executable on the server, for this device (sent as soon as the server can take it, like the other launch
    settings) and, when this is an admin, for all of them: a new install of the game, here or on another device, then knows
    which one to offer. Only an admin may set what all devices share, so another user's try for it is quietly left out."""
    key = executable_key(rec)
    if key is None:
        return
    push(client, rec, {"executable": key}, everywhere=False)
    client.put_launch_settings(rec.game_id, None, {"executable": key})


def remembered_executable(client: MogClient, rec: InstalledGame) -> Path | None:
    """The executable the server knows for the game, as a file below this install, or None: nothing kept, an older server,
    no server to ask, or a file that is not there in this install."""
    if connectivity.is_offline():
        return None
    try:
        key = effective(client.launch_settings(rec.game_id, device_id(client)))["executable"]
    except RuntimeError:
        return None
    if not key:
        return None
    if ".." in key.split("/") or key.startswith("/") or "\\" in key:  # a path that leaves the game's folder is not ours to run
        return None
    path = Path(rec.files_dir or rec.install_dir) / key
    return path if path.is_file() else None


def pull_args(client: MogClient, rec: InstalledGame, preference: str) -> None:
    """Only the launch arguments: for a game that has no Proton build or Wine prefix to bring along (a Windows or a native
    Linux game), for the look at every installed game's, and for the send after an edit here."""
    _sync_args(client, rec, client.launch_settings(rec.game_id, device_id(client)), preference)


def _align_with_empty_server(rec: InstalledGame, preference: str, unsent: set[str], say: Callable[[str], None]) -> None:
    """The server held launch settings for the game and holds none now: someone deleted them there. The game goes back to
    the launcher's own Proton build, and what winecfg settings changed in its prefix is put back (see `revert_reg`; if that
    fails the next start tries again). What winetricks put there stays, since nothing can take it out; what is still waiting
    to be sent is not touched."""
    if rec.proton and "proton" not in unsent:
        manager.set_proton(rec, None, preference)
        say(f"The launch settings of {rec.name} were deleted on the server: it runs under the launcher's own Proton again")
    if rec.winecfg_applied and "winecfg_reg" not in unsent:
        try:
            if revert_reg(rec, preference):
                say(f"The Wine settings of {rec.name} were taken out of its prefix")
            else:
                say(f"The Wine settings of {rec.name} stay in its prefix: nothing was recorded of what they changed")
        except (RuntimeError, OSError) as e:
            say(f"The Wine settings of {rec.name} could not be taken out of its prefix: {e}")
            return  # kept as they are, so the next start tries again
        manager._update(rec, winecfg_applied=None)
    manager._update(rec, launch_synced=False)


def _mark_synced(game_id: int) -> None:
    rec = config.load_library().get(game_id)
    if rec is not None and not rec.launch_synced:
        manager._update(rec, launch_synced=True)


# --- Keeping changes for the server until it has them ---
#
# A change made here (a Proton version, what winecfg or winetricks did) is written to a file first and sent after, and stays in it
# until the server answers 200, so a server that cannot be reached loses nothing: it is tried again at every look at the
# server (see `flush_all`) and before a game starts. It is a file of its own, not part of the game's record, so it outlives an
# uninstall. A later change to the same thing replaces the earlier one waiting.

_OUTBOX_LOCK = threading.RLock()
RETRY_STATUSES = (401, 403, 408, 429)  # besides a server that cannot be reached and every 5xx: it may be put right


def outbox_path():
    return config.data_dir() / "launch-outbox.json"


def _load_outbox() -> dict[str, list[dict]]:
    raw = config._read_json(outbox_path(), {})
    return raw if isinstance(raw, dict) else {}


def _save_outbox(outbox: dict[str, list[dict]]) -> None:
    config._write_json(outbox_path(), {k: v for k, v in outbox.items() if v})


def waiting(game_id: int) -> list[dict]:
    """What is waiting to be sent for the game: [{"everywhere": bool, "changes": {...}}]."""
    with _OUTBOX_LOCK:
        return list(_load_outbox().get(str(game_id), []))


def waiting_fields(game_id: int) -> set[str]:
    """The settings of the game that have an unsent change here: the server's older value must not replace them."""
    return {field for entry in waiting(game_id) for field in entry.get("changes", {})}


def has_waiting() -> bool:
    with _OUTBOX_LOCK:
        return any(_load_outbox().values())


def queue(game_id: int, changes: dict, everywhere: bool) -> None:
    with _OUTBOX_LOCK:
        outbox = _load_outbox()
        entries = outbox.setdefault(str(game_id), [])
        for entry in entries:
            if entry.get("everywhere") == everywhere:
                entry["changes"] = {**entry.get("changes", {}), **changes}
                break
        else:
            entries.append({"everywhere": everywhere, "changes": dict(changes)})
        _save_outbox(outbox)


def _drop(game_id: int, entry: dict) -> None:
    with _OUTBOX_LOCK:
        outbox = _load_outbox()
        outbox[str(game_id)] = [e for e in outbox.get(str(game_id), []) if e != entry]
        _save_outbox(outbox)


def worth_retrying(status: int | None) -> bool:
    return status is None or status >= 500 or status in RETRY_STATUSES


def flush(client: MogClient, game_id: int, say: Callable[[str], None] = lambda _m: None) -> bool:
    """Send what waits for the game, oldest first. An entry goes when the server says 200, or refuses it for good (it cannot
    take it as it is: sending it again would not change that, and it would block what follows); one the server could not take
    for now stays. True when nothing is left."""
    with _OUTBOX_LOCK:
        if connectivity.is_offline():
            return not waiting(game_id)  # it all stays where it is until the server is back
        for entry in waiting(game_id):
            target = None
            if not entry.get("everywhere"):
                target = device_id(client)
                if target is None:
                    return False  # this machine has no name on the server yet: not for all devices instead of this one
            status = client.put_launch_settings(game_id, target, entry["changes"])
            if status == 200:
                _drop(game_id, entry)
                _mark_synced(game_id)
            elif worth_retrying(status):
                return False
            else:
                say(f"The server refused the launch settings of game {game_id} (HTTP {status}); they were dropped")
                _drop(game_id, entry)
        return True


def flush_all(client: MogClient, say: Callable[[str], None] = lambda _m: None) -> None:
    """Try again every game with something waiting; stops at the first the server cannot be reached for."""
    with _OUTBOX_LOCK:
        game_ids = [int(k) for k, v in _load_outbox().items() if v and k.isdigit()]
    for game_id in game_ids:
        if not flush(client, game_id, say):
            return
        say(f"Launch settings of game {game_id} sent to the server")


def winecfg_to_keep(game_id: int, answer: dict | None) -> str | None:
    """The winecfg settings a new prefix of the game is to get: the ones still waiting to be sent from here, else the
    server's (`answer`)."""
    for entry in reversed(waiting(game_id)):
        if "winecfg_reg" in entry.get("changes", {}):
            return entry["changes"]["winecfg_reg"]
    return effective(answer)["winecfg_reg"]


def push(client: MogClient, rec: InstalledGame, changes: dict, everywhere: bool) -> bool:
    """Keep `changes` for this device or, with `everywhere`, for all of the game's: written to the file first, then sent. False
    when the server has not taken them yet; they are sent as soon as it can."""
    queue(rec.game_id, changes, everywhere)
    return flush(client, rec.game_id)


def _winecfg_dir() -> Path:
    return config.data_dir() / "winecfg"


def undo_path(rec: InstalledGame) -> Path:
    """What puts the game's prefix back as it was before winecfg settings were put in it or made in it (a `.reg` of the old
    values, and deletions of the values that were new). Only what really changed is in it: a prefix is full of the launcher's
    own defaults in these same keys, and those are not ours to touch."""
    return _winecfg_dir() / f"{rec.game_id}.undo.reg"


def prefix_settings(rec: InstalledGame) -> str | None:
    """The winecfg settings the prefix holds now, or None when it has no registry yet: the engine makes the prefix the first time
    anything runs in it, with its own defaults in these same keys, and with no earlier state to compare with, a default cannot
    be told from a setting."""
    reg_file = winecfg.find_user_reg(prefix_of(rec))
    return winecfg.export_settings(reg_file.read_text(errors="replace")) if reg_file else None


def remember_undo(rec: InstalledGame, undo: str) -> None:
    """Add `undo` to what is kept for putting the prefix back; a value already in it keeps the older entry (what it was before
    any of the changes)."""
    if not undo:
        return
    path = undo_path(rec)
    older = path.read_text(errors="replace") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(winecfg.merge_undo(older, undo) if older else undo)


def _import_reg(rec: InstalledGame, preference: str, path: Path) -> None:
    proc = launcher.start_tool(rec, preference, "regedit", ("/S", str(path)))
    if proc.wait(timeout=300) != 0:
        raise RuntimeError(launcher.launch_failure(proc) or "regedit failed")
    # A launcher such as Faugus's may hand over and return at once: wait for regedit itself, if it is still there.
    watcher.wait_for_tool("regedit", prefix_of(rec), appear_timeout=10.0, linger=1.0)


def apply_reg(rec: InstalledGame, preference: str, reg_text: str) -> None:
    """Import settings into the game's prefix with the engine's own regedit; only what `winecfg.clean_import` leaves. What
    they change is noted first, so `revert_reg` can put it back."""
    reg = winecfg.clean_import(reg_text)
    if not reg:
        return
    before = prefix_settings(rec)
    if before is not None:
        remember_undo(rec, winecfg.undo_text(before, reg))
    path = _winecfg_dir() / f"{rec.game_id}.reg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reg)
    _import_reg(rec, preference, path)


def revert_reg(rec: InstalledGame, preference: str) -> bool:
    """Put the game's prefix back as it was before winecfg settings were put in it or made in it, by importing what
    `remember_undo` kept. False when nothing was kept (settings from before that was recorded stay where they are);
    RuntimeError when regedit fails, and what was kept stays for another try."""
    path = undo_path(rec)
    if not path.is_file():
        return False
    _import_reg(rec, preference, path)
    path.unlink(missing_ok=True)
    return True


def apply_settings(rec: InstalledGame, preference: str, entries: list[str]) -> None:
    """Put winetricks settings the client knows (`vd=1280x1024`, see `winetricks.registry_changes`) into the game's prefix by
    writing them into its registry with the engine's own regedit: no winetricks, so no window of it. The prefix is the game's
    own, where it already is."""
    changes = [change for entry in entries for change in winetricks.registry_changes(entry) or []]
    if not changes:
        return
    if any(winetricks.key_of(entry) == "locale" for entry in entries):
        remember_code_pages(rec)
    path = _winecfg_dir() / f"{rec.game_id}.winetricks.reg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(winecfg._reg_of(changes))
    _import_reg(rec, preference, path)


def run_winetricks(rec: InstalledGame, preference: str, verbs: list[str]) -> None:
    """Apply `verbs` (verbs, and settings such as `vd=1280x1024`) to the game's prefix and note them. The settings the client
    knows are written into the registry (silent); the rest goes to winetricks, unattended. Raises RuntimeError when that
    fails; what failed is not noted, so the next try starts again."""
    verbs = winetricks.clean(verbs)
    if not verbs:
        return
    direct = [v for v in verbs if winetricks.registry_changes(v) is not None]
    rest = [v for v in verbs if v not in direct]
    if rest:
        proc = launcher.start_tool(rec, preference, "winetricks", ("-q", *rest))
        code = proc.wait()
        # A launcher such as Faugus's may hand over and return at once: wait for winetricks itself, if it is still there.
        watcher.wait_for_tool("winetricks", prefix_of(rec), appear_timeout=15.0, linger=5.0)
        if code != 0:
            raise RuntimeError(launcher.launch_failure(proc) or f"winetricks exited with {code}")
        _note_applied(rec, rest)
    if direct:
        apply_settings(rec, preference, direct)
        _note_applied(rec, direct)
        refresh_script(rec, preference)


def reset_settings(rec: InstalledGame, preference: str, entries: list[str]) -> None:
    """Take settings the client writes itself (`vd=1280x1024`, see `winetricks.registry_reset`) out of the game's prefix: their
    registry values are deleted, so Wine's defaults hold again, and they are no longer noted as applied."""
    changes = [change for entry in entries for change in winetricks.registry_reset(entry) or []]
    if changes:
        path = _winecfg_dir() / f"{rec.game_id}.winetricks.reg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(winecfg._reg_of(changes))
        _import_reg(rec, preference, path)
    language = any(winetricks.key_of(entry) == "locale" for entry in entries)
    if language:
        restore_code_pages(rec, preference)
    manager._update(
        rec,
        winetricks=[v for v in rec.winetricks if v not in entries],
        winetricks_failed=[v for v in rec.winetricks_failed if v not in entries],
    )
    if language:
        refresh_script(rec, preference)


def refresh_script(rec: InstalledGame, preference: str) -> None:
    """The game's launch script written again: it starts the game with the environment a system language gives it (see
    `launcher.launch_command`), so it follows when that changes, for the desktop entry and the Steam shortcut that run it."""
    if sys.platform == "win32" or rec.native or rec.state != "installed" or not rec.executable:
        return
    try:
        launcher.write_launch_script(rec, preference)
    except (RuntimeError, OSError) as e:
        logstore.warning(f"Could not write the launch script of {rec.name} again: {e}")


def _code_pages_path(rec: InstalledGame) -> Path:
    return _winecfg_dir() / f"{rec.game_id}.codepages.reg"


def remember_code_pages(rec: InstalledGame) -> None:
    """Keep the prefix's code pages (ACP and OEMCP) as they are before a system language changes them, once: the first
    language set is what they go back to. Nothing is kept for a prefix with no registry yet."""
    path = _code_pages_path(rec)
    if path.is_file():
        return
    system_reg = next((c for c in (prefix_of(rec) / "system.reg", prefix_of(rec) / "pfx" / "system.reg") if c.is_file()), None)
    if system_reg is None:
        return
    lines = winecfg.section_values(system_reg.read_text(errors="replace"), "System\\\\CurrentControlSet\\\\Control\\\\Nls\\\\CodePage", ("acp", "oemcp"))
    if lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(winecfg._reg_of([(winetricks.CODEPAGE_KEY, line) for line in lines]))


def restore_code_pages(rec: InstalledGame, preference: str) -> None:
    """Put the code pages back as `remember_code_pages` kept them."""
    path = _code_pages_path(rec)
    if path.is_file():
        _import_reg(rec, preference, path)
        path.unlink(missing_ok=True)


def _drive_c(prefix: Path) -> Path | None:
    """The prefix's C: drive: in the prefix itself for Wine, one level down (`pfx/pfx`) for Proton."""
    return next((c for c in (prefix / "drive_c", prefix / "pfx" / "drive_c") if c.is_dir()), None)


def _copy_into(src: Path, dst: Path) -> None:
    """`src` copied into `dst` over what `dst` has with the same name. A link is copied as a link and never followed, and a
    link `dst` already has is left alone: Wine points Documents and the like to the home folder, where the files already are."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if target.is_symlink():
            continue
        if item.is_symlink() or not item.is_dir():
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if item.is_symlink():
                os.symlink(os.readlink(item), target)
            else:
                shutil.copy2(item, target)
        else:
            if target.exists() and not target.is_dir():
                target.unlink()
            _copy_into(item, target)


def _make_prefix(rec: InstalledGame, preference: str) -> None:
    """Have the game's engine make its prefix, empty, as it does the first time anything runs in it."""
    proc = launcher.start_tool(rec, preference, "wineboot", ("-u",))
    code = proc.wait(timeout=600)
    # A launcher such as Faugus's may hand over and return at once: wait for wineboot itself, if it is still there.
    watcher.wait_for_tool("wineboot", prefix_of(rec), appear_timeout=15.0, linger=3.0)
    if code != 0 or _drive_c(prefix_of(rec)) is None:
        raise RuntimeError(launcher.launch_failure(proc) or "the engine did not make a new prefix")


def rebuild_prefix(
    rec: InstalledGame, preference: str, keep: list[str], winecfg_reg: str | None = None,
    say: Callable[[str], None] = lambda _m: None,
) -> None:
    """Make the game's prefix again with only `keep` in it: what winetricks installed cannot be taken out any other way.
    The old prefix is set aside, a new one is made, the old one's `drive_c/users` (where most saves are) is copied into it,
    and only then is the old one deleted; when the new one cannot be made, the old one is put back. Then `keep` and the
    winecfg settings `winecfg_reg` go into the new one (without them, the next start applies the server's). RuntimeError
    when something fails."""
    prefix = prefix_of(rec)
    old = prefix.with_name(launcher.OLD_PREFIX_DIR)
    if old.exists() or old.is_symlink():
        raise RuntimeError(f"{old} is left from a rebuild that did not finish: take what you need out of it, delete it, then try again")
    if prefix.exists():
        prefix.rename(old)
    say(f"Making a new prefix for {rec.name}")
    try:
        _make_prefix(rec, preference)
        old_drive = _drive_c(old) if old.exists() else None
        if old_drive is not None and (old_drive / "users").is_dir():
            _copy_into(old_drive / "users", _drive_c(prefix) / "users")
    except BaseException:
        shutil.rmtree(prefix, ignore_errors=True)
        if old.exists():
            old.rename(prefix)
        raise
    shutil.rmtree(old, ignore_errors=True)
    if old.exists():
        say(f"The old prefix of {rec.name} could not be deleted completely: {old} can be deleted by hand")
    undo_path(rec).unlink(missing_ok=True)  # it describes the old prefix
    manager._update(rec, winetricks=[], winetricks_failed=[], winecfg_applied=None)
    _code_pages_path(rec).unlink(missing_ok=True)  # the new prefix has its own
    todo = winetricks.clean(keep)
    if todo:
        say(f"Installing {', '.join(todo)} into the new prefix of {rec.name}")
        try:
            run_winetricks(rec, preference, todo)
        except RuntimeError:
            manager._update(rec, winetricks_failed=list(dict.fromkeys([*rec.winetricks_failed, *todo])))
            raise
    reg = winecfg.clean_import(winecfg_reg or "")
    if reg:
        apply_reg(rec, preference, reg)
        manager._update(rec, winecfg_applied=digest(reg))


CLEARED = {"proton": None, "winecfg_reg": None, "winetricks": None, "launch_args": None}


def custom_settings(rec: InstalledGame, answer: dict | None) -> list[str]:
    """What the game has set beyond its files and saves, here and on the server (`answer`), one line each, to show before
    `clear_custom` takes it all away."""
    wanted = effective(answer)
    lines = []
    for tag in dict.fromkeys([*rec.launch_args, *(wanted["launch_args"] or [])]):
        lines.append(f"Launch argument: {tag}")
    if rec.launcher and rec.launcher != "auto":
        lines.append(f"Engine: {launcher.launcher_label(rec.launcher)}")
    for proton in dict.fromkeys(p for p in (rec.proton, wanted["proton"]) if p):
        lines.append(f"Proton: {proton}")
    if rec.wine:
        lines.append(f"Wine: {rec.wine}")
    for entry in winetricks.clean([*rec.winetricks, *wanted["winetricks"]]):
        lines.append(f"Winetricks: {entry}")
    for value in winecfg.describe(winecfg_to_keep(rec.game_id, answer) or ""):
        lines.append(f"Registry (winecfg): {value}")
    return lines


def clear_custom(rec: InstalledGame, preference: str, rows: list[str], say: Callable[[str], None] = lambda _m: None) -> None:
    """Take away all the game has set beyond its files and saves: the launch arguments, the engine and its Proton or Wine
    build, and the prefix (made again by `rebuild_prefix` with nothing in it: no winetricks verb or setting, no winecfg
    setting, nothing an installer left; `drive_c/users` is kept). The server's `rows` ("shared", "device") that hold
    something are emptied too, through the outbox and before anything else, so a start meanwhile does not bring it back."""
    for row in rows:
        queue(rec.game_id, dict(CLEARED), everywhere=row == "shared")
    if rec.launch_args:
        manager.set_launch_args(rec, [], preference)  # the next look at the server takes them off its list too (see _sync_args)
    if rec.launcher and rec.launcher != "auto":
        manager.set_launcher(rec, "auto", preference)
    if rec.proton:
        manager.set_proton(rec, None, preference)
    if rec.wine:
        manager.set_wine(rec, None, preference)
    language = winetricks.locale_of(rec.winetricks) is not None
    if not rec.native and prefix_of(rec).exists():
        rebuild_prefix(rec, preference, [], None, say)
    else:
        undo_path(rec).unlink(missing_ok=True)
        _code_pages_path(rec).unlink(missing_ok=True)
        manager._update(rec, winetricks=[], winetricks_failed=[], winecfg_applied=None)
    if language:  # the script started the game with it
        refresh_script(rec, preference)


def _note_applied(rec: InstalledGame, entries: list[str]) -> None:
    """In the prefix now: a setting's new value takes the old one's place, and nothing in it has failed any more."""
    manager._update(
        rec,
        winetricks=winetricks.merge(rec.winetricks, entries),
        winetricks_failed=[v for v in rec.winetricks_failed if v not in entries],
    )


def run_winecfg(
    rec: InstalledGame, preference: str, sleep: Callable[[float], None] = time.sleep, wait=watcher.wait_for_tool,
    program: str = "winecfg",
) -> str:
    """Open winecfg (or `program`: regedit changes the same settings) in the game's prefix, wait for it to be closed, and
    return the settings it left (empty when there are none). The wait is for the program's own process: it is no .exe of
    the game's folder, which is all the game watcher looks for."""
    reg_file = winecfg.find_user_reg(prefix_of(rec))
    before = reg_file.stat().st_mtime if reg_file else 0.0
    settings_before = prefix_settings(rec)
    proc = launcher.start_tool(rec, preference, program)
    if not wait(program, prefix_of(rec), abort_before_start=lambda: proc.poll() not in (None, 0)):
        raise RuntimeError(launcher.launch_failure(proc) or f"{program} did not start")
    waited = 0.0
    while waited < WRITE_WAIT:  # Wine writes the registry when its last process ends
        reg_file = winecfg.find_user_reg(prefix_of(rec))
        if reg_file and reg_file.stat().st_mtime > before:
            break
        sleep(0.5)
        waited += 0.5
    reg_file = winecfg.find_user_reg(prefix_of(rec))
    if reg_file is None:
        return ""
    settings = winecfg.export_settings(reg_file.read_text(errors="replace"))
    if settings_before is not None:
        remember_undo(rec, winecfg.undo_text(settings_before, settings))
    return settings
