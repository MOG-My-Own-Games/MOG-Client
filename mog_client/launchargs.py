"""A game's launch arguments as tags: a list in which each tag is one option with what belongs to it (`-res 1280 720`,
`--nosound`). Two editors (the server's web page, this client) are merged by tag, where a text would let one overwrite the
other. The same rules as the server's `utils/launch_args.py`. Stdlib only."""

from __future__ import annotations

import re
import shlex

MAX_TAG_LENGTH = 200
MAX_TAGS = 50

_NEGATIVE_NUMBER = re.compile(r"-\d+(?:\.\d+)?")


def split_tags(text: str) -> list[str]:
    """Typed arguments as tags: a new tag starts at each word that begins with `-` or `--` (a negative number is a value, not
    an option), and the words after it, up to the next such word, are part of it. Quoting is kept (`-title 'My Game'`)."""
    try:
        words = shlex.split(text)
        join = shlex.join
    except ValueError:  # a quote left open: the words as they are, joined as they were
        words = text.split()
        join = " ".join
    tags: list[str] = []
    group: list[str] = []
    for word in words:
        if word.startswith("-") and not _NEGATIVE_NUMBER.fullmatch(word) and group:
            tags.append(join(group))
            group = []
        group.append(word)
    if group:
        tags.append(join(group))
    return list(dict.fromkeys(tags))


def is_tag(tag: object) -> bool:
    return (
        isinstance(tag, str)
        and tag == tag.strip()
        and 0 < len(tag) <= MAX_TAG_LENGTH
        and not any(ord(c) < 32 or ord(c) == 127 for c in tag)
    )


def as_tags(value) -> list[str]:
    """Tags from whatever was kept or sent: a list (what is not a tag is dropped, once each), the text an earlier version kept
    (split into tags), or nothing."""
    if isinstance(value, str):
        return split_tags(value)
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(t for t in value if is_tag(t)))[:MAX_TAGS]


def words(tags: list[str]) -> list[str]:
    """The arguments to put after the executable: each tag split like a shell does, in order. Raises ValueError for a tag with
    a quote that is never closed."""
    return [word for tag in tags for word in shlex.split(tag)]


def merge(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
    """The tags of two editors that both started from `base`: what the server holds (`theirs`) without what this side took out
    of `base`, then what this side put in that the server lacks, at the end. A tag either side added stays; one either side
    removed goes; nobody's change is lost to the other's."""
    removed_here = {t for t in base if t not in ours}
    kept = [t for t in theirs if t not in removed_here]
    return kept + [t for t in ours if t not in base and t not in kept]
