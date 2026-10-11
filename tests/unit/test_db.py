import sqlite3
import subprocess
import sys
import zipapp
from pathlib import Path

import pytest

from dmslog.store import SCHEMA_VERSION, SchemaVersionError, connect, create_case_db, migrate

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
    assert migrate(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT source_id, minute, sev, n FROM bucket_sev ORDER BY 1, 2, 3").fetchall()
    assert rows == [(1, 10, 2, 7), (1, 10, 4, 5), (2, 11, 2, 1)]
    assert migrate(conn) == SCHEMA_VERSION
    conn.close()


def test_migration_4_adds_the_fingerprint_and_rebuilds_bookmark_caches(tmp_path):
    from dmslog.store import bookmarks as bm
    from dmslog.store.db import MIGRATIONS

    conn = connect(tmp_path / "v3.sqlite")
    MIGRATIONS[1](conn)
    conn.execute("ALTER TABLE bookmark DROP COLUMN fp")  # what a version-3 database looks like
    conn.execute("PRAGMA user_version=3")
    add_entry(conn, "kept record", entry_id=7)
    conn.execute(
        "INSERT INTO bookmark(collection_id, member, line_no, entry_id, created_at) VALUES"
        " (1, 'a.log', 1, 3, 't'),"  # stale cache from a reindex before v4
        " (1, 'a.log', 9, 4, 't')"  # its record is gone
    )
    conn.commit()
    assert migrate(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT line_no, entry_id, fp FROM bookmark ORDER BY id").fetchall()
    assert rows == [(1, 7, bm.fingerprint(0, None, "kept record")), (9, None, None)]
    assert migrate(conn) == SCHEMA_VERSION
    conn.close()
