"""Build info, written to _version.py by the build scripts (scripts/version.py).

UPDATE_METHOD is a build flag: "appimage" or "exe" lets the app replace itself
from the latest GitHub release, "none" (dev runs, pip installs, a future
Flatpak) leaves updating to whatever installed it.
"""

try:
    from mog_client._version import UPDATE_METHOD, __version__
except ImportError:
    __version__ = "dev"
    UPDATE_METHOD = "none"
