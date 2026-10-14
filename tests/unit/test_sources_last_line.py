"""``source.last_line`` (schema 7): written by every indexing batch, filled once for older cases, read by
``/api/sources`` with the same result as the per-request lookup it replaces (issue #142)."""

from __future__ import annotations

import sqlite3
import threading

import pytest
import test_pipeline as tp
import test_writer as tw
from test_api import Client

from dmslog.ingest.pipeline import run_ingest
from dmslog.ingest.watch import Watcher
from dmslog.parse.envelope import get_envelope
from dmslog.parse.records import parse_bytes
from dmslog.server.app import DmslogServer
from dmslog.store import db as store_db
from dmslog.store.writer import IndexWriter

WFM, WFM_ROTATED = tp.WFM, tp.WFM_ROTATED
EMPTY = f"{tp.LOGDIR}/WfmAdapter_2222.log"

# The lookup /api/sources ran before schema 7 (one temporary B-tree per source): the reference result.
REFERENCE_SQL = (
    "SELECT s.id, (SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id"
    " ORDER BY e.id DESC LIMIT 1) FROM source s ORDER BY s.id"
)


def reference(conn) -> dict[int, int]:
    """``{source id: lines}`` as /api/sources reported them before schema 7 (no record = 0)."""
    return {sid: line or 0 for sid, line in conn.execute(REFERENCE_SQL)}


def stored(conn) -> dict[int, int | None]:
    return dict(conn.execute("SELECT id, last_line FROM source ORDER BY id"))


def build_folder(tmp_path):
    """A rotated file and the current one (records with continuation lines) and an empty file."""
    return tp.write_folder(
        tmp_path / "Log",
        {
            WFM_ROTATED: tp.make_log(30, cont_every=3),
            WFM: tp.make_log(25, start=9000, cont_every=2),
            EMPTY: b"",
        },
    )


def downgrade_to_6(path) -> None:
    """What a case indexed by a dmslog before schema 7 looks like: no ``source.last_line``."""
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE source DROP COLUMN last_line")
    conn.execute("PRAGMA user_version=6")
    conn.commit()
    conn.close()


def untouched_rows(conn) -> tuple:
    """Everything a reindex would rewrite (ids included)."""
    return (
        conn.execute("SELECT * FROM entry ORDER BY id").fetchall(),
        conn.execute(
            "SELECT id, member, indexed_bytes, head_hash, tail_hash, n_entries, status FROM source"
            " ORDER BY id"
        ).fetchall(),
    )


@pytest.fixture
def server(tmp_path):
    srv = DmslogServer(0, data_dir=tmp_path / "data")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


@pytest.fixture
def client(server):
    return Client(server)


@pytest.fixture
def case_path(tmp_path, server, client):
    """The database of a case ``demo`` holding :func:`build_folder`, indexed by the pipeline."""
    assert client.post("/api/cases", {"name": "demo"})[0] == 201
    path = server.api.store.db_path("demo")
    conn = store_db.create_case_db(path)
    try:
        run_ingest(conn, build_folder(tmp_path))
    finally:
        conn.close()
    return path


def api_lines(client) -> dict[int, int]:
    status, body, _ = client.get("/api/sources?case=demo")
    assert status == 200
    return {s["id"]: s["lines"] for s in body["sources"]}


def test_the_listing_reports_the_same_lines_as_the_record_lookup(client, case_path):
    conn = store_db.connect(case_path, readonly=True)
    try:
        expected = reference(conn)
        by_member = dict(conn.execute("SELECT member, id FROM source"))
        rotation = dict(conn.execute("SELECT member, rotation FROM source"))
        assert stored(conn) == {sid: line or None for sid, line in expected.items()}
        multi = conn.execute("SELECT count(*) FROM entry WHERE nlines > 1").fetchone()[0]
    finally:
        conn.close()
    assert multi > 0 and rotation[WFM_ROTATED] == 0  # continuation lines and a rotated file are covered
    assert expected[by_member[WFM_ROTATED]] == tp.lines_of(tp.make_log(30, cont_every=3))
    assert expected[by_member[WFM]] == tp.lines_of(tp.make_log(25, start=9000, cont_every=2))
    assert expected[by_member[EMPTY]] == 0
    assert api_lines(client) == expected


def test_the_fallback_lookup_seeks_by_max_id_without_a_temporary_btree(case_path):
    conn = store_db.connect(case_path, readonly=True)
    try:
        sql = f"SELECT s.id, ({store_db.LAST_LINE_SQL.format(source='s.id')}) FROM source s ORDER BY s.id"
        plan = " | ".join(str(r[3]) for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}"))
        assert "TEMP B-TREE" not in plan and "INTEGER PRIMARY KEY" in plan
        assert dict(conn.execute(sql)) == dict(conn.execute(REFERENCE_SQL))
    finally:
        conn.close()


def test_a_case_from_schema_6_lists_the_same_lines_and_is_filled_once_without_reindexing(client, case_path):
    conn = store_db.connect(case_path, readonly=True)
    expected = reference(conn)
    conn.close()
    downgrade_to_6(case_path)
    assert api_lines(client) == expected  # read-only, not migrated yet: the max(id) lookup
    conn = store_db.connect(case_path)
    before = untouched_rows(conn)
    assert store_db.get_user_version(conn) == 6
    assert store_db.migrate(conn) == store_db.SCHEMA_VERSION == 7  # what opening the case for a write does
    assert untouched_rows(conn) == before  # no record nor source rewritten: nothing was reindexed
    assert stored(conn) == {sid: line or None for sid, line in expected.items()}
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'source'").fetchone()
    assert store_db.migrate(conn) == 7  # idempotent
    store_db.MIGRATIONS[7](conn)  # the step itself too (the column exists, no NULL left to fill)
    conn.commit()
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'source'").fetchone() == schema
    assert stored(conn) == {sid: line or None for sid, line in expected.items()}
    conn.close()
    assert api_lines(client) == expected


def test_a_stale_null_falls_back_to_the_lookup(client, case_path):
    conn = store_db.connect(case_path)
    expected = reference(conn)
    conn.execute("UPDATE source SET last_line = NULL")
    conn.commit()
    conn.close()
    assert api_lines(client) == expected


def test_incremental_and_watch_cycles_keep_the_column_up_to_date(tmp_path, client, case_path):
    root = tmp_path / "Log"
    conn = store_db.create_case_db(case_path)
    try:
        watcher = Watcher(conn, root)
        first = watcher.cycle()  # the first cycle of the watcher ingests: the folder is already indexed
        assert first.ran and not first.news
        grown = tp.make_log(25, start=9000, cont_every=2) + tp.make_log(12, start=40_000, cont_every=1)
        (root / WFM).write_bytes(grown)
        (root / EMPTY).write_bytes(tp.make_log(4, pid=2222, start=70_000))
        report = watcher.cycle()
        assert report.ran
        assert {d.member: d.action for d in report.news} == {WFM: "append", EMPTY: "append"}
        ids = dict(conn.execute("SELECT member, id FROM source"))
        assert stored(conn)[ids[WFM]] == tp.lines_of(grown)
        assert stored(conn)[ids[EMPTY]] == tp.lines_of(tp.make_log(4, pid=2222, start=70_000))
        assert stored(conn) == reference(conn)
        tp.assert_consistent(conn)
        expected = reference(conn)
    finally:
        conn.close()
    assert api_lines(client) == expected


# --------------------------------------------------------------------------- every IndexWriter path


def last_line_of(conn, sid: int) -> int | None:
    return conn.execute("SELECT last_line FROM source WHERE id = ?", (sid,)).fetchone()[0]


def checked(conn, sid: int, expected: int | None) -> None:
    """The column holds ``expected`` and equals the reference lookup for every source."""
    assert last_line_of(conn, sid) == expected
    tp.assert_last_line_matches_records(conn)


def test_every_writer_insert_and_delete_keeps_the_column_equal_to_the_records(tmp_path):
    conn = store_db.create_case_db(tmp_path / "case.sqlite")
    try:
        w, sid, _ = tw.load(conn)  # lines 1, 2-4 (continuation), 5, 6
        checked(conn, sid, 6)
        w.begin()
        w.start_source(sid, None)
        w.append(sid, parse_bytes(tw.DATA, get_envelope("adms"), start_off=len(tw.DATA), start_line=7))
        w.flush()  # visible inside the open transaction, before the commit
        checked(conn, sid, 12)
        w.commit()
        checked(conn, sid, 12)  # direct append (review reproduction: stayed at 6)
        third = conn.execute(
            "SELECT id, byte_off FROM entry WHERE source_id = ? ORDER BY id LIMIT 1 OFFSET 2", (sid,)
        ).fetchone()
        w.begin()
        w.truncate_from(sid, third[1], min_id=third[0])
        w.commit()
        checked(conn, sid, 4)  # truncation (review reproduction: stayed at 6)
        w.begin()
        assert w.truncate_from(sid, 10**9) == 0  # nothing at or after the cut: nothing changes
        w.commit()
        checked(conn, sid, 4)
        w.begin()
        w.delete_source_entries(sid)
        w.commit()
        checked(conn, sid, None)  # full deletion (review reproduction: stayed at 6); /api/sources says 0
        w.begin()
        w.start_source(sid, None)
        w.append(sid, parse_bytes(tw.DATA, get_envelope("adms")))
        w.commit()
        checked(conn, sid, 6)  # reload after the deletion
        w.begin()
        w.start_source(sid, None)
        w.append(sid, parse_bytes(tw.DATA, get_envelope("adms"), start_off=len(tw.DATA), start_line=7))
        w.flush()
        w.rollback()
        checked(conn, sid, 6)  # a rolled-back batch leaves the committed value
    finally:
        conn.close()


def test_removing_a_group_or_renaming_sources_leaves_the_other_sources_right(tmp_path):
    conn = store_db.create_case_db(tmp_path / "case.sqlite")
    try:
        w, first, _ = tw.load(conn)
        second = tw.new_source(w)  # a collection (and group) of its own
        w.begin()
        w.start_source(second, None)
        w.append(second, parse_bytes(tw.DATA[: tw.DATA.index(b"\n") + 1], get_envelope("adms")))
        w.commit()
        checked(conn, second, 1)
        w.rename_sources({first: ("a.log.0", "a.log.0", 0)})  # rotation moves the source, not its records
        w.commit()
        checked(conn, first, 6)
        group = conn.execute(
            "SELECT c.ers_group FROM source s JOIN collection c ON c.id = s.collection_id WHERE s.id = ?",
            (first,),
        ).fetchone()[0]
        assert w.remove_group(group) is not None
        assert conn.execute("SELECT count(*) FROM source WHERE id = ?", (first,)).fetchone()[0] == 0
        checked(conn, second, 1)
    finally:
        conn.close()


def test_the_listing_reports_zero_after_a_direct_full_deletion(client, case_path):
    conn = store_db.create_case_db(case_path)
    try:
        sid = conn.execute("SELECT id FROM source WHERE member = ?", (WFM,)).fetchone()[0]
        w = IndexWriter(conn)
        w.begin()
        w.delete_source_entries(sid)
        w.commit()
        assert last_line_of(conn, sid) is None
        expected = reference(conn)
    finally:
        conn.close()
    lines = api_lines(client)
    assert lines == expected and lines[sid] == 0
