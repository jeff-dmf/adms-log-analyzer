"""Repeated records (ROADMAP 9.12): the ``repeat`` filter leaf and ``repeat_marks`` (synthetic rows)."""

from __future__ import annotations

import json

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
