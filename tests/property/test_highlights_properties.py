"""Property tests of the highlight evaluation (store/highlights.py) and of the filter modes it uses."""

import re
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from query_helpers import build_case, open_ro, order_key, py_match  # noqa: E402
from test_filters_properties import fts_leaf, leaf, trees  # noqa: E402

from dmslog.store import filters as F  # noqa: E402
from dmslog.store import highlights as H  # noqa: E402
from dmslog.store import query as Q  # noqa: E402

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@lru_cache(maxsize=1)
def _case():
    folder = Path(tempfile.mkdtemp(prefix="dmslog-hl-prop-"))
    rows = build_case(folder / "index.sqlite")
    return open_ro(folder / "index.sqlite"), rows


def _compile(tree, **kw):
    try:
        return F.compile_filter(tree, **kw)
    except F.FilterError:
        return None


def _ids(conn, cf):
    with Q.read_guard(conn, 5.0):
        return {r[0] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}


@SETTINGS
@given(trees(st.one_of(leaf, fts_leaf)))
def test_rowwise_mode_selects_the_same_rows(tree):
    a, b = _compile(tree), _compile(tree, rowwise=True)
    assume(a is not None)
    assert b is not None and b.params == a.params and b.sql.count("?") == len(b.params)
    conn, _ = _case()
    assert _ids(conn, a) == _ids(conn, b)


rule_lists = st.lists(trees(leaf), min_size=0, max_size=6)


@SETTINGS
@given(rule_lists, st.lists(st.integers(-3, 260), max_size=60))
def test_mark_is_the_first_matching_rule(rules, ids):
    assume(all(_compile(r) is not None for r in rules) and all(r for r in rules))
    conn, rows = _case()
    wanted = [i for i in ids if i >= 0]
    got = H.mark(conn, rules, wanted)
    by_id = {r["id"]: r for r in rows}
    expected = {}
    for i in wanted:
        row = by_id.get(i)
        if row is None:
            continue
        hit = next((k for k, rule in enumerate(rules) if py_match(rule, row)), None)
        if hit is not None:
            expected[i] = hit
    assert got == expected


@SETTINGS
@given(
    trees(leaf),
    st.lists(trees(leaf), min_size=1, max_size=4),
    st.one_of(st.none(), st.integers(1, 245)),
    st.sampled_from(["fwd", "back"]),
)
def test_next_match_is_the_nearest_match_in_grid_order(view, rules, at, direction):
    assume(_compile(view) is not None and all(r and _compile(r) is not None for r in rules))
    conn, rows = _case()
    ordered = sorted(rows, key=order_key)
    cursor_row = next((r for r in rows if r["id"] == at), None) if at is not None else None
    cursor = None if cursor_row is None else Q.Cursor(cursor_row["ts"], cursor_row["id"])
    if cursor_row is None:
        candidates = ordered if direction == "fwd" else ordered[::-1]
    elif direction == "fwd":
        candidates = [r for r in ordered if order_key(r) > order_key(cursor_row)]
    else:
        candidates = [r for r in ordered[::-1] if order_key(r) < order_key(cursor_row)]
    expected = next((r for r in candidates if py_match(view, r) and any(py_match(x, r) for x in rules)), None)
    found = H.next_match(conn, view, rules, cursor, direction)
    assert (found.row["id"] if found.row else None) == (expected["id"] if expected else None)
    if expected is not None:
        assert found.rule == next(k for k, x in enumerate(rules) if py_match(x, expected))


PIECES = ["a", "e", "m", " ", "\\d", "\\w", "[0-9]", "[^ ]", ".", "-", "=", "^", "$", "(?:ge|ss)", "\\b"]
QUANT = ["", "", "?", "+", "*", "{1,3}"]


@SETTINGS
@given(st.lists(st.tuples(st.sampled_from(PIECES), st.sampled_from(QUANT)), min_size=1, max_size=5))
def test_regex_leaf_matches_python_search_or_is_refused(parts):
    pattern = "".join(p + (q if p not in ("^", "$", "\\b") else "") for p, q in parts)
    try:
        cf = F.compile_filter({"text": {"regex": pattern}})
    except F.FilterError as exc:
        assert exc.code == "bad_regex"
        return
    conn, rows = _case()
    rx = re.compile(pattern)
    assert _ids(conn, cf) == {r["id"] for r in rows if rx.search(r["msg"], 0, F.REGEX_WINDOW)}


@SETTINGS
@given(st.text(max_size=80))
def test_any_regex_text_is_refused_cleanly_or_runs(pattern):
    try:
        cf = F.compile_filter({"text": {"regex": pattern}})
    except F.FilterError as exc:
        assert exc.code in {"bad_regex", "bad_value"} and exc.path == "$.text.regex"
        return
    conn, _ = _case()
    _ids(conn, cf)
