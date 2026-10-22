"""Handoff link rules: the match is unique both ways, independent of ids (synthetic data only)."""

from __future__ import annotations

import pytest
from handoff_helpers import MAX_DT, RULE1, RULE2, TOML_FH, TOML_JOB, HandoffDb, catalog, load_world

from dmslog.derive import links as links_mod
from dmslog.derive.links import link_pending
from dmslog.parse.templates import (
    SchemaError,
    TemplateReferenceError,
    load_texts,
)


@pytest.fixture
def make(tmp_path):
    dbs = []

    def build(world=None, **kw):
        db = HandoffDb(tmp_path / f"case{len(dbs)}.sqlite", **kw)
        dbs.append(db)
        if world:
            load_world(db, world)
        return db

    yield build
    for d in dbs:
        d.close()


def L(a, b, conf=3):
    return (a, b, conf)


def test_scheduler_to_launcher_in_the_same_family(make):
    db = make({"sched.log": [(100, "TST-001", 7, None)], "launch.log": [(110, "TST-002", 7, None)]})
    s = db.run()
    assert db.links() == {L(("sched.log", 1), ("launch.log", 1))}
    assert (s.links, s.errors) == (1, [])
    assert db.stats()[RULE1] == {"linked": 1}


def test_launcher_to_function_host_needs_families_and_checks_the_pid(make):
    world = {
        "launch.log": [(100, "TST-003", 7, 31)],
        "fh1.log": [(105, "TST-101", 7, None)],
    }
    db = make(world)
    db.run()
    assert db.links() == {L(("launch.log", 1), ("fh1.log", 1))}
    assert db.stats()[RULE2] == {"linked": 1}


def test_a_rule_without_families_does_not_see_the_other_family(make, tmp_path):
    text = TOML_JOB.replace('families = ["DMSFH"]\n', "").replace('pid_field = "fh_pid"\n', "")
    # b = TST-101 now lives in a family the rule cannot see: the catalog refuses it
    with pytest.raises(TemplateReferenceError):
        load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})


def test_confidence_3_with_equal_pids_and_2_when_one_side_is_unknown(make):
    db = make(
        {
            "launch.log": [(100, "TST-003", 1, 31), (200, "TST-003", 2, None)],
            "fh1.log": [(105, "TST-101", 1, None), (205, "TST-101", 2, None)],
        }
    )
    db.run()
    assert db.links() == {L(("launch.log", 1), ("fh1.log", 1), 3), L(("launch.log", 2), ("fh1.log", 2), 2)}


def test_a_known_pid_mismatch_links_nothing_and_is_counted(make):
    db = make({"launch.log": [(100, "TST-003", 1, 32)], "fh1.log": [(105, "TST-101", 1, None)]})
    db.run()
    assert db.links() == set()
    st = db.stats()[RULE2]
    assert st == {"linked": 0, "pid_mismatch": 1}


def test_two_a_in_the_window_of_a_b_link_nothing(make):
    db = make(
        {
            "sched.log": [(100, "TST-001", 1, None), (110, "TST-001", 1, None)],
            "launch.log": [(120, "TST-002", 1, None)],
        }
    )
    db.run()
    assert db.links() == set()
    assert db.stats()[RULE1]["ambiguous"] == 1


def test_two_b_in_the_window_of_an_a_link_nothing(make):
    db = make(
        {
            "sched.log": [(100, "TST-001", 1, None)],
            "launch.log": [(110, "TST-002", 1, None), (120, "TST-002", 1, None)],
        }
    )
    db.run()
    assert db.links() == set()
    assert db.stats()[RULE1]["ambiguous"] == 2


def test_the_counterexample_that_broke_the_first_algorithm(make, monkeypatch):
    """Window 10: a1@0, a2@5, b1@6, b2@11. b2 only sees a2 and a1 only b1, but a2 has two b and b1 two a."""
    text = TOML_JOB.replace(f"max_dt_ms = {MAX_DT}", "max_dt_ms = 10")
    cat = load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})
    db = make(cat=cat)
    for i, ts in enumerate((0, 5), 1):
        db.add("sched.log", i, (ts, "TST-001", 1, None))
    for i, ts in enumerate((6, 11), 1):
        db.add("launch.log", i, (ts, "TST-002", 1, None))
    db.run()
    assert db.links() == set()
    assert db.stats()[RULE1]["ambiguous"] == 2


def test_a_later_b_inside_the_window_of_the_a_raises_its_degree(make):
    """Window 10: a@0, b1@1, b2@2. The link is decided only when the window of the a closes."""
    text = TOML_JOB.replace(f"max_dt_ms = {MAX_DT}", "max_dt_ms = 10")
    db = make(cat=load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH}))
    db.add("sched.log", 1, (0, "TST-001", 1, None))
    db.add("launch.log", 1, (1, "TST-002", 1, None))
    db.add("launch.log", 2, (2, "TST-002", 1, None))
    db.run()
    assert db.links() == set()
    assert db.stats()[RULE1]["ambiguous"] == 2
    # without the second b the link exists
    db2 = make(cat=load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH}))
    db2.add("sched.log", 1, (0, "TST-001", 1, None))
    db2.add("launch.log", 1, (1, "TST-002", 1, None))
    db2.run()
    assert len(db2.links()) == 1


def test_a_and_b_with_the_same_ts_link_but_two_a_or_two_b_do_not(make):
    db = make({"sched.log": [(100, "TST-001", 1, None)], "launch.log": [(100, "TST-002", 1, None)]})
    db.run()
    assert len(db.links()) == 1
    db2 = make(
        {
            "sched.log": [(100, "TST-001", 2, None), (100, "TST-001", 2, None)],
            "launch.log": [(100, "TST-002", 2, None)],
        }
    )
    db2.run()
    assert db2.links() == set()
    db3 = make(
        {
            "sched.log": [(100, "TST-001", 3, None)],
            "launch.log": [(100, "TST-002", 3, None), (100, "TST-002", 3, None)],
        }
    )
    db3.run()
    assert db3.links() == set()


def test_negative_and_too_large_dt_do_not_link(make):
    db = make(
        {
            "sched.log": [(1000, "TST-001", 1, None), (2000, "TST-001", 2, None)],
            "launch.log": [(999, "TST-002", 1, None), (2000 + MAX_DT + 1, "TST-002", 2, None)],
        }
    )
    db.run()
    assert db.links() == set()
    # the boundary is inclusive
    db2 = make(
        {"sched.log": [(2000, "TST-001", 2, None)], "launch.log": [(2000 + MAX_DT, "TST-002", 2, None)]}
    )
    db2.run()
    assert len(db2.links()) == 1


def test_out_of_coverage_is_not_an_orphan(make):
    # the Scheduler file covers 5000..5100 only; the Launcher b at 100 is before it, the one at 5050 is not
    db = make(
        {
            "sched.log": [(5000, "TST-001", 1, None), (5100, "TST-001", 9, None)],
            "launch.log": [(100, "TST-002", 5, None), (5050, "TST-002", 6, None), (5010, "TST-002", 1, None)],
        }
    )
    db.run()
    st = db.stats()[RULE1]
    assert (st["out_of_coverage"], st["unmatched"], st["linked"]) == (1, 1, 1)


def test_unanswered_only_inside_the_coverage_of_the_b_side(make):
    db = make(
        {
            "sched.log": [(1, "TST-001", 1, None), (500, "TST-001", 2, None), (9000, "TST-001", 3, None)],
            "launch.log": [(400, "TST-002", 7, None), (600, "TST-002", 8, None)],
        }
    )
    db.run()
    st = db.stats()[RULE1]
    assert st["unanswered"] == 1  # job 2 at 500; 1 and 9000 are outside the span 400..600


def test_two_collection_groups_are_independent(make):
    db = make()
    db.add("sched.log", 1, (100, "TST-001", 1, None), group=1)
    db.add("launch.log", 1, (110, "TST-002", 1, None), group=2)
    db.run()
    assert db.links() == set()
    assert db.stats(1) == {} or db.stats(1)[RULE1].get("linked", 0) == 0


def test_a_recompute_replaces_only_the_engine_kinds(make):
    world = {"sched.log": [(100, "TST-001", 1, None)], "launch.log": [(110, "TST-002", 1, None)]}
    db = make(world)
    keep = db.add("sched.log", 9, (10_000, "TST-001", 5, None))
    other = db.add("launch.log", 9, (10_001, "TST-002", 5, None))
    for kind in ("normal_sensitive", "payload"):
        db.conn.execute(
            "INSERT INTO link(a_entry_id, b_entry_id, kind, confidence) VALUES (?,?,?,3)", (keep, other, kind)
        )
    db.run()
    n = db.conn.execute("SELECT count(*) FROM link").fetchone()[0]
    db.conn.execute("DELETE FROM entry WHERE id = ?", (other,))
    db.conn.execute("UPDATE source SET n_entries = n_entries + 1")  # signature changes: recompute
    db.run()
    kinds = {r[0] for r in db.conn.execute("SELECT kind FROM link")}
    assert {"normal_sensitive", "payload"} <= kinds and n == 4


def test_an_unchanged_group_writes_nothing(make):
    db = make({"sched.log": [(100, "TST-001", 1, None)], "launch.log": [(110, "TST-002", 1, None)]})
    assert db.run().groups == 1
    assert db.run().groups == 0


def test_an_invalid_rule_is_reported_not_raised(make):
    class Set:
        def link_rules(self):
            return [{"kind": "handoff", "a": "TST-001", "b": "TST-002", "key": "job_id", "scope": "stream"}]

    class Cat:
        def for_family(self, family, sensitive=False):
            return Set()

    db = make({"sched.log": [(100, "TST-001", 1, None)]})
    db.sync()
    s = link_pending(db.conn, Cat())
    assert s.links == 0 and any("scope" in m for _g, m in s.errors)


def test_the_memory_bound_counts_dropped_and_never_links(make, monkeypatch):
    monkeypatch.setattr(links_mod, "MAX_OPEN_KEYS", 3)
    sched = [(100 + i, "TST-001", i, None) for i in range(10)]
    launch = [(120 + i, "TST-002", i, None) for i in range(10)]
    db = make({"sched.log": sched, "launch.log": launch})
    db.run()
    st = db.stats()[RULE1]
    assert st["dropped"] > 0 and st["linked"] == len(db.links())
    # nothing is linked after the stop, and what is linked is right
    assert all(a[1] == b[1] for a, b, _c in db.links())


def test_the_memory_bound_does_not_depend_on_the_order_inside_a_ts(make, monkeypatch):
    monkeypatch.setattr(links_mod, "MAX_OPEN_KEYS", 3)
    rows = [(100, "TST-001", i, None) for i in range(5)] + [(100, "TST-002", i, None) for i in range(5)]
    first = make({"sched.log": rows[:5], "launch.log": rows[5:]})
    first.run()
    second = make({"sched.log": list(reversed(rows[:5])), "launch.log": list(reversed(rows[5:]))})
    second.run()
    assert first.stats()[RULE1] == second.stats()[RULE1]


# ----------------------------------------------------------------------- catalog load errors


def _with(rule_edit):
    text = TOML_JOB.replace('families = ["DMSFH"]', rule_edit)
    return load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})


@pytest.mark.parametrize(
    ("edit", "error"),
    [
        ('families = ["NOPE"]', TemplateReferenceError),
        ('families = ["_common"]', TemplateReferenceError),
        ('families = "DMSFH"', SchemaError),
        ('families = ["DMSFH"]\nwindow_only = true', SchemaError),
        ('families = ["DMSFH"]\norigin = "operation"', SchemaError),
        ('families = ["DMSFH"]\npid_field = "nope"', TemplateReferenceError),
        ('families = ["DMSFH"]\npid_field = "Bad Name"', SchemaError),
    ],
)
def test_catalog_load_errors(edit, error):
    text = TOML_JOB.replace('families = ["DMSFH"]\npid_field = "fh_pid"', edit)
    with pytest.raises(error):
        load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})


def test_families_needs_scope_collection_and_collection_needs_handoff():
    text = TOML_JOB.replace('kind = "handoff"\na = "TST-003"', 'kind = "response"\na = "TST-003"')
    with pytest.raises(SchemaError):
        load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})
    text = TOML_JOB.replace('scope = "collection"\nfamilies', 'scope = "stream"\nfamilies')
    with pytest.raises(SchemaError):
        load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})


def test_a_handoff_without_key_or_with_shared_codes_is_refused():
    with pytest.raises(SchemaError):
        load_texts(
            {
                "dmsjob.toml": TOML_JOB.replace('b = "TST-002"\nkey = "job_id"', 'b = "TST-002"'),
                "dmsfh.toml": TOML_FH,
            }
        )
    with pytest.raises(SchemaError):
        load_texts({"dmsjob.toml": TOML_JOB.replace('b = "TST-002"', 'b = "TST-001"'), "dmsfh.toml": TOML_FH})


def test_the_b_codes_of_another_family_are_invisible_without_families():
    text = TOML_JOB.replace('families = ["DMSFH"]\npid_field = "fh_pid"\n', "")
    with pytest.raises(TemplateReferenceError):
        load_texts({"dmsjob.toml": text, "dmsfh.toml": TOML_FH})


def test_the_shipped_catalog_loads_the_job_rules():
    from dmslog.parse.templates import load_catalog

    cat = load_catalog()
    rules = [r for r in cat.for_family("DMSJOB", sensitive=False).link_rules() if r["kind"] == "handoff"]
    assert [(r["a"], r["b"]) for r in rules] == [("DJB-003", "DJB-051"), ("DJB-050", "DFH-001")]
    assert rules[1]["families"] == ["DMSFH"] and rules[1]["pid_field"] == "fh_pid"
    assert catalog() is not None


def test_the_bound_also_applies_to_the_last_ts(make, monkeypatch):
    """Four pairs of distinct keys at one ts with a bound of 3: nothing is linked, whatever follows."""
    monkeypatch.setattr(links_mod, "MAX_OPEN_KEYS", 3)
    pairs = {"sched.log": [(100, "TST-001", i, None) for i in range(4)]}
    pairs["launch.log"] = [(100, "TST-002", i, None) for i in range(4)]
    alone = make(pairs)
    alone.run()
    assert alone.links() == set()
    assert alone.stats()[RULE1] == {"linked": 0, "dropped": 4}
    later = dict(pairs)
    later["launch.log"] = [*pairs["launch.log"], (500, "TST-002", 99, None)]
    after = make(later)
    after.run()
    assert after.links() == set()  # the same result, plus the later record itself
    assert after.stats()[RULE1]["dropped"] == 5


def test_a_key_repeated_in_the_window_stays_linear(make):
    import time

    n = 4000
    db = make(
        {
            "sched.log": [(100 + i // 100, "TST-001", 1, None) for i in range(n)],
            "launch.log": [(105 + i // 100, "TST-002", 1, None) for i in range(n)],
        }
    )
    started = time.perf_counter()
    db.run()
    assert time.perf_counter() - started < 5
    assert db.links() == set()
    assert db.stats()[RULE1] == {"linked": 0, "ambiguous": n}


def test_degrees_by_pid_class(make):
    """An unknown-PID a matches every b; a known one only the b of its PID or of an unknown PID."""
    db = make(
        {
            "launch.log": [(100, "TST-003", 1, 31), (101, "TST-003", 1, None)],
            "fh1.log": [(110, "TST-101", 1, None)],  # PID 31: compatible with both a
            "fh2.log": [(111, "TST-101", 1, None)],  # PID 32: only with the unknown-PID a
        }
    )
    db.run()
    assert db.links() == set()
    assert db.stats()[RULE2] == {"linked": 0, "ambiguous": 2}


def test_pid_counters_do_not_accumulate(make, monkeypatch):
    """One key kept open by a records while b records of many PIDs pass: the counters stay small."""
    peak = 0
    real = links_mod._ValueState.add_b

    def track(self, pid):
        nonlocal peak
        real(self, pid)
        peak = max(peak, len(self.n_pid))

    monkeypatch.setattr(links_mod._ValueState, "add_b", track)
    db = make()
    n = 20000
    for i in range(n):
        db.add("launch.log", i + 1, (i * 10, "TST-003", 1, 31 if i % 2 else None))
    for i in range(n):
        db.add("fh1.log", i + 1, (i * 10 + 5, "TST-101", 1, None), pid=1000 + i)
    db.run()
    assert peak <= 1  # at most the PID of an open a record, never the 20000 others
