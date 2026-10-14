"""Declarative aggregation (store/aggregate.py, ROADMAP 9.6). Synthetic data only."""

from __future__ import annotations

import itertools
import json
import math
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

import pytest
from query_helpers import KEYS, SOURCE_STREAM, STREAM_FAMILY, build_case, open_ro, py_match

from dmslog.knowledge.pack import load_pack_from
from dmslog.knowledge.resolve import Knowledge
from dmslog.report import aggexport
from dmslog.store import aggregate as A
from dmslog.store import distinct as D
from dmslog.store import query as Q
from dmslog.store.filters import FilterError, compile_filter

MINI_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "mini_pack"
FILTERS = [
    None,
    {"sev": {"le": 3}},
    {"source": [1, 3]},
    {"and": [{"source": [1, 2]}, {"not": {"tpl": [0]}}]},
    {"or": [{"tid": 16}, {"level": {"ge": 6}}]},
    {"ts": {"ge": 1_000_100, "lt": 1_000_400}},
]
EVENT_NAMES = ["GetIncidents", "SendWorks", "Changed crews"]
EVENT_STATUSES = ["ok", "fail", "timeout", None]


def _events(rows):
    """Events 3..: chunks of the entries 17.. of sources 1 and 2, with durations (some unknown)."""
    rnd = random.Random(11)
    events = []
    ids = [r["id"] for r in rows if r["id"] >= 17 and r["source_id"] in (1, 2)]
    k = 0
    event_id = 3
    while k < len(ids):
        size = rnd.randint(1, 6)
        members = ids[k : k + size]
        k += size
        first = next(r for r in rows if r["id"] == members[0])
        duration = None if rnd.random() < 0.15 else rnd.choice([0, 5, 5, 17, 120, 999, rnd.randint(1, 5000)])
        events.append({
            "id": event_id, "stream_id": SOURCE_STREAM[first["source_id"]],
            "kind": "operation" if rnd.random() < 0.8 else "retry_seq", "name": rnd.choice(EVENT_NAMES),
            "status": rnd.choice(EVENT_STATUSES), "sev": rnd.randint(1, 5), "tid": first["tid"],
            "ts_start": first["ts"], "duration_ms": duration, "n_entries": len(members), "members": members,
        })  # fmt: skip
        event_id += 1
    return events


def _fill(path, rows):
    events = _events(rows)
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
        conn.execute("UPDATE collection SET ers_group = 1")
        for ev in events:
            conn.execute(
                "INSERT INTO event(id, stream_id, kind, name, tid, ts_start, duration_ms, status, sev,"
                " n_entries) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ev["id"], ev["stream_id"], ev["kind"], ev["name"], ev["tid"], ev["ts_start"],
                 ev["duration_ms"], ev["status"], ev["sev"], ev["n_entries"]),
            )  # fmt: skip
            marks = ",".join("?" * len(ev["members"]))
            conn.execute(f"UPDATE entry SET event_id = ? WHERE id IN ({marks})", (ev["id"], *ev["members"]))
            for m in ev["members"]:
                rows[m - 1]["event_id"] = ev["id"]
        conn.executemany(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)",
            [("errcode", "1.2", 50), ("errcode", "1.8", 51), ("errcode", "1.2", 52)],
        )
    conn.close()
    base_events = [
        {"id": 1, "stream_id": 1, "kind": "operation", "name": "GetIncidents", "status": "fail", "sev": 2,
         "tid": None, "ts_start": 1_000_000, "duration_ms": None, "n_entries": None},
        {"id": 2, "stream_id": 1, "kind": "operation", "name": "SendWorks", "status": "ok", "sev": 4,
         "tid": None, "ts_start": 1_000_100, "duration_ms": None, "n_entries": None},
    ]  # fmt: skip
    return base_events + events


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    """(plain: no bucket rows; full: bucket, events, collection group; rows; events)."""
    base = tmp_path_factory.mktemp("aggregate")
    rows = build_case(base / "plain.sqlite")
    build_case(base / "full.sqlite")
    events = _fill(base / "full.sqlite", rows)
    plain, full = open_ro(base / "plain.sqlite"), open_ro(base / "full.sqlite")
    yield plain, full, rows, events
    plain.close()
    full.close()


# --- reference -----------------------------------------------------------------------------------


def _keys(row):
    found = set(KEYS.get(row["id"], set()))
    found |= {("errcode", v) for v, e in (("1.2", 50), ("1.8", 51), ("1.2", 52)) if e == row["id"]}
    return found


def _floor(ts, width):
    return None if ts is None else ts // width * width


def _field(row, name):
    return json.loads(row["fields"]).get(name) if row["fields"] else None


def _entry_dim(row, dim, collection):
    if dim.startswith("bucket:"):
        return [_floor(row["ts"], A.BUCKETS[dim[7:]])]
    if dim.startswith("key."):
        return sorted(v for t, v in _keys(row) if t == dim[4:])
    if dim.startswith("fields."):
        return [_field(row, dim[7:])]
    if dim == "family":
        return [STREAM_FAMILY[SOURCE_STREAM[row["source_id"]]]]
    if dim == "collection":
        return [collection]
    return [row[{"tpl": "tpl_id", "source": "source_id"}.get(dim, dim)]]


def _rank(values, p):
    values = sorted(values)
    return values[math.ceil(len(values) * p / 100) - 1] if values else None


def _measure(fn, items):
    """items: list of (rid, value-or-set)."""
    if fn == "count":
        return len({rid for rid, _ in items})
    if fn == "distinct":
        return len({v for _, vs in items for v in vs if v is not None})
    values = [v for _, v in items if isinstance(v, int | float) and not isinstance(v, bool)]
    if fn in A.PERCENTILES:
        return _rank(values, A.PERCENTILES[fn])
    if not values:
        return None
    return {"min": min, "max": max, "sum": sum, "avg": lambda v: sum(v) / len(v)}[fn](values)


def ref_entries(rows, tree, dims, measures, collection=None):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if not py_match(tree, row):
            continue
        for combo in itertools.product(*[_entry_dim(row, d, collection) for d in dims]):
            for m in measures:
                fn, _, arg = m.partition(":")
                if fn == "distinct":
                    item = set(_entry_dim(row, arg, collection))
                elif arg.startswith("fields."):
                    item = _field(row, arg[7:])
                else:
                    item = None
                groups[combo][m].append((row["id"], item))
    return {g: tuple(_measure(m.partition(":")[0], per[m]) for m in measures) for g, per in groups.items()}


def ref_events(rows, events, tree, conds, dims, measures):
    matching = {r["event_id"] for r in rows if r.get("event_id") is not None and py_match(tree, r)}
    groups = defaultdict(lambda: defaultdict(list))
    for ev in events:
        if tree is not None and ev["id"] not in matching:
            continue
        if any(ev[name] not in values for name, values in conds.items()):
            continue
        combo = []
        for d in dims:
            if d.startswith("bucket:"):
                combo.append(_floor(ev["ts_start"], A.BUCKETS[d[7:]]))
            elif d == "family":
                combo.append(STREAM_FAMILY[ev["stream_id"]])
            elif d == "collection":
                combo.append(1)
            else:
                combo.append(ev[d])
        for m in measures:
            fn, _, arg = m.partition(":")
            item = (
                {ev[arg]}
                if fn == "distinct"
                else ev[{"duration": "duration_ms", "entries": "n_entries"}.get(arg)]
                if arg
                else None
            )
            groups[tuple(combo)][m].append((ev["id"], item))
    return {g: tuple(_measure(m.partition(":")[0], per[m]) for m in measures) for g, per in groups.items()}


def empty_row(measures):
    """Without dimensions an empty input still gives one row: counts 0, the rest NULL."""
    return tuple(0 if m == "count" or m.startswith("distinct:") else None for m in measures)


def as_map(result):
    n = len(result.request.dims)
    return {
        tuple(r["values"][:n]): tuple(r["values"][n : n + len(result.request.measures)]) for r in result.rows
    }


def same(got, want):
    assert set(got) == set(want)
    for g, values in want.items():
        for a, b in zip(got[g], values, strict=True):
            if isinstance(b, float) or isinstance(a, float):
                assert a == pytest.approx(b, abs=1e-3), g
            else:
                assert a == b, g


# --- exact results ------------------------------------------------------------------------------

ENTRY_CASES = [
    (["tpl"], ["count"]),
    (["sev", "source"], ["count", "distinct:tid"]),
    (["bucket:1m"], ["count", "min:fields.n", "max:fields.n", "avg:fields.n", "sum:fields.n"]),
    (["family", "collection", "bucket:5m"], ["count"]),
    (["fields.op"], ["count", "p50:fields.n", "p90:fields.n", "p95:fields.n", "p99:fields.n"]),
    (["key.msgid"], ["count", "distinct:source"]),
    (["key.errcode", "tpl"], ["count"]),
    ([], ["count", "distinct:key.msgid", "distinct:fields.op"]),
    (["level"], ["count", "distinct:key.errcode"]),
    (["tid", "pid"], ["distinct:level", "p50:fields.n"]),
]


@pytest.mark.parametrize("tree", FILTERS)
@pytest.mark.parametrize(("dims", "measures"), ENTRY_CASES)
def test_entries_match_the_reference(case, tree, dims, measures):
    plain, full, rows, _ = case
    for conn, collection in ((plain, None), (full, 1)):
        if conn is plain and ("key.errcode" in dims or "distinct:key.errcode" in measures):
            continue  # the plain case has no errcode keys
        result = A.aggregate(conn, {"filter": tree, "group_by": dims, "measures": measures, "limit": 1000})
        assert result.exact and result.method in ("exact", "bucket")
        want = ref_entries(rows, tree, dims, measures, collection)
        if not dims and not want:
            want = {(): empty_row(measures)}
        same(as_map(result), want)
        assert result.groups == len(want)
        if "count" in measures:
            assert result.total == sum(v[measures.index("count")] for v in want.values())


def test_bucket_path_equals_the_exact_path(case):
    _, full, rows, _ = case
    for tree in FILTERS:
        for dims in (["tpl"], ["sev", "bucket:1m"], ["collection", "family"], [], ["source", "bucket:1d"]):
            result = A.aggregate(full, {"filter": tree, "group_by": dims, "limit": 1000})
            expressible = D._bucket_plan(tree, compile_filter(tree)) is not None  # noqa: SLF001
            assert result.method == ("bucket" if expressible else "exact"), (tree, dims)
            exact = A.aggregate(full, {"filter": tree, "group_by": dims, "limit": 1000}, bucket_timeout_s=0)
            assert as_map(result) == as_map(exact)
    # ts in a list is not a window: the exact path answers
    result = A.aggregate(full, {"filter": {"ts": [1_000_100]}, "group_by": ["tpl"]})
    assert result.method == "exact"


EVENT_CASES = [
    (["name"], ["count", "p50:duration", "p90:duration", "p99:duration", "avg:duration", "max:duration"]),
    (["name", "status"], ["count", "min:duration", "sum:entries"]),
    (["bucket:1m"], ["count", "p95:duration", "distinct:name"]),
    (["family", "collection", "kind"], ["count", "avg:entries"]),
    ([], ["count", "distinct:status", "p50:duration"]),
    (["sev", "tid"], ["count"]),
]


@pytest.mark.parametrize("tree", [None, {"sev": {"le": 2}}, {"tpl": [1]}, {"source": [2]}])
@pytest.mark.parametrize(("dims", "measures"), EVENT_CASES)
@pytest.mark.parametrize("conds", [{}, {"kind": ["operation"]}, {"name": ["SendWorks", "Changed crews"]}])
def test_events_match_the_reference(case, tree, dims, measures, conds):
    _, full, rows, events = case
    req = {"rows": "events", "filter": tree, "group_by": dims, "measures": measures, "limit": 1000}
    if conds:
        req["events"] = conds
    result = A.aggregate(full, req)
    want = ref_events(rows, events, tree, conds, dims, measures)
    if not dims and not want:
        want = {(): empty_row(measures)}
    same(as_map(result), want)
    assert result.method == "exact" and result.exact


def test_percentiles_are_nearest_rank():
    durations = [None, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE event(id INTEGER PRIMARY KEY, stream_id INT, kind TEXT, name TEXT, key TEXT, tid INT,"
        " ts_start INT, duration_ms INT, status TEXT, sev INT, n_entries INT);"
        "CREATE TABLE stream(id INTEGER PRIMARY KEY, family TEXT, collection_group INT);"
        "CREATE TABLE entry(id INTEGER PRIMARY KEY, event_id INT);"
    )
    conn.executemany(
        "INSERT INTO event(stream_id, kind, name, ts_start, duration_ms, sev)"
        " VALUES (1,'operation','Op',0,?,4)",
        [(d,) for d in durations],
    )
    measures = ["p50:duration", "p90:duration", "p95:duration", "p99:duration", "min:duration", "count"]
    result = A.aggregate(conn, {"rows": "events", "measures": measures})
    assert result.rows[0]["values"] == [50, 90, 100, 100, 10, 11]


# --- order, limit, drill-down -------------------------------------------------------------------


def test_order_limit_groups_and_total(case):
    _, full, rows, _ = case
    result = A.aggregate(full, {"group_by": ["source", "sev"], "limit": 3})
    assert len(result.rows) == 3 and result.groups == 11 and result.truncated  # source 3: noise only
    counts = [r["values"][2] for r in result.rows]
    assert counts == sorted(counts, reverse=True)
    assert result.total == len(rows)
    asc = A.aggregate(full, {"group_by": ["sev"], "order": {"by": "sev", "dir": "asc"}})
    assert [r["values"][0] for r in asc.rows] == [1, 2, 3, 4, 5]
    series = A.aggregate(full, {"group_by": ["bucket:1m"], "filter": {"source": [1]}})
    times = [r["values"][0] for r in series.rows]
    assert times == sorted(times)  # a bucket orders by time by default
    labels = A.aggregate(full, {"group_by": ["tpl", "source", "collection"], "limit": 1000})
    for row in labels.rows:
        tpl, source, group = row["values"][:3]
        assert row["labels"][1] == {1: "a.log", 2: "a.log", 3: "x.txt"}[source]
        assert row["labels"][2] == "C1" and group == 1
        assert row["labels"][0] == {0: None, 1: "TST-001", 2: "TST-002"}[tpl]


@pytest.mark.parametrize(
    "dims", [["tpl"], ["sev", "bucket:1m"], ["key.msgid"], ["fields.op", "family"], ["collection", "level"]]
)
def test_drill_down_selects_the_group(case, dims):
    _, full, rows, _ = case
    tree = {"source": [1, 2]}
    result = A.aggregate(full, {"filter": tree, "group_by": dims, "limit": 1000})
    for row in result.rows:
        if row["drill"] is None:
            assert any(v is None for v in row["values"][: len(dims)])
            continue
        drilled = {"and": [tree, row["drill"]]}
        n = Q.count(full, drilled).n
        assert n == row["values"][len(dims)], row


def test_event_drill_down_selects_records_of_the_group(case):
    _, full, rows, events = case
    result = A.aggregate(full, {"rows": "events", "group_by": ["name", "status"], "limit": 1000})
    for row in result.rows:
        name, status = row["values"][:2]
        if status is None:
            assert row["drill"] is None
            continue
        assert row["drill"] == {"and": [{"event_name": name}, {"event_status": status}]}
        ids = {ev["id"] for ev in events if ev["name"] == name and ev["status"] == status}
        want = sum(1 for r in rows if r.get("event_id") in ids)
        assert Q.count(full, row["drill"]).n == want
    kinds = compile_filter({"event_kind": "retry_seq"})
    got = full.execute(f"SELECT count(*) FROM entry e WHERE {kinds.sql}", kinds.params).fetchone()[0]
    retry = {ev["id"] for ev in events if ev["kind"] == "retry_seq"}
    assert got == sum(1 for r in rows if r.get("event_id") in retry)
    rowwise = compile_filter({"not": {"event_name": ["SendWorks"]}}, rowwise=True)
    plain = compile_filter({"not": {"event_name": ["SendWorks"]}})
    for cf in (rowwise, plain):
        n = full.execute(f"SELECT count(*) FROM entry e WHERE {cf.sql}", cf.params).fetchone()[0]
        send = {ev["id"] for ev in events if ev["name"] == "SendWorks"}
        assert n == sum(1 for r in rows if r.get("event_id") not in send)


# --- time policy --------------------------------------------------------------------------------


def test_timeout_falls_back_to_estimates_then_408(case, monkeypatch):
    _, full, rows, _ = case
    req = {"group_by": ["level"], "measures": ["count", "p50:fields.n"]}
    result = A.aggregate(full, req, timeout_s=0)
    assert result.method == "sample" and not result.exact and result.total is not None
    exact = A.aggregate(full, req)
    assert result.total == pytest.approx(exact.total, rel=0.6)
    est = A.aggregate(full, {"group_by": ["tpl"]}, timeout_s=0, bucket_timeout_s=0.5)
    assert est.method in ("bucket", "bucket_estimate")
    real_run = A._run  # noqa: SLF001
    calls = []

    def first_too_slow(*args):
        calls.append(1)
        if len(calls) == 1:
            raise Q.QueryTimeout("simulated")
        return real_run(*args)

    monkeypatch.setattr(A, "_run", first_too_slow)  # the exact statement "times out"
    events = A.aggregate(
        full, {"rows": "events", "group_by": ["name"], "measures": ["count", "p90:duration"]}
    )
    assert events.method == "sample" and not events.exact and len(calls) == 2

    def too_slow(*args):
        raise Q.QueryTimeout("simulated")

    monkeypatch.setattr(A, "_run", too_slow)  # every statement "times out"
    with pytest.raises(A.AggregateTimeout) as info:
        A.aggregate(full, req)
    assert info.value.estimated_rows == len(rows)  # bucket bound: timed rows plus the 5 without ts
    with pytest.raises(A.AggregateTimeout) as info:
        A.aggregate(full, {"rows": "events", "group_by": ["name"]})
    assert info.value.estimated_rows == len(case[3])  # events the event conditions keep


def test_direct_sample_above_the_bound_unless_exact(case, monkeypatch):
    _, full, _, _ = case
    monkeypatch.setattr(A, "DIRECT_SAMPLE_ROWS", 10)
    req = {"group_by": ["level"]}
    assert A.aggregate(full, req).method == "sample"
    assert A.aggregate(full, {**req, "exact": True}).method == "exact"


def test_empty_case_answers_exactly(tmp_path):
    from dmslog.store.db import connect, create_case_db

    create_case_db(tmp_path / "e.sqlite").close()
    conn = connect(tmp_path / "e.sqlite", readonly=True)
    result = A.aggregate(conn, {"group_by": ["tpl"], "measures": ["count"]}, timeout_s=0)
    assert result.rows == [] and result.exact
    total = A.aggregate(conn, {"measures": ["count", "avg:fields.n"]})
    assert total.rows[0]["values"] == [0, None]
    conn.close()


# --- lookups ------------------------------------------------------------------------------------


def test_lookups_without_pack_are_left_out(case, monkeypatch):
    _, full, _, _ = case
    from dmslog.knowledge import pack as pack_module

    monkeypatch.setattr(pack_module, "current_pack", lambda: None)
    req = {"group_by": ["key.errcode"], "lookups": ["error.reason"]}
    for knowledge in (None, Knowledge()):
        result = A.aggregate(full, req, knowledge=knowledge)
        assert [c["role"] for c in result.columns] == ["group", "measure"]
        assert result.lookup["state"] == "not_installed" and result.lookup["message"]
        assert result.notes == ["lookups_without_pack"]
        assert all(len(r["values"]) == 2 for r in result.rows)


def test_lookups_from_the_pack(tmp_path):
    rows = build_case(tmp_path / "c.sqlite")
    conn = sqlite3.connect(tmp_path / "c.sqlite")
    with conn:
        conn.execute("UPDATE stream SET family = 'AAAA' WHERE id IN (1, 2)")
        conn.executemany(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES ('errcode', ?, ?)",
            [("1.2", 50), ("1.8", 51), ("9.9", 52)],
        )
        conn.execute("UPDATE event SET name = 'ChangedExampleItems' WHERE id = 1")
    conn.close()
    conn = open_ro(tmp_path / "c.sqlite")
    knowledge = Knowledge(pack=load_pack_from(MINI_PACK))
    result = A.aggregate(
        conn,
        {
            "group_by": ["key.errcode"],
            "lookups": ["error.reason", "error.severity"],
            "order": {"by": "key.errcode"},
        },
        knowledge=knowledge,
    )
    assert [c["id"] for c in result.columns] == ["key.errcode", "count", "error.reason", "error.severity"]
    got = {r["values"][0]: r["values"][2:] for r in result.rows}
    assert got == {"1.2": ["IdMissing", "WARNING"], "1.8": ["InvalidMessage", "FATAL"], "9.9": [None, None]}
    assert result.lookup == {
        "state": "installed",
        "columns": ["error.reason", "error.severity"],
        "message": None,
    }
    ops = A.aggregate(
        conn,
        {
            "rows": "events",
            "group_by": ["family", "name"],
            "lookups": ["operation.direction", "operation.service"],
        },
        knowledge=knowledge,
    )
    got = {r["values"][1]: r["values"][3:] for r in ops.rows}
    assert got["ChangedExampleItems"] == ["inbound", "ReceiveExampleItems"]
    assert got["SendWorks"] == [None, None]
    assert rows  # the synthetic case is not empty
    conn.close()


# --- validation and safety ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code", "path"),
    [
        ([], "bad_type", "$"),
        ({"rows": "lines"}, "bad_rows", "$.rows"),
        ({"filter": [1]}, "bad_type", "$.filter"),
        ({"group_by": "tpl"}, "bad_type", "$.group_by"),
        ({"group_by": ["msg"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["tpl; DROP TABLE entry"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["fields.a-b"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["key.a b"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["bucket:2h"]}, "bad_bucket", "$.group_by[0]"),
        ({"group_by": ["bucket:1h", "bucket:1d"]}, "one_bucket", "$.group_by"),
        ({"group_by": ["tpl", "sev", "source", "level"]}, "too_many_dims", "$.group_by"),
        ({"group_by": ["tpl", "tpl"]}, "duplicate", "$"),
        ({"group_by": ["key.a", "key.b", "key.c"]}, "too_many_keys", "$.group_by"),
        ({"rows": "events", "group_by": ["tpl"]}, "bad_dimension", "$.group_by[0]"),
        ({"measures": []}, "no_measure", "$.measures"),
        ({"measures": ["median"]}, "bad_measure", "$.measures[0]"),
        ({"measures": ["p42:fields.n"]}, "bad_measure", "$.measures[0]"),
        ({"measures": ["p90:duration"]}, "needs_events", "$.measures[0]"),
        ({"measures": ["p90:msg"]}, "bad_value", "$.measures[0]"),
        ({"rows": "events", "measures": ["avg:fields.n"]}, "bad_value", "$.measures[0]"),
        ({"measures": ["distinct:bucket:1h"]}, "bad_measure", "$.measures[0]"),
        ({"measures": ["count"] * 9}, "too_many_measures", "$.measures"),
        ({"measures": ["distinct:key.msgid", "avg:fields.n"]}, "key_with_numeric", "$.measures"),
        ({"order": {"by": "sev"}}, "bad_order", "$.order.by"),
        ({"order": {"by": "count", "dir": "up"}}, "bad_direction", "$.order.dir"),
        ({"order": "count"}, "bad_type", "$.order"),
        ({"limit": 0}, "bad_limit", "$.limit"),
        ({"limit": 1001}, "bad_limit", "$.limit"),
        ({"limit": True}, "bad_limit", "$.limit"),
        ({"lookups": ["error.reason"]}, "lookup_needs", "$.lookups[0]"),
        ({"lookups": ["sql"]}, "bad_lookup", "$.lookups[0]"),
        (
            {"rows": "events", "group_by": ["name"], "lookups": ["error.reason"]},
            "lookup_needs",
            "$.lookups[0]",
        ),
        ({"exact": 1}, "bad_exact", "$.exact"),
        ({"events": {"kind": ["operation"]}}, "events_needs_events", "$.events"),
        ({"rows": "events", "events": {"key": ["x"]}}, "bad_events", "$.events.key"),
        ({"rows": "events", "events": {"name": []}}, "bad_events_list", "$.events.name"),
        ({"rows": "events", "events": {"name": ["a\nb"]}}, "bad_events_value", "$.events.name[0]"),
    ],
)
def test_validation(body, code, path):
    with pytest.raises(A.AggregateError) as info:
        A.parse_request(body)
    assert info.value.code == code and info.value.path == path


def test_unknown_fields_and_filters_are_rejected(case):
    _, full, _, _ = case
    for req in ({"group_by": ["fields.not_in_catalog"]}, {"measures": ["avg:fields.nope_x"]},
                {"group_by": ["key.not_a_type_here"]}, {"measures": ["distinct:key.zzz"]}):  # fmt: skip
        with pytest.raises(A.AggregateError) as info:
            A.aggregate(full, req)
        assert info.value.code == "bad_dimension"
    with pytest.raises(FilterError):
        A.aggregate(full, {"filter": {"bogus": 1}})


def test_no_client_text_reaches_the_sql(case):
    """Values only travel as parameters: hostile strings in event lists and filters are plain data."""
    _, full, _, _ = case
    statements = []
    full.set_trace_callback(statements.append)
    try:
        hostile = "x'); DROP TABLE entry;--"
        result = A.aggregate(
            full,
            {"rows": "events", "filter": {"key": {"type": "msgid", "eq": hostile}},
             "events": {"name": [hostile]}, "group_by": ["name"], "measures": ["count", "p90:duration"]},
        )  # fmt: skip
        assert result.rows == []
    finally:
        full.set_trace_callback(None)
    # the trace expands parameters; the statements themselves come from constants
    assert full.execute("SELECT count(*) FROM entry").fetchone()[0] > 0


def test_csv_export(case):
    _, full, _, _ = case
    result = A.aggregate(
        full, {"group_by": ["sev", "bucket:1h", "tpl"], "measures": ["count", "avg:fields.n"], "limit": 4}
    )
    text = "".join(aggexport.stream_csv(result, bom=True))
    lines = text.split("\r\n")
    assert lines[0] == "﻿Severity,Time (1h),Message type,Count,Average fields.n"
    assert len(lines) == 6 and lines[-1] == ""
    first = lines[1].split(",")
    assert first[0] in ("critical", "error", "warning", "info", "noise")
    assert first[1].startswith("1970-01-01 00:00:00.000")


def test_events_direct_sample_and_access_paths(case, monkeypatch):
    _, full, rows, events = case
    req = {"rows": "events", "group_by": ["name"], "measures": ["count", "p90:duration"]}
    monkeypatch.setattr(A, "EVENTS_DIRECT_SAMPLE_ROWS", 5)
    assert A.aggregate(full, req).method == "sample"
    assert A.aggregate(full, {**req, "exact": True}).method == "exact"
    # a broad view over many events also samples; few events are checked one by one (exact)
    monkeypatch.setattr(A, "EVENTS_DIRECT_SAMPLE_ROWS", 10**9)
    monkeypatch.setattr(A, "DIRECT_SAMPLE_ROWS", 10)
    monkeypatch.setattr(A, "EVENTS_ROWWISE_MAX", 0)
    assert A.aggregate(full, {**req, "filter": {"sev": {"le": 4}}}).method == "sample"
    monkeypatch.setattr(A, "EVENTS_ROWWISE_MAX", 10**6)
    exact = A.aggregate(full, {**req, "filter": {"sev": {"le": 4}}})
    assert exact.method == "exact"
    same(as_map(exact), ref_events(rows, events, {"sev": {"le": 4}}, {}, ["name"], req["measures"]))


@pytest.mark.parametrize(
    "conds",
    [{"status": ["fail"]}, {"kind": ["retry_seq"]}, {"name": ["SendWorks"], "status": ["ok", "timeout"]}],
)
def test_event_drill_down_keeps_the_event_conditions(case, conds):
    """Each row's drill selects exactly the records of the events counted in it (conditions included)."""
    _, full, rows, events = case
    result = A.aggregate(full, {"rows": "events", "events": conds, "group_by": ["name"], "limit": 1000})
    assert result.rows
    for row in result.rows:
        name = row["values"][0]
        ids = {
            ev["id"] for ev in events
            if ev["name"] == name and all(ev[k] in v for k, v in conds.items())
        }  # fmt: skip
        assert row["values"][1] == len(ids)
        want = sum(1 for r in rows if r.get("event_id") in ids)
        assert Q.count(full, row["drill"]).n == want, (conds, name)
