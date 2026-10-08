"""HTTP client for MOG-Server, shared by the CLI and the GUI. Stdlib only."""

from __future__ import annotations

import base64
import glob
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

from mog_client import logstore, net, trace

POLL_INTERVAL = 3
STREAM_TIMEOUT = 30.0  # per socket read, not for the whole transfer
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
    logstore.info(msg)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr, flush=True)
    logstore.warning(msg)


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

    def post_file(self, path: str, file_path: Path, timeout: float = 600.0) -> tuple[int, dict]:
        """POST a file as multipart form data under the field name `file`. Never retried."""
        boundary = uuid.uuid4().hex
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        length = len(head) + file_path.stat().st_size + len(tail)
        try:
            with file_path.open("rb") as f:
                req = urllib.request.Request(
                    self.base + path,
                    data=_MultipartBody(head, f, tail),
                    method="POST",
                    headers=self._headers(
                        {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(length)}
                    ),
                )
                try:
                    with net.urlopen(req, timeout=timeout) as resp:
                        status, content = resp.status, resp.read()
                except urllib.error.HTTPError as e:
                    status, content = e.code, e.read()
        except (OSError, http.client.HTTPException) as e:
            raise RuntimeError(f"connection error ({self.base}): {getattr(e, 'reason', e)}") from e
        try:
            return status, json.loads(content.decode() or "null")
        except json.JSONDecodeError:
            return status, {"_raw": content.decode(errors="replace")}

    def download_to(
        self,
        path: str,
        dest: Path,
        timeout: float = 600.0,
        on_progress: Callable[[int, int], None] | None = None,
        resume: bool = False,
    ) -> int:
        """GET a file straight to `dest` (replaced atomically); returns the HTTP status. `on_progress(written, total)`
        is called as it arrives (total is 0 when the server does not say). Whatever `on_progress` raises stops the
        download and leaves nothing behind. With `resume` a download that was cut short keeps its half file, and the
        next call continues it from there, as long as the server's file is still the same one."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if resume:
            return self._download_resumable(path, dest, timeout, on_progress)
        req = urllib.request.Request(self.base + path, headers=self._headers({"Accept": "*/*"}))
        # Unique per call: two downloads of one file must not share (and then rename away) the same temp file.
        part = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            with net.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                written = 0
                while chunk := resp.read(STREAM_CHUNK):
                    out.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total)
                status = resp.status
        except urllib.error.HTTPError as e:
            part.unlink(missing_ok=True)
            return e.code
        except (OSError, http.client.HTTPException) as e:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"connection error ({self.base}): {getattr(e, 'reason', e)}") from e
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(dest)
        return status

    def _download_resumable(self, path: str, dest: Path, timeout: float, on_progress) -> int:
        part = dest.with_name(f"{dest.name}.part")
        marker = dest.with_name(f"{dest.name}.part.id")  # the server's ETag for what the half file is a piece of

        def drop() -> None:
            part.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)

        # Half files an earlier version left, one per try.
        for old in glob.glob(glob.escape(str(dest)) + ".*.part"):
            Path(old).unlink(missing_ok=True)
        for attempt in range(2):
            have = part.stat().st_size if part.exists() and marker.exists() else 0
            etag = marker.read_text().strip() if have else ""
            if not have:
                drop()
            headers = {"Accept": "*/*"}
            if have and etag:
                headers.update({"Range": f"bytes={have}-", "If-Range": etag})
            else:
                have = 0
            req = urllib.request.Request(self.base + path, headers=self._headers(headers))
            try:
                with net.urlopen(req, timeout=timeout) as resp:
                    status = resp.status
                    if status != 206:
                        have = 0  # the whole file again: nothing to continue, or the server's file changed
                    new_etag = resp.headers.get("ETag") or ""
                    total = have + int(resp.headers.get("Content-Length") or 0)
                    if new_etag:
                        marker.write_text(new_etag)
                    else:
                        marker.unlink(missing_ok=True)
                    written = have
                    with open(part, "ab" if have else "wb") as out:
                        while chunk := resp.read(STREAM_CHUNK):
                            out.write(chunk)
                            written += len(chunk)
                            if on_progress:
                                on_progress(written, total)
                    if total and written < total:
                        raise _CutShort(f"the download was cut short at {written} of {total} bytes")
            except urllib.error.HTTPError as e:
                if e.code == 416 and attempt == 0:  # the half file is longer than the server's: fetch it whole
                    drop()
                    continue
                drop()
                return e.code
            except _CutShort as e:
                raise RuntimeError(f"connection error ({self.base}): {e}") from e
            except (OSError, http.client.HTTPException) as e:
                raise RuntimeError(f"connection error ({self.base}): {getattr(e, 'reason', e)}") from e
            except BaseException:
                drop()
                raise
            part.replace(dest)
            marker.unlink(missing_ok=True)
            return 200 if status == 206 else status
        return 416

    def delete_json(self, path: str, **kw) -> tuple[int, dict]:
        status, content, _ = self.request("DELETE", path, **kw)
        try:
            return status, json.loads(content.decode() or "null")
        except json.JSONDecodeError:
            return status, {"_raw": content.decode(errors="replace")}


class _CutShort(Exception):
    """The server ended a download before the length it announced."""


class _MultipartBody:
    """A multipart/form-data body (head, the file, tail) read in pieces, so a big file is never held in memory."""

    def __init__(self, head: bytes, f, tail: bytes):
        self._parts = [head, f, tail]

    def read(self, n: int = -1) -> bytes:
        while self._parts:
            part = self._parts[0]
            if isinstance(part, bytes):
                self._parts.pop(0)
                if part:
                    return part
                continue
            if chunk := part.read(n):
                return chunk
            self._parts.pop(0)
        return b""


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

    def candidates(self, game_id: int, source: str | None = None) -> dict:
        """The installers the server finds for a game, or, with `source` (an archive of the game's own), what is
        inside that archive (`extract_suggested` is then true when it looks like a game that needs no installer)."""
        path = f"/api/games/{game_id}/install/candidates"
        if source is not None:
            path += f"?source={urllib.parse.quote(source, safe='')}"
        status, data = self.c.get_json(path)
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def portable_archive(self, game_id: int, installer: dict | None = None) -> str | None:
        """The name of the archive that would be installed when it holds a game with no installer in it, so that
        the user can be asked about extracting it as it is. None when it is not an archive, has an installer, or
        the server cannot say (an older one, or an error: asking is a nicety, never a reason to stop)."""
        try:
            pick = installer
            if pick is None:
                found = self.candidates(game_id).get("candidates", [])
                pick = next((c for c in found if c.get("category", "game") == "game"), None)
            if not pick or pick.get("kind") not in ("disc image", "archive"):
                return None
            listing = self.candidates(game_id, pick["path"])
        except (RuntimeError, KeyError, TypeError):
            return None
        return pick.get("file_name") or pick["path"] if listing.get("extract_suggested") else None

    def start_session(
        self,
        game_id: int,
        installer_path: str | None,
        proton_build: str | None,
        ttl: int | None,
        auto_mode: bool | None = None,
        manual_mode: bool | None = None,
        source_path: str | None = None,
        extract_only: bool | None = None,
    ) -> dict:
        """`extract_only` True unpacks the game's archive as it is, False runs the installer inside it even when it
        has none, and None leaves it to the server (which extracts an archive that holds no installer)."""
        body = {"installer_path": installer_path, "proton_build": proton_build}
        if source_path is not None:
            body["source_path"] = source_path
        if extract_only is not None:
            body["extract_only"] = extract_only
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
            asked = time.monotonic()
            try:
                resp = net.pooled_request("GET", url, headers, STREAM_TIMEOUT)
            except net.ConnectionReset:
                trace.event(f"{path}: connection dropped on purpose while waiting, retrying from {written}")
                continue  # dropped on purpose: carry on over a new connection
            except (OSError, http.client.HTTPException) as e:
                raise RuntimeError(f"connection error ({self.c.base}): {getattr(e, 'reason', e)}") from e
            status = resp.status
            waited = time.monotonic() - asked
            if waited > 2:
                trace.event(f"{path}: the server took {waited:.1f}s to answer (status {status})")
            if status >= 300:
                try:
                    body = resp.read()  # reading it out keeps the connection usable
                except (OSError, http.client.HTTPException) as e:
                    if net.failed(url):
                        continue
                    raise RuntimeError(f"connection error ({self.c.base}): {e}") from e
                net.touch(url)
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
                if net.failed(url):
                    trace.event(f"{path}: connection dropped on purpose mid-body, resuming from {written}")
                    continue  # our own reset: resume from what is on disk over a new connection
                raise RuntimeError(f"connection lost after {fmt_bytes(written)} of {path}: {e}") from e
            expected = resp.headers.get("Content-Length")
            if expected and expected.isdigit() and got < int(expected):
                # http.client reports a body cut short as a clean end of stream.
                if net.failed(url):
                    continue
                raise RuntimeError(f"connection lost after {fmt_bytes(written)} of {path}: body cut short")
            net.touch(url)
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

    def mods(self, game_id: int) -> list[dict]:
        """The game's mods ({name, kind, size_bytes, file_count}), none when it has none or the server is older."""
        try:
            status, data = self.c.get_json(f"/api/games/{game_id}/mods")
        except RuntimeError:
            return []
        found = data.get("mods") if status == 200 and isinstance(data, dict) else None
        return [m for m in found if isinstance(m, dict) and m.get("name")] if isinstance(found, list) else []

    @staticmethod
    def _mod_path(game_id: int, name: str, action: str) -> str:
        return f"/api/games/{game_id}/mods/{urllib.parse.quote(name, safe='')}/{action}"

    def prepare_mod(self, game_id: int, name: str) -> dict:
        """Ask the server to get a mod ready: a folder starts being zipped. Returns its state."""
        status, data = self.c.post_json(self._mod_path(game_id, name, "prepare"), {})
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def mod_status(self, game_id: int, name: str) -> dict:
        status, data = self.c.get_json(self._mod_path(game_id, name, "status"))
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def cancel_mod(self, game_id: int, name: str) -> None:
        """Tell the server to stop zipping a mod and drop what it made. Best effort: the client has stopped anyway."""
        try:
            self.c.post_json(self._mod_path(game_id, name, "cancel"), {})
        except RuntimeError:
            pass

    def mod_downloaded(self, game_id: int, name: str, machine: str | None = None) -> bool:
        """Tell the server the mod is on this machine, so the person finds it in their notifications. False when the
        server cannot keep the notice (an older one), and the client has to say it itself."""
        path = self._mod_path(game_id, name, "downloaded")
        if machine:
            path += "?" + urllib.parse.urlencode({"machine": machine})
        try:
            status, _ = self.c.post_json(path, {})
        except RuntimeError:
            return False
        return status in (200, 204)

    def download_mod(self, game_id: int, name: str, dest: Path, on_progress: Callable[[int, int], None] | None = None) -> None:
        status = self.c.download_to(
            self._mod_path(game_id, name, "download"), dest, timeout=3600.0, on_progress=on_progress, resume=True
        )
        if status != 200:
            raise RuntimeError(f"the server refused the download of {name} (HTTP {status})")

    def save_paths(self, game_id: int) -> list[str] | None:
        """Where a Linux build of the game keeps its saves, as the server's manifest names them (placeholders such as
        `<xdgConfig>` left in), or None when the server cannot say (an older one)."""
        try:
            status, data = self.c.get_json(f"/api/games/{game_id}/save-paths")
        except RuntimeError:
            return None
        paths = data.get("paths") if status == 200 and isinstance(data, dict) else None
        return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else None

    def games_revision(self) -> str | None:
        """A value that changes when the server's library does, or None (an older server)."""
        try:
            status, data = self.c.get_json("/api/games/revision")
        except RuntimeError:
            return None
        revision = data.get("revision") if status == 200 and isinstance(data, dict) else None
        return revision if isinstance(revision, str) else None

    def game_sizes(self, game_id: int) -> dict | None:
        """What the server holds for the game: {"installer", "cache", "saves", "total"} bytes, or None (an older
        server). The server remembers the figures, so asking is cheap."""
        try:
            status, data = self.c.get_json(f"/api/games/{game_id}/sizes")
        except RuntimeError:
            return None
        if status != 200 or not isinstance(data, dict):
            return None
        try:
            return {
                "installer": int(data["installer_bytes"]),
                "cache": int(data["cache_bytes"]),
                "saves": int(data["saves_bytes"]),
                "total": int(data["total_bytes"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def register_device(
        self,
        client_uid: str,
        hostname: str,
        platform: str,
        os_id: str | None,
        adopt_device_id: int | None = None,
        name: str | None = None,
    ) -> tuple[int, dict]:
        body = {"client_uid": client_uid, "hostname": hostname, "platform": platform, "os_id": os_id}
        if adopt_device_id is not None:
            body["adopt_device_id"] = adopt_device_id
        if name:
            body["name"] = name
        return self.c.post_json("/api/devices/register", body)

    def list_saves(self, game_id: int) -> dict | None:
        """Every device's saved versions of a game, or None when the server cannot say (an older one)."""
        try:
            status, data = self.c.get_json(f"/api/games/{game_id}/saves")
        except RuntimeError:
            return None
        return data if status == 200 and isinstance(data, dict) else None

    def get_save(self, version_id: int) -> dict:
        status, data = self.c.get_json(f"/api/saves/{version_id}")
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def upload_save(self, game_id: int, device_id: int, archive: Path, trigger: str) -> dict:
        query = urllib.parse.urlencode({"device_id": device_id, "trigger": trigger})
        status, data = self.c.post_file(f"/api/games/{game_id}/saves?{query}", archive)
        if status != 200:
            raise RuntimeError(extract_error(json.dumps(data).encode(), status))
        return data

    def download_save(self, version_id: int, dest: Path, on_progress: Callable[[int, int], None] | None = None) -> None:
        status = self.c.download_to(f"/api/saves/{version_id}/download", dest, on_progress=on_progress)
        if status != 200:
            raise RuntimeError(f"could not download save version {version_id}: HTTP {status}")

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
