"""PyInstaller entry point: same behaviour as the `mog` console script."""

import sys

from mog_client.cli import main

if __name__ == "__main__":
    sys.exit(main())
