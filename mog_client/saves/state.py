"""What this machine remembers about one game's saves between runs (a JSON file per game)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from mog_client.config import _read_json, _write_json, data_dir


@dataclass
class FileInfo:
    size: int
    mtime_ns: int
    sha256: str


@dataclass
class SaveState:
    # Where this game's Wine prefix is, once found or chosen, and how it was found.
    prefix: str | None = None
    prefix_source: str | None = None
    # The files attributed to this game, by archive key, as of the last sync.
    tracked: dict[str, FileInfo] = field(default_factory=dict)
    # For a prefix shared with other programs: the folders the user confirmed are this game's
    # (archive-key prefixes). None until asked.
    includes: list[str] | None = None
    last_version_id: int | None = None
    last_synced_at: str | None = None
    # Newest server version, from any device, this machine already handled (applied or declined).
    seen_version_id: int | None = None
    # A downloaded archive waiting for a prefix to restore into.
    pending_restore: str | None = None


def saves_dir(game_id: int) -> Path:
    return data_dir() / "saves" / str(game_id)


def state_path(game_id: int) -> Path:
    return saves_dir(game_id) / "state.json"


def load_state(game_id: int) -> SaveState:
    raw = _read_json(state_path(game_id), {})
    known = SaveState.__dataclass_fields__
    state = SaveState(**{k: v for k, v in raw.items() if k in known and k != "tracked"})
    state.tracked = {}
    for key, info in (raw.get("tracked") or {}).items():
        try:
            state.tracked[key] = FileInfo(**info)
        except TypeError:
            continue
    return state


def save_state(game_id: int, state: SaveState) -> None:
    _write_json(state_path(game_id), asdict(state))


def manifest_path(game_id: int) -> Path:
    return data_dir() / "manifests" / f"{game_id}.json"


def baseline_path(game_id: int) -> Path:
    return data_dir() / "manifests" / f"{game_id}.baseline.json"


def save_install_manifest(game_id: int, files: list[dict]) -> None:
    """Add what an installer produced ({path, size_bytes, sha1}) to what is kept of the game's own files, so
    they can later be told apart from what the game wrote. It adds to the list and never replaces it: a game's
    files come from every installer run into it (a patch, a DLC, a reinstall), and a later list that only
    names the new ones must not make the earlier ones look like saves. A file named again takes the new entry."""
    kept = load_install_manifest(game_id) or {}
    kept.update({e["path"]: (e["size_bytes"], e["sha1"]) for e in files})
    _write_json(manifest_path(game_id), {path: list(entry) for path, entry in kept.items()})


def load_install_manifest(game_id: int) -> dict[str, tuple[int, str]] | None:
    raw = _read_json(manifest_path(game_id), None)
    if not isinstance(raw, dict):
        return None
    return {path: (size, sha1) for path, (size, sha1) in raw.items()}


def forget_game(game_id: int) -> None:
    """Drop what is kept about a game that is no longer installed. The backups of files a restore
    replaced stay: they are the user's, and small."""
    for path in (state_path(game_id), manifest_path(game_id), baseline_path(game_id)):
        path.unlink(missing_ok=True)
