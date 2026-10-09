"""derive/payloads.py: announcement, operation and pair linking on hand-built cases (synthetic data only)."""

from __future__ import annotations

import json

import pytest

from dmslog.derive import payloads as pl
from dmslog.derive.payloads import link_payloads
from dmslog.parse.templates import load_texts
from dmslog.store.db import create_case_db

TOML = """
schema = 1
family = "AWFM"

[[template]]
code = "GEN-MSG-01"
class = "other"
refs_sensitive = true
regex = '''^Message with MessageID: '(?P<message_id>[^']*)' will be logged in sensitive log\\.$'''

[[template]]
code = "GEN-PLD-01"
class = "req_resp"
stream = "sensitive"
text = "payload"

[[template]]
code = "WFM-112"
class = "other"
refs_sensitive = true
text = "delta announcement"

[[template]]
code = "WFM-S112"
class = "other"
stream = "sensitive"
text = "delta"

[[template]]
code = "WFM-120"
class = "other"
stream = "normal"
text = "content"

[[template]]
code = "SMN-110"
class = "other"
text = "publication announcement"

[[template]]
code = "SMN-S110"
class = "other"
stream = "sensitive"
text = "publication"

[[template]]
code = "TST-ANN"
class = "other"
text = "custom announcement"

[[template]]
code = "TST-PLD"
class = "other"
stream = "sensitive"
text = "custom payload"

[[template]]
code = "TST-START"
class = "start"
text = "start"

[[sensitive_pair]]
normal = "TST-ANN"
sensitive = "TST-PLD"
match = ["pid", "tid"]
"""

G = [f"00000000-0000-4000-8000-{n:012x}" for n in range(1, 30)]


class Case:
    """A case database filled by hand: one process (normal + sensitive streams), entries, events, payloads."""

    def __init__(self, tmp_path):
        self.conn = create_case_db(tmp_path / "case.sqlite")
        self.conn.isolation_level = None
        self.cat = load_texts({"awfm.toml": TOML})
        self.conn.execute(
            "INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', '2026-01-01')"
        )
        for t in self.cat.templates():
            self.conn.execute(
                "INSERT INTO template(id, code, family, class, pattern, def_version) VALUES (?,?,?,?,?,'v1')",
                (self.cat.tpl_id(t.code), t.code, t.family, t.cls, t.text or ""),
            )
        self.streams: dict[tuple, int] = {}
        self.sources: dict[int, int] = {}
        self.next_id = 1

    def stream(self, sensitive=0, group=1, pid=100, family="AWFM", process="WfmAdapter") -> int:
        k = (group, family, process, pid, sensitive)
        if k not in self.streams:
            sid = self.conn.execute(
                "INSERT INTO stream(collection_group, family, process, pid, sensitive) VALUES (?,?,?,?,?)", k
            ).lastrowid
            self.sources[sid] = self.conn.execute(
                "INSERT INTO source(collection_id, stream_id, member, file_name, envelope, status)"
                " VALUES (1, ?, ?, ?, 'adms', 'ready')",
                (sid, f"m{sid}", f"f{sid}.log"),
            ).lastrowid
            self.streams[k] = sid
        return self.streams[k]

    def entry(
        self, code, ts, *, tid=1, sensitive=0, msg=None, event=None, key=None, fields=None, pid=100, group=1
    ):
        sid = self.stream(sensitive, group=group, pid=pid)
        eid = self.next_id
        self.next_id += 1
        self.conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " fields, msg, event_id) VALUES (?,?,?,?,1,?,?,?,4,?,0,?,?,?)",
            (eid, self.sources[sid], eid, eid, ts, pid, tid, self.cat.tpl_id(code),
             json.dumps(fields) if fields else None, msg if msg is not None else code, event),
        )  # fmt: skip
        if key is not None:
            self.conn.execute(
                "INSERT INTO entry_key(key_type, value, entry_id) VALUES ('msgid', ?, ?)", (key, eid)
            )
        return eid

    def announce(self, ts, ident, *, tid=1, code="GEN-MSG-01", pid=100, group=1):
        msg = (
            f"Message with MessageID: '{ident}' will be logged in sensitive log."
            if ident is not None
            else code
        )
        return self.entry(code, ts, tid=tid, msg=msg, pid=pid, group=group)

    def payload(
        self, code, ts, role, *, tid=1, sensitive=1, root="Things", mid=None, corr=None, pid=100, group=1
    ):
        eid = self.entry(code, ts, tid=tid, sensitive=sensitive, pid=pid, group=group)
        pid_ = self.conn.execute(
            "INSERT INTO payload(entry_id, role, direction, operation, msg_id, corr_id, fmt, root, char_off,"
            " char_len, well_formed, truncated) VALUES (?,?,?,?,?,?,'xml',?,0,10,1,0)",
            (eid, role, "in", root, mid, corr, root),
        ).lastrowid
        return pid_, eid

    def operation(
        self, name, ts_start, ts_end, *, status="ok", tid=9, key=None, msg_type=None, extra_entries=0
    ):
        """An operation event with its start entry (carrying the msgid key) and an optional typed record."""
        sid = self.stream(0)
        ev = self.conn.execute(
            "INSERT INTO event(stream_id, kind, name, key, tid, ts_start, ts_end, duration_ms, status, sev)"
            " VALUES (?, 'operation', ?, ?, ?, ?, ?, ?, ?, 4)",
            (sid, name, key, tid, ts_start, ts_end, ts_end - ts_start, status),
        ).lastrowid
        start = self.entry("TST-START", ts_start, tid=tid, event=ev, key=key)
        if msg_type:
            self.entry("TST-START", ts_start + 1, tid=tid, event=ev, fields={"msg_type": msg_type})
        for _ in range(extra_entries):
            self.entry("TST-START", ts_start + 1, tid=tid, event=ev)
        self.conn.execute("UPDATE event SET start_entry_id = ? WHERE id = ?", (start, ev))
        return ev

    def row(self, payload_id):
        names = "event_id, event_conf, ref_entry_id, pair_id, operation"
        return self.conn.execute(f"SELECT {names} FROM payload WHERE id = ?", (payload_id,)).fetchone()

    def links(self, kind="normal_sensitive"):
        return self.conn.execute(
            "SELECT a_entry_id, b_entry_id, confidence FROM link WHERE kind = ? ORDER BY 1, 2", (kind,)
        ).fetchall()

    def run(self):
        return link_payloads(self.conn, self.cat)


@pytest.fixture
def case(tmp_path):
    c = Case(tmp_path)
    yield c
    c.conn.close()


# --------------------------------------------------------------------------- rule 1


def test_announcement_by_tid_time_and_message_id(case):
    a = case.announce(1000, G[0], tid=5)
    p, pe = case.payload("GEN-PLD-01", 1000, "request", tid=5, mid=G[0])
    case.run()
    assert case.row(p)[2] == a
    assert case.links() == [(a, pe, 3)]


def test_announcement_without_id_on_both_sides_has_confidence_two(case):
    a = case.announce(1000, "", tid=5)
    p, pe = case.payload("GEN-PLD-01", 1010, "response", tid=5)
    case.run()
    assert case.row(p)[2] == a and case.links() == [(a, pe, 2)]


def test_announcement_checks_the_id_tid_and_time(case):
    case.announce(1000, G[0], tid=5)
    other_id, _ = case.payload("GEN-PLD-01", 1000, "request", tid=5, mid=G[1])
    other_tid, _ = case.payload("GEN-PLD-01", 1000, "request", tid=6, mid=G[0])
    too_late, _ = case.payload("GEN-PLD-01", 2001, "request", tid=5, mid=G[0])
    empty_vs_id, _ = case.payload("GEN-PLD-01", 1000, "response", tid=5)
    case.run()
    for p in (other_id, other_tid, too_late, empty_vs_id):
        assert case.row(p)[2] is None
    assert case.links() == []


def test_announcement_may_be_logged_after_the_payload_and_is_used_once(case):
    a = case.announce(1500, G[0], tid=5)
    p1, _ = case.payload("GEN-PLD-01", 1000, "request", tid=5, mid=G[0])
    p2, _ = case.payload("GEN-PLD-01", 1001, "request", tid=5, mid=G[0])
    case.run()
    assert sorted(case.row(p)[2] for p in (p1, p2) if case.row(p)[2]) == [a]  # one payload per announcement


def test_nearest_announcement_wins(case):
    case.announce(500, "", tid=5)
    near = case.announce(990, "", tid=5)
    p, _ = case.payload("GEN-PLD-01", 1000, "response", tid=5)
    case.run()
    assert case.row(p)[2] == near


def test_announcers_come_from_the_catalog_pairs_and_the_defaults(case):
    a1 = case.announce(1000, None, tid=5, code="TST-ANN")
    p1, _ = case.payload("TST-PLD", 1000, "other", tid=5)
    a2 = case.announce(2000, None, tid=5, code="WFM-112")
    p2, _ = case.payload("WFM-S112", 2000, "delta", tid=5)
    wrong = case.announce(3000, None, tid=5, code="WFM-112")  # not the announcer of GEN-PLD-01
    p3, _ = case.payload("GEN-PLD-01", 3000, "response", tid=5)
    case.run()
    assert case.row(p1)[2] == a1 and case.row(p2)[2] == a2 and case.row(p3)[2] is None
    assert wrong  # the announcement exists but belongs to another payload template


def test_announcement_never_crosses_processes(case):
    case.announce(1000, "", tid=5, pid=200)
    p, _ = case.payload("GEN-PLD-01", 1000, "response", tid=5, pid=100)
    case.run()
    assert case.row(p)[2] is None


def test_existing_links_are_kept(case):
    a = case.announce(1000, "", tid=5)
    p, pe = case.payload("GEN-PLD-01", 1000, "response", tid=5)
    case.conn.execute("INSERT INTO link VALUES (?, ?, 'normal_sensitive', 3)", (a, pe))
    case.run()
    assert case.links() == [(a, pe, 3)]


# --------------------------------------------------------------------------- rules 2 and 3


def test_request_is_linked_to_the_operation_by_message_id(case):
    ev = case.operation("Changed crews", 900, 1200, key=G[3])
    p, _ = case.payload("GEN-PLD-01", 1000, "request", tid=77, mid=G[3], root="ChangedCrews")
    s = case.run()
    assert case.row(p) == (ev, 3, None, None, "Changed crews")
    assert (s.linked, s.by_rule) == (1, {2: 1})


def test_message_id_is_compared_without_case(case):
    ev = case.operation("Op", 900, 1200, key=G[3].lower())
    p, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3].upper())
    case.run()
    assert case.row(p)[0] == ev


def test_with_several_operations_with_the_key_the_nearest_in_time_wins(case):
    case.operation("Op", 100, 200, key=G[3])
    near = case.operation("Op", 5000, 5100, key=G[3])
    p, _ = case.payload("GEN-PLD-01", 5050, "request", mid=G[3])
    case.run()
    assert case.row(p)[0] == near


def test_request_pairs_with_response_by_correlation_id_not_by_tid(case):
    ev = case.operation("Changed crews", 900, 1200, key=G[3])
    req, _ = case.payload("GEN-PLD-01", 1000, "request", tid=1, mid=G[3])
    decoy, _ = case.payload(
        "GEN-PLD-01", 1001, "response", tid=1, mid=G[5], corr=G[9], root="XResponseMessage"
    )
    resp, _ = case.payload(
        "GEN-PLD-01", 1100, "response", tid=2, mid=G[4], corr=G[3], root="XResponseMessage"
    )
    s = case.run()
    assert case.row(req)[3] == resp and case.row(resp)[3] == req  # same pair, different TIDs
    assert case.row(resp)[:2] == (ev, 3)
    assert case.row(decoy)[3] is None and case.row(decoy)[0] is None  # same TID as the request: not paired
    assert s.by_rule[3] == 1 and s.orphans[pl.R_NO_REQUEST] == 1


def test_response_without_a_request_payload_uses_the_operation_key(case):
    ev = case.operation("Op", 900, 1200, key=G[3])
    resp, _ = case.payload("GEN-PLD-01", 1100, "response", mid=G[4], corr=G[3], root="XResponseMessage")
    case.run()
    assert case.row(resp) == (ev, 3, None, None, "Op")


def test_request_without_id_or_operation_is_an_orphan_with_a_reason(case):
    r1, _ = case.payload("GEN-PLD-01", 1000, "request")
    r2, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[7])
    s = case.run()
    assert case.row(r1)[0] is None and case.row(r2)[0] is None
    assert s.orphans == {pl.R_NO_ID: 1, pl.R_NO_OPERATION: 1}


def test_key_of_an_operation_of_another_process_is_ignored(case):
    other_stream = case.stream(0, pid=200)
    ev = case.conn.execute(
        "INSERT INTO event(stream_id, kind, name, ts_start, ts_end, status, sev)"
        " VALUES (?, 'operation', 'Op', 900, 1200, 'ok', 4)",
        (other_stream,),
    ).lastrowid
    case.entry("TST-START", 900, event=ev, key=G[3], pid=200)
    p, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3], pid=100)
    case.run()
    assert case.row(p)[0] is None


# --------------------------------------------------------------------------- rule 4 and 5


def test_response_without_ids_by_operation_end_and_type(case):
    ev = case.operation("SendIncident", 900, 1000, key=G[3], msg_type="IncidentsEventMessageType")
    req, _ = case.payload("GEN-PLD-01", 950, "request", mid=G[3])
    resp, _ = case.payload("GEN-PLD-01", 1010, "response", root="ns1:IncidentsResponseMessage")
    s = case.run()
    assert case.row(resp) == (ev, 1, None, req, "SendIncident")
    assert case.row(req)[3] == resp
    assert s.by_rule[4] == 1


def test_response_outside_16_ms_or_of_another_type_is_not_linked(case):
    case.operation("SendIncident", 900, 1000, msg_type="IncidentsEventMessageType")
    late, _ = case.payload("GEN-PLD-01", 1017, "response", root="ns1:IncidentsResponseMessage")
    typed, _ = case.payload("GEN-PLD-01", 1000, "response", root="ns1:WorksResponseMessage")
    s = case.run()
    assert case.row(late)[0] is None and case.row(typed)[0] is None
    assert s.orphans == {pl.R_NO_END: 2}


def test_several_candidates_make_the_response_an_orphan_ambiguous(case):
    case.operation("SendIncident", 900, 1000, msg_type="IncidentsEventMessageType")
    case.operation("SendIncident", 905, 1005, msg_type="IncidentsEventMessageType")
    p, _ = case.payload("GEN-PLD-01", 1003, "response", root="ns1:IncidentsResponseMessage")
    s = case.run()
    assert case.row(p)[0] is None and s.orphans == {pl.R_AMBIGUOUS: 1}


def test_an_operation_answers_once(case):
    ev = case.operation("SendWorks", 900, 1000, msg_type="WorksEventMessageType")
    first, _ = case.payload("GEN-PLD-01", 1000, "response", root="ns0:WorksResponseMessage")
    second, _ = case.payload("GEN-PLD-01", 1001, "response", root="ns0:WorksResponseMessage")
    case.run()
    assert case.row(first)[0] == ev and case.row(second)[0] is None


def test_unknown_operation_type_does_not_exclude_the_candidate(case):
    ev = case.operation("SendWorks", 900, 1000)  # no msg_type in its records
    p, _ = case.payload("GEN-PLD-01", 1000, "response", root="ns0:WorksResponseMessage")
    case.run()
    assert case.row(p)[:2] == (ev, 1)


def test_fault_links_to_the_failed_operation_of_the_same_millisecond(case):
    case.operation("Ok op", 900, 1000, status="ok")
    bad = case.operation("Changed crews", 900, 1000, status="fail")
    p, _ = case.payload("GEN-PLD-01", 1000, "fault", root="s:Fault")
    s = case.run()
    assert case.row(p)[:2] == (bad, 1) and s.by_rule[5] == 1


def test_fault_in_another_millisecond_is_an_orphan(case):
    case.operation("Changed crews", 900, 1000, status="fail")
    p, _ = case.payload("GEN-PLD-01", 1001, "fault", root="s:Fault")
    s = case.run()
    assert case.row(p)[0] is None and s.orphans == {pl.R_NO_FAILURE: 1}


# --------------------------------------------------------------------------- rule 6 and 7


def test_payload_in_a_normal_record_inside_an_operation(case):
    ev = case.operation("Changed crews", 900, 1200, tid=9)
    p, _ = case.payload("WFM-120", 1000, "content", tid=9, sensitive=0)
    case.conn.execute(
        "UPDATE entry SET event_id = ? WHERE id = (SELECT entry_id FROM payload WHERE id = ?)", (ev, p)
    )
    case.run()
    assert case.row(p) == (ev, 2, None, None, "Changed crews")


def test_payload_announced_from_inside_an_operation(case):
    ev = case.operation("Changed crews", 900, 1200, tid=9)
    a = case.announce(1000, None, tid=9, code="WFM-112")
    case.conn.execute("UPDATE entry SET event_id = ? WHERE id = ?", (ev, a))
    p, _ = case.payload("WFM-S112", 1000, "delta", tid=9)
    case.run()
    assert case.row(p) == (ev, 2, a, None, "Changed crews")


def test_publication_outside_any_operation_has_no_event(case):
    case.announce(1000, None, tid=9, code="SMN-110")
    p, _ = case.payload("SMN-S110", 1000, "publication", tid=9)
    s = case.run()
    assert case.row(p)[0] is None and case.row(p)[2] is not None  # announced, not in an operation
    assert s.orphans == {pl.R_NOT_IN_OPERATION: 1}


def test_an_announcement_inside_a_non_operation_event_is_not_used(case):
    sid = case.stream(0)
    ev = case.conn.execute(
        "INSERT INTO event(stream_id, kind, ts_start, status, sev) VALUES (?, 'stats_block', 900, 'ok', 4)",
        (sid,),
    ).lastrowid
    a = case.announce(1000, None, tid=9, code="WFM-112")
    case.conn.execute("UPDATE entry SET event_id = ? WHERE id = ?", (ev, a))
    p, _ = case.payload("WFM-S112", 1000, "delta", tid=9)
    case.run()
    assert case.row(p)[0] is None


# --------------------------------------------------------------------------- execution


def build_full(case):
    case.operation("Changed crews", 900, 1200, key=G[3])
    case.announce(1000, G[3], tid=5)
    case.payload("GEN-PLD-01", 1000, "request", tid=5, mid=G[3])


def snapshot(case):
    return case.conn.execute("SELECT * FROM payload ORDER BY id").fetchall(), case.links()


def test_unchanged_group_writes_nothing(case):
    build_full(case)
    s1 = case.run()
    before = snapshot(case)
    changes = case.conn.total_changes
    s2 = case.run()
    assert (s1.groups, s2.groups) == (1, 0)
    assert case.conn.total_changes == changes and snapshot(case) == before
    assert case.conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'payloads.%'").fetchone() == (1,)


def test_changed_sources_recompute_and_the_result_is_deterministic(case):
    build_full(case)
    case.run()
    before = snapshot(case)
    case.conn.execute("UPDATE source SET n_entries = n_entries + 1")
    s = case.run()
    assert s.groups == 1 and snapshot(case) == before


def test_recompute_clears_stale_links(case):
    build_full(case)
    case.run()
    p = case.conn.execute("SELECT id FROM payload").fetchone()[0]
    case.conn.execute("DELETE FROM entry_key")  # the key is gone
    case.conn.execute("UPDATE source SET n_entries = n_entries + 1")
    case.run()
    assert case.row(p)[0] is None and case.row(p)[1] is None
    case.conn.execute("DELETE FROM entry WHERE tpl_id = ?", (case.cat.tpl_id("GEN-MSG-01"),))
    case.conn.execute("UPDATE meta SET value = '1' WHERE key = 'x'")
    case.conn.execute("INSERT OR REPLACE INTO meta VALUES ('entries_epoch', '5')")
    case.run()
    assert case.row(p)[2] is None


def test_new_events_trigger_a_recompute_without_source_changes(case):
    p, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3])
    case.run()
    assert case.row(p)[0] is None
    ev = case.operation("Op", 900, 1200, key=G[3])
    case.run()
    assert case.row(p)[0] == ev


def test_groups_are_independent(case):
    case.payload("GEN-PLD-01", 1000, "request", mid=G[3], group=1)
    case.payload("GEN-PLD-01", 1000, "request", mid=G[3], group=2, pid=300)
    s = case.run()
    assert s.groups == 2
    case.conn.execute(
        "UPDATE source SET n_entries = 1 WHERE stream_id = ?", (case.stream(1, group=2, pid=300),)
    )
    changes = case.run()
    assert changes.groups == 1


def test_failure_rolls_the_group_back_and_is_reported(case, monkeypatch):
    build_full(case)

    def boom(*args, **kwargs):
        case.conn.execute("UPDATE payload SET event_id = 999")
        raise RuntimeError("boom")

    monkeypatch.setattr(pl, "_link_operations", boom)
    s = case.run()
    assert s.groups == 0 and s.errors and "boom" in s.errors[0][1]
    assert case.conn.execute("SELECT count(*) FROM payload WHERE event_id = 999").fetchone() == (0,)
    assert case.conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'payloads.%'").fetchone() == (0,)
    assert not case.conn.in_transaction
    monkeypatch.undo()
    assert case.run().groups == 1


def test_runs_inside_the_callers_transaction(case):
    build_full(case)
    case.conn.execute("BEGIN")
    s = case.run()
    assert s.groups == 1 and case.conn.in_transaction
    case.conn.execute("ROLLBACK")
    assert case.conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'payloads.%'").fetchone() == (0,)


def test_no_payloads_touches_nothing(case):
    case.announce(1000, "")
    changes = case.conn.total_changes
    s = case.run()
    assert s.groups == 0 and case.conn.total_changes == changes


def test_payload_cap_is_physical(case, monkeypatch):
    monkeypatch.setattr(pl, "MAX_PAYLOADS", 3)
    for i in range(6):
        case.payload("GEN-PLD-01", 1000 + i, "request", mid=f"id{i}")
    s = case.run()
    assert s.payloads == 6 and s.dropped == 3 and s.orphans[pl.R_TOO_MANY] == 3
    reasons = [r for (r,) in case.conn.execute("SELECT reason FROM payload ORDER BY id")]
    assert reasons[:3] == [pl.R_NO_OPERATION] * 3 and reasons[3:] == [pl.R_TOO_MANY] * 3
    assert case.run().groups == 0  # stable


def test_announcer_cap_is_physical(case, monkeypatch):
    monkeypatch.setattr(pl, "MAX_ANNOUNCERS", 2)
    for i in range(5):
        case.announce(1000 + i, "", tid=5)
    case.payload("GEN-PLD-01", 1000, "response", tid=5)
    s = case.run()
    assert s.dropped == 3


def test_reason_is_stored(case):
    ok = case.operation("Op", 900, 1200, key=G[3])
    p1, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3])
    p2, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[9])
    case.run()
    reasons = dict(case.conn.execute("SELECT id, reason FROM payload"))
    assert reasons == {p1: None, p2: pl.R_NO_OPERATION} and case.row(p1)[0] == ok


def test_summary_counts(case):
    case.operation("Op", 900, 1200, key=G[3])
    case.announce(1000, G[3], tid=5)
    case.payload("GEN-PLD-01", 1000, "request", tid=5, mid=G[3])
    case.payload("GEN-PLD-01", 1100, "response", tid=6, mid=G[4], corr=G[3], root="XResponseMessage")
    case.payload("SMN-S110", 5000, "publication", tid=7)
    s = case.run()
    assert (s.payloads, s.announced, s.linked, s.paired, s.links) == (3, 1, 2, 2, 1)
    assert s.orphans == {pl.R_NOT_IN_OPERATION: 1} and s.errors == []


# --------------------------------------------------------------------------- review fixes


def test_response_with_several_requests_of_the_same_id_is_ambiguous(case):
    case.operation("Op", 900, 1200, key=G[3])
    r1, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3])
    r2, _ = case.payload("GEN-PLD-01", 1050, "request", mid=G[3])
    resp, _ = case.payload("GEN-PLD-01", 1100, "response", mid=G[4], corr=G[3], root="XResponseMessage")
    s = case.run()
    assert case.row(resp)[0] is None and case.row(resp)[3] is None
    assert case.row(r1)[3] is None and case.row(r2)[3] is None  # nobody is paired by order
    assert s.orphans == {pl.R_AMBIGUOUS: 1}
    assert case.conn.execute("SELECT reason FROM payload WHERE id = ?", (resp,)).fetchone() == (
        pl.R_AMBIGUOUS,
    )


def test_a_single_request_before_the_response_still_pairs_when_another_is_later(case):
    ev = case.operation("Op", 900, 1200, key=G[3])
    early, _ = case.payload("GEN-PLD-01", 1000, "request", mid=G[3])
    case.payload("GEN-PLD-01", 1500, "request", mid=G[3])  # logged after the response: not a candidate
    resp, _ = case.payload("GEN-PLD-01", 1100, "response", mid=G[4], corr=G[3], root="XResponseMessage")
    case.run()
    assert case.row(resp)[:2] == (ev, 3) and case.row(resp)[3] == early


def test_truncated_scan_never_links_a_response(case, monkeypatch):
    monkeypatch.setattr(pl, "MAX_SCAN", 3)
    # five operations end within 16 ms; only the 1st and the 5th have the response's type
    names = ["Incidents", "Works", "Works", "Works", "Incidents"]
    for i, noun in enumerate(names):
        case.operation(f"Op{i}", 900, 1000 + i, msg_type=f"{noun}EventMessageType")
    p, _ = case.payload("GEN-PLD-01", 1002, "response", root="ns1:IncidentsResponseMessage")
    s = case.run()
    assert case.row(p)[0] is None
    assert s.orphans == {pl.R_SCAN_LIMIT: 1} and s.scan_limited == 1
    assert case.conn.execute("SELECT reason FROM payload").fetchone() == (pl.R_SCAN_LIMIT,)


def test_a_scan_that_ends_before_the_limit_still_links(case, monkeypatch):
    monkeypatch.setattr(pl, "MAX_SCAN", 3)
    ev = case.operation("Op", 900, 1000, msg_type="IncidentsEventMessageType")
    case.operation("Other", 900, 1001, msg_type="WorksEventMessageType")
    p, _ = case.payload("GEN-PLD-01", 1000, "response", root="ns1:IncidentsResponseMessage")
    s = case.run()
    assert case.row(p)[:2] == (ev, 1) and s.scan_limited == 0


def test_truncated_scan_never_links_a_fault(case, monkeypatch):
    monkeypatch.setattr(pl, "MAX_SCAN", 2)
    for i, status in enumerate(["ok", "ok", "ok"]):
        case.operation(f"Op{i}", 900, 1000, status=status)
    case.operation("Failed", 900, 1000, status="fail")
    p, _ = case.payload("GEN-PLD-01", 1000, "fault", root="s:Fault")
    s = case.run()
    # only failed operations are scanned for a fault, so the unique one is proven within the limit
    assert case.row(p)[0] is not None and s.scan_limited == 0
    for i in range(3):
        case.operation(f"Failed{i}", 900, 1000, status="fail")
    case.conn.execute("UPDATE source SET n_entries = n_entries + 1")
    s = case.run()
    assert case.row(p)[0] is None and s.orphans == {pl.R_AMBIGUOUS: 1}


def test_announcement_that_changes_replaces_the_old_link(case):
    far = case.announce(600, "", tid=5)
    p, pe = case.payload("GEN-PLD-01", 1000, "response", tid=5)
    case.run()
    assert case.links() == [(far, pe, 2)]
    near = case.announce(999, "", tid=5)
    case.conn.execute("UPDATE source SET n_entries = n_entries + 1")
    case.run()
    assert case.row(p)[2] == near
    assert case.links() == [(near, pe, 2)]  # one link, not two
    case.run()
    assert case.links() == [(near, pe, 2)]


def test_links_of_other_rules_survive_the_recompute(case):
    other = case.announce(100, "", tid=8)
    a = case.announce(1000, "", tid=5)
    p, pe = case.payload("GEN-PLD-01", 1000, "response", tid=5)
    case.conn.execute("INSERT INTO link VALUES (?, ?, 'normal_sensitive', 3)", (other, pe))  # correlate.py's
    case.conn.execute("INSERT INTO link VALUES (?, ?, 'callback', 3)", (a, pe))
    case.run()
    case.conn.execute("UPDATE source SET n_entries = n_entries + 1")
    case.run()
    assert sorted(case.links()) == sorted([(a, pe, 2), (other, pe, 3)])
    assert case.links("callback") == [(a, pe, 3)]


# --------------------------------------------------------------------------- the whole pipeline

E2E_TOML = """
schema = 1
family = "AWFM"

[payload_defaults]
request = { direction = "in", peer = "WFM" }
response = { direction = "out", peer = "WFM" }

[[template]]
code = "E-START"
class = "start"
regex = '''^(?P<op>[A-Z][\\w ]+?) request received\\. ID "(?P<request_id>[0-9a-fA-F-]{36})"\\.$'''
keys = [{ field = "request_id", type = "msgid" }]

[[template]]
code = "E-END"
class = "end"
regex = '''^Request "(?P<request_id>[0-9a-fA-F-]{36})" finished\\.$'''

[[template]]
code = "GEN-MSG-01"
class = "other"
refs_sensitive = true
regex = '''^Message with MessageID: '(?P<message_id>[^']*)' will be logged in sensitive log\\.$'''

[[template]]
code = "GEN-PLD-01"
class = "req_resp"
stream = "sensitive"
regex = '''^Message(?: content)?: (?=<)'''
payload = { fmt = "xml", start = "match_end", strip_suffix = ".", role = "by_root" }

[[operation]]
name_field = "op"
start = ["E-START"]
end_ok = ["E-END"]
pair_by = ["tid"]
check_key = "msgid"
"""


def test_pipeline_extracts_and_links_payloads(tmp_path):
    import test_pipeline as tp

    from dmslog.ingest.pipeline import IngestOptions, run_ingest

    mf = tp.mf
    req_id, resp_id = G[0], G[1]
    normal = [
        mf.header(100, "INFO", f'Changed crews request received. ID "{req_id}".', tid=0x10),
        mf.header(
            120, "DEBUGLOG", f"Message with MessageID: '{req_id}' will be logged in sensitive log.", tid=0x20
        ),
        mf.header(
            130, "DEBUGLOG", f"Message with MessageID: '{resp_id}' will be logged in sensitive log.", tid=0x21
        ),
        mf.header(140, "INFO", f'Request "{req_id}" finished.', tid=0x10),
    ]
    sensitive = [
        mf.header(120, "INFO", "Message content: <ChangedCrews>", tid=0x20),
        f"  <Header><MessageID>{req_id}</MessageID></Header>",
        "</ChangedCrews>.",
        mf.header(130, "INFO", "Message: <CrewsResponseMessage>", tid=0x21),
        f"  <Header><MessageID>{resp_id}</MessageID><CorrelationID>{req_id}</CorrelationID></Header>",
        "</CrewsResponseMessage>.",
    ]
    root = tp.write_folder(
        tmp_path / "Log",
        {
            tp.WFM: mf.render(normal, eol="\r\n"),
            tp.WFM.replace(".log", ".sensitive.log"): mf.render(sensitive, eol="\r\n"),
        },
    )
    conn = tp.fresh_db(tmp_path, "e2e")
    result = run_ingest(conn, root, IngestOptions(catalog=load_texts({"awfm.toml": E2E_TOML})))
    assert result.sources_failed == 0 and result.warnings == []
    assert (result.payloads, result.payloads_linked) == (2, 2)
    rows = conn.execute(
        "SELECT p.role, p.operation, p.event_conf, p.pair_id IS NOT NULL, a.msg LIKE 'Message with%',"
        " ev.name, ev.status FROM payload p LEFT JOIN entry a ON a.id = p.ref_entry_id"
        " LEFT JOIN event ev ON ev.id = p.event_id ORDER BY p.id"
    ).fetchall()
    assert rows == [
        ("request", "Changed crews", 3, 1, 1, "Changed crews", "ok"),
        ("response", "Changed crews", 3, 1, 1, "Changed crews", "ok"),
    ]
    assert conn.execute("SELECT count(*) FROM link WHERE kind = 'normal_sensitive'").fetchone()[0] >= 2
    # a second run writes nothing
    changes = conn.total_changes
    again = run_ingest(conn, root, IngestOptions(catalog=load_texts({"awfm.toml": E2E_TOML})))
    assert conn.total_changes == changes and again.payloads == 0 and again.payloads_linked == 0
    conn.close()
