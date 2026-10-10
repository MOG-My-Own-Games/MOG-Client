"""Executable discovery, launch commands and desktop entries for installed games."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from mog_client.api import safe_dirname
from mog_client.config import InstalledGame, config_dir, data_dir

# Redistributables, helpers and uninstallers: never the game's own executable.
_NOT_A_GAME = re.compile(
    r"(redist|directx|dxsetup|dxwebsetup|vcredist|vc_redist|dotnet|ndp\d|oalinst|physx|xnafx|"
    r"unins\d*|uninstall|crashpad|crashhandler|crashreport|unitycrash|setup_|installer|"
    r"cleanup|launcherhelper|vulkan|dxcpl|dosbox|7z|notification_helper)",
    re.IGNORECASE,
)
_REDIST_DIRS = re.compile(r"(redist|directx|dotnet|vcredist|_commonredist|__installer|support)", re.IGNORECASE)


PREFIX_DIR = "pfx"  # the prefix folder inside a game's folder (see pfx_dir)


# What a GOG Linux installer leaves beside the game that is not the game: its support and uninstall scripts and
# the bundled dialog tool (yad). "preuninst" and "reuninst" are the same script as it is spelled in the wild.
_NOT_A_SCRIPT = re.compile(r"^(gog-system-report|postinst|preuninst|reuninst|yad|uninstall-.*)\.sh$", re.IGNORECASE)
_INSTALLER_DATA_DIRS = (".mojosetup",)


def _is_game_script(rel: Path) -> bool:
    return (
        rel.suffix.lower() == ".sh"
        and not _NOT_A_SCRIPT.match(rel.name)
        and not any(_REDIST_DIRS.search(p) or p in _INSTALLER_DATA_DIRS for p in rel.parts[:-1])
    )


def list_executables(install_dir: Path) -> list[Path]:
    """Candidate game executables under install_dir, the ones that run natively first: on Linux the game's own start
    script (the `start.sh` GOG's Linux games have at the top, then any other script, shallowest first) comes before
    every .exe, the biggest first (the game binary is nearly always the biggest one that isn't a redistributable).
    Scripts are Linux's way to start a game and are not offered on Windows, where the .exe files are all there is."""
    found = []
    for dirpath, dirnames, filenames in os.walk(install_dir):
        here = Path(dirpath)
        if here == install_dir:
            dirnames[:] = [d for d in dirnames if d != PREFIX_DIR]  # the prefix is full of Windows executables
        dirnames[:] = [d for d in dirnames if d not in _INSTALLER_DATA_DIRS]
        for name in filenames:
            path = here / name
            if not path.is_file():
                continue
            rel = path.relative_to(install_dir)
            if path.suffix.lower() == ".exe":
                if _NOT_A_GAME.search(path.stem) or any(_REDIST_DIRS.search(p) for p in rel.parts[:-1]):
                    continue
            elif sys.platform == "win32" or not _is_game_script(rel):
                continue
            found.append(path)

    def rank(path: Path) -> tuple[int, int, int]:
        rel = path.relative_to(install_dir)
        if path.suffix.lower() == ".exe":
            return 2, 0, -path.stat().st_size
        if rel.name.lower() == "start.sh" and len(rel.parts) == 1:
            return 0, 0, 0
        return 1, len(rel.parts), 0

    found.sort(key=lambda p: (*rank(p), p.name.lower()))
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


# Faugus first, then umu, Proton and Wine. PortProton (between Faugus and umu in the intended
# order) is not supported yet: it picks its own prefix by name and ignores a given path.
LAUNCHER_PRIORITY = ("faugus", "umu", "proton", "wine")

_known: list[str] | None = None  # the saved scan, once loaded (see ensure_scanned)


def scan_launchers() -> list[str]:
    """The launchers present on this machine, in order of preference."""
    if sys.platform == "win32":
        return ["native"]
    present = {
        "faugus": bool(faugus_command()),
        "umu": bool(shutil.which("umu-run")),
        "proton": find_proton() is not None,
        "wine": bool(shutil.which("wine")),
    }
    return [name for name in LAUNCHER_PRIORITY if present[name]]


def ensure_scanned(settings, rescan: bool = False) -> list[str]:
    """Put the saved list of launchers in use, scanning (and saving) first when none is saved, MOG
    was updated since, or `rescan` asks for it."""
    from mog_client.config import save_settings
    from mog_client.version import __version__

    global _known
    if rescan or not settings.launchers or settings.launchers_scanned_for != __version__:
        found = scan_launchers()
        if (found, __version__) != (settings.launchers, settings.launchers_scanned_for):
            settings.launchers, settings.launchers_scanned_for = found, __version__
            save_settings(settings)
    _known = list(settings.launchers)
    return _known


def available_launchers() -> list[str]:
    """Launchers usable on this machine, in default-preference order."""
    return list(_known) if _known is not None else scan_launchers()


def detect_launcher(preference: str = "auto") -> str | None:
    """The launcher to use: the preference if usable, else the first one found."""
    available = available_launchers()
    if preference in available:
        return preference
    return available[0] if available else None


def effective_launcher(game: InstalledGame, preference: str = "auto") -> str:
    """The game's own engine if it has one, else the one chosen in Settings."""
    return game.launcher if game.launcher and game.launcher != "auto" else preference


def pfx_dir(game: InstalledGame) -> Path:
    """The prefix MOG hands every engine: a `pfx` folder inside the game's own folder. What goes in
    it, and how, is up to the engine."""
    return Path(game.install_dir) / PREFIX_DIR


def ensure_executable(path: str) -> None:
    """Give a script the executable bit it may have lost on the way (a download keeps no modes)."""
    try:
        mode = Path(path).stat().st_mode
        if not mode & 0o100:
            Path(path).chmod(mode | 0o755)
    except OSError:
        pass  # the launch itself will say so


PERMISSIONS_MARK = ".mog-permissions"  # in a game's folder once its programs have been given back their executable bit
_SHARED_LIBRARY = re.compile(r"\.so(\.\d+)*$", re.IGNORECASE)


def fix_native_permissions(install_dir: Path) -> int:
    """Give back the executable bit a download does not keep: to every program (an ELF file) and script (a `#!` file)
    under a Linux game's folder, whose start script runs them. Libraries need none. Returns how many were changed."""
    changed = 0
    for dirpath, dirnames, filenames in os.walk(install_dir):
        if Path(dirpath) == install_dir:
            dirnames[:] = [d for d in dirnames if d != PREFIX_DIR]
        for name in filenames:
            if _SHARED_LIBRARY.search(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                info = os.lstat(path)
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o100:
                    continue
                with open(path, "rb") as f:
                    head = f.read(4)
                if head == b"\x7fELF" or head[:2] == b"#!":
                    os.chmod(path, stat.S_IMODE(info.st_mode) | 0o755)
                    changed += 1
            except OSError:
                continue
    return changed


def ensure_native_permissions(install_dir: str) -> None:
    """Once per game folder: see to its programs' executable bits (see `fix_native_permissions`)."""
    mark = Path(install_dir) / PERMISSIONS_MARK
    if mark.exists() or not Path(install_dir).is_dir():
        return
    fix_native_permissions(Path(install_dir))
    try:
        mark.write_text("")
    except OSError:
        pass  # it is looked at again next time


def launch_command(
    game: InstalledGame, preference: str = "auto", require_umu: bool = True
) -> tuple[list[str], dict[str, str]]:
    """Command + extra environment that runs the game's chosen executable."""
    if not game.executable:
        raise RuntimeError("no executable chosen for this game")
    if game.native:
        ensure_executable(game.executable)
        ensure_native_permissions(game.install_dir)  # the start script runs programs of its own, which a download left unmarked
        return [game.executable], {}  # a Linux program: no launcher, no prefix
    launcher = detect_launcher(effective_launcher(game, preference))
    if launcher is None:
        raise RuntimeError("no launcher found: install Faugus (Flatpak), umu-launcher, Proton or Wine")
    if launcher == "native":
        return [game.executable], {}
    prefix = pfx_dir(game)
    prefix.mkdir(parents=True, exist_ok=True)
    env = {"WINEPREFIX": str(prefix), "GAMEID": f"umu-mog-{game.game_id}"}
    if launcher == "wine":
        return ["wine", game.executable], {"WINEPREFIX": str(prefix)}
    if launcher == "proton":
        proton = find_proton()
        steam_root = next(
            str(p.parent) for p in proton.parents if p.name in ("steamapps", "compatibilitytools.d")
        )
        return [str(proton), "run", game.executable], {
            "STEAM_COMPAT_DATA_PATH": str(prefix),
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


def _bundle_roots() -> tuple[str, ...]:
    """Where this build's own libraries live: the PyInstaller directory, the AppImage mount and any other
    AppImage mount (an earlier instance of this client, before an update restarted it)."""
    roots = [os.environ.get("APPDIR"), getattr(sys, "_MEIPASS", None)]
    if getattr(sys, "frozen", False):
        roots.append(str(Path(sys.executable).resolve().parent))
    return tuple(r.rstrip("/") for r in roots if r)


def _outside_bundle(path_list: str) -> str:
    roots = _bundle_roots()
    keep = [
        p
        for p in path_list.split(":")
        if p and not re.match(r"/tmp/\.mount_[^/]+(/|$)", p) and not any(p == r or p.startswith(r + "/") for r in roots)
    ]
    return ":".join(keep)


def host_environ() -> dict[str, str]:
    """This process's environment as the host system's programs should see it."""
    env = dict(os.environ)
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        env["LD_LIBRARY_PATH"] = original
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    # A restart after an update hands the old bundle's directories on as the "original" path.
    if env.get("LD_LIBRARY_PATH"):
        env["LD_LIBRARY_PATH"] = _outside_bundle(env["LD_LIBRARY_PATH"])
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


def client_pointer_path() -> Path:
    return config_dir() / "client-path"


def remember_client() -> None:
    """Write where this client is into a file the launch scripts read, so moving or renaming the
    AppImage or exe only needs one start from the new place."""
    command = client_command()
    path = client_pointer_path()
    try:
        if not path.is_file() or path.read_text().strip() != command:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(command + "\n")
    except OSError:
        pass  # the scripts fall back to the path they were written with


def standalone_command(game: InstalledGame, preference: str = "auto") -> list[str]:
    """The whole command that starts the game by itself, environment included (through `env`),
    for shortcuts that must work without MOG. Faugus fetches its own umu-run on first use, so
    that file need not exist yet."""
    cmd, extra = launch_command(game, preference, require_umu=False)
    resolved = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]
    if not extra:
        return resolved
    return [shutil.which("env") or "/usr/bin/env", *(f"{k}={v}" for k, v in extra.items()), *resolved]


def entry_stem(game: InstalledGame) -> str:
    return safe_dirname(game.name)


def _in_game_folder(game: InstalledGame, name: str) -> Path:
    return Path(game.install_dir) / name


def launch_script_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, f"{entry_stem(game)}.sh")


def desktop_file_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, f"{entry_stem(game)}.desktop")


def steam_import_path(game: InstalledGame) -> Path:
    """The `.desktop` file Steam is asked to import the game from (see steam.add_through_steam); a file of its own, so
    it is there whether or not the user wanted a desktop entry."""
    return _in_game_folder(game, ".mog-steam.desktop")


def shortcut_lnk_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, f"{entry_stem(game)}.lnk")


def directory_file_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, ".directory")


def icon_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, ".mog-icon")


def menu_entry_path(game: InstalledGame) -> Path:
    return desktop_entries_dir() / f"mog-{game.game_id}.desktop"


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
        'MOG=$(cat "${XDG_CONFIG_HOME:-$HOME/.config}/mog-client/client-path" 2>/dev/null)\n'
        f'[ -x "$MOG" ] || MOG={shlex.quote(client_command())}\n'
        '[ -x "$MOG" ] || MOG=$(command -v mog || command -v mog-client)\n'
        'if [ -x "$MOG" ]; then\n'
        f'  "$MOG" --save-pre {game.game_id} >/dev/null 2>&1\n'
        f'  "$MOG" --save-watch {game.game_id} >/dev/null 2>&1 &\n'
        "fi\n"
        f"exec {shlex.join(argv)}\n"
    )
    path.chmod(0o755)
    return str(path)


def launch_cmd_path(game: InstalledGame) -> Path:
    return _in_game_folder(game, f"{entry_stem(game)}.cmd")


def _cmd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("%", "%%") + '"'


def launch_cmd_text(game: InstalledGame, client: str) -> str:
    """The Windows twin of the launch script: restore, run the game and wait for it, then back the saves up
    (a batch file keeps running after the game, which a shell script that execs cannot). The client is looked up
    like the script does; without it the game just starts."""
    pointer = r"%APPDATA%\mog-client\client-path"
    exe = Path(game.executable)
    lines = [
        "@echo off",
        f"rem {game.name.replace(chr(10), ' ')}: written by MOG; it does not need MOG to run.",
        'set "MOG="',
        f'if exist "{pointer}" set /p MOG=<"{pointer}"',
        f'if not exist "%MOG%" set "MOG={client.replace("%", "%%")}"',
        'if not exist "%MOG%" set "MOG="',
        f"cd /d {_cmd_quote(exe.parent)}",
        f'if defined MOG "%MOG%" --save-pre {game.game_id} >nul 2>&1',
        f'start /wait "" {_cmd_quote(exe)}',
        f'if defined MOG "%MOG%" --save-sync {game.game_id} --window >nul 2>&1',
    ]
    return "\r\n".join(lines) + "\r\n"


def write_launch_cmd(game: InstalledGame) -> str:
    path = launch_cmd_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launch_cmd_text(game, client_command()).encode("utf-8"))
    return str(path)


def entry_command(game: InstalledGame, preference: str = "auto") -> list[str]:
    """What a desktop or Steam entry runs: the game's launch script (a batch file on Windows)."""
    if sys.platform == "win32":
        return [write_launch_cmd(game)]
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


def desktop_folder() -> Path:
    """The user's desktop folder: XDG's (`user-dirs.dirs`), else ~/Desktop."""
    if sys.platform != "win32":
        dirs = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
        try:
            for line in dirs.read_text().splitlines():
                if line.startswith("XDG_DESKTOP_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                    if value:
                        return Path(value)
        except OSError:
            pass
    return Path.home() / "Desktop"


def desktop_shortcut_path(game: InstalledGame) -> Path:
    suffix = ".lnk" if sys.platform == "win32" else ".desktop"
    return desktop_folder() / f"{entry_stem(game)}{suffix}"


def create_desktop_shortcut(game: InstalledGame) -> str:
    """A copy of the game's entry (the one next to its files; make that first) on the desktop. A copy and not a
    link: desktops only offer to launch a file that is really there, and some ask once to trust it."""
    source = shortcut_lnk_path(game) if sys.platform == "win32" else desktop_file_path(game)
    target = desktop_shortcut_path(game)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sys.platform != "win32":
        target.chmod(0o755)
        if gio := shutil.which("gio"):  # GNOME keeps launchers it was not told to trust from running
            subprocess.run([gio, "set", str(target), "metadata::trusted", "true"], capture_output=True, check=False)
    return str(target)


def write_desktop_file(path: Path, game: InstalledGame, command: list[str], icon: Path | None = None) -> None:
    """A `.desktop` file that starts the game with `command`."""
    icon = icon or (icon_path(game) if icon_path(game).is_file() else None)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={game.name.replace(chr(10), ' ')}",
        f"Exec={desktop_exec(command)}",
        f"Path={Path(game.executable).parent}",
        "Categories=Game;",
        "Terminal=false",
    ]
    if icon:
        lines.append(f"Icon={icon}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)


def create_desktop_entry(game: InstalledGame, icon: Path | None = None, preference: str = "auto") -> str | None:
    """The game's entry point next to its files: a `.desktop` that starts the game through its launch
    script (not through MOG), with a symlink to it in the applications menu; on Windows a `.lnk`
    to the game's batch file. Returns the path to record (the menu symlink, or the `.lnk`)."""
    if sys.platform == "win32":
        create_windows_shortcut(
            shortcut_lnk_path(game), write_launch_cmd(game), str(Path(game.executable).parent), icon=game.executable
        )
        return str(shortcut_lnk_path(game))
    desktop = desktop_file_path(game)
    write_desktop_file(desktop, game, entry_command(game, preference), icon)
    menu = menu_entry_path(game)
    menu.parent.mkdir(parents=True, exist_ok=True)
    if menu.is_symlink() or menu.exists():
        menu.unlink()
    menu.symlink_to(desktop)
    return str(menu)


def write_directory_file(game: InstalledGame) -> None:
    """Give the game's folder its icon in file managers that read `.directory` (KDE's); no icon, no file."""
    if sys.platform == "win32":
        return
    path, icon = directory_file_path(game), icon_path(game)
    if icon.is_file():
        path.write_text(f"[Desktop Entry]\nIcon={icon}\n")
    else:
        path.unlink(missing_ok=True)


def windows_shortcut_script(lnk: Path, target: str, workdir: str, icon: str | None = None) -> str:
    """The PowerShell that makes a `.lnk` (WScript.Shell is the only stock way to write one). The window is
    minimized: a batch file target would otherwise flash a console."""

    def quote(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    return ";".join(
        [
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({quote(lnk)})",
            f"$s.TargetPath={quote(target)}",
            f"$s.WorkingDirectory={quote(workdir)}",
            "$s.WindowStyle=7",
            *([f"$s.IconLocation={quote(icon + ',0')}"] if icon else []),
            "$s.Save()",
        ]
    )


def create_windows_shortcut(lnk: Path, target: str, workdir: str, icon: str | None = None) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", windows_shortcut_script(lnk, target, workdir, icon)],
            check=True,
            capture_output=True,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
        )
    except (OSError, subprocess.CalledProcessError) as e:
        raise RuntimeError(f"could not create the shortcut {lnk}: {e}") from e


def remove_entry_files(game: InstalledGame) -> None:
    """Delete what MOG generated for the game: its script, entries, folder icon and menu link."""
    for path in (
        launch_script_path(game),
        launch_cmd_path(game),
        desktop_file_path(game),
        steam_import_path(game),
        shortcut_lnk_path(game),
        directory_file_path(game),
        menu_entry_path(game),
        *([Path(game.desktop_entry)] if game.desktop_entry else []),
        *([Path(game.desktop_shortcut)] if game.desktop_shortcut else []),
    ):
        path.unlink(missing_ok=True)
