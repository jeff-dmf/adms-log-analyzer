"""``pool``/``instance`` read-time annotation in events, key records and links (R2b)."""

from __future__ import annotations

import pytest

from dmslog.store import create_case_db, events, keys
from dmslog.store.events import EventFilter

T0 = 1_900_000_000_000


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "c" / "case.sqlite")
    c.execute(
        "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES"
        " (1, 1, 'DMSFH', 'DMS_FunctionHost_7', NULL, 0), (2, 1, 'DMSJOB', 'DMS_JobLauncher', 100, 0)"
    )
    c.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')")
    c.execute(
        "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope) VALUES"
        " (1, 1, 1, 'Log/fh7.log', 'fh7.log', 'adms'), (2, 1, 2, 'Log/job.log', 'job.log', 'adms')"
    )
    for i, source in ((1, 1), (2, 2)):
        c.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, level, sev, tpl_id, msg)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (i, source, i, i, 1, T0 + i, 1, 3, 4, 0, "m"),
        )
        c.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('job_id', '100001', ?)", (i,))
    c.execute("INSERT INTO link VALUES (1, 2, 'handoff', 3)")
    for stream in (1, 2):
        c.execute(
            "INSERT INTO event(stream_id, kind, name, key, ts_start, ts_end, duration_ms, status, sev)"
            " VALUES (?, 'operation', 'Job', '100001', ?, ?, 5, 'ok', 4)",
            (stream, T0, T0 + 5),
        )
    c.commit()
    yield c
    c.close()


def test_events_carry_pool_and_instance(conn):
    page, _ = events.list_events(conn, EventFilter())
    by_family = {e["family"]: e for e in page}
    assert (by_family["DMSFH"]["pool"], by_family["DMSFH"]["instance"]) == ("DMS_FunctionHost", 7)
    assert by_family["DMSFH"]["process"] == "DMS_FunctionHost_7"
    assert by_family["DMSJOB"]["pool"] is None and by_family["DMSJOB"]["instance"] is None


def test_key_records_carry_pool_and_instance(conn):
    records, _ = keys.records_with_key(conn, "job_id", "100001")
    by_family = {r["family"]: r for r in records}
    assert (by_family["DMSFH"]["pool"], by_family["DMSFH"]["instance"]) == ("DMS_FunctionHost", 7)
    assert by_family["DMSJOB"]["pool"] is None and by_family["DMSJOB"]["instance"] is None


def test_links_carry_pool_and_instance(conn):
    (link,) = keys.links_of(conn, 2)
    assert link["family"] == "DMSFH" and (link["pool"], link["instance"]) == ("DMS_FunctionHost", 7)
    (back,) = keys.links_of(conn, 1)
    assert back["pool"] is None and back["instance"] is None
