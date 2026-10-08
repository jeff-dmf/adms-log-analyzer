"""Tests for dmslog.store.writer: batches, FTS sync, bucket counts, tail removal, rollback."""

import pytest

from dmslog.ingest.pipeline import generic_severity
from dmslog.parse.envelope import get_envelope
from dmslog.parse.records import parse_bytes
from dmslog.store import create_case_db
from dmslog.store.writer import BatchStats, IndexWriter

MIN = 60_000


def hdr(second: int, level: str, msg: str, minute: int = 0) -> str:
    return f"2030-01-15 10:{minute:02d}:{second:02d}.000 [PID=1][TID=0x1] {level} - {msg}"


DATA = "\n".join(
    [
        hdr(0, "INFO", "alpha started"),
        hdr(1, "ERROR", "request failed badly"),
        "  continuation with needle token",
        "  second continuation",
        hdr(2, "INFO", "beta done", minute=1),
        hdr(1, "WARNING", "clock went back", minute=1),
        "",
    ]
).encode()


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def new_source(w: IndexWriter) -> int:
    cid = w.add_collection(
        kind="file", path="/x", ers_guid=None, ers_ts=None, ers_variant=None, ers_group=None,
        system=None, machine=None, label=None,
    )  # fmt: skip
    group = w.collection_group(cid)
    stream = w.get_or_create_stream(
        collection_group=group, family="UNKNOWN", process=None, pid=None, account=None, service=None,
        sensitive=False,
    )  # fmt: skip
    return w.add_source(
        collection_id=cid, stream_id=stream, member="a.log", file_name="a.log", rotation=None,
        envelope="adms", size=None, mtime=None, zip_crc=None, status="pending",
    )  # fmt: skip


def fts_integrity(conn) -> None:
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")


def load(db, data: bytes = DATA):
    w = IndexWriter(db, severity=generic_severity)
    sid = new_source(w)
    w.begin()
    w.start_source(sid, None)
    stats = BatchStats()
    w.append(sid, parse_bytes(data, get_envelope("adms")), stats)
    w.commit()
    return w, sid, stats


def test_batch_inserts_entries_bodies_fts_and_buckets(db):
    w, sid, stats = load(db)
    assert stats.records == 4
    assert stats.lines == 6
    rows = db.execute("SELECT id, line_no, nlines, sev, flags, msg FROM entry ORDER BY id").fetchall()
    assert [r[1] for r in rows] == [1, 2, 5, 6]
    assert [r[2] for r in rows] == [1, 3, 1, 1]
    assert rows[1][4] & 2  # has_cont
    assert not rows[0][4] & 2
    bodies = db.execute("SELECT cont FROM entry_body").fetchall()
    assert bodies == [("  continuation with needle token\n  second continuation",)]
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'needle'").fetchone()[0] == 1
    hit = "SELECT rowid FROM entry_fts WHERE entry_fts MATCH ?"
    assert db.execute(hit, ("needle",)).fetchone()[0] == rows[1][0]
    assert db.execute(hit, ("beta",)).fetchone()[0] == rows[2][0]
    fts_integrity(db)
    buckets = db.execute("SELECT minute, sev, n FROM bucket ORDER BY minute, sev").fetchall()
    base = db.execute("SELECT min(ts) FROM entry").fetchone()[0] // MIN
    assert buckets == [(base, 2, 1), (base, 4, 1), (base + 1, 3, 1), (base + 1, 4, 1)]


def test_flags_partial_and_ts_out_of_order(db):
    w, sid, _ = load(db, DATA.rstrip(b"\n"))  # no final newline -> last record partial
    flags = [r[0] for r in db.execute("SELECT flags FROM entry ORDER BY id")]
    assert flags[3] & 1  # partial
    assert flags[3] & 32  # ts went back (10:01:02 -> 10:01:01)
    assert not flags[2] & 32


def test_truncate_from_removes_tail_everywhere(db):
    w, sid, _ = load(db)
    third = db.execute("SELECT byte_off FROM entry ORDER BY id LIMIT 1 OFFSET 2").fetchone()[0]
    w.begin()
    removed = w.truncate_from(sid, third)
    w.commit()
    assert removed == 2
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'beta'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'needle'").fetchone()[0] == 1
    assert db.execute("SELECT sum(n) FROM bucket").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM bucket WHERE n <= 0").fetchone()[0] == 0
    fts_integrity(db)


def test_truncate_of_a_record_with_body_removes_the_body_and_its_fts_text(db):
    w, sid, _ = load(db)
    last = w.last_entry(sid)
    second = db.execute("SELECT id, byte_off FROM entry ORDER BY id LIMIT 1 OFFSET 1").fetchone()
    w.begin()
    w.truncate_from(sid, second[1], min_id=second[0])
    w.commit()
    assert last.id > second[0]
    assert db.execute("SELECT count(*) FROM entry_body").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'needle'").fetchone()[0] == 0
    fts_integrity(db)


def test_delete_source_entries_and_reload_gives_same_content(db):
    w, sid, _ = load(db)
    w.begin()
    w.delete_source_entries(sid)
    w.commit()
    for table in ("entry", "entry_body", "bucket"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    fts_integrity(db)
    w.begin()
    w.start_source(sid, None)
    w.append(sid, parse_bytes(DATA, get_envelope("adms")))
    w.commit()
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 4
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'alpha'").fetchone()[0] == 1
    fts_integrity(db)


def test_rollback_discards_the_open_batch(db):
    w = IndexWriter(db)
    sid = new_source(w)
    w.commit()
    w.begin()
    w.start_source(sid, None)
    w.append(sid, parse_bytes(DATA, get_envelope("adms")))
    w.flush()
    w.append(sid, parse_bytes(DATA, get_envelope("adms")))  # still only buffered
    w.rollback()
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM entry_fts").fetchone()[0] == 0
    w.begin()
    w.start_source(sid, None)
    w.append(sid, parse_bytes(DATA, get_envelope("adms")))
    w.commit()
    assert db.execute("SELECT min(id) FROM entry").fetchone()[0] == 1
    fts_integrity(db)


def test_script_text_is_stored_literally(db):
    data = (hdr(0, "INFO", "user <script>alert(1)</script> end") + '\n<b onclick="x">cont</b>\n').encode()
    load(db, data)
    msg = db.execute("SELECT msg FROM entry").fetchone()[0]
    cont = db.execute("SELECT cont FROM entry_body").fetchone()[0]
    assert msg == "user <script>alert(1)</script> end"
    assert cont == '<b onclick="x">cont</b>'


def test_stream_is_shared_by_rotated_files_and_split_by_sensitivity(db):
    w = IndexWriter(db)
    kw = dict(collection_group=1, family="AWFM", process="WfmAdapter", pid=7, account="a", service="s")
    a = w.get_or_create_stream(sensitive=False, **kw)
    assert w.get_or_create_stream(sensitive=False, **kw) == a
    assert w.get_or_create_stream(sensitive=True, **kw) != a
    other = dict(kw, process=None, pid=None)
    no_pid = w.get_or_create_stream(sensitive=False, **other)
    assert w.get_or_create_stream(sensitive=False, **other) == no_pid
    w.commit()


def test_rename_sources_can_swap_members(db):
    w = IndexWriter(db)
    s1 = new_source(w)
    cid = db.execute("SELECT collection_id FROM source WHERE id=?", (s1,)).fetchone()[0]
    s2 = w.add_source(
        collection_id=cid, stream_id=None, member="b.log", file_name="b.log", rotation=None, envelope="adms",
        size=None, mtime=None, zip_crc=None, status="pending",
    )  # fmt: skip
    w.rename_sources({s1: ("b.log", "b.log", None), s2: ("a.log", "a.log", 0)})
    w.commit()
    rows = dict(db.execute("SELECT id, member FROM source").fetchall())
    assert rows == {s1: "b.log", s2: "a.log"}


def test_update_source_rejects_unknown_columns(db):
    w = IndexWriter(db)
    sid = new_source(w)
    with pytest.raises(ValueError):
        w.update_source(sid, nope=1)
