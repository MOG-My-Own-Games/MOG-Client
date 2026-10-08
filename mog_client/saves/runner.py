"""The save-sync flows the GUI and the command line both run, with the settings and server wired in."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from mog_client import played
from mog_client.api import MogClient
from mog_client.config import InstalledGame, Settings, load_library
from mog_client.launcher import detect_launcher, effective_launcher
from mog_client.saves import devices, sync
from mog_client.saves.devices import HostnameTaken
from mog_client.saves.state import load_state
from mog_client.saves.watcher import wait_for_game

Log = Callable[[str], None]
RUNTIME = "runtime"


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


def watch_session(
    rec: InstalledGame,
    settings: Settings,
    client: MogClient,
    since_ns: int,
    stop: Callable[[], bool] = lambda: False,
    log: Log = print,
) -> sync.BackupResult | None:
    """Follow a game from its start to its end, then back up what it wrote. Meant to run on its own."""
    ctx = make_context(rec, settings, client, log=log)
    if ctx is None:
        return None

    on_prefix = None if rec.native else runtime_prefix_recorder(ctx)
    if not wait_for_game(Path(rec.install_dir), on_prefix=on_prefix, stop=stop, native=rec.native):
        return None
    result = sync.backup(ctx, sync.QUIT, since_ns=since_ns)
    log(f"save sync: {result.status}")
    return result


def pre_launch(rec: InstalledGame, settings: Settings, client: MogClient, log: Log = print) -> None:
    """Before a game starts on its own (a shortcut, not MOG): put back a restore that was waiting
    for the prefix, but only where the game has not made files of its own since."""
    ctx = make_context(rec, settings, client, register=False, log=log)
    if ctx is not None:
        sync.apply_pending(ctx, only_if_free=True)


def run_command(action: str, game_id: int, settings: Settings, client: MogClient) -> int:
    """`--save-pre`, `--save-watch` and `--save-sync` from the command line."""
    rec = load_library().get(game_id)
    if rec is None or rec.state != "installed":
        print(f"game {game_id} is not installed")
        return 1
    try:
        if action == "pre":
            played.record(game_id)  # a start from a shortcut or Steam too, which is where plays are not seen otherwise
            pre_launch(rec, settings, client)
        elif action == "watch":
            watch_session(rec, settings, client, since_ns=time.time_ns())
        else:
            ctx = make_context(rec, settings, client)
            if ctx is None:
                print("save sync is off for this game, or this machine is not registered")
                return 1
            result = sync.backup(ctx, sync.MANUAL, force=True)
            print(f"save sync: {result.status}")
            if result.status == "needs-confirmation":
                print("these folders hold files that may be this game's; confirm them in MOG:")
                print("\n".join(f"  {f}" for f in result.folders))
                return 2
            return 0 if result.status in ("uploaded", "duplicate", "unchanged") else 1
    except RuntimeError as e:
        print(f"save sync: {e}")
        return 1
    return 0

