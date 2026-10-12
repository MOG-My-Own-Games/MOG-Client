"""What winecfg changed in a prefix, as a small registry export that can be kept on the server and put into another
device's prefix. Stdlib only.

Only the settings winecfg is for travel: the Windows version, DLL overrides, Direct3D, DirectSound, the X11 and virtual
desktop options and the per-program versions of these. The rest of `HKCU\\Software\\Wine` (audio driver, the machine's
display) belongs to the machine and stays out, both when exporting and when importing what the server holds."""

from __future__ import annotations

import re
from pathlib import Path

HEADER = "Windows Registry Editor Version 5.00"
ROOT = "HKEY_CURRENT_USER\\Software\\Wine"
# Keys under Software\Wine that are taken whole (with what is below them), and the values taken from the two that are
# taken only in part.
WHOLE_KEYS = ("dlloverrides", "direct3d", "directsound", "x11 driver", "explorer\\desktops", "appdefaults")
SOME_VALUES = {"": ("version",), "explorer": ("desktop",)}
# Limit on what is stored; the server refuses more (models/game_launch_settings.py).
MAX_BYTES = 64 * 1024

_SECTION = re.compile(r"^\[(?P<key>[^\]]+)\]")
_DELETES = re.compile(r'^(?:"(?:[^"\\]|\\.)*"|@)=-')  # `"name"=-` deletes a value, which is not a setting
_VALUE = re.compile(r'^(?:"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<default>@))=')


def find_user_reg(prefix: Path) -> Path | None:
    """The prefix's HKCU file: in the prefix itself for Wine, one level down (`pfx/pfx`) for Proton."""
    for candidate in (prefix / "user.reg", prefix / "pfx" / "user.reg"):
        if candidate.is_file():
            return candidate
    return None


def _wanted(key: str, value: str | None) -> bool:
    """Whether `key` (below Software\\Wine, backslash-separated) and, for a value line, its name are ones to keep."""
    key = key.lower()
    if value is None:  # a section's own header: kept when something of it may be
        return key in SOME_VALUES or any(key == w or key.startswith(w + "\\") for w in WHOLE_KEYS)
    if key in SOME_VALUES:
        return value.lower() in SOME_VALUES[key]
    return any(key == w or key.startswith(w + "\\") for w in WHOLE_KEYS)


def _value_name(line: str) -> str | None:
    match = _VALUE.match(line)
    if match is None:
        return None
    return "@" if match.group("default") else match.group("quoted")


def _filtered(sections: list[tuple[str, list[str]]]) -> str:
    blocks = []
    for key, values in sections:
        title = f"{ROOT}\\{key}" if key else ROOT
        blocks.append(f"[{title}]\n" + "\n".join(values))
    return f"{HEADER}\n\n" + "\n\n".join(blocks) + "\n" if blocks else ""


def _collect(lines: list[str], header_to_key) -> list[tuple[str, list[str]]]:
    """(key below Software\\Wine, its value lines) for the sections `header_to_key` accepts and the values `_wanted` keeps.
    The continuation lines of a long value (`hex:...,\\`) are joined to its first line with a newline."""
    sections: list[tuple[str, list[str]]] = []
    key: str | None = None
    values: list[str] | None = None
    continuing = False
    for raw in lines:
        line = raw.rstrip("\r")
        if continuing:
            if values:
                values[-1] += "\n" + line
            continuing = line.endswith("\\")
            continue
        header = _SECTION.match(line)
        if header:
            key = header_to_key(header.group("key"))
            values = None
            if key is not None and _wanted(key, None):
                values = []
                sections.append((key, values))
            continue
        name = _value_name(line)
        if values is None or name is None or _DELETES.match(line) or not _wanted(key, name):
            continue
        values.append(line)
        continuing = line.endswith("\\")
    return [(k, v) for k, v in sections if v]


def _from_wine_header(key: str) -> str | None:
    parts = key.split("\\\\")  # user.reg doubles the backslashes of a key
    if [p.lower() for p in parts[:2]] != ["software", "wine"]:
        return None
    return "\\".join(parts[2:])


def _from_reg_header(key: str) -> str | None:
    parts = key.split("\\")
    if [p.lower() for p in parts[:3]] != ["hkey_current_user", "software", "wine"]:
        return None  # also drops `[-HKEY...]`, which deletes a key
    return "\\".join(parts[3:])


def export_settings(user_reg: str) -> str:
    """The winecfg settings in a prefix's `user.reg`, as `.reg` text; empty when there are none."""
    return _filtered(_collect(user_reg.splitlines(), _from_wine_header))


def clean_import(reg_text: str) -> str:
    """`reg_text` (from the server, so not trusted) reduced to the settings this module exports: any other key, key
    deletion or value deletion is dropped. Empty when nothing is left."""
    return _filtered(_collect(reg_text.splitlines(), _from_reg_header))
