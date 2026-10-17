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


def _collect(lines: list[str], header_to_key, keep_deletes: bool = False) -> list[tuple[str, list[str]]]:
    """(key below Software\\Wine, its value lines) for the sections `header_to_key` accepts and the values `_wanted` keeps.
    The continuation lines of a long value (`hex:...,\\`) are joined to its first line with a newline. A value deletion
    (`"name"=-`) is dropped, except with `keep_deletes` (our own undo text has them)."""
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
        if values is None or name is None or (_DELETES.match(line) and not keep_deletes) or not _wanted(key, name):
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


def section_values(reg_text: str, section: str, names: tuple[str, ...]) -> list[str]:
    """The `"name"="value"` lines of a prefix's registry file (`system.reg`, `user.reg`) for `names` (lower case) in `section`
    (its path with doubled backslashes, as those files write it), as `.reg` lines."""
    found: list[str] = []
    inside = False
    for line in reg_text.splitlines():
        if line.startswith("["):
            inside = line[1 : line.find("]")].lower() == section.lower()
        elif inside and line.startswith('"'):
            end = line.find('"', 1)
            if end > 0 and line[1:end].lower() in names and line[end + 1 :].startswith("="):
                found.append(line)
    return found


def describe(reg_text: str) -> list[str]:
    """One line for each value the settings set, `Key: "name"="value"` (the key under HKCU\\Software\\Wine), to show them."""
    return [f"{key or 'Wine'}: {value.splitlines()[0]}" for key, value in _index(clean_import(reg_text)).values()]


def _index(reg_text: str, keep_deletes: bool = False) -> dict[tuple[str, str], tuple[str, str]]:
    """{(key, value name), both lower case: (key, the value's text)} of a `.reg` as `export_settings` and `clean_import` leave it."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for key, values in _collect(reg_text.splitlines(), _from_reg_header, keep_deletes):
        for value in values:
            name = _value_name(value.split("\n", 1)[0])
            if name is not None:
                found[(key.lower(), name.lower())] = (key, value)
    return found


def _reg_of(entries: list[tuple[str, str]]) -> str:
    """`.reg` text for (key, value text) pairs, grouped by key in the order they come. A key is below Software\\Wine, unless
    it starts with HKEY_ (a whole path, as the language settings need)."""
    sections: dict[str, list[str]] = {}
    for key, value in entries:
        sections.setdefault(key, []).append(value)
    blocks = [f"[{key}]\n" if key.startswith("HKEY_") else f"[{ROOT}\\{key}]\n" if key else f"[{ROOT}]\n" for key in sections]
    return (
        f"{HEADER}\n\n" + "\n\n".join(block + "\n".join(values) for block, values in zip(blocks, sections.values())) + "\n"
        if sections
        else ""
    )


def undo_text(before: str, after: str) -> str:
    """A `.reg` that puts back what changing `before` into `after` altered: a value that was not there is deleted, one that
    was there with another value gets it back; a value that did not change is left out. Both are `.reg` text as
    `export_settings` makes it (empty for none). Empty when nothing changed."""
    was, now = _index(before), _index(after)
    undo: list[tuple[str, str]] = []
    for ident, (key, value) in now.items():
        if ident not in was:
            name = _value_name(value.split("\n", 1)[0])
            undo.append((key, "@=-" if name == "@" else f'"{_VALUE.match(value).group("quoted")}"=-'))
        elif was[ident][1] != value:
            undo.append(was[ident])
    return _reg_of(undo)


def merge_undo(older: str, newer: str) -> str:
    """Two undo texts as one: for a value both put back, the older one's (what it was before either change) wins."""
    kept = _index(older, keep_deletes=True)
    entries = list(kept.values())
    entries += [pair for ident, pair in _index(newer, keep_deletes=True).items() if ident not in kept]
    return _reg_of(entries)
