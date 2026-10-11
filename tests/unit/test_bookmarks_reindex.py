"""Bookmarks across the real ingestion flow: reindex, truncation, growth and rotation (synthetic logs).

The writer keeps ``bookmark.entry_id`` right in the same transaction that changes the records, so these
tests check the stored cache, not only what a read resolves.
"""

from __future__ import annotations

import re

import pytest
import test_pipeline as tp

from dmslog.ingest.pipeline import PAYLOAD_VERSION_KEY, IngestOptions, run_ingest
from dmslog.store import bookmarks as bm
from dmslog.store.writer import IndexWriter

WFM = tp.WFM
WFM_ROTATED = tp.WFM_ROTATED
WFM_OLDER = f"{tp.LOGDIR}/WfmAdapter_1111.log.1"


@pytest.fixture
def db(tmp_path):
    conn = tp.fresh_db(tmp_path, "bm")
    yield conn
    conn.close()


def entry_at(conn, member: str, line_no: int) -> tuple[int, str]:
    row = conn.execute(
        "SELECT e.id, e.msg FROM entry e JOIN source s ON s.id = e.source_id"
        " WHERE s.member = ? AND e.line_no = ?",
        (member, line_no),
    ).fetchone()
    assert row is not None, (member, line_no)
    return row[0], row[1]


def record_lines(conn, member: str) -> list[int]:
    """Line numbers where the records of a file start (continuation lines are not records)."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
            " WHERE s.member = ? ORDER BY e.id",
            (member,),
        )
    ]


def mark(conn, member: str, line_no: int, note: str) -> int:
    entry_id, _ = entry_at(conn, member, line_no)
    return bm.create_bookmark(conn, entry_id, "red", note)["id"]


def view(conn) -> dict[str, dict]:
    """Every bookmark by note: what a read returns plus the stored cache."""
    items, _ = bm.list_bookmarks(conn, limit=bm.MAX_LIMIT)
    cache = dict(conn.execute("SELECT id, entry_id FROM bookmark"))
    out = {}
    for item in items:
        item["cache"] = cache[item["id"]]
        out[item["note"]] = item
    return out


def assert_linked(item: dict, member: str, msg: str) -> None:
    assert not item["orphan"] and item["member"] == member
    assert item["msg"] == msg
    assert item["cache"] == item["entry_id"]  # the writer refreshed the cache; no reassociate needed


def assert_orphan(item: dict) -> None:
    assert item["orphan"] and item["entry_id"] is None and item["msg"] is None and item["cache"] is None


def ingest(conn, root):
    return run_ingest(conn, root)


def actions(result) -> dict[str, str]:
    return {d.member: d.action for d in result.details if d.action != "unchanged"}


# --------------------------------------------------------------------------- reindex of the same content


def test_full_reindex_keeps_every_bookmark_on_the_same_record(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(120)})
    ingest(db, root)
    starts = record_lines(db, WFM)
    picks = {f"n{line}": line for line in (starts[0], starts[1], starts[40], starts[80], starts[-1])}
    msgs = {}
    for note, line in picks.items():
        mark(db, WFM, line, note)
        msgs[note] = entry_at(db, WFM, line)[1]
    ids_before = {note: entry_at(db, WFM, line)[0] for note, line in picks.items()}
    sid = db.execute("SELECT id FROM source WHERE member = ?", (WFM,)).fetchone()[0]
    db.execute("DELETE FROM meta WHERE key = ?", (f"{PAYLOAD_VERSION_KEY}.{sid}",))  # forces a reindex
    db.commit()
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    state = view(db)
    for note, line in picks.items():
        assert_linked(state[note], WFM, msgs[note])
        assert state[note]["entry_id"] == entry_at(db, WFM, line)[0] != ids_before[note]
    assert bm.reassociate(db)["relinked"] == 0  # nothing left for a full pass to fix


def test_small_batches_relink_bookmarks_spread_over_many_commits(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(400)})
    ingest(db, root)
    starts = record_lines(db, WFM)
    lines = (starts[0], starts[120], starts[260], starts[-1])
    for line in lines:
        mark(db, WFM, line, f"n{line}")
    sid = db.execute("SELECT id FROM source").fetchone()[0]
    db.execute("DELETE FROM meta WHERE key = ?", (f"{PAYLOAD_VERSION_KEY}.{sid}",))
    db.commit()
    run_ingest(db, root, IngestOptions(batch_bytes=2048))  # one commit every ~2 KiB
    state = view(db)
    for line in lines:
        assert_linked(state[f"n{line}"], WFM, entry_at(db, WFM, line)[1])


# --------------------------------------------------------------------------- truncation and growth


def test_truncated_file_orphans_its_bookmarks_and_they_come_back_with_the_content(tmp_path, db):
    original = tp.make_log(150)
    root = tp.write_folder(tmp_path / "Log", {WFM: original})
    ingest(db, root)
    early, late = record_lines(db, WFM)[3], record_lines(db, WFM)[-1]
    mark(db, WFM, early, "early")  # the new content also has a record at this line
    mark(db, WFM, late, "late")  # beyond the end of the new content
    early_msg = entry_at(db, WFM, early)[1]
    (root / WFM).write_bytes(tp.make_log(40, start=500_000))  # rewritten and shorter
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    state = view(db)
    assert_orphan(state["early"])  # line 3 now holds another record: not the bookmarked one
    assert_orphan(state["late"])
    new_id, new_msg = entry_at(db, WFM, early)
    assert new_msg != early_msg
    assert bm.for_entries(db, [new_id]) == {}  # the grid does not mark the new record
    again = bm.create_bookmark(db, new_id, "blue", "new record")  # the line is free to bookmark again
    assert again["entry_id"] == new_id
    # The file grows again (append): the orphans stay orphans, the new bookmark stays put.
    (root / WFM).write_bytes(tp.make_log(40, start=500_000) + tp.make_log(200, start=600_000))
    assert actions(ingest(db, root)) == {WFM: "append"}
    state = view(db)
    assert_orphan(state["early"])
    assert_orphan(state["late"])
    assert_linked(state["new record"], WFM, new_msg)
    # The original content comes back: the orphans find their records again; the other one is orphaned.
    (root / WFM).write_bytes(original)
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    state = view(db)
    assert_linked(state["early"], WFM, early_msg)
    assert_linked(state["late"], WFM, entry_at(db, WFM, late)[1])
    assert_orphan(state["new record"])
    assert bm.for_entries(db, [state["early"]["entry_id"]]) == {
        state["early"]["entry_id"]: {"id": state["early"]["id"], "color": "red", "has_note": True}
    }


def test_growth_relinks_the_reparsed_last_record_even_when_it_was_partial(tmp_path, db):
    head = tp.make_log(30)
    partial = tp.mf.header(900_000, "INFO", "[Alpha] last one is still being wr").encode()
    root = tp.write_folder(tmp_path / "Log", {WFM: head + partial})  # no final newline
    ingest(db, root)
    last_line = db.execute("SELECT max(line_no) FROM entry").fetchone()[0]
    assert db.execute("SELECT flags & 1 FROM entry WHERE line_no = ?", (last_line,)).fetchone()[0] == 1
    mark(db, WFM, last_line, "partial")
    middle = record_lines(db, WFM)[5]
    mark(db, WFM, middle, "middle")
    (root / WFM).write_bytes(head + partial + b"itten\n  with a continuation\n" + tp.make_log(5, start=1_000))
    assert actions(ingest(db, root)) == {WFM: "append"}
    state = view(db)
    assert_linked(state["partial"], WFM, entry_at(db, WFM, last_line)[1])
    assert state["partial"]["msg"].endswith("still being written")
    assert_linked(state["middle"], WFM, entry_at(db, WFM, middle)[1])


# --------------------------------------------------------------------------- rotation


def test_rotation_moves_bookmarks_with_their_file(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(60)})
    ingest(db, root)
    seventh = record_lines(db, WFM)[7]
    mark(db, WFM, seventh, "first")
    first_id, first_msg = entry_at(db, WFM, seventh)
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(60, start=50_000))  # the new .log also has a line 7
    assert actions(ingest(db, root)) == {WFM: "new"}
    state = view(db)
    assert_linked(state["first"], WFM_ROTATED, first_msg)
    assert state["first"]["entry_id"] == first_id  # the moved source kept its records
    assert seventh in record_lines(db, WFM)
    mark(db, WFM, seventh, "second")
    second_msg = entry_at(db, WFM, seventh)[1]
    # A second rotation shifts the chain: .log.0 -> .log.1 and .log -> .log.0 in the same run.
    (root / WFM_ROTATED).rename(root / WFM_OLDER)
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(20, start=90_000))
    assert actions(ingest(db, root)) == {WFM: "new"}
    state = view(db)
    assert_linked(state["first"], WFM_OLDER, first_msg)
    assert_linked(state["second"], WFM_ROTATED, second_msg)
    marks = bm.for_entries(db, [entry_at(db, WFM, seventh)[0]])
    assert marks == {}  # the newest .log is not bookmarked


def test_a_dropped_stale_source_orphans_its_bookmarks_instead_of_lending_them(tmp_path, db):
    root = tp.write_folder(
        tmp_path / "Log", {WFM: tp.make_log(40), WFM_ROTATED: tp.make_log(40, start=20_000)}
    )
    ingest(db, root)
    fourth = record_lines(db, WFM)[4]
    assert record_lines(db, WFM_ROTATED)[4] == fourth
    mark(db, WFM_ROTATED, fourth, "old rotated")
    mark(db, WFM, fourth, "current")
    current_msg = entry_at(db, WFM, fourth)[1]
    # .log.0 disappears (not shifted to .log.1) and .log rotates into its name.
    (root / WFM_ROTATED).unlink()
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(10, start=70_000))
    ingest(db, root)
    state = view(db)
    assert_linked(state["current"], WFM_ROTATED, current_msg)
    assert_orphan(state["old rotated"])  # same locator as "current" now, but its record is gone
    assert state["old rotated"]["member"] == WFM_ROTATED
    entry_id = state["current"]["entry_id"]
    assert bm.for_entries(db, [entry_id]) == {entry_id: {"id": state["current"]["id"], "color": "red",
                                                         "has_note": True}}  # fmt: skip


# --------------------------------------------------------------------------- writer-level guarantees


def test_rollback_restores_the_bookmark_cache(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    ingest(db, root)
    made = mark(db, WFM, record_lines(db, WFM)[2], "kept")
    before = db.execute("SELECT entry_id, fp FROM bookmark").fetchone()
    sid = db.execute("SELECT id FROM source").fetchone()[0]
    writer = IndexWriter(db)
    writer.delete_source_entries(sid)
    assert db.execute("SELECT entry_id FROM bookmark").fetchone()[0] is None  # orphaned inside the batch
    writer.rollback()
    assert db.execute("SELECT entry_id, fp FROM bookmark").fetchone() == before
    assert bm.get_bookmark(db, made)["orphan"] is False


def test_an_empty_bookmark_table_costs_one_probe_per_batch(tmp_path, db):
    data = tp.make_log(300)
    root = tp.write_folder(tmp_path / "Log", {WFM: data})
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    try:
        run_ingest(db, root, IngestOptions(batch_bytes=4096))
    finally:
        db.set_trace_callback(None)
    touching = [s for s in statements if re.search(r"\b(from|into|update)\s+bookmark\b", s, re.IGNORECASE)]
    commits = sum(1 for s in statements if s.strip().upper() == "COMMIT")
    assert touching and all(s.strip().upper().startswith("SELECT 1 FROM BOOKMARK") for s in touching)
    assert len(touching) <= commits  # bounded by batches, never by records
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] > 4 * len(touching)


def test_many_bookmarks_survive_a_reindex_and_full_reassociate_is_idempotent(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(200)})
    ingest(db, root)
    lines = record_lines(db, WFM)[::3]
    for line in lines:
        mark(db, WFM, line, f"n{line}")
    (root / WFM).write_bytes(tp.make_log(200)[:-400])  # shorter: a reindex of mostly the same content
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    state = view(db)
    alive = {r[0] for r in db.execute("SELECT line_no FROM entry")}
    last_alive = max(alive)
    gone = {line for line in lines if line not in alive}
    assert gone  # the cut removed bookmarked records
    orphans = {line for line in lines if state[f"n{line}"]["orphan"]}
    assert gone <= orphans <= gone | {last_alive}  # the cut last record may have changed too
    for line in lines:
        if line in orphans:
            assert_orphan(state[f"n{line}"])
        else:
            assert_linked(state[f"n{line}"], WFM, entry_at(db, WFM, line)[1])
    first = bm.reassociate(db)
    assert first["relinked"] == 0 and first["orphaned"] == len(orphans)
    assert bm.reassociate(db) == first
