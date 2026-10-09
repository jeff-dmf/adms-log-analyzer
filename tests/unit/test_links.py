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
