"""Normal <-> sensitive pairing (derive/correlate.py) over a synthetic case database."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from dmslog.derive.correlate import correlate_pending, pair_stats
from dmslog.store import create_case_db

N, S = "TST-N1", "TST-S1"


class Rules:
    def __init__(self, pairs):
        self._pairs = tuple(pairs)

    def sensitive_pairs(self):
        return self._pairs


class FakeCatalog:
    def __init__(self, pairs):
        self.rules = Rules(pairs)

    def for_family(self, family, sensitive=False):
        return self.rules if family == "ATST" else Rules(())


def catalog(**rule):
    base = {"normal": N, "sensitive": S}
    base.update(rule)
    return FakeCatalog([base])


class Case:
    """Hand-built case: one group, a normal and a sensitive stream of the same process."""

    def __init__(self, conn, group=1, pid=100):
        self.conn = conn
        self.group = group
        self.pid = pid
        conn.execute(
            "INSERT OR IGNORE INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')"
        )
        for tid, code in ((1, N), (2, S), (3, "TST-X")):
            conn.execute(
                "INSERT OR IGNORE INTO template(id, code, family, class, pattern, def_version)"
                " VALUES (?, ?, 'ATST', 'other', 'p', 'v1')",
                (tid, code),
            )
        self.tpl = {N: 1, S: 2, "TST-X": 3}
        self.streams = {}
        for sens in (0, 1):
            cur = conn.execute(
                "INSERT INTO stream(collection_group, family, process, pid, sensitive)"
                " VALUES (?, 'ATST', 'TstAdapter', ?, ?)",
                (group, pid, sens),
            )
            sid = cur.lastrowid
            src = conn.execute(
                "INSERT INTO source(collection_id, stream_id, member, file_name, envelope, size,"
                " indexed_bytes, n_entries, status) VALUES (1, ?, ?, ?, 'adms', 1, 1, 0, 'ready')",
                (sid, f"m{group}_{sid}", f"f{sid}.log"),
            ).lastrowid
            self.streams[sens] = src
        conn.commit()

    def add(self, sens, code, ts, tid, fields=None, flags=0, key_type=None, key=None):
        src = self.streams[sens]
        cur = self.conn.execute(
            "INSERT INTO entry(source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " fields, msg) VALUES (?, 1, 0, 1, ?, ?, ?, 4, ?, ?, ?, 'm')",
            (src, ts, self.pid, tid, self.tpl[code], flags, json.dumps(fields) if fields else None),
        )
        if key_type:
            self.conn.execute(
                "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?, ?, ?)",
                (key_type, key, cur.lastrowid),
            )
        self.conn.execute(
            "UPDATE source SET n_entries = n_entries + 1, indexed_bytes = indexed_bytes + 1 WHERE id = ?",
            (src,),
        )
        return cur.lastrowid

    def normal(self, ts, tid=1, **kw):
        kw.setdefault("flags", 4)
        return self.add(0, N, ts, tid, **kw)

    def sensitive(self, ts, tid=1, **kw):
        return self.add(1, S, ts, tid, **kw)

    def bracket(self, lo=0, hi=1_000_000):
        """Make both streams cover [lo, hi] so the common window is wide (unrelated records)."""
        for sens in (0, 1):
            self.add(sens, "TST-X", lo, 99)
            self.add(sens, "TST-X", hi, 99)


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "case.sqlite")
    yield c
    c.close()


def links(conn):
    return conn.execute(
        "SELECT a_entry_id, b_entry_id, kind, confidence FROM link ORDER BY a_entry_id"
    ).fetchall()


def test_exact_ts_and_tid_is_confidence_2(conn):
    c = Case(conn)
    c.bracket()
    n, s = c.normal(5000), c.sensitive(5000)
    summary = correlate_pending(conn, catalog())
    assert (summary.links, summary.groups, summary.errors) == (1, 1, [])
    assert links(conn) == [(n, s, "normal_sensitive", 2)]


def test_same_second_is_confidence_1(conn):
    c = Case(conn)
    c.bracket()
    n, s = c.normal(5000), c.sensitive(5012)
    correlate_pending(conn, catalog())
    assert links(conn) == [(n, s, "normal_sensitive", 1)]


def test_key_equality_is_confidence_3_and_decides(conn):
    c = Case(conn)
    c.bracket()
    n1 = c.normal(5000, fields={"rid": "A"})
    n2 = c.normal(5001, fields={"rid": "B"})
    s2 = c.sensitive(5002, fields={"rid": "B"})
    s1 = c.sensitive(5003, fields={"rid": "A"})
    correlate_pending(conn, catalog(key="rid"))
    assert links(conn) == [(n1, s1, "normal_sensitive", 3), (n2, s2, "normal_sensitive", 3)]


def test_key_from_entry_key_and_tid_ignored_when_match_omits_it(conn):
    c = Case(conn)
    c.bracket()
    n = c.normal(5000, tid=7, key_type="rid", key="K1")
    s = c.sensitive(5900, tid=8, key_type="rid", key="K1")
    correlate_pending(conn, catalog(key="rid", match=["pid"]))
    assert links(conn) == [(n, s, "normal_sensitive", 3)]


def test_different_keys_never_pair(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000, fields={"rid": "A"})
    c.sensitive(5000, fields={"rid": "B"})
    assert correlate_pending(conn, catalog(key="rid")).links == 0


def test_consumed_in_order_two_normals_one_sensitive(conn):
    c = Case(conn)
    c.bracket()
    n1, _n2 = c.normal(5000), c.normal(5100)
    s = c.sensitive(5100)
    correlate_pending(conn, catalog())
    # the first normal takes the only sensitive record; the second finds none left
    assert links(conn) == [(n1, s, "normal_sensitive", 1)]
    assert pair_stats(conn, 1)[f"{N}>{S}"]["normal_in_window"] == 2


def test_two_normals_two_sensitives_pair_in_order(conn):
    c = Case(conn)
    c.bracket()
    n1, n2 = c.normal(5000), c.normal(5100)
    s1, s2 = c.sensitive(5000), c.sensitive(5100)
    correlate_pending(conn, catalog())
    assert links(conn) == [(n1, s1, "normal_sensitive", 2), (n2, s2, "normal_sensitive", 2)]


def test_tid_mismatch_does_not_pair(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000, tid=1)
    c.sensitive(5000, tid=2)
    assert correlate_pending(conn, catalog()).links == 0


def test_null_tid_and_null_ts_never_raise(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000, tid=None)
    c.sensitive(5000, tid=None)
    c.normal(None)
    summary = correlate_pending(conn, catalog())
    assert summary.errors == [] and summary.links == 0


def test_outside_common_window_is_not_linked_nor_counted(conn):
    c = Case(conn)
    # normal covers [0, 100000]; sensitive covers [50000, 200000]; window [50000, 100000]
    c.add(0, "TST-X", 0, 99)
    c.add(0, "TST-X", 100_000, 99)
    c.add(1, "TST-X", 50_000, 99)
    c.add(1, "TST-X", 200_000, 99)
    c.normal(10_000)  # before the sensitive stream starts: not counted
    inside_n, inside_s = c.normal(70_000), c.sensitive(70_000)
    summary = correlate_pending(conn, catalog())
    assert summary.links == 1
    assert links(conn) == [(inside_n, inside_s, "normal_sensitive", 2)]
    stats = pair_stats(conn, 1)[f"{N}>{S}"]
    assert (stats["normal_in_window"], stats["linked"]) == (1, 1)


@pytest.mark.parametrize(("dt", "linked"), [(1000, 1), (1001, 0)])
def test_max_dt_boundary(conn, dt, linked):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000 + dt)
    assert correlate_pending(conn, catalog(max_dt_ms=1000)).links == linked


def test_sensitive_before_normal_only_with_order_any(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(4000)
    assert correlate_pending(conn, catalog()).links == 0
    conn.execute("DELETE FROM meta WHERE key LIKE 'correlate.%'")
    assert correlate_pending(conn, catalog(order="any")).links == 1


def test_unflagged_normals_are_counted_in_the_summary(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000, flags=0)
    c.sensitive(5000)
    summary = correlate_pending(conn, catalog())
    assert summary.links == 1 and summary.unflagged == {N: 1}


def test_stats_by_confidence(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000)
    c.normal(6000, tid=2)
    c.sensitive(6400, tid=2)
    correlate_pending(conn, catalog())
    stats = pair_stats(conn, 1)[f"{N}>{S}"]
    assert stats["by_confidence"] == {3: 0, 2: 1, 1: 1}
    assert pair_stats(conn, 99) == {}


def test_rerun_writes_nothing(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000)
    cat = catalog()
    assert correlate_pending(conn, cat).links == 1
    conn.commit()
    before = conn.total_changes
    summary = correlate_pending(conn, cat)
    assert (summary.links, summary.groups) == (0, 0)
    assert conn.total_changes == before


def test_rerun_after_growth_recomputes(conn):
    c = Case(conn)
    c.bracket()
    n1 = c.normal(5000)
    s1 = c.sensitive(5000)
    cat = catalog()
    correlate_pending(conn, cat)
    n2, s2 = c.normal(7000), c.sensitive(7000)
    summary = correlate_pending(conn, cat)
    assert (summary.groups, summary.links) == (1, 2)
    assert links(conn) == [(n1, s1, "normal_sensitive", 2), (n2, s2, "normal_sensitive", 2)]


def test_other_kinds_of_links_survive_a_recompute(conn):
    c = Case(conn)
    c.bracket()
    n, s = c.normal(5000), c.sensitive(5000)
    conn.execute("INSERT INTO link VALUES (?, ?, 'callback', 3)", (n, s))
    correlate_pending(conn, catalog())
    c.normal(9000)
    correlate_pending(conn, catalog())
    assert {k for _a, _b, k, _c in links(conn)} == {"callback", "normal_sensitive"}


def test_group_without_a_sensitive_stream_writes_nothing(conn):
    c = Case(conn)
    conn.execute("DELETE FROM source WHERE stream_id IN (SELECT id FROM stream WHERE sensitive = 1)")
    conn.execute("DELETE FROM stream WHERE sensitive = 1")
    c.normal(5000)
    conn.commit()
    before = conn.total_changes
    assert correlate_pending(conn, catalog()).groups == 0
    assert conn.total_changes == before


def test_failure_rolls_back_inside_a_caller_transaction(conn, monkeypatch):
    import dmslog.derive.correlate as mod

    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000)
    conn.commit()
    original = mod._correlate_group

    def boom(*args, **kwargs):
        original(*args, **kwargs)
        conn.execute(
            "INSERT INTO link VALUES (900, 901, 'normal_sensitive', 1)"
        )  # written before the failure
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_correlate_group", boom)
    conn.execute("INSERT INTO meta(key, value) VALUES ('caller', 'kept')")  # caller's own open transaction
    summary = correlate_pending(conn, catalog())
    assert summary.links == 0 and summary.groups == 0
    assert [g for g, m in summary.errors if "boom" in m] == [1]
    assert conn.in_transaction
    assert conn.execute("SELECT count(*) FROM link").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'correlate.%'").fetchone()[0] == 0
    assert conn.execute("SELECT value FROM meta WHERE key = 'caller'").fetchone() == ("kept",)
    conn.rollback()
    monkeypatch.undo()
    assert correlate_pending(conn, catalog()).links == 1  # nothing stale blocks the next run


def test_caller_transaction_is_left_open_on_success(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000)
    conn.commit()
    conn.execute("INSERT INTO meta(key, value) VALUES ('caller', 'x')")
    assert correlate_pending(conn, catalog()).links == 1
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM link").fetchone()[0] == 0


def test_ten_thousand_pairs_are_fast(conn):
    c = Case(conn)
    c.bracket()
    for i in range(10_000):
        ts = 1000 + i * 50
        tid = i % 7 + 1
        c.normal(ts, tid=tid)
        c.sensitive(ts + (i % 3), tid=tid)
    conn.commit()
    started = time.perf_counter()
    summary = correlate_pending(conn, catalog())
    elapsed = time.perf_counter() - started
    assert summary.links == 10_000
    stats = pair_stats(conn, 1)[f"{N}>{S}"]
    assert stats["linked"] == stats["normal_in_window"] == 10_000
    assert elapsed < 5


def test_unknown_family_and_missing_templates_are_harmless(conn):
    c = Case(conn)
    c.bracket()
    c.normal(5000)
    c.sensitive(5000)
    cat = FakeCatalog([{"normal": "NOPE-1", "sensitive": "NOPE-2"}])
    summary = correlate_pending(conn, cat)
    assert summary.errors == [] and summary.links == 0
    assert SimpleNamespace(**pair_stats(conn, 1)["NOPE-1>NOPE-2"]).linked == 0


# ---- review fixes: signature, stale links, complexity, bounded memory


def one_pair(conn):
    c = Case(conn)
    c.bracket()
    n, s = c.normal(5000), c.sensitive(5000)
    conn.commit()
    return c, n, s


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE source SET zip_crc = 12345 WHERE stream_id IN (SELECT id FROM stream WHERE sensitive = 0)",
        "UPDATE source SET mtime = 99.5",
        "UPDATE source SET size = size + 7",
        "UPDATE template SET def_version = 'v2' WHERE code = 'TST-N1'",
        "UPDATE meta SET value = 'x' WHERE key = 'entries_epoch'",
    ],
)
def test_signature_notices_a_reindex_with_the_same_counts(conn, sql):
    one_pair(conn)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('entries_epoch', '1')")
    cat = catalog()
    assert correlate_pending(conn, cat).groups == 1
    conn.commit()
    before = conn.total_changes
    assert correlate_pending(conn, cat).groups == 0 and conn.total_changes == before  # unchanged: no writes
    conn.execute(sql)
    assert correlate_pending(conn, cat).groups == 1


def test_unrelated_template_change_keeps_the_signature(conn):
    one_pair(conn)
    cat = catalog()
    correlate_pending(conn, cat)
    conn.execute("UPDATE template SET def_version = 'v2' WHERE code = 'TST-X'")
    assert correlate_pending(conn, cat).groups == 0


def fts_index(conn):
    """Index the hand-made entries in the FTS table (the writer's delete path removes them from it)."""
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES ('delete-all')")
    conn.execute("INSERT INTO entry_fts(rowid, msg, cont) SELECT rowid, msg, cont FROM entry_text")
    conn.commit()


def test_writer_deletion_removes_incident_links_and_bumps_the_epoch(conn):
    from dmslog.store.writer import IndexWriter

    c, n, s = one_pair(conn)
    correlate_pending(conn, catalog())
    conn.commit()
    assert len(links(conn)) == 1
    fts_index(conn)
    w = IndexWriter(conn)
    w.delete_source_entries(c.streams[0])  # the normal side goes away (reindex)
    w.commit()
    assert links(conn) == []
    assert int(conn.execute("SELECT value FROM meta WHERE key = 'entries_epoch'").fetchone()[0]) == 1
    # the sensitive side too: links where the removed record is endpoint b
    n2 = c.normal(8000)
    s2 = c.sensitive(8000)
    conn.commit()
    correlate_pending(conn, catalog())
    conn.commit()
    fts_index(conn)
    assert (n2, s2) in [(a, b) for a, b, _k, _c in links(conn)]
    w.delete_source_entries(c.streams[1])
    w.commit()
    assert links(conn) == []


def test_recreated_record_leaves_no_stale_pair(conn):
    c, n, s = one_pair(conn)
    cat = catalog()
    correlate_pending(conn, cat)
    # simulate a writer that deleted the normal record without cleaning links, then a new one at the same spot
    conn.execute("DELETE FROM entry WHERE id = ?", (n,))
    n2 = c.normal(5000)
    conn.commit()
    summary = correlate_pending(conn, cat)
    assert summary.groups == 1
    assert [(a, b) for a, b, _k, _c in links(conn)] == [(n2, s)]


def test_orphan_links_of_this_engine_are_removed_but_not_other_kinds(conn):
    c, n, s = one_pair(conn)
    conn.execute("INSERT INTO link VALUES (9001, ?, 'normal_sensitive', 1)", (s,))
    conn.execute("INSERT INTO link VALUES (9002, ?, 'callback', 3)", (s,))
    conn.commit()
    correlate_pending(conn, catalog())
    assert sorted((a, k) for a, _b, k, _c in links(conn)) == sorted(
        [(n, "normal_sensitive"), (9002, "callback")]
    )


def timed_pairing(n_records):
    normals = [[i, 1000, 1, None, 4] for i in range(n_records)]
    sens = [[n_records + i, 1000, 1, None, 0] for i in range(n_records)]
    started = time.perf_counter()
    from dmslog.derive.correlate import pair_records

    out = pair_records(normals, sens, use_tid=True, max_dt=1000, order_any=False, use_key=False)
    assert len(out) == n_records
    return time.perf_counter() - started


def test_equal_timestamps_do_not_go_quadratic():
    small, large = timed_pairing(2_500), timed_pairing(20_000)  # 8x the records
    assert large < 5
    assert large < max(small, 0.005) * 40  # quadratic growth would be about 64x


def test_key_mismatch_does_not_go_quadratic():
    from dmslog.derive.correlate import pair_records

    n = 20_000
    normals = [[i, 1000, 1, f"A{i}", 4] for i in range(n)]
    sens = [[n + i, 1000, 1, f"B{i}", 0] for i in range(n)]
    started = time.perf_counter()
    out = pair_records(normals, sens, use_tid=True, max_dt=1000, order_any=False, use_key=True)
    assert out == [] and time.perf_counter() - started < 5


def test_streaming_flushes_in_batches_and_matches_the_default_result(conn, monkeypatch):
    import dmslog.derive.correlate as mod

    c = Case(conn)
    c.bracket()
    for i in range(300):
        c.normal(1000 + i * 10, tid=i % 4 + 1)
        c.sensitive(1000 + i * 10 + i % 2, tid=i % 4 + 1)
    conn.commit()
    correlate_pending(conn, catalog())
    expected = links(conn)
    conn.execute("DELETE FROM meta WHERE key LIKE 'correlate.%'")
    conn.commit()
    flushed = []
    original = mod._flush

    def spy(connection, batch):
        flushed.append(len(batch))
        original(connection, batch)

    monkeypatch.setattr(mod, "INSERT_BATCH", 50)
    monkeypatch.setattr(mod, "CHUNK", 7)
    monkeypatch.setattr(mod, "_flush", spy)
    summary = correlate_pending(conn, catalog())
    assert summary.links == 300 and links(conn) == expected
    assert flushed == [50] * 6


def test_caps_drop_and_report(conn, monkeypatch):
    import dmslog.derive.correlate as mod

    c = Case(conn)
    c.bracket()
    for i in range(20):
        c.normal(1000 + i, tid=1)
        c.sensitive(1000 + i, tid=1)
    conn.commit()
    monkeypatch.setattr(mod, "MAX_CANDIDATES_PER_RULE", 12)
    summary = correlate_pending(conn, catalog())
    assert summary.links == 12 and summary.dropped == 8
    assert summary.dropped_by_rule == {f"{N}>{S}": {"dropped": 8}}
    conn.execute("DELETE FROM meta WHERE key LIKE 'correlate.%'")
    monkeypatch.setattr(mod, "MAX_CANDIDATES_PER_RULE", 10_000)
    monkeypatch.setattr(mod, "MAX_BUFFER", 5)
    summary = correlate_pending(conn, catalog())
    assert summary.dropped > 0 and summary.errors == []


# ---- key priority and physical bound of pair_stream


def reference_pairs(normals, sens, *, use_tid, max_dt, order_any, use_key):
    """Brute-force two-pass matching (the specification of ``pair_stream``)."""
    lo = 2100 if order_any else 0
    taken: set[int] = set()
    paired: dict[int, tuple[int, int]] = {}

    def window(n, s):
        return (not use_tid or n[2] == s[2]) and n[1] - lo <= s[1] <= n[1] + max_dt

    for n in normals:
        if not use_key or n[3] is None or (use_tid and n[2] is None):
            continue
        for j, s in enumerate(sens):
            if j not in taken and s[3] == n[3] and not (use_tid and s[2] is None) and window(n, s):
                taken.add(j)
                paired[n[0]] = (s[0], 3)
                break
    for n in normals:
        if n[0] in paired or (use_tid and n[2] is None):
            continue
        for j, s in enumerate(sens):
            if j in taken or (use_tid and s[2] is None) or not window(n, s):
                continue
            if use_key and n[3] is not None and s[3] is not None:
                continue
            taken.add(j)
            paired[n[0]] = (s[0], 2 if s[1] == n[1] else 1)
            break
    return {(a, b, c) for a, (b, c) in paired.items()}


def test_keyed_normal_keeps_priority_over_an_earlier_keyless_one():
    from dmslog.derive.correlate import pair_records

    normals = [[1, 1000, 1, None, 4], [2, 1001, 1, "A", 4]]
    sens = [[10, 1001, 1, "A", 0]]
    out = pair_records(normals, sens, use_tid=True, max_dt=1000, order_any=False, use_key=True)
    assert out == [(2, 10, 3)]


def small_records(draw_ts, draw_tid, draw_key, first_id):
    return [first_id, draw_ts, draw_tid, draw_key, 4]


def test_pair_stream_matches_the_brute_force_reference():
    hyp = pytest.importorskip("hypothesis")
    st = hyp.strategies
    from dmslog.derive.correlate import pair_records

    rec = st.tuples(
        st.integers(0, 12), st.sampled_from([1, 1, 2, None]), st.sampled_from([None, None, "A", "B"])
    )

    @hyp.settings(max_examples=400, deadline=None)
    @hyp.given(
        ns=st.lists(rec, max_size=14),
        ss=st.lists(rec, max_size=14),
        scale=st.sampled_from([1, 100, 450]),
        max_dt=st.sampled_from([0, 1, 1000, 1800]),
        use_tid=st.booleans(),
        order_any=st.booleans(),
        use_key=st.booleans(),
    )
    def check(ns, ss, scale, max_dt, use_tid, order_any, use_key):
        normals = [
            [i, ts * scale, tid, key, 4] for i, (ts, tid, key) in enumerate(sorted(ns, key=lambda r: r[0]))
        ]
        sens = [
            [100 + i, ts * scale, tid, key, 0]
            for i, (ts, tid, key) in enumerate(sorted(ss, key=lambda r: r[0]))
        ]
        kw = {"use_tid": use_tid, "max_dt": max_dt, "order_any": order_any, "use_key": use_key}
        got = pair_records(normals, sens, **kw)
        assert len(got) == len({a for a, _b, _c in got}) == len({b for _a, b, _c in got})  # each used once
        assert set(got) == reference_pairs(normals, sens, **kw)

    check()


def test_consumed_records_do_not_pile_up_behind_a_live_one():
    from dmslog.derive.correlate import pair_records

    cap, n = 1000, 40_000
    # one sensitive record that never matches (TID 99) stays in the window the whole time (order "any");
    # the keyed pairs are consumed right away (pass 1), so only a few records are ever live
    sens = [[1, 1000, 99, None, 0]] + [[10 + i, 1000 + i // 20, 1, f"k{i}", 0] for i in range(n)]
    normals = [[100_000 + i, 1000 + i // 20, 1, f"k{i}", 4] for i in range(n)]
    peak = [0]
    counters: dict[str, int] = {}
    out = pair_records(
        normals,
        sens,
        use_tid=True,
        max_dt=2,
        order_any=True,
        use_key=True,
        max_buffer=cap,
        counters=counters,
        peak=peak,
    )
    assert len(out) == n and counters.get("dropped", 0) == 0
    assert peak[0] <= 6 * cap  # without compaction the consumed records would pile up to n


def run_bounded(normals, sens, cap, **kw):
    from dmslog.derive.correlate import pair_records

    peak = [0]
    counters: dict[str, int] = {}
    args = {"use_tid": True, "max_dt": 1000, "order_any": False, "use_key": True}
    args.update(kw)
    out = pair_records(normals, sens, max_buffer=cap, counters=counters, peak=peak, **args)
    return out, counters.get("dropped", 0), peak[0]


def test_many_keyed_sensitives_at_one_timestamp_stay_bounded():
    cap, n = 1000, 40_000
    normals = [[1, 1000, 1, "nomatch", 4]]
    sens = [[10 + i, 1000, 1, f"k{i}", 0] for i in range(n)]
    out, dropped, peak = run_bounded(normals, sens, cap)
    assert dropped >= n - cap - 1  # everything beyond the cap is dropped and counted
    assert peak <= 6 * cap  # the SUM of every container, not just the main queue
    assert len(out) <= 1


def test_many_keyed_normals_and_one_sensitive_stay_bounded():
    cap, n = 1000, 40_000
    normals = [[10 + i, 1000, 1, f"k{i}", 4] for i in range(n)]
    sens = [[1, 1000, 1, None, 0]]
    out, _dropped, peak = run_bounded(normals, sens, cap)
    assert peak <= 6 * cap
    assert len(out) == 1  # the only sensitive record is consumed once


def test_long_stream_where_nothing_matches_stays_bounded():
    cap, n = 2000, 60_000
    normals = [[i, 1000 + 2 * i, 1, f"n{i}", 4] for i in range(n)]
    sens = [[n + i, 1000 + 2 * i, 2, f"s{i}" if i % 2 else None, 0] for i in range(n)]  # other TID: no pair
    out, dropped, peak = run_bounded(normals, sens, cap)
    assert out == [] and dropped == 0
    assert peak <= 4 * cap
