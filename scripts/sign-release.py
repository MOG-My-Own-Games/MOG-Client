#!/usr/bin/env python3
"""Sign a release's checksum file, so the client can tell a release from one somebody put in its place.

  scripts/sign-release.py --generate        make a key pair: the secret seed goes in the repository secret
                                            MOG_RELEASE_SIGNING_KEY, the public key in updater.RELEASE_PUBLIC_KEY
  scripts/sign-release.py SHA256SUMS.txt    write SHA256SUMS.txt.sig (hex), signed with the key in the environment
                                            variable MOG_RELEASE_SIGNING_KEY

See docs/RELEASING.md. Stdlib only."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mog_client import ed25519  # noqa: E402


def main(argv: list[str]) -> int:
    if argv == ["--generate"]:
        seed = secrets.token_bytes(32)
        print(f"secret seed (keep it secret):  {seed.hex()}")
        print(f"public key (goes in the app):  {ed25519.public_key(seed).hex()}")
        return 0
    if len(argv) != 1:
        print(__doc__)
        return 2
    key = os.environ.get("MOG_RELEASE_SIGNING_KEY", "").strip()
    if not key:
        print("MOG_RELEASE_SIGNING_KEY is not set: the release is left unsigned", file=sys.stderr)
        return 0 if os.environ.get("MOG_SIGNING_OPTIONAL") else 1
    source = Path(argv[0])
    signature = ed25519.sign(bytes.fromhex(key), source.read_bytes())
    Path(str(source) + ".sig").write_text(signature.hex() + "\n")
    print(f"signed {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
