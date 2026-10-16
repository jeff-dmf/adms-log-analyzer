"""Expected sequences and missing steps (ROADMAP 9.12), read from the events (synthetic rows)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from dmslog.ingest.pipeline import run_ingest
from dmslog.store.db import connect, create_case_db
from dmslog.store.events import Cursor, EventFilter
from dmslog.store.sequences import SEQUENCES, list_missing, missing_summary

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "families"

# (id, stream, kind, name, start_entry_id, end_entry_id, ts_start, status, detail, expected (missing, cause))
EVENTS = [
    (1, 1, "operation", "GetIncidents", 10, 11, 100, "ok", None, None),
    (2, 1, "operation", "GetIncidents", 12, None, 200, "truncated", {"closed_by": "new_start"},
     ("end", "superseded")),
    (3, 1, "operation", "GetIncidents", 13, None, 300, "truncated", {"closed_by": "evicted"},
     ("end", "capacity")),
    (4, 1, "operation", "SendWorks", 14, None, 400, "truncated", {"closed_by": "eof"},
     ("end", "capture_cut")),
    (5, 1, "operation", "SendWorks", 15, None, 500, "open", {"closed_by": "eof"}, ("end", "end_of_log")),
    (6, 1, "operation", "SendWorks", 16, None, 600, "fail", {"closed_by": "eof", "fail_how": "fail_code"},
     ("end", "end_of_log")),
    (7, 1, "operation", "SendWorks", 17, 18, 700, "fail", {"fail_how": "end_text"}, None),
    (8, 1, "startup", None, 20, 21, 800, "ok", {"closed_by": "hot", "hot_ts": 801}, None),
    (9, 1, "startup", None, 22, 23, 900, "ok", {"closed_by": "timeout"}, ("hot", "timeout")),
    (10, 2, "startup", None, 24, 25, 1000, "open", {"closed_by": "eof"}, ("hot", "end_of_log")),
    (11, 2, "map_reload", "OMS_CREW", 26, 27, 1100, "ok", {"map": "OMS_CREW"}, None),
    (12, 2, "map_reload", "OMS_CREW", 28, 28, 1200, "truncated", {"map": "OMS_CREW"}, ("open", "superseded")),
    (13, 2, "map_reload", "OMS_CREW", 29, 29, 1300, "open", {"map": "OMS_CREW"}, ("open", "end_of_log")),
    (14, 2, "retry_seq", "Work", 30, 31, 1400, "ok", {"outcome": "sent"}, None),
    (15, 2, "retry_seq", "Work", 32, 33, 1500, "fail", {"outcome": "failed"}, None),
    (16, 2, "retry_seq", "Work", 34, 35, 1600, "open", {"outcome": "open", "evicted": True},
     ("outcome", "capacity")),
    (17, 2, "retry_seq", "Work", 36, 37, 1700, "open", {"outcome": "open"}, ("outcome", "end_of_log")),
    (18, 2, "gap", None, 38, 39, 1800, "ok", None, None),
    (19, 2, "startup", None, 40, 41, 1900, "open", "{not json", ("hot", "end_of_log")),
    (20, 2, "startup", None, 42, 43, 1900, "ok", {"closed_by": "hot", "hot_ts": None}, ("hot", "end_of_log")),
]  # fmt: skip


def _case(tmp_path: Path):
    path = tmp_path / "case.sqlite"
    conn = create_case_db(path)
    with conn:
        conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'Log', 'x')")
        conn.executemany(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES (?,1,?,?,?,0)",
            [(1, "AOSR", "OsrAdapter", 1), (2, "ASMN", "SmnAdapter", 2)],
        )
        for eid, stream, kind, name, start, end, ts, status, detail, _ in EVENTS:
            text = detail if isinstance(detail, str) or detail is None else json.dumps(detail)
            conn.execute(
                "INSERT INTO event(id, stream_id, kind, name, start_entry_id, end_entry_id, ts_start, status,"
                " sev, detail) VALUES (?,?,?,?,?,?,?,?,4,?)",
                (eid, stream, kind, name, start, end, ts, status, text),
            )
    conn.close()
    return connect(path, readonly=True)


def _expected(kinds=None):
    return [(e[0], *e[9]) for e in EVENTS if e[9] is not None and (kinds is None or e[2] in kinds)]


def _all(conn, flt, limit):
    items, after = [], None
    while True:
        page, nxt = list_missing(conn, flt, limit=limit, after=after)
        items.extend(page)
        if nxt is None:
            return items
        after = Cursor.parse(nxt)


def _ids(conn, flt):
    return [i["event"]["id"] for i in _all(conn, flt, 50)]


def test_each_sequence_reports_its_missing_step_and_cause(tmp_path):
    conn = _case(tmp_path)
    for limit in (1, 2, 7, 1000):
        items = _all(conn, EventFilter(), limit)
        assert [(i["event"]["id"], i["missing"][0], i["cause"]) for i in items] == _expected()
    item = next(i for i in _all(conn, EventFilter(), 50) if i["event"]["id"] == 2)
    assert item["steps"] == ["start", "end"] and item["proven"] is True
    assert item["event"]["family"] == "AOSR" and item["event"]["detail"] == {"closed_by": "new_start"}
    proven = {i["event"]["id"] for i in items if i["proven"]}
    assert proven == {2, 9, 12}


def test_filters_of_the_event_list_apply(tmp_path):
    conn = _case(tmp_path)
    items = _all(conn, EventFilter(kinds=("startup", "gap", "nope")), 50)
    assert [i["event"]["id"] for i in items] == [e[0] for e in _expected({"startup"})]
    assert list_missing(conn, EventFilter(kinds=("gap",))) == ([], None)
    assert _ids(conn, EventFilter(stream_id=2)) == [10, 12, 13, 16, 17, 19, 20]
    assert _ids(conn, EventFilter(statuses=("open",))) == [5, 10, 13, 16, 17, 19]
    assert _ids(conn, EventFilter(ts_from=1200, ts_to=1600)) == [12, 13, 16]


def test_summary_counts_by_kind_family_name_and_cause(tmp_path):
    conn = _case(tmp_path)
    summary = missing_summary(conn, EventFilter())
    assert summary["total"] == len(_expected())
    groups = {(g["kind"], g["family"], g["name"]): g for g in summary["groups"]}
    assert list(groups) == [
        ("map_reload", "ASMN", "OMS_CREW"),
        ("operation", "AOSR", "GetIncidents"),
        ("operation", "AOSR", "SendWorks"),
        ("retry_seq", "ASMN", "Work"),
        ("startup", "AOSR", None),
        ("startup", "ASMN", None),
    ]
    assert groups[("operation", "AOSR", "SendWorks")] == {
        "kind": "operation", "family": "AOSR", "name": "SendWorks", "missing": "end", "n": 3,
        "causes": {"capture_cut": 1, "end_of_log": 2},
    }  # fmt: skip
    assert groups[("retry_seq", "ASMN", "Work")]["causes"] == {"capacity": 1, "end_of_log": 1}
    assert groups[("startup", "ASMN", None)]["n"] == 3
    assert missing_summary(conn, EventFilter(kinds=("gap",))) == {"groups": [], "total": 0}


def test_the_catalog_and_the_causes_agree():
    assert set(SEQUENCES) == {"operation", "startup", "map_reload", "retry_seq"}
    for seq in SEQUENCES.values():
        assert seq.missing in seq.steps and seq.steps[0] != seq.missing


def test_real_derivation_of_the_family_fixtures(tmp_path):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "SVC"
    shutil.copytree(FIXTURES / "dnaApp_zone1" / "SVC", folder)
    path = tmp_path / "case.sqlite"
    conn = create_case_db(path)
    run_ingest(conn, tmp_path / "Log")
    conn.close()
    ro = connect(path, readonly=True)
    items = _all(ro, EventFilter(), 200)
    expected = ro.execute(
        "SELECT count(*) FROM event WHERE (kind = 'operation' AND end_entry_id IS NULL)"
        " OR (kind = 'startup' AND json_extract(detail, '$.hot_ts') IS NULL)"
        " OR (kind = 'map_reload' AND status <> 'ok') OR (kind = 'retry_seq' AND status = 'open')"
    ).fetchone()[0]
    assert len(items) == expected == missing_summary(ro, EventFilter())["total"]
    assert all(i["missing"][0] in i["steps"] for i in items)
