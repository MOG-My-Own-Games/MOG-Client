"""HTTPS with a usable CA bundle. A bundled Python (AppImage, exe) often looks
for certificates in paths its build machine had but the user's distro does
not, which fails every HTTPS request with CERTIFICATE_VERIFY_FAILED."""

from __future__ import annotations

import os
import ssl
import urllib.error
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
