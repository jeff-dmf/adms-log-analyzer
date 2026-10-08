"""Filter tree -> parameterized SQL (ARQUITETURA 10): validation, semantics and injection."""

import json
import sqlite3

import pytest
from query_helpers import INJECTION_MSG, build_case, open_ro, py_match

from dmslog.store import filters as F
from dmslog.store import query as Q
from dmslog.store.db import create_case_db

SECTION_10_EXAMPLE = {
    "and": [
        {"source": [3, 4]},
        {"sev": {"le": 2}},
        {"tpl": ["WFM-081", "GEN-EXC-01"]},
        {"ts": {"ge": 1791374400000, "lt": 1791378000000}},
        {"tid": 17604},
        {"text": {"fts": "timeout"}},
        {"key": {"type": "request_id", "eq": "a0b0"}},
        {"not": {"tpl": ["GEN-STA-01", "GEN-STA-02", "GEN-STA-03"]}},
        {"or": [{"event_status": "fail"}, {"flags": "partial"}]},
        {"payload": {"role": "response", "operation": "SendSwitchingSteps"}},
    ]
}

INJECTION_STRINGS = [
    "' OR 1=1 --",
    '"); DROP TABLE entry; --',
    "Robert'); DROP TABLE entry;--",
    "NEAR(",
    "NEAR(a b, 2)",
    "*",
    "msg:timeout",
    "^timeout",
    "timeout OR 1",
    'a" OR "b',
    "(((",
    "\x00",
    "\\",
    "%_",
    "'; ATTACH DATABASE 'x' AS y; --",
    "\u00e7\u00e3o \u4e2d\u6587 \U0001f600",
    "x" * 1024,
]


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    path = tmp_path_factory.mktemp("case") / "index.sqlite"
    rows = build_case(path)
    conn = open_ro(path)
    yield conn, rows
    conn.close()


def ids_for(conn, tree):
    cf = F.compile_filter(tree)
    with Q.read_guard(conn, 5.0):
        return {r[0] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}


def expected(rows, tree):
    return {r["id"] for r in rows if py_match(tree, r)}


def intact(conn, rows):
    assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] == len(rows)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"entry", "entry_body", "source", "template"} <= names


# --- compile ------------------------------------------------------------------------------------


def test_no_filter_is_true():
    for tree in (None, {}):
        assert F.compile_filter(tree) == F.TRUE
    assert F.compile_filter({"and": []}).sql == "1"
    assert F.compile_filter({"or": []}).sql == "0"


def test_section_10_example_compiles_with_one_param_per_placeholder(case):
    conn, _ = case
    cf = F.compile_filter(SECTION_10_EXAMPLE)
    assert cf.sql.count("?") == len(cf.params)
    assert cf.uses_fts
    assert ids_for(conn, SECTION_10_EXAMPLE) == set()


def test_user_values_never_appear_in_sql():
    marker = "zz_marker_zz"
    trees = [
        {"tpl": marker},
        {"family": marker},
        {"key": {"type": marker, "eq": marker}},
        {"event_status": marker},
        {"payload": {"role": marker}},
        {"text": {"substr": marker}},
        {"text": {"fts": marker}},
    ]
    for tree in trees:
        cf = F.compile_filter(tree)
        assert marker not in cf.sql
        assert any(marker in str(p) for p in cf.params)


@pytest.mark.parametrize(
    ("tree", "code"),
    [
        ([], "bad_node"),
        ("sev", "bad_node"),
        ({"sev": 1, "tid": 2}, "bad_node"),
        ({"drop": 1}, "unknown_operator"),
        ({"and": {"sev": 1}}, "bad_type"),
        ({"sev": "fatal"}, "bad_value"),
        ({"sev": 9}, "bad_value"),
        ({"sev": {"like": 1}}, "unknown_operator"),
        ({"sev": {}}, "bad_value"),
        ({"level": "loud"}, "bad_value"),
        ({"tid": "1; DROP"}, "bad_type"),
        ({"tid": True}, "bad_type"),
        ({"tid": 1.5}, "bad_type"),
        ({"tid": 2**63}, "bad_value"),
        ({"tid": []}, "empty_list"),
        ({"tid": list(range(F.MAX_LIST + 1))}, "list_too_long"),
        ({"tpl": "x" * (F.MAX_STR + 1)}, "string_too_long"),
        ({"tpl": -1}, "bad_value"),
        ({"sensitive": 1}, "bad_type"),
        ({"text": "timeout"}, "bad_type"),
        ({"text": {"regex": "a.*"}}, "unknown_operator"),
        ({"text": {"substr": ""}}, "bad_value"),
        ({"text": {"fts": "*"}}, "fts_empty"),
        ({"text": {"fts": "a*"}}, "fts_prefix_too_short"),
        ({"text": {"fts": " ".join(f"w{k}" for k in range(F.MAX_FTS_TERMS + 1))}}, "fts_too_many_terms"),
        ({"text": {"fts": "x" * (F.MAX_FTS_CHARS + 1)}}, "string_too_long"),
        ({"key": {"eq": "x"}}, "bad_type"),
        ({"key": {"type": "msgid", "eq": "a", "in": ["b"]}}, "bad_value"),
        ({"key": {"type": "msgid", "in": "a"}}, "bad_type"),
        ({"key": {"type": "msgid", "like": "a"}}, "bad_type"),
        ({"flags": "deleted"}, "bad_value"),
        ({"payload": {"body": "x"}}, "unknown_operator"),
        ({"payload": "response"}, "bad_type"),
        ({"ts": {"ge": "2026-01-01"}}, "bad_type"),
    ],
)
def test_invalid_trees_raise_typed_errors(tree, code):
    with pytest.raises(F.FilterError) as info:
        F.compile_filter(tree)
    assert info.value.code == code
    assert info.value.path.startswith("$")
    assert isinstance(info.value, ValueError)


def test_depth_and_node_limits():
    deep: dict = {"sev": 1}
    for _ in range(F.MAX_DEPTH):
        deep = {"not": deep}
    with pytest.raises(F.FilterError) as info:
        F.compile_filter(deep)
    assert info.value.code == "too_deep"
    wide = {"or": [{"sev": 1}] * F.MAX_NODES}
    with pytest.raises(F.FilterError) as info:
        F.compile_filter(wide)
    assert info.value.code == "too_many_nodes"
    many_params = {"or": [{"tid": list(range(F.MAX_LIST))}] * 5}
    with pytest.raises(F.FilterError) as info:
        F.compile_filter(many_params)
    assert info.value.code == "too_many_params"


def test_parse_filter_limits():
    assert F.parse_filter(None) is None
    assert F.parse_filter("  ") is None
    assert F.parse_filter('{"sev": 1}') == {"sev": 1}
    for text, code in (
        ("{", "invalid_json"),
        ("[" * 100_000 + "]" * 100_000, "too_large"),
        (" " * (F.MAX_JSON_BYTES + 1), "too_large"),
    ):
        with pytest.raises(F.FilterError) as info:
            F.parse_filter(text)
        assert info.value.code == code
    # Deep nesting within the size limit either fails to decode or is rejected by compile.
    deep = '{"not": ' * 6_000 + "{}" + "}" * 6_000
    with pytest.raises(F.FilterError) as info:
        F.compile_filter(F.parse_filter(deep))
    assert info.value.code in ("invalid_json", "too_deep")


# --- FTS restricted syntax ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("timeout", '"timeout"'),
        ("time*", '"time"*'),
        ('"exact phrase" more', '"exact phrase" "more"'),
        ("NEAR(a b)", '"NEAR(a" "b)"'),
        ("a OR b", '"a" "OR" "b"'),
        ("msg:timeout", '"msg:timeout"'),
        ('ab"cd', '"ab""cd"'),
        ('"unbalanced quote', '"unbalanced quote"'),
        ("* ** - ^", None),
        ("x\x00y", '"x" "y"'),
    ],
)
def test_fts_query_translation(text, query):
    if query is None:
        with pytest.raises(F.FilterError):
            F.fts_query(text)
    else:
        assert F.fts_query(text) == query


def test_fts_matches_words_and_prefixes(case):
    conn, rows = case
    indexed = [r for r in rows if r["tpl_id"] != 2]  # template 2 has fts = 0
    want = {r["id"] for r in indexed if "timeout" in r["msg"].lower().split()}
    assert want
    assert ids_for(conn, {"text": {"fts": "timeout"}}) == want
    assert ids_for(conn, {"text": {"fts": "TIME*"}}) == want
    want_cont = {r["id"] for r in indexed if r["cont"] and "Frame()" in r["cont"]}
    assert ids_for(conn, {"text": {"fts": '"Other Frame"'}}) == want_cont


# --- semantics against the reference evaluator ----------------------------------------------------

SEMANTIC_TREES = [
    {"source": 1},
    {"source": [2, 3]},
    {"stream": 2},
    {"family": "UNKNOWN"},
    {"sensitive": True},
    {"sensitive": False},
    {"sev": {"le": "error"}},
    {"sev": ["warning", 5]},
    {"sev": {"gt": 1, "lt": 4}},
    {"level": {"ge": "info"}},
    {"not": {"level": 3}},
    {"ts": {"ge": 1_000_100, "lt": 1_000_400}},
    {"ts": {"ne": 1_000_000}},
    {"not": {"ts": {"ge": 1_000_200}}},
    {"tid": [16, 32]},
    {"not": {"tid": 16}},
    {"pid": 4002},
    {"tpl": "TST-001"},
    {"tpl": ["TST-002", 0]},
    {"tpl": "NOPE"},
    {"event": 1},
    {"event_status": "fail"},
    {"not": {"event_status": "fail"}},
    {"key": {"type": "msgid", "eq": "m-1"}},
    {"key": {"type": "msgid", "in": ["m-1", "x"]}},
    {"key": {"type": "gid"}},
    {"flags": "has_cont"},
    {"flags": ["has_cont", "partial"]},
    {"not": {"flags": "ts_out_of_order"}},
    {"payload": {}},
    {"payload": {"role": ["response", "fault"], "peer": "WFM"}},
    {"payload": {"operation": "Other"}},
    {"text": {"substr": "Stack.Frame1"}},
    {"text": {"substr": "DROP TABLE"}},
    {"or": [{"event_status": "fail"}, {"flags": "partial"}]},
    {"and": [{"source": 1}, {"not": {"sev": 5}}, {"or": [{"tid": 16}, {"tid": 32}]}]},
]


@pytest.mark.parametrize("tree", SEMANTIC_TREES, ids=lambda t: json.dumps(t)[:60])
def test_semantics_match_reference_and_not_is_complement(case, tree):
    conn, rows = case
    got = ids_for(conn, tree)
    assert got == expected(rows, tree)
    assert ids_for(conn, {"not": tree}) == {r["id"] for r in rows} - got


# --- injection ------------------------------------------------------------------------------------


def _slots(s):
    return [
        {"tpl": s},
        {"family": [s, "AWFM"]},
        {"key": {"type": s, "eq": s}},
        {"key": {"type": "msgid", "in": [s]}},
        {"event_status": s},
        {"payload": {"operation": s, "role": [s]}},
        {"text": {"substr": s}},
        {"not": {"text": {"substr": s}}},
        {"text": {"fts": s}},
        {"or": [{"text": {"fts": s}}, {"tpl": s}]},
    ]


@pytest.mark.parametrize("s", INJECTION_STRINGS, ids=range(len(INJECTION_STRINGS)))
def test_injection_strings_are_plain_values(case, s):
    conn, rows = case
    for tree in _slots(s):
        try:
            cf = F.compile_filter(tree)
        except F.FilterError as exc:
            assert exc.code in ("fts_empty", "string_too_long")
            continue
        assert cf.sql.count("?") == len(cf.params)
        got = ids_for(conn, tree)
        if "fts" not in json.dumps(tree):
            assert got == expected(rows, tree)
        Q.page(conn, tree, None, "fwd", 50)
        assert Q.count(conn, tree).exact
    intact(conn, rows)


def test_substr_finds_the_injection_text_literally(case):
    conn, rows = case
    target = {r["id"] for r in rows if r["msg"] == INJECTION_MSG}
    assert len(target) == 1
    for part in ("Robert'); DROP TABLE entry;--", "' OR 1=1 --", "NEAR(x y) *"):
        assert ids_for(conn, {"text": {"substr": part}}) == target
    assert ids_for(conn, {"text": {"fts": "DROP TABLE"}}) == target


def test_fts_operators_from_user_are_literal_words(case):
    conn, _ = case
    # As FTS5 syntax these would be OR/NOT/NEAR/column queries; restricted, they are plain words.
    for text in ("timeout OR connected", "timeout NOT connected", "NEAR(timeout connected)", "msg:timeout"):
        assert ids_for(conn, {"text": {"fts": text}}) == set()


# --- empty tables (M1: template/event/payload/key not filled yet) ---------------------------------


def test_filters_on_empty_tables_return_nothing(tmp_path):
    conn = create_case_db(tmp_path / "index.sqlite")
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, msg)"
        " VALUES (1, 1, 1, 0, 1, 5, 4, 0, 'x')"
    )
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    ro = open_ro(tmp_path / "index.sqlite")
    for tree in (
        {"tpl": "WFM-081"},
        {"event_status": "fail"},
        {"event": 1},
        {"key": {"type": "msgid", "eq": "x"}},
        {"payload": {"role": "response"}},
        {"family": "AWFM"},
        {"sensitive": True},
        {"stream": 1},
    ):
        assert ids_for(ro, tree) == set()
        assert ids_for(ro, {"not": tree}) == {1}
    assert ids_for(ro, {"text": {"fts": "x"}}) == {1}
    ro.close()


# --- bucket compile -------------------------------------------------------------------------------


def test_bucket_filter_compatibility():
    assert F.compile_bucket_filter(None) == F.TRUE
    cf = F.compile_bucket_filter(
        {"and": [{"source": 1}, {"sev": {"le": 2}}, {"ts": {"ge": 120_000, "lt": 180_000}}]}
    )
    assert cf is not None and "b.minute" in cf.sql and "e." not in cf.sql
    assert cf.params == (1, 2, 2, 2)
    assert F.compile_bucket_filter({"not": {"tpl": "X"}}) is not None
    for tree in ({"tid": 1}, {"text": {"fts": "x"}}, {"not": {"ts": {"ge": 1}}}, {"ts": {"ne": 1}}):
        assert F.compile_bucket_filter(tree) is None
    with pytest.raises(F.FilterError):
        F.compile_bucket_filter({"sev": 99})


def test_read_guard_denies_writes(case):
    conn, _ = case
    with pytest.raises(sqlite3.DatabaseError), Q.read_guard(conn, 1.0):
        conn.execute("SELECT 1; ").fetchall()
        conn.execute("DELETE FROM entry")
