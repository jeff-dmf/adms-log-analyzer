"""Data directory resolution (testable for both OSes) and the SQLite/FTS5 startup check."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
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


REPLACE_RETRIES = 5
REPLACE_RETRY_DELAY_S = 0.02
# ``fsync`` makes a write durable across a power cut but costs milliseconds (on Windows, tens of them), so
# the test suite switches it off (tests/conftest.py) except in the tests that check that it is called.
SYNC_WRITES = True


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` so that a reader (or a crash) sees the old file or the new one.

    Temporary file in the same folder, ``fsync``, ``os.replace`` (atomic on POSIX and on Windows), then
    ``fsync`` of the folder where that is possible. No handle stays open. On any failure the temporary
    file is removed and the previous file is untouched. On Windows ``os.replace`` can fail briefly
    (``PermissionError``) while an antivirus or indexer holds the target: it is retried a few times.
    """
    target = Path(path)
    private_mkdir(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if SYNC_WRITES:
                os.fsync(handle.fileno())
        try:
            tmp.chmod(0o600)  # POSIX only; a no-op where permissions do not apply
        except OSError:
            pass
        for attempt in range(REPLACE_RETRIES):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_RETRY_DELAY_S)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if os.name == "posix" and SYNC_WRITES:  # a directory cannot be opened for fsync on Windows
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)


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
