"""Repeated records (ROADMAP 9.12): the ``repeat`` filter leaf and ``repeat_marks`` (synthetic rows)."""

from __future__ import annotations

import json
import sys

import pytest

from dmslog.store import query
from dmslog.store.db import connect, create_case_db
from dmslog.store.distinct import distinct
from dmslog.store.filters import INT64_MAX, INT64_MIN, FilterError, compile_bucket_filter, compile_filter
from dmslog.store.repeats import MAX_MARK_IDS, RepeatSpec, parse_spec, predicate_sql, repeat_marks

# stream 1: X.log.1, X.log.0, X.log (one chain) and Y.log (another chain of the same stream)
# stream 2: another process
SOURCES = [
    (1, 1, "Log/X.log.1", "X.log.1", 1),
    (2, 1, "Log/X.log.0", "X.log.0", 0),
    (3, 1, "Log/X.log", "X.log", None),
    (4, 1, "Log/Y.log", "Y.log", None),
    (5, 2, "Log/Z.log", "Z.log", None),
]


def _case(tmp_path, rows):
    """rows: (source_id, ts, tpl_id, fields, msg, cont, tid); ids are given in list order."""
    path = tmp_path / "case.sqlite"
    conn = create_case_db(path)
    with conn:
        conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'Log', 'x')")
        conn.executemany(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
            " VALUES (?,1,'AWFM',?,?,0)",
            [(1, "WfmAdapter", 1), (2, "OntAdapter", 2)],
        )
        conn.executemany(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, rotation, envelope)"
            " VALUES (?,1,?,?,?,?,'adms')",
            SOURCES,
        )
        conn.executemany(
            "INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
            " VALUES (?,?,'AWFM','other',4,'x',1,'v')",
            [(1, "TST-001"), (2, "TST-002")],
        )
        for i, (source_id, ts, tpl, fields, msg, cont, tid) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, flags,"
                " fields, msg) VALUES (?,?,?,?,1,?,?,4,?,?,?,?)",
                (i, source_id, i, i * 10, ts, tid, tpl, 2 if cont is not None else 0,
                 None if fields is None else json.dumps(fields), msg),
            )  # fmt: skip
            if cont is not None:
                conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?,?)", (i, cont))
    conn.close()
    return connect(path, readonly=True)


def _hits(conn, spec=None) -> list[int]:
    cf = compile_filter({"repeat": spec if spec is not None else {}})
    return [r[0] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql} ORDER BY e.id", cf.params)]


def test_the_first_of_a_burst_is_not_a_repeat_and_the_window_looks_back(tmp_path):
    a = {"op": "A"}
    conn = _case(
        tmp_path,
        [
            (3, 1000, 1, a, "m", None, 7),  # 1 first
            (3, 2000, 1, a, "m", None, 7),  # 2 exactly 1000 ms later: repeat
            (3, 3001, 1, a, "m", None, 7),  # 3 1001 ms after 2: not a repeat
            (3, 3001, 1, {"op": "B"}, "m", None, 7),  # 4 other key
            (3, 3002, 2, a, "m", None, 7),  # 5 other template
            (5, 3003, 1, a, "m", None, 7),  # 6 other stream
            (3, None, 1, a, "m", None, 7),  # 7 no time: never
        ],
    )
    assert _hits(conn) == [2]
    assert _hits(conn, {"window_ms": 1001}) == [2, 3]
    assert _hits(conn, {"window_ms": 0}) == []
    marks = repeat_marks(conn, list(range(1, 9)), RepeatSpec())  # 8 does not exist
    assert marks == {2: {"prev": 1, "dt_ms": 1000, "earlier": 1}}


def test_the_record_after_a_step_back_repeats_the_later_one(tmp_path):
    conn = _case(
        tmp_path,
        [
            (3, 5000, 1, None, "same", None, 7),  # 1
            (3, 4990, 1, None, "same", None, 7),  # 2 earlier ts (clock went back): 1 repeats 2
        ],
    )
    assert _hits(conn) == [1]
    assert repeat_marks(conn, [1, 2], RepeatSpec()) == {1: {"prev": 2, "dt_ms": 10, "earlier": 1}}


def test_ties_follow_the_file_then_the_rotation_then_the_names(tmp_path):
    conn = _case(
        tmp_path,
        [
            (3, 100, 1, None, "t", None, 7),  # 1 X.log
            (3, 100, 1, None, "t", None, 7),  # 2 X.log, later in the file: repeat of 1
            (2, 100, 1, None, "t", None, 7),  # 3 X.log.0: before every X.log record
            (1, 100, 1, None, "t", None, 7),  # 4 X.log.1: the oldest
            (4, 100, 1, None, "t", None, 7),  # 5 Y.log: current file, name after X.log
        ],
    )
    assert _hits(conn) == [1, 2, 3, 5]
    marks = repeat_marks(conn, [1, 2, 3, 4, 5], RepeatSpec())
    assert marks[3] == {"prev": 4, "dt_ms": 0, "earlier": 1}
    assert marks[1] == {"prev": 3, "dt_ms": 0, "earlier": 2}
    assert marks[2] == {"prev": 1, "dt_ms": 0, "earlier": 3}
    assert marks[5] == {"prev": 2, "dt_ms": 0, "earlier": 4}


def test_the_default_key_is_the_fields_or_else_the_whole_text(tmp_path):
    conn = _case(
        tmp_path,
        [
            (3, 10, 1, {"op": "A", "n": 1}, "text one", None, 7),  # 1
            (3, 20, 1, {"op": "A", "n": 1}, "text two", "x", 7),  # 2 same fields, other text: repeat
            (3, 30, 0, None, "unknown", "body 1", 7),  # 3
            (3, 40, 0, None, "unknown", "body 2", 7),  # 4 same first line, other body
            (3, 50, 0, None, "unknown", "body 1", 7),  # 5 same as 3: repeat
            (3, 60, 0, None, "unknown", None, 7),  # 6 no body
            (3, 70, 0, None, "unknown", None, 9),  # 7 same as 6 (other thread): repeat
            (3, 80, 1, None, "no fields", None, 7),  # 8 known template without fields: by text
            (3, 90, 1, None, "no fields", None, 7),  # 9 repeat
        ],
    )
    assert _hits(conn) == [2, 5, 7, 9]
    assert _hits(conn, {"tid": True}) == [2, 5, 9]
    assert repeat_marks(conn, [5], RepeatSpec())[5] == {"prev": 3, "dt_ms": 20, "earlier": 1}


def test_key_fields(tmp_path):
    conn = _case(
        tmp_path,
        [
            (3, 10, 1, {"op": "A", "n": 1}, "a", None, 7),  # 1
            (3, 20, 1, {"op": "A", "n": 2}, "b", None, 7),  # 2 same op
            (3, 30, 1, {"op": 5}, "c", None, 7),  # 3 op as a number
            (3, 40, 1, {"op": "5"}, "d", None, 7),  # 4 op as text: not equal to 5
            (3, 50, 1, {"n": 2}, "e", None, 7),  # 5 no op
            (3, 60, 1, None, "f", None, 7),  # 6 no fields at all: also no op -> repeat of 5
            (3, 70, 0, None, "u", None, 7),  # 7 unknown: by text even with key fields
            (3, 80, 0, None, "v", None, 7),  # 8 other text
        ],
    )
    assert _hits(conn, {"fields": ["op"]}) == [2, 6]
    assert _hits(conn, {"fields": ["op", "n"]}) == []
    assert _hits(conn, {"fields": ["n"]}) == [4, 5, 6]  # 4 and 6 have no n, like 3; 5 has the n of 2
    assert _hits(conn, {"fields": ["missing"], "window_ms": 100}) == [2, 3, 4, 5, 6]


def test_extreme_times_do_not_overflow(tmp_path):
    conn = _case(
        tmp_path,
        [
            (3, INT64_MIN, 1, None, "m", None, 7),
            (3, INT64_MIN + 5, 1, None, "m", None, 7),
            (3, INT64_MAX - 5, 1, None, "m", None, 7),
            (3, INT64_MAX, 1, None, "m", None, 7),
        ],
    )
    for window in (0, 10, 3_600_000):
        hits = _hits(conn, {"window_ms": window})
        assert hits == ([] if window == 0 else [2, 4])
    marks = repeat_marks(conn, [1, 2, 3, 4], RepeatSpec(window_ms=3_600_000))
    assert marks == {2: {"prev": 1, "dt_ms": 5, "earlier": 1}, 4: {"prev": 3, "dt_ms": 5, "earlier": 1}}


def test_not_is_the_complement_and_other_modes(tmp_path):
    rows = [(3, k * 100 + (k % 7) * 90, 1 + k % 2, None, f"m{k % 3}", None, 7) for k in range(30)]
    conn = _case(tmp_path, rows)
    hits = set(_hits(conn))
    cf = compile_filter({"not": {"repeat": {}}})
    rest = {r[0] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}
    assert hits and rest and hits | rest == set(range(1, 31)) and not hits & rest
    # rowwise compiles the same, and the bucket table cannot express it
    assert compile_filter({"repeat": {}}, rowwise=True).sql == compile_filter({"repeat": {}}).sql
    assert compile_bucket_filter({"repeat": {}}) is None
    assert compile_bucket_filter({"and": [{"source": 3}, {"repeat": {}}]}) is None
    # page, count and distinct accept it
    page = query.page(conn, {"repeat": {}}, None)
    assert [r["id"] for r in page.rows] == sorted(hits, key=lambda i: (rows[i - 1][1], i))
    assert query.count(conn, {"repeat": {}}).n == len(hits)
    result = distinct(conn, {"repeat": {}}, "tpl")
    assert result.total == len(hits)
    assert set(repeat_marks(conn, list(range(1, 31)), RepeatSpec())) == hits


@pytest.mark.parametrize(
    ("arg", "code", "path"),
    [
        ([], "bad_type", "$.repeat"),
        ({"window": 1}, "unknown_operator", "$.repeat"),
        ({"window_ms": -1}, "bad_value", "$.repeat.window_ms"),
        ({"window_ms": 3_600_001}, "bad_value", "$.repeat.window_ms"),
        ({"window_ms": True}, "bad_type", "$.repeat.window_ms"),
        ({"window_ms": "5"}, "bad_type", "$.repeat.window_ms"),
        ({"fields": "op"}, "bad_type", "$.repeat.fields"),
        ({"fields": []}, "empty_list", "$.repeat.fields"),
        ({"fields": ["a"] * 17}, "list_too_long", "$.repeat.fields"),
        ({"fields": ["a b"]}, "bad_value", "$.repeat.fields[0]"),
        ({"fields": [1]}, "bad_type", "$.repeat.fields[0]"),
        ({"tid": 1}, "bad_type", "$.repeat.tid"),
    ],
)
def test_invalid_specs(arg, code, path):
    with pytest.raises(FilterError) as info:
        compile_filter({"repeat": arg})
    assert (info.value.code, info.value.path) == (code, path)


def test_spec_defaults_and_parameters_only():
    assert parse_spec({}) == RepeatSpec(1000, (), False)
    assert parse_spec(None) == RepeatSpec()
    spec = parse_spec({"fields": ["op", "op", "n"], "tid": True, "window_ms": 0})
    assert spec == RepeatSpec(0, ("op", "n"), True)
    sql, params = predicate_sql(parse_spec({"fields": ["op"]}))
    assert "op" not in sql and "$.op" in params  # the field name is a parameter, never SQL text


def test_mark_limits(tmp_path):
    conn = _case(tmp_path, [(3, 1, 1, None, "m", None, 7)])
    assert repeat_marks(conn, [], RepeatSpec()) == {}
    assert repeat_marks(conn, list(range(MAX_MARK_IDS)), RepeatSpec()) == {}
    with pytest.raises(ValueError):
        repeat_marks(conn, list(range(MAX_MARK_IDS + 1)), RepeatSpec())


def test_the_plan_walks_the_template_index(tmp_path):
    conn = _case(tmp_path, [(3, 1, 1, None, "m", None, 7)])
    sql, params = predicate_sql(RepeatSpec())
    plan = " | ".join(
        r[3] for r in conn.execute(f"EXPLAIN QUERY PLAN SELECT e.id FROM entry e WHERE {sql}", params)
    )
    assert "SEARCH rp USING INDEX ix_entry_tpl_ts (tpl_id=? AND ts>? AND ts<?)" in plan
    assert "SEARCH rse USING INTEGER PRIMARY KEY" in plan and "SEARCH rsp USING INTEGER PRIMARY KEY" in plan


# --------------------------------------------------------------------------- review of PR #200


def _two_halves(tmp_path, enterprise_first: bool, ts=(100, 100)):
    """The same member in the same stream from the two halves of one ERS, added in either order."""
    path = tmp_path / ("ent" if enterprise_first else "sen") / "case.sqlite"
    conn = create_case_db(path)
    halves = [("enterprise", "/cases/ERS_Enterprise.zip"), ("sensitive", "/cases/ERS_Sensitive.zip")]
    if not enterprise_first:
        halves.reverse()
    with conn:
        for cid, (variant, zpath) in enumerate(halves, 1):
            conn.execute(
                "INSERT INTO collection(id, kind, path, ers_variant, ers_group, added_at)"
                " VALUES (?, 'ers_zip', ?, ?, 1, 'x')",
                (cid, zpath, variant),
            )
        conn.execute(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
            " VALUES (1, 1, 'AWFM', 'WfmAdapter', 1, 0)"
        )
        conn.execute("INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
                     " VALUES (1, 'TST-001', 'AWFM', 'other', 4, 'x', 1, 'v')")  # fmt: skip
        for cid in (1, 2):
            conn.execute(
                "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope)"
                " VALUES (?, ?, 1, 'Log/W.log', 'W.log', 'adms')",
                (cid, cid),
            )
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, flags,"
                " msg)"
                " VALUES (?, ?, 1, 0, 1, ?, 7, 4, 1, 0, 'same')",
                (cid, cid, ts[cid - 1] if enterprise_first else ts[2 - cid]),
            )
    conn.close()
    ro = connect(path, readonly=True)
    variant = dict(
        ro.execute("SELECT s.id, c.ers_variant FROM source s JOIN collection c ON c.id = s.collection_id")
    )
    return ro, variant


@pytest.mark.parametrize("ts", [(100, 100), (100, 150)])
def test_ties_between_the_halves_of_an_ers_do_not_depend_on_the_order_they_were_added(tmp_path, ts):
    """ts = (Enterprise, Sensitive). Tied: the Enterprise record is first either way."""
    results = []
    for enterprise_first in (True, False):
        conn, variant = _two_halves(tmp_path, enterprise_first, ts)
        marks = repeat_marks(conn, [1, 2], RepeatSpec())
        results.append({variant[e]: (variant[m["prev"]], m["dt_ms"]) for e, m in marks.items()})
        assert {variant[i] for i in _hits(conn)} == set(results[-1])
        conn.close()
    assert results[0] == results[1] == {"sensitive": ("enterprise", ts[1] - ts[0])}


def test_the_closest_earlier_record_is_chosen_by_origin_not_by_id(tmp_path):
    for enterprise_first in (True, False):
        conn, variant = _two_halves(tmp_path, enterprise_first)
        # a third record, later in the Sensitive file, at the same time: it repeats both, the closest is
        # the Sensitive one (same source, earlier line), whatever the ids
        sensitive = next(s for s, v in variant.items() if v == "sensitive")
        conn.close()
        rw = connect(tmp_path / ("ent" if enterprise_first else "sen") / "case.sqlite")
        with rw:
            rw.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, flags,"
                " msg)"
                " VALUES (3, ?, 2, 10, 1, 100, 7, 4, 1, 0, 'same')",
                (sensitive,),
            )
        rw.close()
        conn = connect(tmp_path / ("ent" if enterprise_first else "sen") / "case.sqlite", readonly=True)
        marks = repeat_marks(conn, [3], RepeatSpec())
        assert marks == {3: {"prev": sensitive, "dt_ms": 0, "earlier": 2}}
        conn.close()


def test_field_values_are_compared_canonically(tmp_path):
    rows = [
        (3, 10, 1, {"kv": {"Identifier": "C1", "Availability": "A"}, "n": [1, 2]}, "a", None, 7),  # 1
        (3, 20, 1, {"kv": {"Availability": "A", "Identifier": "C1"}, "n": [1, 2]}, "b", None, 7),  # 2 = 1
        (3, 30, 1, {"n": [1, 2], "kv": {"Identifier": "C1", "Availability": "A"}}, "c", None, 7),  # 3 = 1
        (3, 40, 2, {"kv": {"Identifier": "C1", "Availability": "A"}, "n": [2, 1]}, "d", None, 7),  # 4
        (3, 50, 2, {"kv": {"Availability": "A", "Identifier": "C1"}, "n": [1, 2]}, "e", None, 7),  # 5 != 4
        (3, 60, 2, {"kv": {"Availability": "B", "Identifier": "C1"}, "n": [2, 1]}, "f", None, 7),  # 6
        (3, 70, 2, {"kv": '{"Availability":"B","Identifier":"C1"}'}, "g", None, 7),  # 7 text, not object
    ]
    conn = _case(tmp_path, rows)
    assert _hits(conn) == [2, 3]
    assert _hits(conn, {"fields": ["kv"]}) == [2, 3, 5]
    assert _hits(conn, {"fields": ["n"]}) == [2, 3, 6]
    assert _hits(conn, {"fields": ["kv", "n"]}) == [2, 3]
    assert repeat_marks(conn, [2, 5], RepeatSpec(fields=("kv",))) == {
        2: {"prev": 1, "dt_ms": 10, "earlier": 1},
        5: {"prev": 4, "dt_ms": 10, "earlier": 1},
    }


def test_canonical_json_function():
    from dmslog.store.filters import json_canon

    assert (
        json_canon('{"b":{"y":1,"x":[2,{"q":1,"p":2}]},"a":"\u00e9"}')
        == '{"a":"\u00e9","b":{"x":[2,{"p":2,"q":1}],"y":1}}'
    )
    assert json_canon("not json") is None and json_canon(None) is None and json_canon(5) is None
    assert json_canon("[" * 100_000) is None  # too deep: no crash


def test_real_parser_reorders_continuations_wfm_s010(tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    from dmslog.ingest.pipeline import run_ingest

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("make_fixture", root / "tools" / "make_fixture.py")
    mf = sys.modules.get("make_fixture") or importlib.util.module_from_spec(spec)
    if "make_fixture" not in sys.modules:
        sys.modules["make_fixture"] = mf
        spec.loader.exec_module(mf)
    folder = tmp_path / "Log" / "dnaApp_zone1" / "SVC"
    folder.mkdir(parents=True)
    head = 'Changed crews request received. Request with ID "00000000-0000-4000-8000-000000000001" content:'
    lines = [
        mf.header(0, "DEBUGLOG", head, pid=4444, tid=7), "Identifier: CREW0001", "Availability: Available",
        mf.header(100, "DEBUGLOG", head, pid=4444, tid=7), "Availability: Available", "Identifier: CREW0001",
    ]  # fmt: skip
    (folder / "WfmAdapter_4444.sensitive.log").write_bytes(mf.render(lines))
    (folder / "WfmAdapter_4444.log").write_bytes(mf.render([mf.header(0, "INFO", "x", pid=4444)]))
    path = tmp_path / "case.sqlite"
    conn = create_case_db(path)
    run_ingest(conn, tmp_path / "Log")
    conn.close()
    ro = connect(path, readonly=True)
    ids = [r[0] for r in ro.execute("SELECT e.id FROM entry e JOIN template t ON t.id = e.tpl_id"
                                    " WHERE t.code = 'WFM-S010' ORDER BY e.id")]  # fmt: skip
    first, second = ids
    raw = [r[0] for r in ro.execute("SELECT fields FROM entry WHERE id IN (?, ?) ORDER BY id", ids)]
    assert raw[0] != raw[1] and json.loads(raw[0]) == json.loads(raw[1])  # the case the review found
    for spec_ in (RepeatSpec(), RepeatSpec(fields=("kv",))):
        assert repeat_marks(ro, ids, spec_) == {second: {"prev": first, "dt_ms": 100, "earlier": 1}}


def test_field_types_and_null(tmp_path):
    rows = [
        (3, 10, 1, {"v": True}, "a", None, 7),  # 1
        (3, 20, 1, {"v": 1}, "b", None, 7),  # 2 integer, not true
        (3, 30, 1, {"v": 1.0}, "c", None, 7),  # 3 real, not integer
        (3, 40, 1, {"v": None}, "d", None, 7),  # 4 null
        (3, 50, 1, {"w": 1}, "e", None, 7),  # 5 missing = null
    ]
    conn = _case(tmp_path, rows)
    assert _hits(conn, {"fields": ["v"]}) == [5]


def _guid_rows(n: int, *, step_ms: int, nested: bool = True, same_ts: bool = False):
    import uuid

    rows = []
    for i in range(n):
        fields = {"request_id": str(uuid.UUID(int=i * 7919 + 1)), "op": "Changed crews"}
        if nested:
            fields["kv"] = {"Identifier": "CREW0001", "Availability": "Available"}
        rows.append((3, 0 if same_ts else i * step_ms, 1, fields, "m", None, 7))
    return rows


@pytest.mark.parametrize("same_ts", [False, True])
def test_each_distinct_field_text_is_parsed_once_however_many_pairs(tmp_path, same_ts):
    """Review of PR #200: distinct GUIDs have the same length, so every pair reached the canonical form,
    parsed twice per pair (QueryTimeout with a one hour window). Counted, not timed."""
    from dmslog.store.filters import CANON_CACHE

    n = 300
    conn = _case(tmp_path, _guid_rows(n, step_ms=1000, same_ts=same_ts))
    CANON_CACHE.reset()
    flt = {"and": [{"source": 3}, {"repeat": {"window_ms": 3_600_000}}]}
    assert query.count(conn, flt).n == 0
    assert query.page(conn, flt, None).rows == []
    assert CANON_CACHE.parses <= n  # once per distinct text, not per pair
    assert CANON_CACHE.calls >= n * (n - 1) // 2  # every pair was compared (the case the review found)
    CANON_CACHE.reset()
    assert _hits(conn, {"fields": ["kv"], "window_ms": 3_600_000}) == list(range(2, n + 1))
    assert CANON_CACHE.parses == 0  # equal texts never reach the function
    marks = repeat_marks(conn, list(range(1, n + 1)), RepeatSpec(window_ms=3_600_000))
    assert marks == {} and CANON_CACHE.parses <= n


def test_the_cache_bound_keeps_the_answers(tmp_path, monkeypatch):
    from dmslog.store import filters

    rows = _guid_rows(40, step_ms=10)
    rows.append(
        (
            3,
            400,
            1,
            {**rows[0][3], "kv": {"Availability": "Available", "Identifier": "CREW0001"}},
            "x",
            None,
            7,
        )
    )
    conn = _case(tmp_path, rows)
    filters.CANON_CACHE.reset()
    monkeypatch.setattr(filters, "CANON_CACHE_CHARS", 500)  # a couple of texts: emptied all the time
    assert _hits(conn) == [41]
    assert repeat_marks(conn, [41], RepeatSpec()) == {41: {"prev": 1, "dt_ms": 400, "earlier": 1}}
    assert filters.CANON_CACHE.chars <= 500


def test_a_text_larger_than_the_cache_is_compared_but_never_kept(monkeypatch):
    """Review of PR #200: the cache emptied itself and kept the oversized entry anyway."""
    from dmslog.store import filters

    cache = filters._CanonCache()
    monkeypatch.setattr(filters, "CANON_CACHE_CHARS", 200)
    big_a = json.dumps({"a": "x" * 60, "b": 1}, separators=(",", ":"))
    big_b = json.dumps({"b": 1, "a": "x" * 60}, separators=(",", ":"))
    small = '{"k":1}'
    assert cache.same(small, small) == 1 and cache.chars == filters._entry_size(small)
    assert cache.same(big_a, big_b) == 1  # still compared canonically
    assert cache.same(big_a, '{"a":"y","b":1}') == 0
    assert big_a not in cache.forms and big_b not in cache.forms
    assert set(cache.forms) == {small, '{"a":"y","b":1}'}  # the small ones only
    assert cache.chars == sum(filters._entry_size(t) for t in cache.forms) <= 200


def test_concurrent_insertions_keep_the_limit(monkeypatch):
    """Review of PR #200: check, empty and insert were not one step; two threads could pass the limit."""
    import random
    import threading

    from dmslog.store import filters

    cache = filters._CanonCache()
    limit = 400  # a handful of entries: emptied all the time
    monkeypatch.setattr(filters, "CANON_CACHE_CHARS", limit)
    texts = [json.dumps({"n": i, "pad": "p" * (i % 17)}, separators=(",", ":")) for i in range(400)]
    canon = {t: filters.json_canon(t) for t in texts}
    seen_over: list[int] = []
    errors: list[str] = []
    start = threading.Barrier(8)

    def work(seed: int) -> None:
        rnd = random.Random(seed)
        start.wait()
        for _ in range(3000):
            t = rnd.choice(texts)
            if cache.form(t) != canon[t]:
                errors.append(t)
            chars = cache.chars
            if chars > limit:
                seen_over.append(chars)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    try:
        threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        sys.setswitchinterval(old)
    assert not errors and not seen_over
    with cache._lock:
        assert cache.chars == sum(filters._entry_size(t) for t in cache.forms) <= limit
        assert all(cache.forms[t] == canon[t] for t in cache.forms)
