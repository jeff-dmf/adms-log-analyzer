"""Key and link queries over a synthetic case (rows inserted directly)."""

from __future__ import annotations

import pytest

from dmslog.store import create_case_db, keys
from dmslog.store.events import Cursor

T0 = 1_900_000_000_000
HOSTILE = "x'; DROP TABLE entry_key; --"


def seed(conn):
    """Normal stream 1 / sensitive stream 2 (one group); entries 1..12; ts ties on purpose."""
    conn.execute(
        "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES"
        " (1, 1, 'AWFM', 'WfmAdapter', 100, 0), (2, 1, 'AWFM', 'WfmAdapter', 100, 1)"
    )
    conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')")
    conn.execute(
        "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope) VALUES"
        " (1, 1, 1, 'Log/a.log', 'a.log', 'adms'),"
        " (2, 1, 2, 'Log/a_sensitive.log', 'a_sensitive.log', 'adms'),"
        " (3, 1, NULL, 'Log/loose.log', 'loose.log', 'plain')"
    )
    conn.execute(
        "INSERT INTO template(id, code, family, class, pattern, def_version)"
        " VALUES (1, 'TST-001', 'AWFM', 'start', 'x', 'v')"
    )
    rows = []
    for i in range(1, 11):  # 1..5 normal, 6..10 sensitive; ts ties by pairs
        source = 1 if i <= 5 else 2
        rows.append((i, source, T0 + (i // 2) * 1000, 7 if source == 1 else 8, f"msg {i} " + "y" * 400))
    rows.append((11, 3, None, None, "no time"))
    rows.append((12, 3, None, None, "no time either"))
    for id_, source, ts, tid, msg in rows:
        conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, level, sev, tpl_id, msg)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (id_, source, id_, id_, 1, ts, tid, 3, 4, 1 if id_ == 1 else 0, msg),
        )
    for entry_id in range(1, 13):
        conn.execute(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES ('msgid', 'M-1', ?)", (entry_id,)
        )
    conn.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('msgid', 'M-2', 1)")
    conn.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('errcode', '50%/ \u00e9', 2)")
    conn.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('errcode', ?, 3)", (HOSTILE,))
    conn.executemany(
        "INSERT INTO link VALUES (?,?,?,?)",
        [
            (1, 6, "normal_sensitive", 2),
            (1, 7, "same_key", 3),
            (1, 8, "normal_sensitive", 3),
            (3, 1, "retry", 1),
            (9, 10, "callback", 0),
        ],
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "c" / "case.sqlite")
    seed(c)
    yield c
    c.close()


def test_key_types_counts(conn):
    assert keys.key_types(conn) == [
        {"type": "errcode", "count": 2, "values": 2},
        {"type": "msgid", "count": 13, "values": 2},
    ]


def test_records_fields_and_msg_cut(conn):
    records, nxt = keys.records_with_key(conn, "msgid", "M-2")
    assert nxt is None and len(records) == 1
    rec = records[0]
    assert rec["id"] == 1 and rec["member"] == "Log/a.log" and rec["family"] == "AWFM"
    assert rec["process"] == "WfmAdapter" and rec["sensitive"] is False and rec["tpl_code"] == "TST-001"
    assert rec["tid"] == 7 and len(rec["msg"]) == keys.MSG_CHARS


def test_records_unknown_stream_and_null_ts(conn):
    records, _ = keys.records_with_key(conn, "msgid", "M-1", limit=1000)
    loose = [r for r in records if r["id"] in (11, 12)]
    assert [r["id"] for r in records[:2]] == [11, 12]  # records without a time sort first
    assert all(r["sensitive"] is None and r["family"] is None and r["ts"] is None for r in loose)


def test_keyset_pagination_has_no_gaps_or_duplicates_on_ties(conn):
    full, _ = keys.records_with_key(conn, "msgid", "M-1", limit=1000)
    assert [r["id"] for r in full] == sorted(
        (r["id"] for r in full), key=lambda i: (next(x["ts"] for x in full if x["id"] == i) or -1, i)
    )
    for size in (1, 2, 3, 5, 11):
        seen, after = [], None
        while True:
            page, nxt = keys.records_with_key(conn, "msgid", "M-1", size, after)
            assert len(page) <= size
            seen += [r["id"] for r in page]
            if nxt is None:
                break
            after = Cursor.parse(nxt)
            assert after is not None
        assert seen == [r["id"] for r in full], size
        assert len(set(seen)) == len(seen) == 12


def test_odd_and_hostile_values_are_only_parameters(conn):
    assert [r["id"] for r in keys.records_with_key(conn, "errcode", "50%/ \u00e9")[0]] == [2]
    assert [r["id"] for r in keys.records_with_key(conn, "errcode", HOSTILE)[0]] == [3]
    assert keys.records_with_key(conn, "errcode", "50%")[0] == []  # no LIKE semantics
    assert keys.records_with_key(conn, "msgid' OR '1'='1", "M-1")[0] == []
    assert conn.execute("SELECT count(*) FROM entry_key").fetchone()[0] == 15


def test_links_both_directions(conn):
    out = keys.links_of(conn, 1)
    assert {(x["entry_id"], x["kind"], x["direction"]) for x in out} == {
        (6, "normal_sensitive", "out"),
        (7, "same_key", "out"),
        (8, "normal_sensitive", "out"),
        (3, "retry", "in"),
    }
    sens = next(x for x in out if x["entry_id"] == 6)
    assert sens["source"] == {"member": "Log/a_sensitive.log", "sensitive": True}
    assert sens["confidence"] == 2 and sens["tid"] == 8 and len(sens["msg"]) == keys.MSG_CHARS
    back = keys.links_of(conn, 6)
    assert [(x["entry_id"], x["direction"]) for x in back] == [(1, "in")]
    assert keys.links_of(conn, 12) == []


def test_best_counterpart_prefers_normal_sensitive_then_confidence(conn):
    best = keys.best_counterpart(conn, 1)
    assert (best.entry_id, best.kind, best.confidence) == (8, "normal_sensitive", 3)
    assert keys.best_counterpart(conn, 3).entry_id == 1  # reverse direction
    assert keys.best_counterpart(conn, 12) is None


def test_sensitivity_and_exists(conn):
    assert keys.sensitivity_of(conn, 1) is False and keys.sensitivity_of(conn, 6) is True
    assert keys.sensitivity_of(conn, 11) is None and keys.sensitivity_of(conn, 999) is None
    assert keys.entry_exists(conn, 12) and not keys.entry_exists(conn, 13)
