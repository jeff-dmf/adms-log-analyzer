"""Clock offset between collections (ROADMAP 9.15, store/clock.py): reads on adjusted time against a model."""

from __future__ import annotations

import json
import random

import clock_helpers as H
import multi_helpers as mh
import pytest

from dmslog.ingest.partial import FirstPass
from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.store import clock as C
from dmslog.store import collections as cs
from dmslog.store import create_case_db, highlights, query
from dmslog.store.db import connect
from dmslog.store.filters import INT64_MAX, INT64_MIN
from dmslog.store.writer import CLOCK_PREFIX, IndexWriter

T0 = 1_900_000_000_000
TREES = [
    None,
    {"ts": {"ge": T0 + 100_000, "lt": T0 + 300_000}},
    {"or": [{"ts": [T0 + 500, T0 + 12_845]}, {"not": {"ts": {"gt": T0 + 200_000}}}]},
    {"and": [{"sev": [1, 2]}, {"collection": [3]}]},
    {"ts": {"ne": T0 + 1000}},
    {"not": {"ts": T0 + 61_000}},
]


def rows_for(seed: int, n: int = 300, spread: int = 400_000, nulls: float = 0.05):
    r = random.Random(seed)
    return [
        (
            r.choice(list(H.SOURCES)),
            None if r.random() < nulls else T0 + r.randint(0, spread) // 7 * 7,  # repeated times on purpose
            r.randint(1, 5),
        )
        for _ in range(n)
    ]


@pytest.fixture
def case(tmp_path):
    """``case(rows, offsets) -> (read connection, model rows, ClockMap)``."""
    opened = []

    def make(rows, offsets):
        path = tmp_path / f"c{len(opened)}.sqlite"
        model = H.build(path, rows, offsets)
        conn = connect(path, readonly=True)
        opened.append(conn)
        return conn, model, C.load(conn)

    yield make
    for conn in opened:
        conn.close()


# --------------------------------------------------------------------------- offsets 0: unchanged


def test_without_offsets_every_read_is_the_query_module(case):
    conn, _model, clock = case(rows_for(1), {})
    assert not clock.active and clock.offsets == {1: 0, 3: 0, 4: 0}
    for tree in TREES:
        for direction in ("fwd", "back"):
            mine = C.page(conn, tree, None, direction, 50, ["collection"], clock=clock)
            theirs = query.page(conn, tree, None, direction, 50, ["collection"])
            assert json.dumps(mine.rows) == json.dumps(theirs.rows)  # byte for byte, key order included
            assert (mine.first, mine.last, mine.has_more, mine.columns) == (
                theirs.first,
                theirs.last,
                theirs.has_more,
                theirs.columns,
            )
        assert C.count(conn, tree, clock=clock) == query.count(conn, tree)
        assert C.histogram(conn, tree, None, "source", clock=clock) == query.histogram(
            conn, tree, None, "source"
        )
    for same in ("source", "tid", "merge"):
        mine, theirs = C.context(conn, 40, 5, 5, same, clock=clock), query.context(conn, 40, 5, 5, same)
        assert json.dumps(mine.rows) == json.dumps(theirs.rows) and mine.anchor == theirs.anchor
    entry = query.get_entry(conn, 40)
    assert C.decorate_entry(conn, dict(entry), clock) == entry


def test_without_offsets_the_identity_path_runs_the_same_statements(case):
    conn, _model, clock = case(rows_for(2), {})
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    query.page(conn, TREES[1], None, "fwd", 20)
    plain, seen[:] = list(seen), []
    C.page(conn, TREES[1], None, "fwd", 20, clock=clock)
    assert seen == plain


def test_a_shifted_group_without_records_changes_nothing_but_adds_ts_adjusted(case):
    rows = [r for r in rows_for(3) if H.group_of_source(r[0]) != 4]
    conn, _model, clock = case(rows, {4: 777})
    assert clock.active and len(clock.classes()) == 2
    for tree in TREES:
        mine = H.walk(lambda c, d, n, t=tree: C.page(conn, t, c, d, n, clock=clock), "fwd", 17)
        plain = H.walk(lambda c, d, n, t=tree: query.page(conn, t, c, d, n), "fwd", 17)
        assert mine == plain


# --------------------------------------------------------------------------- reads against the model


@pytest.mark.parametrize("offsets", [{3: 12_345, 4: -61_000}, {1: 5, 3: 5, 4: 5}, {3: -1}, {4: 120_000}])
def test_pages_counts_and_histograms_follow_the_model(case, offsets):
    conn, model, clock = case(rows_for(4), offsets)
    for tree in TREES:
        want = H.expected(model, offsets, tree)
        for limit in (1, 7, 200):
            for direction in ("fwd", "back"):
                got = H.walk(lambda c, d, n, t=tree: C.page(conn, t, c, d, n, clock=clock), direction, limit)
                assert got == want, (tree, limit, direction)
        assert C.count(conn, tree, clock=clock).n == len(want)
        timed = [a for _i, a in want if a is not None]
        for bucket_ms in (None, 1000, 60_000, 300_000):
            h = C.histogram(conn, tree, bucket_ms, "sev", clock=clock)
            assert h.exact and h.total == len(timed)
            per_bucket = [0] * h.n_buckets
            for a in timed:
                per_bucket[(a - h.start) // h.bucket_ms] += 1
            assert [sum(s["counts"][i] for s in h.series) for i in range(h.n_buckets)] == per_bucket


def test_rows_carry_the_original_time_and_the_adjusted_one(case):
    conn, model, clock = case(rows_for(5), {3: 2500})
    page = C.page(conn, None, None, "fwd", 1000, ["collection"], clock=clock)
    by_id = {r.id: r for r in model}
    for row in page.rows:
        assert row["ts"] == by_id[row["id"]].ts  # never changed
        assert row["ts_adjusted"] == H.adjusted(by_id[row["id"]], {3: 2500})
    assert page.columns[-1] == "ts_adjusted"


def test_jumping_to_an_adjusted_instant(case):
    offsets = {3: 30_000}
    conn, model, clock = case(rows_for(6), offsets)
    t = T0 + 150_000
    page = C.page(conn, None, f"{t}:-1", "fwd", 5, clock=clock)
    want = [x for x in H.expected(model, offsets) if x[1] is not None and x[1] >= t][:5]
    assert [(r["id"], r["ts_adjusted"]) for r in page.rows] == want


def test_buckets_are_used_when_every_class_is_whole_minutes(case):
    offsets = {3: 2 * H.MINUTE, 4: -H.MINUTE}
    conn, model, clock = case(rows_for(7), offsets)
    h = C.histogram(conn, None, None, "source", clock=clock)
    assert (h.method, h.exact) == ("bucket", True)
    timed = [a for _i, a in H.expected(model, offsets) if a is not None]
    assert h.total == len(timed)
    per_bucket = [0] * h.n_buckets
    for a in timed:
        per_bucket[(a - h.start) // h.bucket_ms] += 1
    assert [sum(s["counts"][i] for s in h.series) for i in range(h.n_buckets)] == per_bucket
    # a class off the minute grid is read from entry, the others still from the buckets: same counts
    h2 = C.histogram(conn, None, 60_000, "source", clock=C.ClockMap({**clock.offsets, 3: 2 * H.MINUTE + 1}))
    assert h2.method == "entry" and h2.exact


def test_a_histogram_past_its_budget_is_an_estimate(case):
    conn, model, clock = case(rows_for(8), {3: 1234})
    h = C.histogram(conn, None, 60_000, "sev", clock=clock, timeout_s=0)
    assert not h.exact and h.method == "bucket_estimate"
    assert h.total == sum(1 for r in model if r.ts is not None)  # minutes moved whole: totals are kept
    with pytest.raises(query.QueryTimeout):
        C.histogram(conn, None, 60_000, "sev", clock=clock, timeout_s=0, estimate_timeout_s=0)


def test_a_page_past_its_budget_times_out(case):
    conn, _model, clock = case(rows_for(9), {3: 1})
    with pytest.raises(query.QueryTimeout):
        C.page(conn, None, None, "fwd", 10, clock=clock, timeout_s=0)


def test_merge_context_follows_adjusted_time(case):
    offsets = {3: -40_000}
    conn, model, clock = case(rows_for(10), offsets)
    order = [x for x in H.expected(model, offsets) if x[1] is not None]
    for anchor_pos in (0, 5, len(order) // 2, len(order) - 1):
        anchor = order[anchor_pos][0]
        ctx = C.context(conn, anchor, 4, 3, "merge", clock=clock)
        lo = max(0, anchor_pos - 4)
        assert [(r["id"], r["ts_adjusted"]) for r in ctx.rows] == order[lo : anchor_pos + 4]
        assert ctx.rows[ctx.anchor]["id"] == anchor and ctx.same == "merge"
    untimed = next(r.id for r in model if r.ts is None)
    ctx = C.context(conn, untimed, 2, 2, "merge", clock=clock)
    assert ctx.same == "merge" and all("ts_adjusted" in r for r in ctx.rows)
    assert C.context(conn, 10**9, 1, 1, "merge", clock=clock) is None


def test_next_highlight_walks_the_adjusted_order(case):
    offsets = {3: 90_000, 4: -90_000}
    conn, model, clock = case(rows_for(11), offsets)
    view = {"ts": {"ge": T0 + 50_000}}
    rules = [{"sev": [1]}]
    want = [x for x in H.expected(model, offsets, {"and": [view, {"sev": [1]}]})]
    got, cursor = [], None
    while True:
        found = highlights.next_match(conn, view, rules, cursor, "fwd", clock=clock)
        if found.row is None:
            break
        got.append((found.row["id"], found.row["ts_adjusted"]))
        assert found.rule == 0
        cursor = f"{found.row['ts_adjusted']}:{found.row['id']}"
    assert got == want


# --------------------------------------------------------------------------- the int64 limits


def test_times_and_conditions_at_the_int64_limits(case):
    big = C.MAX_OFFSET_MS
    rows = [
        (1, INT64_MAX, 1),
        (1, INT64_MIN, 1),
        (5, INT64_MAX, 2),
        (5, INT64_MIN, 2),
        (7, INT64_MAX - 3, 3),
        (7, INT64_MIN + 3, 3),
        (3, None, 4),
        (5, 0, 4),
    ]
    offsets = {3: big, 4: -big}
    conn, model, clock = case(rows, offsets)
    trees = [
        None,
        {"ts": {"ge": INT64_MAX - 10}},
        {"ts": {"lt": INT64_MIN + 10}},
        {"ts": {"le": INT64_MAX, "ge": INT64_MIN}},
        {"ts": [INT64_MAX, INT64_MIN, 0]},
        {"ts": {"ne": INT64_MAX}},
        {"not": {"ts": {"eq": INT64_MIN}}},
        {"ts": {"gt": INT64_MAX - big}},
    ]
    for tree in trees:
        want = H.expected(model, offsets, tree)
        for direction in ("fwd", "back"):
            got = H.walk(lambda c, d, n, t=tree: C.page(conn, t, c, d, n, clock=clock), direction, 2)
            assert got == want, (tree, direction)
        assert C.count(conn, tree, clock=clock).n == len(want), tree
    assert (
        max(a for _i, a in H.expected(model, offsets) if a is not None) == INT64_MAX + big
    )  # exact, no wrap


def test_shift_tree_turns_out_of_range_values_into_constants():
    assert C.shift_tree({"ts": {"lt": INT64_MAX}}, 5) == {
        "ts": {"ge": INT64_MIN}
    }  # always true (with a time)
    assert C.shift_tree({"ts": {"ge": INT64_MAX}}, 5) == {"or": []}
    assert C.shift_tree({"ts": [INT64_MIN, 7]}, -5) == {"ts": [2]}
    assert C.shift_tree({"ts": INT64_MIN}, -1) == {"or": []}
    assert C.shift_tree({"not": {"ts": {"gt": 10, "ne": INT64_MIN}}}, -1) == {"not": {"ts": {"gt": 9}}}
    tree = {"and": [{"sev": [1]}, {"ts": 5}]}
    assert C.shift_tree(tree, 0) is tree and C.shift_tree(tree, 3) == {"and": [{"sev": [1]}, {"ts": 8}]}


@pytest.mark.parametrize("text", ["x", "1", "1:2:3", "null", ":5", f"{10**30}:1", f"1:{2**63}"])
def test_bad_cursors_are_refused(text):
    with pytest.raises(query.CursorError):
        C.parse_cursor(text)


def test_cursors_reach_past_int64_by_the_largest_offset():
    assert C.parse_cursor(f"{INT64_MAX + C.MAX_OFFSET_MS}:3") == query.Cursor(INT64_MAX + C.MAX_OFFSET_MS, 3)
    assert C.parse_cursor("null:4") == query.Cursor(None, 4)
    with pytest.raises(query.CursorError):
        C.parse_cursor(f"{INT64_MAX + C.MAX_OFFSET_MS + 1}:3")


def test_invalid_stored_offsets_count_as_none(case):
    conn, _model, _clock = case(rows_for(12, n=20), {})
    rw = connect(conn.execute("PRAGMA database_list").fetchone()[2])
    with rw:
        rw.execute("UPDATE collection SET clock_offset_ms = ? WHERE id = 3", (C.MAX_OFFSET_MS + 1,))
        rw.execute("UPDATE collection SET clock_offset_ms = 'abc' WHERE id = 4")
    rw.close()
    assert not C.load(conn).active


# --------------------------------------------------------------------------- setting an offset


@pytest.fixture
def rw(tmp_path):
    path = tmp_path / "w.sqlite"
    H.build(path, [(1, T0 + 1000, 4), (5, T0 + 4000, 4), (7, T0 + 500, 4), (3, None, 4), (2, T0, 2)], {})
    conn = connect(path)
    yield conn
    conn.close()


def offsets_of(conn):
    return conn.execute("SELECT id, clock_offset_ms FROM collection ORDER BY id").fetchall()


def test_a_pair_sets_the_target_group(rw):
    out = C.set_offset(rw, 3, {"anchor": 1, "target": 2})
    assert out["clock_offset_ms"] == -3000 and out["previous_ms"] == 0 and out["applied"]
    assert out["anchor"] == {"entry_id": 1, "ts": T0 + 1000, "ts_adjusted": T0 + 1000}
    assert out["target"] == {"entry_id": 2, "ts": T0 + 4000, "ts_adjusted": T0 + 1000}
    assert out["skew_ms"] == 3000  # the target's clock runs 3 s ahead
    assert offsets_of(rw) == [(1, 0), (2, 0), (3, -3000), (4, 0)]
    record = json.loads(rw.execute("SELECT value FROM meta WHERE key = 'clock.3'").fetchone()[0])
    assert record["method"] == "pair"
    assert record["anchor"] == {
        "group": 1,
        "collection": 1,
        "member": "Log/s1.log",
        "line_no": 1,
        "ts": T0 + 1000,
    }
    assert record["target"] == {
        "group": 3,
        "collection": 3,
        "member": "Log/s5.log",
        "line_no": 2,
        "ts": T0 + 4000,
    }
    assert C.describe(rw)[3]["clock_source"] == record and C.describe(rw)[1]["clock_source"] is None
    # chained: an anchor in a shifted group counts with its adjusted time
    out = C.set_offset(rw, 4, {"anchor": 2, "target": 3})
    assert out["clock_offset_ms"] == (T0 + 4000 - 3000) - (T0 + 500)


def test_dry_run_writes_nothing(rw):
    out = C.set_offset(rw, 3, {"anchor": 1, "target": 2, "dry_run": True})
    assert out["clock_offset_ms"] == -3000 and not out["applied"]
    assert offsets_of(rw) == [(1, 0), (2, 0), (3, 0), (4, 0)] and not rw.in_transaction
    assert rw.execute("SELECT count(*) FROM meta WHERE key LIKE 'clock.%'").fetchone()[0] == 0


def test_manual_offset_and_reset(rw):
    assert C.set_offset(rw, 1, {"offset_ms": 250})["clock_offset_ms"] == 250
    assert offsets_of(rw)[:2] == [(1, 250), (2, 250)]  # every row of the group
    assert C.describe(rw)[1]["clock_source"]["method"] == "manual"
    out = C.reset_offset(rw, 1)
    assert (out["clock_offset_ms"], out["previous_ms"], out["clock_source"]) == (0, 250, None)
    assert rw.execute("SELECT count(*) FROM meta WHERE key LIKE 'clock.%'").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("group", "body", "code"),
    [
        (3, {}, "bad_request"),
        (3, {"offset_ms": 1, "anchor": 1}, "bad_request"),
        (3, {"offset_ms": True}, "bad_offset"),
        (3, {"offset_ms": 1.5}, "bad_offset"),
        (3, {"offset_ms": C.MAX_OFFSET_MS + 1}, "bad_offset"),
        (3, {"offset_ms": 1, "dry_run": "yes"}, "bad_request"),
        (3, {"anchor": 1, "target": True}, "bad_entry"),
        (3, {"anchor": 0, "target": 2}, "bad_entry"),
        (3, {"anchor": 1}, "bad_entry"),
        (2, {"offset_ms": 1}, "collection_not_found"),  # a member, not the head of its group
        (9, {"offset_ms": 1}, "collection_not_found"),
        (3, {"anchor": 99, "target": 2}, "entry_not_found"),
        (3, {"anchor": 4, "target": 2}, "no_time"),
        (3, {"anchor": 1, "target": 3}, "target_not_in_collection"),
        (1, {"anchor": 5, "target": 1}, "same_collection"),
    ],
)
def test_bad_requests_change_nothing(rw, group, body, code):
    with pytest.raises(C.ClockError) as err:
        C.set_offset(rw, group, body)
    assert err.value.code == code
    assert offsets_of(rw) == [(1, 0), (2, 0), (3, 0), (4, 0)] and not rw.in_transaction


def test_records_too_far_apart_are_refused(tmp_path):
    path = tmp_path / "far.sqlite"
    H.build(path, [(1, T0, 4), (5, T0 + C.MAX_OFFSET_MS + 1, 4), (7, T0 + C.MAX_OFFSET_MS, 4)], {})
    conn = connect(path)
    with pytest.raises(C.ClockError) as err:
        C.set_offset(conn, 3, {"anchor": 1, "target": 2})
    assert err.value.code == "offset_too_large"
    assert C.set_offset(conn, 4, {"anchor": 1, "target": 3})["clock_offset_ms"] == -C.MAX_OFFSET_MS
    conn.close()


def test_the_meta_prefix_is_the_one_the_writer_removes():
    assert C.META_PREFIX == CLOCK_PREFIX


# --------------------------------------------------------------------------- real ingestion


def first_avl(conn, server: str, sensitive: bool = False) -> int:
    """Id of the first AVL record of a server's normal (or sensitive) log, found by its locator."""
    suffix = ".sensitive.log" if sensitive else ".log"
    return conn.execute(
        "SELECT e.id FROM entry e JOIN source s ON s.id = e.source_id"
        " JOIN collection c ON c.id = s.collection_id WHERE c.ers_guid = ? AND s.member LIKE ?"
        " AND e.msg LIKE 'Position is updated%' ORDER BY e.line_no LIMIT 1",
        (mh.SERVERS[server][0], f"%{suffix}"),
    ).fetchone()[0]


def grid(conn):
    """The whole grid on adjusted time, by locator (never by id); ties of ts_adjusted as sets."""
    clock = C.load(conn)
    rows = []
    cursor = None
    while True:
        page = C.page(conn, None, cursor, "fwd", 7, clock=clock)
        for r in page.rows:
            loc = conn.execute(
                "SELECT c.ers_guid, s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
                " JOIN collection c ON c.id = s.collection_id WHERE e.id = ?",
                (r["id"],),
            ).fetchone()
            rows.append((r["ts_adjusted"], (*loc, r["ts"])))
        if not page.has_more:
            break
        cursor = str(page.last)
    out: dict = {}
    for adj, loc in rows:
        out.setdefault(adj, set()).add(loc)
    assert [a for a, _l in rows] == sorted(a for a, _l in rows)
    return out


def test_same_result_whatever_the_indexing_order(tmp_path):
    zips = mh.build_all(tmp_path / "zips")
    orders = {
        "full": [("A", "enterprise"), ("A", "sensitive"), ("B", "enterprise"), ("B", "sensitive")],
        "reversed": [("B", "sensitive"), ("A", "sensitive"), ("B", "enterprise"), ("A", "enterprise")],
        "partial": [("A", "enterprise"), ("B", "enterprise"), ("A", "sensitive"), ("B", "sensitive")],
    }
    results = {}
    for name, order in orders.items():
        conn = create_case_db(tmp_path / name / "case.sqlite")
        for server, kind in order:
            options = None
            if name == "partial":
                first = FirstPass(ts_from=mh_ms(2000), ts_to=mh_ms(4000), defer_rest=True)
                options = IngestOptions(first=first)
            run_ingest(conn, zips[(server, kind)], options)
        if name == "partial":
            for server, kind in order:  # the second pass completes every collection
                run_ingest(conn, zips[(server, kind)])
        b = mh.group_of(conn, "B")
        out = C.set_offset(conn, b, {"anchor": first_avl(conn, "A"), "target": first_avl(conn, "B", True)})
        assert out["clock_offset_ms"] == 0  # same readings on both servers
        C.set_offset(conn, b, {"offset_ms": 1500})
        clock = C.load(conn)
        results[name] = (
            grid(conn),
            C.count(conn, {"ts": {"ge": mh_ms(2600)}}, clock=clock).n,
            C.histogram(conn, None, 1000, "sev", clock=clock),
            sorted(
                (g["guid"], g["clock_offset_ms"]) for g in cs.list_collections(conn)
            ),  # C<n> follows arrival
        )
        conn.close()
    assert results["full"] == results["reversed"] == results["partial"]


def mh_ms(offset: int) -> int:
    from dmslog.parse.envelope import parse_ts

    text = mh.mf.fmt_ts(offset)
    return parse_ts(text[:19].encode(), text[20:23].encode())


def test_the_other_half_inherits_and_removal_forgets(tmp_path):
    zips = mh.build_all(tmp_path / "zips")
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, zips[("A", "enterprise")])
    run_ingest(conn, zips[("B", "enterprise")])
    b = mh.group_of(conn, "B")
    C.set_offset(conn, b, {"offset_ms": -700})
    run_ingest(conn, zips[("B", "sensitive")])
    assert {
        r[0] for r in conn.execute("SELECT clock_offset_ms FROM collection WHERE ers_group = ?", (b,))
    } == {-700}
    groups = cs.list_collections(conn)
    assert [(g["clock_offset_ms"], (g["clock_source"] or {}).get("method")) for g in groups] == [
        (0, None),
        (-700, "manual"),
    ]
    assert cs.clock_state(groups) == "adjusted"
    IndexWriter(conn).remove_group(b)
    assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'clock.%'").fetchone()[0] == 0
    assert cs.clock_state(cs.list_collections(conn)) == "raw" and not C.load(conn).active
    conn.close()


@pytest.mark.parametrize("edge", ["low", "high"])
def test_histogram_windows_at_the_int64_limits(case, edge):
    base = INT64_MIN if edge == "low" else INT64_MAX - 20_000
    r = random.Random(13)
    rows = [(r.choice(list(H.SOURCES)), base + r.randint(0, 20_000), r.randint(1, 5)) for _ in range(120)]
    offsets = {3: 5_003, 4: -7_001}
    conn, model, clock = case(rows, offsets)
    for bucket_ms in (1000, 3000):
        h = C.histogram(conn, None, bucket_ms, "sev", clock=clock)
        timed = [a for _i, a in H.expected(model, offsets) if a is not None]
        assert h.exact and h.total == len(timed)
        per_bucket = [0] * h.n_buckets
        for a in timed:
            per_bucket[(a - h.start) // h.bucket_ms] += 1
        assert [sum(s["counts"][i] for s in h.series) for i in range(h.n_buckets)] == per_bucket


# ------------------------------------------------------------------------- one clock state per read


def _writer_of(conn):
    return connect(conn.execute("PRAGMA database_list").fetchone()[2])


def _assert_context_sound(ctx, offsets_by_id):
    ids_ = [r["id"] for r in ctx.rows]
    assert len(ids_) == len(set(ids_))  # never the anchor (or anything) twice
    keys = [(r["ts_adjusted"], r["id"]) for r in ctx.rows]
    assert keys == sorted(keys)
    for r in ctx.rows:
        assert r["ts_adjusted"] == r["ts"] + offsets_by_id[r["id"]]


@pytest.mark.parametrize("new_offset", [-150, 150, 0])
def test_an_offset_set_during_a_read_is_not_seen_by_it(case, new_offset):
    rows = [(1, 1000, 4), (5, 1000, 4), (1, 1100, 4), (5, 900, 4), (7, 1050, 4)]
    conn, model, _clock = case(rows, {3: 100})
    rw = _writer_of(conn)
    try:
        with C.snapshot(conn) as clock:
            assert clock.offset_of(3) == 100
            C.set_offset(rw, 3, {"offset_ms": new_offset})  # a concurrent PUT, committed
            offsets = {r.id: clock.offset_of(H.group_of_source(r.source)) for r in model}
            for anchor in (1, 2, 4):
                _assert_context_sound(C.context(conn, anchor, 5, 5, "merge", clock=clock), offsets)
            got = H.walk(lambda c, d, n: C.page(conn, None, c, d, n, clock=clock), "fwd", 2)
            assert got == H.expected(model, {3: 100})
            assert C.decorate_entry(conn, dict(query.get_entry(conn, 2)), clock)["ts_adjusted"] == 1100
        assert C.load(conn).offset_of(3) == new_offset  # the next read sees it
    finally:
        rw.close()


def test_a_stale_map_is_used_for_the_anchor_too(case):
    """Without a snapshot the reads may see a newer database, but every offset comes from the one map."""
    rows = [(1, 1000, 4), (5, 1000, 4), (1, 1100, 4), (5, 900, 4)]
    conn, model, clock = case(rows, {3: 100})
    rw = _writer_of(conn)
    try:
        C.set_offset(rw, 3, {"offset_ms": -100})
        offsets = {r.id: clock.offset_of(H.group_of_source(r.source)) for r in model}
        for anchor in (1, 2, 3, 4):
            _assert_context_sound(C.context(conn, anchor, 5, 5, "merge", clock=clock), offsets)
    finally:
        rw.close()


def test_a_collection_added_after_the_map_never_takes_an_offset(case):
    rows = [(1, 1000, 4), (5, 1001, 4), (7, 1002, 4)]
    conn, _model, _clock = case(rows, {1: 50, 3: 50, 4: 50})  # one class only
    rw = _writer_of(conn)
    try:
        with C.snapshot(conn) as clock:
            assert [g for _o, g in clock.classes()] == [(1, 3, 4)]
            with rw:  # a new collection group (id 9) with one record, committed during the read
                rw.execute(
                    "INSERT INTO collection(id, kind, path, ers_group, added_at)"
                    " VALUES (9, 'file', '/n', 9, '2030')"
                )
                rw.execute(
                    "INSERT INTO source(id, collection_id, member, file_name, envelope, status)"
                    " VALUES (9, 9, 'n.log', 'n.log', 'adms', 'ready')"
                )
                rw.execute(
                    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, flags, msg)"
                    " VALUES (9, 9, 1, 0, 1, 1000, 4, 0, 0, 'new')"
                )
            page = C.page(conn, None, None, "fwd", 50, clock=clock)
            assert [r["id"] for r in page.rows] == [1, 2, 3]  # the snapshot does not see it
        stale = clock
        page = C.page(conn, None, None, "fwd", 50, clock=stale)  # the database has it, the map does not
        assert 9 not in [r["id"] for r in page.rows]  # left out rather than shifted by the other offset
        fresh = C.load(conn)
        rows_ = {r["id"]: r["ts_adjusted"] for r in C.page(conn, None, None, "fwd", 50, clock=fresh).rows}
        assert rows_[9] == 1000 and rows_[1] == 1050
    finally:
        rw.close()


def test_the_snapshot_ends_its_transaction_and_keeps_a_callers_one(case):
    conn, _model, _clock = case(rows_for(14, n=10), {3: 5})
    with C.snapshot(conn):
        assert conn.in_transaction
    assert not conn.in_transaction
    conn.execute("BEGIN")
    with C.snapshot(conn):
        pass
    assert conn.in_transaction
    conn.rollback()


@pytest.mark.parametrize("same", ["merge", "source", "tid"])
@pytest.mark.parametrize(("before", "after"), [(501, 0), (0, 501), (-1, 0), (True, 1)])
def test_context_limits_hold_with_offsets(case, same, before, after):
    conn, _model, clock = case(rows_for(15, n=40, nulls=0), {3: 5})
    assert clock.active
    with pytest.raises(query.ColumnError):
        C.context(conn, 1, before, after, same, clock=clock)
    with pytest.raises(query.ColumnError):
        C.context(conn, 1, 1, 1, "nope", clock=clock)


def test_context_keeps_records_adjusted_below_int64(case):
    rows = [(5, INT64_MIN, 4), (5, INT64_MIN + 1, 4), (1, INT64_MIN, 4), (1, 0, 4), (3, None, 4)]
    offsets = {3: -1}
    conn, model, clock = case(rows, offsets)
    order = [x for x in H.expected(model, offsets) if x[1] is not None]
    assert order[0] == (1, INT64_MIN - 1)  # in the grid
    for anchor_pos, (anchor, _adj) in enumerate(order):
        ctx = C.context(conn, anchor, 5, 5, "merge", clock=clock)
        assert [(r["id"], r["ts_adjusted"]) for r in ctx.rows] == order  # every timed record, in order
        assert ctx.anchor == anchor_pos


def _many_groups(path, offsets_by_group):
    """One group (one collection, one source, two records) per key of ``offsets_by_group``."""
    conn = create_case_db(path)
    with conn:
        for g, off in offsets_by_group.items():
            conn.execute(
                "INSERT INTO collection(id, kind, path, ers_group, clock_offset_ms, added_at)"
                " VALUES (?, 'file', ?, ?, ?, '2030')",
                (g, f"/f{g}", g, off),
            )
            conn.execute(
                "INSERT INTO source(id, collection_id, member, file_name, envelope, status)"
                " VALUES (?, ?, 'x.log', 'x.log', 'adms', 'ready')",
                (g, g),
            )
            for k in (0, 1):
                conn.execute(
                    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, flags, msg)"
                    " VALUES (?, ?, ?, 0, 1, ?, 4, 0, 0, 'r')",
                    (2 * g + k, g, k + 1, T0 + (g * 37 + k * 1000) % 50_000),
                )
    conn.close()


@pytest.mark.parametrize(
    "offsets",
    [
        {g: 5 for g in range(1, 1002)},  # 1.001 groups, one class
        {g: (5 if g % 2 else -7) for g in range(1, 1202)},  # two classes of 600+ groups (two chunks each)
        {**{g: 0 for g in range(1, 1102)}, 1102: 3},  # a zero class of 1.101 groups
    ],
)
def test_classes_of_any_size(tmp_path, offsets):
    path = tmp_path / "many.sqlite"
    _many_groups(path, offsets)
    conn = connect(path, readonly=True)
    try:
        with C.snapshot(conn) as clock:
            assert max(len(g) for _o, g in clock.classes()) > C.GROUP_CHUNK
            rows = conn.execute("SELECT id, source_id, ts FROM entry").fetchall()
            want = sorted((ts + offsets[src], i) for i, src, ts in rows)
            got = H.walk(lambda c, d, n: C.page(conn, None, c, d, n, clock=clock), "fwd", 500)
            assert [(a, i) for i, a in got] == want
            window = {"ts": {"ge": T0 + 10_000, "lt": T0 + 30_000}}
            inside = [w for w in want if T0 + 10_000 <= w[0] < T0 + 30_000]
            assert C.count(conn, window, clock=clock).n == len(inside)
            h = C.histogram(conn, window, 1000, "sev", clock=clock)
            assert h.exact and h.total == len(inside)
            assert C.histogram(conn, None, 60_000, "sev", clock=clock).total == len(want)
    finally:
        conn.close()
