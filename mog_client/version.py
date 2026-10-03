"""Build version, written to _version.py by the build scripts (scripts/version.py)."""

try:
    from mog_client._version import __version__
except ImportError:
    __version__ = "dev"
