"""HTTP client for MOG-Server, shared by the CLI and the GUI. Stdlib only."""

from __future__ import annotations

import base64
import http.client
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from mog_client import net

POLL_INTERVAL = 3
STREAM_TIMEOUT = 60.0  # per socket read, not for the whole transfer
STREAM_CHUNK = 1024 * 1024
REQUEST_RETRIES = 3
MANIFEST_INTERVAL = 3

ACTIVE_STATES = {"detecting", "awaiting_installer", "installing", "streaming"}

# Distinguishes this client process from another one in the server's own
# per-(user, device) tracking, should that land later (see MOG-Server's
# docs/TODO.md) - kept now so adding it there needs no client-side change.
DEVICE_ID = f"client-{uuid.uuid4().hex[:8]}"


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr, flush=True)


def basic_header(user: str, pw: str) -> str:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {token}"


def normalize_base(base: str) -> str:
    """Server URL as typed, trailing slash dropped; a bare host:port means plain http."""
    base = base.strip().rstrip("/")
    return base if not base or "://" in base else f"http://{base}"


class Client:
    def __init__(self, base: str, user: str, pw: str, timeout: float = 30.0):
        self.base = normalize_base(base)
        self.user = user
        self.pw = pw
        self.timeout = timeout

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Accept": "application/json", "User-Agent": "mog-client/0.2"}
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
        # Only reads are retried: a repeated POST or DELETE could act twice.
        attempts = REQUEST_RETRIES if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                with net.urlopen(req, timeout=timeout or self.timeout) as resp:
                    return resp.status, resp.read(), dict(resp.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)
            except (OSError, http.client.HTTPException) as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, ssl.SSLError):
                    raise RuntimeError(
                        f"TLS error talking to {self.base}: {reason}. If the server only speaks "
                        "plain http, set the Server URL to http://host:port"
                    ) from e
                if attempt == attempts:
                    raise RuntimeError(f"connection error ({self.base}): {reason}") from e
                time.sleep(0.5 * 3 ** (attempt - 1))
        raise AssertionError("unreachable")

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
        self,
        game_id: int,
        installer_path: str | None,
        proton_build: str | None,
        ttl: int | None,
        auto_mode: bool | None = None,
        manual_mode: bool | None = None,
        source_path: str | None = None,
    ) -> dict:
        body = {"installer_path": installer_path, "proton_build": proton_build}
        if source_path is not None:
            body["source_path"] = source_path
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

    def stream_file(
        self,
        game_id: int,
        path: str,
        out_dir: Path,
        session_id: int | None = None,
        on_chunk: Callable[[int], None] | None = None,
        stop: threading.Event | None = None,
    ) -> int:
        """Range-stream whatever is currently available for one file,
        resuming from wherever the local copy left off. Returns the file's
        total size on disk after this call. Never blocks waiting for more:
        drains what's already sealed, then returns - the caller's own
        manifest-repoll loop retries a file that isn't ready yet.

        The body goes to disk in chunks as it arrives, so a dropped connection
        keeps everything received so far: the error is raised and the caller's
        retry resumes from the file's size on disk."""
        encoded = urllib.parse.quote(path, safe="/")
        params = {"device_id": DEVICE_ID}
        if session_id is not None:
            params["session_id"] = session_id
        endpoint = f"/api/games/{game_id}/install/stream/{encoded}?{urllib.parse.urlencode(params)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        written = dest.stat().st_size if dest.exists() else 0
        url = self.c.base + endpoint
        while not (stop and stop.is_set()):
            headers = self.c._headers({"Range": f"bytes={written}-"} if written > 0 else {})
            try:
                resp = net.pooled_request("GET", url, headers, STREAM_TIMEOUT)
            except (OSError, http.client.HTTPException) as e:
                raise RuntimeError(f"connection error ({self.c.base}): {getattr(e, 'reason', e)}") from e
            status = resp.status
            if status >= 300:
                body = resp.read()  # reading it out keeps the connection usable
                if status == 416:
                    return written
                if status < 400:
                    raise RuntimeError(f"{url} redirects to {resp.headers.get('Location')}: use the final URL as the server URL")
                raise RuntimeError(extract_error(body, status))
            got = 0
            try:
                restart = written > 0 and status == 200  # the server ignored Range: start over
                if restart:
                    written = 0
                with open(dest, "wb" if written == 0 else "ab") as f:
                    while chunk := resp.read1(STREAM_CHUNK):
                        f.write(chunk)
                        got += len(chunk)
                        written += len(chunk)
                        if on_chunk:
                            on_chunk(written)
                        if stop and stop.is_set():
                            net.discard(url)  # the rest of the body is still on the wire
                            return written
            except (OSError, http.client.HTTPException) as e:
                net.discard(url)
                raise RuntimeError(f"connection lost after {fmt_bytes(written)} of {path}: {e}") from e
            expected = resp.headers.get("Content-Length")
            if expected and expected.isdigit() and got < int(expected):
                # http.client reports a body cut short as a clean end of stream.
                net.discard(url)
                raise RuntimeError(f"connection lost after {fmt_bytes(written)} of {path}: body cut short")
            content_range = resp.headers.get("Content-Range")
            if status == 200:
                return written
            if content_range and "/" in content_range:
                total = content_range.rsplit("/", 1)[1]
                if total.isdigit() and int(total) > 0 and written >= int(total):
                    return written
            if got == 0:
                return written
        return written


    def me(self) -> dict:
        status, data = self.c.get_json("/api/users/me")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def game_size(self, game_id: int) -> int | None:
        """Bytes the game's folder takes on the server, or None if it cannot say (an older server)."""
        try:
            status, data = self.c.get_json(f"/api/games/{game_id}/size")
        except RuntimeError:
            return None
        size = data.get("size_bytes") if status == 200 and isinstance(data, dict) else None
        return size if isinstance(size, int) else None

    def get_image(self, path: str) -> bytes | None:
        """An image served by the MOG-Server (cached there), or None if it has none."""
        try:
            status, content, _ = self.c.request("GET", path, timeout=30.0)
        except RuntimeError:
            return None
        return content if status == 200 and content else None

    def download_workers(self) -> int | None:
        """How many files to download at once, per the server's Settings; None if it does not say."""
        try:
            status, data = self.c.get_json("/api/games/install/defaults")
        except RuntimeError:
            return None
        workers = data.get("download_workers") if status == 200 and isinstance(data, dict) else None
        return workers if isinstance(workers, int) and workers > 0 else None

    def notifications(self) -> dict:
        status, data = self.c.get_json("/api/notifications")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def mark_notifications_read(self, notification_id: int | None = None) -> None:
        path = "/api/notifications/read" if notification_id is None else f"/api/notifications/{notification_id}/read"
        self.c.post_json(path, {})

    def delete_notifications(self, notification_id: int | None = None) -> None:
        self.c.delete_json("/api/notifications" if notification_id is None else f"/api/notifications/{notification_id}")

    def list_libraries(self) -> list[dict]:
        status, data = self.c.get_json("/api/libraries")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def list_games(self) -> list[dict]:
        status, data = self.c.get_json("/api/games")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def get_game(self, game_id: int) -> dict:
        status, data = self.c.get_json(f"/api/games/{game_id}")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data


def fetch_url(url: str, timeout: float = 30.0) -> bytes:
    """Plain GET of an absolute URL (cover/screenshot hosted by the metadata
    provider, not by the MOG-Server, so no auth header)."""
    req = urllib.request.Request(url, headers={"User-Agent": "mog-client/0.2"})
    try:
        with net.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"fetch {url}: {e}") from e
