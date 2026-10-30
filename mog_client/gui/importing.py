"""The questions of adding a game that is already on this machine, outside MOG, so its saves are synced.

Like `saves_ui`, nothing here touches Qt: the window supplies `choose`, `checklist`, `ask`, `ask_text`, `message`, `notify`,
`browse_folder`, `show_game`, and `app.bridge.call` to run something on the GUI thread. The game's folder and prefix stay where
they are; see `saves/importer.py` for what is looked for and `manager.import_game` for what is recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mog_client import connectivity, identity, logstore, manager
from mog_client.config import InstalledGame, load_library
from mog_client.launcher import PREFIX_DIR
from mog_client.saves import importer, native, sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.locations import GAME_KEY, native_key, sandbox_of
from mog_client.saves.runner import make_context
from mog_client.saves.state import load_state

NEW_ENTRY, BROWSE, NEW_PREFIX, ANOTHER_FOLDER = "*new*", "*browse*", "*new-prefix*", "*another*"
NEW_LIBRARY, NO_LIBRARY = "*new-library*", "*no-library*"
IN_GAME_FOLDER = "*in-game-folder*"

PREFIX_NOTE = (
    "Do not pick a Default prefix (like ~/.wine or umu-default): other games run there too, and their saves may be in it."
)


@dataclass
class Draft:
    """What has been answered so far, before anything is recorded."""

    folder: Path
    target: str = importer.LINUX
    name: str = ""
    game: dict | None = None
    prefix: Path | None = None
    new_prefix: bool = False
    library_id: int | None = None


def key_for_folder(path: Path, install_dir: Path, sandbox: str | None = None) -> str | None:
    """The archive key of a folder the user picked: `game/...` inside the game's own folder, else below the home folder (or, for
    a game in a Flatpak app, the app's own folders). None for a whole root (the home folder, `~/.config`...), which holds far
    more than a game."""
    try:
        rel = path.relative_to(install_dir).as_posix()
    except ValueError:
        key = native_key(path, sandbox)
        return key if key and "/" in key else None
    return GAME_KEY if rel == "." else f"{GAME_KEY}/{rel}"


class ImportFlow:
    def __init__(self, win) -> None:
        self.win = win

    @property
    def app(self):
        return self.win.app

    def gui(self, fn: Callable[[], None]) -> None:
        self.app.bridge.call.emit(fn)

    # --- the questions ---

    def start(self) -> None:
        if not self.app.settings.configured:
            self.win.message("Sign in to MOG Server first: a game added from this computer is kept on it", "warning")
            return
        if connectivity.is_offline():
            self.win.message("MOG Server is not reachable (offline mode): a game cannot be added now", "warning")
            return
        self.win.message("Pick the folder of the game you already have on this computer", "info")
        self.win.browse_folder(Path.home(), self.folder_chosen)

    def folder_chosen(self, folder: str) -> None:
        path = Path(folder)
        if not path.is_dir():
            self.win.message(f"{path} is not a folder", "error")
            return
        for rec in load_library().values():
            if Path(rec.install_dir) == path:
                self.win.message(f"{rec.name} is already in this library, from that folder", "warning", rec.game_id)
                return
        draft = Draft(path, name=path.name)
        supported = importer.supported_targets()
        detected = [t for t in importer.detect_targets(path) if t in supported]
        if len(supported) == 1:
            draft.target = supported[0]
            self.ask_name(draft)
            return
        order = detected + [t for t in supported if t not in detected]
        options = [(importer.TARGET_LABELS[t] + (" (found in the folder)" if t in detected else ""), t) for t in order]

        def chosen(target: str | None) -> None:
            if target:
                draft.target = target
                self.ask_name(draft)

        self.win.choose(
            "What is this game made for?",
            f"Which system {path.name} runs on decides where its saves are looked for. A Windows game runs in a Wine prefix.",
            options,
            chosen,
        )

    def ask_name(self, draft: Draft) -> None:
        self.win.ask_text(
            "Name of the game",
            "This is how the game is listed on MOG Server. If it is already there, it is found next.",
            draft.name,
            lambda name: self.name_chosen(draft, name),
        )

    def matches(self, name: str) -> list[dict]:
        """The server's games this name may be: not those already on this machine, the ones other machines added included."""
        here = load_library()
        found = []
        for game in self.app.games.values():
            if game["id"] in here:
                continue
            if native.squash(game["name"]) == native.squash(name) or identity.alike(game["name"], name):
                found.append(game)
        return sorted(found, key=lambda g: (native.squash(g["name"]) != native.squash(name), g["name"].casefold()))

    def name_chosen(self, draft: Draft, name: str) -> None:
        draft.name = name
        found = self.matches(name)
        if not found:
            self.ask_library(draft)
            return
        libraries = {lib["id"]: lib["name"] for lib in self.win.library.libraries}

        def describe(game: dict) -> str:
            where = "added from another computer" if game.get("imported") else libraries.get(game.get("library_id"), "on the server")
            return f"{game['name']}  ({where})"

        options = [(describe(g), g) for g in found] + [(f"None of these: add \"{name}\" as a new game", NEW_ENTRY)]

        def chosen(answer) -> None:
            if answer is None:
                return
            if answer == NEW_ENTRY:
                self.ask_library(draft)
            else:
                draft.game = answer
                self.after_entry(draft)

        self.win.choose(
            "Is it one of these games?",
            "MOG Server has games with a name like this. Pick the one this is, so its saves and the ones from your other computers are the same.",
            options,
            chosen,
        )

    # --- the library it is listed under ---

    def ask_library(self, draft: Draft) -> None:
        """Which library of the server the new game is listed under. It is only a label: MOG never writes into a library's folder."""

        def work() -> None:
            try:
                libraries = self.app.client().list_libraries()
            except RuntimeError as e:  # an older server, or no answer: the game is still added, under none
                logstore.warning(f"Could not list the libraries: {e}")
                self.gui(lambda: self.create_entry(draft))
                return
            self.gui(lambda: self.show_libraries(draft, libraries))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not add the game: {m}"))

    def show_libraries(self, draft: Draft, libraries: list[dict]) -> None:
        options = [(f"{lib['name']}   ({lib['root_path']})", lib["id"]) for lib in libraries]
        options += [("New Library...", NEW_LIBRARY), ("No library: only its saves are kept", NO_LIBRARY)]

        def chosen(answer) -> None:
            if answer == NEW_LIBRARY:
                self.new_library(draft, libraries)
            elif answer == NO_LIBRARY:
                draft.library_id = None
                self.create_entry(draft)
            elif answer is not None:
                draft.library_id = answer
                self.create_entry(draft)

        self.win.choose(
            f"Which library is {draft.name} in?",
            f"{draft.name} is listed under this library on MOG Server. MOG does not write into the library's folder: "
            "the game is only associated with it, until its files are added there.",
            options,
            chosen,
        )

    def new_library(self, draft: Draft, libraries: list[dict]) -> None:
        def named(name: str) -> None:
            parent = PurePosixPath(libraries[0]["root_path"]).parent if libraries else PurePosixPath("/library")
            self.win.ask_text(
                "Folder of the library",
                "The folder as the server sees it (a path inside its container, like /library/games). It does not have to "
                "exist yet, and MOG never writes into it.",
                str(parent / name),
                lambda path: make(name, path),
            )

        def make(name: str, path: str) -> None:
            def work() -> None:
                try:
                    library = self.app.client().create_library(name, path)
                except RuntimeError as e:
                    text = f"The library was not made: {e}"
                    self.gui(lambda: (self.win.message(text, "error"), self.ask_library(draft)))
                    return
                draft.library_id = library["id"]
                self.gui(lambda: self.create_entry(draft))

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not make the library: {m}"))

        self.win.ask_text("New Library", "What is the library called?", "", named)

    def create_entry(self, draft: Draft) -> None:
        def work() -> None:
            client = self.app.client()
            game = client.import_game(draft.name, draft.library_id)
            self.app.bridge.games.emit(client.list_games())  # the new entry is in the library before its page is opened
            draft.game = game
            self.gui(lambda: self.after_entry(draft))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not add the game: {m}"))

    def after_entry(self, draft: Draft) -> None:
        if importer.needs_prefix(draft.target):
            self.ask_prefix(draft)
        else:
            self.pick_saves(draft)

    # --- the prefix of a Windows game ---

    def ask_prefix(self, draft: Draft) -> None:
        def chosen(answer: str | None) -> None:
            if answer == BROWSE:
                self.browse_prefix(draft)
            elif answer == NEW_PREFIX:
                self.new_prefix(draft)

        self.win.choose(
            "Where is the Wine prefix of this game?",
            f"A Windows game keeps its saves in the Wine prefix it runs in.\n\n{PREFIX_NOTE}",
            [("Browse Prefix: it already has one", BROWSE), ("New Prefix: make one for this game", NEW_PREFIX)],
            chosen,
        )

    def browse_prefix(self, draft: Draft) -> None:
        options = [
            (f"{p}   (Default: shared with other games)" if prefixes.is_default(p) else str(p), p)
            for p in prefixes.candidate_prefixes()
        ] + [("Another folder...", ANOTHER_FOLDER)]

        def use(path: Path) -> None:
            if not prefixes.is_prefix(path):
                self.win.message(f"{path} does not look like a Wine prefix (no drive_c inside)", "error")
                return
            if prefixes.is_default(path):
                self.win.ask(
                    f"{path} is a Default prefix. Other games run in it, and their saves are in it too.\n"
                    "Only the folders you tick next are taken as this game's saves. Use it anyway?",
                    lambda: self.prefix_chosen(draft, path, False),
                    danger=True,
                )
            else:
                self.prefix_chosen(draft, path, False)

        def chosen(answer) -> None:
            if answer == ANOTHER_FOLDER:
                self.win.browse_folder(Path.home(), lambda picked: use(Path(picked)))
            elif answer is not None:
                use(answer)

        self.win.choose("Which prefix?", PREFIX_NOTE, options, chosen)

    def new_prefix(self, draft: Draft) -> None:
        own = draft.folder / PREFIX_DIR

        def go(path: Path) -> None:
            self.win.ask(
                "A new prefix starts empty, and the game will use it from now on.\n"
                "If the game already has saves, they may be in the prefix it runs in now: this one will not see them.\n"
                f"Make the prefix in {path}?",
                lambda: self.prefix_chosen(draft, path, True),
                danger=True,
            )

        def chosen(answer) -> None:
            if answer == IN_GAME_FOLDER:
                go(own)
            elif answer == ANOTHER_FOLDER:
                self.win.browse_folder(Path.home(), lambda picked: go(Path(picked)))

        self.win.choose(
            "Where should the new prefix be?",
            "The launcher (Faugus, umu, Proton or Wine) makes it the first time the game starts, so you will pick the game's executable too.",
            [(f"In the game's folder: {own}", IN_GAME_FOLDER), ("Another folder...", ANOTHER_FOLDER)],
            chosen,
        )

    def prefix_chosen(self, draft: Draft, path: Path, new: bool) -> None:
        draft.prefix, draft.new_prefix = path, new
        self.pick_saves(draft)

    # --- the folders of the saves ---

    def _scratch(self, draft: Draft) -> InstalledGame:
        return InstalledGame(
            game_id=draft.game["id"], name=draft.game["name"], install_dir=str(draft.folder), imported=True,
            target=draft.target, prefix=str(draft.prefix) if draft.prefix else None,
        )

    def _suggestions(self, rec: InstalledGame, prefix: Path | None) -> list[importer.Suggestion]:
        hints: list[str] = []
        try:
            paths = self.app.client().save_paths(rec.game_id)
            hints = native.manifest_folders(paths or [], Path(rec.install_dir))
        except (RuntimeError, OSError, AttributeError):
            pass
        return importer.suggest(rec, prefix, hints)

    def pick_saves(self, draft: Draft) -> None:
        if draft.new_prefix:  # nothing in it yet: the saves come from the server, or from the first play
            self.finish(draft, [])
            return
        rec = self._scratch(draft)

        def work() -> None:
            found = self._suggestions(rec, draft.prefix)
            self.gui(lambda: self.show_saves(rec, found, [], lambda keys, sandbox: self.finish(draft, keys, sandbox)))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not look for the saves: {m}"))

    def show_saves(
        self,
        rec: InstalledGame,
        found: list[importer.Suggestion],
        current: list[str],
        then: Callable[[list[str], str | None], None],
    ) -> None:
        """Tick the folders that are this game's saves. Those named like the game, and those chosen before, start ticked. `current`
        are rows as `importer.choice_token` makes them; `then(keys, sandbox)` gets the keys and the Flatpak app they are in, if any."""
        shown = {s.token for s in found}
        items = [
            (
                f"{importer.label(s.key, s.sandbox)}   ({s.files} file{'' if s.files == 1 else 's'})",
                s.token,
                s.score == 2 or s.token in current,
            )
            for s in found
        ]
        items += [(importer.label(*importer.split_token(t)), t, True) for t in current if t not in shown]
        can_browse = rec.target != importer.WINDOWS
        if can_browse:
            items.append(("Another folder...", ANOTHER_FOLDER, False))
        note = (
            f"Tick the folders that hold the saves of {rec.name}. Every file in them is kept on the server now, "
            "not only what changes when you play. Nothing ticked is fine if the game has no saves here yet."
            if found or current
            else f"No place that looks like the saves of {rec.name} was found. {'Pick the folder yourself, or ' if can_browse else ''}"
            "carry on with none: saves from your other computers are put in, and later ones are found when the game runs."
        )

        def done(picked: list[str]) -> None:
            ticked = [p for p in picked if p != ANOTHER_FOLDER]
            try:
                keys, sandbox = importer.resolve_choice(ticked)
            except RuntimeError as e:  # folders of two places: say so, and let the choice be made again
                self.win.message(str(e), "error")
                self.show_saves(rec, found, ticked, then)
                return
            if ANOTHER_FOLDER in picked:
                self.browse_saves(rec, found, ticked, then)
            else:
                then(keys, sandbox)

        self.win.checklist("Which folders are this game's saves?", note, items, done)

    def browse_saves(self, rec, found, ticked: list[str], then) -> None:
        def picked(folder: str) -> None:
            sandbox = sandbox_of(Path(folder))  # a folder of a Flatpak app's own: its keys are the usual ones
            key = key_for_folder(Path(folder), Path(rec.install_dir), sandbox)
            if key is None:
                self.win.message(f"{folder} is not a folder below your home folder that can hold a game's saves", "error")
                self.show_saves(rec, found, ticked, then)
                return
            self.show_saves(rec, found, sorted({*ticked, importer.choice_token(key, sandbox)}), then)

        self.win.message(f"Pick the folder {rec.name} keeps its saves in, usually below ~/.config or ~/.local/share", "info")
        self.win.browse_folder(Path.home(), picked)

    def choose_saves(self, rec: InstalledGame, then: Callable[[], None] | None = None) -> None:
        """Change which folders are the saves of a game added from outside MOG (its Saves options)."""
        current = [importer.choice_token(k, rec.in_sandbox) for k in load_state(rec.game_id).includes or []]

        def work() -> None:
            found = self._suggestions(rec, Path(rec.prefix) if rec.prefix else None)

            def done(picked: list[str], sandbox: str | None) -> None:
                importer.remember(rec, picked)
                manager.set_sandbox(rec, sandbox)
                self.win.notify(f"Saves of {rec.name} will be taken from {len(picked)} folder{'' if len(picked) == 1 else 's'}")
                if then:
                    then()

            self.gui(lambda: self.show_saves(rec, found, current, done))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not look for the saves: {m}"))

    # --- recording it, and the first sync ---

    def finish(self, draft: Draft, includes: list[str], sandbox: str | None = None) -> None:
        game = draft.game

        def work() -> None:
            rec = manager.import_game(game, draft.folder, draft.target, draft.prefix, draft.new_prefix, sandbox=sandbox)
            importer.remember(rec, includes, draft.prefix)
            logstore.info(f"{rec.name}: saves taken from {', '.join(includes) or 'no folder yet'}")
            ctx = make_context(rec, self.app.settings, self.app.client(), log=logstore.info)
            if ctx is None:
                why = (
                    "Save sync is switched off for it."
                    if not sync.enabled(rec, self.app.settings)
                    else "Its saves are synced once this computer is known to the server."
                )
                self.gui(lambda: self.added(rec, why, draft))
                return
            outcome = importer.first_sync(ctx)
            self.gui(lambda: self.synced(rec, ctx, outcome, draft))

        self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not add the game: {m}"))

    def synced(self, rec: InstalledGame, ctx: sync.Context, outcome: sync.StartupOutcome, draft: Draft) -> None:
        if outcome.status == "conflict":
            self.settle_conflict(rec, ctx, outcome, draft)
            return
        text = {
            "uploaded": "Its saves are on the server now.",
            "restored": f"Saves from {outcome.from_device} were put in; what they replaced is in a backup.",
            "unchanged": "No saves were found yet: they are backed up as soon as the game writes them.",
            "nothing": "No saves were found yet: they are backed up as soon as the game writes them.",
            "offline": "MOG Server is out of reach: the saves are sent as soon as it is back.",
            "setup": "Its saves are waiting for the prefix to be set.",
            "pending": "Saves from another computer wait for the prefix the game has not made yet.",
        }.get(outcome.status, f"Saves: {outcome.status}.")
        self.added(rec, text, draft)

    def settle_conflict(self, rec: InstalledGame, ctx: sync.Context, outcome: sync.StartupOutcome, draft: Draft) -> None:
        def use_theirs() -> None:
            def work() -> None:
                restored = sync.restore(ctx, outcome.version["id"])
                done = f"Saves from {outcome.from_device} were put in; yours are in a backup." if restored.status == "restored" else (
                    f"The saves from {outcome.from_device} could not be put in ({restored.status})."
                )
                self.gui(lambda: self.added(rec, done, draft))

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not put the saves in: {m}"))

        def keep_mine() -> None:
            def work() -> None:
                settled = importer.settle_keeping_local(ctx, outcome.version["id"])
                self.gui(lambda: self.added(rec, f"Your saves are on the server now ({settled.status}).", draft))

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not send the saves: {m}"))

        self.win.ask(
            f"{rec.name} has saves here, and {outcome.from_device} left others on the server.\n"
            "Yes: use the ones from the server (the files here are copied to a backup first).\n"
            "No: keep the ones here and send them.",
            use_theirs,
            on_no=keep_mine,
            danger=False,
            game=rec.game_id,
        )

    def added(self, rec: InstalledGame, text: str, draft: Draft) -> None:
        self.win.refresh_items()
        self.app.refresh()
        self.win.show_game(rec.game_id)
        self.win.message(f"{rec.name} was added from {rec.install_dir}. {text}", "info", rec.game_id)
        if not rec.executable:  # as if it had just been installed: which file starts it (a new prefix is made by it, too)
            page = self.win.game_pages.get(rec.game_id)
            if page is not None:
                page.choose_executable(rec)

    # --- moving it into MOG ---

    def move_into_mog(self, rec: InstalledGame, on_change: Callable[[], None]) -> None:
        """Move the game's folder, and its prefix if the person wants, into the install folder. Asks before anything is touched."""
        settings = self.app.settings
        dest = manager.move_target(rec, settings)
        old_prefix = Path(rec.prefix) if rec.prefix else None
        outside = old_prefix is not None and not old_prefix.is_relative_to(Path(rec.install_dir))
        shared = outside and not rec.prefix_dedicated
        warning = (
            f"This MOVES {rec.install_dir} to {dest}"
            + (f" and the prefix {old_prefix} into it" if outside else "")
            + ".\nThe originals are deleted from where they are: other programs that started the game from there "
            "(itch.io, Faugus, Steam, Lutris...) will not find it any more.\nDo this only if you know what you are doing."
        )

        def decided(move_prefix: bool) -> None:
            if move_prefix and shared:
                self.win.ask(
                    f"{old_prefix} may be a Default or shared prefix: other games run in it, and moving it takes their "
                    "data and saves away from them too.\nMove it anyway?",
                    lambda: confirmed(True),
                    danger=True,
                    game=rec.game_id,
                )
            else:
                confirmed(move_prefix)

        def confirmed(move_prefix: bool) -> None:
            self.win.saves.final_backup(rec, lambda: self.run_move(rec, move_prefix, on_change), action="Move")

        def after_warning() -> None:
            if not outside:
                decided(False)
                return
            self.win.choose(
                "And the prefix?",
                f"{old_prefix} is outside the game's folder."
                + (" It looks shared with other games." if shared and prefixes.is_default(old_prefix) else ""),
                [("Move it too, into the game's folder", True), ("Leave it where it is", False)],
                lambda answer: decided(bool(answer)) if answer is not None else None,
            )

        self.win.ask(warning, after_warning, danger=True, game=rec.game_id)

    def run_move(self, rec: InstalledGame, move_prefix: bool, on_change: Callable[[], None]) -> None:
        self.win.show_busy("Moving into MOG", rec.name, rec.game_id)

        def work() -> None:
            notes = manager.move_into_mog(rec, self.app.settings, move_prefix, self.app.settings.launcher)

            def done() -> None:
                self.win.hide_busy()
                self.win.refresh_items()
                on_change()
                self.win.message(f"{rec.name} is a MOG game now, in {rec.install_dir}. " + " ".join(notes), "info", rec.game_id)

            self.gui(done)

        def failed(message: str) -> None:
            self.win.hide_busy()
            self.win.message(f"Could not move {rec.name}: {message}", "error", rec.game_id)
            on_change()

        self.app.run_bg(work, on_error=failed)

    # --- taking it out of MOG ---

    def remove(self, rec: InstalledGame, on_change: Callable[[], None]) -> None:
        """Take the game out of MOG after a last backup of its saves. The folder and the prefix are never deleted."""

        def go() -> None:
            def work() -> None:
                manager.remove_import(rec)

                def done() -> None:
                    self.win.refresh_items()
                    on_change()
                    self.win.message(f"{rec.name} was taken out of MOG. Its folder and prefix were not touched; its saves stay on the server.", "info")

                self.gui(done)

            self.app.run_bg(work, on_error=lambda m: self.app.bridge.error.emit(f"Could not remove {rec.name}: {m}"))

        self.win.ask(
            f"Take {rec.name} out of MOG on this computer? Its folder and its prefix are not deleted, and its saves stay on the server.",
            lambda: self.win.saves.final_backup(rec, go, action="Remove"),
            game=rec.game_id,
        )
