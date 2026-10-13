"""Distinct values of a field inside a filter (store/distinct.py, ROADMAP 9.2). Synthetic data only."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

import pytest
from query_helpers import KEYS, build_case, open_ro, py_match

from dmslog.store import distinct as D
from dmslog.store import keys as K
from dmslog.store import query as Q
from dmslog.store.filters import FilterError, compile_filter

COLUMN = {"tpl": "tpl_id", "source": "source_id", "sev": "sev", "level": "level", "tid": "tid", "pid": "pid"}
FILTERS = [
    None,
    {"sev": {"le": 3}},
    {"source": [1, 3]},
    {"and": [{"source": [1, 2]}, {"not": {"tpl": [0]}}]},
    {"or": [{"tid": 16}, {"level": {"ge": 6}}]},
    {"ts": {"ge": 1_000_100, "lt": 1_000_400}},
    {"tpl": "TST-001"},
]
FIELDS = [*COLUMN, "fields.op", "fields.n", "key.msgid", "key.gid"]
# Payload keys (entry_key.origin = 1) next to the template keys of query_helpers: entry 30 also
# carries m-1 from its payload (same pair stays origin 0, as the writer does), 50..52 only payload keys.
PAYLOAD_KEYS = [("msgid", "m-2", 50), ("msgid", "m-2", 51), ("mrid", "r-7", 52), ("mrid", "r-7", 30)]


def _fill_bucket(path):
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) SELECT source_id, ts / 60000, sev, tpl_id,"
            " count(*) FROM entry WHERE ts IS NOT NULL GROUP BY 1, 2, 3, 4"
        )
        conn.execute(
            "INSERT INTO bucket_sev(source_id, minute, sev, n) SELECT source_id, minute, sev, sum(n)"
            " FROM bucket GROUP BY 1, 2, 3"
        )
        conn.executemany(
            "INSERT INTO entry_key(key_type, value, entry_id, origin) VALUES (?,?,?,1)", PAYLOAD_KEYS
        )
        conn.execute("UPDATE template SET title = 'Test message two' WHERE id = 2")
    conn.close()


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    """(plain case without bucket rows, same case with bucket and payload keys)."""
    base = tmp_path_factory.mktemp("distinct")
    rows = build_case(base / "plain.sqlite")
    build_case(base / "full.sqlite")
    _fill_bucket(base / "full.sqlite")
    plain, full = open_ro(base / "plain.sqlite"), open_ro(base / "full.sqlite")
    yield plain, full, rows
    plain.close()
    full.close()


def keys_of(row, payload):
    found = set(KEYS.get(row["id"], set()))
    if payload:
        found |= {(t, v) for t, v, e in PAYLOAD_KEYS if e == row["id"]}
    return found


def reference(rows, tree, field, payload=False):
    """Counter of value -> entries (keys: (entry, value) pairs) and value -> [min ts, max ts]."""
    counts: Counter = Counter()
    span: dict = {}
    for row in rows:
        if not py_match(tree, row):
            continue
        if field.startswith("key."):
            values = [v for t, v in keys_of(row, payload) if t == field[4:]]
        elif field.startswith("fields."):
            values = [json.loads(row["fields"]).get(field[7:]) if row["fields"] else None]
        else:
            values = [row[COLUMN[field]]]
        for v in values:
            counts[v] += 1
            if row["ts"] is not None:
                lo, hi = span.get(v, (row["ts"], row["ts"]))
                span[v] = (min(lo, row["ts"]), max(hi, row["ts"]))
    return counts, span


def check(result, counts, span, top, with_span):
    total = sum(counts.values())
    assert result.total == total and result.groups == len(counts)
    got = [(v["value"], v["count"]) for v in result.values]
    want = sorted(
        counts.items(), key=lambda kv: (-kv[1], kv[0] is not None, kv[0] if kv[0] is not None else 0)
    )
    assert [c for _, c in got] == [c for _, c in want[:top]]
    assert all(counts[v] == c for v, c in got)
    assert result.others == total - sum(c for _, c in got)
    assert result.other_groups == len(counts) - len(got)
    assert sum(c for _, c in got) + result.others == result.total
    for v in result.values:
        assert v["pct"] == round(100 * v["count"] / total, 2)
        if with_span:
            assert (v["first_ts"], v["last_ts"]) == span.get(v["value"], (None, None))


@pytest.mark.parametrize("tree", FILTERS)
@pytest.mark.parametrize("field", FIELDS)
def test_exact_counts_match_the_reference(cases, tree, field):
    plain, full, rows = cases
    for conn, payload in ((plain, False), (full, True)):
        counts, span = reference(rows, tree, field, payload)
        for top in (1, 3, 20):
            result = D.distinct(conn, tree, field, top, span=True)
            assert result.exact
            check(result, counts, span, top, True)


def test_bucket_path_is_exact_and_includes_entries_without_time(cases):
    plain, full, rows = cases
    for field in ("tpl", "source", "sev"):
        for tree in (
            None,
            {"sev": {"le": 3}},
            {"and": [{"source": [2, 3]}, {"tpl": [0, 1]}]},
            {"family": "UNKNOWN"},
        ):
            result = D.distinct(full, tree, field, 20, span=True)
            assert result.method == "bucket", (field, tree)
            counts, span = reference(rows, tree, field)
            check(result, counts, span, 20, True)
            assert D.distinct(plain, tree, field, 20).method == "exact"
    # The plain source (no ts) is counted: it is not in the bucket table.
    by_source = {v["value"]: v["count"] for v in D.distinct(full, None, "source").values}
    assert by_source[3] == 5


def test_ts_conditions_outside_a_window_and_non_bucket_filters_use_the_entry_path(cases):
    _, full, _ = cases
    for tree in (
        {"ts": [1_000_100, 1_000_200]},
        {"ts": {"ne": 1_000_100}},
        {"or": [{"ts": {"ge": 1_000_100}}, {"sev": 1}]},
        {"not": {"ts": {"ge": 1_000_100}}},
        {"ts": {"ge": 1_000_100, "lt": 1_000_200}},  # no whole minute inside
        {"tid": 16},
    ):
        assert D.distinct(full, tree, "tpl").method == "exact", tree
    assert D.distinct(full, None, "level").method == "exact"


# Same rows with the time stretched (x 2000) so that windows hold whole minutes and partial edges.
def _stretch(ts):
    return None if ts is None else (ts - 999_000) * 2000


@pytest.fixture(scope="module")
def wide(tmp_path_factory):
    path = tmp_path_factory.mktemp("distinct-wide") / "wide.sqlite"
    rows = build_case(path)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("UPDATE entry SET ts = (ts - 999000) * 2000 WHERE ts IS NOT NULL")
    conn.close()
    _fill_bucket(path)
    stretched = [{**r, "ts": _stretch(r["ts"])} for r in rows]
    ro = open_ro(path)
    yield ro, stretched
    ro.close()


WINDOWS = [
    {"ts": {"ge": 2_030_123}},
    {"ts": {"lt": 2_345_678}},
    {"ts": {"gt": 2_030_123, "le": 3_456_789}},
    {"ts": {"ge": 2_040_000, "lt": 3_000_000}},  # minute-aligned on both ends
    {"and": [{"source": [1]}, {"ts": {"ge": 2_100_001}}, {"ts": {"lt": 4_000_000}}, {"sev": {"le": 4}}]},
    {"and": [{"ts": {"ge": 2_100_001, "lt": 3_900_000}}, {"and": [{"not": {"tpl": [0]}}]}]},
]


@pytest.mark.parametrize("tree", WINDOWS)
@pytest.mark.parametrize("field", ["tpl", "source", "sev"])
def test_time_window_is_exact_from_bucket_plus_partial_minutes(wide, tree, field):
    conn, rows = wide
    counts, span = reference(rows, tree, field)
    assert sum(counts.values()) > 0
    for top in (1, 20):
        result = D.distinct(conn, tree, field, top, span=True)
        assert (result.method, result.exact) == ("bucket", True), tree
        check(result, counts, span, top, True)
    assert D.distinct(conn, tree, "level").method == "exact"  # level is not in bucket


def test_time_window_reads_only_the_partial_minutes_of_entry(wide):
    conn, _ = wide
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        D.distinct(conn, {"ts": {"ge": 2_030_123, "lt": 3_456_789}}, "tpl")
    finally:
        conn.set_trace_callback(None)
    (sql,) = [s for s in seen if "UNION ALL" in s]
    assert sql.count("FROM entry e") == 2
    assert "e.ts >= 2030123 AND e.ts < 2040000" in sql and "e.ts >= 3420000 AND e.ts < 3456789" in sql
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
    assert "ix_entry_ts" in plan


def test_rollup_table_is_used_for_sev_and_source_only(cases):
    _, full, _ = cases
    seen: list[str] = []
    full.set_trace_callback(seen.append)
    try:
        D.distinct(full, {"sev": {"le": 4}}, "source")
        assert any("bucket_sev" in s for s in seen)
        seen.clear()
        D.distinct(full, {"tpl": [1]}, "sev")
        assert not any("bucket_sev" in s for s in seen)
        seen.clear()
        D.distinct(full, None, "tpl")
        assert not any("bucket_sev" in s for s in seen)
    finally:
        full.set_trace_callback(None)


def test_labels_for_templates_and_sources(cases):
    plain, full, _ = cases
    tpl = {v["value"]: (v["label"], v["code"]) for v in D.distinct(plain, None, "tpl").values}
    assert tpl == {0: (None, None), 1: ("TST-001", "TST-001"), 2: ("TST-002", "TST-002")}
    # The readable title is the label when the template has one; the code stays apart.
    titled = {v["value"]: (v["label"], v["code"]) for v in D.distinct(full, None, "tpl").values}
    assert titled[2] == ("Test message two", "TST-002") and titled[1] == ("TST-001", "TST-001")
    src = {v["value"]: v["label"] for v in D.distinct(plain, None, "source").values}
    assert src == {1: "a.log", 2: "a.log", 3: "x.txt"}
    assert "code" not in D.distinct(plain, None, "source").values[0]
    assert "label" not in D.distinct(plain, None, "sev").values[0]


@pytest.mark.parametrize("field", FIELDS)
def test_drill_down_leaf_selects_exactly_the_group(cases, field):
    _, full, _ = cases
    base = {"sev": {"le": 4}}
    result = D.distinct(full, base, field, 50)
    entries = Q.count(full, base).n
    for v in result.values:
        leaf = v["leaf"]
        if leaf is None:
            assert v["value"] is None and field in ("tid", "pid")
            continue
        assert Q.count(full, {"and": [base, leaf]}).n == v["count"], (field, v)
        if not field.startswith("key."):
            assert Q.count(full, {"and": [base, {"not": leaf}]}).n == entries - v["count"]


def test_null_level_drill_down(tmp_path):
    rows = build_case(tmp_path / "c.sqlite")
    conn = open_ro(tmp_path / "c.sqlite")
    nulls = sum(1 for r in rows if r["level"] is None)
    (item,) = [v for v in D.distinct(conn, None, "level", 50).values if v["value"] is None]
    assert item["count"] == nulls and Q.count(conn, item["leaf"]).n == nulls
    conn.close()


def test_payload_keys_are_counted_like_the_key_filter_and_the_key_search(cases):
    """Regression: entry_key rows with origin = 1 (payload) are part of the distinct values of a key
    type, exactly as the ``key`` filter leaf and /api/keys see them."""
    plain, full, _ = cases
    msgid = {v["value"]: v["count"] for v in D.distinct(full, None, "key.msgid").values}
    assert msgid == {"m-1": 2, "m-2": 2}
    assert {v["value"] for v in D.distinct(plain, None, "key.msgid").values} == {"m-1"}
    mrid = D.distinct(full, None, "key.mrid")
    assert [(v["value"], v["count"]) for v in mrid.values] == [("r-7", 2)]
    for field in ("key.msgid", "key.mrid"):
        for v in D.distinct(full, None, field).values:
            assert Q.count(full, v["leaf"]).n == v["count"]
            records, _ = K.records_with_key(full, field[4:], v["value"])
            assert len(records) == v["count"]


def test_key_paths_agree(cases):
    plain, full, rows = cases
    spec = D.parse_field("key.msgid")
    cf = compile_filter({"source": [1, 2]})
    for entry_first in (False, True):
        sql, params = D._entry_group_sql(spec, cf, False, entry_first=entry_first)
        got = dict(full.execute(sql, params).fetchall())
        assert got == {"m-1": 2, "m-2": 2}
    # The bucket bound (a superset: whole minutes, plus the entries without ts when ts is free).
    window = {"ts": {"ge": 1_000_100, "lt": 1_000_200}}
    in_window = sum(1 for r in rows if py_match(window, r))
    assert in_window <= D._bucket_bound(full, window, 1.0) <= len(rows)
    assert D._bucket_bound(full, None, 1.0) == len(rows)
    assert D._bucket_bound(full, {"tid": 16}, 1.0) is None  # bucket cannot express it
    assert D._bucket_bound(plain, None, 1.0) is None  # bucket empty


@pytest.mark.parametrize("field", ["level", "tid", "fields.op", "key.msgid"])
def test_large_filter_goes_directly_to_the_sample(cases, monkeypatch, field):
    """Above DISTINCT_DIRECT_SAMPLE_ROWS (bucket bound) the exact grouping is not even tried."""
    plain, full, rows = cases
    monkeypatch.setattr(D, "DISTINCT_DIRECT_SAMPLE_ROWS", len(rows) - 1)
    seen: list[str] = []
    full.set_trace_callback(seen.append)
    try:
        result = D.distinct(full, None, field, 5, span=True)
    finally:
        full.set_trace_callback(None)
    assert (result.method, result.exact) == ("sample", False)
    assert result.total is not None and sum(v["count"] for v in result.values) + result.others == result.total
    assert not any("count(*) AS n" in s for s in seen)  # no exact GROUP BY ran
    # At or below the limit, or without a bucket bound (empty bucket, tid filter), it stays exact.
    assert D.distinct(full, {"sev": {"le": 2}}, field).method == "exact"
    assert D.distinct(full, {"tid": 16}, field).method == "exact"
    assert D.distinct(plain, None, field).method == "exact"
    # Columns in bucket keep their exact bucket path whatever the size.
    assert D.distinct(full, None, "tpl").method == "bucket"


@pytest.mark.parametrize("field", ["level", "fields.op", "key.msgid"])
def test_exact_on_demand_skips_the_direct_sample(cases, monkeypatch, field):
    """``exact=True`` ("Count exactly") tries the exact grouping even above the limit."""
    _, full, rows = cases
    monkeypatch.setattr(D, "DISTINCT_DIRECT_SAMPLE_ROWS", 0)
    assert D.distinct(full, None, field).method == "sample"
    forced = D.distinct(full, None, field, 20, span=True, exact=True)
    assert (forced.method, forced.exact) == ("exact", True)
    counts, span = reference(rows, None, field, payload=True)
    check(forced, counts, span, 20, True)


def test_exact_on_demand_still_falls_back_to_the_estimate_on_timeout(cases, monkeypatch):
    _, full, _ = cases
    monkeypatch.setattr(D, "DISTINCT_DIRECT_SAMPLE_ROWS", 0)
    monkeypatch.setattr(Q, "PROGRESS_OPS", 1)
    result = D.distinct(full, None, "level", exact=True, timeout_s=0, estimate_timeout_s=None)
    assert (result.method, result.exact) == ("sample", False)


# --- estimates and timeouts --------------------------------------------------------------------


@pytest.fixture
def instant_timeout(monkeypatch):
    monkeypatch.setattr(Q, "PROGRESS_OPS", 1)


def test_timeout_falls_back_to_bucket_estimate_then_sample_then_none(cases, instant_timeout):
    plain, full, rows = cases
    # A ts list is not a window: no exact bucket plan, but the bucket estimate (whole minutes) holds it.
    tree = {"and": [{"source": [1, 2]}, {"ts": [1_000_100, 1_000_200, 1_000_300]}]}
    est = D.distinct(full, tree, "tpl", 20, timeout_s=0, estimate_timeout_s=None, bucket_timeout_s=None)
    assert est.method == "bucket_estimate" and not est.exact
    exact_total = sum(reference(rows, tree, "tpl")[0].values())
    assert est.total >= exact_total  # whole minutes: a superset
    sample = D.distinct(plain, {"source": [1, 2]}, "fields.op", 3, timeout_s=0, estimate_timeout_s=None)
    assert sample.method == "sample" and not sample.exact
    assert 0 < sample.total <= 4 * len(rows)
    assert sum(v["count"] for v in sample.values) + sample.others == sample.total
    keys = D.distinct(plain, None, "key.msgid", 3, timeout_s=0, estimate_timeout_s=None)
    assert keys.method == "sample"
    none = D.distinct(plain, None, "level", timeout_s=0, estimate_timeout_s=0)
    assert (none.method, none.total, none.values, none.exact) == ("none", None, [], False)


def test_empty_database(tmp_path):
    from dmslog.store.db import create_case_db

    create_case_db(tmp_path / "e.sqlite").close()
    conn = open_ro(tmp_path / "e.sqlite")
    for field in FIELDS:
        result = D.distinct(conn, None, field)
        assert (result.total, result.groups, result.values, result.exact) == (0, 0, [], True)
    conn.close()


# --- validation --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        None, 5, "", "msg", "e.msg", "tpl; DROP TABLE entry", "fields.", "fields.a-b", "fields.op') --",
        "fields." + "a" * 65, "key.", "key.a b", "key.' OR 1=1 --", "key." + "x" * 65, "source_id", "TPL",
    ],
)  # fmt: skip
def test_unknown_fields_are_rejected(cases, name):
    plain, _, _ = cases
    with pytest.raises(D.DistinctError) as info:
        D.distinct(plain, None, name)
    assert info.value.code == "bad_field" and info.value.path == "$.field"


@pytest.mark.parametrize("top", [0, D.MAX_TOP + 1, True, "5", 2.0])
def test_bad_limit_is_rejected(cases, top):
    plain, _, _ = cases
    with pytest.raises(D.DistinctError) as info:
        D.distinct(plain, None, "tpl", top)
    assert info.value.code == "bad_top"


def test_bad_span_and_bad_filter_are_rejected(cases):
    plain, _, _ = cases
    with pytest.raises(D.DistinctError):
        D.distinct(plain, None, "tpl", span="yes")
    with pytest.raises(D.DistinctError):
        D.distinct(plain, None, "tpl", exact=1)
    with pytest.raises(FilterError):
        D.distinct(plain, {"sev": "' OR 1=1"}, "tpl")


def test_field_names_never_reach_the_sql_text():
    cf = compile_filter({"sev": 1})
    for name, needle in (("fields.secret_name", "secret_name"), ("key.secret.type", "secret.type")):
        spec = D.parse_field(name)
        for entry_first in (False, True):
            sql, params = D._entry_group_sql(spec, cf, True, entry_first=entry_first)
            assert needle not in sql and any(needle in str(p) for p in params)


# --- the field filter leaf ---------------------------------------------------------------------


def test_field_leaf_semantics(cases):
    plain, _, rows = cases
    n_timeout = sum(1 for r in rows if r["fields"] and json.loads(r["fields"])["op"] == "timeout")
    assert Q.count(plain, {"field": {"name": "op", "eq": "timeout"}}).n == n_timeout
    assert Q.count(plain, {"field": {"name": "n", "eq": 3}}).n == 2
    assert Q.count(plain, {"field": {"name": "n", "eq": 3.0}}).n == 2
    assert Q.count(plain, {"field": {"name": "n", "eq": "3"}}).n == 0  # type matters
    assert Q.count(plain, {"field": {"name": "n", "eq": None}}).n == 5  # entries without fields
    assert Q.count(plain, {"field": {"name": "nope", "eq": None}}).n == len(rows)
    assert Q.count(plain, {"not": {"field": {"name": "nope", "eq": None}}}).n == 0


@pytest.mark.parametrize(
    "arg",
    [
        "op", {"name": "op"}, {"eq": 1}, {"name": "op", "eq": 1, "x": 2}, {"name": "a-b", "eq": 1},
        {"name": "op')--", "eq": 1}, {"name": 5, "eq": 1}, {"name": "op", "eq": True},
        {"name": "op", "eq": [1]},
        {"name": "op", "eq": {"a": 1}}, {"name": "op", "eq": "x" * 2000}, {"name": "op", "eq": 2**70},
        {"name": "op", "eq": float("nan")},
    ],
)  # fmt: skip
def test_field_leaf_validation(arg):
    with pytest.raises(FilterError):
        compile_filter({"field": arg})


def test_field_leaf_binds_name_and_value():
    cf = compile_filter({"field": {"name": "op", "eq": "x' OR 1=1 --"}})
    assert cf.sql == "(json_extract(e.fields, ?) IS ?)"
    assert cf.params == ("$.op", "x' OR 1=1 --")
