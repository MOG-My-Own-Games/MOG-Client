"""The Proton builds on this device as something to manage: which MOG games use each, what it takes, and removing it (Qt-free).

Only a build in a `compatibilitytools.d` folder is removable: that is where Proton-GE and UMU-Proton are put, by MOG, Faugus,
umu or ProtonUp. The ones in Steam's own library belong to Steam, which updates and removes them itself. Stdlib only."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from mog_client import installdirs, launcher
from mog_client.config import InstalledGame

TOOLS_FOLDER = "compatibilitytools.d"


@dataclass(frozen=True)
class Build:
    name: str
    folder: Path
    removable: bool


def installed() -> list[Build]:
    """The builds on this device, newest name last (the order `launcher.list_proton_builds` gives)."""
    return [
        Build(name, script.parent, script.parent.parent.name == TOOLS_FOLDER)
        for name, script in launcher.list_proton_builds().items()
    ]


def used_by(name: str, games) -> list[InstalledGame]:
    """The MOG games set to run under the build `name`, by name."""
    return sorted((g for g in games if g.proton == name), key=lambda g: g.name.casefold())


def size(build: Build) -> int:
    return installdirs.folder_size(build.folder)


def remove(build: Build) -> None:
    """Delete the build's folder. Raises RuntimeError for a build that is not in a `compatibilitytools.d` or cannot be removed."""
    if not build.removable:
        raise RuntimeError(f"{build.name} belongs to Steam: remove it there")
    try:
        if build.folder.is_symlink():
            build.folder.unlink()  # only the link: what it points to is not ours
        else:
            shutil.rmtree(build.folder)
    except OSError as e:
        raise RuntimeError(f"could not remove {build.name}: {e}") from e
