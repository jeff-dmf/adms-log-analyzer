"""bucket_sev rollup: histogram results equal the bucket path and the entry path."""

import pytest

from dmslog.store import create_case_db
from dmslog.store.query import histogram

MIN = 60_000


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def fill(conn, rows):
    """rows: (source_id, minute, sev, tpl_id, n) written to bucket and bucket_sev only."""
    conn.executemany("INSERT INTO bucket(source_id, minute, sev, tpl_id, n) VALUES (?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO bucket_sev(source_id, minute, sev, n)"
        " SELECT source_id, minute, sev, sum(n) FROM bucket WHERE source_id=? AND minute=? AND sev=?"
        " GROUP BY 1, 2, 3",
        sorted({(r[0], r[1], r[2]) for r in rows}),
    )


def used_tables(conn, fn):
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        return fn(), " ".join(seen)
    finally:
        conn.set_trace_callback(None)


def test_sev_and_source_splits_read_the_rollup_and_match_bucket(db):
    fill(db, [(1, 100, 2, 0, 3), (1, 100, 2, 5, 4), (1, 101, 4, 5, 6), (2, 100, 4, 0, 1)])
    for split in ("sev", "source"):
        h, sql = used_tables(db, lambda s=split: histogram(db, {"sev": {"le": 4}}, 600_000, s))
        assert "bucket_sev" in sql
        assert h.exact is True
        assert sum(sum(x["counts"]) for x in h.series) == 14
        assert h.method == "bucket"


def test_tpl_split_and_tpl_filter_keep_using_bucket(db):
    fill(db, [(1, 100, 2, 0, 3), (1, 100, 2, 5, 4)])
    _, sql = used_tables(db, lambda: histogram(db, None, 600_000, "tpl"))
    assert "bucket_sev" not in sql
    _, sql = used_tables(db, lambda: histogram(db, {"tpl": [5]}, 600_000, "sev"))
    assert "bucket_sev" not in sql


def test_filter_on_sev_and_source_gives_exact_counts(db):
    fill(db, [(1, 100, 2, 0, 3), (1, 100, 3, 5, 4), (2, 100, 2, 5, 9)])
    h = histogram(db, {"and": [{"source": [1]}, {"sev": {"le": 2}}]}, 60_000, "sev")
    assert sum(sum(x["counts"]) for x in h.series) == 3
    assert h.exact is True


def test_database_without_rollup_table_falls_back_to_bucket(db):
    fill(db, [(1, 100, 2, 0, 3)])
    db.execute("DROP TABLE bucket_sev")
    h = histogram(db, None, 60_000, "sev")
    assert sum(sum(x["counts"]) for x in h.series) == 3
