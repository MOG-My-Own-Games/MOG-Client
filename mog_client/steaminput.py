"""Steam Input's choice of controller template for a non-Steam game (Qt-free).

Steam keeps what each account picked in `steamapps/common/Steam Controller Configs/<account>/config/`, one
`configset_controller_<type>.vdf` per kind of controller (`neptune` is the Steam Deck). In it a game is a block named by the
shortcut's name, lowercased and without punctuation, that says which template it uses:

    "controller_config"
    {
        "my game"
        {
            "template"		"controller_neptune_wasd.vdf"
        }
    }

`controller_neptune_wasd.vdf` is the Deck's "Keyboard (WASD) and Mouse" template. A game that already has a block there (a
template, a layout the person made, a workshop one) is never changed: what they chose wins. The file is edited as text, so
everything else in it stays as Steam wrote it.
"""

from __future__ import annotations

import re
from pathlib import Path

DECK = "neptune"
KEYBOARD_MOUSE = "controller_neptune_wasd.vdf"
FOLDER = Path("steamapps/common/Steam Controller Configs")


def configset_path(user_dir: Path, controller: str = DECK) -> Path:
    """The file for a kind of controller in the account whose `userdata/<id>` folder is `user_dir`."""
    return user_dir.parent.parent / FOLDER / user_dir.name / "config" / f"configset_controller_{controller}.vdf"


def key_of(name: str) -> str:
    """How Steam names a shortcut's block: its name in lower case, keeping letters, digits, spaces and apostrophes (`Setup.exe` is
    `setupexe`, `Living With Sister: Monochrome Fantasy` is `living with sister monochrome fantasy`)."""
    return "".join(c for c in name.lower() if c.isalnum() or c in " '")


def has_block(text: str, key: str) -> bool:
    """Whether the file has a block for `key` at the top level (one tab in, then its brace)."""
    return re.search(rf'^\t"{re.escape(key)}"[ \t]*\r?\n\t\{{', text, re.MULTILINE) is not None


def with_template(text: str, key: str, template: str) -> str | None:
    """The file's text with a block for `key` using `template` added before the closing brace; None when the text is not a
    `controller_config` file (nothing is touched then)."""
    if not text.lstrip().startswith('"controller_config"'):
        return None
    end = text.rfind("}")
    if end < 0:
        return None
    block = f'\t"{key}"\n\t{{\n\t\t"template"\t\t"{template}"\n\t}}\n'
    head = text[:end] if text[:end].endswith("\n") else text[:end] + "\n"
    return head + block + text[end:]


def set_template(user_dir: Path, name: str, template: str = KEYBOARD_MOUSE, controller: str = DECK) -> bool:
    """Give the game called `name` that template in this account, unless it already has a block. True when the file was written.
    The first time, the file is copied beside itself as `.mog-backup`."""
    key = key_of(name)
    if not key.strip():
        return False
    path = configset_path(user_dir, controller)
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if has_block(text, key):
            return False
        new = with_template(text, key, template)
        if new is None:
            return False
        backup = path.with_name(path.name + ".mog-backup")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
    elif (user_dir.parent.parent / "steamapps").is_dir():  # a Steam that has run: the file Steam would make has the one block
        new = with_template('"controller_config"\n{\n}\n', key, template)
    else:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
    tmp.replace(path)
    return True
