"""The save-sync flows the GUI and the command line both run, with the settings and server wired in."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from mog_client import played
from mog_client.api import MogClient
from mog_client.config import InstalledGame, Settings, load_library
from mog_client.gui.saves_ui import when
from mog_client.launcher import detect_launcher, effective_launcher
from mog_client.saves import devices, sync, watcher
from mog_client.saves.devices import HostnameTaken
from mog_client.saves.state import load_state
from mog_client.saves.watcher import LINGER, wait_for_game

Log = Callable[[str], None]
RUNTIME = "runtime"
END_POLL = 1.0  # how often a watched game is looked for while waiting for its end


def make_context(
    rec: InstalledGame, settings: Settings, client: MogClient, register: bool = True, log: Log = print
) -> sync.Context | None:
    """The context for one game, or None when syncing is off for it or this machine is not known to
    the server yet (and could not be registered without asking the user, see HostnameTaken)."""
    if not sync.enabled(rec, settings):
        return None
    device = devices.known_device()
    if device is None and register:
        try:
            device = devices.register(client)
        except HostnameTaken:
            log("this machine's name is already used on the server: open MOG to say whether it is the same machine")
            return None
        except (RuntimeError, devices.NameTaken) as e:
            log(f"save sync: {e}")
            return None
    if device is None:
        return None
    engine = detect_launcher(effective_launcher(rec, settings.launcher)) or "auto"
    return sync.Context(rec, settings, client, device, engine)


def runtime_prefix_recorder(ctx: sync.Context) -> Callable[[Path], None]:
    """What to do with the prefix seen in a running game's environment: keep it, but only when
    nothing else names one (it would otherwise replace the right one and drop what is tracked)."""

    def found(prefix: Path) -> None:
        if sync.find_prefix(ctx, load_state(ctx.rec.game_id)) is None:
            sync.remember_prefix(ctx.rec.game_id, prefix, RUNTIME)

    return found


def await_game_end(
    rec: InstalledGame,
    settings: Settings,
    client: MogClient,
    stop: Callable[[], bool] = lambda: False,
    log: Log = print,
    **timing,
) -> sync.Context | None:
    """Follow a game from its start to its end. The context to back up with, or None when saves are not
    synced for it or the game never started. `timing` goes to `wait_for_game`."""
    ctx = make_context(rec, settings, client, log=log)
    if ctx is None:
        return None
    on_prefix = None if rec.native else runtime_prefix_recorder(ctx)
    if not wait_for_game(Path(rec.install_dir), on_prefix=on_prefix, stop=stop, native=rec.native, **timing):
        return None
    return ctx


def watch_session(
    rec: InstalledGame,
    settings: Settings,
    client: MogClient,
    since_ns: int,
    stop: Callable[[], bool] = lambda: False,
    log: Log = print,
) -> sync.BackupResult | None:
    """Follow a game from its start to its end, then back up what it wrote. Meant to run on its own."""
    ctx = await_game_end(rec, settings, client, stop, log)
    if ctx is None:
        return None
    result = sync.backup(ctx, sync.QUIT, since_ns=since_ns)
    log(f"save sync: {result.status}")
    return result


PRE_TIMEOUT = 8.0  # seconds the server has to answer before a game starts; a game never waits long for it


def pre_launch(rec: InstalledGame, settings: Settings, client: MogClient, log: Log = print) -> None:
    """Before a game starts on its own (a shortcut, not MOG): put back a restore that was waiting
    for the prefix (but only where the game has not made files of its own since), then take a newer
    save another machine left. When both sides changed, the user is asked which to keep."""
    ctx = make_context(rec, settings, client, register=False, log=log)
    if ctx is None:
        return
    sync.apply_pending(ctx, only_if_free=True)
    try:
        newer = sync.newer_elsewhere(ctx)
    except RuntimeError:
        return  # the server cannot be reached: the game starts with what is here
    if newer is not None:
        with_window(rec, True, lambda ui: take_newer(ctx, ui, log), describe_start)


def take_newer(ctx: sync.Context, ui, log: Log = print) -> sync.StartupOutcome | None:
    """Put the newer save back, or ask which side wins when both changed. Without a window to ask in,
    nothing is decided: the same question comes back at the next start."""
    outcome = sync.apply_newer(ctx)
    if outcome is None or outcome.status != "conflict":
        return outcome
    version, device = outcome.version, outcome.from_device
    answer = ui.ask(
        "A newer save exists",
        f"{device} saved {ctx.rec.name} on {when(version['created_at'])}, and the saves here changed too. "
        "Use that one? What it replaces is copied to a backup first.",
        [("Use it", "use"), ("Keep this machine's saves", "keep"), ("Ask me next time", "later")],
    )
    if answer == "use":
        result = sync.restore(ctx, version["id"])
        status = "restored" if result.status == "restored" else "pending"
        return sync.StartupOutcome(status, device, version, len(result.restored or []))
    if answer == "keep":
        sync.decline(ctx, version["id"])
        return sync.StartupOutcome("kept", device, version)
    log("save sync: a newer save is waiting, to be decided later")
    return outcome


OK_STATUSES = ("uploaded", "duplicate", "unchanged")
STATUS_TEXT = {
    "uploaded": "Saves backed up",
    "duplicate": "Saves already backed up",
    "unchanged": "Saves already up to date",
    "needs-confirmation": "Some folders need your confirmation: open MOG",
    "needs-prefix": "Could not find the game's saves: open MOG",
    "needs-folder": "Tell MOG where the game keeps its saves: open it",
    "nothing": "No saves found for this game",
}


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, f"Save sync: {status}")


def sync_now(rec: InstalledGame, settings: Settings, client: MogClient) -> sync.BackupResult | None:
    """Back a game up now. None when saving is off for it or this machine is not registered."""
    ctx = make_context(rec, settings, client)
    return None if ctx is None else sync.backup(ctx, sync.MANUAL, force=True)


def report_sync(result: sync.BackupResult | None, log: Log = print) -> int:
    """Print what `sync_now` found and return the command's exit code."""
    if result is None:
        log("save sync is off for this game, or this machine is not registered")
        return 1
    log(f"save sync: {result.status}")
    if result.status == "needs-confirmation":
        log("these folders hold files that may be this game's; confirm them in MOG:")
        log("\n".join(f"  {f}" for f in result.folders))
        return 2
    return 0 if result.status in OK_STATUSES else 1


def describe(result: sync.BackupResult | None) -> tuple[str, bool]:
    """The line the window shows for a backup, and whether it went well."""
    if result is None:
        return "Saves are not synced for this game", False
    return status_text(result.status), result.status in OK_STATUSES


def describe_start(outcome: sync.StartupOutcome | None) -> tuple[str, bool]:
    """The same for what happened to a newer save at the start of a game."""
    if outcome is None:
        return "Saves are up to date", True
    text = {
        "restored": f"Saves from {outcome.from_device} restored",
        "kept": "Keeping this machine's saves",
        "conflict": "The choice is asked again next time",
        "pending": "The saves go in once the game's folder exists",
        "setup": "Could not find the game's saves: open MOG",
    }.get(outcome.status, f"Save sync: {outcome.status}")
    return text, outcome.status != "setup"


class NoWindow:
    """What the work is given when no window is shown: nothing to hide, and nobody to ask."""

    def __call__(self, visible: bool) -> None:
        pass

    def say(self, text: str) -> None:
        pass

    def ask(self, title: str, text: str, options: list[tuple[str, object]]):
        return None


def window_available() -> bool:
    """Whether the window can be shown here: PySide6 is installed and there is a display."""
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return importlib.util.find_spec("PySide6") is not None


def with_window(rec: InstalledGame, wanted: bool, work: Callable, describe_result=describe):
    """Run `work`, behind the small "Syncing saves" window when it is wanted and can be shown. `work` is given
    a control to hide and show the window and to ask the user something (`NoWindow` when there is none)."""
    if wanted and window_available():
        from mog_client.gui import syncwindow  # Qt stays out of a command that never shows the window

        return syncwindow.run(rec, work, describe_result)
    return work(NoWindow())


def confirm_ended(rec: InstalledGame, set_visible: Callable[[bool], None], linger: float = LINGER) -> None:
    """The game's processes are gone: wait out the gaps a launcher leaves between handing over to the game
    and its helpers. If it comes back, the window is hidden until it really ends."""
    deadline = time.monotonic() + linger
    while time.monotonic() < deadline:
        if watcher.running_pids(Path(rec.install_dir), exclude=(os.getpid(),), native=rec.native):
            set_visible(False)
            wait_for_game(Path(rec.install_dir), native=rec.native, linger=linger, poll=END_POLL)
            set_visible(True)
            return
        time.sleep(END_POLL / 2)


def run_command(action: str, game_id: int, settings: Settings, client: MogClient, window: bool = False) -> int:
    """`--save-pre`, `--save-watch` and `--save-sync` from the command line. A watch shows the window
    when the setting is on, a sync only when asked for with `window`."""
    rec = load_library().get(game_id)
    if rec is None or rec.state != "installed":
        print(f"game {game_id} is not installed")
        return 1
    try:
        if action == "pre":
            client.c.timeout = PRE_TIMEOUT
            played.record(game_id)  # a start from a shortcut or Steam too, which is where plays are not seen otherwise
            pre_launch(rec, settings, client)
        elif action == "watch":
            since_ns = time.time_ns()
            # The window opens as soon as the game's processes are gone, and the wait for stragglers happens behind it.
            if (ctx := await_game_end(rec, settings, client, linger=0.0, poll=END_POLL)) is not None:

                def backup(set_visible: Callable[[bool], None]) -> sync.BackupResult:
                    confirm_ended(rec, set_visible)
                    return sync.backup(ctx, sync.QUIT, since_ns=since_ns)

                result = with_window(rec, window or settings.sync_window, backup)
                print(f"save sync: {result.status}")
        else:
            return report_sync(with_window(rec, window, lambda _set_visible: sync_now(rec, settings, client)))
    except RuntimeError as e:
        print(f"save sync: {e}")
        return 1
    return 0
