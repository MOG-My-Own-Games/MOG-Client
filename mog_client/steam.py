"""Add/remove non-Steam shortcuts (shortcuts.vdf) and their grid artwork."""

from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

_END, _MAP, _STR, _INT = 8, 0, 1, 2


def steam_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    if sys.platform == "win32":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "Steam")
    seen, roots = set(), []
    for c in candidates:
        r = c.resolve()
        if (r / "userdata").is_dir() and r not in seen:
            seen.add(r)
            roots.append(r)
    return roots


def steam_user_dirs() -> list[Path]:
    """userdata/<id> dirs, most recently active first."""
    dirs = [d for root in steam_roots() for d in (root / "userdata").iterdir() if d.name.isdigit() and d.name != "0"]
    dirs.sort(key=lambda d: (d / "config").stat().st_mtime if (d / "config").exists() else 0, reverse=True)
    return dirs


def steam_running() -> bool:
    if sys.platform == "win32":
        return False
    for pid in Path("/proc").glob("[0-9]*"):
        try:
            if (pid / "comm").read_text().strip() in ("steam", "steamwebhelper"):
                return True
        except OSError:
            continue
    return False


def shortcut_appid(exe: str, name: str) -> int:
    """Unsigned 32-bit id Steam derives from the quoted exe + name; it names the grid files."""
    return (zlib.crc32((exe + name).encode()) | 0x80000000) & 0xFFFFFFFF


def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def parse_vdf(data: bytes, pos: int = 0) -> tuple[dict, int]:
    out: dict = {}
    while pos < len(data):
        kind = data[pos]
        pos += 1
        if kind == _END:
            return out, pos
        key, pos = _read_cstr(data, pos)
        if kind == _MAP:
            out[key], pos = parse_vdf(data, pos)
        elif kind == _STR:
            out[key], pos = _read_cstr(data, pos)
        elif kind == _INT:
            out[key] = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        else:
            raise ValueError(f"unsupported VDF type {kind}")
    return out, pos


def dump_vdf(d: dict) -> bytes:
    out = bytearray()
    for key, val in d.items():
        k = key.encode() + b"\0"
        if isinstance(val, dict):
            out += bytes([_MAP]) + k + dump_vdf(val)
        elif isinstance(val, int):
            out += bytes([_INT]) + k + struct.pack("<i", _to_i32(val))
        else:
            out += bytes([_STR]) + k + str(val).encode() + b"\0"
    return bytes(out) + bytes([_END])


def _to_i32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


def shortcuts_path(user_dir: Path) -> Path:
    return user_dir / "config" / "shortcuts.vdf"


def load_shortcuts(path: Path) -> dict:
    if not path.is_file():
        return {"shortcuts": {}}
    parsed, _ = parse_vdf(path.read_bytes())
    parsed.setdefault("shortcuts", {})
    return parsed


def save_shortcuts(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = path.with_name(path.name + ".mog-backup")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(dump_vdf(data))
    tmp.replace(path)


def _image_ext(blob: bytes) -> str:
    return "png" if blob[:4] == b"\x89PNG" else "jpg"


def write_artwork(user_dir: Path, appid: int, artwork: dict[str, bytes] | None) -> list[str]:
    """Write a shortcut's images into Steam's grid folder; returns the files written."""
    grid = user_dir / "config" / "grid"
    suffix = {"portrait": "p", "wide": "", "hero": "_hero", "logo": "_logo", "icon": "_icon"}
    written = []
    for kind, blob in (artwork or {}).items():
        grid.mkdir(parents=True, exist_ok=True)
        dest = grid / f"{appid}{suffix[kind]}.{_image_ext(blob)}"
        dest.write_bytes(blob)
        written.append(str(dest))
    return written


def add_shortcut(
    user_dir: Path,
    name: str,
    exe: str,
    start_dir: str,
    launch_options: str,
    icon: str = "",
    artwork: dict[str, bytes] | None = None,
) -> dict:
    """Add one shortcut. `artwork` maps "portrait"/"wide"/"hero"/"logo"/"icon" to image bytes.
    Returns the record to store so the entry can be removed later."""
    quoted = f'"{exe}"'
    appid = shortcut_appid(quoted, name)
    path = shortcuts_path(user_dir)
    data = load_shortcuts(path)
    entries = data["shortcuts"]
    entries = {k: v for k, v in entries.items() if v.get("appid", 0) & 0xFFFFFFFF != appid}
    entries[str(len(entries))] = {
        "appid": appid,
        "AppName": name,
        "Exe": quoted,
        "StartDir": f'"{start_dir}"',
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {},
    }
    data["shortcuts"] = {str(i): v for i, v in enumerate(entries.values())}
    save_shortcuts(path, data)

    return {"shortcuts_path": str(path), "appid": appid, "artwork": write_artwork(user_dir, appid, artwork)}


def update_shortcut(
    record: dict,
    exe: str,
    start_dir: str,
    launch_options: str,
    name: str | None = None,
    artwork: dict[str, bytes] | None = None,
) -> bool | None:
    """Edit a shortcut made by add_shortcut where it stands. Its appid stays, so the artwork
    and play time that belong to it stay too; `artwork` rewrites the images under that same
    appid. Returns True if the shortcut changed (Steam sees that after a restart), False if it
    already matched, None if it is no longer in the file."""
    path = Path(record["shortcuts_path"])
    if not path.is_file():
        return None
    data = load_shortcuts(path)
    for entry in data["shortcuts"].values():
        if entry.get("appid", 0) & 0xFFFFFFFF == record["appid"]:
            wanted = {"Exe": f'"{exe}"', "StartDir": f'"{start_dir}"', "LaunchOptions": launch_options}
            if name is not None:
                wanted["AppName"] = name
            if artwork:
                record["artwork"] = write_artwork(path.parent.parent, record["appid"], artwork)
            if all(entry.get(k) == v for k, v in wanted.items()):
                return False
            entry.update(wanted)
            save_shortcuts(path, data)
            return True
    return None


def remove_shortcut(record: dict) -> None:
    path = Path(record["shortcuts_path"])
    if path.is_file():
        data = load_shortcuts(path)
        kept = [v for v in data["shortcuts"].values() if v.get("appid", 0) & 0xFFFFFFFF != record["appid"]]
        data["shortcuts"] = {str(i): v for i, v in enumerate(kept)}
        save_shortcuts(path, data)
    for art in record.get("artwork", []):
        Path(art).unlink(missing_ok=True)
