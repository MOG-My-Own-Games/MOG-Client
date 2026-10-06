"""Backing a game's saves up to the server and putting them back, for one game on this machine.

Nothing here asks the user anything: a function returns what it found (a prefix it could not
work out, folders it needs confirmed, a newer save from another machine) and the caller decides.
A restore never replaces a file without first copying it to a backup archive.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mog_client.api import MogClient
from mog_client.config import InstalledGame, Settings
from mog_client.saves import prefix as prefixes
from mog_client.saves.archive import build_archive, extract_archive, read_keys
from mog_client.saves.devices import DeviceRecord
from mog_client.saves.locations import (
    GAME_KEY,
    SHARED_DIRS,
    USER_KEY,
    USER_SUBDIRS,
    Candidate,
    install_candidates,
    profile_candidates,
    target_for_key,
)
from mog_client.saves.scan import attribute, sha256_of
from mog_client.saves.state import (
    FileInfo,
    SaveState,
    load_install_manifest,
    load_state,
    save_state,
    saves_dir,
)

# What an upload is for; the server shows it next to each version.
LAUNCH, QUIT, MANUAL, UNINSTALL, SYNC = "launch", "quit", "manual", "uninstall", "sync"

# Prefixes that belong to one game only, so everything in them can be attributed to it.
DEDICATED_SOURCES = (prefixes.FROM_GAME, prefixes.FROM_FAUGUS, prefixes.FROM_STEAM)


@dataclass
class Context:
    rec: InstalledGame
    settings: Settings
    client: MogClient
    device: DeviceRecord
    engine: str = "auto"


def enabled(rec: InstalledGame, settings: Settings) -> bool:
    if rec.native:
        return False  # saves are found through the Wine prefix, which a native game does not have
    return settings.sync_saves if rec.save_sync is None else rec.save_sync


@dataclass
class BackupResult:
    # "uploaded" | "unchanged" | "nothing" | "needs-prefix" | "needs-confirmation" | "duplicate"
    status: str
    version: dict | None = None
    changed: list[str] = field(default_factory=list)
    # For "needs-confirmation": the top-level folders found, for the user to say which are this game's.
    folders: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    # "restored" | "needs-prefix" | "conflict" (only_if_free, and something is already there)
    status: str
    restored: list[str] = field(default_factory=list)
    backup: Path | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_prefix(ctx: Context, state: SaveState) -> prefixes.Found | None:
    return prefixes.resolve_prefix(ctx.rec, ctx.engine, state.prefix, state.prefix_source)


def prefix_ready(ctx: Context, state: SaveState) -> bool:
    """The prefix is known and exists; one that is chosen but not made yet (the game never ran) is not."""
    return _drive_c(find_prefix(ctx, state)) is not None


def remember_prefix(game_id: int, prefix: Path, source: str) -> None:
    """Keep where the game's prefix is. A different prefix, or one the user just picked, makes what
    was tracked and confirmed before no longer apply."""
    state = load_state(game_id)
    if state.prefix != str(prefix) or source == prefixes.FROM_USER:
        state.tracked, state.includes = {}, None
    state.prefix, state.prefix_source = str(prefix), source
    save_state(game_id, state)


def _drive_c(found: prefixes.Found | None) -> Path | None:
    return prefixes.drive_c_of(found.prefix) if found else None


def _candidates(ctx: Context, drive_c: Path | None) -> list[Candidate]:
    found: list[Candidate] = []
    if drive_c is not None:
        found += list(profile_candidates(drive_c))
    found += list(install_candidates(Path(ctx.rec.install_dir), load_install_manifest(ctx.rec.game_id)))
    return found


def _folder_of(key: str) -> str:
    """The folder a key is shown and confirmed under: the game-sized folder inside a profile or
    shared root (`users/USER/Documents/Game`), or the root itself for a file directly in it."""
    roots = [f"{USER_KEY}/{sub}" for sub in USER_SUBDIRS] + list(SHARED_DIRS)
    for root in roots:
        if key.startswith(root + "/"):
            inside = key[len(root) + 1 :].split("/")
            return f"{root}/{inside[0]}" if len(inside) > 1 else root
    return key.split("/")[0]


def confirm_folders(game_id: int, folders: list[str]) -> None:
    """Record the folders the user said belong to this game (the prefix is shared with other programs)."""
    state = load_state(game_id)
    state.includes = sorted(set(folders))
    save_state(game_id, state)


def backup(
    ctx: Context, trigger: str, since_ns: int | None = None, force: bool = False, upload: bool = True
) -> BackupResult:
    """Upload this game's saves if they changed. `since_ns` is when the session began, when known.
    With `upload=False` nothing is sent or remembered: "changed" says only that something differs here."""
    rec = ctx.rec
    state = load_state(rec.game_id)
    found = find_prefix(ctx, state)
    drive_c = _drive_c(found)
    candidates = _candidates(ctx, drive_c)
    if found is None and not candidates:
        return BackupResult("needs-prefix")

    dedicated = found is not None and found.source in DEDICATED_SOURCES
    includes = state.includes

    def accept(key: str) -> bool:
        if key.startswith(f"{GAME_KEY}/") or dedicated:
            return True
        return includes is not None and any(key.startswith(p + "/") or key == p for p in includes)

    result = attribute(candidates, state.tracked, since_ns, accept)
    if result.unattributed and since_ns is None and not dedicated and includes is None:
        folders = sorted({_folder_of(k) for k in result.unattributed if not k.startswith(f"{GAME_KEY}/")})
        if folders:
            return BackupResult("needs-confirmation", folders=folders)

    if not upload and result.changed:
        return BackupResult("changed", changed=result.changed)

    state.tracked = result.tracked
    if found is not None and not state.prefix:
        state.prefix, state.prefix_source = str(found.prefix), found.source
    if not result.tracked:
        save_state(rec.game_id, state)
        return BackupResult("nothing")
    if not result.changed and not force:
        save_state(rec.game_id, state)
        return BackupResult("unchanged")

    by_key = {c.key: c.path for c in candidates}
    outgoing = saves_dir(rec.game_id) / "outgoing.zip"
    try:
        count = build_archive(((k, by_key[k]) for k in sorted(result.tracked) if k in by_key), outgoing)
        if not count:
            return BackupResult("nothing")
        uploaded = ctx.client.upload_save(rec.game_id, ctx.device.device_id, outgoing, trigger)
    finally:
        outgoing.unlink(missing_ok=True)
    version = uploaded["version"]
    state.last_version_id = version["id"]
    state.seen_version_id = max(state.seen_version_id or 0, version["id"])
    state.last_synced_at = _now()
    save_state(rec.game_id, state)
    return BackupResult("uploaded" if uploaded.get("created") else "duplicate", version, result.changed)


@dataclass
class StartupOutcome:
    # "uploaded" | "restored" | "conflict" | "setup" | "pending" | "unchanged"
    status: str
    from_device: str | None = None
    version: dict | None = None
    files: int = 0


def startup_check(ctx: Context) -> StartupOutcome:
    """What to do about a game's saves when the client starts: send what changed here, or take the newer
    version another machine left when nothing changed here (what it replaces is backed up first). When
    both sides changed it does neither and says so, and when the game's folders are not known yet it waits."""
    try:
        newer = newer_elsewhere(ctx)
    except RuntimeError:
        newer = None
    if newer is None:
        result = backup(ctx, SYNC)
        status = {"uploaded": "uploaded", "needs-prefix": "setup", "needs-confirmation": "setup"}.get(
            result.status, "unchanged"
        )
        return StartupOutcome(status, version=result.version)
    version, device = newer
    local = backup(ctx, SYNC, upload=False)
    if local.status in ("needs-prefix", "needs-confirmation"):
        return StartupOutcome("setup", device["name"], version)
    if local.status == "changed":
        return StartupOutcome("conflict", device["name"], version)
    restored = restore(ctx, version["id"])
    if restored.status == "restored":
        return StartupOutcome("restored", device["name"], version, len(restored.restored))
    return StartupOutcome("pending", device["name"], version)


def newest_version(saves: dict | None) -> tuple[dict, dict] | None:
    """(version, device) of the newest save from any device, from `list_saves`' answer."""
    best = None
    for entry in (saves or {}).get("devices", []):
        for version in entry["versions"]:
            if best is None or (version["created_at"], version["id"]) > (best[0]["created_at"], best[0]["id"]):
                best = (version, entry["device"])
    return best


def newer_elsewhere(ctx: Context) -> tuple[dict, dict] | None:
    """The newest save from another device that this machine has not applied or declined yet."""
    best = newest_version(ctx.client.list_saves(ctx.rec.game_id))
    if best is None:
        return None
    version, device = best
    state = load_state(ctx.rec.game_id)
    if device["id"] == ctx.device.device_id or version["id"] <= (state.seen_version_id or 0):
        return None
    return version, device


def decline(ctx: Context, version_id: int) -> None:
    """Remember that the user chose to keep the local saves over this version."""
    state = load_state(ctx.rec.game_id)
    state.seen_version_id = max(state.seen_version_id or 0, version_id)
    save_state(ctx.rec.game_id, state)


_restore_locks: dict[int, threading.Lock] = {}
_restore_locks_guard = threading.Lock()


def _restore_lock(game_id: int) -> threading.Lock:
    """One lock per game: overlapping restores would download and unpack the same archive path at once."""
    with _restore_locks_guard:
        return _restore_locks.setdefault(game_id, threading.Lock())


def restore(ctx: Context, version_id: int) -> RestoreResult:
    """Put a server version back on this machine, keeping what it replaces in a backup archive.
    When the archive holds profile files and the prefix is not known yet, it is kept to be applied
    once the prefix is found (see apply_pending)."""
    rec = ctx.rec
    with _restore_lock(rec.game_id):
        state = load_state(rec.game_id)
        archive = saves_dir(rec.game_id) / f"version-{version_id}.zip"
        ctx.client.download_save(version_id, archive)
        return _apply(ctx, state, archive, version_id)


def _apply(
    ctx: Context, state: SaveState, archive: Path, version_id: int | None, only_if_free: bool = False
) -> RestoreResult:
    rec = ctx.rec
    keys = read_keys(archive)
    drive_c = _drive_c(find_prefix(ctx, state))
    if drive_c is None and any(not k.startswith(f"{GAME_KEY}/") for k in keys):
        state.pending_restore = str(archive)
        if version_id is not None:  # downloaded and kept: it is not "newer" any more, it only waits
            state.seen_version_id = max(state.seen_version_id or 0, version_id)
        save_state(rec.game_id, state)
        return RestoreResult("needs-prefix")

    install_dir = Path(rec.install_dir)
    if only_if_free and any(target_for_key(k, drive_c, install_dir).exists() for k in keys):
        return RestoreResult("conflict")
    backup_zip = saves_dir(rec.game_id) / "backups" / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    written = extract_archive(archive, lambda key: target_for_key(key, drive_c, install_dir), backup=backup_zip)
    for key, path in written:
        try:
            stat = path.stat()
        except OSError:
            continue
        state.tracked[key] = FileInfo(stat.st_size, stat.st_mtime_ns, sha256_of(path))
    if version_id is not None:
        state.seen_version_id = max(state.seen_version_id or 0, version_id)
    state.pending_restore = None
    save_state(rec.game_id, state)
    archive.unlink(missing_ok=True)
    return RestoreResult("restored", [k for k, _ in written], backup_zip if backup_zip.exists() else None)


def apply_pending(ctx: Context, only_if_free: bool = False) -> RestoreResult | None:
    """Finish a restore that was waiting for the prefix, if it is known now. With `only_if_free`
    nothing is written when any of its files already exists (the game has made its own since)."""
    state = load_state(ctx.rec.game_id)
    if not state.pending_restore or not Path(state.pending_restore).is_file():
        return None
    result = _apply(ctx, state, Path(state.pending_restore), None, only_if_free)
    return result if result.status == "restored" else None


def versions_of(ctx: Context) -> list[tuple[dict, dict]]:
    """Every saved version of the game as (version, device), newest first."""
    saves = ctx.client.list_saves(ctx.rec.game_id) or {}
    rows = [(v, e["device"]) for e in saves.get("devices", []) for v in e["versions"]]
    return sorted(rows, key=lambda r: (r[0]["created_at"], r[0]["id"]), reverse=True)

