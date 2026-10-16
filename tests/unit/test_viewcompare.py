"""Comparison of views by message type (store/viewcompare.py, ROADMAP 9.7). Synthetic data only."""

from __future__ import annotations

import sqlite3

import pytest
from compare_helpers import STREAMS, Content, Record, build, open_ro, random_content, without_ids

from dmslog.parse.signature import signature
from dmslog.store import align as AL
from dmslog.store import viewcompare as V
from dmslog.store.filters import FilterError


def run(path, body):
    conn = open_ro(path)
    try:
        return V.compare(conn, V.parse_request(body))
    finally:
        conn.close()


def seq(stream, start, codes, ts0=1_000_000, step=10):
    return [
        Record(stream, start + k, ts0 + (start + k) * step, c, "odd 1 line" if c is None else "m")
        for k, c in enumerate(codes)
    ]


def codes_of(n):
    return [f"CMP-00{k}" for k in n]


@pytest.fixture
def ops_case(tmp_path):
    recs = seq("wfm1", 1, codes_of([1, 2, 3, 4])) + seq("wfm1", 5, codes_of([1, 3, 4, 5]))
    recs += seq("wfm1", 9, codes_of([1, 2, 3])) + seq("wfm1", 12, codes_of([1, 5, 6, 3]))
    content = Content(
        recs, {"op1": [("wfm1", k) for k in range(1, 5)], "op2": [("wfm1", k) for k in range(5, 9)]}
    )
    return build(tmp_path / "ops.sqlite", content)


def test_operation_mode_aligns_two_runs(ops_case):
    b = ops_case
    out = run(b.path, {"mode": "operation", "a": {"event": b.event["op1"]}, "b": {"event": b.event["op2"]}})
    assert out["runs"] == [
        {"op": "same", "a": [0, 1], "b": [0, 1]},
        {"op": "only_a", "a": [1, 1], "b": [1, 0]},
        {"op": "same", "a": [2, 2], "b": [1, 2]},
        {"op": "only_b", "a": [4, 0], "b": [3, 1]},
    ]
    assert out["optimal"] is True and out["method"] == "myers" and out["notes"] == []
    assert out["stats"] == {
        "same": 3,
        "only_a": 1,
        "only_b": 1,
        "changed_a": 0,
        "changed_b": 0,
        "similarity": 0.75,
    }
    assert out["a"]["event"]["name"] == "op1" and out["a"]["count"] == 4 and out["a"]["truncated"] is False
    by_code = {t["code"]: (t["a"], t["b"]) for t in out["templates"]}
    assert by_code == {
        "CMP-001": (1, 1),
        "CMP-002": (1, 0),
        "CMP-003": (1, 1),
        "CMP-004": (1, 1),
        "CMP-005": (0, 1),
    }
    assert out["templates"][0]["label"] in ("Title of CMP-002", "Title of CMP-005")
    rows = out["rows"]
    assert [r["op"] for r in rows] == ["same", "only_a", "same", "same", "only_b"]
    assert rows[1]["b"] is None and rows[1]["a"]["tpl"] == "CMP-002" and rows[1]["a_pos"] == 1
    assert rows[4]["a"] is None and rows[4]["b"]["label"] == "Title of CMP-005"
    assert rows[0]["a"]["id"] == b.entry[("wfm1", 1)] and rows[0]["b"]["id"] == b.entry[("wfm1", 5)]
    assert out["total_rows"] == 5 and out["next_offset"] is None and len(out["digest"]) == 32


def test_substitution_is_a_changed_run_with_paired_rows(ops_case):
    b = ops_case
    a_win, b_win = (1_000_090, 1_000_120), (1_000_120, 1_000_160)  # lines 9-11 and 12-15
    out = run(
        b.path,
        {
            "mode": "window",
            "filter": {"stream": [b.stream["wfm1"]]},
            "a": {"from": a_win[0], "to": a_win[1]},
            "b": {"from": b_win[0], "to": b_win[1]},
        },
    )
    assert [(r["op"], r["a"], r["b"]) for r in out["runs"]] == [
        ("same", [0, 1], [0, 1]),
        ("changed", [1, 1], [1, 2]),
        ("same", [2, 1], [3, 1]),
    ]
    rows = out["rows"]
    assert [(r["op"], r["a_pos"], r["b_pos"]) for r in rows] == [
        ("same", 0, 0),
        ("changed", 1, 1),
        ("changed", None, 2),
        ("same", 2, 3),
    ]
    assert out["stats"]["changed_a"] == 1 and out["stats"]["changed_b"] == 2
    assert out["a"] == {
        "count": 3,
        "truncated": False,
        "first_ts": 1_000_090,
        "last_ts": 1_000_110,
        "from": a_win[0],
        "to": a_win[1],
    }


def test_process_mode_with_window_and_selector(tmp_path):
    recs = seq("wfm1", 1, codes_of([1, 2, 3])) + seq("wfm2", 1, codes_of([1, 3]))
    b = build(tmp_path / "p.sqlite", Content(recs))
    out = run(
        b.path,
        {
            "mode": "process",
            "a": {"stream": b.stream["wfm1"]},
            "b": {"stream": b.stream["wfm2"], "from": 0, "to": 2_000_000},
        },
    )
    assert out["a"]["stream"] == {
        "id": b.stream["wfm1"],
        "collection": 1,
        "family": "AWFM",
        "process": "Awfm",
        "pid": 100,
        "sensitive": 0,
    }
    assert out["b"]["from"] == 0 and out["b"]["to"] == 2_000_000 and "from" not in out["a"]
    assert out["stats"]["same"] == 2 and out["stats"]["only_a"] == 1


def test_filter_mode_and_common_filter(tmp_path):
    recs = seq("wfm1", 1, codes_of([1, 2, 3, 1, 2]))
    b = build(tmp_path / "f.sqlite", Content(recs))
    out = run(
        b.path,
        {
            "mode": "filter",
            "filter": {"not": {"tpl": ["CMP-002"]}},
            "a": {"filter": {"ts": {"lt": 1_000_030}}},
            "b": {"filter": {"ts": {"ge": 1_000_030}}},
        },
    )
    assert out["a"]["count"] == 1 and out["b"]["count"] == 2  # [1] vs [3, 1]
    assert out["stats"]["same"] == 1 and out["stats"]["only_b"] == 1
    empty = run(b.path, {"mode": "filter", "a": {"filter": None}, "b": {"filter": {"tpl": ["CMP-006"]}}})
    assert empty["b"]["count"] == 0 and empty["stats"]["similarity"] == 0.0
    both_empty = run(
        b.path, {"mode": "filter", "a": {"filter": {"tpl": ["CMP-006"]}}, "b": {"filter": {"or": []}}}
    )
    assert both_empty["runs"] == [] and both_empty["rows"] == [] and both_empty["stats"]["similarity"] == 1.0


def test_unknown_records_match_only_by_signature(tmp_path):
    recs = [
        Record("wfm1", 1, 1_000_000, None, "odd thing 12 happened"),
        Record("wfm1", 2, 1_000_001, None, "other 'quoted' line"),
        Record("wfm2", 1, 1_000_000, None, "odd thing 977 happened"),  # same signature as line 1
        Record("wfm2", 2, 1_000_001, None, "something else entirely"),
    ]
    b = build(tmp_path / "u.sqlite", Content(recs))
    out = run(
        b.path, {"mode": "process", "a": {"stream": b.stream["wfm1"]}, "b": {"stream": b.stream["wfm2"]}}
    )
    assert out["stats"]["same"] == 1 and out["runs"][0] == {"op": "same", "a": [0, 1], "b": [0, 1]}
    unknown = [t for t in out["templates"] if t["unknown"]]
    assert {t["label"] for t in unknown} == {
        signature("odd thing 12 happened"),
        signature("other 'quoted' line"),
        signature("something else entirely"),
    }
    assert all(t["code"] is None for t in unknown)


def test_stable_order_breaks_ties_by_locator_not_by_id(tmp_path):
    # same millisecond in two files; ids interleave differently in the two builds
    recs = [Record("wfm2", k, 1_000_000, f"CMP-00{k}", "m") for k in range(1, 4)]
    recs += [Record("wfm1", k, 1_000_000, f"CMP-00{k + 3}", "m") for k in range(1, 4)]
    content = Content(recs)
    body = {"mode": "filter", "a": {"filter": None}, "b": {"filter": {"not": {"tpl": ["CMP-001"]}}}}
    plain = run(build(tmp_path / "a.sqlite", content).path, body)
    other = run(build(tmp_path / "b.sqlite", content, permute=3).path, body)
    # member "Log/WfmAdapter_100.log" (wfm1) sorts before "..._200.log" (wfm2)
    assert [r["a"]["tpl"] for r in plain["rows"]] == [
        "CMP-004",
        "CMP-005",
        "CMP-006",
        "CMP-001",
        "CMP-002",
        "CMP-003",
    ]
    assert without_ids(plain) == without_ids(other)


@pytest.mark.parametrize("max_side", [1, 2, 3, 5, 8, 13])
def test_truncation_keeps_the_first_records_in_stable_order(tmp_path, max_side):
    content = random_content(5, n=40, ts_span=6)  # many ties, a few records without ts
    plain = build(tmp_path / "a.sqlite", content)
    other = build(tmp_path / "b.sqlite", content, permute=9)
    body = {
        "mode": "filter",
        "a": {"filter": None},
        "b": {"filter": {"sensitive": False}},
        "max_side": max_side,
    }
    out = run(plain.path, body)
    assert out["a"]["count"] == max_side and out["a"]["truncated"] is True
    assert out["notes"][:2] == ["truncated_a", "truncated_b"]
    expected = sorted(
        content.records,
        key=lambda r: (r.ts is not None, r.ts or 0, STREAMS[r.stream][0], STREAMS[r.stream][4], r.line),
    )[:max_side]
    got = [r["a"] for r in run(plain.path, {**body, "limit": 1000})["rows"] if r["a"] is not None]
    assert [g["id"] for g in got] == [plain.entry[(r.stream, r.line)] for r in expected]
    assert without_ids(out) == without_ids(run(other.path, body))


def test_paging_and_only_diff(ops_case):
    b = ops_case
    body = {
        "mode": "filter",
        "a": {"filter": {"event": [b.event["op1"]]}},
        "b": {"filter": {"ts": {"ge": 1_000_090}}},
    }
    full = run(b.path, {**body, "limit": 1000})
    pages, offset = [], 0
    while offset is not None:
        page = run(b.path, {**body, "limit": 2, "offset": offset})
        assert page["total_rows"] == full["total_rows"] and page["digest"] == full["digest"]
        pages += page["rows"]
        offset = page["next_offset"]
    assert pages == full["rows"]
    diff = run(b.path, {**body, "only_diff": True, "limit": 1000})
    assert diff["rows"] == [r for r in full["rows"] if r["op"] != "same"]
    assert diff["runs"] == full["runs"] and diff["total_rows"] == len(diff["rows"])
    beyond = run(b.path, {**body, "offset": 500})
    assert beyond["rows"] == [] and beyond["next_offset"] is None


def test_stages_of_the_alignment(ops_case, monkeypatch):
    b = ops_case
    body = {"mode": "operation", "a": {"event": b.event["op1"]}, "b": {"event": b.event["op2"]}}
    exact = run(b.path, body)
    assert exact["method"] == "myers"
    monkeypatch.setattr(AL, "ALIGN_WORK", 0)
    bits = run(b.path, body)
    assert bits["method"] == "bit_parallel" and bits["optimal"] is True and "approximate" not in bits["notes"]
    assert bits["stats"] == exact["stats"]
    monkeypatch.setattr(AL, "BIT_WORK", 0)
    approx = run(b.path, body)
    assert approx["optimal"] is False and approx["method"] == "anchored" and "approximate" in approx["notes"]
    assert approx["stats"]["same"] <= exact["stats"]["same"]
    assert sum(r["a"][1] for r in approx["runs"]) == 4 and sum(r["b"][1] for r in approx["runs"]) == 4
    monkeypatch.setattr(AL, "FALLBACK_WORK", 0)
    bare = run(b.path, body)  # no budget at all: only the linear trimming of common prefix and suffix
    assert bare["method"] == "anchored" and bare["stats"]["same"] <= exact["stats"]["same"]
    assert sum(r["a"][1] for r in bare["runs"]) == 4 and sum(r["b"][1] for r in bare["runs"]) == 4


def test_notes_for_incomplete_index_and_mixed_clocks(tmp_path):
    recs = seq("wfm1", 1, codes_of([1, 2])) + seq("ont", 1, codes_of([1, 2]))
    b = build(tmp_path / "n.sqlite", Content(recs))
    body = {"mode": "filter", "a": {"filter": None}, "b": {"filter": {"collection": [1]}}}
    assert run(b.path, body)["notes"] == ["mixed_clocks"]
    conn = sqlite3.connect(b.path)
    with conn:
        conn.execute("INSERT INTO meta(key, value) VALUES ('partial_scope.1', '{}')")
    conn.close()
    assert run(b.path, body)["notes"] == ["incomplete_index", "mixed_clocks"]
    same_clock = {
        "mode": "filter",
        "a": {"filter": {"collection": [1]}},
        "b": {"filter": {"collection": [1]}},
    }
    assert run(b.path, same_clock)["notes"] == ["incomplete_index"]


def test_missing_event_and_stream(ops_case):
    b = ops_case
    with pytest.raises(V.CompareError) as err:
        run(b.path, {"mode": "operation", "a": {"event": b.event["op1"]}, "b": {"event": 999}})
    assert err.value.code == "event_not_found" and err.value.path == "$.b.event"
    with pytest.raises(V.CompareError) as err:
        run(b.path, {"mode": "process", "a": {"stream": 999}, "b": {"stream": 1}})
    assert err.value.code == "stream_not_found" and err.value.path == "$.a.stream"


@pytest.mark.parametrize(
    ("body", "code", "path"),
    [
        ({"mode": "diff"}, "bad_mode", "$.mode"),
        ({"mode": "window", "a": [], "b": {}}, "bad_side", "$.a"),
        ({"mode": "window", "a": {"from": 1}, "b": {"from": 1, "to": 2}}, "missing_side_key", "$.a.to"),
        ({"mode": "window", "a": {"from": 1, "to": 2, "x": 1}, "b": {}}, "bad_side_key", "$.a.x"),
        ({"mode": "window", "a": {"from": 2, "to": 2}, "b": {}}, "bad_window", "$.a.to"),
        ({"mode": "window", "a": {"from": 1.5, "to": 2}, "b": {}}, "bad_window", "$.a.from"),
        ({"mode": "window", "a": {"from": True, "to": 2}, "b": {}}, "bad_window", "$.a.from"),
        ({"mode": "window", "a": {"from": 0, "to": 2**63}, "b": {}}, "bad_window", "$.a.to"),
        ({"mode": "operation", "a": {"event": 0}, "b": {"event": 1}}, "bad_id", "$.a.event"),
        ({"mode": "operation", "a": {"event": "1"}, "b": {"event": 1}}, "bad_id", "$.a.event"),
        ({"mode": "process", "a": {"stream": 1, "from": 5}, "b": {"stream": 1}}, "bad_window", "$.a.to"),
        (
            {"mode": "process", "a": {"stream": 1, "event": 5}, "b": {"stream": 1}},
            "bad_side_key",
            "$.a.event",
        ),
        ({"mode": "filter", "a": {"filter": [1]}, "b": {"filter": None}}, "bad_type", "$.a.filter"),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": {"bogus": 1}}},
            "unknown_operator",
            "$.b.filter",
        ),
        (
            {"mode": "filter", "filter": {"sev": {"zz": 1}}, "a": {"filter": None}, "b": {"filter": None}},
            "unknown_operator",
            "$.filter.sev",
        ),
        (
            {"mode": "filter", "filter": 3, "a": {"filter": None}, "b": {"filter": None}},
            "bad_type",
            "$.filter",
        ),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": None}, "max_side": 0},
            "bad_max_side",
            "$.max_side",
        ),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": None}, "max_side": V.MAX_SIDE + 1},
            "bad_max_side",
            "$.max_side",
        ),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": None}, "offset": -1},
            "bad_offset",
            "$.offset",
        ),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": None}, "limit": 1001},
            "bad_limit",
            "$.limit",
        ),
        (
            {"mode": "filter", "a": {"filter": None}, "b": {"filter": None}, "only_diff": 1},
            "bad_only_diff",
            "$.only_diff",
        ),
    ],
)
def test_request_validation(body, code, path):
    with pytest.raises((V.CompareError, FilterError)) as err:
        V.parse_request(body)
    assert err.value.code == code and err.value.path == path, (err.value.code, err.value.path)
    assert str(err.value)


def test_side_trees_compose_with_the_common_filter():
    req = V.parse_request(
        {
            "mode": "process",
            "filter": {"sev": {"le": 3}},
            "a": {"stream": 4, "from": 10, "to": 20},
            "b": {"stream": 5},
        }
    )
    assert req.side_tree(req.a) == {
        "and": [{"sev": {"le": 3}}, {"and": [{"stream": [4]}, {"ts": {"ge": 10, "lt": 20}}]}]
    }
    assert req.side_tree(req.b) == {"and": [{"sev": {"le": 3}}, {"stream": [5]}]}
    assert (
        V.parse_request({"mode": "filter", "filter": {}, "a": {"filter": {}}, "b": {"filter": None}}).filter
        is None
    )


@pytest.mark.parametrize("seed", range(12))
def test_same_result_for_any_indexing_of_the_same_files(tmp_path, seed):
    content = random_content(seed)
    plain = build(tmp_path / "a.sqlite", content)
    others = [build(tmp_path / f"p{k}.sqlite", content, permute=seed * 10 + k) for k in range(2)]
    assert all(o.entry != plain.entry and o.stream != plain.stream for o in others)  # the ids really differ
    bodies = [
        ({"mode": "filter", "a": {"filter": {"family": "AWFM"}}, "b": {"filter": {"family": "AONT"}}}, {}),
        (
            {
                "mode": "window",
                "a": {"from": 1_000_000, "to": 1_000_020},
                "b": {"from": 1_000_020, "to": 1_000_041},
            },
            {},
        ),
        ({"mode": "process", "max_side": 7}, {"a": "wfm1", "b": "wfm2"}),
        ({"mode": "operation"}, {"a": "op1", "b": "op2"}),
        (
            {
                "mode": "filter",
                "a": {"filter": None},
                "b": {"filter": {"sev": 4}},
                "max_side": 9,
                "only_diff": True,
            },
            {},
        ),
    ]
    for body, ids in bodies:
        results = []
        for built in (plain, *others):
            req = dict(body)
            if body["mode"] == "process":
                req["a"], req["b"] = {"stream": built.stream[ids["a"]]}, {"stream": built.stream[ids["b"]]}
            elif body["mode"] == "operation":
                if not set(ids.values()) <= set(built.event):
                    continue
                req["a"], req["b"] = {"event": built.event[ids["a"]]}, {"event": built.event[ids["b"]]}
            results.append(without_ids(run(built.path, {**req, "limit": 1000})))
        assert all(r == results[0] for r in results[1:]), body["mode"]


def test_reads_share_one_snapshot_and_leave_no_transaction(ops_case):
    b = ops_case
    body = {"mode": "operation", "a": {"event": b.event["op1"]}, "b": {"event": b.event["op2"]}}
    conn = open_ro(b.path)
    try:
        V.compare(conn, V.parse_request(body))
        assert not conn.in_transaction
        with pytest.raises(V.CompareError):
            V.compare(conn, V.parse_request({**body, "b": {"event": 999}}))
        assert not conn.in_transaction
        conn.execute("BEGIN")
        V.compare(conn, V.parse_request(body))
        assert conn.in_transaction  # the caller's transaction is left alone
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def test_rows_and_sequences_come_from_the_same_snapshot(ops_case, monkeypatch):
    """A writer that deletes records between the reads of the sides and of the page rows is not seen."""
    b = ops_case
    body = {"mode": "operation", "a": {"event": b.event["op1"]}, "b": {"event": b.event["op2"]}}
    real = V._CACHE.get

    def align_then_write(ta, tb):
        writer = sqlite3.connect(b.path)
        with writer:
            writer.execute("DELETE FROM entry WHERE event_id IS NOT NULL")
        writer.close()
        return real(ta, tb)

    monkeypatch.setattr(V._CACHE, "get", align_then_write)
    out = run(b.path, body)
    assert all((r["a"] is None) == (r["a_pos"] is None) for r in out["rows"])
    assert all((r["b"] is None) == (r["b_pos"] is None) for r in out["rows"])
