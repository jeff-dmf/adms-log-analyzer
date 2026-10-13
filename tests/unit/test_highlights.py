"""Highlight rules of a case (store/highlights.py), the text.regex leaf and the rowwise compile mode."""

from __future__ import annotations

import sqlite3
import time

import pytest
from query_helpers import build_case, open_ro, order_key, py_match

from dmslog.store import db, filters, query
from dmslog.store import highlights as H
from dmslog.store.filters import FilterError, compile_filter


@pytest.fixture
def case_path(tmp_path):
    path = tmp_path / "case" / "index.sqlite"
    rows = build_case(path)
    return path, rows


@pytest.fixture
def rw(case_path):
    conn = db.connect(case_path[0])
    yield conn
    conn.close()


@pytest.fixture
def ro(case_path):
    conn = open_ro(case_path[0])
    yield conn
    conn.close()


# --------------------------------------------------------------------------- storage


def test_create_list_update_delete_and_order(rw):
    a = H.create_rule(rw, "  timeouts\t", "red", {"text": {"fts": "timeout"}})
    b = H.create_rule(rw, "gid", "blue", {"key": {"type": "gid", "eq": "g-9"}}, enabled=False)
    assert a["name"] == "timeouts" and a["pos"] == 0 and a["enabled"] is True
    assert b["pos"] == 1 and b["enabled"] is False and b["cond"] == {"key": {"type": "gid", "eq": "g-9"}}
    assert [r["id"] for r in H.list_rules(rw)] == [a["id"], b["id"]]
    changed = H.update_rule(rw, b["id"], color="green", enabled=True, name="the gid")
    assert (changed["color"], changed["enabled"], changed["name"], changed["pos"]) == (
        "green",
        True,
        "the gid",
        1,
    )
    assert H.update_rule(rw, a["id"])["name"] == "timeouts"  # nothing to change: still found
    assert [r["id"] for r in H.reorder(rw, [b["id"], a["id"]])] == [b["id"], a["id"]]
    H.delete_rule(rw, b["id"])
    (left,) = H.list_rules(rw)
    assert left["id"] == a["id"] and left["pos"] == 0  # renumbered
    with pytest.raises(H.HighlightError) as err:
        H.delete_rule(rw, b["id"])
    assert err.value.code == "not_found"
    with pytest.raises(H.HighlightError):
        H.update_rule(rw, 999, color="red")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"name": "", "color": "red", "cond": {"sev": 1}}, "bad_name"),
        ({"name": "x" * 81, "color": "red", "cond": {"sev": 1}}, "bad_name"),
        ({"name": "x", "color": "#ff0000", "cond": {"sev": 1}}, "bad_color"),
        ({"name": "x", "color": "red", "cond": {}}, "bad_cond"),
        ({"name": "x", "color": "red", "cond": [1]}, "bad_cond"),
        ({"name": "x", "color": "red", "cond": {"sev": 1}, "enabled": 1}, "bad_enabled"),
    ],
)
def test_create_validates_everything_before_writing(rw, kwargs, code):
    with pytest.raises(H.HighlightError) as err:
        H.create_rule(rw, **kwargs)
    assert err.value.code == code
    assert H.list_rules(rw) == []


def test_invalid_condition_is_a_filter_error_with_a_path(rw):
    with pytest.raises(FilterError) as err:
        H.create_rule(rw, "x", "red", {"and": [{"sev": "nope"}]})
    assert err.value.code == "bad_value" and err.value.path == "$.and[0].sev[0]"


def test_rule_limit_and_reorder_validation(rw):
    ids = [H.create_rule(rw, f"r{k}", "yellow", {"sev": 1})["id"] for k in range(H.MAX_RULES)]
    with pytest.raises(H.HighlightError) as err:
        H.create_rule(rw, "one more", "red", {"sev": 2})
    assert err.value.code == "too_many_rules"
    with pytest.raises(H.HighlightError) as err:
        H.highlight_key(rw, "gid", "g-9")
    assert err.value.code == "too_many_rules"
    for bad in (ids[:-1], [*ids[:-1], ids[0]], [*ids, 999], "1,2", [True] * len(ids)):
        with pytest.raises(H.HighlightError) as err:
            H.reorder(rw, bad)
        assert err.value.code == "bad_order"
    assert [r["id"] for r in H.reorder(rw, ids[::-1])] == ids[::-1]


def test_highlight_key_creates_then_recolors_the_same_rule(rw):
    rule, created = H.highlight_key(rw, "msgid", "m-1")
    assert created and rule["color"] == H.DEFAULT_COLOR and rule["name"] == "msgid = m-1"
    assert rule["cond"] == {"key": {"type": "msgid", "eq": "m-1"}}
    H.update_rule(rw, rule["id"], enabled=False)
    again, created = H.highlight_key(rw, "msgid", "m-1", "purple")
    assert not created and again["id"] == rule["id"] and again["color"] == "purple" and again["enabled"]
    assert len(H.list_rules(rw)) == 1
    for bad in (("", "x"), ("gid", ""), (None, "x"), ("gid", 5)):
        with pytest.raises(H.HighlightError) as err:
            H.highlight_key(rw, *bad)
        assert err.value.code == "bad_key"


def test_highlight_key_matches_the_condition_whatever_the_property_order(rw):
    original = H.create_rule(rw, "mine", "red", {"key": {"eq": "g-9", "type": "gid"}})
    rule, created = H.highlight_key(rw, "gid", "g-9", "blue")
    assert not created and rule["id"] == original["id"] and rule["color"] == "blue"
    assert len(H.list_rules(rw)) == 1


def test_compile_conditions_applies_the_size_limit_with_the_index(rw):
    huge = {"text": {"fts": "x" * (H.MAX_JSON_BYTES + 1)}}
    with pytest.raises(H.FilterError) as err:
        H.compile_conditions([{"sev": 1}, huge], rowwise=True)
    assert err.value.code == "too_large" and err.value.path == "$.rules[1]"


def test_rules_store_only_what_was_typed(rw, case_path):
    _, rows = case_path
    H.highlight_key(rw, "gid", "g-9")
    H.create_rule(rw, "mine", "cyan", {"text": {"substr": "timeout"}})
    stored = rw.execute("SELECT name, color, cond FROM highlight_rule ORDER BY pos").fetchall()
    assert stored == [
        ("gid = g-9", "yellow", '{"key":{"type":"gid","eq":"g-9"}}'),
        ("mine", "cyan", '{"text":{"substr":"timeout"}}'),
    ]
    messages = {r["msg"] for r in rows}
    assert not any(m in text for (n, _, c) in stored for text in (n, c) for m in messages if len(m) > 12)


def test_a_case_from_schema_5_lists_no_rules_and_migrates_on_first_write(tmp_path):
    path = tmp_path / "old.sqlite"
    build_case(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE highlight_rule")
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()
    ro = open_ro(path)
    try:
        assert H.list_rules(ro) == [] and not H.has_table(ro)
        assert H.mark(ro, [{"sev": 1}], [1, 2, 3]) is not None  # evaluation never needs the table
        with pytest.raises(H.HighlightError):
            H.get_rule(ro, 1)
    finally:
        ro.close()
    rw = db.connect(path)
    try:
        made = H.create_rule(rw, "x", "red", {"sev": 1})
        assert db.get_user_version(rw) == db.SCHEMA_VERSION and H.list_rules(rw) == [made]
        before = rw.execute("SELECT sql FROM sqlite_master WHERE name = 'highlight_rule'").fetchone()
        db.MIGRATIONS[6](rw)  # idempotent (CREATE TABLE IF NOT EXISTS)
        assert rw.execute("SELECT sql FROM sqlite_master WHERE name = 'highlight_rule'").fetchone() == before
    finally:
        rw.close()


def test_failed_write_rolls_back(rw, monkeypatch):
    H.create_rule(rw, "a", "red", {"sev": 1})

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(H, "_renumber", boom)
    with pytest.raises(RuntimeError):
        H.delete_rule(rw, H.list_rules(rw)[0]["id"])
    assert len(H.list_rules(rw)) == 1 and not rw.in_transaction


# --------------------------------------------------------------------------- evaluation


RULES = [
    {"key": {"type": "msgid", "eq": "m-1"}},
    {"text": {"fts": "timeout"}},
    {"sev": {"le": 1}},
    {"text": {"substr": "Frame"}},
]


def _expected_first(rules, rows, fts_ids):
    out = {}
    for row in rows:
        for k, rule in enumerate(rules):
            hit = row["id"] in fts_ids if "text" in rule and "fts" in rule["text"] else py_match(rule, row)
            if hit:
                out[row["id"]] = k
                break
    return out


def test_mark_gives_the_first_matching_rule(ro, case_path):
    _, rows = case_path
    cf = compile_filter({"text": {"fts": "timeout"}})  # the set form (templates with fts = false are out)
    fts_ids = {r[0] for r in ro.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}
    assert fts_ids
    ids = [r["id"] for r in rows]
    got = {}
    for start in range(0, len(ids), H.MAX_MARK_IDS):
        got.update(H.mark(ro, RULES, ids[start : start + H.MAX_MARK_IDS]))
    assert got == _expected_first(RULES, rows, fts_ids)
    assert got[30] == 0 and got[140] == 0  # the key wins over the others (priority)
    assert H.mark(ro, [], ids[:5]) == {} and H.mark(ro, RULES, []) == {}


@pytest.mark.parametrize(
    ("rules", "ids", "code"),
    [
        ([{"sev": 1}] * (H.MAX_RULES + 1), [1], "bad_rules"),
        ({"sev": 1}, [1], "bad_rules"),
        ([{}], [1], "bad_rules"),
        ([{"sev": 1}], list(range(H.MAX_MARK_IDS + 1)), "bad_ids"),
        ([{"sev": 1}], [1, "2"], "bad_ids"),
        ([{"sev": 1}], [True], "bad_ids"),
        ([{"sev": 1}], [-1], "bad_ids"),
        ([{"sev": 1}], "1,2", "bad_ids"),
    ],
)
def test_mark_limits(ro, rules, ids, code):
    with pytest.raises(H.HighlightError) as err:
        H.mark(ro, rules, ids)
    assert err.value.code == code


def test_mark_reports_the_rule_index_in_filter_errors(ro):
    with pytest.raises(FilterError) as err:
        H.mark(ro, [{"sev": 1}, {"text": {"fts": "*"}}], [1])
    assert err.value.path == "$.rules[1].text.fts"


def test_mark_with_many_values_is_refused(ro):
    rules = [{"tid": list(range(1000))}] * 5
    with pytest.raises(H.HighlightError) as err:
        H.mark(ro, rules, [1])
    assert err.value.code == "too_many_params"


def _reference_next(rows, view, rules, cursor_row, direction):
    ordered = sorted(rows, key=order_key)
    if cursor_row is None:
        candidates = ordered if direction == "fwd" else ordered[::-1]
    else:
        key = order_key(cursor_row)
        if direction == "fwd":
            candidates = [r for r in ordered if order_key(r) > key]
        else:
            candidates = [r for r in ordered[::-1] if order_key(r) < key]
    for row in candidates:
        if py_match(view, row) and any(py_match(rule, row) for rule in rules):
            return row
    return None


def test_next_match_walks_by_keyset_inside_the_view(ro, case_path):
    _, rows = case_path
    view = {"source": [1, 2]}
    rules = [{"key": {"type": "msgid", "eq": "m-1"}}, {"sev": {"le": 1}}]
    by_id = {r["id"]: r for r in rows}
    for direction in ("fwd", "back"):
        cursor = None
        seen = []
        for _ in range(200):
            found = H.next_match(ro, view, rules, cursor, direction)
            ref = _reference_next(rows, view, rules, by_id.get(seen[-1]) if seen else None, direction)
            assert (found.row["id"] if found.row else None) == (ref["id"] if ref else None)
            if found.row is None:
                assert found.rule is None
                break
            row = by_id[found.row["id"]]
            assert found.rule == next(k for k, r in enumerate(rules) if py_match(r, row))
            seen.append(row["id"])
            cursor = query.Cursor(found.row["ts"], found.row["id"])
        assert seen and len(seen) == len({*seen})
    assert H.next_match(ro, view, [], None).row is None


def test_next_match_respects_the_timeout(ro):
    with pytest.raises(query.QueryTimeout):
        H.next_match(ro, None, [{"text": {"regex": "no such text"}}], None, timeout_s=0)


# --------------------------------------------------------------------------- regex leaf and rowwise mode


def test_regex_leaf_searches_the_first_line_window(ro, case_path):
    _, rows = case_path
    cf = compile_filter({"text": {"regex": r"message 1-\d+ value=9"}})
    assert cf.uses_regex and cf.sql == "(dmslog_regexp(?, e.msg) = 1)"
    with query.read_guard(ro, 5.0):
        got = {r[0] for r in ro.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}
    import re

    assert got == {r["id"] for r in rows if re.search(r"message 1-\d+ value=9", r["msg"][:512])}
    assert got
    assert filters.regexp("b$", "a" * 600 + "b") == 0  # past the window
    assert filters.regexp("^a{3}", "a" * 600 + "b") == 1
    assert filters.regexp("x", None) == 0


@pytest.mark.parametrize(
    "pattern",
    ["(a)", "(?i)x", "a.*b.*c", "Timeout.*GID \\d+", "[", "(?P<x>a)\\1", "x" * 257, ""],
)
def test_regex_leaf_rejects_unsafe_or_invalid_patterns(pattern):
    with pytest.raises(FilterError) as err:
        compile_filter({"text": {"regex": pattern}})
    assert err.value.code in {"bad_regex", "string_too_long", "bad_value"}
    assert err.value.path == "$.text.regex"


@pytest.mark.parametrize("pattern", ["GID: \\d+", "^Message with MessageID", "[^ ]+ failed", "\\w+Exception"])
def test_regex_leaf_accepts_linear_patterns(pattern):
    assert compile_filter({"text": {"regex": pattern}}).params == (pattern,)


def test_regex_function_stops_at_the_query_deadline(ro):
    previous = filters.set_regex_deadline(time.monotonic() - 1)
    try:
        with pytest.raises(filters.RegexDeadline):
            filters.regexp("a", "a")
    finally:
        filters.set_regex_deadline(previous)
    cf = compile_filter({"text": {"regex": "value"}})
    with pytest.raises(query.QueryTimeout):
        with query.read_guard(ro, 0):
            ro.execute(f"SELECT count(*) FROM entry e WHERE {cf.sql}", cf.params).fetchone()
    assert getattr(filters._deadline, "at", None) is None  # restored


def test_regex_needs_a_scope_in_the_grid_like_substr():
    from dmslog.server.api import ApiError, _check_scan_filter

    with pytest.raises(ApiError) as err:
        _check_scan_filter({"text": {"regex": "x"}})
    assert err.value.code == "substr_needs_scope"
    _check_scan_filter({"and": [{"source": [1]}, {"text": {"regex": "x"}}]})


@pytest.mark.parametrize(
    "tree",
    [
        {"text": {"fts": "timeout"}},
        {"key": {"type": "msgid"}},
        {"key": {"type": "msgid", "in": ["m-1", "z"]}},
        {"payload": {"role": "response"}},
        {"payload": {}},
        {"event_status": ["fail"]},
        {"not": {"key": {"type": "gid", "eq": "g-9"}}},
        {"or": [{"text": {"fts": "crew"}}, {"event_status": "ok"}]},
    ],
)
def test_rowwise_mode_has_the_same_result(ro, tree):
    a, b = compile_filter(tree), compile_filter(tree, rowwise=True)
    assert b.params == a.params and (b.sql != a.sql or "EXISTS" in a.sql)

    def ids(cf):
        return {r[0] for r in ro.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}

    assert ids(a) == ids(b)
