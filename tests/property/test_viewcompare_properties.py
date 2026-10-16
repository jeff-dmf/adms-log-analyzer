"""Property tests for the comparison of views (store/align.py and store/viewcompare.py, ROADMAP 9.7)."""

import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from compare_helpers import CODES, STREAMS, Content, Record, build, open_ro, without_ids  # noqa: E402

from dmslog.parse.signature import signature  # noqa: E402
from dmslog.store import align as AL  # noqa: E402
from dmslog.store import viewcompare as V  # noqa: E402
from dmslog.store.align import align, lcs_length  # noqa: E402

SETTINGS = settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
DB_SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

seqs = st.lists(st.integers(0, 5), max_size=24)


def _valid(a, b, pairs):
    return all(a[i] == b[j] for i, j in pairs) and all(
        p[0] < q[0] and p[1] < q[1] for p, q in zip(pairs, pairs[1:], strict=False)
    )


@SETTINGS
@given(seqs, seqs)
def test_exact_alignment_is_a_longest_common_subsequence(a, b):
    out = align(a, b)
    assert _valid(a, b, out.pairs)
    assert out.optimal and len(out.pairs) == lcs_length(a, b)
    assert out == align(a, b)
    assert len(align(b, a).pairs) == len(out.pairs)


@SETTINGS
@given(seqs, seqs, st.integers(0, 80), st.integers(0, 400))
def test_any_budget_gives_a_valid_common_subsequence(a, b, work, fallback):
    out = align(a, b, work=work, fallback_work=fallback)
    assert _valid(a, b, out.pairs)
    best = lcs_length(a, b)
    assert len(out.pairs) <= best and (not out.optimal or len(out.pairs) == best)
    assert out == align(a, b, work=work, fallback_work=fallback)


@SETTINGS
@given(seqs, seqs, st.permutations(range(6)))
def test_alignment_depends_only_on_token_equality(a, b, perm):
    assert align(a, b).pairs == align([f"x{perm[t]}" for t in a], [f"x{perm[t]}" for t in b]).pairs


# --------------------------------------------------------------------------- database level

records = st.lists(
    st.tuples(
        st.sampled_from(list(STREAMS)),
        st.one_of(st.none(), st.integers(1_000_000, 1_000_012)),  # few distinct ms: ties across files
        st.one_of(st.sampled_from(CODES), st.none()),
        st.sampled_from(["odd 1 x", "odd 22 x", "other 'q' y", "nul\x00 a", "nul\x00 b", "\x00"]),
    ),
    max_size=40,
)


def _content(spec):
    lines = dict.fromkeys(STREAMS, 0)
    out = []
    for stream, ts, code, msg in spec:
        lines[stream] += 1
        out.append(Record(stream, lines[stream], ts, code, msg))
    return Content(out)


def _token(r):
    return r.code if r.code else "?" + signature(r.msg)


def _reference(content, pick, max_side):
    """Model: records of the side in (ts, collection path, member, line) order, first max_side, as tokens."""
    chosen = sorted(
        (r for r in content.records if pick(r)),
        key=lambda r: (r.ts is not None, r.ts or 0, STREAMS[r.stream][0], STREAMS[r.stream][4], r.line),
    )
    return [_token(r) for r in chosen[:max_side]], len(chosen) > max_side


SIDES = {
    "all": ({"filter": None}, lambda r: True),
    "awfm": ({"filter": {"family": "AWFM"}}, lambda r: STREAMS[r.stream][2] == "AWFM"),
    "early": ({"filter": {"ts": {"lt": 1_000_006}}}, lambda r: r.ts is not None and r.ts < 1_000_006),
    "known": ({"filter": {"not": {"tpl": [0]}}}, lambda r: r.code is not None),
}


@DB_SETTINGS
@given(
    records,
    st.sampled_from(list(SIDES)),
    st.sampled_from(list(SIDES)),
    st.integers(1, 45),
    st.integers(0, 99),
)
def test_compare_matches_the_model_and_ignores_ids(spec, side_a, side_b, max_side, seed):
    content = _content(spec)
    body = {
        "mode": "filter",
        "a": SIDES[side_a][0],
        "b": SIDES[side_b][0],
        "max_side": max_side,
        "limit": 1000,
    }
    results = []
    with tempfile.TemporaryDirectory(prefix="dmslog-compare-", ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp)
        plain = build(folder / "a.sqlite", content)
        other = build(folder / "b.sqlite", content, permute=seed)
        for built in (plain, other):
            conn = open_ro(built.path)
            try:
                results.append(V.compare(conn, V.parse_request(body)))
            finally:
                conn.close()
    out = results[0]
    assert without_ids(out) == without_ids(results[1])
    ta, cut_a = _reference(content, SIDES[side_a][1], max_side)
    tb, cut_b = _reference(content, SIDES[side_b][1], max_side)
    assert (out["a"]["count"], out["a"]["truncated"]) == (len(ta), cut_a)
    assert (out["b"]["count"], out["b"]["truncated"]) == (len(tb), cut_b)
    assert out["stats"]["same"] == lcs_length(ta, tb) and out["optimal"]
    # rows rebuild both sequences in order, and "same" rows pair equal tokens
    got_a = [r["a_pos"] for r in out["rows"] if r["a_pos"] is not None]
    got_b = [r["b_pos"] for r in out["rows"] if r["b_pos"] is not None]
    assert got_a == list(range(len(ta))) and got_b == list(range(len(tb)))
    for r in out["rows"]:
        if r["op"] == "same":
            assert ta[r["a_pos"]] == tb[r["b_pos"]]
        if r["a"] is not None:
            assert (r["a"]["tpl"] or "?") == ta[r["a_pos"]][: len(r["a"]["tpl"] or "?")]
    assert out["total_rows"] == len(out["rows"]) and out["next_offset"] is None


@SETTINGS
@given(st.lists(st.integers(0, 5), max_size=60), st.lists(st.integers(0, 5), max_size=60))
def test_bit_parallel_stage_is_a_longest_common_subsequence(a, b):
    small = AL.SMALL
    AL.SMALL = 0  # Hirschberg down to single rows or columns (no direct Myers on small blocks)
    try:
        out = align(a, b, work=0)
    finally:
        AL.SMALL = small
    assert _valid(a, b, out.pairs) and out.optimal and len(out.pairs) == lcs_length(a, b)


tokens = st.lists(st.text(alphabet="?ab\x00", max_size=4), max_size=6)


@SETTINGS
@given(st.lists(st.tuples(tokens, tokens), min_size=1, max_size=6))
def test_alignment_cache_returns_what_a_new_computation_would(calls):
    cache = V._AlignCache(V.CACHE_SIZE)
    for a, b in calls:
        assert cache.get(a, b) == align(a, b)
