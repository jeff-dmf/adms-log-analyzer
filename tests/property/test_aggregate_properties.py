"""Property tests for the declarative aggregation (store/aggregate.py, ROADMAP 9.6): every result equals
a pure Python reference over synthetic data, percentiles included."""

import atexit
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_helpers import build_case, open_ro  # noqa: E402
from test_aggregate import _fill, as_map, empty_row, ref_entries, ref_events, same  # noqa: E402
from test_filters_properties import _compile, leaf, trees  # noqa: E402

from dmslog.store import aggregate as A  # noqa: E402
from dmslog.store import query as Q  # noqa: E402

SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
ENTRY_DIMS = [
    "tpl",
    "source",
    "sev",
    "level",
    "tid",
    "family",
    "collection",
    "fields.op",
    "key.msgid",
    "bucket:1m",
]
ENTRY_MEASURES = [
    "count", "distinct:tid", "distinct:fields.op", "min:fields.n", "max:fields.n", "avg:fields.n",
    "sum:fields.n", "p50:fields.n", "p90:fields.n", "p99:fields.n",
]  # fmt: skip
EVENT_DIMS = ["name", "status", "kind", "family", "bucket:1m", "sev"]
EVENT_MEASURES = [
    "count", "distinct:name", "min:duration", "max:duration", "avg:duration", "sum:entries", "p50:duration",
    "p90:duration", "p95:duration", "p99:duration",
]  # fmt: skip


@lru_cache(maxsize=1)
def _case():
    folder = Path(tempfile.mkdtemp(prefix="dmslog-aggregate-"))
    atexit.register(shutil.rmtree, folder, True)  # best effort (Windows keeps open files)
    rows = build_case(folder / "full.sqlite")
    events = _fill(folder / "full.sqlite", rows)
    return open_ro(folder / "full.sqlite"), rows, events


def _uses(tree, word):
    return f'"{word}"' in json.dumps(tree)


@SETTINGS
@given(
    trees(leaf),
    st.lists(st.sampled_from(ENTRY_DIMS), max_size=3, unique=True),
    st.lists(st.sampled_from(ENTRY_MEASURES), min_size=1, max_size=4, unique=True),
)
def test_entries_match_the_reference(tree, dims, measures):
    if _compile(tree) is None or _uses(tree, "event_status") or _uses(tree, "event"):
        return  # the reference knows the events of query_helpers only
    if sum(d.startswith("bucket:") for d in dims) > 1:
        return
    conn, rows, _ = _case()
    result = A.aggregate(conn, {"filter": tree, "group_by": dims, "measures": measures, "limit": 1000})
    assert result.exact
    want = ref_entries(rows, tree, dims, measures, collection=1)
    if not dims and not want:
        want = {(): empty_row(measures)}
    same(as_map(result), want)
    assert result.groups == len(want) and not result.truncated


@SETTINGS
@given(
    trees(leaf),
    st.lists(
        st.sampled_from(["tpl", "sev", "fields.op", "key.msgid", "bucket:1m"]),
        min_size=1,
        max_size=2,
        unique=True,
    ),
)
def test_drill_down_selects_each_group(tree, dims):
    if _compile(tree) is None or sum(d.startswith("bucket:") for d in dims) > 1:
        return
    conn, _, _ = _case()
    result = A.aggregate(conn, {"filter": tree, "group_by": dims, "limit": 1000})
    for row in result.rows:
        if row["drill"] is not None:
            assert Q.count(conn, {"and": [tree, row["drill"]]}).n == row["values"][len(dims)]


@SETTINGS
@given(
    st.sampled_from([None, {"sev": {"le": 2}}, {"tpl": [1, 2]}, {"source": [1]}, {"tid": 16}]),
    st.lists(st.sampled_from(EVENT_DIMS), max_size=2, unique=True),
    st.lists(st.sampled_from(EVENT_MEASURES), min_size=1, max_size=4, unique=True),
    st.sampled_from([{}, {"kind": ["operation"]}, {"status": ["ok", "fail"]}]),
    st.booleans(),
)
def test_events_match_the_reference(tree, dims, measures, conds, per_event):
    if sum(d.startswith("bucket:") for d in dims) > 1:
        return
    conn, rows, events = _case()
    req = {"rows": "events", "filter": tree, "group_by": dims, "measures": measures, "limit": 1000}
    if conds:
        req["events"] = conds
    limit = A.EVENTS_ROWWISE_MAX
    try:
        A.EVENTS_ROWWISE_MAX = limit if per_event else 0  # per event or as the view's set: same answer
        result = A.aggregate(conn, req)
    finally:
        A.EVENTS_ROWWISE_MAX = limit
    want = ref_events(rows, events, tree, conds, dims, measures)
    if not dims and not want:
        want = {(): empty_row(measures)}
    same(as_map(result), want)


durations = st.one_of(st.none(), st.integers(0, 10_000))
event_rows = st.lists(
    st.tuples(
        st.sampled_from(["A", "B", "C"]),
        st.sampled_from(["ok", "fail", None]),
        durations,
        st.integers(0, 600_000),
    ),
    max_size=80,
)


@SETTINGS
@given(event_rows, st.lists(st.sampled_from(["name", "status", "bucket:1m"]), max_size=2, unique=True))
def test_percentiles_equal_nearest_rank(data, dims):
    """Any sizes and NULL shares: pXX is the value at rank ceil(n * XX / 100) of the non-NULL values."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE event(id INTEGER PRIMARY KEY, stream_id INT, kind TEXT, name TEXT, key TEXT, tid INT,"
        " ts_start INT, duration_ms INT, status TEXT, sev INT, n_entries INT);"
        "CREATE TABLE stream(id INTEGER PRIMARY KEY, family TEXT, collection_group INT);"
        "CREATE TABLE entry(id INTEGER PRIMARY KEY, event_id INT);"
    )
    conn.executemany(
        "INSERT INTO event(stream_id, kind, name, status, duration_ms, ts_start, sev)"
        " VALUES (1,'operation',?,?,?,?,4)",
        data,
    )
    measures = [
        "count",
        "p50:duration",
        "p90:duration",
        "p95:duration",
        "p99:duration",
        "min:duration",
        "max:duration",
    ]
    result = A.aggregate(conn, {"rows": "events", "group_by": dims, "measures": measures, "limit": 1000})
    groups = defaultdict(list)
    for name, status, duration, ts in data:
        key = tuple({"name": name, "status": status, "bucket:1m": ts // 60_000 * 60_000}[d] for d in dims)
        groups[key].append(duration)
    want = {}
    for key, values in groups.items():
        known = sorted(v for v in values if v is not None)
        ranks = [known[math.ceil(len(known) * p / 100) - 1] if known else None for p in (50, 90, 95, 99)]
        want[key] = (len(values), *ranks, min(known, default=None), max(known, default=None))
    if not dims and not want:
        want = {(): (0, None, None, None, None, None, None)}
    assert as_map(result) == want
