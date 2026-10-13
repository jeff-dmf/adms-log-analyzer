"""Property tests for the distinct values of a field (store/distinct.py, ROADMAP 9.2)."""

import json
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_helpers import build_case, open_ro  # noqa: E402
from test_distinct import FIELDS, _fill_bucket, check, reference  # noqa: E402
from test_filters_properties import _compile, leaf, trees  # noqa: E402

from dmslog.store import distinct as D  # noqa: E402
from dmslog.store import query as Q  # noqa: E402

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@lru_cache(maxsize=1)
def _cases():
    folder = Path(tempfile.mkdtemp(prefix="dmslog-distinct-"))
    rows = build_case(folder / "plain.sqlite")
    build_case(folder / "full.sqlite")
    _fill_bucket(folder / "full.sqlite")
    return open_ro(folder / "plain.sqlite"), open_ro(folder / "full.sqlite"), rows


@SETTINGS
@given(trees(leaf), st.sampled_from(FIELDS), st.integers(1, 30), st.booleans())
def test_distinct_matches_reference_on_every_path(tree, field, top, span):
    if _compile(tree) is None:
        return
    plain, full, rows = _cases()
    # The reference evaluator knows the template keys only: key leaves are checked on the plain case.
    targets = [(plain, False)] if '"key"' in json.dumps(tree) else [(plain, False), (full, True)]
    for conn, payload in targets:
        result = D.distinct(conn, tree, field, top, span=span)
        assert result.exact
        counts, spans = reference(rows, tree, field, payload)
        check(result, counts, spans, top, span)


@SETTINGS
@given(trees(leaf), st.sampled_from(FIELDS))
def test_drill_down_leaves_partition_the_filter(tree, field):
    if _compile(tree) is None:
        return
    _, full, _ = _cases()
    result = D.distinct(full, tree, field, D.MAX_TOP)
    assert result.others == 0
    for v in result.values:
        if v["leaf"] is not None:
            assert Q.count(full, {"and": [tree, v["leaf"]]}).n == v["count"]


@lru_cache(maxsize=1)
def _wide():
    """The case with its times stretched (x 2000) so that windows hold whole minutes."""
    import sqlite3

    folder = Path(tempfile.mkdtemp(prefix="dmslog-distinct-wide-"))
    rows = build_case(folder / "wide.sqlite")
    conn = sqlite3.connect(folder / "wide.sqlite")
    with conn:
        conn.execute("UPDATE entry SET ts = (ts - 999000) * 2000 WHERE ts IS NOT NULL")
    conn.close()
    _fill_bucket(folder / "wide.sqlite")
    stretched = [{**r, "ts": None if r["ts"] is None else (r["ts"] - 999_000) * 2000} for r in rows]
    return open_ro(folder / "wide.sqlite"), stretched


wide_ts = st.integers(-100_000, 12_000_000)
window = st.dictionaries(st.sampled_from(["lt", "le", "gt", "ge"]), wide_ts, min_size=1, max_size=2)


@SETTINGS
@given(trees(leaf), window, st.sampled_from(["tpl", "source", "sev"]), st.integers(1, 30), st.booleans())
def test_time_window_split_matches_reference(tree, bounds, field, top, span):
    """Whole minutes from bucket plus the partial minutes from entry equal the exact grouping."""
    if _compile(tree) is None or Q._uses_ts(tree) or '"key"' in json.dumps(tree):
        return
    conn, rows = _wide()
    flt = {"and": [tree, {"ts": bounds}]}
    result = D.distinct(conn, flt, field, top, span=span)
    assert result.exact and result.method in ("bucket", "exact")
    counts, spans = reference(rows, flt, field)
    check(result, counts, spans, top, span)
