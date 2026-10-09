"""Derivation engine over SQLite: events, entry.event_id, checkpoints, rotation, incremental equivalence."""

from __future__ import annotations

import json
import sqlite3

import pytest
import test_pipeline as tp
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from dmslog.derive import build_derivers
from dmslog.derive.engine import derive_pending, derive_stream
from dmslog.derive.operations import OperationsDeriver
from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse.templates import load_texts

mf = tp.mf

TOML = r"""
schema = 1
family = "AWFM"

[[template]]
code = "TST-001"
class = "start"
regex = '''^\[Op\] (?P<op>\w+) begin (?P<rid>\d+)$'''
keys = [{ field = "rid", type = "request_id" }]
fields = { rid = "int" }

[[template]]
code = "TST-002"
class = "end"
regex = '''^\[Op\] end (?P<rid>\d+)$'''
keys = [{ field = "rid", type = "request_id" }]
fields = { rid = "int" }

[[template]]
code = "TST-003"
class = "error"
text = "[Op] problem"

[[template]]
code = "TST-004"
class = "other"
regex = '''^\[Op\] work (?P<rid>\d+)$'''
keys = [{ field = "rid", type = "request_id" }]
fields = { rid = "int" }

[[operation]]
name_field = "op"
start = ["TST-001"]
end_ok = ["TST-002"]
fail = ["TST-003"]
pair_by = ["tid"]
check_key = "request_id"
"""

WFM = tp.WFM
WFM_ROTATED = tp.WFM_ROTATED


def catalog():
    return load_texts({"awfm.toml": TOML})


def line(t, msg, tid=1):
    return mf.header(t, "INFO", msg, tid=tid)


def render(lines):
    return mf.render(lines)


def ingest(conn, root):
    return run_ingest(conn, root, IngestOptions(catalog=catalog()))


@pytest.fixture
def db(tmp_path):
    conn = tp.fresh_db(tmp_path, "c")
    yield conn
    conn.close()


def events(conn):
    return conn.execute(
        "SELECT kind, name, key, tid, start_entry_id, end_entry_id, ts_start, ts_end, duration_ms, status,"
        " sev,"
        " n_entries, detail FROM event ORDER BY start_entry_id, id"
    ).fetchall()


def basic_lines():
    return [
        line(0, "[Op] SendExample begin 1", 1),
        line(10, "[Op] work 1", 1),
        line(15, "[Op] work 9", 2),  # another TID
        line(20, "[Op] end 1", 1),
        line(30, "[Op] Other begin 2", 2),
        line(35, "[Op] problem", 2),
        line(40, "[Op] end 2", 2),
        line(50, "[Op] Last begin 3", 3),  # never ends
    ]


def test_events_and_event_id_are_stored(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    result = ingest(db, root)
    assert result.warnings == [] and result.events_derived == 3
    rows = events(db)
    assert [(r[1], r[9], r[8], r[11]) for r in rows] == [
        ("SendExample", "ok", 20, 3),
        ("Other", "fail", 10, 3),
        ("Last", "open", None, 1),
    ]
    by_event = {}
    for eid, ev in db.execute("SELECT id, event_id FROM entry"):
        by_event.setdefault(ev, []).append(eid)
    first = db.execute("SELECT id FROM event WHERE name = 'SendExample'").fetchone()[0]
    assert len(by_event[first]) == 3 and None in by_event  # the TID 2 "work 9" line belongs to nothing yet
    assert db.execute("SELECT msg FROM entry WHERE event_id = ?", (first,)).fetchall() == [
        ("[Op] SendExample begin 1",),
        ("[Op] work 1",),
        ("[Op] end 1",),
    ]
    detail = json.loads(db.execute("SELECT detail FROM event WHERE name = 'Other'").fetchone()[0])
    assert detail["fail_how"] == "fail_code"
    (cp,) = db.execute("SELECT derive_checkpoint FROM stream").fetchone()
    last = db.execute("SELECT id FROM entry WHERE msg LIKE '%Last begin%'").fetchone()[0]
    assert 0 < cp <= last  # other derivers (startup window) may hold it earlier


def test_rerun_without_changes_writes_nothing(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    ingest(db, root)
    before = db.total_changes
    result = ingest(db, root)
    assert db.total_changes == before and result.events_derived == 0
    (stream_id,) = db.execute("SELECT id FROM stream").fetchone()
    assert derive_stream(db, stream_id, catalog()).skipped
    assert derive_pending(db, catalog()).derived == 0
    assert db.total_changes == before


def test_checkpoint_follows_the_oldest_open_operation_and_tail_is_rederived(tmp_path, db, monkeypatch):
    import dmslog.derive.engine as engine

    monkeypatch.setattr(
        engine, "build_derivers", lambda f, r, *, stream_start=True, **k: [OperationsDeriver(f, r, **k)]
    )
    lines = basic_lines()
    root = tp.write_folder(tmp_path / "Log", {WFM: render(lines)})
    ingest(db, root)
    cp = db.execute("SELECT derive_checkpoint FROM stream").fetchone()[0]
    assert cp == db.execute("SELECT id FROM entry WHERE msg LIKE '%Last begin%'").fetchone()[0]
    keep = db.execute("SELECT id FROM event WHERE name = 'SendExample'").fetchone()[0]
    lines += [line(60, "[Op] work 3", 3), line(70, "[Op] end 3", 3)]
    (root / WFM).write_bytes(render(lines))
    ingest(db, root)
    assert db.execute("SELECT id FROM event WHERE name = 'SendExample'").fetchone()[0] == keep  # untouched
    last = db.execute("SELECT status, duration_ms, n_entries FROM event WHERE name = 'Last'").fetchall()
    assert last == [("ok", 20, 3)]
    assert db.execute("SELECT count(*) FROM event").fetchone()[0] == 3
    assert db.execute("SELECT derive_checkpoint FROM stream").fetchone()[0] > cp


def test_operation_split_between_rotated_log_and_current_log(tmp_path, db):
    """The older file is the rotated .log.0; ids may be in any order across the two files."""
    old = render([line(0, "[Op] Split begin 5", 1), line(5, "[Op] work 5", 1)])
    new = render([line(10, "[Op] work 5", 1), line(20, "[Op] end 5", 1)])
    root = tp.write_folder(tmp_path / "Log", {WFM: new, WFM_ROTATED: old})
    ingest(db, root)
    (ev,) = events(db)
    assert (ev[1], ev[9], ev[8], ev[11]) == ("Split", "ok", 20, 4)
    ids = db.execute(
        "SELECT s.file_name, e.msg FROM entry e JOIN source s ON s.id = e.source_id"
        " WHERE e.event_id IS NOT NULL ORDER BY e.id"
    ).fetchall()
    assert len(ids) == 4
    # the older file got the lower ids (ingested oldest first), so the checkpoint is meaningful
    lows = dict(
        db.execute(
            "SELECT s.file_name, min(e.id) FROM entry e JOIN source s ON s.id = e.source_id GROUP BY 1"
        )
    )
    assert lows["WfmAdapter_1111.log.0"] < lows["WfmAdapter_1111.log"]


def test_rotation_follow_up_after_the_current_file_was_rotated(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: render([line(0, "[Op] Split begin 5", 1)])})
    ingest(db, root)
    assert db.execute("SELECT status FROM event").fetchall() == [("open",)]
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(render([line(20, "[Op] end 5", 1)]))
    ingest(db, root)
    assert db.execute("SELECT status, duration_ms FROM event").fetchall() == [("ok", 20)]


def test_unordered_ids_fall_back_to_full_derivation(tmp_path, db):
    """An older file added after a newer one (ids against the stream order): each change re-derives it all."""
    root = tp.write_folder(tmp_path / "Log", {WFM: render([line(20, "[Op] end 5", 1)])})
    ingest(db, root)
    (root / WFM_ROTATED).write_bytes(render([line(0, "[Op] Split begin 5", 1)]))
    ingest(db, root)
    assert [(r[1], r[9], r[8]) for r in events(db)] == [("Split", "ok", 20)]
    assert db.execute("SELECT derive_checkpoint FROM stream").fetchone() == (0,)
    before = db.total_changes
    ingest(db, root)
    assert db.total_changes == before


def test_reindex_and_changed_rules_rederive_from_scratch(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    ingest(db, root)
    (stream_id,) = db.execute("SELECT id FROM stream").fetchone()
    other = load_texts({"awfm.toml": TOML.replace('check_key = "request_id"', "")})
    result = derive_stream(db, stream_id, other)
    assert not result.skipped and result.full and result.events == 3
    assert db.execute("SELECT count(*) FROM event").fetchone()[0] == 3  # replaced, not duplicated
    (root / WFM).write_bytes(render(basic_lines()[:4]))  # rewritten shorter: reindexed
    ingest(db, root)
    assert [(r[1], r[9]) for r in events(db)] == [("SendExample", "ok")]
    assert db.execute("SELECT count(*) FROM entry WHERE event_id IS NOT NULL").fetchone() == (3,)


def test_derivation_failure_is_a_warning_not_an_error(tmp_path, db, monkeypatch):
    import dmslog.derive.engine as engine

    def boom(*a, **k):
        raise RuntimeError("deriver bug")

    monkeypatch.setattr(OperationsDeriver, "feed", boom)
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    result = ingest(db, root)
    assert result.records == 8 and len(result.warnings) == 1 and "deriver bug" in result.warnings[0]
    assert db.execute("SELECT count(*) FROM event").fetchone() == (0,)
    assert not db.in_transaction and engine.META_PREFIX
    monkeypatch.undo()
    ingest(db, root)  # the next run retries
    assert db.execute("SELECT count(*) FROM event").fetchone() == (3,)


def test_build_derivers_always_has_operations_first():
    derivers = build_derivers("AWFM", None)
    assert isinstance(derivers[0], OperationsDeriver)
    assert [type(d).__name__ for d in derivers[1:]] == [
        n
        for n in ("StatsDeriver", "LifecycleDeriver", "ExceptionDeriver", "RetryDeriver")
        if n in {type(d).__name__ for d in derivers}
    ]


# ------------------------------------------------------------ property: incremental == one shot

ACTIONS = st.lists(
    st.tuples(st.integers(1, 3), st.sampled_from(["begin", "work", "end", "problem", "level"])),
    min_size=1,
    max_size=40,
)


def build_lines(actions):
    lines, rid, current = [], 0, {}
    for i, (tid, action) in enumerate(actions):
        t = i * 7
        if action == "begin":
            rid += 1
            current[tid] = rid
            lines.append(line(t, f"[Op] Op{tid} begin {rid}", tid))
        elif action == "end":
            lines.append(line(t, f"[Op] end {current.get(tid, 99)}", tid))
        elif action == "level":
            lines.append(line(t, "Debug level changed from Info (3) to Debug (4).", tid))
        elif action == "work":
            lines.append(line(t, f"[Op] work {current.get(tid, 98)}", tid))
        else:
            lines.append(line(t, "[Op] problem", tid))
    return lines


def _without_ids(detail):
    """Entry ids differ between the two routes (the incremental one re-adds the last record): drop them."""
    data = json.loads(detail) if detail else {}
    data.pop("fail_entry_id", None)
    return data


def snapshot(conn):
    ev = conn.execute(
        "SELECT e.kind, e.name, e.key, e.tid, s.byte_off, f.byte_off, e.ts_start, e.ts_end, e.duration_ms,"
        " e.status,"
        " e.sev, e.n_entries, e.detail FROM event e LEFT JOIN entry s ON s.id = e.start_entry_id"
        " LEFT JOIN entry f ON f.id = e.end_entry_id ORDER BY s.byte_off, e.name"
    ).fetchall()
    ev = [(*r[:-1], _without_ids(r[-1])) for r in ev]
    owner = conn.execute(
        "SELECT x.byte_off, o.byte_off FROM entry x LEFT JOIN event v ON v.id = x.event_id"
        " LEFT JOIN entry o ON o.id = v.start_entry_id ORDER BY x.byte_off"
    ).fetchall()
    return ev, owner


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@example(  # an operation closed by the tail record (re-added by the ingestion) next to an open level window
    actions=[(1, "begin")] * 7 + [(1, "level"), (1, "end"), (1, "begin")], cut=0, cut2=9
)
@example(actions=[(1, "begin")] * 7 + [(1, "level"), (1, "begin")], cut=0, cut2=8)
@given(actions=ACTIONS, cut=st.integers(0, 40), cut2=st.integers(0, 40))
def test_incremental_derivation_equals_one_shot(tmp_path_factory, actions, cut, cut2):
    lines = build_lines(actions)
    first = min(cut, len(lines))
    second = min(max(first, cut2), len(lines))
    base = tmp_path_factory.mktemp("prop")
    whole = tp.fresh_db(base, "whole")
    steps = tp.fresh_db(base, "steps")
    try:
        ingest(whole, tp.write_folder(base / "w", {WFM: render(lines)}))
        root = tp.write_folder(base / "s", {WFM: render(lines[:first])})
        ingest(steps, root)
        for upto in (second, len(lines)):
            (root / WFM).write_bytes(render(lines[:upto]))
            ingest(steps, root)
        assert snapshot(steps) == snapshot(whole)
    finally:
        whole.close()
        steps.close()


# --------------------------------------------------------------------------- stateful deriver (stats deltas)

HOUR = 3_600_000


def stats_block(t, scale):
    rows = [("Example.Contract.IQuery.Get", 2 * scale, 20 * scale, 200 * scale)]
    csv = ["[method name, call count, request total size, response total size]"]
    csv += [f"{m},{c},{q},{r}" for m, c, q, r in rows]
    out = [
        line(t, "[Communication2] - Client-side statistics:"),
        line(
            t,
            f"[Communication2] - [TOTAL][Client] Call count: {2 * scale},"
            f" Request bytes: {20 * scale} (avg. 1.0),"
            f" Response bytes: {200 * scale} (avg. 2.5). Total operations: {2 * scale}",
        ),
    ]
    for order in ("call count", "Request Message Size Total", "Response Message Size Total"):
        out.append(line(t, f"[Communication2] - By {order}: "))
        out += csv
    out.append(line(t + 5, "[Op] work 1", 9))
    return out


def sample_rows(conn):
    return conn.execute(
        "SELECT ts, side, method, calls, req_bytes, resp_bytes, d_calls, d_req_bytes, d_resp_bytes"
        " FROM stat_sample ORDER BY ts, side, method"
    ).fetchall()


def test_stat_samples_and_deltas_survive_incremental_derivation(tmp_path, db):
    from dmslog.ingest.pipeline import default_catalog

    blocks = [stats_block(i * HOUR, i + 1) for i in range(4)]
    whole = tp.fresh_db(tmp_path, "whole")
    try:
        full = [ln for b in blocks for ln in b]
        run_ingest(
            whole,
            tp.write_folder(tmp_path / "w", {WFM: render(full)}),
            IngestOptions(catalog=default_catalog()),
        )
        want = sample_rows(whole)
        assert len(want) >= 8 and want[-1][6] == 2  # the last delta is 2 calls
        root = tp.write_folder(tmp_path / "s", {WFM: render(blocks[0] + blocks[1][:4])})  # cut inside block 2
        for upto in (blocks[0] + blocks[1] + blocks[2][:3], full):
            run_ingest(db, root, IngestOptions(catalog=default_catalog()))
            (root / WFM).write_bytes(render(upto))
        run_ingest(db, root, IngestOptions(catalog=default_catalog()))
        assert sample_rows(db) == want
        before = db.total_changes
        run_ingest(db, root, IngestOptions(catalog=default_catalog()))
        assert db.total_changes == before
    finally:
        whole.close()


# --------------------------------------------------------------------------- rotation chains in one stream

BLT_TOML = TOML.replace('family = "AWFM"', 'family = "BLTHOST"')
DAY = 86_400_000


def blt_catalog():
    return load_texts({"blt.toml": BLT_TOML})


def chain_lines(base, n, first=1):
    out = []
    for i in range(first, first + n):
        out += [line(base + i * 100, f"[Op] Op begin {i}"), line(base + i * 100 + 5, f"[Op] end {i}")]
    return out


def ingest_blt(conn, root):
    return run_ingest(conn, root, IngestOptions(catalog=blt_catalog()))


def test_unrelated_files_of_one_stream_are_separate_chains(tmp_path, db):
    root = tp.write_folder(
        tmp_path / "Log",
        {
            "BLTHost.Common.log": render(chain_lines(10 * DAY, 3)),
            "BLTHost.Common.log.0": render(chain_lines(9 * DAY, 2)),
            "BLTHost.Arbitration.log": render(chain_lines(0, 3)),  # days before: no jump between files
        },
    )
    assert ingest_blt(db, root).warnings == []
    assert db.execute("SELECT count(*) FROM stream").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'clock_jump'").fetchone() == (0,)
    chains = {
        json.loads(d)["chain"] for (d,) in db.execute("SELECT detail FROM event WHERE kind = 'operation'")
    }
    assert chains == {"BLTHost.Common.log", "BLTHost.Arbitration.log"}
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'operation'").fetchone() == (8,)
    keys = [k for (k,) in db.execute("SELECT key FROM meta WHERE key LIKE 'derive.%' ORDER BY key")]
    assert keys == ["derive.1.BLTHost.Arbitration.log", "derive.1.BLTHost.Common.log"]


def test_growing_one_chain_rederives_only_that_chain(tmp_path, db):
    files = {
        "BLTHost.Common.log": render(chain_lines(10 * DAY, 3)),
        "BLTHost.Arbitration.log": render(chain_lines(0, 3)),
    }
    root = tp.write_folder(tmp_path / "Log", files)
    ingest_blt(db, root)

    def snap(name):
        rec = db.execute("SELECT value FROM meta WHERE key = ?", (f"derive.1.{name}",)).fetchone()[0]
        ids = db.execute(
            "SELECT id FROM event WHERE json_extract(detail, '$.chain') = ? ORDER BY id", (name,)
        ).fetchall()
        return rec, ids

    arb_before, common_before = snap("BLTHost.Arbitration.log"), snap("BLTHost.Common.log")
    (root / "BLTHost.Common.log").write_bytes(render(chain_lines(10 * DAY, 5)))
    ingest_blt(db, root)
    assert snap("BLTHost.Arbitration.log") == arb_before  # untouched: same record, same event rows
    rec, ids = snap("BLTHost.Common.log")
    assert rec != common_before[0] and len(ids) == 5
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'operation'").fetchone() == (8,)
    before = db.total_changes
    ingest_blt(db, root)
    assert db.total_changes == before


def test_out_of_order_ids_across_chains_do_not_force_full_derivation(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {"BLTHost.Common.log": render(chain_lines(5 * DAY, 2))})
    ingest_blt(db, root)
    (root / "BLTHost.Arbitration.log").write_bytes(render(chain_lines(0, 2)))  # older ts, higher ids
    ingest_blt(db, root)
    common = db.execute("SELECT value FROM meta WHERE key = 'derive.1.BLTHost.Common.log'").fetchone()[0]
    assert json.loads(common)["cp"] > 0
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'operation'").fetchone() == (4,)


def test_clock_jump_pass_flags_simultaneous_hour_gaps_in_every_chain(tmp_path, db):
    from dmslog.derive.engine import _clock_jump_pass

    root = tp.write_folder(
        tmp_path / "Log",
        {
            "BLTHost.Common.log": render(chain_lines(0, 2)),
            "BLTHost.Arbitration.log": render(chain_lines(0, 2)),
        },
    )
    ingest_blt(db, root)
    (group,) = db.execute("SELECT collection_group FROM stream").fetchone()
    hour = 3_600_000
    for chain, start in (("BLTHost.Common.log", 1000), ("BLTHost.Arbitration.log", 1010)):
        db.execute(
            "INSERT INTO event(stream_id, kind, start_entry_id, ts_start, ts_end, duration_ms, sev, detail)"
            " VALUES (1, 'gap', ?, ?, ?, ?, 4, ?)",
            (start, start, start + hour, hour, json.dumps({"chain": chain})),
        )
    db.commit()
    _clock_jump_pass(db, group)
    db.commit()
    flags = db.execute("SELECT json_extract(detail, '$.possible_clock_jump') FROM event WHERE kind = 'gap'")
    assert flags.fetchall() == [(1,), (1,)]
    before = db.total_changes
    _clock_jump_pass(db, group)
    assert db.total_changes == before
    db.execute("DELETE FROM event WHERE kind = 'gap' AND start_entry_id = 1010")
    _clock_jump_pass(db, group)  # a chain without its matching gap vetoes the flag
    assert db.execute(
        "SELECT json_extract(detail, '$.possible_clock_jump') FROM event WHERE kind='gap'"
    ).fetchall() == [(None,)]


# --------------------------------------------------------------------------- review fixes (PR #21)


def test_reset_finds_chain_events_without_their_start_entries(tmp_path, db):
    root = tp.write_folder(
        tmp_path / "Log",
        {
            "BLTHost.Common.log": render(chain_lines(0, 3)),
            "BLTHost.Arbitration.log": render(chain_lines(0, 3)),
        },
    )
    ingest_blt(db, root)
    (stream_id,) = db.execute("SELECT id FROM stream").fetchone()
    victim = db.execute(
        "SELECT id, start_entry_id FROM event WHERE kind = 'operation'"
        " AND json_extract(detail, '$.chain') = 'BLTHost.Common.log' ORDER BY id LIMIT 1"
    ).fetchone()
    before = db.execute("SELECT count(*) FROM event WHERE kind = 'operation'").fetchone()[0]
    db.execute("UPDATE entry SET event_id = NULL WHERE event_id = ?", (victim[0],))
    db.execute("DELETE FROM entry WHERE id = ?", (victim[1],))  # what a re-index does to the start entry
    db.commit()
    derive_stream(db, stream_id, catalog_for_blt(), force=True)
    assert db.execute("SELECT count(*) FROM event WHERE id = ?", (victim[0],)).fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'operation'").fetchone() == (before - 1,)
    # the other chain keeps all of its events
    other = "SELECT count(*) FROM event WHERE json_extract(detail, '$.chain') = 'BLTHost.Arbitration.log'"
    assert db.execute(other + " AND kind = 'operation'").fetchone() == (3,)


def catalog_for_blt():
    return blt_catalog()


def test_failure_inside_a_callers_transaction_rolls_back_the_stream(tmp_path, db, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    ingest(db, root)
    (stream_id,) = db.execute("SELECT id FROM stream").fetchone()

    def snap():
        return (
            db.execute("SELECT * FROM event ORDER BY id").fetchall(),
            db.execute("SELECT id, event_id FROM entry ORDER BY id").fetchall(),
            db.execute("SELECT * FROM stat_sample ORDER BY ts").fetchall(),
            db.execute("SELECT key, value FROM meta ORDER BY key").fetchall(),
        )

    before = snap()
    calls = {"n": 0}
    real = OperationsDeriver.feed

    def flaky(self, entry):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("deriver bug")
        return real(self, entry)

    monkeypatch.setattr(OperationsDeriver, "feed", flaky)
    db.execute("BEGIN")  # the caller's transaction
    db.execute("INSERT INTO meta(key, value) VALUES ('caller.mark', '1')")
    with pytest.raises(RuntimeError):
        derive_stream(db, stream_id, catalog(), force=True)
    assert db.in_transaction  # still the caller's
    summary = derive_pending(db, catalog())  # no pending changes: nothing to do, but must not lose anything
    assert summary.errors == []
    db.execute("COMMIT")
    after = snap()
    assert [r for r in after[3] if r[0] != "caller.mark"] == before[3]
    assert after[:3] == before[:3]


def test_resumed_pass_does_not_open_an_extra_level_window(tmp_path, db):
    lines = [
        line(0, "[Op] Open begin 1"),
        line(10, "[Op] work 1"),
        line(20, "Debug level changed from Info (3) to Debug (4)."),
        line(30, "[Op] work 1"),
    ]
    root = tp.write_folder(tmp_path / "Log", {WFM: render(lines[:2])})
    ingest(db, root)
    (root / WFM).write_bytes(render(lines))
    ingest(db, root)
    other = tp.fresh_db(tmp_path, "whole")
    try:
        ingest(other, tp.write_folder(tmp_path / "W", {WFM: render(lines)}))
        assert snapshot(db) == snapshot(other)
    finally:
        other.close()
    assert db.execute("SELECT count(*) FROM event WHERE kind = 'level_window'").fetchone()[0] > 0


def test_failing_commit_surfaces_the_original_error_and_leaves_nothing_applied(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: render(basic_lines())})
    ingest(db, root)
    (stream_id,) = db.execute("SELECT id FROM stream").fetchone()
    before = (
        db.execute("SELECT * FROM event ORDER BY id").fetchall(),
        db.execute("SELECT id, event_id FROM entry ORDER BY id").fetchall(),
        db.execute("SELECT key, value FROM meta ORDER BY key").fetchall(),
    )
    # a deferred foreign key violated by a trigger on the engine's last UPDATE makes COMMIT fail
    db.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE t_parent(id INTEGER PRIMARY KEY);
        CREATE TABLE t_child(p INTEGER REFERENCES t_parent(id) DEFERRABLE INITIALLY DEFERRED);
        CREATE TRIGGER t_break AFTER UPDATE OF derive_checkpoint ON stream
        BEGIN INSERT INTO t_child(p) VALUES (999); END;
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        derive_stream(db, stream_id, catalog(), force=True)
    assert not db.in_transaction
    assert db.execute("SELECT count(*) FROM t_child").fetchone() == (0,)
    after = (
        db.execute("SELECT * FROM event ORDER BY id").fetchall(),
        db.execute("SELECT id, event_id FROM entry ORDER BY id").fetchall(),
        db.execute("SELECT key, value FROM meta ORDER BY key").fetchall(),
    )
    assert after == before
