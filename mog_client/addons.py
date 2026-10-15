"""Mods, patches and DLC on a game installed here: running a layer of them on the game's folder, sending what it changed to
the server as an addon, and taking an addon the server kept from another install.

Every change goes through the install ledger (saves/installed.py), so its files are recorded in the game's manifest and
never taken for save files. Qt-free."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path

from mog_client.saves import installed as ledger
from mog_client.saves.installed import Snapshot
from mog_client.saves.state import baseline_path

ADDON_CATEGORIES = ("mod", "patch", "dlc")


class AddonError(RuntimeError):
    pass


def open_layer(game_id: int, install_dir: Path) -> Snapshot:
    """Start a layer: the folder as it is now, so what the layer changes can be told apart. Pair it with close_layer."""
    return ledger.begin(game_id, install_dir)


def layer_is_open(game_id: int) -> bool:
    """Whether a layer was started for the game and not ended: the ledger keeps its starting picture on disk, so this
    holds across restarts of the client."""
    return baseline_path(game_id).exists()


def close_layer(game_id: int, install_dir: Path, before: Snapshot) -> list[tuple[str, Path]]:
    """End a layer: record what it added or changed in the game's manifest (never as saves) and return those files as
    (path relative to the folder, file)."""
    now = ledger.snapshot(install_dir)
    changed = [
        (rel, install_dir / rel)
        for rel, signature in now.items()
        if before.get(rel) != signature
    ]
    ledger.finish(game_id, install_dir, before)
    return sorted(changed)


def zip_files(files: list[tuple[str, Path]], dest: Path) -> Path:
    """Zip the files under their relative paths, to send to the server as an addon."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for member, source in files:
            zf.write(source, member)
    return dest


def upload_layer(
    client, game_id: int, install_dir: Path, changed: list[tuple[str, Path]], category: str, name: str, scratch: Path
) -> dict:
    """Send the layer's files to the server as a mod, patch or DLC of the game. The zip is kept in `scratch` only as
    long as the upload takes."""
    if category not in ADDON_CATEGORIES:
        raise AddonError(f"Unknown kind of addon: {category}")
    if not changed:
        raise AddonError("The layer changed no files: nothing to send")
    archive = zip_files(changed, scratch / "layer.zip")
    try:
        return client.upload_addon(game_id, category, name, archive)
    finally:
        archive.unlink(missing_ok=True)


MAX_MEMBERS = 100000
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024 * 1024


def _member_path(name: str) -> Path:
    member = Path(name)
    drive = len(name) > 1 and name[1] == ":"  # `C:evil.dll` is under no folder of ours once joined on Windows
    if not name or "\0" in name or member.is_absolute() or ".." in member.parts or "\\" in name or drive:
        raise AddonError(f"Unsafe path in the addon: {name}")
    return member


def apply_addon(client, game_id: int, install_dir: Path, addon: dict, scratch: Path) -> int:
    """Download an addon the server kept and extract it over the game's folder, as a ledger layer. Returns how many
    files it wrote. The whole archive is checked before anything is written (the server is not trusted to have)."""
    archive = scratch / f"addon-{addon['id']}.zip"
    client.download_addon(game_id, addon["id"], archive)
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise AddonError("The addon is too large to apply")
            for info in infos:
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise AddonError(f"A link in the addon: {info.filename}")
            members = [info for info in infos if not info.is_dir()]
            targets = [(info, install_dir / _member_path(info.filename)) for info in members]
            before = open_layer(game_id, install_dir)
            for info, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        close_layer(game_id, install_dir, before)
    finally:
        archive.unlink(missing_ok=True)
    return len(targets)


# Installers that can run one after another in a session: an archive or a disc image is a source to unpack, not a step,
# but an executable found inside one is (it carries the `source_path` of the disc or archive it is in).
CHAINABLE_KINDS = ("known installer", "executable (top level)", "executable (nested)")
SOURCE_KINDS = ("disc image", "archive")
# "Just copy the files, run nothing" is a step like an installer, and the only one that can come before the patches: the
# game's own files, as they are, are the game, and the installers after it are what is added to it.
COPY_STEP = "mog:copy"
COPY_KIND = "copy"
COPY_LABEL = "Just copy the files, run nothing"
INSIDE_KIND = "executable (inside)"
# A descriptor next to a disc image (DISK1.MDS beside DISK1.ISO), not something to open.
_NOT_A_SOURCE = (".mds",)
MAX_SOURCES = 8  # how many discs and archives are looked inside, so a folder of parts does not keep the page waiting


def copy_entry() -> dict:
    """The copy as a row of the list of installers, the same for every version of a title."""
    return {"path": COPY_STEP, "kind": COPY_KIND, "category": "game", "file_size_bytes": 0, "source_path": None}


def is_copy(c: dict | None) -> bool:
    return c is not None and c.get("path") == COPY_STEP and c.get("kind") == COPY_KIND


def chainable(candidates: list[dict]) -> list[dict]:
    return [c for c in candidates if c.get("kind") in CHAINABLE_KINDS or c.get("kind") in (INSIDE_KIND, COPY_KIND)]


def sources(candidates: list[dict]) -> list[dict]:
    """The discs and archives worth looking inside for an installer."""
    found = [
        c for c in candidates if c.get("kind") in SOURCE_KINDS and not c["path"].lower().endswith(_NOT_A_SOURCE)
    ]
    return found[:MAX_SOURCES]


def inside_entries(source: dict, found: list[dict]) -> list[dict]:
    """The installers inside a disc or archive, as entries that remember where they are: `path` is inside it and
    `source_path` is the disc or archive of the game's own."""
    return [
        {
            "path": c["path"],
            "file_size_bytes": c.get("file_size_bytes", 0),
            "kind": INSIDE_KIND,
            "category": c.get("category", "game"),
            "source_path": source["path"],
        }
        for c in found
        if c.get("kind") in CHAINABLE_KINDS
    ]


def with_inside(candidates: list[dict], inside: dict[str, list[dict]]) -> list[dict]:
    """The candidates with, right after each disc or archive, the installers found inside it (`inside` is keyed by the
    disc's own path)."""
    out: list[dict] = []
    for c in candidates:
        out.append(c)
        out.extend(inside_entries(c, inside.get(c["path"], [])))
    return out


def label_of(c: dict) -> str:
    """How an installer is named in a list: its path, and for one inside a disc or archive, that disc's name first."""
    if is_copy(c):
        return COPY_LABEL
    source = c.get("source_path")
    return f"{source.rsplit('/', 1)[-1]} > {c['path']}" if source else c["path"]


def step_key(c: dict) -> tuple[str, str | None]:
    return c["path"], c.get("source_path")


def arrange(candidates: list[dict], saved_order: list[dict]) -> list[tuple[dict, bool]]:
    """The chainable installers as (candidate, checked): the ones in the server's saved order first, checked and in that
    order, then the rest unchecked. With no saved order that still matches, the first installer of the game is checked."""
    found = chainable(candidates)
    by_key = {step_key(c): c for c in found}
    saved = [by_key[(step["path"], step.get("source_path"))] for step in saved_order if (step.get("path"), step.get("source_path")) in by_key]
    if saved:
        rest = [c for c in found if c not in saved]
        return [(c, True) for c in saved] + [(c, False) for c in rest]
    return [(c, i == 0 and c.get("category", "game") == "game") for i, c in enumerate(found)]


def role_of(position: int) -> str:
    """The first installer run is the game's own; every one after it is a patch."""
    return "Game" if position == 0 else "Patch"


def steps_of(checked: list[dict]) -> list[dict]:
    return [{"path": c["path"], "source_path": c.get("source_path")} for c in checked]
