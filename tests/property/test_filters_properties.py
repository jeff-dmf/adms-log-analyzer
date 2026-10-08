"""Property tests for the filter compiler (ARQUITETURA 10 and 13)."""

import sqlite3
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from query_helpers import build_case, open_ro, py_match  # noqa: E402

from dmslog.store import filters as F  # noqa: E402
from dmslog.store import query as Q  # noqa: E402

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Tables a compiled filter may read (plus FTS5 shadow tables).
EXPECTED_TABLES = {
    "entry",
    "entry_body",
    "source",
    "stream",
    "template",
    "event",
    "entry_key",
    "payload",
    "entry_fts",
    "entry_text",
}

any_text = st.text(max_size=40)
small_ints = st.integers(min_value=-5, max_value=200)
id_list = st.one_of(small_ints, st.lists(small_ints, min_size=1, max_size=5))
str_list = st.one_of(any_text, st.lists(any_text, min_size=1, max_size=4))


def names_or_ints(names, lo, hi):
    return st.one_of(st.integers(lo, hi), st.sampled_from(sorted(names)))


def ordered(names, lo, hi):
    value = names_or_ints(names, lo, hi)
    cmp = st.dictionaries(
        st.sampled_from(["eq", "ne", "lt", "le", "gt", "ge"]), value, min_size=1, max_size=3
    )
    return st.one_of(value, st.lists(value, min_size=1, max_size=3), cmp)


ts_value = st.integers(999_900, 1_004_000)
ts_cmp = st.dictionaries(
    st.sampled_from(["eq", "ne", "lt", "le", "gt", "ge"]), ts_value, min_size=1, max_size=2
)

leaf = st.one_of(
    st.builds(lambda v: {"source": v}, id_list),
    st.builds(lambda v: {"stream": v}, id_list),
    st.builds(lambda v: {"family": v}, st.one_of(str_list, st.sampled_from(["AWFM", "UNKNOWN"]))),
    st.builds(lambda v: {"sensitive": v}, st.booleans()),
    st.builds(lambda v: {"sev": v}, ordered(F.SEV_NAMES, 1, 5)),
    st.builds(lambda v: {"level": v}, ordered(F.LEVEL_NAMES, 1, 8)),
    st.builds(lambda v: {"ts": v}, st.one_of(ts_value, ts_cmp)),
    st.builds(lambda v: {"tid": v}, st.one_of(st.sampled_from([16, 32, 48]), id_list)),
    st.builds(lambda v: {"pid": v}, st.one_of(st.sampled_from([4001, 4002]), id_list)),
    st.builds(lambda v: {"event": v}, id_list),
    st.builds(lambda v: {"tpl": v}, st.one_of(st.sampled_from(["TST-001", "TST-002", 0, 1, 2]), str_list)),
    st.builds(
        lambda v: {"text": {"substr": v}},
        st.one_of(st.sampled_from(["Frame", "timeout", "'"]), st.text(min_size=1, max_size=20)),
    ),
    st.builds(
        lambda t, v: {"key": {"type": t, "eq": v}},
        st.sampled_from(["msgid", "gid", "x"]),
        st.sampled_from(["m-1", "g-9", "' OR 1=1 --"]),
    ),
    st.builds(lambda t: {"key": {"type": t}}, st.sampled_from(["msgid", "gid", "x"])),
    st.builds(lambda v: {"event_status": v}, st.one_of(st.sampled_from(["fail", "ok"]), str_list)),
    st.builds(lambda v: {"flags": v}, st.lists(st.sampled_from(sorted(F.FLAG_BITS)), min_size=1, max_size=3)),
    st.builds(
        lambda v: {"payload": v},
        st.dictionaries(
            st.sampled_from(["role", "peer", "operation", "fmt"]),
            st.sampled_from(["response", "WFM", "SendWorks", "xml", "z"]),
            max_size=3,
        ),
    ),
)
fts_leaf = st.builds(
    lambda v: {"text": {"fts": v}},
    st.one_of(st.sampled_from(["timeout", "crew*", '"value 5"']), st.text(min_size=1, max_size=30)),
)


def trees(base):
    return st.recursive(
        base,
        lambda children: st.one_of(
            st.builds(lambda c: {"and": c}, st.lists(children, max_size=4)),
            st.builds(lambda c: {"or": c}, st.lists(children, max_size=4)),
            st.builds(lambda c: {"not": c}, children),
        ),
        max_leaves=12,
    )


@lru_cache(maxsize=1)
def _case():
    folder = Path(tempfile.mkdtemp(prefix="dmslog-prop-"))
    rows = build_case(folder / "index.sqlite")
    return open_ro(folder / "index.sqlite"), rows


def _ids(conn, cf):
    with Q.read_guard(conn, 5.0):
        return {r[0] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}


def _compile(tree):
    try:
        return F.compile_filter(tree)
    except F.FilterError as exc:
        # Generated values may be legitimately invalid (empty FTS text, sev 0, ...): typed error only.
        assert exc.code in {"fts_empty", "fts_prefix_too_short", "bad_value", "too_many_nodes", "too_deep"}
        return None


@SETTINGS
@given(trees(st.one_of(leaf, fts_leaf)))
def test_compiled_filter_is_parameterized_and_only_reads(tree):
    cf = _compile(tree)
    if cf is None:
        return
    assert cf.sql.count("?") == len(cf.params)
    assert "'" not in cf.sql and '"' not in cf.sql and ";" not in cf.sql and "--" not in cf.sql
    conn, _ = _case()
    actions = []

    def recorder(action, arg1, arg2, db, src):
        actions.append((action, arg1))
        return sqlite3.SQLITE_OK

    conn.set_authorizer(recorder)
    try:
        conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params).fetchall()
    finally:
        conn.set_authorizer(None)
    for action, arg1 in actions:
        if action == sqlite3.SQLITE_READ:
            assert arg1 in EXPECTED_TABLES or arg1.startswith("entry_fts_"), arg1
        elif action == sqlite3.SQLITE_PRAGMA:
            assert arg1 == "data_version"
        else:
            assert action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION), action


@SETTINGS
@given(trees(leaf))
def test_compiled_filter_matches_reference_semantics(tree):
    cf = _compile(tree)
    if cf is None:
        return
    conn, rows = _case()
    got = _ids(conn, cf)
    assert got == {r["id"] for r in rows if py_match(tree, r)}


@SETTINGS
@given(trees(st.one_of(leaf, fts_leaf)), trees(st.one_of(leaf, fts_leaf)))
def test_boolean_algebra_holds(a, b):
    ca, cb = _compile(a), _compile(b)
    if ca is None or cb is None:
        return
    conn, rows = _case()
    every = {r["id"] for r in rows}
    sa, sb = _ids(conn, ca), _ids(conn, cb)
    assert _ids(conn, F.compile_filter({"not": a})) == every - sa
    assert _ids(conn, F.compile_filter({"and": [a, b]})) == sa & sb
    assert _ids(conn, F.compile_filter({"or": [a, b]})) == sa | sb


@SETTINGS
@given(st.text(max_size=600))
def test_any_search_text_is_safe_for_fts(text):
    try:
        query = F.fts_query(text)
    except F.FilterError as exc:
        assert exc.code in {"fts_empty", "fts_prefix_too_short", "fts_too_many_terms", "string_too_long"}
        return
    conn, _ = _case()
    with Q.read_guard(conn, 5.0):
        conn.execute("SELECT rowid FROM entry_fts WHERE entry_fts MATCH ?", (query,)).fetchall()


@SETTINGS
@given(
    st.recursive(
        st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), any_text),
        lambda c: st.one_of(st.lists(c, max_size=4), st.dictionaries(any_text, c, max_size=3)),
        max_leaves=20,
    )
)
def test_arbitrary_json_never_crashes_the_compiler(tree):
    try:
        cf = F.compile_filter(tree)
    except F.FilterError:
        return
    assert cf.sql.count("?") == len(cf.params)
