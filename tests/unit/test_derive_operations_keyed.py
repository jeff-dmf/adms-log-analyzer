"""Operations deriver, pairing by a key other than the TID (``pair_by = ["job_id"]``): synthetic entries."""

from __future__ import annotations

from dmslog.derive import operations as ops_mod
from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.operations import OperationsDeriver

KEYED = [
    {
        "name": "Job",
        "start": ["JB-1"],
        "end": ["JB-9"],
        "end_fail": ["JB-8"],
        "fail": ["JB-7"],
        "pair_by": ["job_id"],
    }
]


class Feed:
    def __init__(self) -> None:
        self.n = 0
        self.entries: list[StoredEntry] = []

    def add(self, code, job, *, ts, tid=1) -> StoredEntry:
        self.n += 1
        fields = {} if job is None else {"job_id": job}
        keys = () if job is None else (("job_id", str(job)),)
        e = StoredEntry(
            id=self.n, stream_id=1, ts=ts, tid=tid, pid=1, level=4, sev=4, tpl_code=code, tpl_class="other",
            fields=fields, msg="m", keys=keys,
        )  # fmt: skip
        self.entries.append(e)
        return e


def derive(feed, **kw):
    d = OperationsDeriver("EXAMPLE", KEYED, **kw)
    return d, run_in_memory([d], feed.entries)


def test_pair_by_key_crosses_threads_and_collects_entries_by_key():
    f = Feed()
    s = f.add("JB-1", 7, ts=100, tid=1)
    other_job = f.add("X-1", 8, ts=105, tid=2)
    mid = f.add("X-1", 7, ts=110, tid=3)  # another thread, same job: joins
    same_tid_keyless = f.add("X-1", None, ts=115, tid=1)  # same thread, no key: does not join
    e = f.add("JB-9", 7, ts=150, tid=4)  # the end comes from another thread
    d, events = derive(f)
    (ev,) = events
    assert (ev.status, ev.key, ev.tid, ev.duration_ms) == ("ok", "7", 1, 50)
    assert ev.entry_ids == [s.id, mid.id, e.id]
    assert other_job.id not in ev.entry_ids and same_tid_keyless.id not in ev.entry_ids
    assert (d.orphan_ends, d.keyless_starts) == (0, 0)


def test_pair_by_key_keeps_two_jobs_open_on_one_thread():
    f = Feed()
    f.add("JB-1", 1, ts=0)
    f.add("JB-1", 2, ts=1)
    f.add("JB-9", 1, ts=5)
    f.add("JB-8", 2, ts=9)
    d, events = derive(f)
    assert [(e.key, e.status) for e in events] == [("1", "ok"), ("2", "fail")]
    assert d.overlaps == 0


def test_pair_by_key_keyless_start_is_ignored_and_counted():
    f = Feed()
    f.add("JB-1", None, ts=0)
    f.add("JB-9", None, ts=1)  # keyless end: orphan
    d, events = derive(f)
    assert events == [] and (d.keyless_starts, d.orphan_ends) == (1, 1)


def test_pair_by_key_new_start_truncates_fail_marks_and_eof():
    f = Feed()
    f.add("JB-1", 5, ts=0)
    f.add("JB-7", 5, ts=1)
    f.add("JB-1", 5, ts=2)  # same value again: the older is closed (fail seen)
    f.add("JB-1", 6, ts=3)
    d, events = derive(f)
    assert d.overlaps == 1
    assert [(e.key, e.status, e.detail.get("closed_by")) for e in events] == [
        ("5", "fail", "new_start"),
        ("5", "open", "eof"),
        ("6", "open", "eof"),
    ]


def test_pair_by_key_evicts_the_oldest(monkeypatch):
    monkeypatch.setattr(ops_mod, "MAX_OPEN", 2)
    f = Feed()
    for job in (1, 2, 3):
        f.add("JB-1", job, ts=job)
    f.add("JB-9", 1, ts=9)  # job 1 was evicted: orphan end
    d, events = derive(f)
    assert d.evicted == 1 and d.orphan_ends == 1
    assert (events[0].key, events[0].status, events[0].detail["closed_by"]) == ("1", "truncated", "evicted")
