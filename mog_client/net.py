"""HTTPS with a usable CA bundle. A bundled Python (AppImage, exe) often looks
for certificates in paths its build machine had but the user's distro does
not, which fails every HTTPS request with CERTIFICATE_VERIFY_FAILED."""

from __future__ import annotations

import os
import ssl
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


def urlopen(request: urllib.request.Request | str, timeout: float | None = None):
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())
