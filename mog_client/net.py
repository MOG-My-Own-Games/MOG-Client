"""HTTPS with a usable CA bundle. A bundled Python (AppImage, exe) often looks
for certificates in paths its build machine had but the user's distro does
not, which fails every HTTPS request with CERTIFICATE_VERIFY_FAILED."""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

SYSTEM_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine, macOS
)


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    paths = ssl.get_default_verify_paths()
    if os.environ.get("SSL_CERT_FILE") or (paths.cafile and os.path.isfile(paths.cafile)):
        return ssl.create_default_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for bundle in SYSTEM_BUNDLES:
        if os.path.isfile(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


class _NoUpgradeRedirect(urllib.request.HTTPRedirectHandler):
    """A plain-HTTP URL stays plain HTTP: a redirect to https is reported
    instead of silently starting a TLS handshake the user never asked for."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.startswith("http://") and newurl.startswith("https://"):
            raise urllib.error.URLError(
                f"{req.full_url} redirects to {newurl}: use https:// in the server URL, "
                "or serve the server over plain http"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@lru_cache(maxsize=1)
def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), _NoUpgradeRedirect())


def urlopen(request: urllib.request.Request | str, timeout: float | None = None):
    return _opener().open(request, timeout=timeout)


# One persistent connection per thread and host: a download of many small files
# otherwise pays a TCP (and TLS) handshake for every file, which dominates on a
# link with any latency.
#
# A connection left idle is not trusted: servers and the proxies and NATs in front of them
# drop idle ones, often without telling the client, and the next request on such a connection
# then waits out the whole read timeout before failing.
MAX_IDLE_SECONDS = 4.0  # below uvicorn's 5s keep-alive, far below any proxy's
RESET_INTERVAL = 1.0  # seconds between one connection being dropped and the next


class ConnectionReset(OSError):
    """A connection this client dropped on purpose (see reset_connections); retry on a fresh one."""


class _Pooled:
    def __init__(self, conn: http.client.HTTPConnection):
        self.conn = conn
        self.last_used = time.monotonic()
        self.killed = False

    def interrupt(self) -> None:
        """Cut the socket from another thread. Only the socket: closing the connection object under
        a thread that is reading from it would break that thread in unpredictable ways, so the
        owner closes it when it notices (the read wakes up with an error or end of stream)."""
        sock = self.conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self) -> None:
        self.interrupt()
        self.conn.close()


_local = threading.local()
_registry: set[_Pooled] = set()
_registry_lock = threading.Lock()


def _connection_key(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.netloc


def _conns() -> dict:
    return _local.__dict__.setdefault("conns", {})


def _forget(key: tuple[str, str]) -> _Pooled | None:
    pooled = _conns().pop(key, None)
    if pooled is not None:
        with _registry_lock:
            _registry.discard(pooled)
        pooled.close()
    return pooled


def pooled_request(
    method: str, url: str, headers: dict[str, str], timeout: float
) -> http.client.HTTPResponse:
    """Send a request over this thread's persistent connection and return the
    response, still to be read. Read it to the end and call `touch(url)` to keep the
    connection for the next request; after a failed or early-stopped read call `failed(url)`
    or `discard(url)`. Raises ConnectionReset if the connection was dropped on purpose."""
    scheme, netloc = _connection_key(url)
    key = (scheme, netloc)
    parts = urllib.parse.urlsplit(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    for attempt in (1, 2):
        pooled = _conns().get(key)
        if pooled is not None and (pooled.killed or time.monotonic() - pooled.last_used > MAX_IDLE_SECONDS):
            _forget(key)
            pooled = None
        reused = pooled is not None
        if pooled is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(netloc, timeout=timeout, context=ssl_context())
            else:
                conn = http.client.HTTPConnection(netloc, timeout=timeout)
            pooled = _Pooled(conn)
            _conns()[key] = pooled
            with _registry_lock:
                _registry.add(pooled)
        pooled.last_used = time.monotonic()
        pooled.conn.timeout = timeout
        try:
            pooled.conn.request(method, target, headers=headers)
            if pooled.conn.sock is not None:
                pooled.conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            return pooled.conn.getresponse()
        except (http.client.HTTPException, OSError):
            killed = pooled.killed
            _forget(key)
            if killed:
                raise ConnectionReset("connection dropped by the client") from None
            # A kept-alive connection the server already closed is expected once; anything else is real.
            if not reused or attempt == 2:
                raise
    raise AssertionError("unreachable")


def touch(url: str) -> None:
    """The response on this thread's connection to the URL's host was read to the end: it is idle from now."""
    pooled = _conns().get(_connection_key(url))
    if pooled is not None:
        pooled.last_used = time.monotonic()


def failed(url: str) -> bool:
    """A read on this thread's connection to the URL's host went wrong. Drops it and says whether
    that was our own reset (retry at once) or a real failure."""
    pooled = _forget(_connection_key(url))
    return bool(pooled and pooled.killed)


def discard(url: str) -> None:
    """Drop this thread's connection to the URL's host (after a read that did not finish)."""
    _forget(_connection_key(url))


def reset_connections(interval: float = RESET_INTERVAL) -> threading.Thread:
    """Drop every pooled connection one after another, `interval` seconds apart, so that nothing
    stays stuck on one that went bad. A transfer waiting on a dropped connection fails at once with
    ConnectionReset and carries on over a new one; idle ones are simply replaced on next use."""

    def run() -> None:
        with _registry_lock:
            snapshot = sorted(_registry, key=lambda p: p.last_used)
        for pooled in snapshot:
            pooled.killed = True
            pooled.interrupt()
            time.sleep(interval)

    thread = threading.Thread(target=run, daemon=True, name="connection-reset")
    thread.start()
    return thread
