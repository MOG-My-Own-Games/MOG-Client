"""The questions of Define saves: teaching MOG what to back up for a game. A path can be blacklisted (never backed up: the
shader and build caches some ports write beside their saves) or added by hand, and the files of a version kept on the server can
be listed, ticked and taken out of it.

Like `saves_ui`, nothing here touches Qt: the window supplies `choose`, `ask`, `message`, `notify`, `browse_any`, the two pages
(`show_define_saves`, `show_save_files`) and `app.bridge.call` to run something on the GUI thread. The rules live on the server
(see `saves/rules.py`); every change goes there first and the pages show what it answered.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mog_client.config import InstalledGame
from mog_client.saves import rules as save_rules
from mog_client.saves import sync
from mog_client.saves.state import load_state

BLACKLIST, WHITELIST = "blacklist", "whitelist"
CHUNK = 500  # the paths one call of the server takes


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class DefineSaves:
    def __init__(self, win) -> None:
        self.win = win

    @property
    def app(self):
        return self.win.app

    def gui(self, fn: Callable[[], None]) -> None:
        self.app.bridge.call.emit(fn)

    def fail(self, text: str) -> None:
        self.app.bridge.error.emit(text)

    def start(self, rec: InstalledGame) -> None:
        """The button in the game's Saves options: the rules as the server has them."""

        def run(ctx: sync.Context) -> None:
            try:
                rules = save_rules.load(ctx.client, rec.game_id)
            except (RuntimeError, OSError) as e:
                return self.fail(f"Could not read the save rules of {rec.name}: {e}")
            self.gui(lambda: self.win.show_define_saves(self, rec, ctx, rules))

        self.win.saves.with_context(rec, run, otherwise=lambda why: self.win.message(
            f"Define saves needs MOG Server: {why or 'it is not reachable'}", "warning"), ask=True)

    # --- the rules ---

    def add(self, rec: InstalledGame, ctx: sync.Context, page) -> None:
        """Pick a file or a folder on this machine, then say whether it is saves or not."""
        start = Path(rec.install_dir) if Path(rec.install_dir).is_dir() else Path.home()

        def picked(chosen: str) -> None:
            key = self.key_of(rec, ctx, Path(chosen))
            if key is None:
                self.win.message(
                    f"{chosen} is not in the game's folder, its Wine prefix or your home folder, so a save has no name for it", "error"
                )
                return
            self.win.choose(
                "What is it?",
                f"{key}\nA folder counts with everything in it.",
                [("Never back it up (blacklist)", BLACKLIST), ("Always back it up (add to the saves)", WHITELIST)],
                lambda kind: self.change(rec, ctx, page, kind, [key]) if kind else None,
                game=rec.game_id,
            )

        self.win.browse_any(
            start, picked, text=f"Pick a file or a folder of {rec.name}: a shader cache to leave out, or a save the scan does not find."
        )

    def add_pattern(self, rec: InstalledGame, ctx: sync.Context, page) -> None:
        """A pattern typed by hand: `*.png`, `game/*/shaders`, or `re:` and a regular expression."""

        def typed(text: str) -> None:
            why = save_rules.check_rule(text)
            if why:
                self.win.message(f"{text}: {why}", "error")
                return
            self.win.choose(
                "What is it?",
                f"{text}\nIt names every path it fits, in the game's folder and its prefix. A pattern that is added to the saves "
                "only keeps what MOG already finds.",
                [("Never back them up (blacklist)", BLACKLIST), ("Always back them up (add to the saves)", WHITELIST)],
                lambda kind: self.change(rec, ctx, page, kind, [text]) if kind else None,
                game=rec.game_id,
            )

        self.win.ask_text(
            "Add a pattern",
            "* is any name, ? one letter: *.png is every png, cache* a folder, game/*/shaders one at any name. "
            "Start with re: for a regular expression, like re:\\.(png|jpg)$",
            "",
            typed,
        )

    def key_of(self, rec: InstalledGame, ctx: sync.Context, path: Path) -> str | None:
        state = load_state(rec.game_id)
        drive_c = sync._drive_c(sync.find_prefix(ctx, state), rec)
        return save_rules.key_for_path(path, drive_c, Path(rec.install_dir), rec.in_sandbox)

    def change(self, rec, ctx, page, kind: str, keys: list[str], said: str | None = None) -> None:
        """Put keys on a list on the server and show the lists it answers with."""

        def work() -> None:
            answer = None
            for i in range(0, len(keys), CHUNK):
                answer = ctx.client.add_save_rules(rec.game_id, kind, keys[i : i + CHUNK])
            rules = self.remember(rec, answer)
            what = "left out of the backup" if kind == BLACKLIST else "added to the saves"
            self.gui(lambda: self.done(page, rules, said or f"{plural(len(keys), 'path')} {what}"))

        self.app.run_bg(work, on_error=lambda m: self.fail(f"Could not change the save rules: {m}"))

    def forget(self, rec, ctx, page, keys: list[str], said: str | None = None) -> None:
        """Take rules off: a blacklisted path is backed up again the next time it is there."""

        def work() -> None:
            answer = None
            for i in range(0, len(keys), CHUNK):
                answer = ctx.client.remove_save_rules(rec.game_id, keys[i : i + CHUNK])
            rules = self.remember(rec, answer)
            self.gui(lambda: self.done(page, rules, said or f"{plural(len(keys), 'rule')} removed"))

        self.app.run_bg(work, on_error=lambda m: self.fail(f"Could not change the save rules: {m}"))

    def remember(self, rec, answer: dict | None) -> save_rules.Rules:
        rules = save_rules.from_answer(answer or {})
        save_rules.remember(rec.game_id, rules)
        return rules

    def done(self, page, rules: save_rules.Rules, text: str) -> None:
        page.set_rules(rules)
        self.win.notify(text)

    # --- the files of a saved version ---

    def files(self, rec: InstalledGame, ctx: sync.Context, page) -> None:
        """Choose a saved version on the server, then list its files."""

        def run() -> None:
            rows = sync.versions_of(ctx)
            self.gui(lambda: self.pick_version(rec, ctx, page, rows))

        self.app.run_bg(run, on_error=lambda m: self.fail(f"Could not list the saved versions: {m}"))

    def pick_version(self, rec, ctx, page, rows) -> None:
        from mog_client.gui.saves_ui import version_label

        if not rows:
            self.win.message(f"No saves of {rec.name} on the server yet", "info")
            return
        self.win.choose(
            "Which version?",
            "The files of a version saved on the server. Edit there lets you take files out of it or blacklist them.",
            [(version_label(v, d, rec.game_id), v["id"]) for v, d in rows],
            lambda version_id: self.open_version(rec, ctx, page, version_id) if version_id else None,
            game=rec.game_id,
        )

    def open_version(self, rec, ctx, page, version_id: int) -> None:
        def work() -> None:
            version = ctx.client.get_save(version_id)
            self.gui(lambda: self.win.show_save_files(self, rec, ctx, version, page.rules, page))

        self.app.run_bg(work, on_error=lambda m: self.fail(f"Could not read the version: {m}"))

    def delete_files(self, rec, ctx, page, version: dict, keys: list[str]) -> None:
        """Take files out of a saved version on the server (not off this machine), after asking."""

        def go() -> None:
            def work() -> None:
                after: dict | None = version
                for i in range(0, len(keys), CHUNK):
                    after = ctx.client.remove_save_files(version["id"], keys[i : i + CHUNK])
                    if after is None:
                        break
                self.gui(lambda: self.deleted(page, after, len(keys)))

            self.app.run_bg(work, on_error=lambda m: self.fail(f"Could not take the files out of the version: {m}"))

        self.win.ask(
            f"Take {plural(len(keys), 'file')} out of this saved version on the server? That cannot be undone. "
            "The files on this machine stay, and are backed up again unless they are blacklisted.",
            go,
            danger=True,
            game=rec.game_id,
        )

    def deleted(self, page, after: dict | None, count: int) -> None:
        page.set_version(after)
        self.win.notify(f"{plural(count, 'file')} taken out of the version")
