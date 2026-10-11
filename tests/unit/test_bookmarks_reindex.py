"""Bookmarks across the real ingestion flow: reindex, truncation, growth and rotation (synthetic logs).

The writer keeps ``bookmark.entry_id`` right in the same transaction that changes the records, so these
tests check the stored cache, not only what a read resolves.
"""

from __future__ import annotations

import re
import time

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


# --------------------------------------------------------------------------- record identity (fingerprint)


def same_start_log(tail: str, cont: str) -> bytes:
    """Ten records; the 5th starts with the same 80 characters in every variant and differs after them."""
    lines = []
    for i in range(10):
        if i == 4:
            lines.append(tp.mf.header(5_000, "INFO", "[Alpha] " + "s" * 80 + tail))
            lines.append(cont)
        else:
            lines.append(tp.mf.header(i * 1_000, "INFO", f"[Alpha] item {i} done"))
    return tp.mf.render(lines)


@pytest.mark.parametrize(
    ("tail", "cont"),
    [(" tail two", "  same continuation"), (" tail one", "  diff continuation")],
    ids=["message-after-the-prefix", "continuation"],
)
def test_two_complete_records_with_the_same_start_are_told_apart(tmp_path, db, tail, cont):
    original = same_start_log(" tail one", "  same continuation")
    variant = same_start_log(tail, cont)
    assert len(original) == len(variant)  # same offsets and timestamps: only the content differs
    root = tp.write_folder(tmp_path / "Log", {WFM: original})
    ingest(db, root)
    line = record_lines(db, WFM)[4]
    mark(db, WFM, line, "fifth")
    first_msg = entry_at(db, WFM, line)[1]
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0].startswith("f:")
    (root / WFM).write_bytes(variant)
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    state = view(db)
    assert_orphan(state["fifth"])  # same line, offset, time and start: still another record
    assert bm.for_entries(db, [entry_at(db, WFM, line)[0]]) == {}
    (root / WFM).write_bytes(original)
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    assert_linked(view(db)["fifth"], WFM, first_msg)


def test_open_last_record_relinks_when_completed_and_is_closed_by_the_next_record(tmp_path, db):
    head = tp.make_log(30)
    partial = tp.mf.header(900_000, "INFO", "[Alpha] last one is still being wr").encode()
    root = tp.write_folder(tmp_path / "Log", {WFM: head + partial})  # no final newline
    ingest(db, root)
    last_line = record_lines(db, WFM)[-1]
    mark(db, WFM, last_line, "open")
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0].startswith("p")
    # The record grows (first line completed, then a continuation): still the bookmarked record.
    grown = head + partial + b"itten\n  with a continuation"
    (root / WFM).write_bytes(grown)
    assert actions(ingest(db, root)) == {WFM: "append"}
    assert_linked(view(db)["open"], WFM, entry_at(db, WFM, last_line)[1])
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0].startswith("p")  # still the last record
    # A next record closes it: the fingerprint now covers the whole, final content.
    (root / WFM).write_bytes(grown + b"\n" + tp.make_log(3, start=2_000))
    assert actions(ingest(db, root)) == {WFM: "append"}
    state = view(db)
    assert_linked(state["open"], WFM, entry_at(db, WFM, last_line)[1])
    assert state["open"]["msg"].endswith("still being written")
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0].startswith("f:")
    # A rewrite that completes the same start differently is another record.
    rewritten = head + partial + b"ong guess\n  with a continuation\n" + tp.make_log(3, start=2_000)
    (root / WFM).write_bytes(rewritten)
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    assert_orphan(view(db)["open"])


# --------------------------------------------------------------------------- lineage (rotation)


def twin_log(first: str) -> bytes:
    """A first record that tells the files apart (same size), then records identical across files."""
    return tp.mf.render([tp.mf.header(0, "INFO", f"[Alpha] {first}")]) + tp.make_log(40, start=1_000)


def rotate_onto_a_twin(tmp_path, db):
    """``.log.0`` and ``.log`` share their records after the first one; both are bookmarked at one line.

    Then ``.log.0`` disappears and ``.log`` rotates into its name: the bookmark of the dropped file and
    the one of the moved file end up with the same locator and the same record identity.
    """
    root = tp.write_folder(
        tmp_path / "Log", {WFM_ROTATED: twin_log("origin of file A"), WFM: twin_log("origin of file B")}
    )
    ingest(db, root)
    line = record_lines(db, WFM)[6]
    assert entry_at(db, WFM, line)[1] == entry_at(db, WFM_ROTATED, line)[1]
    mark(db, WFM_ROTATED, line, "old rotated")
    mark(db, WFM, line, "current")
    (root / WFM_ROTATED).unlink()
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(10, start=70_000))
    ingest(db, root)
    return root, line


def force_reindex(db, member: str) -> None:
    sid = db.execute("SELECT id FROM source WHERE member = ?", (member,)).fetchone()[0]
    db.execute("DELETE FROM meta WHERE key = ?", (f"{PAYLOAD_VERSION_KEY}.{sid}",))
    db.commit()


def test_an_orphan_never_takes_over_a_linked_bookmark_after_a_rotation(tmp_path, db):
    root, line = rotate_onto_a_twin(tmp_path, db)
    fps = dict(db.execute("SELECT note, fp FROM bookmark"))
    assert fps["old rotated"] == fps["current"]  # same identity: only the lineage tells them apart
    state = view(db)
    current_msg = state["current"]["msg"]
    assert_linked(state["current"], WFM_ROTATED, current_msg)
    assert_orphan(state["old rotated"])
    lineage = dict(db.execute("SELECT note, source_id FROM bookmark"))
    assert lineage["old rotated"] is None  # its source was dropped: detached
    # The tail of the moved file is parsed again: the linked bookmark keeps its record.
    force_reindex(db, WFM_ROTATED)
    assert actions(ingest(db, root)) == {WFM_ROTATED: "reindex"}
    state = view(db)
    assert_linked(state["current"], WFM_ROTATED, current_msg)
    assert_orphan(state["old rotated"])
    entry_id = state["current"]["entry_id"]
    assert bm.for_entries(db, [entry_id]) == {entry_id: {"id": state["current"]["id"], "color": "red",
                                                         "has_note": True}}  # fmt: skip
    # A second rotation moves the live bookmark only; the detached one stays where it was.
    (root / WFM_ROTATED).rename(root / WFM_OLDER)
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(5, start=90_000))
    ingest(db, root)
    state = view(db)
    assert_linked(state["current"], WFM_OLDER, current_msg)
    assert_orphan(state["old rotated"])
    assert state["old rotated"]["member"] == WFM_ROTATED
    assert bm.for_entries(db, [entry_at(db, WFM_ROTATED, line)[0]]) == {}
    assert bm.reassociate(db) == {"bookmarks": 2, "relinked": 0, "orphaned": 1}


# --------------------------------------------------------------------------- export / import


def notes_state(conn) -> dict[str, tuple]:
    return {
        i["note"]: (i["member"], i["line_no"], i["orphan"], i["msg"])
        for i in bm.list_bookmarks(conn, limit=bm.MAX_LIMIT)[0]
    }


def test_export_import_keeps_orphans_and_linked_bookmarks_at_one_locator(tmp_path, db):
    root, line = rotate_onto_a_twin(tmp_path, db)
    # The same lineage can also hold an orphan and a linked bookmark at one line (rewritten content).
    early = record_lines(db, WFM)[3]
    mark(db, WFM, early, "early")
    (root / WFM).write_bytes(tp.make_log(10, start=500_000))
    assert actions(ingest(db, root)) == {WFM: "reindex"}
    mark(db, WFM, early, "new record")
    expected = notes_state(db)
    assert expected["early"][2] is True and expected["new record"][2] is False
    data = bm.export_bookmarks(db)
    items = {i["note"]: i for i in data["bookmarks"]}
    assert data["version"] == 2 and items["old rotated"]["detached"] and items["early"]["orphan"]
    assert not items["current"]["orphan"] and not items["current"]["detached"]
    # Into a fresh case of the same files: identities decide, no note is lost or swapped.
    other = tp.fresh_db(tmp_path, "other")
    ingest(other, root)
    assert bm.import_bookmarks(other, data) == {"imported": 4, "existing": 0, "unmatched": 0, "invalid": 0}
    assert notes_state(other) == expected
    assert bm.import_bookmarks(other, data) == {"imported": 0, "existing": 4, "unmatched": 0, "invalid": 0}
    assert notes_state(other) == expected
    # Back into the case it came from, after the bookmarks were deleted.
    for (bid,) in db.execute("SELECT id FROM bookmark").fetchall():
        bm.delete_bookmark(db, bid)
    assert bm.import_bookmarks(db, data)["imported"] == 4
    assert notes_state(db) == expected
    # Detached and orphan items do not take a record later on: only the record they bookmarked.
    force_reindex(db, WFM_ROTATED)
    force_reindex(db, WFM)
    ingest(db, root)
    assert notes_state(db) == expected
    other.close()


def test_a_version_1_export_still_imports(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    ingest(db, root)
    lines = record_lines(db, WFM)
    doc = {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": [
        {"member": WFM, "line_no": lines[2], "color": "red", "note": "v1"},
        {"member": WFM, "line_no": 999, "note": "no record"},
    ]}  # fmt: skip
    assert bm.import_bookmarks(db, doc) == {"imported": 2, "existing": 0, "unmatched": 0, "invalid": 0}
    state = view(db)
    assert_linked(state["v1"], WFM, entry_at(db, WFM, lines[2])[1])
    assert db.execute("SELECT fp FROM bookmark WHERE note = 'v1'").fetchone()[0].startswith("f:")
    assert_orphan(state["no record"])
    assert bm.import_bookmarks(db, doc)["existing"] == 2


# --------------------------------------------------------------------------- cost


def bookmark_statements(statements: list[str]) -> list[str]:
    return [s for s in statements if re.search(r"\b(from|into|update)\s+bookmark\b", s, re.IGNORECASE)]


def timed_reindex(conn, root) -> tuple[float, list[str]]:
    force_reindex(conn, WFM)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        start = time.perf_counter()
        assert actions(run_ingest(conn, root)) == {WFM: "reindex"}
        elapsed = time.perf_counter() - start
    finally:
        conn.set_trace_callback(None)
    return elapsed, bookmark_statements(statements)


def test_reindex_cost_follows_orphans_and_bookmarked_records_not_the_table(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(60_000)})
    ingest(db, root)
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 60_000
    empty, empty_statements = timed_reindex(db, root)
    assert all(s.strip().upper().startswith("SELECT 1 FROM BOOKMARK") for s in empty_statements)
    sid = db.execute("SELECT id FROM source").fetchone()[0]
    rows = db.execute("SELECT id, line_no FROM entry ORDER BY id").fetchall()
    linked, orphans = rows[0::30][:2000], rows[15::30][:2000]
    last = rows[-1][0]
    values = [
        (1, WFM, line, eid, "red", "linked", "2030-01-01T00:00:00Z", sid,
         bm.fingerprint(off, ts, text, open_record=eid == last), bm.new_uid())
        for eid, (_sid, line, off, ts, text) in bm._contents(db, [eid for eid, _line in linked])
    ]  # fmt: skip
    values += [
        (1, WFM, line, None, "blue", "orphan", "2030-01-01T00:00:00Z", sid, "f:" + "0" * 32, bm.new_uid())
        for _eid, line in orphans  # a record sits at every orphan's line, never its own
    ]
    db.executemany(bm._INSERT_SQL, values)
    db.commit()
    busy, busy_statements = timed_reindex(db, root)
    counts = db.execute("SELECT note, count(entry_id), count(*) FROM bookmark GROUP BY note ORDER BY note")
    assert counts.fetchall() == [("linked", 2000, 2000), ("orphan", 0, 2000)]
    # Per linked bookmark: its detach, the release of a stale holder and the link itself; nothing per
    # record of the file and nothing per orphan beyond the candidate check of its line.
    assert len(busy_statements) <= 3 * 2000 + 20 * len(empty_statements) + 20
    print(
        f"\nreindex of 60000 records: empty table {empty:.2f}s ({len(empty_statements)} bookmark statements),"
        f" 2000 linked + 2000 orphans {busy:.2f}s ({len(busy_statements)} bookmark statements)"
    )
    assert busy < 2 * empty + 2.0  # generous: CI machines vary; the measured overhead is about 10 %


# --------------------------------------------------------------------------- complete identities never weaken


def closed_log(last: str, *, closed: bool) -> bytes:
    """Three records; the bookmarked one is ``closed A`` (2nd), closed by a 3rd header or left last."""
    lines = [tp.mf.header(0, "INFO", "[Alpha] first"), tp.mf.header(10, "INFO", f"[Alpha] {last}")]
    if closed:
        lines.append(tp.mf.header(20, "INFO", "[Alpha] closes it"))
    return tp.mf.render(lines)


def test_a_complete_fingerprint_survives_a_truncation_that_leaves_its_record_last(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: closed_log("closed A", closed=True)})
    ingest(db, root)
    line = record_lines(db, WFM)[1]
    mark(db, WFM, line, "closed")
    full = db.execute("SELECT fp FROM bookmark").fetchone()[0]
    assert full.startswith("f:")
    (root / WFM).write_bytes(closed_log("closed A", closed=False))  # the closing header is cut away
    ingest(db, root)
    assert_linked(view(db)["closed"], WFM, "[Alpha] closed A")  # same content: still its record
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0] == full  # never weakened to a prefix
    # The record now grows into other content: it is not the bookmarked record any more.
    (root / WFM).write_bytes(closed_log("closed A extended DIFFERENT CONTENT", closed=False))
    ingest(db, root)
    assert_orphan(view(db)["closed"])
    assert db.execute("SELECT fp FROM bookmark").fetchone()[0] == full
    (root / WFM).write_bytes(closed_log("closed A", closed=True))  # the original content is back
    ingest(db, root)
    assert_linked(view(db)["closed"], WFM, "[Alpha] closed A")


def test_import_keeps_a_complete_fingerprint_on_a_record_that_is_last(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: closed_log("closed A", closed=True)})
    ingest(db, root)
    mark(db, WFM, record_lines(db, WFM)[1], "closed")
    data = bm.export_bookmarks(db)
    full = data["bookmarks"][0]["fp"]
    assert full.startswith("f:")
    other_root = tp.write_folder(tmp_path / "Other", {WFM: closed_log("closed A", closed=False)})
    other = tp.fresh_db(tmp_path, "other")
    ingest(other, other_root)
    assert bm.import_bookmarks(other, data)["imported"] == 1
    assert_linked(view(other)["closed"], WFM, "[Alpha] closed A")
    assert other.execute("SELECT fp FROM bookmark").fetchone()[0] == full
    (other_root / WFM).write_bytes(closed_log("closed A extended DIFFERENT CONTENT", closed=False))
    ingest(other, other_root)
    assert_orphan(view(other)["closed"])
    other.close()


# --------------------------------------------------------------------------- export identity


def test_two_detached_bookmarks_with_one_locator_and_identity_both_round_trip(tmp_path, db):
    root = tp.write_folder(
        tmp_path / "Log", {WFM_ROTATED: twin_log("origin of file A"), WFM: twin_log("origin of file B")}
    )
    ingest(db, root)
    line = record_lines(db, WFM)[6]
    mark(db, WFM_ROTATED, line, "note of A")
    mark(db, WFM, line, "note of B")
    for newer in ("origin of file C", "origin of file D"):  # .log.0 dropped twice: both lineages detach
        (root / WFM_ROTATED).unlink()
        (root / WFM).rename(root / WFM_ROTATED)
        (root / WFM).write_bytes(twin_log(newer))
        ingest(db, root)
    rows = db.execute("SELECT member, line_no, fp, source_id FROM bookmark ORDER BY id").fetchall()
    assert rows[0] == rows[1] and rows[0][3] is None  # same locator, same identity, both detached
    data = bm.export_bookmarks(db)
    other = tp.fresh_db(tmp_path, "other")
    ingest(other, root)
    assert bm.import_bookmarks(other, data) == {"imported": 2, "existing": 0, "unmatched": 0, "invalid": 0}
    assert len({i["uid"] for i in data["bookmarks"]}) == 2  # the stable identity tells them apart
    assert sorted(notes_state(other)) == ["note of A", "note of B"]
    assert bm.import_bookmarks(other, data) == {"imported": 0, "existing": 2, "unmatched": 0, "invalid": 0}
    for (bid,) in db.execute("SELECT id FROM bookmark").fetchall():
        bm.delete_bookmark(db, bid)
    assert bm.import_bookmarks(db, data)["imported"] == 2  # back into the case it came from
    assert bm.import_bookmarks(db, data)["existing"] == 2
    assert sorted(notes_state(db)) == ["note of A", "note of B"]
    other.close()


def test_import_after_a_rotation_does_not_duplicate_a_bookmark_that_moved(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(30)})
    ingest(db, root)
    mark(db, WFM, record_lines(db, WFM)[4], "moved")
    data = bm.export_bookmarks(db)
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tp.make_log(30, start=40_000))
    ingest(db, root)
    assert view(db)["moved"]["member"] == WFM_ROTATED
    assert bm.import_bookmarks(db, data) == {"imported": 0, "existing": 1, "unmatched": 0, "invalid": 0}
    assert db.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 1


def test_a_regrown_open_record_stays_with_its_bookmark_when_an_older_orphan_also_matches(tmp_path, db):
    head = tp.mf.header(0, "INFO", "[Alpha] still open")
    root = tp.write_folder(tmp_path / "Log", {WFM: f"{head}\n\n".encode()})  # an empty continuation line
    ingest(db, root)
    mark(db, WFM, 1, "older")  # open identity: the first line plus "\n"
    (root / WFM).write_bytes(f"{head}\n".encode())  # shorter: parsed again without the empty line
    ingest(db, root)
    assert_orphan(view(db)["older"])
    mark(db, WFM, 1, "newer")  # the record as it is now: a shorter open identity
    (root / WFM).write_bytes(f"{head}\nx".encode())  # it grows: both identities match the record now
    assert actions(ingest(db, root)) == {WFM: "append"}
    state = view(db)
    assert_linked(state["newer"], WFM, "[Alpha] still open")  # the bookmark it had keeps it
    assert_orphan(state["older"])
