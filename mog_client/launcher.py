"""Executable discovery, launch commands and desktop entries for installed games."""

from __future__ import annotations

import functools
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
    "system": "System default (as a double click)",
    "faugus": "Faugus Launcher",  # see launcher_label for the Flatpak variant
    "umu": "umu-launcher",
    "proton": "Proton (system)",
    "wine": "Wine (system)",
    "native": "Run directly",
}


def _flatpak_has(app_id: str) -> bool:
    # Install directories first: cheap, and `flatpak info` can fail in a minimal environment.
    for root in (Path.home() / ".local/share/flatpak/app", Path("/var/lib/flatpak/app")):
        if (root / app_id).is_dir():
            return True
    if not shutil.which("flatpak"):
        return False
    return subprocess.run(["flatpak", "info", app_id], capture_output=True).returncode == 0


def faugus_invocation() -> tuple[list[str], str, bool] | None:
    """(runner command, umu-run path to put in the game command, is_flatpak)
    for the installed Faugus, preferring a native install over the Flatpak.

    The runner takes a whole shell-style command line (env vars + umu-run +
    exe), the same one Faugus builds for its own games."""
    # Faugus 2.x runs a command through `faugus-launcher --run`; older installs have a faugus-run binary.
    native = (["faugus-run"] if shutil.which("faugus-run") else None) or (
        ["faugus-launcher", "--run"] if shutil.which("faugus-launcher") else None
    )
    if native:
        umu = Path.home() / ".local/share/faugus-launcher/umu-run"
        return native, str(umu if umu.is_file() else shutil.which("umu-run") or umu), False
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


_EXE_MIME_TYPES = (
    "application/vnd.microsoft.portable-executable",
    "application/x-ms-dos-executable",
    "application/x-msdownload",
)


@functools.lru_cache(maxsize=1)
def exe_handler_registered() -> bool:
    """Whether the desktop has a default application for .exe files, i.e. something a double click runs."""
    gio = shutil.which("gio")
    if not gio:
        return False
    env = {**host_environ(), "LC_ALL": "C"}
    for mime in _EXE_MIME_TYPES:
        out = subprocess.run([gio, "mime", mime], capture_output=True, text=True, env=env).stdout
        if out.strip() and "No default" not in out:
            return True
    return False


def available_launchers() -> list[str]:
    """Launchers usable on this machine, in default-preference order."""
    if sys.platform == "win32":
        return ["native"]
    found = []
    # The user's own .exe handler is the engine that leaves the prefix choice to them.
    if exe_handler_registered():
        found.append("system")
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


def effective_launcher(game: InstalledGame, preference: str = "auto") -> str:
    """The game's own engine if it has one, else the one chosen in Settings."""
    return game.launcher if game.launcher and game.launcher != "auto" else preference


def prefix_for(game: InstalledGame) -> Path:
    """Where MOG keeps a prefix for a game whose engine cannot run without one (system Proton)."""
    return data_dir() / "prefixes" / str(game.game_id)


def open_command() -> list[str]:
    """The command that opens a file with the user's default application, without waiting for it."""
    gio = shutil.which("gio")
    return [gio, "open"] if gio else [shutil.which("xdg-open") or "xdg-open"]


def launch_command(
    game: InstalledGame, preference: str = "auto", require_umu: bool = True
) -> tuple[list[str], dict[str, str]]:
    """Command + extra environment that runs the game's chosen executable."""
    if not game.executable:
        raise RuntimeError("no executable chosen for this game")
    launcher = detect_launcher(effective_launcher(game, preference))
    if launcher is None:
        raise RuntimeError("no launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine")
    if launcher == "native":
        return [game.executable], {}
    if launcher == "system":
        # Returns once the handler has started, so a Steam shortcut cannot follow the game's lifetime.
        return [*open_command(), game.executable], {}
    # The prefix is the user's: only a path they chose for this game (game.prefix) is passed on,
    # otherwise each engine uses its own default, as when the exe is opened by hand.
    env = {"WINEPREFIX": game.prefix} if game.prefix else {}
    if launcher == "wine":
        return ["wine", game.executable], env
    if launcher == "proton":
        # Proton has no default prefix: it cannot run without STEAM_COMPAT_DATA_PATH.
        prefix = game.prefix or str(prefix_for(game))
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
        if require_umu and not Path(umu_path).is_file() and not shutil.which("umu-run"):
            raise RuntimeError("open Faugus once so it can download umu-run, then try again")
        # No PROTONPATH: Faugus picks the Proton build itself.
        inline = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
        return [*runner, f"{inline}{shlex.quote(umu_path)} {shlex.quote(game.executable)}"], {}
    return ["umu-run", game.executable], {**env, "PROTONPATH": "GE-Proton"}


# What a bundled build (PyInstaller, AppImage) puts in the environment for its own libraries and
# Python; a launcher started from here must not inherit it (flatpak and Faugus would load the
# bundled libraries instead of the system's and fail without a word).
_BUNDLE_ENV = (
    "PYTHONHOME",
    "PYTHONPATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "_MEIPASS2",
    "PYINSTALLER_RESET_ENVIRONMENT",
)


def host_environ() -> dict[str, str]:
    """This process's environment as the host system's programs should see it."""
    env = dict(os.environ)
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        env["LD_LIBRARY_PATH"] = original
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    if not env.get("LD_LIBRARY_PATH"):
        env.pop("LD_LIBRARY_PATH", None)
    for key in _BUNDLE_ENV:
        env.pop(key, None)
    return env


def launch_log_path(game: InstalledGame) -> Path:
    return data_dir() / "logs" / f"launch-{game.game_id}.log"


def launch(game: InstalledGame, preference: str = "auto") -> subprocess.Popen:
    """Start the game through its launcher, detached, with the launcher's output in a log file."""
    cmd, extra = launch_command(game, preference)
    log_path = launch_log_path(game)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        log.write(f"$ {shlex.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(game.executable).parent),
            env={**host_environ(), **extra},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    proc.log_path = log_path  # type: ignore[attr-defined]
    return proc


def launch_failure(proc: subprocess.Popen, lines: int = 3) -> str | None:
    """Why a launcher that already exited with an error gave up, from the end of its log; None while it runs or on a clean exit."""
    code = proc.poll()
    if code is None or code == 0:
        return None
    log_path = getattr(proc, "log_path", None)
    tail = ""
    if log_path and Path(log_path).is_file():
        text = [ln for ln in Path(log_path).read_text(errors="replace").splitlines() if ln.strip()]
        tail = " | ".join(text[1:][-lines:])  # the first line is the command itself
    where = f" (log: {log_path})" if log_path else ""
    return f"The launcher exited with code {code}: {tail or 'no output'}{where}"


def client_command() -> str:
    """Absolute path that re-launches this client, used as the Exec of shortcuts.

    A packaged build must point at the file the user keeps (the AppImage or
    the exe), not at the temporary directory it unpacks itself into."""
    if os.environ.get("APPIMAGE"):
        return os.environ["APPIMAGE"]
    if getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("mog") or str(Path(sys.argv[0]).resolve())


def standalone_command(game: InstalledGame, preference: str = "auto") -> list[str]:
    """The whole command that starts the game by itself, environment included (through `env`),
    for shortcuts that must work without MOG. Faugus fetches its own umu-run on first use, so
    that file need not exist yet."""
    cmd, extra = launch_command(game, preference, require_umu=False)
    resolved = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]
    if not extra:
        return resolved
    return [shutil.which("env") or "/usr/bin/env", *(f"{k}={v}" for k, v in extra.items()), *resolved]


def launch_script_path(game: InstalledGame) -> Path:
    return data_dir() / "launchers" / f"mog-{game.game_id}.sh"


def write_launch_script(game: InstalledGame, preference: str = "auto") -> str:
    """A small shell script holding the command that starts the game, and return its path.
    The desktop entry and the Steam shortcut both run this script (it execs the launcher, so
    Steam still tracks the game), so changing the game's engine means rewriting one file and
    touching neither of them. It needs nothing from MOG to run."""
    argv = standalone_command(game, preference)
    path = launch_script_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = detect_launcher(effective_launcher(game, preference)) or "?"
    # The save-sync hooks run only when the client is still there, and decide for themselves whether
    # this game syncs; the exec stays, so whatever started the script tracks the game.
    path.write_text(
        "#!/bin/sh\n"
        f"# {game.name.replace(chr(10), ' ')}: started through {engine}. Written by MOG and rewritten\n"
        "# when the game's launcher is changed there; it does not need MOG to run.\n"
        f"cd {shlex.quote(str(Path(game.executable).parent))} || exit 1\n"
        f"MOG={shlex.quote(client_command())}\n"
        'if [ -x "$MOG" ]; then\n'
        f'  "$MOG" --save-pre {game.game_id} >/dev/null 2>&1\n'
        f'  "$MOG" --save-watch {game.game_id} >/dev/null 2>&1 &\n'
        "fi\n"
        f"exec {shlex.join(argv)}\n"
    )
    path.chmod(0o755)
    return str(path)


def entry_command(game: InstalledGame, preference: str = "auto") -> list[str]:
    """What a desktop or Steam entry runs: the game's launch script, or the exe itself on Windows."""
    if sys.platform == "win32":
        return [game.executable]
    return [write_launch_script(game, preference)]


def desktop_exec(argv: list[str]) -> str:
    """argv as the Exec line of a desktop entry: arguments quoted per the Desktop Entry spec."""

    def quote(arg: str) -> str:
        arg = arg.replace("%", "%%")
        if arg and not re.search(r'[\s"\'\\><~|&;$*?#()`]', arg):
            return arg
        escaped = re.sub(r'(["`$\\])', r"\\\1", arg)
        return f'"{escaped}"'

    return " ".join(quote(a) for a in argv)


def desktop_entries_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/MOG"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"


def create_desktop_entry(game: InstalledGame, icon: Path | None = None, preference: str = "auto") -> str | None:
    """Freedesktop entry that starts the game through its launch script, not through MOG.
    Returns its path (None on Windows, which has no .desktop files)."""
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
        f"Exec={desktop_exec(entry_command(game, preference))}",
        f"Path={Path(game.executable).parent}",
        "Categories=Game;",
        "Terminal=false",
    ]
    if icon:
        lines.append(f"Icon={icon}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return str(path)
