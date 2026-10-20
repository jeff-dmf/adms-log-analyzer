"""Unit tests of the pid_change rule of the life-cycle deriver (synthetic entries)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from dmslog.derive.base import Event, StoredEntry, run_in_memory
from dmslog.derive.engine import derive_stream
from dmslog.derive.lifecycle import KIND_RESTART, LifecycleDeriver
from dmslog.parse.templates import load_texts
from dmslog.store.db import create_case_db

T0 = 1_700_000_000_000


def entries(pids: list[int | None]) -> list[StoredEntry]:
    return [
        StoredEntry(
            id=i + 1,
            stream_id=1,
            ts=T0 + i * 1000,
            tid=5,
            pid=pid,
            level=None,
            sev=4,
            tpl_code=None,
            tpl_class=None,
            fields={},
            msg="x",
            cont="",
        )
        for i, pid in enumerate(pids)
    ]


def restarts(pids: list[int | None], family: str = "DMSFH") -> list[Event]:
    evs = run_in_memory([LifecycleDeriver(family)], entries(pids))
    return [e for e in evs if e.kind == KIND_RESTART]


def test_stable_pid_no_restart() -> None:
    assert restarts([10] * 6) == []


def test_simple_change() -> None:
    (ev,) = restarts([10, 10, 20, 20, 20])
    assert ev.detail == {"old_pid": 10, "new_pid": 20, "cause": "pid_change"}
    assert ev.start_entry_id == 3
    assert ev.ts_start == T0 + 2000


def test_a_b_a_is_interleaving() -> None:
    assert restarts([10, 10, 20, 10, 10]) == []


def test_old_pid_returning_after_restart_is_a_restart_back() -> None:
    evs = restarts([10, 20, 20, 10, 10, 20])
    assert [(e.detail["old_pid"], e.detail["new_pid"]) for e in evs] == [(10, 20), (20, 10)]


def test_two_successive_restarts() -> None:
    evs = restarts([10, 10, 20, 20, 30, 30])
    assert [(e.detail["old_pid"], e.detail["new_pid"]) for e in evs] == [(10, 20), (20, 30)]


def test_none_pid_ignored_and_trailing_candidate_not_confirmed() -> None:
    assert restarts([10, None, 10, 20]) == []
    assert len(restarts([10, None, 20, None, 20])) == 1


def test_rotation_is_separate_streams() -> None:
    # each chain is its own stream: a different PID in another chain is not compared
    assert restarts([10, 10, 10]) == []
    assert restarts([20, 20, 20]) == []


def test_other_family_unaffected() -> None:
    assert restarts([10, 10, 20, 20], family="DMSJOB") == []


def _resumed(es: list[StoredEntry], cut: int) -> list[tuple[int | None, int]]:
    d = LifecycleDeriver("DMSFH")
    got: list[Event] = []
    for e in es[:cut]:
        got.extend(d.feed(e))
    wm = d.low_watermark()
    assert wm is not None
    got = [ev for ev in got if (ev.start_entry_id or 0) < wm]
    d2 = LifecycleDeriver("DMSFH", stream_start=False)
    for e in es:
        if e.id >= wm:
            got.extend(d2.feed(e))
    got.extend(d2.flush(eof=True))
    return [(e.start_entry_id, e.detail["new_pid"]) for e in got if e.kind == KIND_RESTART]


PID_CASES = [
    [10, 10, 20, 20, 20],
    [10, 10, 20, 10, 10, 20, 20],
    [10, None, 20, None, 20, 30, 30, 30],
    [10, 20, 30, 30, 10, 10],
    [10, 10, 20, 20, None, None],
]


@pytest.mark.parametrize("pids", PID_CASES)
def test_resume_at_every_cut_equals_full_run(pids: list[int | None]) -> None:
    es = entries(pids)
    full = [(e.start_entry_id, e.detail["new_pid"]) for e in restarts(pids)]
    for cut in range(1, len(pids)):
        assert _resumed(es, cut) == full, cut


def test_resume_with_level_change_before_pending_candidate() -> None:
    es = entries([10, 10, 20, 20, 20])
    es[2] = replace(es[2], msg="Debug level changed from Info (5) to Debug (3).")
    assert _resumed(es, 3) == [(3, 20)]


def test_pid_reaches_the_derivers_through_the_sqlite_engine(tmp_path) -> None:
    pids = [10, 10, 20, 20, 20, 30, 30]

    def build(name: str):
        conn = create_case_db(tmp_path / name / "case.sqlite")
        conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')")
        sid = conn.execute(
            "INSERT INTO stream(collection_group, family, process, sensitive)"
            " VALUES (1, 'DMSFH', 'DMS_FunctionHost_1', 0)"
        ).lastrowid
        conn.execute(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope, status)"
            " VALUES (1, 1, ?, 'm', 'DMS_FunctionHost_1.log', 'adms', 'ready')",
            (sid,),
        )
        return conn, sid

    def add(conn, lo: int, hi: int) -> None:
        for i in range(lo, hi):
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id,"
                " flags, msg) VALUES (?,1,?,?,1,?,?,5,4,0,0,'x')",
                (i + 1, i + 1, i + 1, T0 + i * 1000, pids[i]),
            )
        conn.execute("UPDATE source SET n_entries = ?", (hi,))
        conn.commit()

    def restarts_of(conn):
        return conn.execute(
            "SELECT start_entry_id, detail FROM event WHERE kind = 'restart' ORDER BY start_entry_id"
        ).fetchall()

    cat = load_texts({})
    conn, sid = build("full")
    add(conn, 0, len(pids))
    derive_stream(conn, sid, cat)
    full = restarts_of(conn)
    assert [r[0] for r in full] == [3, 6]
    assert json.loads(full[0][1]) == {"old_pid": 10, "new_pid": 20, "cause": "pid_change"}
    conn.close()
    for cut in range(1, len(pids)):
        conn, sid = build(f"cut{cut}")
        add(conn, 0, cut)
        derive_stream(conn, sid, cat)
        add(conn, cut, len(pids))
        derive_stream(conn, sid, cat)
        assert restarts_of(conn) == full, cut
        conn.close()
