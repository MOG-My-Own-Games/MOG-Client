"""What a game's saves are, told by hand (Qt-free). The server keeps, per game, paths that are never backed up (the shader and
build caches a port writes beside its saves) and paths that always are, as archive keys: a file, a folder with all that is in
it, or a pattern (`*.png`, `game/*/shaders`, `re:\\.(png|jpg)$`). A backup reads them first, and uses the ones it read last when the server cannot be asked."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

from mog_client.saves.locations import (
    GAME_KEY,
    Candidate,
    UnsafeKey,
    native_key,
    target_for_key,
)
from mog_client.saves.state import load_state, save_state


@dataclass
class Rules:
    blacklist: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.blacklist or self.whitelist)

    def blocked(self, key: str) -> bool:
        """Whether the backup leaves this key out: a blacklist rule covers it and no whitelist rule does. The whitelist is
        stronger, whichever rule is more specific: `game/thisgame` blacklisted and `game/thisgame/saves` added leave out all
        of the game but its saves."""
        return is_covered(key, self.blacklist) and not is_covered(key, self.whitelist)

    def added(self, key: str) -> bool:
        return is_covered(key, self.whitelist)

    def covering(self, key: str) -> list[str]:
        """The blacklist rules that leave this key out; none when the whitelist keeps it."""
        return [] if is_covered(key, self.whitelist) else [p for p in self.blacklist if covers(p, key)]


REGEX_PREFIX = "re:"
REGEX_MAX_LENGTH = 200


def is_pattern(rule: str) -> bool:
    """A rule that is a pattern, not a path: `re:` and a regular expression, or a glob (`*`, `?` or a `[...]` class)."""
    return rule.startswith(REGEX_PREFIX) or "*" in rule or "?" in rule or ("[" in rule and "]" in rule)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as a regular expression: `*` and `?` stay inside a folder name, `**` crosses folders, `[abc]` is one of them
    (`[!abc]` none of them). Letter case does not count: the files of a Wine prefix do not tell it either."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[" and (close := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : close].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body[0] in "!^" else body) + "]")
            i = close + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


@lru_cache(maxsize=256)
def _regex(rule: str) -> re.Pattern[str] | None:
    try:
        return re.compile(rule[len(REGEX_PREFIX) :])
    except re.error:
        return None  # a rule another client wrote that this one cannot read leaves nothing out


def check_rule(rule: str) -> str | None:
    """Why a rule typed by hand cannot be used, or None. The server checks it too; this is to say so before sending."""
    if rule.startswith(REGEX_PREFIX):
        pattern = rule[len(REGEX_PREFIX) :]
        if not pattern or len(pattern) > REGEX_MAX_LENGTH:
            return f"A regular expression takes 1 to {REGEX_MAX_LENGTH} characters"
        try:
            re.compile(pattern)
        except re.error as e:
            return f"Not a regular expression: {e}"
        return None
    parts = rule.rstrip("/").split("/")
    if not rule.strip("/") or rule.startswith("/") or "\\" in rule or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
        return "Write it like the keys of the lists: relative, without .., a drive letter or a backslash"
    try:
        _glob_regex(rule) if is_pattern(rule) else None
    except re.error as e:
        return f"Not a pattern: {e}"
    return None


def covers(rule: str, key: str) -> bool:
    """Whether a rule names `key`: a path (itself, or a folder above it), a glob (without a `/` matched against each name in the
    key, so `*.png` is every png and `cache*` a folder; with one, against the key from its start and each folder above it) or
    `re:` and a regular expression searched in the key. The server reads them the same way (`models/game_save_rule.py`)."""
    if rule.startswith(REGEX_PREFIX):
        rx = _regex(rule)
        return rx is not None and rx.search(key) is not None
    if not is_pattern(rule):
        return key == rule or key.startswith(rule + "/")
    parts = key.split("/")
    rx = _glob_regex(rule)
    if "/" not in rule:
        return any(rx.fullmatch(part) for part in parts)
    return any(rx.fullmatch("/".join(parts[:n])) for n in range(1, len(parts) + 1))


def is_covered(key: str, paths: Iterable[str]) -> bool:
    return any(covers(p, key) for p in paths)


def stored(game_id: int) -> Rules:
    """The rules this machine read last."""
    state = load_state(game_id)
    return Rules(list(state.rules_blacklist), list(state.rules_whitelist))


def remember(game_id: int, rules: Rules) -> None:
    state = load_state(game_id)
    if (state.rules_blacklist, state.rules_whitelist) != (rules.blacklist, rules.whitelist):
        state.rules_blacklist, state.rules_whitelist = list(rules.blacklist), list(rules.whitelist)
        save_state(game_id, state)


def from_answer(data: dict) -> Rules:
    return Rules(
        [p for p in data.get("blacklist") or [] if isinstance(p, str)],
        [p for p in data.get("whitelist") or [] if isinstance(p, str)],
    )


def load(client, game_id: int) -> Rules:
    """The game's rules from the server, which this machine keeps; the ones it kept when the server cannot say."""
    answer = client.save_rules(game_id)
    if answer is None:
        return stored(game_id)
    rules = from_answer(answer)
    remember(game_id, rules)
    return rules


def without_blacklisted(candidates: list[Candidate], rules: Rules) -> list[Candidate]:
    if not rules.blacklist:
        return candidates
    return [c for c in candidates if not rules.blocked(c.key)]


def _all_files(root: Path, key: str) -> Iterator[Candidate]:
    """Every file below a folder the user added by hand: no cache or log rule applies to what was asked for."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            yield Candidate(str(PurePosixPath(key, *path.relative_to(root).parts)), path)


def added_files(rules: Rules, drive_c: Path | None, install_dir: Path, sandbox: str | None = None) -> list[Candidate]:
    """The files of the paths added by hand that are on this machine, whatever the scan finds (those blacklisted are not)."""
    found: list[Candidate] = []
    for key in rules.whitelist:
        if is_pattern(key):
            continue  # a pattern keeps what the scan found; there is no place to look for it
        try:
            path = target_for_key(key, drive_c, install_dir, sandbox)
        except (UnsafeKey, OSError):
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            found.append(Candidate(key, path))
        elif path.is_dir():
            found += _all_files(path, key)
    return [c for c in found if not rules.blocked(c.key)]


def merge(candidates: list[Candidate], extra: list[Candidate]) -> list[Candidate]:
    """The candidates and the added files, a key once."""
    have = {c.key for c in candidates}
    return candidates + [c for c in extra if c.key not in have]


def key_for_path(path: Path, drive_c: Path | None, install_dir: Path, sandbox: str | None = None) -> str | None:
    """The archive key of a file or folder on this machine, or None when it is in none of the places a save has a key in. The
    game's folder gives `game/...`; the profile of the Wine prefix gives `users/USER/...` (and `drive_c` paths without one);
    anything else below the home folder gives a native key."""
    path = Path(os.path.abspath(path))
    if install_dir and (install_dir == path or install_dir in path.parents):
        return str(PurePosixPath(GAME_KEY, *path.relative_to(install_dir).parts)) if path != install_dir else None
    if drive_c is not None:
        from mog_client.saves.locations import USER_KEY, user_dir_name

        profile = drive_c / "users" / user_dir_name(drive_c)
        if profile == path:
            return None
        if profile in path.parents:
            return str(PurePosixPath(USER_KEY, *path.relative_to(profile).parts))
        if drive_c in path.parents:
            return str(PurePosixPath(*path.relative_to(drive_c).parts))
    key = native_key(path, sandbox)
    return key if key and "/" in key else None
