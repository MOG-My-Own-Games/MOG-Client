"""Install/stream loops shared by the CLI and the GUI.

Output goes through `log`/`warn` callbacks (print by default) so the GUI can
route the same messages to its own widgets.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Callable

from mog_client.api import ACTIVE_STATES, MANIFEST_INTERVAL, POLL_INTERVAL, MogClient, bar, fmt_bytes, log, warn

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

    `on_bytes(downloaded, known_total)` fires as data arrives; the known
    total grows while the server is still producing files.
    """
    stop_event = stop_event or threading.Event()
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    done_paths: set[str] = set()
    progress: dict[str, int] = {}
    last_logged: int | None = None
    failures = 0
    while not stop_event.is_set():
        # Sampled before the manifest fetch: a manifest read after this
        # moment can't be missing files the finished install produced.
        server_was_done = server_done is None or server_done.is_set()
        try:
            manifest = client.stream_manifest(game_id, session_id=session_id)
        except RuntimeError as e:
            warn(str(e))
            manifest = None
        files = manifest.get("files", []) if manifest else []
        if len(files) != last_logged:
            last_logged = len(files)
            log(f"manifest: {len(files)} file(s)")
        known_total = sum(f.get("size_bytes", 0) for f in files)
        failed = False
        for f in files:
            if stop_event.is_set():
                return total, False
            path, size, complete = f["path"], f.get("size_bytes", 0), f.get("complete", False)
            if path in done_paths:
                continue
            local_path = out_dir / path
            if complete and local_path.is_file() and local_path.stat().st_size >= size:
                total += size - progress.get(path, 0)
                progress[path] = size
                done_paths.add(path)
                if on_bytes:
                    on_bytes(total, known_total)
                continue
            def in_flight(written_now: int, path: str = path, base: int = total, known: int = known_total) -> None:
                if on_bytes:
                    on_bytes(base + written_now - progress.get(path, 0), known)

            try:
                written = client.stream_file(
                    game_id, path, out_dir, session_id=session_id, on_chunk=in_flight, stop=stop_event
                )
            except RuntimeError as e:
                warn(str(e))
                failed = True
                break
            delta = written - progress.get(path, 0)
            if delta > 0:
                log(f"  streaming {path} ({fmt_bytes(written)} / {fmt_bytes(size)}" + (" DONE" if complete and written >= size else ")"))
            total += delta
            progress[path] = written
            if on_bytes and delta > 0:
                on_bytes(total, known_total)
            if complete and written >= size:
                done_paths.add(path)
            elif complete:
                warn(f"  {path}: only got {fmt_bytes(written)} of {fmt_bytes(size)}")
        if not failed and server_was_done and files and all(f["path"] in done_paths for f in files):
            log("all files downloaded")
            return total, True
        failures = failures + 1 if failed else 0
        # A flaky link retries soon at first, then backs off to at most 30s.
        if stop_event.wait(min(MANIFEST_INTERVAL * 2**failures, 30) if failed else MANIFEST_INTERVAL):
            break
    return total, False


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_repair(
    client: MogClient,
    game_id: int,
    out_dir: Path,
    session_id: int | None = None,
    log: Callable[[str], None] = log,
    warn: Callable[[str], None] = warn,
) -> None:
    """Verify every downloaded file's sha1 against the server's own finished,
    hash-verified manifest and re-fetch whatever doesn't match."""
    try:
        manifest = client.list_files(game_id, session_id=session_id)
    except RuntimeError as e:
        warn(f"couldn't verify downloaded files: {e}")
        return

    files = manifest.get("files", [])
    log(f"verifying {len(files)} file(s) against the server's hashes...")
    mismatches = 0
    for entry in files:
        path = entry["path"]
        local_path = out_dir / path
        if not local_path.is_file():
            continue
        if _sha1_of(local_path) == entry["sha1"]:
            continue
        mismatches += 1
        warn(f"  {path}: hash mismatch - re-downloading")
        try:
            local_path.write_bytes(client.download_file(game_id, path, session_id=session_id))
            log(f"  {path}: repaired" if _sha1_of(local_path) == entry["sha1"] else f"  {path}: still mismatched after re-download")
        except RuntimeError as e:
            warn(f"  {path}: repair failed: {e}")
    if mismatches == 0:
        log("all files verified OK")


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
