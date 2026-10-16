"""SQLite connections, schema loading and migrations for the per-case index."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from dmslog.paths import private_mkdir

from .filters import REGEX_FUNCTION, SAME_JSON_FUNCTION, json_same, regexp

SCHEMA_RESOURCE = "schema.sql"

# PRAGMAs applied to every connection (docs/ARQUITETURA.md, section 5).
_COMMON_PRAGMAS = (
    "PRAGMA foreign_keys=ON",
    "PRAGMA cache_size=-131072",
    "PRAGMA temp_store=MEMORY",
)


class SchemaVersionError(RuntimeError):
    """The database was written by a newer dmslog than this one."""

    diag_safe = True  # the message holds two schema numbers and fixed words


class MigrationError(RuntimeError):
    """A migration was asked for on a connection whose caller left a transaction open."""

    diag_safe = True  # the message holds two schema numbers and fixed words


def read_schema() -> str:
    """Return schema.sql, read through importlib.resources (works inside a zipapp)."""
    return resources.files(__package__).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def schema_statements(script: str) -> list[str]:
    """Split a SQL script into complete statements (triggers included), in order."""
    out: list[str] = []
    buf: list[str] = []
    for line in script.splitlines(keepends=True):
        buf.append(line)
        text = "".join(buf)
        if sqlite3.complete_statement(text):
            out.append(text.strip())
            buf = []
    if "".join(buf).strip():
        raise ValueError("incomplete SQL statement at the end of the schema")
    return out


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    # Statement by statement, not executescript (which commits): the migration runs in one transaction.
    for statement in schema_statements(read_schema()):
        conn.execute(statement)


_BUCKET_SEV_DDL = """
CREATE TABLE IF NOT EXISTS bucket_sev(
  source_id INTEGER NOT NULL, minute INTEGER NOT NULL, sev INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY(source_id, minute, sev)) WITHOUT ROWID
"""
_BUCKET_SEV_INDEX_DDL = "CREATE INDEX IF NOT EXISTS bucket_sev_minute ON bucket_sev(minute, sev, n)"


def _migrate_to_2(conn: sqlite3.Connection) -> None:
    """Add the template-free ``bucket_sev`` rollup and backfill it from ``bucket``."""
    conn.execute(_BUCKET_SEV_DDL)
    conn.execute(_BUCKET_SEV_INDEX_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO bucket_sev(source_id, minute, sev, n)"
        " SELECT source_id, minute, sev, sum(n) FROM bucket GROUP BY source_id, minute, sev"
    )


def _migrate_to_3(conn: sqlite3.Connection) -> None:
    """Add ``payload.reason`` (why a payload has no operation) to databases created before v3."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(payload)")}
    if "reason" not in columns:
        conn.execute("ALTER TABLE payload ADD COLUMN reason TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_payload_ref ON payload(ref_entry_id) WHERE ref_entry_id IS NOT NULL"
    )


_BOOKMARK_V4_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_bookmark_source ON bookmark(source_id, line_no)",
    "CREATE INDEX IF NOT EXISTS ix_bookmark_orphan ON bookmark(source_id, line_no) WHERE entry_id IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_bookmark_open ON bookmark(source_id)"
    " WHERE entry_id IS NOT NULL AND substr(fp, 1, 1) = 'p'",
    "CREATE TRIGGER IF NOT EXISTS bookmark_source_gone AFTER DELETE ON source BEGIN"
    " UPDATE bookmark SET source_id = NULL, entry_id = NULL WHERE source_id = OLD.id; END",
)
_BOOKMARK_UNIQUE_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bookmark_entry ON bookmark(entry_id) WHERE entry_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bookmark_uid ON bookmark(uid)",
)


def _migrate_to_4(conn: sqlite3.Connection) -> None:
    """Bookmark lineage (``source_id``), record fingerprint (``fp``), stable identity (``uid``) and
    ``orphan_seq``.

    Before v4 the writer did not keep ``bookmark.entry_id``. The lineage of a bookmark is the source
    that holds its member; the full pass links each bookmark to the record at its line and stores that
    record's fingerprint (docs/ARQUITETURA.md 9.3); every bookmark gets a random ``uid``. Runs inside
    the migration transaction, which holds the write lock before the first read, so no reindex can run
    between the reads and the updates.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bookmark)")}
    if "source_id" not in columns:
        conn.execute("ALTER TABLE bookmark ADD COLUMN source_id INTEGER")
    if "fp" not in columns:
        conn.execute("ALTER TABLE bookmark ADD COLUMN fp TEXT")
    if "uid" not in columns:
        conn.execute("ALTER TABLE bookmark ADD COLUMN uid TEXT")
    if "orphan_seq" not in columns:
        conn.execute("ALTER TABLE bookmark ADD COLUMN orphan_seq INTEGER")
    conn.execute("UPDATE bookmark SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL")
    for ddl in _BOOKMARK_V4_DDL:
        conn.execute(ddl)
    from . import bookmarks  # bookmarks imports the query layer; keep db.py import-light

    if "source_id" not in columns:
        bookmarks.adopt_lineage(conn)
    bookmarks.relink_all(conn)
    for ddl in _BOOKMARK_UNIQUE_DDL:  # after the pass: a version-3 cache may hold one record twice
        conn.execute(ddl)


def _migrate_to_5(conn: sqlite3.Connection) -> None:
    """Add ``entry_key.origin`` (0 = key of a template, 1 = key read from a payload).

    Existing rows are template keys (no payload key was written before v5), so the default 0 is right for
    them; no row is rewritten. ``ix_key_payload`` (partial, ``origin = 1``) lets the derivation leave the
    payload keys out without reading the table; ``ix_key_entry`` stays as it is (the query planner relies
    on it for the lookups by record and type). The payload keys of such a case come from the pipeline,
    source by source (payload extraction version 2, ARQUITETURA 6.6).
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entry_key)")}
    if "origin" not in columns:
        conn.execute("ALTER TABLE entry_key ADD COLUMN origin INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_key_payload ON entry_key(entry_id) WHERE origin = 1")


_HIGHLIGHT_RULE_DDL = (
    "CREATE TABLE IF NOT EXISTS highlight_rule(id INTEGER PRIMARY KEY, pos INTEGER NOT NULL,"
    " name TEXT NOT NULL, color TEXT NOT NULL, cond TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,"
    " created_at TEXT NOT NULL)"
)


def _migrate_to_6(conn: sqlite3.Connection) -> None:
    """Add ``highlight_rule`` (user highlight rules of the case, store/highlights.py; ARQUITETURA 10b).

    User data like ``saved_view``: no existing row changes, so the step only creates the table."""
    conn.execute(_HIGHLIGHT_RULE_DDL)


# Last physical line of a source: the last line of its highest-id record (ids follow file order), NULL when
# the source has no record. ``max(id)`` seeks the source's slice of an index once; ordering by id instead
# would sort every record of the source in a temporary B-tree (ARQUITETURA 5, ``source.last_line``).
LAST_LINE_SQL = (
    "SELECT e.line_no + e.nlines - 1 FROM entry e"
    " WHERE e.id = (SELECT max(x.id) FROM entry x WHERE x.source_id = {source})"
)


def _migrate_to_7(conn: sqlite3.Connection) -> None:
    """Add ``source.last_line`` and fill it once from the records already stored (no reindex).

    The writer records it at the end of every indexing batch (pipeline ``commit``); ``list_sources`` reads
    it instead of looking the last record up per request. Only the NULL rows are filled, so running the
    step again rewrites nothing.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    if "last_line" not in columns:
        conn.execute("ALTER TABLE source ADD COLUMN last_line INTEGER")
    conn.execute(
        f"UPDATE source SET last_line = ({LAST_LINE_SQL.format(source='source.id')})"  # noqa: S608
        " WHERE last_line IS NULL"
    )


# Ordered migration steps: version -> function that upgrades from version-1 to version.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_1,
    2: _migrate_to_2,
    3: _migrate_to_3,
    4: _migrate_to_4,
    5: _migrate_to_5,
    6: _migrate_to_6,
    7: _migrate_to_7,
}
SCHEMA_VERSION = max(MIGRATIONS)


def get_user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database to SCHEMA_VERSION and return the resulting version.

    Each step runs in its own transaction, opened with ``BEGIN IMMEDIATE`` (the write lock is taken
    before the step reads anything) and committed together with its ``PRAGMA user_version``; a failure
    rolls the whole step back, DDL included, and leaves the previous version intact. The version is
    read again under the lock, so a step that another connection already applied is skipped. Idempotent.

    The caller's transaction is never committed nor rolled back here: a database already at the
    current version returns at once (an open transaction stays open, untouched), and one that needs a
    step while the caller holds an open transaction raises :class:`MigrationError` before anything is
    written. Raises SchemaVersionError if the database is newer than this code (also without touching
    an open transaction).
    """
    current = get_user_version(conn)
    _check_version(current)
    if current >= SCHEMA_VERSION:
        return current
    if conn.in_transaction:
        raise MigrationError(
            f"schema migration {current} -> {SCHEMA_VERSION} needs a connection without an open"
            " transaction; commit or roll it back first"
        )
    previous = conn.isolation_level
    conn.isolation_level = None  # explicit transactions only
    try:
        for version in range(1, SCHEMA_VERSION + 1):
            if get_user_version(conn) >= version:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = get_user_version(conn)
                _check_version(current)
                if current < version:
                    MIGRATIONS[version](conn)
                    conn.execute(f"PRAGMA user_version={version}")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = previous
    return get_user_version(conn)


def _check_version(current: int) -> None:
    if current > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} is newer than the supported version "
            f"{SCHEMA_VERSION}; upgrade dmslog to open this case"
        )


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
    conn.create_function(REGEX_FUNCTION, 2, regexp, deterministic=True)  # text.regex of the filters
    conn.create_function(SAME_JSON_FUNCTION, 2, json_same, deterministic=True)  # repeat leaf of the filters
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def create_case_db(path: str | Path) -> sqlite3.Connection:
    """Create the parent folder if needed, open the database read-write and migrate it."""
    p = Path(path)
    private_mkdir(p.parent)
    conn = connect(p)
    migrate(conn)
    return conn
