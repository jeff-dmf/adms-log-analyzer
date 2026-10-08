"""SQLite connections, schema loading and migrations for the per-case index."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from importlib import resources
from pathlib import Path

SCHEMA_RESOURCE = "schema.sql"

# PRAGMAs applied to every connection (docs/ARQUITETURA.md, section 5).
_COMMON_PRAGMAS = (
    "PRAGMA foreign_keys=ON",
    "PRAGMA cache_size=-131072",
    "PRAGMA temp_store=MEMORY",
)


class SchemaVersionError(RuntimeError):
    """The database was written by a newer dmslog than this one."""


def read_schema() -> str:
    """Return schema.sql, read through importlib.resources (works inside a zipapp)."""
    return resources.files(__package__).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    conn.executescript(read_schema())


# Ordered migration steps: version -> function that upgrades from version-1 to version.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _migrate_to_1}
SCHEMA_VERSION = max(MIGRATIONS)


def get_user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database to SCHEMA_VERSION and return the resulting version.

    Idempotent. Raises SchemaVersionError if the database is newer than this code.
    """
    current = get_user_version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} is newer than the supported version "
            f"{SCHEMA_VERSION}; upgrade dmslog to open this case"
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version={version}")
    return get_user_version(conn)


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a case database with the standard PRAGMAs.

    readonly=True opens an existing file with mode=ro (used by the server); writes fail.
    """
    p = Path(path)
    if readonly:
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(p)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    for pragma in _COMMON_PRAGMAS:
        conn.execute(pragma)
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def create_case_db(path: str | Path) -> sqlite3.Connection:
    """Create the parent folder if needed, open the database read-write and migrate it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    migrate(conn)
    return conn
