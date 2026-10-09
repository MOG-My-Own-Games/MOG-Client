"""The questions and messages of save sync, between the window and mog_client.saves.

Nothing here touches Qt: the window supplies a few calls (`choose`, `checklist`, `ask`, `notify`,
`browse_folder`) and `app.bridge.call` to run something on the GUI thread, so the flows can be
tested with a stand-in window. Work that talks to the server runs on worker threads.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mog_client import installdirs, logstore
from mog_client.config import InstalledGame, load_library
from mog_client.launcher import detect_launcher, effective_launcher, pfx_dir
from mog_client.saves import devices, native, sync
from mog_client.saves import prefix as prefixes
from mog_client.saves.locations import describe_key
from mog_client.saves.state import load_state

SKIP = None
PROGRESS_EVERY = 0.1  # seconds between updates of the progress shown while a save downloads


class _Cancelled(Exception):
    """The user cancelled a restore that was downloading."""


@dataclass
class RestoreJob:
    """A restore the user asked for, listed with the other tasks while it runs."""

    cancel: threading.Event = field(default_factory=threading.Event)
    percent: int = 0
    applying: bool = False  # the files are being put back: too late to cancel, and the game must not start meanwhile


# The entry of the folder list that opens the folder browser.
OTHER_FOLDER = "*other*"


def when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def when_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


_CHECK_RESULT = {
    "uploaded": "saves uploaded",
    "duplicate": "saves already on the server",
    "unchanged": "up to date, nothing new",
    "nothing": "no save files found",
    "needs-prefix": "needs the prefix to be set",
    "needs-confirmation": "needs the save folders to be confirmed",
    "needs-folder": "needs the folder it saves in",
    "failed": "failed, the server could not be reached",
}
_CHECK_TRIGGER = {"quit": "after the game closed", "launch": "at start", "manual": "by hand", "sync": "at start", "uninstall": "on uninstall"}


def describe_check(state) -> str:
    """The last time this machine looked at a game's saves for a backup, and what came of it."""
    if not state.last_check_at:
        return "Not yet"
    result = _CHECK_RESULT.get(state.last_check_status or "", state.last_check_status or "")
    trigger = _CHECK_TRIGGER.get(state.last_check_trigger or "")
    return f"{when(state.last_check_at)}: {result}" + (f" ({trigger})" if trigger else "")


def version_label(version: dict, device: dict) -> str:
    count = version.get("file_count")
    files = f", {count} file{'' if count == 1 else 's'}" if count is not None else ""
    return f"{device['name']}: {when(version['created_at'])} ({version.get('trigger', 'sync')}{files})"


class SaveSync:
    def __init__(self, win) -> None:
        self.win = win
        self._checked = False
        self.restoring: dict[int, RestoreJob] = {}  # game id -> its restore, while it runs
        self.uploading: set[int] = set()  # games whose saves are being backed up by hand

    # --- plumbing ---

    @property
    def app(self):
        return self.win.app

    def gui(self, fn: Callable[[], None]) -> None:
        self.app.bridge.call.emit(fn)

    def say(self, text: str, level: str | None = None) -> None:
        """From any thread: a message over the window when it has a level, else just the log."""
        self.gui(lambda: self.win.message(text, level) if level else self.win.notify(text))

    def enabled(self, rec: InstalledGame) -> bool:
        return sync.enabled(rec, self.app.settings)

    def with_context(
        self,
        rec: InstalledGame,
        then: Callable[[sync.Context], None],
        otherwise: Callable[[str | None], None] | None = None,
        ask: bool = False,
    ) -> None:
        """Run `then(ctx)` on a worker thread once this machine is known to the server. When that
        cannot be (sync off, no server, a name to settle) `otherwise(reason)` runs on the GUI
        thread instead; `ask` allows asking the user to settle the machine's name."""
        app = self.app

        def fail(reason: str | None) -> None:
            if otherwise is not None:
                self.gui(lambda: otherwise(reason))
            elif reason:
                self.say(f"Save sync: {reason}", "error")

        def work() -> None:
            settings = app.settings
            if not sync.enabled(rec, settings):
                return fail("saving is off for this game" if ask else None)
            client = app.client()
            device = devices.known_device()
            if device is None:
                try:
                    device = devices.register(client)
                except devices.HostnameTaken as taken:
                    if ask:
                        self.gui(lambda t=taken: self._ask_identity(t, rec, then, otherwise))
                        return
                    return fail("this machine has no name on the server yet; open the game's Options to set it up")
                except (RuntimeError, devices.NameTaken) as e:
                    return fail(str(e))
            engine = detect_launcher(effective_launcher(rec, settings.launcher)) or "auto"
            then(sync.Context(rec, settings, client, device, engine))

        app.run_bg(work, on_error=lambda m: fail(m))

    def _ask_identity(self, taken: devices.HostnameTaken, rec, then, otherwise) -> None:
        options = [(f"Yes, this is {d['name']}", ("adopt", d["id"])) for d in taken.devices]
        options += [(f"No, a new machine: call it {n}", ("name", n)) for n in taken.suggested_names]

        def chosen(answer) -> None:
            kind, value = answer

            def work() -> None:
                client = self.app.client()
                if kind == "adopt":
                    devices.register(client, adopt_device_id=value)
                else:
                    devices.register(client, name=value)
                self.with_context(rec, then, otherwise)

            self.app.run_bg(work, on_error=lambda m: self.say(f"Save sync: {m}", "error"))

        self.win.choose(
            "Is this machine one of these?",
            f"The server already has a machine named '{devices.hostname()}'. Say so if this is the same "
            "computer (or you reinstalled its system), so its saves stay together; otherwise it gets a name of its own.",
            options,
            chosen,
        )

    # --- backing up ---

    def backup_now(self, rec: InstalledGame) -> None:
        """Back the saves up, as a task in the sidebar (and a ring on the game's page) rather than over the window."""
        gid = rec.game_id

        def listed(on: bool) -> None:
            (self.uploading.add if on else self.uploading.discard)(gid)
            self.win.refresh_tasks()

        def run(ctx: sync.Context) -> None:
            if gid in self.uploading:
                return
            self.gui(lambda: listed(True))
            try:
                result = sync.backup(ctx, sync.MANUAL, force=True)
            except Exception as e:  # noqa: BLE001 - shown to the user
                text = f"Could not back up: {e}"  # `e` is gone once this block ends, before the GUI thread runs the lambda
                self.gui(lambda: (listed(False), self.win.message(text, "error")))
                return
            self.gui(lambda: (listed(False), self.after_backup(rec, result, lambda: self.backup_now(rec), quiet=False)))

        self.with_context(rec, run, ask=True)

    def after_backup(self, rec: InstalledGame, result: sync.BackupResult, retry: Callable[[], None], quiet: bool) -> None:
        status = result.status
        if status == "uploaded":
            count = (result.version or {}).get("file_count")
            text = f"Saves of {rec.name} backed up" + (f" ({count} files)" if count else "")
            self.win.notify(text) if quiet else self.win.message(text, "info")
        elif status in ("unchanged", "duplicate") and not quiet:
            self.win.message(f"Saves of {rec.name} are up to date", "info")
        elif status == "nothing" and not quiet:
            self.win.message(f"No save files found for {rec.name} yet", "info")
        elif status == "needs-prefix":
            if quiet:
                self.win.message(f"Where does {rec.name} keep its saves? Open its Options > Where is the prefix", "warning")
            else:
                self.choose_prefix(rec, retry)
        elif status == "needs-confirmation":
            if quiet:
                self.win.message(f"Confirm which folders are {rec.name}'s saves: Options > Back up saves now", "warning")
            else:
                self.confirm_folders(rec, result.folders, retry, native=rec.native)
        elif status == "needs-folder" and not quiet:
            self.choose_folder(rec, retry)

    def choose_prefix(self, rec: InstalledGame, then: Callable[[], None] | None = None, skip: Callable[[], None] | None = None) -> None:
        """Ask where the game's Wine prefix is. This only tells save sync where to look; it does not
        change how the game is started."""
        own = pfx_dir(rec)
        options: list[tuple[str, object]] = [("Game's Folder (Default)", own)]
        options += [(str(p), p) for p in prefixes.candidate_prefixes() if p.resolve() != own.resolve()]
        options.append(("Another folder...", "browse"))

        def store(path: Path) -> None:
            # The game's own folder is taken as it is: the prefix is made there when the game first runs.
            if path != own and not prefixes.is_prefix(path):
                self.win.message(f"{path} does not look like a Wine prefix (no drive_c inside)", "error")
                return
            sync.remember_prefix(rec.game_id, path, prefixes.FROM_USER)
            self.win.notify(f"Saves of {rec.name} will be looked for in {path}")
            if then:
                then()

        def chosen(answer) -> None:
            if answer == "browse":
                self.win.browse_folder(Path.home(), lambda picked: store(Path(picked)))
            elif answer is SKIP:
                if skip:
                    skip()
            else:
                store(answer)

        self.win.choose(
            "Where is this game's Wine prefix?",
            f"MOG could not find the Wine prefix {rec.name} runs in. The default is the pfx folder inside the game's "
            "own folder. If you start the game some other way, pick the prefix it uses. This is kept on this "
            "computer only; the server is never told.",
            options,
            chosen,
            skip="Not now" if skip is not None else None,
        )

    def confirm_folders(
        self, rec: InstalledGame, folders: list[str], then: Callable[[], None], native: bool = False
    ) -> None:
        if native:
            return self._confirm_native_folders(rec, folders, then)

        def done(picked: list[str]) -> None:
            sync.confirm_folders(rec.game_id, picked)
            then()

        self.win.checklist(
            "Which folders are this game's saves?",
            f"The prefix is shared with other programs. Tick the folders that belong to {rec.name}.",
            [(f, f) for f in folders],
            done,
        )

    def _confirm_native_folders(self, rec: InstalledGame, folders: list[str], then: Callable[[], None]) -> None:
        """The folders a native game wrote to while it ran. The ones named like the game start ticked."""
        names = native.game_names(rec)
        items = [(describe_key(f), f, native.folder_score(f, names) == 2) for f in folders] + [("Another folder...", OTHER_FOLDER)]

        def done(picked: list[str]) -> None:
            sync.confirm_folders(rec.game_id, [p for p in picked if p != OTHER_FOLDER])
            if OTHER_FOLDER in picked:
                self.choose_folder(rec, then)
            else:
                then()

        self.win.checklist(
            "Which folders are this game's saves?",
            f"{rec.name} wrote to these while it ran, and other programs may have too. Tick the ones that "
            "hold its saves; the ones named like the game are ticked already.",
            items,
            done,
        )

    def choose_folder(self, rec: InstalledGame, then: Callable[[], None] | None = None) -> None:
        """Ask for a folder a native game keeps its saves in (MOG found none, or not the right one)."""

        def store(picked: str) -> None:
            if sync.add_folder(rec.game_id, Path(picked)) is None:
                self.win.message(f"{picked} is not a folder below your home folder that can hold a game's saves", "error")
                return
            self.win.notify(f"Saves of {rec.name} will be backed up from {picked}")
            if then:
                then()

        self.win.message(f"Pick the folder {rec.name} keeps its saves in, usually below ~/.config or ~/.local/share", "info")
        self.win.browse_folder(Path.home(), store)

    # --- before the game starts ---

    def before_launch(self, rec: InstalledGame, go: Callable[[], None]) -> None:
        """Offer a newer save from another machine, or a restore that was waiting for the prefix,
        then `go()`. Never blocks starting the game on a failure."""
        if not self.enabled(rec):
            go()
            return

        def run(ctx: sync.Context) -> None:
            try:
                newer = sync.newer_elsewhere(ctx)
            except RuntimeError:
                newer = None
            pending = bool(load_state(rec.game_id).pending_restore)
            self.gui(lambda: self._offer(rec, ctx, newer, pending, go))

        self.with_context(rec, run, otherwise=lambda _reason: go())

    def _offer(self, rec, ctx, newer, pending: bool, go) -> None:
        if newer is not None:
            version, device = newer
            self.win.choose(
                "A newer save exists",
                f"{device['name']} saved {rec.name} on {when(version['created_at'])}. Use it on this machine? "
                "What it replaces is copied to a backup first.",
                [
                    ("Use it", "use"),
                    ("Keep this machine's saves", "keep"),
                    ("Ask me next time", "later"),
                ],
                lambda answer: self._newer_chosen(rec, ctx, version, answer, go),
            )
        elif pending:
            self._finish_pending(rec, ctx, go)
        else:
            go()

    def _newer_chosen(self, rec, ctx, version, answer, go) -> None:
        if answer == "use":
            self._restore(rec, ctx, version["id"], go)
            return
        if answer == "keep":
            sync.decline(ctx, version["id"])
        go()

    def _finish_pending(self, rec, ctx, go) -> None:
        state = load_state(rec.game_id)
        if sync.find_prefix(ctx, state) is None:
            self.choose_prefix(rec, then=lambda: self.before_launch(rec, go), skip=go)
            return
        if not sync.prefix_ready(ctx, state):
            # The game makes its prefix on this start; the saves go in at the next one.
            self.win.notify(f"Saves for {rec.name} are waiting: close the game once it has started, then start it again")
            go()
            return

        def run() -> None:
            result = sync.apply_pending(ctx, only_if_free=True)
            if result is None and load_state(rec.game_id).pending_restore:
                self.gui(lambda: self._pending_conflict(rec, ctx, go))
            else:
                self.gui(go)

        self.app.run_bg(run, on_error=lambda m: self.gui(go))

    def _pending_conflict(self, rec, ctx, go) -> None:
        self.win.ask(
            f"Saves from another machine are waiting for {rec.name}, but this one already has files of its own. "
            "Replace them? They are copied to a backup first.",
            lambda: self._restore_pending(rec, ctx, go),
            on_no=go,
        )

    def _restore_pending(self, rec, ctx, go) -> None:
        def run() -> None:
            sync.apply_pending(ctx)
            self.gui(go)

        self.app.run_bg(run, on_error=lambda m: self.gui(go))

    # --- restoring ---

    def _restore(self, rec: InstalledGame, ctx: sync.Context, version_id: int, then: Callable[[], None] | None = None) -> None:
        """Put a version back. On its own it is a task in the sidebar that Play waits for; with `then` (the game is
        about to start) it covers the window instead, and `then` runs once it is over."""
        if rec.game_id in self.restoring:
            self.win.message(f"The saves of {rec.name} are already being restored", "warning")
            return
        job = RestoreJob()
        foreground = then is not None
        if foreground:
            self.win.show_busy("Restoring saves", rec.name, rec.game_id, job.cancel.set)
        else:
            self.restoring[rec.game_id] = job
            self.win.refresh_tasks()
        last = [0.0]

        def shown(done: int, total: int) -> None:
            job.percent = done * 100 // total if total else 0
            if foreground:
                self.win.busy_progress(done, total, None if done < total else "Putting the files back...")
            else:
                self.win.refresh_tasks()

        def progress(done: int, total: int) -> None:
            if job.cancel.is_set() and not job.applying:
                raise _Cancelled
            job.applying = bool(total) and done >= total
            now = time.monotonic()
            if now - last[0] >= PROGRESS_EVERY or done == total:
                last[0] = now
                self.gui(lambda: shown(done, total))

        def over() -> None:
            self.restoring.pop(rec.game_id, None)
            if foreground:
                self.win.hide_busy()
            else:
                self.win.refresh_tasks()

        def run() -> None:
            try:
                result = sync.restore(ctx, version_id, progress)
            except _Cancelled:
                self.gui(lambda: (over(), self.win.notify("Restore cancelled"), then() if then else None))
                return
            self.gui(lambda: (over(), self._restored(rec, ctx, result, then)))

        def failed(m: str) -> None:
            self.gui(lambda: (over(), self.win.message(f"Could not restore: {m}", "error"), then() if then else None))

        self.app.run_bg(run, on_error=failed)

    def cancel_restore(self, game_id: int) -> bool:
        """Stop a restore that is still downloading; False when there is none or it is already putting files back."""
        job = self.restoring.get(game_id)
        if job is None or job.applying:
            return False
        job.cancel.set()
        return True

    def wait_for_restore(self, rec: InstalledGame, play_anyway: Callable[[], None]) -> bool:
        """Called when Play is pressed. True when a restore of this game is running, and the user has been told: asked
        whether to cancel it and play anyway while it downloads, only told to wait while it puts the files back."""
        job = self.restoring.get(rec.game_id)
        if job is None:
            return False
        if job.applying:
            self.win.message(f"The saves of {rec.name} are being put back. Try again in a moment.", "warning")
            return True

        def cancel_and_play() -> None:
            if self.cancel_restore(rec.game_id):
                self.restoring.pop(rec.game_id, None)  # the download stops by itself; the game does not wait for it
                self.win.refresh_tasks()
                play_anyway()
            else:
                self.win.message(f"The saves of {rec.name} are being put back. Try again in a moment.", "warning")

        self.win.ask(
            f"The saves of {rec.name} are still downloading ({job.percent}%). Cancel that and play with the saves "
            "on this machine instead?",
            cancel_and_play,
        )
        return True

    def _restored(self, rec, ctx, result: sync.RestoreResult, then) -> None:
        if result.status == "restored":
            logstore.info(
                f"Saves of {rec.name} restored: {len(result.restored)} file(s): {', '.join(result.restored) or 'none'}"
                + (f"; what they replaced is in {result.backup}" if result.backup else "")
            )
            self._keep_notice(rec, result)
            self.win.message(f"Saves of {rec.name} restored. Check the log for more information.", "info")
            if then:
                then()
        elif result.status == "incompatible":
            self.win.message(f"That save of {rec.name} is for the other version of the game (Windows or Linux)", "warning")
        elif result.status == "needs-prefix" and sync.find_prefix(ctx, load_state(rec.game_id)) is not None:
            # The game's own prefix, which it makes the first time it runs: nothing to ask, the saves wait for it.
            self.win.message(
                f"The saves of {rec.name} are kept for when the game has made its prefix. Start the game once, close it "
                "and start it again: they are put in then.",
                "info",
            )
            if then:
                then()
        elif result.status == "needs-prefix":
            def apply() -> None:
                def work() -> None:
                    if done := sync.apply_pending(ctx):
                        self.gui(lambda: self._keep_notice(rec, done))
                        self.say(f"Saves of {rec.name} restored", "info")
                    else:
                        self.say(
                            f"Saves of {rec.name} are kept. Start the game once, close it and start it again: they are put in then"
                        )
                    if then:
                        self.gui(then)

                def failed(m: str) -> None:
                    self.say(f"Could not restore: {m}", "error")
                    if then:
                        self.gui(then)

                self.app.run_bg(work, on_error=failed)

            def skip() -> None:
                self.win.notify("They stay downloaded until the prefix is known")
                if then:
                    then()

            self.choose_prefix(rec, then=apply, skip=skip)
        else:
            self.win.message(f"Saves of {rec.name} could not be restored ({result.status})", "error")

    def _keep_notice(self, rec: InstalledGame, result: sync.RestoreResult) -> None:
        """A server that cannot keep the notice of a restore (an older one) leaves the notifications silent, so this
        client lists it itself."""
        if not result.notified:
            count = len(result.restored)
            self.win.add_local_notification(
                "save_restored", f"Saves restored: {rec.name}", f"On this machine, {count} file{'' if count == 1 else 's'}.", rec.game_id
            )

    def restore_pick(self, rec: InstalledGame) -> None:
        """Choose any saved version, of any machine, to put back."""

        def run(ctx: sync.Context) -> None:
            rows = sync.versions_of(ctx)
            self.gui(lambda: self._pick(rec, ctx, rows))

        self.with_context(rec, run, ask=True)

    def _pick(self, rec, ctx, rows) -> None:
        if not rows:
            self.win.message(f"No saves of {rec.name} on the server yet", "info")
            return
        self.win.choose(
            "Restore which save?",
            f"Put a saved version of {rec.name} back on this machine. What it replaces is copied to a backup first.",
            [(version_label(v, d), v["id"]) for v, d in rows],
            lambda version_id: self._restore(rec, ctx, version_id),
            confirm=True,
        )

    def offer_after_install(self, rec: InstalledGame) -> None:
        """A just-installed game: put back the newest saves another machine left, if the user wants."""
        if not self.enabled(rec):
            return

        def run(ctx: sync.Context) -> None:
            rows = sync.versions_of(ctx)
            newest: dict[int, tuple[dict, dict]] = {}
            for v, d in rows:  # newest first
                newest.setdefault(d["id"], (v, d))
            if newest:
                self.gui(lambda: self._offer_install(rec, ctx, list(newest.values())))

        self.with_context(rec, run, otherwise=lambda _reason: None, ask=True)

    def _offer_install(self, rec, ctx, rows) -> None:
        options = [(f"Restore {version_label(v, d)}", v["id"]) for v, d in rows]
        self.win.choose(
            "Saves from another machine",
            f"The server has saves of {rec.name}. Put one on this machine now?",
            options,
            lambda version_id: self._restore(rec, ctx, version_id) if version_id is not SKIP else None,
            skip="Skip",
            confirm=True,
        )

    # --- uninstalling, and looking over every game at startup ---

    def final_backup(self, rec: InstalledGame, then: Callable[[], None]) -> None:
        """Back the game's saves up one last time, then `then()`; if that cannot be done, ask first."""
        if not self.enabled(rec):
            then()
            return

        def uneasy(reason: str | None) -> None:
            self.win.ask(
                f"The saves of {rec.name} could not be backed up ({reason or 'no reason given'}). Uninstall anyway?",
                then,
                danger=True,
            )

        def run(ctx: sync.Context) -> None:
            result = sync.backup(ctx, sync.UNINSTALL, force=True)
            if result.status in ("uploaded", "duplicate", "unchanged", "nothing"):
                self.gui(then)
            else:
                self.gui(lambda: uneasy("it is not clear which files are its saves"))

        self.with_context(rec, run, otherwise=uneasy)

    def check_all(self) -> None:
        """Once per session, when the setting allows: look over every game's saves. What changed here is
        sent; a newer version from another machine is taken when nothing changed here (the files it
        replaces are backed up first); when both changed, the user is told and nothing is overwritten."""
        settings = self.app.settings
        if self._checked or not settings.sync_saves or not settings.sync_on_start or not settings.configured:
            return
        self._checked = True
        games = [
            r for r in load_library().values() if r.state == "installed" and r.executable and installdirs.present(r) and self.enabled(r)
        ]
        if not games:
            return

        def run(ctx: sync.Context) -> None:
            groups: dict[str, list[str]] = {"uploaded": [], "restored": [], "conflict": [], "setup": [], "pending": []}
            for rec in games:
                one = sync.Context(rec, ctx.settings, ctx.client, ctx.device, ctx.engine)
                try:
                    outcome = sync.startup_check(one)
                except (RuntimeError, OSError) as e:
                    logstore.warning(f"Saves of {rec.name}: {e}")
                    continue
                if outcome.status in groups:
                    who = f" (from {outcome.from_device})" if outcome.from_device else ""
                    groups[outcome.status].append(f"{rec.name}{who}" if outcome.status != "uploaded" else rec.name)
            labels = {
                "uploaded": "backed up",
                "restored": "newer saves put back",
                "conflict": "changed here and elsewhere, nothing overwritten: open the game to choose",
                "setup": "needs setup",
                "pending": "waiting for the prefix",
            }
            parts = [f"{labels[k]}: {', '.join(v)}" for k, v in groups.items() if v]
            if parts:  # a window only for what needs the user now; a conflict is asked when the game is opened
                self.say("Saves: " + "; ".join(parts), "warning" if groups["setup"] else None)

        first = games[0]
        self.with_context(first, run, ask=True, otherwise=lambda _reason: None)
