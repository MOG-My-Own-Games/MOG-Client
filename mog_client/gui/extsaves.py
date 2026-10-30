"""The questions of Import external saves: putting the saves a game made somewhere else in place of its fresh ones.

Like `saves_ui`, nothing here touches Qt: the window supplies `choose`, `ask`, `message`, `notify`, `browse_folder`, and
`app.bridge.call` to run something on the GUI thread. See `saves/external.py` for how the old saves are matched.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mog_client.config import InstalledGame
from mog_client.saves import external, sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.state import load_state

ANOTHER_FOLDER, ALL, NEWER = "*another*", "*all*", "*newer*"
PREFIX_NOTE = "Not a Default prefix (like ~/.wine or umu-default) unless your old saves are really there: it is shared by many games."


class ExternalSaves:
    def __init__(self, win) -> None:
        self.win = win

    @property
    def app(self):
        return self.win.app

    def gui(self, fn: Callable[[], None]) -> None:
        self.app.bridge.call.emit(fn)

    def start(self, rec: InstalledGame) -> None:
        """The button in the game's Saves options."""
        state = load_state(rec.game_id)
        if not external.has_structure(state):
            self.win.message(
                f"Start {rec.name}, save at least once and close it, then come back here.\n"
                "MOG needs to see where the game writes its saves before it can find your old ones in the same places.",
                "info",
            )
            return
        kinds = external.sources(state)
        if not kinds:
            self.win.message(
                f"{rec.name} keeps its saves in your home folder, where an older install of it wrote them too: "
                "there is nothing to import.",
                "info",
            )
            return
        if kinds == {external.PREFIX}:
            self.ask_prefix(rec)
        elif kinds == {external.FOLDER}:
            self.ask_folder(rec)
        else:  # one place at a time: what is put in stays, so the other can be done after
            def chosen(answer) -> None:
                if answer == external.PREFIX:
                    self.ask_prefix(rec)
                elif answer == external.FOLDER:
                    self.ask_folder(rec)

            self.win.choose(
                "Where are your old saves?",
                "The game keeps saves in its prefix and in its own folder: do one, then the other.",
                [("In another Wine prefix...", external.PREFIX), ("In the old game folder...", external.FOLDER)],
                chosen,
            )

    def ask_prefix(self, rec: InstalledGame) -> None:
        mine = prefixes.resolve_prefix(rec, "auto", load_state(rec.game_id).prefix, load_state(rec.game_id).prefix_source)
        own = mine.prefix.resolve() if mine else None
        options = [
            (f"{p}   (Default: shared with other games)" if prefixes.is_default(p) else str(p), p)
            for p in prefixes.candidate_prefixes()
            if p.resolve() != own
        ] + [("Another folder...", ANOTHER_FOLDER)]

        def use(path: Path) -> None:
            if not prefixes.is_prefix(path):
                self.win.message(f"{path} does not look like a Wine prefix (no drive_c inside)", "error")
                return
            self.look(rec, prefix=path)

        def chosen(answer) -> None:
            if answer == ANOTHER_FOLDER:
                self.win.browse_folder(Path.home(), lambda picked: use(Path(picked)))
            elif answer is not None:
                use(answer)

        self.win.choose("In which prefix are your old saves?", PREFIX_NOTE, options, chosen, game=rec.game_id)

    def ask_folder(self, rec: InstalledGame) -> None:
        self.win.browse_folder(
            Path.home(),
            lambda picked: self.look(rec, folder=Path(picked)),
            text=f"Pick the folder of the old install of {rec.name} (the one with its saves in it)",
        )

    def look(self, rec: InstalledGame, prefix: Path | None = None, folder: Path | None = None) -> None:
        source = prefix or folder

        def run(ctx: sync.Context) -> None:
            try:
                matches = external.find(ctx, prefix, folder)
            except OSError as e:
                text = f"Could not look in {source}: {e}"
                self.gui(lambda: self.win.message(text, "error"))
                return
            self.gui(lambda: self.found(rec, ctx, matches, source))

        self.win.saves.with_context(rec, run, ask=True)

    def found(self, rec: InstalledGame, ctx: sync.Context, matches: list[external.Match], source: Path) -> None:
        if not matches:
            self.win.message(
                f"Nothing in {source} is different from what {rec.name} has now, in the places it keeps its saves.", "info"
            )
            return
        newer = external.only_newer(matches)
        replaced = sum(1 for m in matches if m.status == external.DIFFERS)
        count = f"{len(matches)} file{'' if len(matches) == 1 else 's'}"
        text = (
            f"{count} in {source} {'is' if len(matches) == 1 else 'are'} not what {rec.name} has now ({replaced} would replace "
            f"a file, {len(matches) - replaced} new here). What they replace is kept in a backup, and the result is backed up to the server."
        )
        if len(newer) == len(matches):
            self.win.ask(text + "\n\nPut them in?", lambda: self.apply(rec, ctx, matches))
            return
        def chosen(answer) -> None:
            if answer == ALL:
                self.apply(rec, ctx, matches)
            elif answer == NEWER and newer:
                self.apply(rec, ctx, newer)

        options = [(f"All {len(matches)} files", ALL)]
        if newer:
            options.append((f"Only the {len(newer)} that are new or newer than here", NEWER))
        self.win.choose(
            "Put your old saves in?",
            text + f"\n\n{len(matches) - len(newer)} of them are older than the file the game has now.",
            options,
            chosen,
            game=rec.game_id,
        )

    def apply(self, rec: InstalledGame, ctx: sync.Context, matches: list[external.Match]) -> None:
        self.win.notify(f"Putting the old saves of {rec.name} in...")

        def work() -> None:
            result = external.apply(ctx, matches)
            where = f" What they replaced is in {result.backup}." if result.backup else ""
            try:
                sent = sync.backup(ctx, sync.MANUAL, force=True)
                tail = {
                    "uploaded": " They are on the server now.",
                    "duplicate": " The server already had them.",
                    "offline": " MOG Server is out of reach: they are sent as soon as it is back.",
                }.get(sent.status, "")
            except Exception as e:  # noqa: BLE001 - the files are in place; only the upload failed
                tail = f" They could not be backed up yet: {e}"
            n = len(result.restored)
            text = f"{n} old save file{'' if n == 1 else 's'} of {rec.name} {'is' if n == 1 else 'are'} in place.{where}{tail}"
            self.gui(lambda: self.win.message(text, "info"))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not put the old saves in: {m}"))
