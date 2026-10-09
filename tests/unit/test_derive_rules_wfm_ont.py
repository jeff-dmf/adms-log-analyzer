"""[[operation]] rules of AWFM and AONT, run through OperationsDeriver with the packaged catalog.

All data is synthetic. Entries are built by hand with the template codes, fields and keys the
extractor produces for the documented messages (docs/formato/wfm-ont.md).
"""

from __future__ import annotations

import pytest

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.operations import OperationsDeriver
from dmslog.parse import load_catalog

G1 = "00000000-0000-4000-8000-000000000001"
G2 = "00000000-0000-4000-8000-000000000002"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


class Feed:
    def __init__(self) -> None:
        self.n = 0
        self.entries: list[StoredEntry] = []

    def add(self, code, *, ts, tid=1, fields=None, keys=(), msg="m") -> StoredEntry:
        self.n += 1
        e = StoredEntry(
            id=self.n, stream_id=1, ts=ts, tid=tid, pid=1, level=4, sev=4, tpl_code=code, tpl_class=None,
            fields=fields or {}, msg=msg, keys=tuple(keys),
        )  # fmt: skip
        self.entries.append(e)
        return e


def derive(catalog, family: str, feed: Feed, **kw):
    rules = catalog.for_family(family, sensitive=False)
    d = OperationsDeriver(family, rules, **kw)
    return d, run_in_memory([d], feed.entries)


def wfm_start(f, *, ts, op="Changed crews", guid=G1, tid=1):
    return f.add("WFM-010", ts=ts, tid=tid, fields={"op": op, "request_id": guid}, keys=[("msgid", guid)])


def wfm_end(f, *, ts, op="Changed crews", guid=G1, tid=1):
    return f.add("WFM-012", ts=ts, tid=tid, fields={"op": op, "request_id": guid}, keys=[("msgid", guid)])


def test_rules_are_declared_with_canonical_codes(catalog):
    wfm = catalog.for_family("AWFM", sensitive=False).operations()
    assert [r.get("name") or r.get("name_field") for r in wfm] == ["op", "Created crew model"]
    for r in wfm:
        assert r["business_error"] == ["GEN-ERR-01", "GEN-EVT-01"]
        assert r["end_fail"] == ["WFM-081"]
    ont = catalog.for_family("AONT", sensitive=False).operations()
    assert [r["name"] for r in ont] == ["SendIncident", "SendIncidentTroubleTickets"]


# ---------------------------------------------------------------------------------------- AWFM


def test_wfm_request_ok_with_duration_and_name(catalog):
    f = Feed()
    s = wfm_start(f, ts=1000, op="Changed vehicles")
    f.add("WFM-040", ts=1100)  # unrelated line of the same thread belongs to the operation
    e = wfm_end(f, ts=3500, op="Changed vehicles")
    _, events = derive(catalog, "AWFM", f)
    assert len(events) == 1
    ev = events[0]
    assert (ev.kind, ev.name, ev.status, ev.key) == ("operation", "Changed vehicles", "ok", G1)
    assert ev.duration_ms == 2500
    assert (ev.start_entry_id, ev.end_entry_id) == (s.id, e.id)
    assert ev.entry_ids == [s.id, 2, e.id]


def test_wfm_other_request_on_same_thread_does_not_close(catalog):
    f = Feed()
    wfm_start(f, ts=0)
    wfm_end(f, ts=10, guid=G2)  # another request id: not this operation's end
    _, events = derive(catalog, "AWFM", f)
    assert [(e.status, e.end_entry_id) for e in events] == [("open", None)]


def test_wfm_business_error_needs_codes_and_system_event(catalog):
    f = Feed()
    wfm_start(f, ts=0)
    f.add("GEN-ERR-01", ts=5, fields={"codes": ["CodeOne", "CodeTwo"]})
    f.add("GEN-ERR-02", ts=6)
    f.add("GEN-EVT-01", ts=7, fields={"op": "Changed crews"})
    wfm_end(f, ts=20)
    _, events = derive(catalog, "AWFM", f)
    assert [e.status for e in events] == ["business_error"]
    assert events[0].duration_ms == 20
    assert events[0].detail["error_codes"] == ["CodeOne", "CodeTwo"]


def test_wfm_detected_errors_without_system_event_stay_ok(catalog):
    # Info-only codes: Detected errors without the SystemEventSender line (documented case).
    f = Feed()
    wfm_start(f, ts=0)
    f.add("GEN-ERR-01", ts=5, fields={"codes": ["InfoCode"]})
    wfm_end(f, ts=20)
    _, events = derive(catalog, "AWFM", f)
    assert [e.status for e in events] == ["ok"]


def test_wfm_processing_failure_closes_as_fail(catalog):
    f = Feed()
    wfm_start(f, ts=0, op="Changed crews")
    f.add("WFM-080", ts=35000)
    fail = f.add(
        "WFM-081", ts=35010, fields={"event_type": "ChangedCrewsEvent", "exc_type": "System.TimeoutException"}
    )
    _, events = derive(catalog, "AWFM", f)
    assert len(events) == 1
    ev = events[0]
    assert (ev.status, ev.name, ev.duration_ms, ev.end_entry_id) == ("fail", "Changed crews", 35010, fail.id)
    assert ev.detail["fail_how"] == "end_fail"


def test_wfm_failure_then_next_request_on_same_thread(catalog):
    f = Feed()
    wfm_start(f, ts=0, guid=G1)
    f.add("WFM-081", ts=100, fields={"exc_type": "X"})
    wfm_start(f, ts=200, guid=G2)
    wfm_end(f, ts=300, guid=G2)
    _, events = derive(catalog, "AWFM", f)
    assert [(e.key, e.status) for e in events] == [(G1, "fail"), (G2, "ok")]


def test_wfm_two_threads_interleaved(catalog):
    f = Feed()
    wfm_start(f, ts=0, tid=1, guid=G1, op="Changed crews")
    wfm_start(f, ts=10, tid=2, guid=G2, op="Created crew assignments")
    wfm_end(f, ts=50, tid=2, guid=G2, op="Created crew assignments")
    wfm_end(f, ts=70, tid=1, guid=G1, op="Changed crews")
    _, events = derive(catalog, "AWFM", f)
    got = {(e.name, e.status, e.duration_ms) for e in events}
    assert got == {("Created crew assignments", "ok", 40), ("Changed crews", "ok", 70)}


def test_wfm_created_crew_model(catalog):
    f = Feed()
    f.add("WFM-011", ts=0, fields={"request_id": G1}, keys=[("msgid", G1)])
    f.add("WFM-013", ts=10, fields={"phase": "started"})
    f.add("WFM-013", ts=900, fields={"phase": "finished"})
    wfm_end(f, ts=1000, op="Created crew model")
    d, events = derive(catalog, "AWFM", f)
    assert [(e.name, e.status, e.duration_ms) for e in events] == [("Created crew model", "ok", 1000)]
    assert d.low_watermark() is None


def test_wfm_end_without_start_is_orphan_and_open_at_eof(catalog):
    f = Feed()
    wfm_end(f, ts=10)
    wfm_start(f, ts=20, guid=G2, tid=3)
    d, events = derive(catalog, "AWFM", f)
    assert d.orphan_ends >= 1
    assert [e.status for e in events] == ["open"]
    _, events = derive(catalog, "AWFM", f, truncated=True)
    assert [e.status for e in events] == ["truncated"]


# ---------------------------------------------------------------------------------------- AONT


def ont_start(f, *, ts, mrid="INC 1001", tid=1, code="ONT-010"):
    return f.add(code, ts=ts, tid=tid, fields={"mrid": mrid}, keys=[("mrid", mrid)])


def ont_msg(f, code, *, ts, tid=1, guid=G1):
    return f.add(code, ts=ts, tid=tid, fields={"message_id": guid}, keys=[("msgid", guid)])


def test_ont_send_incident_ok(catalog):
    f = Feed()
    s = ont_start(f, ts=1000)
    ont_msg(f, "ONT-011", ts=1010)
    ont_msg(f, "ONT-012", ts=1200)
    f.add("ONT-013", ts=1201)
    e = ont_msg(f, "ONT-014", ts=1210)
    _, events = derive(catalog, "AONT", f)
    assert len(events) == 1
    ev = events[0]
    assert (ev.name, ev.status, ev.key, ev.duration_ms) == ("SendIncident", "ok", "INC 1001", 210)
    assert (ev.start_entry_id, ev.end_entry_id, ev.n_entries) == (s.id, e.id, 5)


def test_ont_send_incident_failure_after_retries(catalog):
    f = Feed()
    ont_start(f, ts=0)
    for i in range(5):  # one ONT-011/ONT-020 pair per attempt; the retries do not fail the send alone
        ont_msg(f, "ONT-011", ts=100 + i * 45000)
        f.add("ONT-020", ts=100 + i * 45000 + 5, fields={"msg_type": "IncidentsEventMessageType"})
    ont_msg(f, "ONT-022", ts=225200)
    last = f.add("ONT-021", ts=225210, fields={"reason": "x"})
    _, events = derive(catalog, "AONT", f)
    assert len(events) == 1
    ev = events[0]
    assert (ev.name, ev.status, ev.duration_ms, ev.end_entry_id) == ("SendIncident", "fail", 225210, last.id)


def test_ont_retries_that_end_well_are_ok(catalog):
    f = Feed()
    ont_start(f, ts=0)
    ont_msg(f, "ONT-011", ts=10)
    f.add("ONT-020", ts=50, fields={"msg_type": "IncidentsEventMessageType"})
    ont_msg(f, "ONT-011", ts=60)
    ont_msg(f, "ONT-014", ts=90)
    _, events = derive(catalog, "AONT", f)
    assert [e.status for e in events] == ["ok"]


def test_ont_failure_marker_before_terminal_message_still_fails(catalog):
    f = Feed()
    ont_start(f, ts=0)
    ont_msg(f, "ONT-022", ts=10)
    f.add("ONT-021", ts=20)
    _, events = derive(catalog, "AONT", f)
    assert [(e.status, e.detail["fail_how"]) for e in events] == [("fail", "fail_code")]


def test_ont_trouble_tickets_ok_and_independent_of_incident(catalog):
    f = Feed()
    ont_start(f, ts=0, mrid="INC 2002", code="ONT-015", tid=7)
    ont_msg(f, "ONT-016A", ts=5, tid=7, guid=G2)
    ont_msg(f, "ONT-016B", ts=100, tid=7, guid=G2)
    f.add("ONT-017", ts=101, tid=7)
    ont_msg(f, "ONT-014", ts=110, tid=7, guid=G2)
    _, events = derive(catalog, "AONT", f)
    assert [(e.name, e.key, e.status, e.duration_ms) for e in events] == [
        ("SendIncidentTroubleTickets", "INC 2002", "ok", 110)
    ]


def test_ont_second_incident_on_busy_thread_truncates_the_first(catalog):
    f = Feed()
    ont_start(f, ts=0, mrid="INC 1")
    ont_start(f, ts=50, mrid="INC 2")
    ont_msg(f, "ONT-014", ts=80)
    _, events = derive(catalog, "AONT", f)
    by_key = {e.key: e for e in events}
    assert by_key["INC 1"].status == "truncated"
    assert by_key["INC 1"].detail["closed_by"] == "new_start"
    assert (by_key["INC 2"].status, by_key["INC 2"].duration_ms) == ("ok", 30)
