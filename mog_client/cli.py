#!/usr/bin/env python3
"""mog: minimal CLI client to drive a MOG-Server's stream-install feature.

Pure stdlib (urllib, json, argparse, sys). No third-party deps.

Usage:
  mog --base http://localhost:5000 --user admin --pass <password> \\
      --game-id 1 --out /tmp/installed

Just run it - the server does the rest, the same way starting an install
through any future client would (single source of truth: POST /{gameId}/install
resolves everything server-side):
  - Already installed (a cache from a prior run is still on disk)? Streamed
    immediately, nothing is (re)installed.
  - Not installed? The server starts it: top-ranked candidate, and for an
    archive or disc image it unpacks it and picks the executable inside
    (progress shows as "extracting <file>" / "mounting <file>").
  - No candidate at all - "manual mode": nobody (human, or auto mode) can
    say which file to run. The session sits in AWAITING_INSTALLER; this CLI
    prints the VNC URL and stops. Finish the install there, then re-run this
    same command to stream the result.

Flow:
  1. GET /api/games/{id}/install - already DONE? skip straight to streaming
  2. GET /api/games/{id}/install/candidates (informational only - the server
     does its own auto-pick, this is just to print the options)
  3. POST /api/games/{id}/install {installer_path, proton_build, ttl_seconds}
     - installer_path is optional; omit it and let the server decide.
     AWAITING_INSTALLER in the response means manual mode - stop and print
     the VNC URL (this outcome never touches the sandbox at all).
  4. poll GET /api/games/{id}/install/stream/manifest every 3s, concurrently
     with step 5's own polling - works while an install is still running too
     (that's the whole "stream install" point), not just once it's DONE.
  5. poll GET /api/games/{id}/install every 3s until state != active
  6. stream each file via Range GET /api/games/{id}/install/stream/{path}
  7. cancel: POST /api/games/{id}/install/cancel
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

POLL_INTERVAL = 3
MANIFEST_INTERVAL = 3

ACTIVE_STATES = {"detecting", "awaiting_installer", "installing", "streaming"}

# Distinguishes this CLI process from another client in the server's own
# per-(user, device) tracking, should that land later (see MOG-Server's
# docs/TODO.md) - kept now so adding it there needs no client-side change.
DEVICE_ID = f"cli-{uuid.uuid4().hex[:8]}"


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr, flush=True)


def basic_header(user: str, pw: str) -> str:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {token}"


class Client:
    def __init__(self, base: str, user: str, pw: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.user = user
        self.pw = pw
        self.timeout = timeout

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Accept": "application/json", "User-Agent": "mog-cli/0.1"}
        if self.user and self.pw:
            h["Authorization"] = basic_header(self.user, self.pw)
        if extra:
            h.update(extra)
        return h

    def request(
        self, method: str, path: str, body: dict | None = None, headers: dict | None = None, timeout: float | None = None
    ) -> tuple[int, bytes, dict]:
        url = self.base + path
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers = headers or {}
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(headers))
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
        except urllib.error.URLError as e:
            raise RuntimeError(f"connection error: {e}") from e

    def get_json(self, path: str, **kw) -> tuple[int, dict]:
        status, content, _ = self.request("GET", path, **kw)
        try:
            return status, json.loads(content.decode() or "null")
        except json.JSONDecodeError:
            return status, {"_raw": content.decode(errors="replace")}

    def post_json(self, path: str, body: dict, **kw) -> tuple[int, dict]:
        status, content, _ = self.request("POST", path, body=body, **kw)
        try:
            return status, json.loads(content.decode() or "null")
        except json.JSONDecodeError:
            return status, {"_raw": content.decode(errors="replace")}

    def delete_json(self, path: str, **kw) -> tuple[int, dict]:
        status, content, _ = self.request("DELETE", path, **kw)
        try:
            return status, json.loads(content.decode() or "null")
        except json.JSONDecodeError:
            return status, {"_raw": content.decode(errors="replace")}


def fmt_bytes(n: float) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PiB"


def bar(pct: float, width: int = 30) -> str:
    filled = int(width * max(0.0, min(1.0, pct)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


_UNSAFE_DIRNAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def safe_dirname(name: str) -> str:
    cleaned = _UNSAFE_DIRNAME_CHARS.sub("_", name).strip(" .")
    return cleaned or "game"


def extract_error(content: bytes, status: int) -> str:
    try:
        data = json.loads(content.decode() or "null")
    except Exception:
        return f"HTTP {status}: {content.decode(errors='replace')[:300]}"
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            return f"HTTP {status}: {detail.get('msg', detail)}"
        if detail:
            return f"HTTP {status}: {detail}"
        return f"HTTP {status}: {data}"
    return f"HTTP {status}: {content.decode(errors='replace')[:300]}"


class MogClient:
    def __init__(self, c: Client):
        self.c = c

    def candidates(self, game_id: int) -> dict:
        status, data = self.c.get_json(f"/api/games/{game_id}/install/candidates")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def start_session(
        self, game_id: int, installer_path: str | None, proton_build: str | None, ttl: int | None, auto_mode: bool | None = None, manual_mode: bool | None = None
    ) -> dict:
        body = {"installer_path": installer_path, "proton_build": proton_build}
        if auto_mode is not None:
            body["auto_mode"] = auto_mode
        if manual_mode is not None:
            body["manual_mode"] = manual_mode
        if ttl is not None:
            body["ttl_seconds"] = ttl
        status, data = self.c.post_json(f"/api/games/{game_id}/install", body)
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def get_session(self, game_id: int, session_id: int | None = None) -> dict:
        endpoint = f"/api/games/{game_id}/install"
        if session_id is not None:
            endpoint += f"?session_id={session_id}"
        status, data = self.c.get_json(endpoint)
        if status == 404:
            return {}
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def cancel_session(self, game_id: int) -> dict:
        status, data = self.c.post_json(f"/api/games/{game_id}/install/cancel", {})
        if status not in (200, 204):
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def clear_cache(self, game_id: int) -> dict:
        status, data = self.c.delete_json(f"/api/games/{game_id}/install")
        if status not in (200, 204):
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def list_files(self, game_id: int, session_id: int | None = None) -> dict:
        endpoint = f"/api/games/{game_id}/install/files"
        if session_id is not None:
            endpoint += f"?session_id={session_id}"
        status, data = self.c.get_json(endpoint)
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def download_file(self, game_id: int, path: str, session_id: int | None = None) -> bytes:
        encoded = urllib.parse.quote(path, safe="/")
        endpoint = f"/api/games/{game_id}/install/files/{encoded}"
        if session_id is not None:
            endpoint += f"?session_id={session_id}"
        status, content, _ = self.c.request("GET", endpoint, timeout=60.0)
        if status != 200:
            raise RuntimeError(extract_error(content, status))
        return content

    def stream_manifest(self, game_id: int, session_id: int | None = None) -> dict | None:
        endpoint = f"/api/games/{game_id}/install/stream/manifest"
        if session_id is not None:
            endpoint += f"?session_id={session_id}"
        status, data = self.c.get_json(endpoint)
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def stream_file(self, game_id: int, path: str, out_dir: Path, session_id: int | None = None) -> int:
        """Range-stream whatever is currently available for one file,
        resuming from wherever the local copy left off. Returns the file's
        total size on disk after this call. Never blocks waiting for more:
        drains what's already sealed, then returns - the caller's own
        manifest-repoll loop retries a file that isn't ready yet."""
        encoded = urllib.parse.quote(path, safe="/")
        params = {"device_id": DEVICE_ID}
        if session_id is not None:
            params["session_id"] = session_id
        endpoint = f"/api/games/{game_id}/install/stream/{encoded}?{urllib.parse.urlencode(params)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        written = dest.stat().st_size if dest.exists() else 0
        while True:
            headers = {"Range": f"bytes={written}-"} if written > 0 else {}
            status, content, resp_headers = self.c.request("GET", endpoint, headers=headers, timeout=60.0)
            if status in (200, 206):
                mode = "ab" if written > 0 else "wb"
                with open(dest, mode) as f:
                    f.write(content)
                written += len(content)
                if status == 200 and not headers.get("Range"):
                    return written
                cr = resp_headers.get("Content-Range") or resp_headers.get("content-range")
                if cr and "/" in cr:
                    total_s = cr.rsplit("/", 1)[1]
                    if total_s.isdigit() and int(total_s) > 0 and written >= int(total_s):
                        return written
                if len(content) == 0:
                    return written
                continue
            if status == 416:
                return written
            raise RuntimeError(extract_error(content, status))


def download_all_files(
    client: MogClient, game_id: int, out_dir: Path, stop_event: threading.Event | None = None, session_id: int | None = None
) -> int:
    """Poll manifest and stream every file to out_dir. Returns total bytes.

    Works the same whether the install is still running or already DONE -
    stream/manifest serves a best-effort live view while in progress and the
    real, final one once it's not, so this loop keeps polling either way
    until every listed file reports `complete`.
    """
    stop_event = stop_event or threading.Event()
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    done_paths: set[str] = set()
    progress: dict[str, int] = {}
    last_manifest = 0.0
    last_logged: int | None = None
    while not stop_event.is_set():
        now = time.time()
        if now - last_manifest < MANIFEST_INTERVAL and done_paths:
            if stop_event.wait(MANIFEST_INTERVAL - (now - last_manifest)):
                break
        last_manifest = time.time()
        try:
            manifest = client.stream_manifest(game_id, session_id=session_id)
        except RuntimeError as e:
            warn(str(e))
            if stop_event.wait(MANIFEST_INTERVAL):
                break
            continue
        if manifest is None:
            if stop_event.wait(MANIFEST_INTERVAL):
                break
            continue
        files = manifest.get("files", [])
        if len(files) != last_logged:
            last_logged = len(files)
            log(f"manifest: {len(files)} file(s)")
        new_done = 0
        for f in files:
            if stop_event.is_set():
                return total
            path, size, complete = f["path"], f.get("size_bytes", 0), f.get("complete", False)
            if path in done_paths:
                continue
            local_path = out_dir / path
            if complete and local_path.is_file() and local_path.stat().st_size >= size:
                total += size - progress.get(path, 0)
                progress[path] = size
                done_paths.add(path)
                new_done += 1
                continue
            try:
                written = client.stream_file(game_id, path, out_dir, session_id=session_id)
            except RuntimeError as e:
                warn(str(e))
                return total
            delta = written - progress.get(path, 0)
            if delta > 0:
                log(f"  streaming {path} ({fmt_bytes(written)} / {fmt_bytes(size)}" + (" DONE" if complete and written >= size else ")"))
            total += delta
            progress[path] = written
            if complete and written >= size:
                done_paths.add(path)
                new_done += 1
            elif complete and written < size:
                warn(f"  {path}: only got {fmt_bytes(written)} of {fmt_bytes(size)}")
        if files and new_done == 0 and all(f.get("complete") for f in files):
            log("all files complete")
            break
        if not files and stop_event.wait(MANIFEST_INTERVAL):
            break
    return total


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_repair(client: MogClient, game_id: int, out_dir: Path, session_id: int | None = None) -> None:
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


def poll_session(client: MogClient, game_id: int, timeout: float = 600.0, session_id: int | None = None) -> dict:
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
    while time.time() < deadline:
        session = client.get_session(game_id, session_id=session_id)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive a MOG-Server install from the command line.")
    parser.add_argument("--base", required=True, help="MOG-Server base URL, e.g. http://localhost:5000")
    parser.add_argument("--user", required=True)
    parser.add_argument("--pass", dest="password", required=True)
    parser.add_argument("--game-id", type=int, required=True)
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: ./<game name>)")
    parser.add_argument("--installer-path", default=None, help="Specific installer file to run, instead of auto-pick")
    parser.add_argument("--proton-build", default=None)
    parser.add_argument("--ttl", type=int, default=None, help="Install cache TTL in seconds (<=0 means unlimited)")
    parser.add_argument("--auto-mode", action="store_true", help="Let the server OCR the installer and click through it")
    parser.add_argument("--manual-mode", action="store_true", help="Force manual pick even if a candidate was auto-detected")
    parser.add_argument("--no-download", action="store_true", help="Just start/watch the install, don't stream files")
    parser.add_argument("--cancel", action="store_true", help="Cancel the running session for this game and exit")
    parser.add_argument("--clear", action="store_true", help="Delete the install cache for this game and exit")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Max seconds to wait for the install to finish")
    args = parser.parse_args()

    client = MogClient(Client(args.base, args.user, args.password))

    if args.cancel:
        client.cancel_session(args.game_id)
        log("cancelled")
        return 0
    if args.clear:
        client.clear_cache(args.game_id)
        log("cache cleared")
        return 0

    existing = client.get_session(args.game_id)
    session_id = existing.get("id") if existing and existing.get("state") == "done" else None

    if session_id is None:
        try:
            candidates = client.candidates(args.game_id)
            log(f"{len(candidates.get('candidates', []))} installer candidate(s) found")
        except RuntimeError as e:
            warn(str(e))

        session = client.start_session(args.game_id, args.installer_path, args.proton_build, args.ttl, args.auto_mode or None, args.manual_mode or None)
        session_id = session.get("id")
        if session.get("state") == "awaiting_installer":
            poll_session(client, args.game_id, session_id=session_id)
            return 1

    out_dir = args.out or Path(safe_dirname(f"game-{args.game_id}"))

    if args.no_download:
        poll_session(client, args.game_id, timeout=args.timeout, session_id=session_id)
        return 0

    stop_event = threading.Event()
    downloader = threading.Thread(target=download_all_files, args=(client, args.game_id, out_dir, stop_event, session_id))
    downloader.start()
    final_session = poll_session(client, args.game_id, timeout=args.timeout, session_id=session_id)
    stop_event.set()
    downloader.join(timeout=10)

    if final_session.get("state") == "done":
        verify_and_repair(client, args.game_id, out_dir, session_id=session_id)
        log(f"done: {out_dir}")
        return 0
    if final_session.get("state") == "failed":
        warn(f"install failed: {final_session.get('error')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
