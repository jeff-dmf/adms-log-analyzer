"""Property tests for the clock offsets (ROADMAP 9.15): reads on adjusted time equal a brute-force model."""

import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import clock_helpers as H  # noqa: E402

from dmslog.store import clock as C  # noqa: E402
from dmslog.store import query  # noqa: E402
from dmslog.store.db import connect  # noqa: E402
from dmslog.store.filters import INT64_MAX, INT64_MIN  # noqa: E402

DB_SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
T0 = 1_900_000_000_000
EXTREMES = [INT64_MIN, INT64_MIN + 1, INT64_MAX - 1, INT64_MAX]

near = st.integers(T0, T0 + 600_000)
wide = st.one_of(near, st.sampled_from(EXTREMES), st.integers(INT64_MIN, INT64_MAX))
offsets_st = st.fixed_dictionaries(
    {},
    optional={
        g: st.one_of(
            st.integers(-C.MAX_OFFSET_MS, C.MAX_OFFSET_MS),
            st.sampled_from([0, 1, -1, H.MINUTE, -3 * H.MINUTE, C.MAX_OFFSET_MS, -C.MAX_OFFSET_MS]),
        )
        for g in H.GROUPS
    },
)


def rows_st(ts):
    row = st.tuples(st.sampled_from(sorted(H.SOURCES)), st.one_of(st.none(), ts), st.integers(1, 5))
    return st.lists(row, max_size=40)


def ts_leaf(values):
    ops = st.dictionaries(
        st.sampled_from(["eq", "ne", "lt", "le", "gt", "ge"]), values, min_size=1, max_size=3
    )
    return st.builds(lambda a: {"ts": a}, st.one_of(values, st.lists(values, min_size=1, max_size=3), ops))


def trees(values):
    leaves = st.one_of(
        ts_leaf(values),
        st.builds(lambda v: {"sev": v}, st.lists(st.integers(1, 5), min_size=1, max_size=3)),
        st.builds(
            lambda v: {"collection": v}, st.lists(st.sampled_from(sorted(H.GROUPS)), min_size=1, max_size=2)
        ),
    )
    return st.recursive(
        leaves,
        lambda kids: st.one_of(
            st.builds(lambda c: {"and": c}, st.lists(kids, max_size=3)),
            st.builds(lambda c: {"or": c}, st.lists(kids, max_size=3)),
            st.builds(lambda c: {"not": c}, kids),
        ),
        max_leaves=5,
    )


def opened(rows, offsets):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "c.sqlite"
    model = H.build(path, rows, offsets)
    return tmp, connect(path, readonly=True), model


@DB_SETTINGS
@given(rows_st(wide), offsets_st, st.one_of(st.none(), trees(wide)), st.integers(1, 6))
def test_pages_and_counts_equal_the_model(rows, offsets, tree, limit):
    tmp, conn, model = opened(rows, offsets)
    try:
        clock = C.load(conn)
        want = H.expected(model, offsets, tree)
        for direction in ("fwd", "back"):
            got = H.walk(lambda c, d, n: C.page(conn, tree, c, d, n, clock=clock), direction, limit)
            assert got == want
        assert C.count(conn, tree, clock=clock).n == len(want)
        cf = C.compile_clocked(tree, clock)
        sql = f"SELECT e.id FROM entry e WHERE {cf.sql} ORDER BY e.id"
        assert [r[0] for r in conn.execute(sql, cf.params)] == sorted(i for i, _a in want)
    finally:
        conn.close()
        tmp.cleanup()


@DB_SETTINGS
@given(rows_st(near), offsets_st, st.one_of(st.none(), trees(near)),
       st.sampled_from([None, 1000, 7000, 60_000, 120_000]), st.sampled_from(["sev", "source"]))  # fmt: skip
def test_histograms_equal_the_model(rows, offsets, tree, bucket_ms, split):
    tmp, conn, model = opened(rows, offsets)
    try:
        clock = C.load(conn)
        h = C.histogram(conn, tree, bucket_ms, split, clock=clock)
        timed = [a for _i, a in H.expected(model, offsets, tree) if a is not None]
        assert h.exact and h.total == len(timed)
        if not timed:
            assert h.n_buckets == 0
            return
        per_bucket = [0] * h.n_buckets
        for a in timed:
            per_bucket[(a - h.start) // h.bucket_ms] += 1
        assert [sum(s["counts"][i] for s in h.series) for i in range(h.n_buckets)] == per_bucket
        assert per_bucket[0] and per_bucket[-1]  # trimmed to the data, like query.histogram
        if not clock.active:
            assert h == query.histogram(conn, tree, bucket_ms, split)
    finally:
        conn.close()
        tmp.cleanup()


@DB_SETTINGS
@given(rows_st(near), st.one_of(st.none(), trees(near)), st.integers(1, 6), st.sampled_from(["fwd", "back"]))
def test_offsets_zero_is_the_current_behavior(rows, tree, limit, direction):
    tmp, conn, _model = opened(rows, {})
    try:
        clock = C.load(conn)
        mine = H.walk(lambda c, d, n: C.page(conn, tree, c, d, n, clock=clock), direction, limit)
        theirs = H.walk(lambda c, d, n: query.page(conn, tree, c, d, n), direction, limit)
        assert mine == theirs
        assert C.count(conn, tree, clock=clock) == query.count(conn, tree)
    finally:
        conn.close()
        tmp.cleanup()
