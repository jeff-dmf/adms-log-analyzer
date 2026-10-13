"""Event and statistics queries over a synthetic case (rows inserted directly)."""

from __future__ import annotations

import json

import pytest

from dmslog.store import create_case_db, events
from dmslog.store.events import Cursor, EventFilter

T0 = events.parse_log_time("2030-01-15 10:00:00.000")
MINUTE = 60_000
N_SEND = 20  # SendA durations 1..20


def seed(conn):
    """Two streams; 20 SendA ops (durations 1..20, ts ties of 3), one open SendA, GetB, exceptions."""
    conn.execute(
        "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES"
        " (1, 1, 'AWFM', 'WfmAdapter', 100, 0), (2, 1, 'AONT', 'OntAdapter', 200, 1)"
    )
    conn.execute(
        "INSERT INTO template(id, code, family, class, pattern, def_version)"
        " VALUES (1, 'TST-001', 'AWFM', 'start', 'x', 'v')"
    )
    rows = []
    for i in range(N_SEND):
        status = "fail" if i % 10 == 9 else "ok"
        rows.append(
            (
                1,
                "operation",
                "SendA",
                f"K{i:03d}",
                T0 + (i // 3) * 1000,
                i + 1,
                status,
                4 if status == "ok" else 2,
            )
        )
    rows.append((1, "operation", "SendA", "K999", T0 + 30_000, None, "open", 4))
    for i, d in enumerate((100, 200, 300)):
        rows.append((2, "operation", "GetB", f"B{i}", T0 + 2 * MINUTE + i * 10, d, "ok", 4))
    rows.append((1, "exception", "Boom", "K003", T0 + 5000, None, None, 2))
    rows.append((1, "retry_seq", "retry", "K003", T0 + 6000, 50, "ok", 3))
    rows.append((1, "level_window", "Info", None, T0 + 7000, 900, None, 5))
    for stream, kind, name, key, ts, dur, status, sev in rows:
        detail = json.dumps({"op_key": key}) if kind == "operation" and key else None
        conn.execute(
            "INSERT INTO event(stream_id, kind, name, key, ts_start, ts_end, duration_ms, status, sev,"
            " detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (stream, kind, name, key, ts, None if dur is None else ts + dur, dur, status, sev, detail),
        )
    conn.commit()


def seed_entries(conn, event_id, n, source_id=1, first=1):
    for i in range(n):
        conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, level, sev, tpl_id,"
            " event_id, msg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                first + i,
                source_id,
                i,
                i,
                1,
                T0 + i,
                7,
                3,
                4,
                1 if i == 0 else 0,
                event_id,
                f"msg {i} " + "x" * 400,
            ),
        )
    conn.commit()


def seed_stats(conn):
    samples = [
        (1, T0, "client", "[TOTAL]", 10, 100, 200, None, None, None),
        (1, T0 + MINUTE, "client", "[TOTAL]", 15, 150, 300, 5, 50, 100),
        (1, T0, "client", "A.B", 4, 40, 80, None, None, None),
        (1, T0 + MINUTE, "service", "A.B", 9, 90, 10, 9, 90, 10),
        (2, T0 + 2 * MINUTE, "client", "[TOTAL]", 1, 1, 1, None, None, None),
    ]
    conn.executemany("INSERT INTO stat_sample VALUES (?,?,?,?,?,?,?,?,?,?)", samples)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "c" / "case.sqlite")
    seed(c)
    yield c
    c.close()


def test_parse_log_time():
    assert events.parse_log_time("1970-01-01 00:00:01.5") == 1500
    assert events.parse_log_time("1970-01-02") == 86_400_000
    assert events.parse_log_time("1970-01-01T00:01") == 60_000
    for bad in ("", "abc", "2030-13-01", "2030-02-30", "2030-01-01 25:00", "2030-01-01 10:00:00.1234"):
        assert events.parse_log_time(bad) is None


def test_cursor_roundtrip():
    assert str(Cursor.parse("5:7")) == "5:7"
    assert Cursor.parse("x") is None and Cursor.parse("1:y") is None and Cursor.parse("1") is None


def test_pagination_has_no_gaps_or_duplicates(conn):
    everything, nxt = events.list_events(conn, EventFilter(), limit=1000)
    assert nxt is None and len(everything) == conn.execute("SELECT count(*) FROM event").fetchone()[0]
    assert [(e["ts_start"], e["id"]) for e in everything] == sorted(
        (e["ts_start"], e["id"]) for e in everything
    )
    for size in (1, 2, 3, 7):
        seen, cursor, pages = [], None, 0
        while True:
            page, nxt = events.list_events(conn, EventFilter(), limit=size, after=cursor)
            seen += [e["id"] for e in page]
            pages += 1
            if nxt is None:
                break
            assert len(page) == size
            cursor = Cursor.parse(nxt)
        assert seen == [e["id"] for e in everything], size
        assert pages == -(-len(everything) // size)


def test_event_shape(conn):
    page, _ = events.list_events(conn, EventFilter(kinds=("operation",), names=("GetB",)), limit=1)
    (event,) = page
    assert set(event) == {
        "id", "stream_id", "family", "process", "sensitive", "kind", "name", "key", "tid", "start_entry_id",
        "end_entry_id", "ts_start", "ts_end", "duration_ms", "status", "sev", "n_entries", "detail",
    }  # fmt: skip
    assert event["family"] == "AONT" and event["sensitive"] is True and event["detail"] == {"op_key": "B0"}


def test_filters(conn):
    def ids(**kw):
        return [e["name"] + ":" + str(e["key"]) for e in events.list_events(conn, EventFilter(**kw), 1000)[0]]

    assert len(ids(kinds=("operation",))) == N_SEND + 1 + 3
    assert len(ids(kinds=("exception", "retry_seq"))) == 2
    assert len(ids(kinds=("operation",), names=("GetB", "SendA"))) == N_SEND + 4
    assert sorted(ids(statuses=("fail",))) == ["SendA:K009", "SendA:K019"]
    assert len(ids(statuses=("open", "fail"))) == 3
    assert len(ids(stream_id=2)) == 3
    assert len(ids(key_query="K003")) == 3
    assert len(ids(key_query="K00")) == 10 + 2  # K000..K009 plus the exception and retry keyed K003
    assert ids(key_query="nope") == []
    assert len(ids(ts_from=T0 + 2 * MINUTE)) == 3
    assert len(ids(ts_to=T0 - 1)) == 0
    assert len(ids(ts_from=T0, ts_to=T0 + 999, kinds=("operation",))) == 3  # i // 3 == 0
    assert ids(key_query="%") == [] and ids(key_query="_") == []  # LIKE wildcards are literal


def test_source_filter(conn):
    first = conn.execute("SELECT id FROM event WHERE key = 'K000'").fetchone()[0]
    conn.execute("UPDATE event SET start_entry_id = 1 WHERE id = ?", (first,))
    seed_entries(conn, first, 2, source_id=5)
    assert [e["id"] for e in events.list_events(conn, EventFilter(source_id=5))[0]] == [first]
    assert events.list_events(conn, EventFilter(source_id=6))[0] == []


def test_user_text_is_never_sql(conn):
    hostile = "x'; DROP TABLE event; --"
    assert events.list_events(conn, EventFilter(names=(hostile,), key_query=hostile))[0] == []
    assert conn.execute("SELECT count(*) FROM event").fetchone()[0] > 0


def test_get_event_with_entries_and_related(conn):
    op = conn.execute("SELECT id FROM event WHERE key = 'K003' AND kind = 'operation'").fetchone()[0]
    seed_entries(conn, op, 250)
    result = events.get_event(conn, op)
    assert result["entries_total"] == 250 and len(result["entries"]) == events.ENTRIES_LIMIT
    ids = [e["id"] for e in result["entries"]]
    assert ids == sorted(ids) and ids[0] == 1
    assert len(result["entries"][0]["msg"]) == events.MSG_CHARS
    assert result["entries"][0]["tpl_code"] == "TST-001" and result["entries"][1]["tpl_code"] is None
    assert [e["kind"] for e in result["related"]] == ["exception", "retry_seq"]


def test_get_event_without_key_has_no_related(conn):
    window = conn.execute("SELECT id FROM event WHERE kind = 'level_window'").fetchone()[0]
    result = events.get_event(conn, window)
    assert "related" not in result and result["entries"] == [] and result["entries_total"] == 0


def test_related_is_capped(conn):
    for i in range(30):
        conn.execute(
            "INSERT INTO event(stream_id, kind, name, key, ts_start, sev)"
            " VALUES (1, 'retry_seq', 'r', 'KR', ?, 3)",
            (T0 + i,),
        )
    conn.execute(
        "INSERT INTO event(id, stream_id, kind, name, key, ts_start, sev)"
        " VALUES (9000, 1, 'operation', 'o', 'KR', 1, 4)"
    )
    assert len(events.get_event(conn, 9000)["related"]) == events.RELATED_LIMIT


def test_get_unknown_event(conn):
    assert events.get_event(conn, 123456) is None


def test_stats_exact_percentiles_on_a_known_set(conn):
    result = events.event_stats(conn, EventFilter(kinds=("operation",)))
    by_name = {(g["family"], g["name"]): g for g in result["groups"]}
    send = by_name[("AWFM", "SendA")]
    # durations 1..20: nearest rank p50 = 10, p90 = 18, p99 = ceil(19.8) = 20
    assert (send["p50_ms"], send["p90_ms"], send["p99_ms"], send["max_ms"], send["avg_ms"]) == (
        10,
        18,
        20,
        20,
        10.5,
    )
    assert (send["n"], send["ok"], send["fail"], send["open"], send["timeout"]) == (21, 18, 2, 1, 0)
    get_b = by_name[("AONT", "GetB")]
    assert (get_b["p50_ms"], get_b["p90_ms"], get_b["p99_ms"], get_b["max_ms"], get_b["avg_ms"]) == (
        200,
        300,
        300,
        300,
        200,
    )
    totals = result["totals"]
    assert totals["n"] == 24 and totals["ok"] == 21 and totals["fail"] == 2 and totals["open"] == 1
    # 23 durations: 1..20 + 100,200,300 -> rank 12 = 12, rank 21 = 100, rank 23 = 300
    assert (totals["p50_ms"], totals["p90_ms"], totals["p99_ms"], totals["max_ms"]) == (12, 100, 300, 300)


def test_stats_without_durations_and_filters(conn):
    result = events.event_stats(conn, EventFilter(kinds=("exception",)))
    (group,) = result["groups"]
    assert group["n"] == 1 and group["p50_ms"] is None and group["avg_ms"] is None and group["max_ms"] is None
    assert events.event_stats(conn, EventFilter(kinds=("nothing",)))["groups"] == []
    only_b = events.event_stats(conn, EventFilter(kinds=("operation",), stream_id=2))
    assert [g["name"] for g in only_b["groups"]] == ["GetB"]
    windowed = events.event_stats(conn, EventFilter(kinds=("operation",), ts_from=T0 + 2 * MINUTE))
    assert windowed["totals"]["n"] == 3


def test_histogram_by_minute_and_hour(conn):
    result = events.event_histogram(conn, EventFilter(kinds=("operation",)), "minute")
    assert result["bucket_ms"] == MINUTE and result["truncated"] is False
    first, second = result["buckets"]
    assert first["t"] == T0 and first["total"] == 21 and first["counts"] == {"ok": 18, "fail": 2, "open": 1}
    assert second["t"] == T0 + 2 * MINUTE and second["counts"] == {"ok": 3}
    (hour,) = events.event_histogram(conn, EventFilter(kinds=("operation",)), "hour")["buckets"]
    assert hour["t"] == T0 and hour["total"] == 24
    nostatus = events.event_histogram(conn, EventFilter(kinds=("exception",)), "minute")["buckets"]
    assert nostatus[0]["counts"] == {"unknown": 1}


def test_histogram_is_capped(conn):
    conn.executemany(
        "INSERT INTO event(stream_id, kind, name, ts_start, sev, status) VALUES (1, 'gap', 'g', ?, 4, 'ok')",
        [(T0 + i * MINUTE,) for i in range(events.MAX_BUCKETS + 10)],
    )
    result = events.event_histogram(conn, EventFilter(kinds=("gap",)), "minute")
    assert len(result["buckets"]) == events.MAX_BUCKETS and result["truncated"] is True


def test_stat_series_and_methods(conn):
    seed_stats(conn)
    samples, truncated = events.stat_series(conn)
    assert (
        truncated is False
        and [s["ts"] for s in samples] == sorted(s["ts"] for s in samples)
        and len(samples) == 5
    )
    assert set(samples[0]) == {
        "stream_id",
        "ts",
        "side",
        "method",
        "calls",
        "req_bytes",
        "resp_bytes",
        "d_calls",
        "d_req_bytes",
        "d_resp_bytes",
    }
    assert len(events.stat_series(conn, stream_id=1, side="client", method="[TOTAL]")[0]) == 2
    assert len(events.stat_series(conn, ts_from=T0 + MINUTE, ts_to=T0 + MINUTE)[0]) == 2
    methods = events.stat_methods(conn)
    assert [(m["stream_id"], m["side"], m["method"], m["calls"]) for m in methods] == [
        (1, "client", "A.B", 4),
        (1, "client", "[TOTAL]", 15),
        (1, "service", "A.B", 9),
        (2, "client", "[TOTAL]", 1),
    ]
    assert methods[0]["family"] == "AWFM"
    assert len(events.stat_methods(conn, stream_id=2)) == 1


def test_stat_series_and_methods_by_collection(conn):
    seed_stats(conn)
    conn.execute("UPDATE stream SET collection_group = 7 WHERE id = 2")  # stream 2 is another collection
    assert {s["stream_id"] for s in events.stat_series(conn, collection=1)[0]} == {1}
    assert {s["stream_id"] for s in events.stat_series(conn, collection=7)[0]} == {2}
    assert events.stat_series(conn, collection=99)[0] == []
    assert (
        len(events.stat_series(conn, collection=1)[0]) + len(events.stat_series(conn, collection=7)[0]) == 5
    )
    assert {m["stream_id"] for m in events.stat_methods(conn, collection=1)} == {1}
    assert [m["stream_id"] for m in events.stat_methods(conn, collection=7)] == [2]
    assert events.stat_methods(conn, stream_id=2, collection=1) == []  # both conditions apply
    assert len(events.stat_methods(conn)) == 4


def test_stat_series_is_capped(conn):
    conn.executemany(
        "INSERT INTO stat_sample(stream_id, ts, side, method, calls) VALUES (1, ?, 'client', 'M', 1)",
        [(i,) for i in range(events.SERIES_LIMIT + 5)],
    )
    samples, truncated = events.stat_series(conn)
    assert len(samples) == events.SERIES_LIMIT and truncated is True
