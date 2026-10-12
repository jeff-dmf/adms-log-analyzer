"""link_pending: callbacks, retries and responses from [[link_rule]] tables (synthetic data only)."""

from __future__ import annotations

import json

import pytest

from dmslog.derive import links as links_mod
from dmslog.derive.links import link_pending
from dmslog.parse.templates import load_texts
from dmslog.store.db import create_case_db

TEMPLATES = """
schema = 1
family = "{family}"

[[template]]
code = "{p}-001"
class = "start"
text = "wait"

[[template]]
code = "{p}-002"
class = "end"
text = "callback"

[[template]]
code = "{p}-003"
class = "other"
text = "attempt"

[[template]]
code = "{p}-004"
class = "other"
text = "response"
"""


def catalog(rules: str, family: str = "AWFM", prefix: str = "TST"):
    return load_texts({f"{family.lower()}.toml": TEMPLATES.format(family=family, p=prefix) + rules})


CALLBACK = """
[[link_rule]]
kind = "callback"
a = "TST-001"
b = "TST-002"
key = "gid"
scope = "{scope}"
max_dt_ms = {max_dt}
"""


def callback_rules(scope="stream", max_dt=60000):
    return CALLBACK.format(scope=scope, max_dt=max_dt)


RETRY_RULES = """
[[link_rule]]
kind = "retry"
a = "TST-003"
b = "TST-003"
key = "msgid"

[[link_rule]]
kind = "response"
a = "TST-003"
b = "TST-004"
key = "msgid"
"""

WINDOW_RULE = """
[[link_rule]]
kind = "callback"
a = "TST-001"
b = "TST-002"
window_only = true
max_dt_ms = 5000
"""


class Case:
    """A case database filled by hand: streams, entries and keys."""

    def __init__(self, tmp_path, cat):
        self.conn = create_case_db(tmp_path / "case.sqlite")
        self.conn.isolation_level = None
        self.cat = cat
        self.conn.execute(
            "INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', '2026-01-01')"
        )
        for t in cat.templates():
            self.conn.execute(
                "INSERT INTO template(id, code, family, class, pattern, def_version) VALUES (?,?,?,?,?,'v1')",
                (cat.tpl_id(t.code), t.code, t.family, t.cls, t.text or ""),
            )
        self.streams: dict[tuple, int] = {}
        self.sources: dict[int, int] = {}
        self.next_id = 1

    def stream(self, group=1, family="AWFM", process="WfmAdapter", pid=100, sensitive=0) -> int:
        k = (group, family, process, pid, sensitive)
        if k not in self.streams:
            cur = self.conn.execute(
                "INSERT INTO stream(collection_group, family, process, pid, sensitive) VALUES (?,?,?,?,?)", k
            )
            sid = cur.lastrowid
            cur = self.conn.execute(
                "INSERT INTO source(collection_id, stream_id, member, file_name, envelope, status)"
                " VALUES (1, ?, ?, ?, 'adms', 'ready')",
                (sid, f"m{sid}", f"f{sid}.log"),
            )
            self.streams[k] = sid
            self.sources[sid] = cur.lastrowid
        return self.streams[k]

    def add(self, code, ts, key=None, key_type="gid", stream=None, tid=1, fields=None) -> int:
        sid = stream or self.stream()
        eid = self.next_id
        self.next_id += 1
        self.conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " fields, msg) VALUES (?,?,?,?,1,?,100,?,4,?,0,?,?)",
            (eid, self.sources[sid], eid, eid, ts, tid, self.cat.tpl_id(code),
             json.dumps(fields) if fields else None, code),
        )  # fmt: skip
        if key is not None:
            self.conn.execute(
                "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)", (key_type, key, eid)
            )
        return eid

    def touch(self, sid):
        """Simulate more indexed data in the stream."""
        self.conn.execute("UPDATE source SET n_entries = n_entries + 1 WHERE stream_id = ?", (sid,))

    def links(self, kind=None):
        sql = "SELECT a_entry_id, b_entry_id, kind, confidence FROM link"
        args = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        return self.conn.execute(sql + " ORDER BY a_entry_id, b_entry_id", args).fetchall()

    def run(self):
        return link_pending(self.conn, self.cat)


@pytest.fixture
def make(tmp_path):
    cases = []

    def build(rules, **kw):
        case = Case(tmp_path, catalog(rules, **kw))
        cases.append(case)
        return case

    yield build
    for c in cases:
        c.conn.close()


def test_callback_by_gid_in_another_tid(make):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1", tid=1)
    b = c.add("TST-002", 2000, "g1", tid=9)
    s = c.run()
    assert c.links() == [(a, b, "callback", 3)]
    assert (s.links, s.groups, s.errors) == (1, 1, [])


def test_key_mismatch_and_dt_bounds(make):
    c = make(callback_rules(max_dt=1000))
    a1 = c.add("TST-001", 1000, "g1")
    c.add("TST-002", 1500, "other")  # key mismatch
    c.add("TST-002", 2500, "g1")  # dt 1500 > max
    b_ok = c.add("TST-002", 2000, "g1")  # dt exactly max
    c.add("TST-002", 900, "g1")  # earlier than the a record
    c.run()
    assert c.links() == [(a1, b_ok, "callback", 3)]


def test_each_b_linked_to_latest_a_only(make):
    c = make(callback_rules())
    c.add("TST-001", 1000, "g1")
    a2 = c.add("TST-001", 1100, "g1")
    b = c.add("TST-002", 1200, "g1")
    c.run()
    assert c.links() == [(a2, b, "callback", 3)]


def test_scope_group_across_normal_and_sensitive_streams(make):
    c = make(callback_rules(scope="group"))
    normal = c.stream(sensitive=0)
    sens = c.stream(sensitive=1)
    other_proc = c.stream(process="Other", pid=200)
    a = c.add("TST-001", 1000, "g1", stream=normal)
    b = c.add("TST-002", 1500, "g1", stream=sens)
    c.add("TST-002", 1600, "g1", stream=other_proc)  # another process: no link
    c.run()
    assert c.links() == [(a, b, "callback", 3)]


def test_scope_stream_does_not_cross_streams(make):
    c = make(callback_rules(scope="stream"))
    normal = c.stream(sensitive=0)
    sens = c.stream(sensitive=1)
    c.add("TST-001", 1000, "g1", stream=normal)
    c.add("TST-002", 1500, "g1", stream=sens)
    c.run()
    assert c.links() == []


def test_retry_chain_with_response(make):
    c = make(RETRY_RULES)
    m = "00000000-0000-4000-8000-000000000001"
    a1 = c.add("TST-003", 1000, m, "msgid")
    a2 = c.add("TST-003", 2000, m, "msgid")
    a3 = c.add("TST-003", 3000, m, "msgid")
    r = c.add("TST-004", 3500, m, "msgid")
    c.add("TST-003", 1500, "00000000-0000-4000-8000-000000000002", "msgid")  # another msgId: alone
    c.run()
    assert c.links("retry") == [(a1, a2, "retry", 3), (a2, a3, "retry", 3)]
    assert c.links("response") == [(a3, r, "response", 3)]


def test_retry_chain_breaks_after_a_gap(make):
    c = make(RETRY_RULES)
    a1 = c.add("TST-003", 1000, "m", "msgid")
    a2 = c.add("TST-003", 2000, "m", "msgid")
    c.add("TST-003", 200_000, "m", "msgid")  # beyond the default window: new chain
    c.run()
    assert c.links("retry") == [(a1, a2, "retry", 3)]


def test_window_only_unambiguous_links_with_confidence_zero(make):
    c = make(WINDOW_RULE)
    a = c.add("TST-001", 1000)
    b = c.add("TST-002", 2000, tid=7)
    s = c.run()
    assert c.links() == [(a, b, "callback", 0)]
    assert s.ambiguous == 0


def test_window_only_ambiguous_links_nothing(make):
    c = make(WINDOW_RULE)
    c.add("TST-001", 1000)
    c.add("TST-001", 1200)
    c.add("TST-002", 2000)
    s = c.run()
    assert c.links() == []
    assert s.ambiguous == 1


def test_window_only_respects_max_dt_and_consumes_a(make):
    c = make(WINDOW_RULE)
    a = c.add("TST-001", 1000)
    b1 = c.add("TST-002", 1500)
    c.add("TST-002", 1600)  # a already consumed: no open a
    c.add("TST-001", 10_000)
    c.add("TST-002", 20_000)  # dt beyond max_dt_ms
    c.run()
    assert c.links() == [(a, b1, "callback", 0)]


def test_key_taken_from_field_when_not_an_entry_key(make):
    c = make(callback_rules().replace('"gid"', '"call_id"'))
    a = c.add("TST-001", 1000, fields={"call_id": 77})
    b = c.add("TST-002", 1100, fields={"call_id": 77})
    c.add("TST-002", 1200, fields={"call_id": 78})
    c.run()
    assert c.links() == [(a, b, "callback", 3)]


def test_rules_do_not_cross_families(make):
    c = make(callback_rules())
    other = c.stream(family="AONT", process="OntAdapter", pid=300)
    c.add("TST-001", 1000, "g1", stream=other)
    c.add("TST-002", 1100, "g1", stream=other)
    c.run()
    assert c.links() == []  # AONT has no link rule of its own


def test_rerun_writes_nothing(make):
    c = make(callback_rules())
    c.add("TST-001", 1000, "g1")
    c.add("TST-002", 2000, "g1")
    assert c.run().links == 1
    before = c.conn.total_changes
    s = c.run()
    assert (s.links, s.groups) == (0, 0)
    assert c.conn.total_changes == before


def test_growth_recomputes_the_group(make):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1")
    b1 = c.add("TST-002", 2000, "g1")
    c.run()
    b2 = c.add("TST-002", 3000, "g1")
    c.touch(c.stream())
    s = c.run()
    assert (s.groups, c.links()) == (1, [(a, b1, "callback", 3), (a, b2, "callback", 3)])
    # an entry removed from the stream: its links are gone after the recompute
    c.conn.execute("DELETE FROM entry WHERE id = ?", (b1,))
    c.touch(c.stream())
    c.run()
    assert c.links() == [(a, b2, "callback", 3)]


def test_other_link_kinds_are_left_alone(make):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1")
    b = c.add("TST-002", 2000, "g1")
    c.conn.execute("INSERT INTO link VALUES (?, ?, 'normal_sensitive', 2)", (a, b))
    c.run()
    c.touch(c.stream())
    c.run()
    assert c.links("normal_sensitive") == [(a, b, "normal_sensitive", 2)]
    assert c.links("callback") == [(a, b, "callback", 3)]


def test_groups_are_independent(make):
    c = make(callback_rules())
    s1 = c.stream(group=1)
    s2 = c.stream(group=2, pid=101)
    c.add("TST-001", 1000, "g1", stream=s1)
    c.add("TST-002", 1100, "g1", stream=s1)
    c.add("TST-001", 1000, "g2", stream=s2)
    c.add("TST-002", 1100, "g2", stream=s2)
    assert c.run().groups == 2
    c.add("TST-002", 1200, "g1", stream=s1)
    c.touch(s1)
    s = c.run()
    assert (s.groups, s.links) == (1, 2)


def test_rollback_on_failure_keeps_the_previous_links(make, monkeypatch):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1")
    b = c.add("TST-002", 2000, "g1")
    c.run()
    c.add("TST-002", 3000, "g1")
    c.touch(c.stream())

    def boom(*args, **kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(links_mod, "_keyed_links", boom)
    s = c.run()
    assert s.links == 0 and s.groups == 0
    assert len(s.errors) == 1 and "boom" in s.errors[0][1]
    assert c.links() == [(a, b, "callback", 3)]
    assert not c.conn.in_transaction
    monkeypatch.undo()
    assert c.run().groups == 1  # the signature was not stored by the failed run
    assert len(c.links()) == 2


def test_rollback_inside_the_callers_transaction(make, monkeypatch):
    c = make(callback_rules())
    c.add("TST-001", 1000, "g1")
    c.add("TST-002", 2000, "g1")
    monkeypatch.setattr(links_mod, "_keyed_links", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    c.conn.execute("BEGIN")
    c.conn.execute("INSERT INTO meta(key, value) VALUES ('mine', '1')")
    s = c.run()
    assert len(s.errors) == 1
    assert c.conn.in_transaction  # the caller's transaction survives
    assert c.conn.execute("SELECT value FROM meta WHERE key = 'mine'").fetchone() == ("1",)
    c.conn.execute("COMMIT")


def test_fanout_is_bounded_and_counted(make, monkeypatch):
    monkeypatch.setattr(links_mod, "MAX_B_PER_A", 3)
    c = make(callback_rules())
    c.add("TST-001", 1000, "g1")
    for i in range(10):
        c.add("TST-002", 1001 + i, "g1")
    s = c.run()
    assert len(c.links()) == 3
    assert s.dropped == 7


def test_open_keys_bound(make, monkeypatch):
    monkeypatch.setattr(links_mod, "MAX_OPEN_KEYS", 2)
    c = make(callback_rules())
    for i in range(4):
        c.add("TST-001", 1000 + i, f"g{i}")
    for i in range(4):
        c.add("TST-002", 2000 + i, f"g{i}")
    s = c.run()
    assert s.dropped == 2
    assert len(c.links()) == 2  # only the two latest keys stayed open


def test_odd_data_never_raises(make):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1")
    c.add("TST-002", None, "g1")  # no ts
    c.add("TST-002", 1100)  # no key
    b = c.add("TST-002", 1100, "g1", tid=None)
    s = c.run()
    assert s.errors == []
    assert c.links() == [(a, b, "callback", 3)]


def test_invalid_rule_is_reported_not_raised(make):
    c = make('[[link_rule]]\nkind = "callback"\na = "TST-001"\nb = "TST-002"\nkey = "gid"\n')
    assert links_mod._rule_problem({"kind": "callback", "a": "X", "b": "Y"}) is not None
    assert links_mod._rule_problem({"kind": "retry", "a": "X", "b": "Y", "window_only": True}) is not None
    assert links_mod._rule_problem({"kind": "nope", "a": "X", "b": "Y", "key": "k"}) is not None
    assert links_mod._rule_problem({"kind": "callback", "a": "X", "b": "Y", "key": "k"}) is None
    c.add("TST-001", 1000, "g1")
    assert c.run().errors == []


def test_database_without_streams(tmp_path):
    conn = create_case_db(tmp_path / "e.sqlite")
    s = link_pending(conn, catalog(""))
    assert (s.links, s.groups, s.errors) == (0, 0, [])
    conn.close()


def _linked_case(make):
    c = make(callback_rules())
    c.add("TST-001", 1000, "g1")
    c.add("TST-002", 2000, "g1")
    assert c.run().groups == 1
    return c


def test_zip_crc_change_with_same_sizes_recomputes(make):
    c = _linked_case(make)
    c.conn.execute("UPDATE source SET size = 10, zip_crc = 1")
    assert c.run().groups == 1
    c.conn.execute("UPDATE source SET zip_crc = 2")
    assert c.run().groups == 1
    assert c.run().groups == 0


def test_def_version_change_recomputes(make):
    c = _linked_case(make)
    c.conn.execute("UPDATE template SET def_version = 'v2' WHERE code = 'TST-001'")
    assert c.run().groups == 1


def test_entries_epoch_change_recomputes(make):
    c = _linked_case(make)
    c.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('entries_epoch', '5')")
    assert c.run().groups == 1
    before = c.conn.total_changes
    assert c.run().groups == 0
    assert c.conn.total_changes == before


def test_deleted_and_recreated_record_gets_new_links_only(make):
    c = make(callback_rules())
    a = c.add("TST-001", 1000, "g1")
    b = c.add("TST-002", 2000, "g1")
    c.run()
    c.conn.execute("DELETE FROM entry WHERE id = ?", (b,))
    c.conn.execute("DELETE FROM entry_key WHERE entry_id = ?", (b,))
    b2 = c.add("TST-002", 2000, "g1")
    c.conn.execute("UPDATE source SET zip_crc = 9")
    c.run()
    assert c.links() == [(a, b2, "callback", 3)]
    # the a side deleted: the orphan link goes away too
    c.conn.execute("DELETE FROM entry WHERE id = ?", (a,))
    c.conn.execute("UPDATE source SET zip_crc = 10")
    c.run()
    assert c.links() == []


# --------------------------------------------------------------------------- origin = "operation" (issue #65)

ORIGIN = """
[[link_rule]]
kind = "callback"
a = "TST-001"
b = "TST-002"
key = "gid"
origin = "operation"
scope = "{scope}"
max_dt_ms = 5000
"""


def origin_rules(scope="stream"):
    return ORIGIN.format(scope=scope)


def add_operation(c, start, end, members, stream=None, tid=1, kind="operation"):
    """An operation event of the stream from ``start`` to ``end`` whose entries get ``entry.event_id``."""
    sid = stream or c.stream()
    ts_start = c.conn.execute("SELECT ts FROM entry WHERE id = ?", (start,)).fetchone()[0]
    cur = c.conn.execute(
        "INSERT INTO event(stream_id, kind, name, tid, start_entry_id, end_entry_id, ts_start, sev)"
        " VALUES (?, ?, 'Changed crew assignments', ?, ?, ?, ?, 4)",
        (sid, kind, tid, start, end, ts_start),
    )
    for eid in (start, *members, *([end] if end else [])):
        c.conn.execute("UPDATE entry SET event_id = ? WHERE id = ?", (cur.lastrowid, eid))
    return cur.lastrowid


def request(c, ts, gids, tid=1, stream=None, close=True):
    """A request (start record) logging the GIDs returned by CRS on its TID; returns (start, sent ids)."""
    start = c.add("TST-003", ts, tid=tid, stream=stream)
    sent = [c.add("TST-001", ts + 100 + i, g, tid=tid, stream=stream) for i, g in enumerate(gids)]
    end = c.add("TST-004", ts + 200, tid=tid, stream=stream) if close else None
    add_operation(c, start, end, sent, stream=stream, tid=tid)
    return start, sent


def test_origin_unique_match_links_the_operation_start(make):
    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1", "g2"])
    b1 = c.add("TST-002", 2000, "g1", tid=9)
    b2 = c.add("TST-002", 2100, "g2", tid=8)
    s = c.run()
    assert c.links() == [(start, b1, "callback", 2), (start, b2, "callback", 2)]
    assert (s.links, s.ambiguous, s.unmatched, s.no_operation, s.out_of_window) == (2, 0, 0, 0, 0)
    assert links_mod.link_stats(c.conn, 1) == {"AWFM/callback:TST-001>TST-002@operation": {"linked": 2}}


def test_origin_absent_key_links_nothing_and_counts(make):
    # DebugLog: the request never logged the GID, the callback has nothing to match
    c = make(origin_rules())
    request(c, 1000, [])
    c.add("TST-002", 2000, "g1", tid=9)
    s = c.run()
    assert c.links() == []
    assert s.unmatched == 1
    assert links_mod.link_stats(c.conn, 1)["AWFM/callback:TST-001>TST-002@operation"] == {
        "linked": 0,
        "unmatched": 1,
    }


def test_origin_ambiguous_key_in_two_operations_links_nothing(make):
    c = make(origin_rules())
    request(c, 1000, ["g1"], tid=1)
    request(c, 1050, ["g1"], tid=2)
    c.add("TST-002", 2000, "g1", tid=9)
    s = c.run()
    assert c.links() == []
    assert s.ambiguous == 1


def test_origin_key_logged_twice_in_the_same_operation_is_not_ambiguous(make):
    # WFM-123 and WFM-124 carry the same GID inside one request
    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1", "g1"])
    b = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    assert c.links() == [(start, b, "callback", 2)]


def test_origin_key_outside_any_operation_links_nothing(make):
    c = make(origin_rules())
    c.add("TST-001", 1000, "g1")  # no event_id
    c.add("TST-002", 1500, "g1", tid=9)
    s = c.run()
    assert c.links() == [] and s.no_operation == 1


def test_origin_key_both_inside_and_outside_an_operation_is_ambiguous(make):
    c = make(origin_rules())
    request(c, 1000, ["g1"])
    c.add("TST-001", 1300, "g1", tid=5)  # same GID on a thread without an operation
    c.add("TST-002", 2000, "g1", tid=9)
    s = c.run()
    assert c.links() == [] and s.ambiguous == 1


def test_origin_callback_before_the_request_or_too_late_links_nothing(make):
    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1"])  # GID logged at 1100
    c.add("TST-002", 1050, "g1", tid=9)  # before the GID was logged
    late = c.add("TST-002", 1100 + 5001, "g1", tid=9)  # beyond max_dt_ms
    ok = c.add("TST-002", 1100 + 5000, "g1", tid=9)  # exactly max_dt_ms
    s = c.run()
    assert c.links() == [(start, ok, "callback", 2)]
    assert s.out_of_window == 2
    assert late not in {b for _, b, _, _ in c.links()}


def test_origin_ignores_events_that_are_not_operations_or_of_other_streams(make):
    c = make(origin_rules())
    other = c.stream(process="Other", pid=200)
    a = c.add("TST-001", 1000, "g1")
    starter = c.add("TST-003", 900, stream=other)
    add_operation(c, starter, None, [], stream=other)
    c.conn.execute("UPDATE entry SET event_id = (SELECT max(id) FROM event) WHERE id = ?", (a,))
    b_start = c.add("TST-003", 950)
    add_operation(c, b_start, None, [], kind="level_window")
    a2 = c.add("TST-001", 1000, "g2")
    c.conn.execute("UPDATE entry SET event_id = (SELECT max(id) FROM event) WHERE id = ?", (a2,))
    a3 = c.add("TST-001", 1000, "g3")
    c.conn.execute("UPDATE entry SET event_id = 999999 WHERE id = ?", (a3,))  # dangling
    for g in ("g1", "g2", "g3"):
        c.add("TST-002", 1500, g, tid=9)
    s = c.run()
    assert c.links() == [] and s.no_operation == 3 and s.errors == []


def test_origin_sensitive_callback_needs_scope_group(make):
    c = make(origin_rules(scope="group"))
    normal = c.stream(sensitive=0)
    sens = c.stream(sensitive=1)
    start, _ = request(c, 1000, ["g1"], stream=normal)
    b = c.add("TST-002", 2000, "g1", stream=sens, tid=9)
    c.run()
    assert c.links() == [(start, b, "callback", 2)]


def test_origin_scope_stream_does_not_reach_the_sensitive_callback(make):
    c2 = make(origin_rules(scope="stream"))
    n2, s2 = c2.stream(sensitive=0), c2.stream(sensitive=1)
    request(c2, 1000, ["g1"], stream=n2)
    c2.add("TST-002", 2000, "g1", stream=s2, tid=9)
    assert (c2.run().unmatched, c2.links()) == (1, [])


def test_origin_open_operation_still_links(make):
    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1"], close=False)  # truncated by the ERS: no end record
    b = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    assert c.links() == [(start, b, "callback", 2)]


def test_origin_rerun_is_idempotent_and_growth_recomputes(make):
    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1", "g2"])
    b1 = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    before = c.conn.total_changes
    assert (c.run().groups, c.conn.total_changes) == (0, before)
    b2 = c.add("TST-002", 2500, "g2", tid=9)  # the callback arrives in the next cycle
    c.touch(c.stream())
    s = c.run()
    assert s.groups == 1
    assert c.links() == [(start, b1, "callback", 2), (start, b2, "callback", 2)]


def test_origin_follows_a_rederivation_without_source_change(make):
    c = make(origin_rules())
    start, sent = request(c, 1000, ["g1"])
    b = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    assert c.links() == [(start, b, "callback", 2)]
    # a new [[operation]] rule re-derives the stream: the GID record is no longer inside an operation
    c.conn.execute("UPDATE entry SET event_id = NULL")
    c.conn.execute("DELETE FROM event")
    s = c.run()
    assert s.groups == 1 and c.links() == [] and s.no_operation == 1


def test_origin_links_survive_the_normal_sensitive_recomputation(make):
    """Regression (8.1): correlate._delete_links removes normal_sensitive links only, never callbacks."""
    from dmslog.derive.correlate import correlate_pending

    pair = '\n[[sensitive_pair]]\nnormal = "TST-002"\nsensitive = "TST-002"\nmatch = ["pid", "tid"]\n'
    c = make(origin_rules() + pair)
    normal = c.stream(sensitive=0)
    sens = c.stream(sensitive=1)
    start, _ = request(c, 1000, ["g1"], stream=normal)
    b = c.add("TST-002", 2000, "g1", stream=normal, tid=9)
    c.add("TST-002", 2000, "g1", stream=sens, tid=9)
    c.run()
    correlate_pending(c.conn, c.cat)
    c.touch(normal)
    c.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('entries_epoch', '7')")
    correlate_pending(c.conn, c.cat)  # recomputes the group: deletes and recreates its normal_sensitive links
    assert c.links("callback") == [(start, b, "callback", 2)]
    assert len(c.links("normal_sensitive")) == 1


def test_origin_bounds_are_counted(make, monkeypatch):
    monkeypatch.setattr(links_mod, "MAX_B_PER_A", 2)
    monkeypatch.setattr(links_mod, "MAX_A_PER_KEY", 2)
    c = make(origin_rules())
    request(c, 1000, ["g1", "g2", "g3"])
    for i, g in enumerate(("g1", "g2", "g3")):
        c.add("TST-002", 2000 + i, g, tid=9)
    request(c, 10_000, ["h", "h", "h"])  # more GID records than MAX_A_PER_KEY: not trusted
    c.add("TST-002", 11_000, "h", tid=9)
    s = c.run()
    assert len(c.links()) == 2
    assert (s.dropped, s.ambiguous) == (1, 1)


def test_origin_rule_problems(make):
    p = links_mod._rule_problem
    base = {"kind": "callback", "a": "X", "b": "Y", "key": "gid", "origin": "operation"}
    assert p(base) is None
    assert p({**base, "kind": "response"}) is None
    assert p({**base, "kind": "retry"}) is not None
    assert p({**base, "window_only": True}) is not None
    assert p({**base, "b": ["X", "Y"]}) is not None
    assert p({**base, "origin": "nope"}) is not None
    c = make(origin_rules().replace('origin = "operation"', 'origin = "bogus"'))
    c.add("TST-002", 1000, "g1")
    s = c.run()
    assert s.links == 0 and any("unknown origin" in m for _, m in s.errors)


def test_old_meta_format_recomputes_once(make):
    c = make(origin_rules())
    request(c, 1000, ["g1"])
    c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    c.conn.execute("UPDATE meta SET value = 'deadbeef' WHERE key LIKE 'correlate.links.%'")
    assert links_mod.link_stats(c.conn, 1) == {}
    assert c.run().groups == 1
    assert c.run().groups == 0


def test_origin_moving_an_a_record_to_another_operation_recomputes(make):
    """Review of PR #89: same event ids, same count and same starts; only one ``entry.event_id`` changes."""
    c = make(origin_rules())
    start1 = c.add("TST-003", 1000, tid=1)
    a1 = c.add("TST-001", 1100, "g1", tid=1)
    a2 = c.add("TST-001", 1150, "g1", tid=1)
    op1 = add_operation(c, start1, None, [a1, a2], tid=1)
    start2 = c.add("TST-003", 1000, tid=2)
    op2 = add_operation(c, start2, None, [], tid=2)
    b = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    assert c.links() == [(start1, b, "callback", 2)]
    events_before = c.conn.execute("SELECT id, start_entry_id FROM event ORDER BY id").fetchall()
    c.conn.execute("UPDATE entry SET event_id = ? WHERE id = ?", (op2, a2))
    assert c.conn.execute("SELECT id, start_entry_id FROM event ORDER BY id").fetchall() == events_before
    assert op1 != op2
    s = c.run()
    assert s.groups == 1 and s.ambiguous == 1
    assert c.links() == []
    assert c.run().groups == 0  # and stable afterwards


def test_origin_links_survive_the_payload_recomputation(make):
    """derive/payloads.py removes only its own normal_sensitive announcement pairs, never callbacks."""
    from dmslog.derive.payloads import link_payloads

    c = make(origin_rules())
    start, _ = request(c, 1000, ["g1"])
    b = c.add("TST-002", 2000, "g1", tid=9)
    c.run()
    # the worst case: a stale announcement link of the payload linker on the very same (a, b) pair
    c.conn.execute("INSERT INTO link VALUES (?, ?, 'normal_sensitive', 2)", (start, b))
    c.conn.execute(
        "INSERT INTO payload(entry_id, ref_entry_id, role, direction, fmt, char_off, char_len)"
        " VALUES (?, ?, 'request', 'in', 'xml', 0, 1)",
        (b, start),
    )
    s = link_payloads(c.conn, c.cat)
    assert s.errors == [] and s.groups == 1
    assert c.links("normal_sensitive") == []  # its own stale pair is gone
    assert c.links("callback") == [(start, b, "callback", 2)]
