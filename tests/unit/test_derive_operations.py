"""Operations deriver (ARQUITETURA 6.5 'operations'): synthetic entries, no database."""

from __future__ import annotations

from dmslog.derive import operations as ops_mod
from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.operations import OperationsDeriver

RULES = [
    {
        "name_field": "op",
        "start": ["EX-010"],
        "end_ok": ["EX-012"],
        "end": ["EX-013"],
        "end_fail": ["EX-014"],
        "fail": ["EX-081"],
        "pair_by": ["tid"],
        "check_key": "request_id",
        "business_error": ["GEN-ERR-01", "GEN-EVT-01"],
    },
    {"name": "Upload", "start": ["EX-020"], "end": ["EX-021"], "pair_by": ["tid"]},
]
GUID1 = "00000000-0000-0000-0000-000000000001"
GUID2 = "00000000-0000-0000-0000-000000000002"


class Feed:
    """Builds entries with ascending ids."""

    def __init__(self) -> None:
        self.n = 0
        self.entries: list[StoredEntry] = []

    def add(self, code=None, *, ts=0, tid=1, msg="m", fields=None, keys=(), cls="other") -> StoredEntry:
        self.n += 1
        e = StoredEntry(
            id=self.n, stream_id=1, ts=ts, tid=tid, pid=1, level=4, sev=4, tpl_code=code, tpl_class=cls,
            fields=fields or {}, msg=msg, keys=tuple(keys),
        )  # fmt: skip
        self.entries.append(e)
        return e


def derive(feed: Feed, **kw):
    d = OperationsDeriver("EXAMPLE", RULES, **kw)
    return d, run_in_memory([d], feed.entries)


def start(f, *, ts=0, tid=1, op="SendExample", guid=GUID1):
    return f.add("EX-010", ts=ts, tid=tid, fields={"op": op, "request_id": guid}, keys=[("request_id", guid)])


def end(f, code="EX-012", *, ts, tid=1, guid=GUID1, msg="done"):
    return f.add(code, ts=ts, tid=tid, msg=msg, keys=[("request_id", guid)])


def test_ok_with_duration_and_entry_ids():
    f = Feed()
    s = start(f, ts=1000)
    mid = f.add("X-1", ts=1010)  # same TID, no key: belongs to the operation
    other_tid = f.add("X-1", ts=1015, tid=2)  # another TID: does not
    e = end(f, ts=1250)
    after = f.add("X-1", ts=1300)
    _, events = derive(f)
    (ev,) = events
    assert (ev.kind, ev.name, ev.key, ev.tid, ev.status, ev.sev) == (
        "operation",
        "SendExample",
        GUID1,
        1,
        "ok",
        4,
    )
    assert (ev.start_entry_id, ev.end_entry_id) == (s.id, e.id)
    assert (ev.ts_start, ev.ts_end, ev.duration_ms) == (1000, 1250, 250)
    assert ev.entry_ids == [s.id, mid.id, e.id] and ev.n_entries == 3
    assert other_tid.id not in ev.entry_ids and after.id not in ev.entry_ids


def test_entries_with_another_key_do_not_join():
    f = Feed()
    s = start(f, ts=0)
    foreign = f.add("X-1", ts=5, keys=[("request_id", GUID2)])
    own = f.add("X-1", ts=6, keys=[("request_id", GUID1)])
    e = end(f, ts=10)
    _, (ev,) = derive(f)
    assert ev.entry_ids == [s.id, own.id, e.id] and foreign.id not in ev.entry_ids


def test_fail_code_inside_marks_fail_even_if_the_end_is_ok():
    f = Feed()
    start(f, ts=0)
    bad = f.add("EX-081", ts=5, msg="boom")
    end(f, ts=10)
    _, (ev,) = derive(f)
    assert ev.status == "fail" and ev.sev == 2
    assert ev.detail["fail_how"] == "fail_code" and ev.detail["fail_entry_id"] == bad.id
    assert ev.duration_ms == 10 and bad.id in ev.entry_ids


def test_failing_end_text_and_end_fail_code():
    f = Feed()
    start(f, ts=0)
    end(f, "EX-013", ts=5, msg="Operation finished unsuccessfully.")
    start(f, ts=10, guid=GUID2)
    end(f, "EX-014", ts=20, guid=GUID2)
    start(f, ts=30, tid=3)
    end(f, "EX-013", ts=35, tid=3, msg="Operation finished.")
    _, events = derive(f)
    assert [(e.status, e.detail.get("fail_how")) for e in events] == [
        ("fail", "end_text"),
        ("fail", "end_fail"),
        ("ok", None),
    ]


def test_business_error_needs_all_declared_codes_inside():
    f = Feed()
    start(f, ts=0)
    f.add("GEN-ERR-01", ts=1, fields={"codes": ["CodeA", "CodeB"]}, cls="validation")
    f.add("GEN-EVT-01", ts=2, cls="error")
    end(f, ts=3)
    start(f, ts=10, tid=2, guid=GUID2)
    f.add("GEN-ERR-01", ts=11, tid=2, fields={"codes": ["CodeC"]})
    end(f, ts=12, tid=2, guid=GUID2)
    _, (a, b) = derive(f)
    assert (a.status, a.sev) == ("business_error", 3) and a.detail["error_codes"] == ["CodeA", "CodeB"]
    assert b.status == "ok"  # only one of the two declared codes


def test_family_without_business_rule_never_reports_it():
    f = Feed()
    f.add("EX-020", ts=0)
    f.add("GEN-ERR-01", ts=1)
    f.add("GEN-EVT-01", ts=2)
    f.add("EX-021", ts=3)
    _, (ev,) = derive(f)
    assert ev.name == "Upload" and ev.status == "ok"


def test_open_at_eof_and_truncated_stream():
    f = Feed()
    s = start(f, ts=100)
    f.add("X-1", ts=110)
    d, (ev,) = derive(f)
    assert (ev.status, ev.sev, ev.end_entry_id, ev.ts_end, ev.duration_ms) == ("open", 3, None, None, None)
    assert ev.start_entry_id == s.id and ev.n_entries == 2
    _, (ev,) = derive(f, truncated=True)
    assert ev.status == "truncated"
    assert d.low_watermark() is None  # flushed


def test_fail_without_end_is_fail_not_open():
    f = Feed()
    start(f, ts=0)
    f.add("EX-081", ts=3)
    _, (ev,) = derive(f)
    assert ev.status == "fail" and ev.end_entry_id is None and ev.duration_ms is None


def test_flush_without_eof_keeps_the_state():
    f = Feed()
    start(f, ts=0)
    d = OperationsDeriver("EXAMPLE", RULES)
    for e in f.entries:
        d.feed(e)
    assert list(d.flush(eof=False)) == [] and d.low_watermark() == 1
    assert [e.status for e in d.flush(eof=True)] == ["open"]


def test_end_and_fail_without_start_are_ignored():
    f = Feed()
    f.add("EX-081", ts=0)
    end(f, ts=1)
    f.add("X-1", ts=2)
    d, events = derive(f)
    assert events == [] and (d.orphan_ends, d.orphan_fails) == (1, 1)


def test_same_tid_overlap_closes_the_older_as_truncated():
    f = Feed()
    s1 = start(f, ts=0)
    s2 = start(f, ts=10, guid=GUID2)
    e = end(f, ts=20, guid=GUID2)
    d, events = derive(f)
    old, new = events
    assert (old.status, old.start_entry_id, old.detail["closed_by"]) == ("truncated", s1.id, "new_start")
    assert (new.status, new.start_entry_id, new.end_entry_id) == ("ok", s2.id, e.id)
    assert "replaced_open" not in new.detail and d.overlaps == 1
    assert old.entry_ids == [s1.id] and new.entry_ids == [s2.id, e.id]


def test_key_mismatch_does_not_close():
    f = Feed()
    start(f, ts=0)
    end(f, ts=5, guid=GUID2)  # another request
    d, (ev,) = derive(f)
    assert ev.status == "open" and d.orphan_ends == 1


def test_name_field_mismatch_does_not_close():
    f = Feed()
    start(f, ts=0)
    f.add("EX-012", ts=5, fields={"op": "OtherOp"})
    d, (ev,) = derive(f)
    assert ev.status == "open" and d.orphan_ends == 1


def test_different_rules_and_tids_pair_independently():
    f = Feed()
    f.add("EX-020", ts=0, tid=1)
    start(f, ts=1, tid=2)
    f.add("EX-021", ts=5, tid=1)
    end(f, ts=9, tid=2)
    _, events = derive(f)
    assert sorted((e.name, e.tid, e.duration_ms) for e in events) == [("SendExample", 2, 8), ("Upload", 1, 5)]


def test_low_watermark_is_the_oldest_open_start():
    f = Feed()
    d = OperationsDeriver("EXAMPLE", RULES)
    assert d.low_watermark() is None
    a = start(f, ts=0, tid=1)
    b = start(f, ts=1, tid=2, guid=GUID2)
    for e in f.entries:
        d.feed(e)
    assert d.low_watermark() == a.id
    d.feed(end(f, ts=5, tid=1))
    assert d.low_watermark() == b.id
    d.feed(end(f, ts=6, tid=2, guid=GUID2))
    assert d.low_watermark() is None


def test_odd_input_never_raises():
    f = Feed()
    f.add("EX-010", ts=None, tid=None, fields={"op": ["not", "a", "str"]})
    f.add("EX-010", ts=50, tid=None)  # same slot (tid None): replaces
    f.add("X-1", ts=None, tid=None)
    f.add("EX-012", ts=40, tid=None)  # ts goes backwards
    f.add(None, ts=None, tid=7)
    d, events = derive(f)
    assert d.errors == 0
    first, second = events
    assert first.status == "truncated" and first.ts_start == 0
    assert second.status == "ok" and second.duration_ms is None and second.detail["ts_out_of_order"] is True


def test_caps_open_operations_and_entry_ids(monkeypatch):
    monkeypatch.setattr(ops_mod, "MAX_OPEN", 3)
    monkeypatch.setattr(ops_mod, "MAX_ENTRY_IDS", 4)
    f = Feed()
    for tid in range(1, 6):
        start(f, ts=tid, tid=tid, guid=f"g{tid}")
    d, events = derive(f)
    evicted = [e for e in events if e.detail.get("closed_by") == "evicted"]
    assert [e.tid for e in evicted] == [1, 2] and d.evicted == 2
    assert [e.status for e in events if e.tid in (3, 4, 5)] == ["open"] * 3

    f = Feed()
    start(f, ts=0)
    for i in range(10):
        f.add("X-1", ts=1 + i)
    _, (ev,) = derive(f)
    assert len(ev.entry_ids) == 4 and ev.n_entries == 11 and ev.detail["entry_ids_dropped"] == 7


def test_exception_first_line_goes_to_detail():
    f = Feed()
    start(f, ts=0)
    f.add("GEN-EXC-01", ts=1, fields={"exc_type": "System.ExampleException", "exc_msg": "bad\nsecond line"})
    end(f, ts=2)
    _, (ev,) = derive(f)
    assert ev.detail["exception"] == "System.ExampleException: bad"


def test_without_rules_or_with_a_plain_ruleset():
    f = Feed()
    start(f, ts=0)
    assert run_in_memory([OperationsDeriver("EXAMPLE")], f.entries) == []
    assert run_in_memory([OperationsDeriver("EXAMPLE", None)], f.entries) == []


def test_seen_fail_wins_over_truncation_on_new_start_eviction_and_eof(monkeypatch):
    f = Feed()
    start(f, ts=0)
    f.add("EX-081", ts=1)
    start(f, ts=10, guid=GUID2)  # same TID: the failed one is closed by the new start
    _, (old, new) = derive(f)
    assert (old.status, old.sev, old.detail["closed_by"]) == ("fail", 2, "new_start")
    assert new.status == "open" and new.detail["closed_by"] == "eof"

    monkeypatch.setattr(ops_mod, "MAX_OPEN", 1)
    f = Feed()
    start(f, ts=0, tid=1)
    f.add("EX-081", ts=1, tid=1)
    start(f, ts=2, tid=2, guid=GUID2)
    _, events = derive(f)
    evicted = next(e for e in events if e.detail.get("closed_by") == "evicted")
    assert evicted.status == "fail" and evicted.tid == 1

    f = Feed()
    start(f, ts=0)
    f.add("EX-081", ts=1)
    _, (eof,) = derive(f, truncated=True)
    assert (eof.status, eof.detail["closed_by"]) == ("fail", "eof")


def test_end_shared_by_two_rules_is_orphan_only_if_no_rule_has_it_open():
    shared = [
        {"name": "A", "start": ["A-1"], "end": ["SH-1"], "fail": ["SH-F"]},
        {"name": "B", "start": ["B-1"], "end": ["SH-1"], "fail": ["SH-F"]},
    ]
    f = Feed()
    f.add("B-1", ts=0)
    f.add("SH-1", ts=5)  # rule A has nothing open, rule B closes it: not an orphan
    f.add("SH-F", ts=6)  # nothing open at all: one orphan fail
    f.add("SH-1", ts=7)  # nothing open at all: one orphan end
    d = OperationsDeriver("EXAMPLE", shared)
    events = run_in_memory([d], f.entries)
    assert [e.name for e in events] == ["B"] and (d.orphan_ends, d.orphan_fails) == (1, 1)
