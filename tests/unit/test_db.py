import re
import sqlite3
import subprocess
import sys
import zipapp
from pathlib import Path

import pytest

from dmslog.store import SCHEMA_VERSION, SchemaVersionError, connect, create_case_db, migrate
from dmslog.store.db import MigrationError

SRC = Path(__file__).resolve().parents[2] / "src"

TABLES = {
    "meta", "collection", "stream", "source", "entry", "entry_body", "entry_key", "template",
    "unknown_sig", "event", "link", "payload", "stat_sample", "bucket", "config_param",
    "config_observed", "error_config", "bookmark", "saved_view", "entry_fts",
}
INDEXES = {
    "ix_entry_ts", "ix_entry_src_ts", "ix_entry_sev_ts", "ix_entry_tpl_ts", "ix_entry_event",
    "ix_entry_tid", "ix_key_entry", "ix_event_kind", "ix_event_stream", "ix_event_status",
    "ix_link_b", "ix_payload_entry", "ix_payload_event", "ix_payload_op", "ix_payload_msg",
}


def names(conn, kind):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'", (kind,))
    return {r[0] for r in rows}


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "cases" / "c1" / "index.sqlite")
    yield c
    c.close()


def add_entry(conn, msg, cont=None, entry_id=1):
    conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'file', 'x', 't')")
    conn.execute(
        "INSERT INTO source(id, collection_id, member, file_name, envelope) "
        "VALUES (1, 1, 'a.log', 'a.log', 'adms')"
    )
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, sev, tpl_id, msg) "
        "VALUES (?,1,1,0,1,4,0,?)",
        (entry_id, msg),
    )
    if cont is not None:
        conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?, ?)", (entry_id, cont))


def test_schema_objects_exist(conn):
    assert names(conn, "table") >= TABLES
    assert INDEXES <= names(conn, "index")
    assert names(conn, "view") == {"entry_text"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4


def test_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA cache_size").fetchone()[0] == -131072
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY


def test_fts_diacritics_search(conn):
    msg = "Opera\u00e7\u00e3o conclu\u00edda com exce\u00e7\u00e3o"
    add_entry(conn, msg, "linha de continua\u00e7\u00e3o\nS\u00e3o Paulo")
    conn.execute("INSERT INTO entry_fts(rowid, msg, cont) SELECT rowid, msg, cont FROM entry_text")
    for term in ("operacao", "excecao", "Sao", "continuacao", "opera\u00e7\u00e3o"):
        rows = conn.execute("SELECT rowid FROM entry_fts WHERE entry_fts MATCH ?", (term,)).fetchall()
        assert rows == [(1,)], term


def test_fts_excludes_template_without_fts(conn):
    conn.execute(
        "INSERT INTO template(id, code, family, class, pattern, fts, def_version) "
        "VALUES (5,'T-1','F','other','p',0,'v')"
    )
    add_entry(conn, "trace noise")
    conn.execute("UPDATE entry SET tpl_id = 5")
    assert conn.execute("SELECT count(*) FROM entry_text").fetchone()[0] == 0


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO source(collection_id, member, file_name, envelope) VALUES (99, 'a', 'a', 'adms')"
        )


def test_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO collection(kind, path, added_at) VALUES ('bogus', 'x', 't')")


def test_migrate_is_idempotent(conn):
    before = names(conn, "table")
    assert migrate(conn) == SCHEMA_VERSION
    assert migrate(conn) == SCHEMA_VERSION
    assert names(conn, "table") == before


def test_future_version_rejected(tmp_path):
    path = tmp_path / "future.sqlite"
    c = sqlite3.connect(path)
    c.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    c.close()
    c = connect(path)
    with pytest.raises(SchemaVersionError, match="newer"):
        migrate(c)
    c.close()


def test_readonly_connection_rejects_writes(tmp_path):
    path = tmp_path / "index.sqlite"
    create_case_db(path).close()
    ro = connect(path, readonly=True)
    assert ro.execute("SELECT count(*) FROM entry").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO meta(key, value) VALUES ('k', 'v')")
    ro.close()


def test_schema_loaded_from_zipapp(tmp_path):
    staging = tmp_path / "staging"
    (staging / "dmslog").mkdir(parents=True)
    for f in (SRC / "dmslog").rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
            target = staging / f.relative_to(SRC)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.read_bytes())
    pyz = tmp_path / "t.pyz"
    zipapp.create_archive(staging, pyz, main="dmslog.cli:main")
    db_path = tmp_path / "from_pyz.sqlite"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from dmslog.store import create_case_db;"
        "c = create_case_db(sys.argv[2]);"
        "print(c.execute('PRAGMA user_version').fetchone()[0])"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code, str(pyz), str(db_path)], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == str(SCHEMA_VERSION)
    check = sqlite3.connect(db_path)
    assert "entry_fts" in names(check, "table")
    check.close()


def test_migration_2_backfills_bucket_sev_from_bucket(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = connect(path)
    from dmslog.store.db import MIGRATIONS

    MIGRATIONS[1](conn)  # version 1 schema (created with the current schema.sql)
    conn.execute("DROP TABLE bucket_sev")  # what a version-1 database looks like
    conn.execute("PRAGMA user_version=1")
    conn.executemany(
        "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) VALUES (?,?,?,?,?)",
        [(1, 10, 2, 0, 3), (1, 10, 2, 7, 4), (1, 10, 4, 7, 5), (2, 11, 2, 0, 1)],
    )
    conn.commit()  # migrate never commits a transaction the caller left open
    assert migrate(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT source_id, minute, sev, n FROM bucket_sev ORDER BY 1, 2, 3").fetchall()
    assert rows == [(1, 10, 2, 7), (1, 10, 4, 5), (2, 11, 2, 1)]
    assert migrate(conn) == SCHEMA_VERSION
    conn.close()


BOOKMARK_V4_OBJECTS = (
    "ix_bookmark_source", "ix_bookmark_orphan", "ix_bookmark_open", "ux_bookmark_entry", "ux_bookmark_uid"
)


def make_v3(path):
    """A version-3 case: the current schema without the bookmark lineage, fingerprint and their objects."""
    from dmslog.store.db import MIGRATIONS

    conn = connect(path)
    conn.isolation_level = None
    MIGRATIONS[1](conn)
    conn.execute("DROP TRIGGER IF EXISTS bookmark_source_gone")
    for index in BOOKMARK_V4_OBJECTS:
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bookmark)")}
    for column in ("fp", "source_id", "uid", "orphan_seq"):
        if column in columns:
            conn.execute(f"ALTER TABLE bookmark DROP COLUMN {column}")
    conn.execute("PRAGMA user_version=3")
    conn.isolation_level = ""
    add_entry(conn, "kept record", entry_id=7)
    conn.execute(
        "INSERT INTO source(id, collection_id, member, file_name, envelope)"
        " VALUES (2, 1, 'b.log', 'b.log', 'adms')"
    )
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, sev, tpl_id, msg) VALUES"
        " (8, 2, 1, 0, 1, 4, 0, 'first of b'), (9, 2, 2, 30, 1, 4, 0, 'second of b')"
    )
    conn.execute("INSERT INTO entry_fts(rowid, msg, cont) SELECT rowid, msg, cont FROM entry_text")
    conn.execute(
        "INSERT INTO bookmark(collection_id, member, line_no, entry_id, created_at, note) VALUES"
        " (1, 'a.log', 1, 3, 't', 'stale'),"  # stale cache from a reindex before v4
        " (1, 'a.log', 9, 4, 't', 'gone'),"  # its record is gone
        " (1, 'b.log', 1, NULL, 't', 'came back'),"  # cache NULL in v3, the record is there now
        " (1, 'dropped.log', 2, 5, 't', 'dropped')"  # no source holds this member any more
    )
    conn.commit()
    return conn


def snapshot(conn):
    return (
        conn.execute("PRAGMA user_version").fetchone()[0],
        sorted(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")),
        conn.execute("SELECT * FROM bookmark ORDER BY id").fetchall(),
        conn.execute("SELECT count(*) FROM entry").fetchone()[0],
    )


def test_migration_4_adds_lineage_and_fingerprint_and_rebuilds_bookmark_caches(tmp_path):
    from dmslog.store import bookmarks as bm

    conn = make_v3(tmp_path / "v3.sqlite")
    assert migrate(conn) == SCHEMA_VERSION
    sql = "SELECT note, entry_id, source_id, fp IS NOT NULL FROM bookmark ORDER BY id"
    rows = conn.execute(sql).fetchall()
    assert rows == [
        ("stale", 7, 1, True),
        ("gone", None, 1, False),  # no record seen: no identity, it may take the record of its line later
        ("came back", 8, 2, True),
        ("dropped", None, None, False),  # detached: its source is gone
    ]
    uids = [r[0] for r in conn.execute("SELECT uid FROM bookmark")]
    assert len(set(uids)) == 4 and all(re.fullmatch(r"[0-9a-f]{32}", u) for u in uids)
    fps = dict(conn.execute("SELECT note, fp FROM bookmark"))
    assert fps["stale"] == bm.fingerprint(0, None, "kept record", open_record=True)  # last record of a.log
    assert fps["came back"] == bm.fingerprint(0, None, "first of b")  # a later record exists: complete
    assert set(BOOKMARK_V4_OBJECTS) <= names(conn, "index")
    assert names(conn, "trigger") == {"bookmark_source_gone"}
    before = snapshot(conn)
    assert migrate(conn) == SCHEMA_VERSION
    assert snapshot(conn) == before
    conn.close()


def test_migration_4_failure_rolls_back_to_an_intact_version_3(tmp_path, monkeypatch):
    from dmslog.store import bookmarks as bm

    conn = make_v3(tmp_path / "v3.sqlite")
    before = snapshot(conn)
    original = bm.relink_all

    def fail_after_relink(c):
        original(c)  # the caches were already rewritten when the failure hits
        raise RuntimeError("injected failure")

    monkeypatch.setattr(bm, "relink_all", fail_after_relink)
    with pytest.raises(RuntimeError, match="injected"):
        migrate(conn)
    assert not conn.in_transaction
    assert snapshot(conn) == before  # no column, index, trigger, cache or version left behind
    other = connect(tmp_path / "v3.sqlite")  # what another process sees
    assert snapshot(other) == before
    other.close()
    monkeypatch.setattr(bm, "relink_all", original)
    assert migrate(conn) == SCHEMA_VERSION  # a second attempt works
    assert conn.execute("SELECT entry_id FROM bookmark WHERE note = 'stale'").fetchone()[0] == 7
    conn.close()


def test_migration_4_holds_the_write_lock_against_a_concurrent_reindex(tmp_path):
    from dmslog.store.writer import IndexWriter

    path = tmp_path / "v3.sqlite"
    conn = make_v3(path)
    other = sqlite3.connect(path, timeout=0)  # a second process (the ingest) on the same case
    attempts: list[str] = []

    def concurrent_reindex(statement: str) -> None:
        if attempts or not statement.lstrip().upper().startswith("UPDATE BOOKMARK"):
            return
        try:  # between the migration's reads and its writes: rewrite the records of a.log
            other.execute("DELETE FROM entry WHERE id = 7")
            other.commit()
            attempts.append("written")
        except sqlite3.OperationalError as exc:
            other.rollback()
            attempts.append(str(exc))
        try:
            migrate(other)
            attempts.append("migrated twice")
        except sqlite3.OperationalError:
            other.rollback()

    conn.set_trace_callback(concurrent_reindex)
    try:
        assert migrate(conn) == SCHEMA_VERSION
    finally:
        conn.set_trace_callback(None)
    assert attempts == ["database is locked"]  # the migration held the write lock before reading
    dangling = conn.execute(
        "SELECT b.id FROM bookmark b LEFT JOIN entry e ON e.id = b.entry_id"
        " WHERE b.entry_id IS NOT NULL AND e.id IS NULL"
    ).fetchall()
    assert dangling == []
    assert migrate(other) == SCHEMA_VERSION  # already done: nothing is applied twice
    writer = IndexWriter(other)  # the reindex runs after the migration, through the writer
    writer.delete_source_entries(1)
    writer.commit()
    rows = conn.execute("SELECT note, entry_id FROM bookmark ORDER BY id").fetchall()
    assert rows[0] == ("stale", None)  # orphaned by the writer, not left pointing at a deleted record
    other.close()
    conn.close()


def test_a_version_3_case_keeps_the_legacy_reads(tmp_path):
    from dmslog.store import bookmarks as bm

    path = tmp_path / "v3.sqlite"
    make_v3(path).close()
    ro = connect(path, readonly=True)  # the server reads a case that no write has migrated yet
    items = {i["note"]: i for i in bm.list_bookmarks(ro)[0]}
    assert items["came back"]["entry_id"] == 8 and not items["came back"]["orphan"]  # NULL cache, record back
    assert items["stale"]["entry_id"] == 7  # stale cache resolved by locator
    assert items["gone"]["orphan"] and items["dropped"]["orphan"]
    came_back = items["came back"]["id"]
    assert bm.get_bookmark(ro, came_back)["entry_id"] == 8
    marks = bm.for_entries(ro, [7, 8, 9])
    assert set(marks) == {7, 8} and marks[8]["id"] == came_back
    exported = {i["note"]: i for i in bm.export_bookmarks(ro)["bookmarks"]}
    assert exported["came back"]["fp"] is None and exported["came back"]["orphan"] is False
    assert exported["dropped"]["detached"] is True and exported["gone"]["detached"] is False
    ro.close()


# --------------------------------------------------------------------------- the caller's transaction


def pending_write(conn) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES ('caller', 'uncommitted')")
    assert conn.in_transaction


def caller_row(path) -> list:
    other = sqlite3.connect(path)
    try:
        return other.execute("SELECT value FROM meta WHERE key = 'caller'").fetchall()
    finally:
        other.close()


def test_migrate_on_a_current_case_leaves_the_callers_transaction_open_and_uncommitted(tmp_path):
    path = tmp_path / "case.sqlite"
    conn = create_case_db(path)
    pending_write(conn)
    assert migrate(conn) == SCHEMA_VERSION
    assert conn.in_transaction  # nothing committed on the caller's behalf
    assert caller_row(path) == []
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM meta WHERE key = 'caller'").fetchone()[0] == 0
    conn.close()


def test_migrate_refuses_to_run_a_step_inside_the_callers_transaction(tmp_path):
    path = tmp_path / "v3.sqlite"
    conn = make_v3(path)
    before = snapshot(conn)
    pending_write(conn)
    with pytest.raises(MigrationError, match="open transaction"):
        migrate(conn)
    assert conn.in_transaction and caller_row(path) == []
    conn.rollback()
    assert snapshot(conn) == before  # nothing migrated, nothing of the caller's committed
    assert migrate(conn) == SCHEMA_VERSION  # without the open transaction it runs
    conn.close()


def test_a_future_version_is_rejected_without_committing_the_callers_transaction(tmp_path):
    path = tmp_path / "future.sqlite"
    conn = create_case_db(path)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    pending_write(conn)
    with pytest.raises(SchemaVersionError):
        migrate(conn)
    assert conn.in_transaction and caller_row(path) == []
    conn.rollback()
    conn.close()
