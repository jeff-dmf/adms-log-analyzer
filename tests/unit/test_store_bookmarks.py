"""Bookmarks store: CRUD, lazy locator resolution, reassociation, export/import (synthetic data)."""

from __future__ import annotations

import json

import pytest
import test_store_keys as tsk

from dmslog.store import bookmarks as bm
from dmslog.store import create_case_db


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "c" / "case.sqlite")
    tsk.seed(c)
    yield c
    c.close()


def code_of(exc_info):
    return exc_info.value.code


def test_create_defaults_and_fields(conn):
    made = bm.create_bookmark(conn, 3, None, "  look\x00 here \r\n now ")
    assert made["entry_id"] == 3 and made["orphan"] is False
    assert made["color"] == bm.DEFAULT_COLOR and made["note"] == "look here \n now"
    assert (made["member"], made["line_no"]) == ("Log/a.log", 3)
    assert made["msg"].startswith("msg 3 ") and len(made["msg"]) == bm.MSG_CHARS
    assert made["source_id"] == 1 and made["created_at"].endswith("Z")


def test_validation(conn):
    with pytest.raises(bm.BookmarkError) as e:
        bm.create_bookmark(conn, 1, "magenta")
    assert code_of(e) == "bad_color"
    with pytest.raises(bm.BookmarkError) as e:
        bm.create_bookmark(conn, 1, "red", "x" * (bm.MAX_NOTE_CHARS + 1))
    assert code_of(e) == "bad_note"
    with pytest.raises(bm.BookmarkError) as e:
        bm.create_bookmark(conn, 9999)
    assert code_of(e) == "entry_not_found"
    assert conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 0


def test_duplicate_reports_existing_id(conn):
    first = bm.create_bookmark(conn, 2)
    with pytest.raises(bm.BookmarkError) as e:
        bm.create_bookmark(conn, 2, "red")
    assert code_of(e) == "already_bookmarked" and e.value.params["bookmark_id"] == first["id"]


def test_limit(conn, monkeypatch):
    monkeypatch.setattr(bm, "MAX_BOOKMARKS", 2)
    bm.create_bookmark(conn, 1)
    bm.create_bookmark(conn, 2)
    with pytest.raises(bm.BookmarkError) as e:
        bm.create_bookmark(conn, 3)
    assert code_of(e) == "too_many"


def test_update_and_delete(conn):
    made = bm.create_bookmark(conn, 1, "red", "a")
    changed = bm.update_bookmark(conn, made["id"], note=None)
    assert changed["note"] is None and changed["color"] == "red"
    changed = bm.update_bookmark(conn, made["id"], color="blue")
    assert changed["color"] == "blue"
    with pytest.raises(bm.BookmarkError) as e:
        bm.update_bookmark(conn, made["id"])
    assert code_of(e) == "no_changes"
    with pytest.raises(bm.BookmarkError) as e:
        bm.update_bookmark(conn, 999, color="red")
    assert code_of(e) == "not_found"
    bm.delete_bookmark(conn, made["id"])
    with pytest.raises(bm.BookmarkError):
        bm.delete_bookmark(conn, made["id"])
    with pytest.raises(bm.BookmarkError):
        bm.get_bookmark(conn, made["id"])


def test_hostile_text_is_stored_as_data(conn):
    evil = "x'); DROP TABLE bookmark;-- <script>alert(1)</script>"
    made = bm.create_bookmark(conn, 1, "red", evil)
    assert made["note"] == evil
    items, _ = bm.list_bookmarks(conn, text="drop table")
    assert [i["id"] for i in items] == [made["id"]]
    assert bm.list_bookmarks(conn, text="%")[0] == []  # no LIKE wildcard semantics


def test_list_keyset_color_and_text(conn):
    ids = [bm.create_bookmark(conn, n, "red" if n % 2 else "green", f"note {n}")["id"] for n in range(1, 8)]
    page1, nxt = bm.list_bookmarks(conn, limit=3)
    assert [i["id"] for i in page1] == ids[:3] and nxt == ids[2]
    page2, nxt = bm.list_bookmarks(conn, limit=3, after=nxt)
    page3, nxt3 = bm.list_bookmarks(conn, limit=3, after=nxt)
    assert [i["id"] for i in page2 + page3] == ids[3:] and nxt3 is None
    assert {i["color"] for i in bm.list_bookmarks(conn, color="green")[0]} == {"green"}
    assert [i["note"] for i in bm.list_bookmarks(conn, text="NOTE 5")[0]] == ["note 5"]
    assert len(bm.list_bookmarks(conn, text="msg 4")[0]) == 1  # matches the record message
    with pytest.raises(bm.BookmarkError):
        bm.list_bookmarks(conn, color="nope")


def reindex(conn, shift=0, drop=()):
    """Rebuild the entry rows of source 1 with new ids (ids +100) and optionally moved/dropped lines."""
    rows = conn.execute("SELECT * FROM entry WHERE source_id = 1").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM entry LIMIT 0").description]
    conn.execute("DELETE FROM entry WHERE source_id = 1")
    for row in rows:
        rec = dict(zip(cols, row, strict=True))
        if rec["line_no"] in drop:
            continue
        rec["id"] += 100
        rec["line_no"] += shift
        conn.execute(
            f"INSERT INTO entry({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",  # noqa: S608
            [rec[c] for c in cols],
        )
    conn.commit()


def test_lazy_resolution_and_reassociate_after_reindex(conn):
    keep = bm.create_bookmark(conn, 2, "red", "kept")
    gone = bm.create_bookmark(conn, 4, "blue", "dropped")
    other = bm.create_bookmark(conn, 7, "green")  # another source, untouched
    reindex(conn, drop={4})
    # reads follow the locator even though the cached entry ids are stale
    items = {i["id"]: i for i in bm.list_bookmarks(conn)[0]}
    assert items[keep["id"]]["entry_id"] == 102 and not items[keep["id"]]["orphan"]
    assert items[gone["id"]]["entry_id"] is None and items[gone["id"]]["orphan"]
    assert items[gone["id"]]["msg"] is None and items[other["id"]]["entry_id"] == 7
    assert bm.for_entries(conn, [102, 103, 7]) == {
        102: {"id": keep["id"], "color": "red", "has_note": True},
        7: {"id": other["id"], "color": "green", "has_note": False},
    }
    assert conn.execute("SELECT entry_id FROM bookmark WHERE id = ?", (keep["id"],)).fetchone()[0] == 2
    result = bm.reassociate(conn)
    assert result == {"bookmarks": 3, "relinked": 2, "orphaned": 1}
    assert conn.execute("SELECT entry_id FROM bookmark WHERE id = ?", (keep["id"],)).fetchone()[0] == 102
    assert conn.execute("SELECT entry_id FROM bookmark WHERE id = ?", (gone["id"],)).fetchone()[0] is None
    assert bm.reassociate(conn) == {"bookmarks": 3, "relinked": 0, "orphaned": 1}  # idempotent


def test_orphan_comes_back(conn):
    made = bm.create_bookmark(conn, 4)
    reindex(conn, drop={4})
    bm.reassociate(conn)
    assert bm.get_bookmark(conn, made["id"])["orphan"] is True
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, level, sev, tpl_id, msg)"
        " VALUES (500, 1, 4, 0, 1, 1, 3, 4, 0, 'back')"
    )
    conn.commit()
    assert bm.reassociate(conn)["orphaned"] == 0
    assert bm.get_bookmark(conn, made["id"])["entry_id"] == 500


def test_for_entries_validation_and_empty(conn):
    assert bm.for_entries(conn, [1, 2, 3]) == {}
    for bad in ([], "1", [True], [-1], [1.5], list(range(bm.MAX_LOOKUP_IDS + 1))):
        with pytest.raises(bm.BookmarkError):
            bm.for_entries(conn, bad)


def test_export_import_roundtrip(conn, tmp_path):
    bm.create_bookmark(conn, 2, "red", "one")
    bm.create_bookmark(conn, 7, "blue")
    data = bm.export_bookmarks(conn)
    assert data["format"] == bm.EXPORT_FORMAT and data["version"] == 1 and len(data["bookmarks"]) == 2
    other = create_case_db(tmp_path / "o" / "case.sqlite")
    tsk.seed(other)
    counts = bm.import_bookmarks(other, data)
    assert counts == {"imported": 2, "existing": 0, "unmatched": 0, "invalid": 0}
    again = bm.import_bookmarks(other, data)
    assert again == {"imported": 0, "existing": 2, "unmatched": 0, "invalid": 0}
    items = bm.list_bookmarks(other)[0]
    assert [(i["entry_id"], i["color"], i["note"]) for i in items] == [(2, "red", "one"), (7, "blue", None)]
    other.close()


def test_import_rejects_and_counts(conn):
    for bad in (None, [], {"format": "x"}, {"format": bm.EXPORT_FORMAT, "version": 2, "bookmarks": []},
                {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": "no"}):  # fmt: skip
        with pytest.raises(bm.BookmarkError) as e:
            bm.import_bookmarks(conn, bad)
        assert code_of(e) == "bad_import"
    items = [
        {"member": "Log/a.log", "line_no": 1, "color": "red"},  # ok (single collection, no identity)
        {"member": "Log/missing.log", "line_no": 1},  # unmatched source
        {"member": "Log/a.log", "line_no": 0},  # invalid
        {"member": "Log/a.log", "line_no": 2, "color": "pink"},  # invalid color
        "text",
        {"ers_guid": "NOPE", "member": "Log/a.log", "line_no": 3},  # unknown collection
    ]
    counts = bm.import_bookmarks(conn, {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": items})
    assert counts == {"imported": 1, "existing": 0, "unmatched": 2, "invalid": 3}


def test_import_is_atomic_on_limit(conn, monkeypatch):
    bm.create_bookmark(conn, 5)
    monkeypatch.setattr(bm, "MAX_BOOKMARKS", 2)
    items = [{"member": "Log/a.log", "line_no": n} for n in (1, 2)]
    with pytest.raises(bm.BookmarkError) as e:
        bm.import_bookmarks(conn, {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": items})
    assert code_of(e) == "too_many"
    assert conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 1


def test_import_validates_identity_and_ranges(conn):
    base = {"member": "Log/a.log", "line_no": 1}
    items = [
        {**base, "ers_ts": []},
        {**base, "ers_guid": []},
        {**base, "ers_guid": {}},
        {**base, "ers_guid": ""},
        {**base, "ers_guid": "x" * 129},
        {**base, "ers_guid": "a\x00b"},
        {**base, "ers_guid": 5},
        {**base, "line_no": 2**63},
        {**base, "line_no": 2**200},
        {**base, "ers_ts": 5},
    ]
    counts = bm.import_bookmarks(conn, {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": items})
    assert counts == {"imported": 0, "existing": 0, "unmatched": 0, "invalid": len(items)}
    assert conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 0
    ok = {**base, "line_no": 2**63 - 1, "ers_guid": None, "ers_ts": None}
    assert (
        bm.import_bookmarks(conn, {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": [ok]})["imported"]
        == 1
    )


def test_integer_range_is_checked_before_queries(conn):
    for bad in (2**63, 2**64, -1):
        with pytest.raises(bm.BookmarkError):
            bm.for_entries(conn, [bad])
    with pytest.raises(bm.BookmarkError):
        bm.create_bookmark(conn, 2**63)
    with pytest.raises(bm.BookmarkError):
        bm.update_bookmark(conn, 2**63, color="red")
    with pytest.raises(bm.BookmarkError):
        bm.list_bookmarks(conn, after=2**63)


def test_text_filter_searches_the_whole_message(conn):
    target = "TARGETWORD"
    conn.execute("UPDATE entry SET msg = ? WHERE id = 4", ("p" * 2000 + target,))
    conn.commit()
    made = bm.create_bookmark(conn, 4)
    items, _ = bm.list_bookmarks(conn, text="targetword")
    assert [i["id"] for i in items] == [made["id"]]
    assert target not in items[0]["msg"] and len(items[0]["msg"]) == bm.MSG_CHARS  # presentation stays cut


def test_export_respects_the_byte_budget_and_round_trips(conn, monkeypatch, tmp_path):
    for n in range(1, 11):
        bm.create_bookmark(conn, n, "red", "\u00e9" * bm.MAX_NOTE_CHARS)
    full = bm.export_bookmarks(conn)
    assert full["truncated"] is False and len(full["bookmarks"]) == 10
    other = create_case_db(tmp_path / "o" / "case.sqlite")
    tsk.seed(other)
    assert bm.import_bookmarks(other, full)["imported"] == 10
    monkeypatch.setattr(bm, "MAX_EXPORT_BYTES", 9000)
    cut = bm.export_bookmarks(conn)
    assert cut["truncated"] is True and 0 < len(cut["bookmarks"]) < 10
    assert len(json.dumps(cut, ensure_ascii=False, separators=(",", ":")).encode()) <= 9000 + 100
    other.close()


def test_lone_surrogates_are_invalid_everywhere(conn):
    sur = "\ud800"
    good = {"member": "Log/a.log", "line_no": 1, "note": "kept"}
    items = [
        good,
        {**good, "line_no": 2, "ers_guid": sur},
        {**good, "line_no": 3, "ers_ts": "a" + sur},
        {**good, "line_no": 4, "member": "Log/a" + sur},
        {**good, "line_no": 5, "note": "n" + sur},
        {**good, "line_no": 6, "created_at": "2026-01-01T00:00:0" + sur + "Z"},
    ]
    counts = bm.import_bookmarks(conn, {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": items})
    assert counts["imported"] == 2 and counts["invalid"] == 4  # the valid item before them survives
    rows = conn.execute("SELECT line_no FROM bookmark ORDER BY line_no").fetchall()
    assert [r[0] for r in rows][0] == 1 and len(rows) == 2
    with pytest.raises(bm.BookmarkError):
        bm.create_bookmark(conn, 3, "red", "x" + sur)
    with pytest.raises(bm.BookmarkError):
        bm.list_bookmarks(conn, text=sur)


def test_text_search_has_a_deadline_and_bounded_batches(conn, monkeypatch):
    from dmslog.store.query import QueryTimeout

    big = "x" * (bm.MAX_SEARCH_MSG_CHARS - 4)
    for entry_id in range(1, 11):
        conn.execute("UPDATE entry SET msg = ? WHERE id = ?", (big + "TAIL", entry_id))
        bm.create_bookmark(conn, entry_id)
    conn.commit()
    with pytest.raises(QueryTimeout):
        bm.list_bookmarks(conn, text="tail", timeout_s=0)
    items, _ = bm.list_bookmarks(conn, text="tail")  # same semantics with the normal budget
    assert len(items) == 10
    # groups of messages never exceed the character budget (a single oversized message goes alone)
    monkeypatch.setattr(bm, "MAX_SEARCH_BATCH_CHARS", 100_000)
    groups = []
    original = conn.execute

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, params=()):
            if sql.startswith("SELECT id, substr(msg"):
                groups.append(len(params))
            return self.inner.execute(sql, params)

    found = bm._matching_messages(Spy(conn), list(range(1, 11)), "tail", None)
    assert found == set(range(1, 11)) and max(groups) == 1 and len(groups) == 10
    assert original is not None


def test_search_horizon_bounds_memory_and_time(conn):
    import time
    import tracemalloc

    from dmslog.store.query import QueryTimeout

    horizon = bm.MAX_SEARCH_MSG_CHARS
    inside = "a" * (horizon - 10) + "INSIDE"
    beyond = "b" * horizon + "BEYOND"
    huge = "c" * 8_000_000 + "FARAWAY"
    for entry_id, msg in ((2, inside), (3, beyond), (4, huge)):
        conn.execute("UPDATE entry SET msg = ? WHERE id = ?", (msg, entry_id))
        bm.create_bookmark(conn, entry_id)
    conn.commit()
    assert [i["entry_id"] for i in bm.list_bookmarks(conn, text="inside")[0]] == [2]
    assert bm.list_bookmarks(conn, text="beyond")[0] == []  # past the search horizon
    assert bm.list_bookmarks(conn, text="faraway")[0] == []
    tracemalloc.start()
    start = time.perf_counter()
    try:
        bm.list_bookmarks(conn, text="zzz", timeout_s=0.01)
    except QueryTimeout:
        pass
    elapsed = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 10_000_000, peak  # the 8 M-character message is never held whole in Python
    assert elapsed < 1.0, elapsed
