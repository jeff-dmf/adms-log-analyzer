"""Service layer of the message queue routes (msgqueue/service.py; issue #74, Q5). Synthetic data only."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import nbfx_writer as nw  # noqa: E402

from dmslog.msgqueue import service  # noqa: E402
from dmslog.msgqueue.service import QueueNotFound, QueueParamError  # noqa: E402
from dmslog.store import create_case_db, queue  # noqa: E402
from dmslog.store.queue import BankIn, ItemIn  # noqa: E402

G1 = "11111111-2222-3333-4444-555555555555"
MEMBER = "SchneiderElectricData/ADMS/data/save_cases/LocalPersistenceStore/0/__~Cache~__Adapter__LPS.db3"


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    conn.execute(
        "INSERT INTO collection(id, kind, path, ers_group, added_at) VALUES (1, 'file', 'x', 1, 't')"
    )
    conn.commit()
    yield conn
    conn.close()


def item(n, *, ts=None, state="pending", mid=None, body=b"", size=None, mtype="TypeA"):
    return ItemIn(
        item_id=n,
        message_id=mid,
        msg_type=mtype,
        status="Sent" if state == "sent" else "",
        state=state,
        file_location="synthetic-location",
        ts=ts,
        size=len(body) if size is None else size,
        body=body or None,
    )


def store(conn, items, *, cid=1, member=MEMBER, status="ready", error=None, snapshot="wal_applied"):
    return queue.replace_bank(
        conn,
        cid,
        member,
        BankIn(
            cache="Cache",
            adapter="Adapter",
            db_size=10,
            wal_size=None,
            fingerprint="f",
            status=status,
            read_at="2026-01-01T00:00:00",
            snapshot=snapshot if status == "ready" else None,
            error=error,
            items=list(items),
        ),
    )


def msg_body(n=1):
    return nw.encode(nw.make_message(n))


def row_of(db, n):
    return db.execute("SELECT id FROM queue_item WHERE item_id = ?", (n,)).fetchone()[0]


# --------------------------------------------------------------------------- summary


def test_case_without_queue_tables_is_disabled_and_empty(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE collection(id INTEGER PRIMARY KEY, ers_group INTEGER)")
    assert service.summary(conn) == {
        "enabled": False,
        "dbs": [],
        "totals": {"items": 0, "pending": 0, "sent": 0, "other": 0},
    }
    assert service.items(conn, {})["items"] == []
    conn.close()


def test_summary_shape_tags_names_and_warnings(db):
    store(db, [item(1, ts=1000, state="sent"), item(2, ts=2000)])
    store(db, [], member=MEMBER.replace("Cache", "Other"), status="error", error="corrupt")
    store(db, [item(3, ts=3000)], member=MEMBER.replace("Cache", "NoWal"), snapshot="no_wal")
    queue.set_enabled(db, True)
    db.commit()
    out = service.summary(db)
    assert out["enabled"] is True
    assert out["totals"] == {"items": 3, "pending": 2, "sent": 1, "other": 0}
    by_name = {d["name"]: d for d in out["dbs"]}
    assert set(by_name) == {
        "__~Cache~__Adapter__LPS.db3",
        "__~Other~__Adapter__LPS.db3",
        "__~NoWal~__Adapter__LPS.db3",
    }
    first = by_name["__~Cache~__Adapter__LPS.db3"]
    assert first["tag"] == "C1" and first["collection"] == 1
    assert (first["items"], first["pending"], first["sent"], first["other"]) == (2, 1, 1, 0)
    assert set(first) == {
        "id", "collection", "tag", "name", "cache", "adapter", "snapshot", "status", "error", "warnings",
        "items", "pending", "sent", "other", "read_at",
    }  # fmt: skip
    assert by_name["__~Other~__Adapter__LPS.db3"]["error"] == "corrupt"
    assert by_name["__~NoWal~__Adapter__LPS.db3"]["warnings"] == ["no_wal"]


# --------------------------------------------------------------------------- items and parameters


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ({"limit": "0"}, "bad_limit"),
        ({"limit": "1001"}, "bad_limit"),
        ({"limit": "abc"}, "bad_limit"),
        ({"limit": "-1"}, "bad_limit"),
        ({"after": "garbage"}, "bad_cursor"),
        ({"after": "1:x"}, "bad_cursor"),
        ({"state": "pending,bogus"}, "bad_state"),
        ({"type": ",".join(f"t{i}" for i in range(33))}, "bad_type"),
        ({"type": "a" * 129}, "bad_type"),
        ({"type": "a,,b"}, "bad_type"),
        ({"q": "not a guid"}, "bad_query"),
        ({"q": "12x"}, "bad_query"),
        ({"q": "9" * 20}, "bad_query"),
        ({"collection": "a"}, "invalid_field"),
        ({"collection": "1,,2"}, "invalid_field"),
        ({"db": ",".join(str(i) for i in range(65))}, "invalid_field"),
    ],
)
def test_items_parameter_errors_carry_stable_codes(db, query, code):
    with pytest.raises(QueueParamError) as err:
        service.items(db, query)
    assert err.value.code == code
    assert err.value.http_status == 400
    assert err.value.message


def test_documented_codes_have_messages():
    assert set(service.ERROR_CODES) == {
        "bad_limit", "bad_cursor", "bad_state", "bad_type", "bad_query", "invalid_field",
        "bad_form", "bad_queue_item_id", "queue_item_not_found",
    }  # fmt: skip
    assert all(service.MESSAGES[c] for c in service.ERROR_CODES)


def test_items_shape_filters_and_string_item_id(db):
    store(db, [item(1, ts=3000, mid=G1, mtype="TypeA"), item(2, ts=1000, state="sent", mtype="TypeB")])
    out = service.items(db, {})
    assert [i["item_id"] for i in out["items"]] == ["2", "1"]  # FIFO by ts
    assert out["next"] is None
    assert out["counts"] == {"pending": 1, "sent": 1, "other": 0}
    first = out["items"][1]
    assert first["tag"] == "C1" and first["collection"] == 1
    assert first["message_id"] == G1 and first["preview"] == "no_body"
    assert first["log"] == {"msgid": 0, "im_id": 0, "payload_id": None}
    assert set(first) == {
        "id", "db", "collection", "tag", "item_id", "message_id", "msg_type", "status", "state",
        "file_location", "ts", "ts_adjusted", "size", "preview", "log",
    }  # fmt: skip
    assert [i["item_id"] for i in service.items(db, {"state": "sent"})["items"]] == ["2"]
    assert [i["item_id"] for i in service.items(db, {"type": "TypeA"})["items"]] == ["1"]
    assert [i["item_id"] for i in service.items(db, {"q": "{" + G1.upper() + "}"})["items"]] == ["1"]
    assert [i["item_id"] for i in service.items(db, {"q": "2"})["items"]] == ["2"]
    assert service.items(db, {"collection": "99"})["items"] == []


def test_items_paginate_with_the_keyset_cursor(db):
    store(db, [item(n, ts=1000 + n) for n in range(1, 6)])
    seen, after = [], None
    while True:
        query = {"limit": "2"} | ({"after": after} if after else {})
        page = service.items(db, query)
        seen += [i["item_id"] for i in page["items"]]
        after = page["next"]
        if after is None:
            break
    assert seen == ["1", "2", "3", "4", "5"]


def test_ts_adjusted_uses_the_collection_clock_offset(db):
    store(db, [item(1, ts=10_000), item(2, ts=None)])
    assert [i["ts_adjusted"] for i in service.items(db, {})["items"]] == [None, 10_000]
    db.execute("UPDATE collection SET clock_offset_ms = 500 WHERE id = 1")
    db.commit()
    got = {i["item_id"]: i for i in service.items(db, {})["items"]}
    assert got["1"]["ts"] == 10_000 and got["1"]["ts_adjusted"] == 10_500
    assert got["2"]["ts_adjusted"] is None


def test_log_links_come_from_the_log_indexes(db):
    store(db, [item(1, ts=1000, mid=G1), item(2, ts=2000)])
    db.execute("INSERT INTO entry_key(entry_id, key_type, value) VALUES (1, 'msgid', ?)", (G1,))
    db.execute("INSERT INTO entry_key(entry_id, key_type, value) VALUES (2, 'im_id', '2')")
    db.commit()
    got = {i["item_id"]: i["log"] for i in service.items(db, {})["items"]}
    assert got["1"] == {"msgid": 1, "im_id": 0, "payload_id": None}
    assert got["2"] == {"msgid": 0, "im_id": 1, "payload_id": None}


# --------------------------------------------------------------------------- preview


def test_preview_wire_and_stored_with_a_valid_body(db):
    store(db, [item(1, ts=1000, mid=G1, body=msg_body(1))])
    rid = str(row_of(db, 1))
    wire = service.preview(db, rid, {}, case="c")
    assert wire["available"] is True and wire["reason"] is None and wire["form"] == "wire"
    assert wire["decode"] == {"ok": True, "error": None, "offset": None}
    assert wire["item"]["preview"] == "available"
    assert wire["reconstruction"]["basis"] in ("catalog", "none")
    assert isinstance(wire["reconstruction"]["notes"], list)
    fmt = wire["formatted"]
    assert fmt["ok"] is True and fmt["kind"] == "xml" and "text-1" in fmt["text"]
    assert set(fmt) == {"text", "ok", "kind", "reason", "truncated_display"}
    stored = service.preview(db, rid, {"form": "stored"}, case="c")
    assert stored["form"] == "stored" and stored["reconstruction"] is None
    assert stored["formatted"]["ok"] is True and "WorksEventMessageType" in stored["formatted"]["text"]
    json.dumps(wire)
    json.dumps(stored)


def test_preview_with_a_truncated_body_answers_instead_of_failing(db):
    store(db, [item(1, ts=1000, body=msg_body(1)[:-6])])
    rid = str(row_of(db, 1))
    for query in ({}, {"form": "stored"}):
        out = service.preview(db, rid, query, case="c")
        assert out["available"] is True
        assert out["decode"]["ok"] is False and out["decode"]["error"]
        assert isinstance(out["decode"]["offset"], int)
        assert out["formatted"] is None and out["reconstruction"] is None


def test_preview_with_garbage_bytes_never_raises(db):
    store(db, [item(1, ts=1000, body=b"\x00\x01garbage")])
    out = service.preview(db, str(row_of(db, 1)), {}, case="c")
    assert out["decode"]["ok"] is False and out["decode"]["error"]


def test_preview_without_body_and_too_large(db):
    store(db, [item(1, ts=1000, size=0), item(2, ts=2000, size=queue.MAX_BODY_BYTES + 1)])
    none = service.preview(db, str(row_of(db, 1)), {}, case="c")
    assert none["available"] is False and none["reason"] == "no_body" and none["decode"] is None
    assert none["formatted"] is None
    large = service.preview(db, str(row_of(db, 2)), {}, case="c")
    assert large["available"] is False and large["reason"] == "too_large"
    assert large["item"]["preview"] == "too_large"


def test_preview_error_codes(db):
    store(db, [item(1, ts=1000, body=msg_body(1))])
    rid = str(row_of(db, 1))
    for raw in ("abc", "", "-1", "1.5", "9" * 30):
        with pytest.raises(QueueParamError) as err:
            service.preview(db, raw, {}, case="c")
        assert err.value.code == "bad_queue_item_id"
    with pytest.raises(QueueParamError) as err:
        service.preview(db, rid, {"form": "xml"}, case="c")
    assert err.value.code == "bad_form" and err.value.http_status == 400
    with pytest.raises(QueueNotFound) as err:
        service.preview(db, "999999", {}, case="c")
    assert err.value.code == "queue_item_not_found" and err.value.http_status == 404
    assert isinstance(err.value, QueueParamError)


def test_preview_writes_nothing(db):
    store(db, [item(1, ts=1000, body=msg_body(1))])
    before = db.total_changes
    service.preview(db, str(row_of(db, 1)), {}, case="c", generation=3)
    service.preview(db, str(row_of(db, 1)), {"form": "stored"}, case="c", generation=3)
    assert db.total_changes == before


# --------------------------------------------------------------------------- privacy


def test_responses_carry_no_file_path(db, tmp_path):
    store(db, [item(1, ts=1000, mid=G1, body=msg_body(1))])
    rid = str(row_of(db, 1))
    blobs = [
        json.dumps(service.summary(db)),
        json.dumps(service.items(db, {})),
        json.dumps(service.preview(db, rid, {}, case="c")),
        json.dumps(service.preview(db, rid, {"form": "stored"}, case="c")),
    ]
    for blob in blobs:
        assert "LocalPersistenceStore" not in blob and "SchneiderElectricData" not in blob
        assert str(tmp_path) not in blob
