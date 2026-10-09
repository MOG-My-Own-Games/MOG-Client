"""Install/stream loops shared by the CLI and the GUI.

Output goes through `log`/`warn` callbacks (print by default) so the GUI can
route the same messages to its own widgets.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mog_client import net, trace
from mog_client.api import ACTIVE_STATES, MANIFEST_INTERVAL, POLL_INTERVAL, MogClient, bar, fmt_bytes, log, warn


STALL_SECONDS = 15.0


def download_all_files(
    client: MogClient,
    game_id: int,
    out_dir: Path,
    stop_event: threading.Event | None = None,
    session_id: int | None = None,
    log: Callable[[str], None] = log,
    warn: Callable[[str], None] = warn,
    on_bytes: Callable[[int, int], None] | None = None,
    server_done: threading.Event | None = None,
) -> tuple[int, bool]:
    """Poll manifest and stream every file to out_dir.

    Returns (bytes downloaded, finished). `finished` is True only once the
    server's install is over (`server_done` set, or None for "already over")
    AND every listed file is fully on disk: the download's own end, not the
    server installer's. Transient errors are retried until `stop_event`.

    How many files are fetched at once is the server's decision (its Settings); a server
    that does not say gets one file at a time.
    `on_bytes(downloaded, known_total)` fires as data arrives; the known
    total grows while the server is still producing files.
    """
    stop_event = stop_event or threading.Event()
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = client.download_workers() or 1
    trace.start(f"download of game {game_id} (session {session_id}) into {out_dir}, {workers} worker(s)")
    last_data = {"at": time.monotonic(), "warned": 0.0}
    done_paths: set[str] = set()
    # Bytes of each file on disk; the total is always their sum, so it cannot drift
    # (a file that restarts from zero just lowers its own entry).
    on_disk: dict[str, int] = {}
    known = {"total": 0}  # what the manifest lists so far; grows while the server still works
    last_logged: int | None = None
    running: dict[str, Future] = {}
    connections_reset = False
    failed_at: dict[str, tuple[float, int]] = {}  # path -> (when it may be tried again, failures so far)
    fetched: set[str] = set()  # files this download started writing, the only ones it may delete

    def drop_vanished(files_by_path: dict[str, dict], final: bool) -> None:
        """The manifest is a snapshot while the installer runs, and its paths can change by the time
        the install ends. Queued transfers for paths it no longer lists would only collect 404s while
        holding up the real ones, so they are cancelled; once the final list is in, the partial files
        written under the old paths go too."""
        for path, future in list(running.items()):
            if path not in files_by_path and future.cancel():
                del running[path]
        if not final:
            return
        gone = [p for p in on_disk if p not in files_by_path]
        for path in gone:
            on_disk.pop(path, None)
            done_paths.discard(path)
            failed_at.pop(path, None)
            if path in fetched:
                local = out_dir / path
                local.unlink(missing_ok=True)
                for parent in local.parents:  # empty folders the old layout left behind
                    if parent == out_dir or not parent.is_relative_to(out_dir):
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
        if gone:
            trace.event(f"final manifest: dropped {len(gone)} path(s) that are not in it")
            log(f"the install ended with a different file layout: {len(gone)} early file(s) dropped")

    def total() -> int:
        return sum(on_disk.values())

    def fetch(f: dict) -> int:
        path = f["path"]

        def in_flight(written_now: int) -> None:
            on_disk[path] = written_now
            last_data["at"] = time.monotonic()
            if on_bytes:
                on_bytes(total(), known["total"])

        began = time.monotonic()
        fetched.add(path)
        trace.event(f"start {path} from {on_disk.get(path, 0)}")
        try:
            written = client.stream_file(game_id, path, out_dir, session_id=session_id, on_chunk=in_flight, stop=stop_event)
        except Exception as e:  # noqa: BLE001 - recorded, then handled by the caller
            trace.event(f"fail {path} after {time.monotonic() - began:.1f}s: {e}")
            raise
        trace.event(f"end {path} at {written} bytes after {time.monotonic() - began:.1f}s")
        return written

    def reap(files_by_path: dict[str, dict]) -> bool:
        """Collect finished transfers; True if any of them moved data."""
        moved = False
        for path, future in list(running.items()):
            if not future.done():
                continue
            del running[path]
            f = files_by_path.get(path, {})
            try:
                written = future.result()
            except RuntimeError as e:
                warn(str(e))
                count = failed_at.get(path, (0.0, 0))[1] + 1
                failed_at[path] = (time.monotonic() + min(MANIFEST_INTERVAL * 2**count, 30), count)
                continue
            failed_at.pop(path, None)
            moved = moved or written != on_disk.get(path)
            on_disk[path] = written
            size, complete = f.get("size_bytes", 0), f.get("complete", False)
            log(f"  streaming {path} ({fmt_bytes(written)} / {fmt_bytes(size)}" + (" DONE" if complete and written >= size else ")"))
            if complete and written >= size:
                done_paths.add(path)
            elif complete:
                # Asked while the install still ran, finished since: the next pass fetches the rest.
                trace.event(f"{path}: {written} of {size} bytes so far, asking again")
            if on_bytes:
                on_bytes(total(), known["total"])
        return moved

    # One pool for the whole download, fed as files turn up: the manifest is polled every few
    # seconds however long a transfer takes, so a finished install is noticed at once and its files
    # start flowing without waiting for the slowest transfer of an earlier pass.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        files_by_path: dict[str, dict] = {}
        while not stop_event.is_set():
            # Sampled before the manifest fetch: a manifest read after this
            # moment can't be missing files the finished install produced.
            server_was_done = server_done is None or server_done.is_set()
            if server_was_done and not connections_reset:
                # The server has finished: whatever connections the download held while it worked
                # may have gone stale, so replace them one by one rather than trust them.
                connections_reset = True
                log("server finished: replacing the connections one by one")
                trace.event(f"server done, resetting connections ({len(running)} transfer(s) running)")
                net.reset_connections()
            asked = time.monotonic()
            try:
                manifest = client.stream_manifest(game_id, session_id=session_id)
            except RuntimeError as e:
                warn(str(e))
                trace.event(f"manifest failed after {time.monotonic() - asked:.1f}s: {e}")
                manifest = None
            else:
                took = time.monotonic() - asked
                if took > 2:
                    trace.event(f"manifest took {took:.1f}s")
            files = manifest.get("files", []) if manifest else []
            files_by_path = {f["path"]: f for f in files}
            if len(files) != last_logged:
                last_logged = len(files)
                log(f"manifest: {len(files)} file(s)")
            known["total"] = sum(f.get("size_bytes", 0) for f in files)

            moved = reap(files_by_path)
            # The final list has every file complete; until it shows up after the server finished,
            # what the endpoint returns is the stale snapshot from the install itself.
            manifest_final = bool(files) and all(f.get("complete") for f in files)
            if files:
                drop_vanished(files_by_path, final=server_was_done and manifest_final)
                known["total"] = sum(f.get("size_bytes", 0) for f in files)
                if on_bytes:
                    on_bytes(total(), known["total"])
            now = time.monotonic()
            for f in files:
                if server_was_done and not manifest_final:
                    break
                path, size, complete = f["path"], f.get("size_bytes", 0), f.get("complete", False)
                if path in done_paths or path in running or failed_at.get(path, (0.0, 0))[0] > now:
                    continue
                local_path = out_dir / path
                local_size = local_path.stat().st_size if local_path.is_file() else 0
                on_disk.setdefault(path, min(local_size, size) if size else local_size)
                if complete and local_size >= size:
                    on_disk[path] = size
                    done_paths.add(path)
                    if on_bytes:
                        on_bytes(total(), known["total"])
                elif complete or on_disk[path] < f.get("sealed_bytes", 0):
                    running[path] = pool.submit(fetch, f)  # nothing new to ask for on a growing file that is caught up

            quiet = time.monotonic() - last_data["at"]
            if running and quiet > STALL_SECONDS and time.monotonic() - last_data["warned"] > STALL_SECONDS:
                last_data["warned"] = time.monotonic()
                waiting = ", ".join(sorted(running)[:3]) + ("..." if len(running) > 3 else "")
                warn(f"no data for {quiet:.0f}s, {len(running)} transfer(s) waiting ({waiting}); trace: {trace.trace_path()}")
                trace.event(f"stalled {quiet:.0f}s; running: {sorted(running)}")
            if not running and server_was_done and files and all(f["path"] in done_paths for f in files):
                log("all files downloaded")
                trace.event("all files downloaded")
                return total(), True
            # Look again soon while data is flowing; otherwise at the usual pace.
            if stop_event.wait(0.5 if running or moved else MANIFEST_INTERVAL):
                break
    return total(), False


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RepairResult:
    checked: int = 0  # files compared with the server's copy
    repaired: int = 0
    failed: list[str] = field(default_factory=list)  # paths that are still wrong
    listed: bool = True  # False: the server's list of files could not be had, nothing was checked
    stopped: bool = False


def verify_and_repair(
    client: MogClient,
    game_id: int,
    out_dir: Path,
    session_id: int | None = None,
    log: Callable[[str], None] = log,
    warn: Callable[[str], None] = warn,
    on_manifest: Callable[[list[dict]], None] | None = None,
    fetch_missing: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    stop: threading.Event | None = None,
) -> RepairResult:
    """Verify every downloaded file's sha1 against the server's own finished,
    hash-verified manifest and re-fetch whatever doesn't match (and, with `fetch_missing`, whatever is not there).
    `on_manifest` gets that manifest's files ({path, size_bytes, sha1}) once it is known; `on_progress` gets
    (files looked at, files in all)."""
    result = RepairResult()
    try:
        manifest = client.list_files(game_id, session_id=session_id)
    except RuntimeError as e:
        warn(f"couldn't verify downloaded files: {e}")
        result.listed = False
        return result

    files = manifest.get("files", [])
    if on_manifest:
        on_manifest(files)
    log(f"verifying {len(files)} file(s) against the server's hashes...")
    for index, entry in enumerate(files):
        if stop is not None and stop.is_set():
            result.stopped = True
            break
        if on_progress:
            on_progress(index, len(files))
        path = entry["path"]
        local_path = out_dir / path
        if not local_path.is_file():
            if not fetch_missing:
                continue
            warn(f"  {path}: missing - downloading")
        elif sha1_of(local_path) == entry["sha1"]:
            result.checked += 1
            continue
        else:
            warn(f"  {path}: hash mismatch - re-downloading")
        result.checked += 1
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            part = local_path.with_name(local_path.name + ".mog-part")
            part.write_bytes(client.download_file(game_id, path, session_id=session_id))
            os.replace(part, local_path)
        except (RuntimeError, OSError) as e:
            warn(f"  {path}: repair failed: {e}")
            result.failed.append(path)
            continue
        if sha1_of(local_path) == entry["sha1"]:
            result.repaired += 1
            log(f"  {path}: repaired")
        else:
            result.failed.append(path)
            log(f"  {path}: still mismatched after re-download")
    if on_progress and not result.stopped:
        on_progress(len(files), len(files))
    if not result.repaired and not result.failed and not result.stopped:
        log("all files verified OK")
    return result


def poll_session(
    client: MogClient,
    game_id: int,
    timeout: float = 600.0,
    session_id: int | None = None,
    log: Callable[[str], None] = log,
    warn: Callable[[str], None] = warn,
    on_session: Callable[[dict], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict:
    """Poll session state until terminal or timeout, printing progress.

    AWAITING_INSTALLER (server-side auto-pick couldn't confidently resolve
    an installer - "manual mode") is reported and returned immediately:
    nothing moves this along except a human finishing it through the VNC
    session, so waiting out any part of `timeout` here is dead time.
    """
    deadline = time.time() + timeout
    last_state = None
    announced_vnc = False
    last_phase = None
    last_auto_status = None
    while time.time() < deadline and not (stop_event and stop_event.is_set()):
        session = client.get_session(game_id, session_id=session_id)
        if session and on_session:
            on_session(session)
        if not session:
            warn("no active session")
            time.sleep(POLL_INTERVAL)
            continue
        state = session.get("state")
        vnc_url = session.get("vnc_url")
        bytes_written, bytes_total = session.get("bytes_written", 0), session.get("bytes_total", 0)
        if state != last_state:
            log(f"state: {state}")
            last_state = state
        if state == "awaiting_installer":
            warn("session needs a manual installer pick - finish it via the VNC URL above (or re-run with --installer-path), then re-run this CLI to stream the result")
            return session
        if state in ACTIVE_STATES:
            phase, phase_detail = session.get("phase"), session.get("phase_detail")
            if phase and (phase, phase_detail) != last_phase:
                log(f"  {'Mounting' if phase == 'mounting' else 'Extracting'} {phase_detail}")
                last_phase = (phase, phase_detail)
            if bytes_total:
                pct = bytes_written / bytes_total if bytes_total else 0.0
                log(f"  {bar(pct)} {fmt_bytes(bytes_written)}/{fmt_bytes(bytes_total)}")
            if vnc_url and not announced_vnc:
                log("  installer is running - watch or take over here:")
                log(f"  {client.c.base}{vnc_url}")
                announced_vnc = True
            auto_status = session.get("auto_status")
            if auto_status != last_auto_status:
                if auto_status == "needs_manual":
                    warn("auto mode cannot continue - continue by hand via the VNC URL above")
                elif auto_status == "running":
                    detail = session.get("auto_detail")
                    if detail:
                        log(f"  auto mode: {detail}")
                last_auto_status = auto_status
        if state not in ACTIVE_STATES:
            return session
        time.sleep(POLL_INTERVAL)
    warn(f"timed out after {timeout}s waiting for the install to finish")
    return client.get_session(game_id, session_id=session_id)
