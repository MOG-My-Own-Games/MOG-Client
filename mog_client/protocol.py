"""The `mog://` links MOG-Server's web UI opens to hand a game to this client, and the registration that makes
the system send them here. Stdlib only.

    mog://install/<game id>?server=<origin of the page that made the link>
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

SCHEME = "mog"
DESKTOP_NAME = "mog-client-url.desktop"
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class Link:
    action: str  # "install"
    game_id: int
    server: str | None = None


def make_link(game_id: int, server: str | None = None) -> str:
    return f"{SCHEME}://install/{game_id}" + (f"?server={quote(server, safe='')}" if server else "")


def parse(url: str) -> Link | None:
    """The link a `mog://` URL stands for, or None when it is not one this client understands."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() != SCHEME or parts.netloc.lower() != "install":
        return None
    game = parts.path.strip("/")
    if not game.isdigit() or int(game) < 1:
        return None
    server = (parse_qs(parts.query).get("server") or [None])[0]
    return Link("install", int(game), server or None)


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url if "://" in url else f"http://{url}")
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if not parts.hostname:
        return None
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS.get(scheme)


def same_server(a: str | None, b: str | None) -> bool:
    """Whether two addresses name the same server, ignoring case, a default port and a trailing path."""
    left, right = _origin(a or ""), _origin(b or "")
    return left is not None and left == right


def _desktop_entry(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MOG Client\n"
        "Comment=Opens mog:// links from MOG-Server\n"
        f'Exec="{command}" %u\n'
        "NoDisplay=true\n"
        "Terminal=false\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n"
    )


def _register_linux(command: str, home: Path | None) -> bool:
    data = (home / ".local/share") if home else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    target = data / "applications" / DESKTOP_NAME
    text = _desktop_entry(command)
    if target.is_file() and target.read_text() == text:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    for cmd in (
        ["xdg-mime", "default", DESKTOP_NAME, f"x-scheme-handler/{SCHEME}"],
        ["update-desktop-database", str(target.parent)],
    ):
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass  # the entry is in place; a missing tool only means the system learns of it later
    return True


def _register_windows(command: str) -> bool:
    import winreg  # noqa: PLC0415 - only exists on Windows

    root = rf"Software\Classes\{SCHEME}"
    want = {"": "URL:MOG", "URL Protocol": ""}
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
            for name, value in want.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{command}" "%1"')
    except OSError:
        return False
    return True


def register(command: str, platform: str | None = None, home: Path | None = None) -> bool:
    """Make `command` the handler of `mog://` links for this user. Idempotent: returns True only when something
    was written. `home` is for tests; it stands for the user's home folder."""
    platform = platform or sys.platform
    if platform == "win32":
        return _register_windows(command)
    if platform.startswith("linux"):
        return _register_linux(command, home)
    return False
