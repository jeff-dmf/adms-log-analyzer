"""Store side of the message queue (migration 8, store/queue.py; issue #74). Synthetic data only."""

from __future__ import annotations

import sqlite3

import pytest

from dmslog.store import create_case_db, queue
from dmslog.store.db import MIGRATIONS, migrate
from dmslog.store.query import Cursor, CursorError
from dmslog.store.queue import BankIn, ItemIn, QueueInputError
from dmslog.store.writer import IndexWriter

G1 = "11111111-2222-3333-4444-555555555555"
G2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def add_collection(conn, cid, group=None):
    conn.execute(
        "INSERT INTO collection(id, kind, path, ers_group, added_at) VALUES (?, 'file', 'x', ?, 't')",
        (cid, group if group is not None else cid),
    )
    conn.commit()


def item(n, *, ts=None, state="pending", mid=None, mtype="TypeA", body=b"<x/>", size=None):
    return ItemIn(
        item_id=n,
        message_id=mid,
        msg_type=mtype,
        status="Sent" if state == "sent" else "",
        state=state,
        file_location=None,
        ts=ts,
        size=len(body) if size is None and body is not None else (size or 0),
        body=body,
    )


def bank(items=(), *, status="ready", cache="CacheOne", fingerprint="f1", snapshot="wal_applied", error=None):
    return BankIn(
        cache=cache,
        adapter="TestAdapter",
        db_size=100,
        wal_size=None,
        fingerprint=fingerprint,
        status=status,
        read_at="2026-01-01T00:00:00",
        snapshot=snapshot if status == "ready" else None,
        error=error,
        items=items if callable(getattr(items, "__next__", None)) else list(items),
    )


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def order(page):
    return [(i["db"], i["item_id"]) for i in page.items]


# ---------------------------------------------------------------------------------- schema


def test_tables_and_indexes_exist_and_username_is_not_a_column(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert {"queue_db", "queue_item", "queue_body", "ix_queue_item_order", "ix_queue_item_state"} <= names
    assert "ix_queue_item_msg" in names
    cols = {
        t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        for t in ("queue_db", "queue_item", "queue_body")
    }
    assert not any("user" in c.lower() for names_ in cols.values() for c in names_)
    assert cols["queue_body"] == ["item_id", "body"]
    assert queue.has_queue(db) and not queue.is_enabled(db)


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO queue_item(db_id, item_id, state, size, body_stored) VALUES (99, 1, 'sent', 0, 0)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO queue_body(item_id, body) VALUES (99, x'00')")


def test_a_v7_case_answers_disabled_and_empty(tmp_path):
    conn = sqlite3.connect(tmp_path / "v7.sqlite")
    for v in range(1, 8):
        conn.execute("BEGIN IMMEDIATE")
        MIGRATIONS[v](conn)
        conn.execute(f"PRAGMA user_version={v}")
        conn.execute("COMMIT")
    assert not queue.has_queue(conn)
    assert queue.summary(conn) == {
        "enabled": False,
        "dbs": [],
        "totals": {"items": 0, "pending": 0, "sent": 0, "other": 0},
    }
    page = queue.list_items(conn)
    assert (page.items, page.next, page.counts) == ([], None, {"pending": 0, "sent": 0, "other": 0})
    assert queue.get_body(conn, 1) is None and queue.get_item(conn, 1) is None
    assert queue.delete_queue(conn) == (0, 0)
    assert queue.delete_collections(conn, [1]) == (0, 0)
    assert queue.fingerprint_of(conn, 1, "a.db3") is None
    conn.close()


def test_the_option_is_a_meta_key(db):
    queue.set_enabled(db, True)
    assert queue.is_enabled(db) and queue.summary(db)["enabled"] is True
    queue.set_enabled(db, False)
    assert not queue.is_enabled(db)
    assert db.execute("SELECT count(*) FROM meta WHERE key = 'queue.enabled'").fetchone()[0] == 0


# ---------------------------------------------------------------------------- replace_bank


def test_replace_bank_stores_items_and_bodies_and_replaces_in_place(db):
    add_collection(db, 1)
    bid = queue.replace_bank(db, 1, "d/a.db3", bank([item(1, ts=10, mid=G1), item(2, ts=20)]))
    assert (count(db, "queue_item"), count(db, "queue_body")) == (2, 2)
    assert db.execute("SELECT items, status, snapshot FROM queue_db").fetchone() == (
        2,
        "ready",
        "wal_applied",
    )
    again = queue.replace_bank(db, 1, "d/a.db3", bank([item(7, ts=5)], fingerprint="f2"))
    assert again == bid  # same row, same id
    assert [r[0] for r in db.execute("SELECT item_id FROM queue_item")] == [7]
    assert count(db, "queue_body") == 1
    assert queue.fingerprint_of(db, 1, "d/a.db3") == ("f2", "ready")
    assert not db.in_transaction


def test_an_error_bank_keeps_only_its_row_and_drops_the_old_items(db):
    add_collection(db, 1)
    queue.replace_bank(db, 1, "a.db3", bank([item(1), item(2)]))
    queue.replace_bank(db, 1, "a.db3", bank(status="error", error="corrupt"))
    assert (count(db, "queue_item"), count(db, "queue_body")) == (0, 0)
    row = db.execute("SELECT status, error, items, snapshot FROM queue_db").fetchone()
    assert row == ("error", "corrupt", 0, None)
    queue.replace_bank(db, 1, "a.db3", bank(status="not_a_queue", error="ignored"))
    assert db.execute("SELECT status, error FROM queue_db").fetchone() == ("not_a_queue", None)


def test_replace_bank_is_one_transaction(db):
    add_collection(db, 1)
    queue.replace_bank(db, 1, "a.db3", bank([item(1)]))

    def broken():
        yield item(2)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        queue.replace_bank(db, 1, "a.db3", bank(broken(), fingerprint="new"))
    assert [r[0] for r in db.execute("SELECT item_id FROM queue_item")] == [1]  # the old reading survives
    assert queue.fingerprint_of(db, 1, "a.db3") == ("f1", "ready")
    assert not db.in_transaction
    db.execute("INSERT INTO meta(key, value) VALUES ('k', 'v')")
    with pytest.raises(RuntimeError):
        queue.replace_bank(db, 1, "a.db3", bank())  # the caller's open transaction is not touched
    assert db.in_transaction
    db.rollback()


def test_invalid_input_is_refused_before_writing(db):
    add_collection(db, 1)
    with pytest.raises(QueueInputError):
        queue.replace_bank(db, 1, "a.db3", bank(status="weird"))
    with pytest.raises(QueueInputError):
        queue.replace_bank(db, 1, "a.db3", bank(snapshot="nope"))
    with pytest.raises(QueueInputError):
        queue.replace_bank(db, 1, "a.db3", bank([item(1, state="done")]))
    assert count(db, "queue_db") == 0 and count(db, "queue_item") == 0 and not db.in_transaction


def test_duplicates_and_out_of_range_ids_are_skipped(db):
    add_collection(db, 1)
    bid = queue.replace_bank(db, 1, "a.db3", bank([item(1), item(1), item(-4), item(2**63)]))
    assert count(db, "queue_item") == 1
    assert db.execute("SELECT items FROM queue_db WHERE id = ?", (bid,)).fetchone()[0] == 1


def test_size_limits(db, monkeypatch):
    add_collection(db, 1)
    monkeypatch.setattr(queue, "MAX_BODY_BYTES", 10)
    monkeypatch.setattr(queue, "MAX_BANK_BODY_BYTES", 15)
    monkeypatch.setattr(queue, "MAX_ITEMS", 5)
    items = [
        item(1, body=b"123456"),  # stored (6)
        item(2, body=b"x" * 11),  # above the per-item limit
        item(3, body=b"123456"),  # stored (12)
        item(4, body=b"123456"),  # past the bank total (18 > 15)
        item(5, body=None, size=0),  # empty
        item(6, body=b"1"),  # past MAX_ITEMS
    ]
    queue.replace_bank(db, 1, "a.db3", bank(items))
    rows = db.execute("SELECT item_id, body_stored, size FROM queue_item ORDER BY item_id").fetchall()
    assert rows == [(1, 1, 6), (2, 0, 11), (3, 1, 6), (4, 0, 6), (5, 0, 0)]
    assert count(db, "queue_body") == 2
    got = {i["item_id"]: i["preview"] for i in queue.list_items(db).items}
    assert got == {1: "available", 2: "too_large", 3: "available", 4: "no_body", 5: "no_body"}
    assert queue.summary(db)["dbs"][0]["bodies_missing"] == 2


# ------------------------------------------------------------------------------- list_items


def fill(db):
    add_collection(db, 1)
    add_collection(db, 2)
    queue.replace_bank(
        db,
        1,
        "d/a.db3",
        bank(
            [
                item(1, ts=100, state="sent", mid=G1, mtype="TypeA"),
                item(2, ts=100, state="pending", mid=G2, mtype="TypeB"),
                item(3, ts=100, state="pending", mtype="TypeA"),
                item(4, ts=None, state="other", mtype=None),
                item(5, ts=50, state="pending", mtype="TypeA"),
            ]
        ),
    )
    queue.replace_bank(db, 2, "b.db3", bank([item(1, ts=100, state="sent", mtype="TypeB")], cache="CacheTwo"))


def test_keyset_is_stable_with_equal_timestamps_and_null_first(db):
    fill(db)
    full = queue.list_items(db)
    assert order(full) == [(1, 4), (1, 5), (1, 1), (1, 2), (1, 3), (2, 1)]  # NULL ts first, then (ts, id)
    seen = []
    cursor = None
    pages = 0
    while True:
        page = queue.list_items(db, limit=2, after=cursor)
        seen += order(page)
        pages += 1
        if page.next is None:
            break
        cursor = str(page.next)
    assert seen == order(full) and pages == 3
    # a cursor taken at a tied timestamp skips nothing and repeats nothing, even with a new row added after
    mid = queue.list_items(db, limit=3)
    assert mid.next is not None
    queue.replace_bank(db, 2, "c.db3", bank([item(1, ts=100)], cache="CacheThree"))
    rest = queue.list_items(db, after=mid.next)
    assert order(rest)[:2] == [(1, 2), (1, 3)]


def test_cursor_accepts_an_object_and_the_string_form(db):
    fill(db)
    first = queue.list_items(db, limit=1)
    assert isinstance(first.next, Cursor) and str(first.next).startswith("null:")
    by_object = queue.list_items(db, limit=1, after=first.next)
    assert order(by_object) == order(queue.list_items(db, limit=1, after=str(first.next)))
    with pytest.raises(CursorError):
        queue.list_items(db, after="garbage")


def test_filters(db):
    fill(db)
    assert order(queue.list_items(db, states=["pending"])) == [(1, 5), (1, 2), (1, 3)]
    assert order(queue.list_items(db, states=["sent", "other"])) == [(1, 4), (1, 1), (2, 1)]
    assert order(queue.list_items(db, states=[])) == []
    assert order(queue.list_items(db, types=["TypeB"])) == [(1, 2), (2, 1)]
    assert order(queue.list_items(db, collections=[2])) == [(2, 1)]
    assert order(queue.list_items(db, collections=[])) == []
    assert order(queue.list_items(db, dbs=[1], types=["TypeA"], states=["pending"])) == [(1, 5), (1, 3)]
    assert order(queue.list_items(db, message_id="{" + G1.upper() + "}")) == [(1, 1)]
    assert order(queue.list_items(db, item_id=4)) == [(1, 4)]
    for bad in (
        {"states": ["done"]},
        {"message_id": "not-a-guid"},
        {"item_id": -1},
        {"limit": 0},
        {"limit": queue.MAX_LIMIT + 1},
        {"limit": True},
        {"collections": ["1"]},
        {"dbs": list(range(65))},
        {"types": ["t"] * 33},
    ):
        with pytest.raises(QueueInputError):
            queue.list_items(db, **bad)


def test_counts_ignore_the_state_filter_and_the_cursor(db):
    fill(db)
    page = queue.list_items(db, states=["sent"], limit=1)
    assert page.counts == {"pending": 3, "sent": 2, "other": 1}
    page = queue.list_items(db, dbs=[1], types=["TypeA"], after="100:1")
    assert page.counts == {"pending": 2, "sent": 1, "other": 0}


def test_item_shape_never_carries_the_body(db):
    fill(db)
    row = queue.list_items(db, item_id=2).items[0]
    assert set(row) == {
        "id", "db", "collection_id", "collection", "item_id", "message_id", "msg_type", "status",
        "state", "file_location", "ts", "size", "preview",
    }  # fmt: skip
    assert row["message_id"] == G2 and row["collection"] == 1 and row["preview"] == "available"
    assert queue.get_item(db, row["id"]) == row and queue.get_item(db, 9999) is None


def test_get_body_reads_one_item_and_lists_never_select_queue_body(db):
    fill(db)
    target = queue.list_items(db, item_id=3).items[0]["id"]
    assert queue.get_body(db, target) == b"<x/>"
    assert queue.get_body(db, 9999) is None
    seen = []
    db.set_trace_callback(seen.append)
    queue.list_items(db)
    queue.summary(db)
    queue.get_item(db, target)
    db.set_trace_callback(None)
    assert seen and not any("FROM queue_body" in s or "JOIN queue_body" in s for s in seen)


# --------------------------------------------------------------------------------- summary


def test_summary(db):
    fill(db)
    queue.set_enabled(db, True)
    s = queue.summary(db)
    assert s["enabled"] is True
    assert s["totals"] == {"items": 6, "pending": 3, "sent": 2, "other": 1}
    first, second = s["dbs"]
    assert (first["name"], first["member"], first["collection"], first["cache"]) == (
        "a.db3",
        "d/a.db3",
        1,
        "CacheOne",
    )
    assert (first["items"], first["pending"], first["sent"], first["other"]) == (5, 3, 1, 1)
    assert (second["name"], second["collection"], second["sent"]) == ("b.db3", 2, 1)
    assert {"snapshot", "status", "error", "read_at", "adapter", "bodies_missing"} <= set(first)


def test_summary_groups_by_ers_group_and_names_only_the_file(db):
    add_collection(db, 1, 1)
    add_collection(db, 2, 1)  # the second half of capture 1
    queue.replace_bank(db, 2, "Win\\Dir\\x.db3", bank([item(1)]))
    d = queue.summary(db)["dbs"][0]
    assert (d["collection"], d["collection_id"], d["name"]) == (1, 2, "x.db3")
    assert queue.list_items(db, collections=[1]).items[0]["collection"] == 1


# --------------------------------------------------------------------------------- deletion


def test_delete_queue_by_group_and_whole_case(db):
    fill(db)
    queue.set_enabled(db, True)
    db.commit()
    assert queue.delete_queue(db, 2) == (1, 1)
    assert count(db, "queue_db") == 1 and count(db, "queue_item") == 5 and count(db, "queue_body") == 5
    assert queue.delete_queue(db, 99) == (0, 0)
    assert queue.delete_queue(db) == (1, 5)
    assert (count(db, "queue_db"), count(db, "queue_item"), count(db, "queue_body")) == (0, 0, 0)
    assert queue.is_enabled(db)  # the option is not changed
    assert not db.in_transaction


def test_remove_group_clears_the_queue_of_that_collection_only(db):
    fill(db)
    removed = IndexWriter(db).remove_group(2)
    assert removed is not None and removed.collections == 1
    assert [r[0] for r in db.execute("SELECT id FROM queue_db")] == [1]
    assert count(db, "queue_item") == 5 and count(db, "queue_body") == 5
    assert (
        db.execute("SELECT count(*) FROM queue_item WHERE db_id NOT IN (SELECT id FROM queue_db)").fetchone()[
            0
        ]
        == 0
    )
    assert IndexWriter(db).remove_group(1) is not None
    assert (count(db, "queue_db"), count(db, "queue_item"), count(db, "queue_body")) == (0, 0, 0)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


# ---------------------------------------------------------------------------------- log links


def add_log(db, n=3):
    db.execute(
        "INSERT INTO source(id, collection_id, member, file_name, envelope) VALUES (1, 1, 'a', 'a', 'adms')"
    )
    for eid in range(1, n + 1):
        db.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, sev, tpl_id, msg)"
            " VALUES (?, 1, ?, 0, 1, 1, 0, 'm')",
            (eid, eid),
        )


def test_log_links(db):
    fill(db)
    add_log(db)
    db.executemany(
        "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?, ?, ?)",
        [
            ("msgid", G1, 1),
            ("msgid", G1.upper(), 2),
            ("msgid", "other", 3),
            ("im_id", "2", 3),
            ("im_id", "2", 1),
        ],
    )
    for pid, mid, direction in (
        (1, G1, "out"),
        (2, G1.upper(), "out"),
        (3, "{" + G1 + "}", "in"),
        (4, G2, "in"),
    ):
        db.execute(
            "INSERT INTO payload(id, entry_id, role, direction, msg_id, fmt, char_off, char_len)"
            " VALUES (?, 1, 'request', ?, ?, 'xml', 0, 1)",
            (pid, direction, mid),
        )
    db.commit()
    items = {i["item_id"]: i for i in queue.list_items(db, dbs=[1]).items}
    links = queue.log_links(db, items.values())
    assert links[items[1]["id"]] == {"msgid": 2, "im_id": 0, "payload_id": 2}  # latest outgoing, any case
    assert links[items[2]["id"]] == {"msgid": 0, "im_id": 2, "payload_id": None}  # only an incoming match
    assert links[items[3]["id"]] == {"msgid": 0, "im_id": 0, "payload_id": None}  # no message id
    assert queue.log_links(db, []) == {}


def test_log_links_are_capped(db, monkeypatch):
    fill(db)
    add_log(db)
    monkeypatch.setattr(queue, "LINK_COUNT_CAP", 2)
    for eid in (1, 2, 3):
        db.execute("INSERT INTO entry_key(key_type, value, entry_id) VALUES ('msgid', ?, ?)", (G1, eid))
    one = queue.list_items(db, item_id=1, dbs=[1]).items
    assert queue.log_links(db, one)[one[0]["id"]]["msgid"] == 2


def test_migrate_is_a_no_op_for_a_current_case(db):
    sql = "SELECT sql FROM sqlite_master WHERE name LIKE 'queue%' ORDER BY name"
    before = db.execute(sql).fetchall()
    assert migrate(db) == 8
    assert db.execute(sql).fetchall() == before
