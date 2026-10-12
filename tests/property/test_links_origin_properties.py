"""Property tests for the origin = "operation" link rule (issue #65): the engine agrees with an oracle.

Synthetic data only: requests (operations) that log GIDs on their own thread, stray GID records outside any
operation and callbacks with random GIDs and times.
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

SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
MAX_DT = 3000

TOML = """
schema = 1
family = "AWFM"

[[template]]
code = "TST-001"
class = "other"
text = "sent"

[[template]]
code = "TST-002"
class = "req_resp"
text = "callback"

[[template]]
code = "TST-003"
class = "start"
text = "start"

[[link_rule]]
kind = "callback"
a = "TST-001"
b = "TST-002"
key = "gid"
origin = "operation"
max_dt_ms = 3000
"""

GIDS = st.sampled_from(["g1", "g2", "g3", "g4"])
TS = st.integers(min_value=0, max_value=20_000)
operation = st.tuples(TS, st.integers(min_value=1, max_value=3), st.lists(GIDS, max_size=3))  # ts, tid, gids
stray = st.tuples(TS, GIDS)  # a GID record outside any operation
callback = st.tuples(TS, GIDS)


def _build(path: Path, ops, strays, callbacks):
    cat = load_texts({"awfm.toml": TOML})
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
        " VALUES (1, 1, 'AWFM', 'W', 1, 0)"
    )
    conn.execute(
        "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope, status)"
        " VALUES (1, 1, 1, 'm', 'f.log', 'adms', 'ready')"
    )
    next_id = [1]
    a_rows, b_rows = [], []  # oracle input: (ts, id, gid, op start or None)

    def add(code, ts, tid, gid=None, event=None):
        eid = next_id[0]
        next_id[0] += 1
        conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " msg, event_id) VALUES (?,1,?,?,1,?,1,?,4,?,0,?,?)",
            (eid, eid, eid, ts, tid, cat.tpl_id(code), code, event),
        )
        if gid is not None:
            conn.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('gid', ?, ?)", (gid, eid))
        return eid

    for ts, tid, gids in ops:
        ev = conn.execute(
            "INSERT INTO event(stream_id, kind, tid, ts_start, sev) VALUES (1, 'operation', ?, ?, 4)",
            (tid, ts),
        ).lastrowid
        start = add("TST-003", ts, tid, event=ev)
        conn.execute("UPDATE event SET start_entry_id = ? WHERE id = ?", (start, ev))
        for i, g in enumerate(gids):
            eid = add("TST-001", ts + 10 + i, tid, g, ev)
            a_rows.append((ts + 10 + i, eid, g, start))
    for ts, g in strays:
        eid = add("TST-001", ts, 9, g)
        a_rows.append((ts, eid, g, None))
    for ts, g in callbacks:
        eid = add("TST-002", ts, 7, g)
        b_rows.append((ts, eid, g))
    return conn, cat, a_rows, b_rows


def _oracle(a_rows, b_rows):
    links: set[tuple[int, int, str, int]] = set()
    counts = dict.fromkeys(("unmatched", "ambiguous", "no_operation", "out_of_window", "dropped"), 0)
    fanout: dict[int, int] = {}
    for ts, eid, g in sorted(b_rows):
        same = [a for a in a_rows if a[2] == g]
        if not same:
            counts["unmatched"] += 1
            continue
        if len(same) > L.MAX_A_PER_KEY or len({a[3] for a in same}) > 1:
            counts["ambiguous"] += 1
            continue
        start = same[0][3]
        if start is None:
            counts["no_operation"] += 1
            continue
        before = [a[0] for a in same if a[0] <= ts]
        if not before or ts - max(before) > MAX_DT:
            counts["out_of_window"] += 1
            continue
        if fanout.get(start, 0) >= L.MAX_B_PER_A:
            counts["dropped"] += 1
            continue
        fanout[start] = fanout.get(start, 0) + 1
        links.add((start, eid, "callback", 2))
    return links, counts


@SETTINGS
@given(st.lists(operation, max_size=6), st.lists(stray, max_size=3), st.lists(callback, max_size=12))
def test_origin_links_match_the_oracle(ops, strays, callbacks):
    with tempfile.TemporaryDirectory() as tmp:
        conn, cat, a_rows, b_rows = _build(Path(tmp) / "case.sqlite", ops, strays, callbacks)
        try:
            summary = L.link_pending(conn, cat)
            got = set(conn.execute("SELECT a_entry_id, b_entry_id, kind, confidence FROM link").fetchall())
            want, counts = _oracle(a_rows, b_rows)
            assert summary.errors == []
            assert got == want
            assert {k: getattr(summary, k) for k in counts} == counts
            # idempotent: an unchanged group writes nothing on the next run
            before = conn.total_changes
            assert L.link_pending(conn, cat).groups == 0
            assert conn.total_changes == before
        finally:
            conn.close()
