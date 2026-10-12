"""Property tests for the window form of origin = "operation" (issue #66): the engine agrees with an oracle.

Synthetic data only: requests (operations with a start record, closed, open or with an odd end), start
records outside any operation and callbacks at random times. The oracle is brute force over every pair.
"""

import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.derive import links as L  # noqa: E402
from dmslog.parse.templates import load_texts  # noqa: E402
from dmslog.store.db import create_case_db  # noqa: E402

SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
MAX_DT = 3000

TOML = """
schema = 1
family = "AOSR"

[[template]]
code = "TST-001"
class = "start"
text = "request"

[[template]]
code = "TST-002"
class = "other"
text = "callback"

[[link_rule]]
kind = "callback"
a = "TST-001"
b = "TST-002"
window_only = true
origin = "operation"
max_dt_ms = 3000
"""

TS = st.integers(min_value=0, max_value=20_000)
# start ts, duration (negative = odd end before the start), closed
request = st.tuples(TS, st.integers(min_value=-50, max_value=6000), st.booleans())


def _build(path: Path, requests, strays, callbacks):
    cat = load_texts({"aosr.toml": TOML})
    conn = create_case_db(path)
    conn.isolation_level = None
    conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', '2026-01-01')")
    for t in cat.templates():
        conn.execute(
            "INSERT INTO template(id, code, family, class, pattern, def_version) VALUES (?,?,?,?,?,'v1')",
            (cat.tpl_id(t.code), t.code, t.family, t.cls, t.text or ""),
        )
    conn.execute(
        "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
        " VALUES (1, 1, 'AOSR', 'O', 1, 0)"
    )
    conn.execute(
        "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope, status)"
        " VALUES (1, 1, 1, 'm', 'f.log', 'adms', 'ready')"
    )
    next_id = [1]
    a_rows, b_rows = [], []  # oracle input: (anchor, id, closed) and (ts, id)

    def add(code, ts, event=None):
        eid = next_id[0]
        next_id[0] += 1
        conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " msg, event_id) VALUES (?,1,?,?,1,?,1,1,4,?,0,?,?)",
            (eid, eid, eid, ts, cat.tpl_id(code), code, event),
        )
        return eid

    for ts, dur, closed in requests:
        end = ts + dur if closed else None
        ev = conn.execute(
            "INSERT INTO event(stream_id, kind, tid, ts_start, ts_end, sev)"
            " VALUES (1, 'operation', 1, ?, ?, 4)",
            (ts, end),
        ).lastrowid
        start = add("TST-001", ts, ev)
        conn.execute("UPDATE event SET start_entry_id = ? WHERE id = ?", (start, ev))
        ok = end is not None and end >= ts
        a_rows.append((end if ok else ts, start, ok))
    for ts in strays:
        a_rows.append((ts, add("TST-001", ts), False))
    for ts in callbacks:
        b_rows.append((ts, add("TST-002", ts)))
    return conn, cat, a_rows, b_rows


def _oracle(a_rows, b_rows):
    links: set[tuple[int, int, str, int]] = set()
    counts = dict.fromkeys(("unmatched", "ambiguous", "no_operation", "out_of_window"), 0)
    for ts, eid in b_rows:
        cands = [a for a in a_rows if ts - MAX_DT <= a[0] <= ts]
        if not cands:
            counts["out_of_window" if a_rows else "unmatched"] += 1
            continue
        if len(cands) > 1:
            counts["ambiguous"] += 1
            continue
        anchor, start, closed = cands[0]
        if sum(1 for t, _ in b_rows if anchor <= t <= anchor + MAX_DT) != 1:
            counts["ambiguous"] += 1
        elif not closed:
            counts["no_operation"] += 1
        else:
            links.add((start, eid, "callback", 0))
    return links, counts


@SETTINGS
@given(st.lists(request, max_size=6), st.lists(TS, max_size=2), st.lists(TS, max_size=10))
def test_window_origin_links_match_the_oracle(requests, strays, callbacks):
    with tempfile.TemporaryDirectory() as tmp:
        conn, cat, a_rows, b_rows = _build(Path(tmp) / "case.sqlite", requests, strays, callbacks)
        try:
            summary = L.link_pending(conn, cat)
            got = set(conn.execute("SELECT a_entry_id, b_entry_id, kind, confidence FROM link").fetchall())
            want, counts = _oracle(a_rows, b_rows)
            assert summary.errors == []
            assert got == want
            assert {k: getattr(summary, k) for k in counts} == counts
            assert summary.dropped == 0
            # every callback is accounted for exactly once
            assert summary.links + sum(counts.values()) == len(b_rows)
            before = conn.total_changes
            assert L.link_pending(conn, cat).groups == 0
            assert conn.total_changes == before
        finally:
            conn.close()


@SETTINGS
@given(st.lists(request, min_size=1, max_size=6), st.lists(TS, min_size=1, max_size=10), st.data())
def test_window_origin_follows_moved_operation_ends(requests, callbacks, data):
    """Re-derivation that only changes ``event.ts_end`` (no source change) gives the oracle's answer."""
    with tempfile.TemporaryDirectory() as tmp:
        conn, cat, a_rows, b_rows = _build(Path(tmp) / "case.sqlite", requests, [], callbacks)
        try:
            L.link_pending(conn, cat)
            i = data.draw(st.integers(min_value=0, max_value=len(a_rows) - 1))
            new_end = data.draw(st.one_of(st.none(), TS))
            _anchor, start, _closed = a_rows[i]
            ts = conn.execute("SELECT ts FROM entry WHERE id = ?", (start,)).fetchone()[0]
            conn.execute("UPDATE event SET ts_end = ? WHERE start_entry_id = ?", (new_end, start))
            ok = new_end is not None and new_end >= ts
            a_rows[i] = (new_end if ok else ts, start, ok)
            summary = L.link_pending(conn, cat)
            got = set(conn.execute("SELECT a_entry_id, b_entry_id, kind, confidence FROM link").fetchall())
            want, _counts = _oracle(a_rows, b_rows)
            assert summary.errors == []
            assert got == want
        finally:
            conn.close()


@SETTINGS
@given(
    st.lists(request, max_size=8),
    st.lists(TS, max_size=2),
    st.lists(TS, max_size=10),
    st.integers(min_value=1, max_value=3),
)
def test_window_origin_memory_bound_never_guesses(requests, strays, callbacks, bound):
    """Review of PR #91: with a tiny ``MAX_OPEN_KEYS`` the engine links a subset of the oracle, never more.

    Every callback is still accounted for exactly once (linked, or one of the counters, ``dropped`` for what
    the bound made undecidable).
    """
    saved = L.MAX_OPEN_KEYS
    L.MAX_OPEN_KEYS = bound
    try:
        with tempfile.TemporaryDirectory() as tmp:
            conn, cat, a_rows, b_rows = _build(Path(tmp) / "case.sqlite", requests, strays, callbacks)
            try:
                summary = L.link_pending(conn, cat)
                got = set(conn.execute("SELECT a_entry_id, b_entry_id, kind, confidence FROM link"))
                want, _counts = _oracle(a_rows, b_rows)
                assert summary.errors == []
                assert got <= want
                decided = summary.ambiguous + summary.unmatched + summary.no_operation + summary.out_of_window
                assert summary.links + decided + summary.dropped == len(b_rows)
                if summary.dropped == 0:
                    assert got == want
                if a_rows:  # requests exist: a lone callback is never "unmatched", whatever the bound
                    assert summary.unmatched == 0
                else:
                    assert summary.out_of_window == 0
            finally:
                conn.close()
    finally:
        L.MAX_OPEN_KEYS = saved
