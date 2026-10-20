"""Unit tests of the pid_change rule of the life-cycle deriver (synthetic entries)."""

from __future__ import annotations

from dmslog.derive.base import Event, StoredEntry, run_in_memory
from dmslog.derive.lifecycle import KIND_RESTART, LifecycleDeriver

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


def test_old_pid_returning_after_restart_is_ignored() -> None:
    (ev,) = restarts([10, 20, 20, 10, 10, 20])
    assert ev.detail["new_pid"] == 20


def test_two_successive_restarts() -> None:
    evs = restarts([10, 10, 20, 20, 30, 30])
    assert [(e.detail["old_pid"], e.detail["new_pid"]) for e in evs] == [(10, 20), (20, 30)]


def test_none_pid_ignored_and_trailing_candidate_not_confirmed() -> None:
    assert restarts([10, None, 10, 20]) == []
    assert len(restarts([10, None, 20, None, 20])) == 1


def test_rotation_is_separate_streams() -> None:
    # each file is its own stream: a different PID in the next file is not compared
    assert restarts([10, 10, 10]) == []
    assert restarts([20, 20, 20]) == []


def test_other_family_unaffected() -> None:
    assert restarts([10, 10, 20, 20], family="DMSJOB") == []


def test_resume_from_watermark_same_events() -> None:
    es = entries([10, 10, 20, 20, 30, 30])
    full = restarts([10, 10, 20, 20, 30, 30])
    d = LifecycleDeriver("DMSFH")
    got: list[Event] = []
    for e in es[:3]:
        got.extend(d.feed(e))
    wm = d.low_watermark()
    assert wm is not None
    d2 = LifecycleDeriver("DMSFH", stream_start=False)
    for e in es:
        if e.id >= wm:
            got.extend(d2.feed(e))
    ours = [(e.start_entry_id, e.detail["new_pid"]) for e in got if e.kind == KIND_RESTART]
    assert ours == [(e.start_entry_id, e.detail["new_pid"]) for e in full]
