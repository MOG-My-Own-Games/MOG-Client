"""HTTPS with a usable CA bundle. A bundled Python (AppImage, exe) often looks
for certificates in paths its build machine had but the user's distro does
not, which fails every HTTPS request with CERTIFICATE_VERIFY_FAILED."""

from __future__ import annotations

import http.client
import os
import ssl
import threading
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
_local = threading.local()


def _connection_key(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.netloc


def pooled_request(
    method: str, url: str, headers: dict[str, str], timeout: float
) -> http.client.HTTPResponse:
    """Send a request over this thread's persistent connection and return the
    response, still to be read. Read it to the end to keep the connection for the
    next request; after an early stop call `discard(url)`."""
    scheme, netloc = _connection_key(url)
    parts = urllib.parse.urlsplit(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    conns: dict = _local.__dict__.setdefault("conns", {})
    for attempt in (1, 2):
        conn = conns.get((scheme, netloc))
        reused = conn is not None
        if conn is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(netloc, timeout=timeout, context=ssl_context())
            else:
                conn = http.client.HTTPConnection(netloc, timeout=timeout)
            conns[(scheme, netloc)] = conn
        conn.timeout = timeout
        try:
            conn.request(method, target, headers=headers)
            return conn.getresponse()
        except (http.client.HTTPException, OSError):
            conn.close()
            conns.pop((scheme, netloc), None)
            # A kept-alive connection the server already closed is expected once; anything else is real.
            if not reused or attempt == 2:
                raise
    raise AssertionError("unreachable")


def discard(url: str) -> None:
    """Drop this thread's connection to the URL's host (after a read that did not finish)."""
    conn = _local.__dict__.setdefault("conns", {}).pop(_connection_key(url), None)
    if conn is not None:
        conn.close()
