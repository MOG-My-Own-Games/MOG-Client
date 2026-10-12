"""How a game is launched, shared through the server: its Proton build and what winecfg changed in its prefix. Stdlib only.

The server keeps them per game, for all of its devices or for one; a device takes its own entry first and the shared one
for what that lacks."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

from mog_client import launcher, manager, protondl, winecfg, winetricks
from mog_client.api import MogClient
from mog_client import config
from mog_client.config import InstalledGame
from mog_client.launcher import detect_launcher, effective_launcher
from mog_client.saves import devices, watcher

WRITE_WAIT = 10.0  # seconds after winecfg ends that Wine is given to write its registry to disk


def digest(reg_text: str) -> str:
    return hashlib.sha1(reg_text.encode()).hexdigest()


def effective(answer: dict | None) -> dict:
    """{"proton", "winecfg_reg", "winetricks"} from the server's answer: the device's own value, else the shared one, else None;
    the winetricks verbs of both entries together (a device adds to what all of them have)."""
    out: dict = {"proton": None, "winecfg_reg": None}
    verbs: list = []
    for row in ((answer or {}).get("shared"), (answer or {}).get("device")):  # the device's last: it wins
        for field in out:
            if isinstance(row, dict) and row.get(field) is not None:
                out[field] = row[field]
        if isinstance(row, dict):
            verbs += winetricks.clean(row.get("winetricks"))
    out["winetricks"] = winetricks.clean(verbs)
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
    """Whether the game's Proton build is a Proton-GE release this device lacks and its engine can use."""
    name = rec.proton
    engine = detect_launcher(effective_launcher(rec, preference))
    return bool(name) and engine in ("umu", "faugus", "proton") and protondl.is_ge(name) and name not in launcher.list_proton_builds()


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


def pull(client: MogClient, rec: InstalledGame, preference: str, say: Callable[[str], None] = lambda _m: None) -> None:
    """Put what the server holds for the game on this machine: the Proton build, and the winecfg settings when they differ
    from the ones last applied. Nothing the server lacks is touched. Raises RuntimeError when applying the settings fails."""
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
    if wanted["proton"] and wanted["proton"] != rec.proton:
        manager.set_proton(rec, wanted["proton"], preference)
        say(f"{rec.name} now runs under {wanted['proton']}")
    ensure_build(rec, preference, say)
    todo = winetricks.missing(wanted["winetricks"], rec.winetricks)
    if todo:  # before the winecfg settings, which a verb may overwrite (DLL overrides)
        say(f"Installing {', '.join(todo)} into the prefix of {rec.name} with winetricks (the game starts when it is done)")
        run_winetricks(rec, preference, todo)
    reg = winecfg.clean_import(wanted["winecfg_reg"] or "")
    if reg and digest(reg) != rec.winecfg_applied:
        say(f"Applying the Wine settings of {rec.name} kept on the server")
        apply_reg(rec, preference, reg)
        manager._update(rec, winecfg_applied=digest(reg))


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


def _prefix_settings(rec: InstalledGame) -> str:
    reg_file = winecfg.find_user_reg(prefix_of(rec))
    return winecfg.export_settings(reg_file.read_text(errors="replace")) if reg_file else ""


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
    remember_undo(rec, winecfg.undo_text(_prefix_settings(rec), reg))
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


def run_winetricks(rec: InstalledGame, preference: str, verbs: list[str]) -> None:
    """Install `verbs` into the game's prefix with winetricks, unattended, and note them as installed. Raises RuntimeError when
    winetricks fails; nothing is noted then, so the next try starts again."""
    verbs = winetricks.clean(verbs)
    if not verbs:
        return
    proc = launcher.start_tool(rec, preference, "winetricks", ("-q", *verbs))
    code = proc.wait()
    # A launcher such as Faugus's may hand over and return at once: wait for winetricks itself, if it is still there.
    watcher.wait_for_tool("winetricks", prefix_of(rec), appear_timeout=15.0, linger=5.0)
    if code != 0:
        raise RuntimeError(launcher.launch_failure(proc) or f"winetricks exited with {code}")
    manager._update(rec, winetricks=list(dict.fromkeys([*rec.winetricks, *verbs])))


def run_winecfg(
    rec: InstalledGame, preference: str, sleep: Callable[[float], None] = time.sleep, wait=watcher.wait_for_tool
) -> str:
    """Open winecfg in the game's prefix, wait for it to be closed, and return the settings it left (empty when there are none).
    The wait is for winecfg's own process: it is no .exe of the game's folder, which is all the game watcher looks for."""
    reg_file = winecfg.find_user_reg(prefix_of(rec))
    before = reg_file.stat().st_mtime if reg_file else 0.0
    settings_before = _prefix_settings(rec)
    proc = launcher.start_tool(rec, preference, "winecfg")
    if not wait("winecfg", prefix_of(rec), abort_before_start=lambda: proc.poll() not in (None, 0)):
        raise RuntimeError(launcher.launch_failure(proc) or "winecfg did not start")
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
    remember_undo(rec, winecfg.undo_text(settings_before, settings))
    return settings
