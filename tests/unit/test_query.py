"""Grid queries (ARQUITETURA 9.4, 10): keyset pages, count with timeout, context, detail."""

import os
import random
import sqlite3
import time

import pytest
from query_helpers import build_case, open_ro, order_key, py_match

from dmslog.store import query as Q
from dmslog.store.db import create_case_db
from dmslog.store.filters import FilterError


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    path = tmp_path_factory.mktemp("case") / "index.sqlite"
    rows = build_case(path)
    conn = open_ro(path)
    yield conn, rows
    conn.close()


def ordered(rows, tree=None):
    return [r["id"] for r in sorted(rows, key=order_key) if py_match(tree, r)]


# --- cursor ---------------------------------------------------------------------------------------


def test_cursor_round_trip_and_errors():
    assert Q.Cursor.parse("1000:5") == Q.Cursor(1000, 5)
    assert Q.Cursor.parse("null:7") == Q.Cursor(None, 7)
    assert Q.Cursor.parse("-5:-1") == Q.Cursor(-5, -1)
    assert Q.Cursor.parse(None) is None and Q.Cursor.parse("") is None
    assert str(Q.Cursor(None, 3)) == "null:3" and str(Q.Cursor(12, 3)) == "12:3"
    for bad in ("12", "a:1", "1:b", "1:2:3", "' OR 1=1 --", "9" * 30 + ":1", "x" * 100):
        with pytest.raises(Q.CursorError):
            Q.Cursor.parse(bad)


# --- page / keyset --------------------------------------------------------------------------------

PAGE_FILTERS = [None, {"source": [1, 2]}, {"sev": {"le": 3}}, {"source": 3}, {"not": {"tid": 16}}]


@pytest.mark.parametrize("tree", PAGE_FILTERS)
@pytest.mark.parametrize("limit", [1, 3, 7, 200])
def test_forward_and_backward_walks_rebuild_the_sequence(case, tree, limit):
    conn, rows = case
    want = ordered(rows, tree)

    got, cursor = [], None
    while True:
        pg = Q.page(conn, tree, cursor, "fwd", limit)
        got += [r["id"] for r in pg.rows]
        if not pg.has_more:
            break
        cursor = pg.last
    assert got == want

    got, cursor = [], None
    while True:
        pg = Q.page(conn, tree, cursor, "back", limit)
        got = [r["id"] for r in pg.rows] + got
        if not pg.has_more:
            break
        cursor = pg.first
    assert got == want


def test_pages_from_any_cursor_in_both_directions(case):
    conn, rows = case
    srt = sorted(rows, key=order_key)
    want = [r["id"] for r in srt]
    for k in range(0, len(srt), 13):
        cur = Q.Cursor(srt[k]["ts"], srt[k]["id"])
        fwd = Q.page(conn, None, str(cur), "fwd", 10)
        assert [r["id"] for r in fwd.rows] == want[k + 1 : k + 11]
        assert fwd.has_more == (k + 11 < len(want))
        back = Q.page(conn, None, cur, "back", 10)
        assert [r["id"] for r in back.rows] == want[max(0, k - 10) : k]
        assert back.has_more == (k - 10 > 0)


def test_jump_to_instant(case):
    conn, rows = case
    t = 1_000_300
    first = min((r for r in rows if r["ts"] is not None and r["ts"] >= t), key=order_key)
    pg = Q.page(conn, None, f"{t}:-1", "fwd", 1)
    assert pg.rows[0]["id"] == first["id"]


def test_page_rows_and_columns(case):
    conn, rows = case
    pg = Q.page(conn, {"source": 1}, None, "fwd", 5, ["fields.op", "tpl", "event_id", "has_body", "fields.n"])
    assert pg.columns[-5:] == ["fields.op", "tpl", "event_id", "has_body", "fields.n"]
    by_id = {r["id"]: r for r in rows}
    for r in pg.rows:
        src = by_id[r["id"]]
        assert r["msg"] == src["msg"] and r["ts"] == src["ts"]
        assert r["fields.op"] in src["msg"]
        assert r["tpl"] == {0: None, 1: "TST-001", 2: "TST-002"}[src["tpl_id"]]
    assert str(pg.first) == f"{pg.rows[0]['ts']}:{pg.rows[0]['id']}"
    empty = Q.page(conn, {"tpl": "NOPE"}, None, "fwd", 5)
    assert empty.rows == [] and empty.first is None and not empty.has_more


@pytest.mark.parametrize(
    "columns",
    [["msg; DROP TABLE entry"], ["fields.op') FROM entry; --"], ["fields."], ["fields.a-b"], [1], ["x"] * 40],
)
def test_page_rejects_unknown_columns(case, columns):
    conn, _ = case
    with pytest.raises(Q.ColumnError):
        Q.page(conn, None, None, "fwd", 5, columns)


@pytest.mark.parametrize(
    ("direction", "limit"), [("up", 5), ("fwd", 0), ("fwd", Q.MAX_LIMIT + 1), ("fwd", True)]
)
def test_page_rejects_bad_arguments(case, direction, limit):
    conn, _ = case
    with pytest.raises(Q.ColumnError):
        Q.page(conn, None, None, direction, limit)


def test_page_rejects_bad_filter(case):
    conn, _ = case
    with pytest.raises(FilterError):
        Q.page(conn, {"sev": "' OR 1=1"}, None, "fwd", 5)


def test_long_msg_is_truncated_in_the_grid_only(tmp_path):
    conn = create_case_db(tmp_path / "index.sqlite")
    long = "x" * (Q.MSG_PREVIEW_CHARS + 100)
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, msg) "
        "VALUES (1, 1, 1, 0, 1, 5, 4, 0, ?)",
        (long,),
    )
    conn.commit()
    conn.close()
    ro = open_ro(tmp_path / "index.sqlite")
    row = Q.page(ro, None, None, "fwd", 1).rows[0]
    assert len(row["msg"]) == Q.MSG_PREVIEW_CHARS and row["msg_len"] == len(long)
    assert Q.get_entry(ro, 1)["msg"] == long
    ro.close()


# --- timeouts and count ---------------------------------------------------------------------------


@pytest.fixture
def instant_timeout(monkeypatch):
    monkeypatch.setattr(Q, "PROGRESS_OPS", 1)


def test_count_exact(case):
    conn, rows = case
    for tree in (None, {"sev": {"le": 2}}, {"text": {"substr": "Frame"}}):
        res = Q.count(conn, tree)
        assert res == Q.CountResult(len(ordered(rows, tree)), True, "exact")


def test_page_timeout_raises(case, instant_timeout):
    conn, _ = case
    with pytest.raises(Q.QueryTimeout):
        Q.page(conn, {"text": {"substr": "zzz"}}, None, "fwd", 10, timeout_s=0)
    # The guard is removed afterwards: the connection still works without a deadline.
    assert Q.page(conn, None, None, "fwd", 1, timeout_s=None).rows


def test_count_timeout_falls_back_to_sample_estimate(case, instant_timeout):
    conn, rows = case
    res = Q.count(conn, {"source": [1, 2]}, timeout_s=0, estimate_timeout_s=None)
    assert res.method == "sample" and not res.exact
    assert 0 <= res.n <= 2 * len(rows)
    none = Q.count(conn, {"source": 1}, timeout_s=0, estimate_timeout_s=0)
    assert none == Q.CountResult(None, False, "none")


def test_count_timeout_uses_bucket_when_filled(tmp_path, instant_timeout):
    path = tmp_path / "index.sqlite"
    rows = build_case(path)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) "
            "SELECT source_id, ts / 60000, sev, tpl_id, count(*) FROM entry WHERE ts IS NOT NULL "
            "GROUP BY 1, 2, 3, 4"
        )
    conn.close()
    ro = open_ro(path)
    tree = {"and": [{"source": [1, 2]}, {"sev": {"le": 3}}]}
    res = Q.count(ro, tree, timeout_s=0, estimate_timeout_s=None)
    assert res == Q.CountResult(len(ordered(rows, tree)), False, "bucket")
    # Not expressible on bucket -> sampling.
    assert Q.count(ro, {"tid": 16}, timeout_s=0, estimate_timeout_s=None).method == "sample"
    ro.close()


def test_count_on_empty_database(tmp_path):
    create_case_db(tmp_path / "index.sqlite").close()
    ro = open_ro(tmp_path / "index.sqlite")
    assert Q.count(ro, None) == Q.CountResult(0, True, "exact")
    assert Q.page(ro, None, None, "back", 10).rows == []
    ro.close()


# --- context --------------------------------------------------------------------------------------


@pytest.mark.parametrize("same", ["source", "tid", "merge"])
def test_context_matches_reference(case, same):
    conn, rows = case
    for anchor in (rows[0], rows[2], rows[30], rows[100], rows[-1]):
        if same == "source" or (same == "merge" and anchor["ts"] is None):
            seq = [r["id"] for r in rows if r["source_id"] == anchor["source_id"]]
        elif same == "tid":
            seq = [
                r["id"] for r in rows if r["source_id"] == anchor["source_id"] and r["tid"] == anchor["tid"]
            ]
        else:
            seq = [r["id"] for r in sorted(rows, key=order_key) if r["ts"] is not None]
        k = seq.index(anchor["id"])
        ctx = Q.context(conn, anchor["id"], 4, 3, same)
        assert [r["id"] for r in ctx.rows] == seq[max(0, k - 4) : k + 4]
        assert ctx.rows[ctx.anchor]["id"] == anchor["id"]


def test_context_edges(case):
    conn, rows = case
    assert Q.context(conn, 10_000_000) is None
    ctx = Q.context(conn, rows[50]["id"], 0, 0, "merge")
    assert [r["id"] for r in ctx.rows] == [rows[50]["id"]] and ctx.anchor == 0
    for kwargs in ({"same": "file"}, {"before": -1}, {"after": Q.MAX_CONTEXT + 1}, {"before": "5"}):
        with pytest.raises(Q.ColumnError):
            Q.context(conn, 1, **{"before": 1, "after": 1, "same": "source", **kwargs})


# --- detail ---------------------------------------------------------------------------------------


def test_get_entry_returns_full_text_and_relations(case):
    conn, rows = case
    with_cont = next(r for r in rows if r["cont"] is not None)
    e = Q.get_entry(conn, with_cont["id"])
    assert e["msg"] == with_cont["msg"] and e["cont"] == with_cont["cont"]
    assert e["fields"]["op"] and e["member"] and e["envelope"] == "adms"
    cut = Q.get_entry(conn, with_cont["id"], max_cont_lines=1)
    assert cut["cont"] == with_cont["cont"].split("\n")[0] and cut["cont_truncated"]
    keyed = Q.get_entry(conn, 30)
    assert keyed["keys"] == [{"key_type": "msgid", "value": "m-1"}]
    assert Q.get_entry(conn, 11)["event"]["status"] == "fail"
    assert Q.get_entry(conn, 1)["cont"] is None and Q.get_entry(conn, 1)["fields"] == {}
    assert Q.get_entry(conn, 10_000_000) is None


def test_read_guard_blocks_writes_and_is_removed(case):
    conn, _ = case
    with pytest.raises(sqlite3.DatabaseError), Q.read_guard(conn, None):
        conn.execute("INSERT INTO meta(key, value) VALUES ('a', 'b')")
    with pytest.raises(sqlite3.DatabaseError), Q.read_guard(conn, None):
        conn.execute("ATTACH DATABASE ':memory:' AS other")
    # Outside the guard the connection is still read-only (mode=ro, query_only).
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta(key, value) VALUES ('a', 'b')")


# --- performance (opt-in: DMSLOG_SLOW=1) ----------------------------------------------------------

WORDS = ["timeout", "connected", "GetIncidents", "SendWorks", "retry", "crew", "device", "switching", "order"]


def _build_big(path, n):
    rnd = random.Random(1)
    conn = create_case_db(path)
    conn.execute("PRAGMA synchronous=OFF")
    ts = 1_790_000_000_000
    batch = []
    with conn:
        for i in range(1, n + 1):
            ts += rnd.choice((0, 0, 1, 3, 10, 40))
            src = 1 + i % 30
            words = " ".join(rnd.choice(WORDS) for _ in range(6))
            rare = f" needle{i // 997 % 50}" if i % 997 == 0 else ""
            batch.append(
                (
                    i,
                    src,
                    i,
                    i * 120,
                    1,
                    ts,
                    4000 + src,
                    rnd.randint(1, 64),
                    rnd.choice((4, 4, 4, 5, 5, 3, 2)),
                    rnd.randint(0, 40),
                    0,
                    f"{words} value={i}{rare}",
                )
            )
            if len(batch) == 50_000:
                conn.executemany(
                    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev,"
                    " tpl_id, flags, msg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev,"
                " tpl_id, flags, msg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
        conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
        conn.execute(
            "INSERT INTO bucket SELECT source_id, ts / 60000, sev, tpl_id, count(*) FROM entry"
            " GROUP BY 1, 2, 3, 4"
        )
    if os.environ.get("DMSLOG_SLOW_ANALYZE") == "1":
        conn.execute("ANALYZE")
    conn.close()


def _ms(fn, repeat=5):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


@pytest.mark.skipif(
    os.environ.get("DMSLOG_SLOW") != "1", reason="set DMSLOG_SLOW=1 to run the 1 M row benchmark"
)
def test_performance_one_million_rows(tmp_path):
    n = int(os.environ.get("DMSLOG_SLOW_ROWS", "1000000"))
    path = tmp_path / "big.sqlite"
    t0 = time.perf_counter()
    _build_big(path, n)
    build_s = time.perf_counter() - t0
    conn = open_ro(path)
    mid = conn.execute("SELECT ts, id FROM entry WHERE id = ?", (n // 2,)).fetchone()
    cur = Q.Cursor(mid[0], mid[1])
    results = {
        "page_fwd_start": _ms(lambda: Q.page(conn, None, None, "fwd", 200)),
        "page_back_end": _ms(lambda: Q.page(conn, None, None, "back", 200)),
        "page_fwd_mid": _ms(lambda: Q.page(conn, None, cur, "fwd", 200)),
        "page_back_mid": _ms(lambda: Q.page(conn, None, cur, "back", 200)),
        "page_source_mid": _ms(lambda: Q.page(conn, {"source": 7}, cur, "fwd", 200)),
        "page_3_sources_mid": _ms(lambda: Q.page(conn, {"source": [3, 7, 11]}, cur, "fwd", 200)),
        "page_sev_le2_mid": _ms(lambda: Q.page(conn, {"sev": {"le": 2}}, cur, "fwd", 200)),
        "page_ts_window": _ms(
            lambda: Q.page(conn, {"ts": {"ge": mid[0], "lt": mid[0] + 600_000}}, None, "fwd", 200)
        ),
        "page_tid_source": _ms(lambda: Q.page(conn, {"and": [{"source": 7}, {"tid": 9}]}, cur, "fwd", 200)),
        "fts_common_page": _ms(lambda: Q.page(conn, {"text": {"fts": "timeout"}}, cur, "fwd", 200)),
        "fts_rare_page": _ms(lambda: Q.page(conn, {"text": {"fts": "needle42"}}, None, "fwd", 200)),
        "fts_prefix_page": _ms(lambda: Q.page(conn, {"text": {"fts": "needle4*"}}, None, "fwd", 200)),
        "fts_rare_and_source": _ms(
            lambda: Q.page(conn, {"and": [{"source": 7}, {"text": {"fts": "needle42"}}]}, None, "fwd", 200)
        ),
        "page_no_match_pid": _ms(lambda: Q.page(conn, {"pid": 1}, None, "fwd", 200), 2),
        "page_no_match_flags": _ms(lambda: Q.page(conn, {"flags": "partial"}, None, "fwd", 200), 2),
        "page_no_match_substr": _ms(lambda: Q.page(conn, {"text": {"substr": "zzz"}}, None, "fwd", 200), 2),
        "page_no_match_fts": _ms(lambda: Q.page(conn, {"text": {"fts": "zzz"}}, None, "fwd", 200)),
        "page_tpl_code_mid": _ms(lambda: Q.page(conn, {"tpl": [5, 6]}, cur, "fwd", 200)),
        "count_all": _ms(lambda: Q.count(conn, None), 3),
        "count_sev": _ms(lambda: Q.count(conn, {"sev": {"le": 2}}), 3),
        "count_fts_common": _ms(lambda: Q.count(conn, {"text": {"fts": "timeout"}}), 3),
        "context_merge": _ms(lambda: Q.context(conn, n // 2, 50, 50, "merge")),
        "context_tid": _ms(lambda: Q.context(conn, n // 2, 50, 50, "tid")),
        "get_entry": _ms(lambda: Q.get_entry(conn, n // 2)),
    }
    counts = {
        "fts_rare_rows": len(Q.page(conn, {"text": {"fts": "needle42"}}, None, "fwd", 200).rows),
        "count_fts_common": Q.count(conn, {"text": {"fts": "timeout"}}),
        "count_substr": Q.count(conn, {"text": {"substr": "value=12345"}}),
    }
    conn.close()
    print(f"\nbuild {n} rows: {build_s:.1f} s")
    for name, ms in results.items():
        print(f"{name:>22}: {ms:8.2f} ms")
    print(counts)
    assert results["page_fwd_mid"] < 50 and results["page_back_mid"] < 50
    assert results["fts_rare_page"] < 100
