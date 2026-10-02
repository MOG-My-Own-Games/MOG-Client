"""Executable discovery, launch commands and desktop entries for installed games."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mog_client.config import InstalledGame, data_dir

# Redistributables, helpers and uninstallers: never the game's own executable.
_NOT_A_GAME = re.compile(
    r"(redist|directx|dxsetup|dxwebsetup|vcredist|vc_redist|dotnet|ndp\d|oalinst|physx|xnafx|"
    r"unins\d*|uninstall|crashpad|crashhandler|crashreport|unitycrash|setup_|installer|"
    r"cleanup|launcherhelper|vulkan|dxcpl|dosbox|7z|notification_helper)",
    re.IGNORECASE,
)
_REDIST_DIRS = re.compile(r"(redist|directx|dotnet|vcredist|_commonredist|__installer|support)", re.IGNORECASE)


def list_executables(install_dir: Path) -> list[Path]:
    """Candidate game executables under install_dir, largest first (the game
    binary is nearly always the biggest .exe that isn't a redistributable)."""
    found = []
    for path in install_dir.rglob("*"):
        if path.suffix.lower() != ".exe" or not path.is_file():
            continue
        rel = path.relative_to(install_dir)
        if _NOT_A_GAME.search(path.stem) or any(_REDIST_DIRS.search(p) for p in rel.parts[:-1]):
            continue
        found.append(path)
    found.sort(key=lambda p: p.stat().st_size, reverse=True)
    return found


FAUGUS_FLATPAK = "io.github.Faugus.faugus-launcher"
LAUNCHER_LABELS = {
    "faugus": "Faugus Launcher",  # see launcher_label for the Flatpak variant
    "umu": "umu-launcher",
    "proton": "Proton (system)",
    "wine": "Wine (system)",
    "native": "Run directly",
}


def _flatpak_has(app_id: str) -> bool:
    if not shutil.which("flatpak"):
        return False
    return subprocess.run(["flatpak", "info", app_id], capture_output=True).returncode == 0


def faugus_invocation() -> tuple[list[str], str, bool] | None:
    """(runner command, umu-run path to put in the game command, is_flatpak)
    for the installed Faugus, preferring a native install over the Flatpak.

    The runner takes a whole shell-style command line (env vars + umu-run +
    exe), the same one Faugus builds for its own games."""
    if shutil.which("faugus-run"):
        umu = Path.home() / ".local/share/faugus-launcher/umu-run"
        return ["faugus-run"], str(umu if umu.is_file() else shutil.which("umu-run") or umu), False
    if _flatpak_has(FAUGUS_FLATPAK):
        umu = Path.home() / ".var/app" / FAUGUS_FLATPAK / "data/faugus-launcher/umu-run"
        runner = ["flatpak", "run", "--command=/app/bin/faugus-launcher", FAUGUS_FLATPAK, "--run"]
        return runner, str(umu), True
    return None


def faugus_command() -> list[str] | None:
    inv = faugus_invocation()
    return inv[0] if inv else None


def launcher_label(name: str) -> str:
    if name == "faugus":
        inv = faugus_invocation()
        return "Faugus (Flatpak)" if inv and inv[2] else "Faugus Launcher"
    return LAUNCHER_LABELS[name]


def find_proton() -> Path | None:
    """Newest Proton found in Steam's library or compatibilitytools.d."""
    roots = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    found = []
    for root in roots:
        for pattern in ("steamapps/common/Proton*/proton", "compatibilitytools.d/*/proton"):
            found += [p for p in root.glob(pattern) if p.is_file()]
    return max(found, key=lambda p: p.parent.name, default=None)


def available_launchers() -> list[str]:
    """Launchers usable on this machine, in default-preference order."""
    if sys.platform == "win32":
        return ["native"]
    found = []
    if faugus_command():
        found.append("faugus")
    if shutil.which("umu-run"):
        found.append("umu")
    if find_proton():
        found.append("proton")
    if shutil.which("wine"):
        found.append("wine")
    return found


def detect_launcher(preference: str = "auto") -> str | None:
    """The launcher to use: the preference if usable, else Faugus when
    installed, else the first system one found."""
    available = available_launchers()
    if preference in available:
        return preference
    return available[0] if available else None


def prefix_for(game: InstalledGame) -> Path:
    return data_dir() / "prefixes" / str(game.game_id)


def launch_command(game: InstalledGame, preference: str = "auto") -> tuple[list[str], dict[str, str]]:
    """Command + extra environment that runs the game's chosen executable."""
    if not game.executable:
        raise RuntimeError("no executable chosen for this game")
    launcher = detect_launcher(preference)
    if launcher is None:
        raise RuntimeError("no launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine")
    if launcher == "native":
        return [game.executable], {}
    prefix = game.prefix or str(prefix_for(game))
    Path(prefix).mkdir(parents=True, exist_ok=True)
    env = {"WINEPREFIX": prefix, "GAMEID": f"umu-mog-{game.game_id}", "PROTONPATH": "GE-Proton"}
    if launcher == "wine":
        return ["wine", game.executable], {"WINEPREFIX": prefix}
    if launcher == "proton":
        proton = find_proton()
        compat = Path(prefix) / "pfx-data"
        compat.mkdir(parents=True, exist_ok=True)
        steam_root = next(
            str(p.parent) for p in proton.parents if p.name in ("steamapps", "compatibilitytools.d")
        )
        return [str(proton), "run", game.executable], {
            "STEAM_COMPAT_DATA_PATH": str(compat),
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": steam_root,
        }
    if launcher == "faugus":
        runner, umu_path, _ = faugus_invocation()
        if not Path(umu_path).is_file() and not shutil.which("umu-run"):
            raise RuntimeError("open Faugus once so it can download umu-run, then try again")
        # No PROTONPATH: Faugus picks the Proton build itself.
        inline = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items() if k != "PROTONPATH")
        return [*runner, f"{inline} {shlex.quote(umu_path)} {shlex.quote(game.executable)}"], {}
    return ["umu-run", game.executable], env


def launch(game: InstalledGame, preference: str = "auto") -> subprocess.Popen:
    cmd, extra = launch_command(game, preference)
    return subprocess.Popen(cmd, cwd=str(Path(game.executable).parent), env={**os.environ, **extra})


def client_command() -> str:
    """Absolute path that re-launches this client, used as the Exec of shortcuts.

    A packaged build must point at the file the user keeps (the AppImage or
    the exe), not at the temporary directory it unpacks itself into."""
    if os.environ.get("APPIMAGE"):
        return os.environ["APPIMAGE"]
    if getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("mog") or str(Path(sys.argv[0]).resolve())


def desktop_entries_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/MOG"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"


def create_desktop_entry(game: InstalledGame, icon: Path | None = None) -> str | None:
    """Freedesktop entry that launches through the client. Returns its path
    (None on Windows, which has no .desktop files)."""
    if sys.platform == "win32":
        return None
    d = desktop_entries_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"mog-{game.game_id}.desktop"
    name = game.name.replace("\n", " ")
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        f"Exec={shlex.quote(client_command())} --launch {game.game_id}",
        "Categories=Game;",
        "Terminal=false",
    ]
    if icon:
        lines.append(f"Icon={icon}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return str(path)
