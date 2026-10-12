"""Data directory resolution (testable for both OSes) and the SQLite/FTS5 startup check."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

APP_DIR_NAME = "dmslog"


def default_data_dir(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default data directory for the given platform and environment.

    Windows: %LOCALAPPDATA%\\dmslog. Elsewhere: ${XDG_DATA_HOME:-~/.local/share}/dmslog.
    All inputs default to the running system, and can be injected to test either OS.
    """
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
    else:
        xdg = env.get("XDG_DATA_HOME")
        # Per the XDG spec, a relative XDG_DATA_HOME must be ignored.
        root = Path(xdg) if xdg and PurePosixPath(xdg).is_absolute() else home / ".local" / "share"
    return root / APP_DIR_NAME


def private_mkdir(path: str | os.PathLike[str]) -> Path:
    """Create ``path`` and its missing parents; on POSIX every folder created here is mode 0700.

    The case databases hold whole logs, so the data folder must not be readable by other local users
    (the process ``umask`` alone usually leaves 0755). Folders that already exist keep their mode.
    On Windows nothing changes: ``%LOCALAPPDATA%`` already has a private ACL.
    """
    target = Path(path)
    missing = []
    for folder in (target, *target.parents):
        if folder.exists():
            break
        missing.append(folder)
    for folder in reversed(missing):
        folder.mkdir(exist_ok=True)
        if os.name == "posix":
            try:
                folder.chmod(0o700)
            except OSError:
                pass
    return target


def resolve_data_dir(
    override: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the data directory: the --data-dir override when given, else the default."""
    if override:
        return Path(override).expanduser()
    return default_data_dir(platform, env, home)


def check_sqlite_fts5() -> dict[str, Any]:
    """Probe SQLite and FTS5 in memory.

    Returns {"sqlite": <version>, "fts5": <bool>, "error": <str | None>}.
    """
    result: dict[str, Any] = {"sqlite": sqlite3.sqlite_version, "fts5": False, "error": None}
    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(body)")
        conn.execute("INSERT INTO fts_probe(body) VALUES ('probe')")
        row = conn.execute("SELECT count(*) FROM fts_probe WHERE fts_probe MATCH 'probe'").fetchone()
        result["fts5"] = bool(row and row[0] == 1)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return result
