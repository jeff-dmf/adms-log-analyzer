"""Chain of a correlation key (operations and handoffs) over synthetic case databases (no real data)."""

from __future__ import annotations

import contextlib

import pytest

from dmslog.store import chain, create_case_db

T0 = 1_900_000_000_000


class Db:
    """A case database filled by hand: ids in insertion order, one source per stream."""

    def __init__(self, path):
        self.conn = create_case_db(path)
        self.conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')")
        for tid, code in ((1, "TST-001"), (2, "TST-002"), (3, "TST-101")):
            self.conn.execute(
                "INSERT INTO template(id, code, family, class, pattern, def_version) VALUES (?,?,?,?,?,'v')",
                (tid, code, "DMSJOB", "start", "x"),
            )
        self.streams: dict[tuple[int, str], int] = {}
        self.next_entry = 1
        self.next_event = 1

    def stream(self, process, group=1, family="DMSJOB"):
        key = (group, process)
        if key not in self.streams:
            sid = len(self.streams) + 1
            self.conn.execute(
                "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
                " VALUES (?,?,?,?,?,0)",
                (sid, group, family, process, sid),
            )
            self.conn.execute(
                "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope)"
                " VALUES (?, 1, ?, ?, ?, 'adms')",
                (sid, sid, f"{process}.{group}.log", f"{process}.log"),
            )
            self.streams[key] = sid
        return self.streams[key]

    def entry(self, process, ts, key=None, event=None, group=1, family="DMSJOB", key_type="job_id", tpl=1):
        sid = self.stream(process, group, family)
        eid = self.next_entry
        self.next_entry += 1
        self.conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, event_id, msg)"
            " VALUES (?,?,?,?,1,?,1,4,?,?,?)",
            (eid, sid, eid, eid, ts, tpl, event, f"m{eid}"),
        )
        if key is not None:
            self.conn.execute(
                "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)", (key_type, key, eid)
            )
        return eid

    def event(self, process, name, ts, key, group=1, family="DMSJOB", kind="operation", status="ok"):
        sid = self.stream(process, group, family)
        evid = self.next_event
        self.next_event += 1
        self.conn.execute(
            "INSERT INTO event(id, stream_id, kind, name, key, ts_start, ts_end, duration_ms, status, sev,"
            " n_entries)"
            " VALUES (?,?,?,?,?,?,?,?,?,4,2)",
            (evid, sid, kind, name, key, ts, ts + 5, 5, status),
        )
        return evid

    def link(self, a, b, kind="handoff", conf=3):
        self.conn.execute("INSERT INTO link VALUES (?,?,?,?)", (a, b, kind, conf))

    def three_steps(self, job, base, group=1, fh="DMS_FunctionHost_7"):
        """Scheduler -> Launcher -> FunctionHost job; returns the three event ids and the entries."""
        e1 = self.event("Sched", "SchedulerJob", base, job, group)
        e2 = self.event("Launch", "LauncherJob", base + 4, job, group)
        e3 = self.event(fh, "FunctionHostJob", base + 14, job, group, family="DMSFH")
        a = self.entry("Sched", base, job, e1, group)
        b = self.entry("Launch", base + 4, job, e2, group)
        c = self.entry("Launch", base + 10, job, e2, group)
        d = self.entry(fh, base + 14, job, e3, group, family="DMSFH")
        self.entry(fh, base + 20, None, e3, group, family="DMSFH")  # same operation, no key (paired by tid)
        self.link(a, b)
        self.link(b, c, "response", 2)
        self.link(c, d)
        return (e1, e2, e3), (a, b, c, d)


@pytest.fixture
def db(tmp_path):
    d = Db(tmp_path / "case.sqlite")
    yield d
    d.conn.close()


def read(db, value="100001", **kw):
    db.conn.commit()
    return chain.key_chain(db.conn, "job_id", value, **kw)


def test_constants():
    assert (chain.CHAIN_SCAN, chain.CHAIN_OPS, chain.CHAIN_LINKS) == (5000, 200, 500)
    assert chain.CHAIN_LINK_KINDS == ("handoff",)


def test_three_step_job(db):
    (e1, e2, e3), (a, b, c, d) = db.three_steps("100001", T0)
    result = read(db)
    assert result["key"] == {"type": "job_id", "value": "100001"}
    assert [(o["id"], o["name"]) for o in result["operations"]] == [
        (e1, "SchedulerJob"), (e2, "LauncherJob"), (e3, "FunctionHostJob"),
    ]  # fmt: skip
    assert all(o["collection"] == 1 for o in result["operations"])
    assert result["operations"][2]["process"] == "DMS_FunctionHost_7"
    handoffs = result["handoffs"]
    assert [(h["from"]["entry_id"], h["to"]["entry_id"], h["dt_ms"], h["kind"]) for h in handoffs] == [
        (a, b, 4, "handoff"), (c, d, 4, "handoff"),
    ]  # fmt: skip
    assert handoffs[0]["from"]["process"] == "Sched" and handoffs[0]["to"]["process"] == "Launch"
    assert handoffs[1]["to"]["event_id"] == e3 and handoffs[1]["to"]["family"] == "DMSFH"
    assert handoffs[0]["from"]["tpl_code"] == "TST-001" and handoffs[0]["confidence"] == 3
    assert set(handoffs[0]["to"]) == {
        "entry_id", "ts", "stream_id", "event_id", "family", "process", "pool", "instance", "tpl_code",
        "collection",
    }  # fmt: skip
    assert handoffs[0]["to"]["pool"] is None and handoffs[0]["to"]["instance"] is None
    assert result["scanned"] == 4 and result["truncated"] is False  # the keyless record is not read


def test_pool_hook_annotates_ends(db):
    db.three_steps("100001", T0)
    seen = []

    def pool_of(family, process):
        seen.append((family, process))
        return ("DMS_FunctionHost", 7) if family == "DMSFH" else None

    result = read(db, pool_of=pool_of)
    assert result["handoffs"][1]["to"]["pool"] == "DMS_FunctionHost"
    assert result["handoffs"][1]["to"]["instance"] == 7
    assert result["handoffs"][0]["to"]["pool"] is None


def test_job_with_only_function_host(db):
    e3 = db.event("DMS_FunctionHost_2", "FunctionHostJob", T0, "5", family="DMSFH")
    db.entry("DMS_FunctionHost_2", T0, "5", e3, family="DMSFH")
    result = read(db, "5")
    assert [o["id"] for o in result["operations"]] == [e3] and result["handoffs"] == []


def test_key_without_operation_or_unknown(db):
    db.entry("Sched", T0, "7")
    db.entry("Sched", T0 + 1, "7")
    result = read(db, "7")
    assert result["operations"] == [] and result["handoffs"] == [] and result["scanned"] == 2
    empty = read(db, "nope")
    assert empty["operations"] == [] and empty["scanned"] == 0 and empty["truncated"] is False


def test_other_key_type_is_not_mixed(db):
    db.entry("Sched", T0, "9", key_type="msgid")
    assert read(db, "9")["scanned"] == 0
    assert chain.key_chain(db.conn, "msgid", "9")["scanned"] == 1


def test_collection_filter_and_two_groups(db):
    one, _ = db.three_steps("100001", T0, group=1)
    two, entries2 = db.three_steps("100001", T0 + 100_000, group=2)
    both = read(db)
    assert [o["id"] for o in both["operations"]] == [*one, *two]
    assert [o["collection"] for o in both["operations"]] == [1, 1, 1, 2, 2, 2]
    assert len(both["handoffs"]) == 4
    assert {(h["from"]["collection"], h["to"]["collection"]) for h in both["handoffs"]} == {(1, 1), (2, 2)}
    only2 = read(db, collection=2)
    assert [o["id"] for o in only2["operations"]] == list(two)
    assert [h["from"]["collection"] for h in only2["handoffs"]] == [2, 2]
    assert only2["handoffs"][0]["from"]["entry_id"] == entries2[0] and only2["scanned"] == 4


def test_no_handoff_between_groups_or_outside_key(db):
    (_, _, _), (a, *_rest) = db.three_steps("100001", T0, group=1)
    x = db.entry("Launch", T0 + 3, "other")
    y = db.entry("Launch", T0 + 3, "100001", group=2)
    db.link(a, x)  # the far end does not carry the key
    db.link(a, y)  # different collection group, still the same key value: kept (the UI separates groups)
    result = read(db)
    ends = {(h["from"]["entry_id"], h["to"]["entry_id"]) for h in result["handoffs"]}
    assert (a, x) not in ends and (a, y) in ends
    assert (a, y) not in {
        (h["from"]["entry_id"], h["to"]["entry_id"]) for h in read(db, collection=1)["handoffs"]
    }


def test_response_links_are_not_handoffs(db):
    db.three_steps("100001", T0)
    assert {h["kind"] for h in read(db)["handoffs"]} == {"handoff"}


def test_end_without_event_and_without_time(db):
    e1 = db.event("Sched", "SchedulerJob", T0, "8")
    a = db.entry("Sched", T0, "8", e1)
    b = db.entry("Launch", None, "8", None)
    db.link(a, b, conf=2)
    result = read(db, "8")
    (h,) = result["handoffs"]
    assert h["to"]["event_id"] is None and h["to"]["ts"] is None and h["dt_ms"] is None


def test_value_with_slash_and_space(db):
    value = "a/b c%20d'; --"
    e = db.event("Sched", "SchedulerJob", T0, value)
    db.entry("Sched", T0, value, e)
    assert [o["id"] for o in read(db, value)["operations"]] == [e]


def test_non_operation_events_are_ignored(db):
    e = db.event("Sched", "gap", T0, "3", kind="gap")
    db.entry("Sched", T0, "3", e)
    assert read(db, "3")["operations"] == []


def test_order_of_operations_ties_by_id(db):
    e2 = db.event("Sched", "B", T0, "4")
    e1 = db.event("Sched", "A", T0, "4")
    db.entry("Sched", T0, "4", e1)
    db.entry("Sched", T0, "4", e2)
    assert [o["id"] for o in read(db, "4")["operations"]] == [e2, e1]


def test_result_independent_of_insertion_order(tmp_path):
    def build(path, reverse):
        d = Db(path)
        d.stream("Sched")
        d.stream("Launch")
        d.stream("DMS_FunctionHost_7", family="DMSFH")
        steps = [("Sched", T0), ("Launch", T0 + 4), ("DMS_FunctionHost_7", T0 + 8)]
        ids = {}
        for process, ts in reversed(steps) if reverse else steps:
            ids[process] = d.entry(
                process, ts, "11", None, family="DMSFH" if "Function" in process else "DMSJOB"
            )
        d.link(ids["Sched"], ids["Launch"])
        d.link(ids["Launch"], ids["DMS_FunctionHost_7"])
        d.conn.commit()
        out = chain.key_chain(d.conn, "job_id", "11")
        d.conn.close()
        return [(h["from"]["process"], h["to"]["process"], h["dt_ms"]) for h in out["handoffs"]]

    assert build(tmp_path / "a.sqlite", False) == build(tmp_path / "b.sqlite", True)
    assert build(tmp_path / "c.sqlite", False) == [
        ("Sched", "Launch", 4),
        ("Launch", "DMS_FunctionHost_7", 4),
    ]


def test_truncated_by_scanned_records(db):
    db.conn.executemany(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, msg)"
        " VALUES (?,1,?,?,1,?,1,4,1,'m')",
        [(i, i, i, T0 + i) for i in range(1, chain.CHAIN_SCAN + 2)],
    )
    db.stream("Sched")
    db.conn.executemany(
        "INSERT INTO entry_key(key_type, value, entry_id) VALUES ('job_id', 'big', ?)",
        [(i,) for i in range(1, chain.CHAIN_SCAN + 2)],
    )
    result = read(db, "big")
    assert result["scanned"] == chain.CHAIN_SCAN and result["truncated"] is True
    exact = read(db, "big", collection=1)
    assert exact["scanned"] == chain.CHAIN_SCAN and exact["truncated"] is True
    db.conn.execute("DELETE FROM entry_key WHERE entry_id = ?", (chain.CHAIN_SCAN + 1,))
    result = read(db, "big")
    assert result["scanned"] == chain.CHAIN_SCAN and result["truncated"] is False


def test_truncated_by_operations(db):
    for i in range(chain.CHAIN_OPS + 1):
        e = db.event("Sched", f"Op{i:03d}", T0 + i, "many")
        db.entry("Sched", T0 + i, "many", e)
    result = read(db, "many")
    assert len(result["operations"]) == chain.CHAIN_OPS and result["truncated"] is True
    assert result["operations"][0]["name"] == "Op000" and result["operations"][-1]["name"] == "Op199"
    assert result["scanned"] == chain.CHAIN_OPS + 1


def test_exactly_at_the_limits_is_not_truncated(db):
    for i in range(chain.CHAIN_OPS):
        e = db.event("Sched", "Op", T0 + i, "edge")
        db.entry("Sched", T0 + i, "edge", e)
    assert read(db, "edge")["truncated"] is False


def test_truncated_by_handoffs(db):
    for i in range(chain.CHAIN_LINKS + 1):
        a = db.entry("Sched", T0 + 2 * i, "links")
        b = db.entry("Launch", T0 + 2 * i + 1, "links")
        db.link(a, b)
    result = read(db, "links")
    assert len(result["handoffs"]) == chain.CHAIN_LINKS and result["truncated"] is True
    assert [h["from"]["ts"] for h in result["handoffs"]] == sorted(
        h["from"]["ts"] for h in result["handoffs"]
    )
    assert result["handoffs"][0]["from"]["ts"] == T0


def test_collection_filter_cost_follows_the_key_not_the_collection(db, monkeypatch):
    db.stream("Sched")
    db.conn.executemany(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, msg)"
        " VALUES (?,1,?,?,1,?,1,4,1,'m')",
        [(i, i, i, T0 + i) for i in range(1, 20_001)],
    )
    db.conn.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('job_id', 'rare', 10)")
    db.conn.commit()
    steps = [0]
    real_guard = chain.read_guard

    @contextlib.contextmanager
    def counting_guard(conn, timeout_s):
        with real_guard(conn, timeout_s) as expired:
            conn.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, 10)
            yield expired

    monkeypatch.setattr(chain, "read_guard", counting_guard)
    result = chain.key_chain(db.conn, "job_id", "rare", collection=1)
    assert result["scanned"] == 1
    assert steps[0] < 50  # x10 VM instructions; materializing the collection costs thousands of ticks
