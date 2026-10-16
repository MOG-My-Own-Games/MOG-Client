"""umu's database of games (umu-database.csv): which of them umu has fixes for, by the id (`umu-zerozone`) umu-protonfixes looks them up
under. umu only applies a game's fixes when it is started with that id as `GAMEID`, so the id is found here for the games that
have one. The file is kept in the client's data folder and fetched again once in a while; a start never waits for the network.
Stdlib only."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from pathlib import Path

from mog_client import config, logstore
from mog_client.api import fetch_url

URL = "https://raw.githubusercontent.com/Open-Wine-Components/umu-database/main/umu-database.csv"
MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_BYTES = 8 * 1024 * 1024
MIN_STEM = 6  # a game found by its exe name alone needs a name this long: "game.exe" would match anything

_loaded: tuple[Path, float, list[dict]] | None = None


def cache_path() -> Path:
    return config.data_dir() / "umu-database.csv"


def _key(text: str) -> str:
    """A title as it is compared: the case, the punctuation and the spaces do not count ("Heroes of Might & Magic" is the same
    as "heroes of might and magic")."""
    return re.sub(r"[^a-z0-9]+", "", text.casefold().replace("&", "and"))


def _rows() -> list[dict]:
    """The database as it is kept now (read again when the file changed), or nothing when it was never fetched."""
    global _loaded
    path = cache_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return []
    if _loaded is None or _loaded[:2] != (path, stamp):
        try:
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
        except (OSError, csv.Error, UnicodeDecodeError):
            rows = []
        _loaded = (path, stamp, rows)
    return _loaded[2]


def _exe_names(row: dict) -> set[str]:
    return {part.strip().casefold() for part in re.split(r"[|;,]", row.get("EXE_STRINGS (Optional)") or "") if part.strip()}


def find(name: str, executable: str | None = None) -> str | None:
    """The id umu knows the game by (`umu-zerozone`), from its title or, failing that, the name of its executable; None when umu
    has no entry for it."""
    rows = [r for r in _rows() if (r.get("UMU_ID") or "").startswith("umu-")]
    wanted = _key(name)
    if wanted:
        for row in rows:
            if _key(row.get("TITLE") or "") == wanted or _key(row.get("COMMON ACRONYM (Optional)") or "") == wanted:
                return row["UMU_ID"]
    exe = Path(executable).name.casefold() if executable else ""
    if exe and len(Path(exe).stem) >= MIN_STEM:
        for row in rows:
            if exe in _exe_names(row) or (row.get("CODENAME") or "").casefold() == exe:
                return row["UMU_ID"]
    return None


def refresh(force: bool = False) -> bool:
    """Fetch the database again when the copy kept is missing or a week old. Returns True when the file is new or changed, so what
    was written from the old one is written again. A failed fetch is only logged: the old copy stays."""
    path = cache_path()
    try:
        if not force and time.time() - path.stat().st_mtime < MAX_AGE_SECONDS:
            return False
    except OSError:
        pass
    try:
        data = fetch_url(URL, timeout=20.0)
        if not data or len(data) > MAX_BYTES or b"UMU_ID" not in data[:400]:
            raise RuntimeError("not the umu database")
        old = path.read_bytes() if path.is_file() else b""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except (RuntimeError, OSError) as e:
        logstore.warning(f"Could not fetch umu's game database: {e}")
        return False
    return hashlib.sha1(old).digest() != hashlib.sha1(data).digest()
