"""Build metadata written into the zipapp by packaging/build_pyz.py."""

from __future__ import annotations

UNKNOWN_COMMIT = "dev"


def get_commit() -> str:
    """Return the commit hash baked into the build, or "dev" when running from source."""
    try:
        from . import _build_info
    except ImportError:
        return UNKNOWN_COMMIT
    return str(getattr(_build_info, "COMMIT", UNKNOWN_COMMIT))
