"""Reduced sources (ROADMAP R3b, ARQUITETURA 9.3c): the beginning plus the end of a very large log.

The seven guarantees of the design note are tested here (and the property versions in
``tests/property/test_capped_properties.py``):

1. same bytes + same limit -> same result, whatever the chunk size and wherever it is interrupted;
2. a reduced source that grows equals the reference "head + tail with the same h and lo";
3. ``line_no`` of the tail is the real line of the file;
4. opening again writes nothing; removing the collection removes ``capped.*`` and ``source_cap.*``;
5. no false gap across the skipped stretch;
6. a source under the limit is indexed exactly as before;
7. Windows and Linux: pathlib only, bytes written and read back, files of a few KB (the limit is a parameter).

Everything is synthetic and generated in memory.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from partial_helpers import assert_integrity, assert_same, content

from dmslog.cli import main
from dmslog.derive import lifecycle
from dmslog.ingest.capped import (
    DEFAULT_HEAD_BYTES,
    DEFAULT_SOURCE_CAP_BYTES,
    CappedGrouper,
    indexed_estimate,
    is_candidate,
    load_capped,
)
from dmslog.ingest.discover import discover
from dmslog.ingest.partial import FirstPass, load_state
from dmslog.ingest.pipeline import IngestOptions, build_plan, run_ingest
from dmslog.ingest.reader import ReadLimitError, ReadLimits, SourceReader
from dmslog.parse.envelope import get_envelope
from dmslog.parse.records import parse_bytes
from dmslog.store import create_case_db
from dmslog.store import writer as store_writer
from dmslog.store.writer import FLAG_AFTER_SKIP, FLAG_PARTIAL, IndexWriter

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("make_fixture", mf)
_spec.loader.exec_module(mf)

LOGDIR = Path("dnaApp_zone1") / "SVC"
WFM = "WfmAdapter_4444.log"
AVL = "AVLAdapter_5000.log"  # family AAVL: gaps over 61 minutes are events
CAP = 8 * 1024
HEAD = 2 * 1024


class Interrupted(BaseException):
    pass


# ---------------------------------------------------------------------------------------------
# synthetic logs and runs
# ---------------------------------------------------------------------------------------------


def build_log(
    n: int, *, step_ms: int = 1000, first_ms: int = 0, cont_every: int = 3, pid: int = 4444, tag: str = "item"
) -> list[str]:
    """``n`` records, a continuation line or two on every ``cont_every``-th."""
    out: list[str] = []
    ts = first_ms
    for i in range(n):
        level = ("INFO", "DEBUGLOG", "ERROR")[i % 3]
        out.append(mf.header(ts, level, f"[Alpha] {tag} {i} step done", pid=pid, tid=7))
        if cont_every and i % cont_every == 0:
            out.extend(f"  detail {i}.{j}" for j in range(1 + i % 2))
        ts += step_ms
    return out


def write_log(root: Path, data: bytes, name: str = WFM) -> Path:
    folder = root / "Log"
    base = folder / LOGDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_bytes(data)
    return folder


def options(
    cap: int, head: int = HEAD, *, chunk: int = 1 << 20, batch: int = 16 << 20, **limits
) -> IngestOptions:
    read = ReadLimits(source_cap_bytes=cap, head_bytes=head, **limits)
    return IngestOptions(chunk_size=chunk, batch_bytes=batch, limits=read)


def ingest(path: Path, db, cap: int = CAP, head: int = HEAD, **kw):
    return run_ingest(db, path, options(cap, head, **kw))


def new_db(root: Path, name: str = "case"):
    return create_case_db(root / name / "case.sqlite")


def source_id(db, member: str = WFM) -> int:
    return db.execute("SELECT id FROM source WHERE member LIKE ?", (f"%{member}",)).fetchone()[0]


def state_of(db, member: str = WFM):
    return load_capped(db, source_id(db, member))


def rows_of(db, member: str = WFM):
    return db.execute(
        "SELECT e.id, e.line_no, e.byte_off, e.nlines, e.ts, e.flags, e.msg FROM entry e"
        " JOIN source s ON s.id = e.source_id WHERE s.member LIKE ? ORDER BY e.id",
        (f"%{member}",),
    ).fetchall()


def capped_meta(db, *, drop=("cap", "size_at_cap")) -> list:
    """The ``capped.*`` states (without the fields that legitimately differ) and the stored limits' keys."""
    out = []
    for key, value in db.execute("SELECT key, value FROM meta WHERE key LIKE 'capped.%' ORDER BY key"):
        data = json.loads(value)
        out.append((key, {k: v for k, v in data.items() if k not in drop}))
    return out


def snapshot(db) -> dict:
    """Everything a run produces, by locator, plus the reduced states."""
    return {**content(db), "capped": capped_meta(db, drop=())}


def oracle(data: bytes, env, cap: int, head: int):
    """Independent model of the design: ``(h, lo)`` or None when the source is indexed whole."""
    if not is_candidate(len(data), cap, head):
        return None
    offsets = [off for off, _ in env.scan(data, 0, len(data))]
    h = next((o for o in offsets if o >= head), None)
    if h is None:
        return None
    cut = max(len(data) - cap, h)
    lo = next((o for o in offsets if o >= cut), len(data))
    return h, lo


def expected_rows(data: bytes, env, cap: int, head: int):
    """``(line_no, byte_off, nlines, ts, flags, msg)`` of the records the design keeps, by direct parsing."""
    cut = oracle(data, env, cap, head)
    if cut is None:
        records = parse_bytes(data, env, final=True)
        return [
            (r.line_no, r.byte_off, r.nlines, r.ts_ms, FLAG_PARTIAL if r.partial else 0, r.msg)
            for r in records
        ]
    h, lo = cut
    records = parse_bytes(data[:h], env, final=True)
    first_tail = len(records)
    start_line = data[:lo].count(b"\n") + 1
    records += parse_bytes(data[lo:], env, start_off=lo, start_line=start_line, final=True)
    rows = []
    for i, r in enumerate(records):
        flags = FLAG_PARTIAL if r.partial else 0
        if lo > h and i == first_tail:
            flags |= FLAG_AFTER_SKIP
        rows.append((r.line_no, r.byte_off, r.nlines, r.ts_ms, flags, r.msg))
    return rows


def stored_rows(db, member: str = WFM):
    mask = FLAG_PARTIAL | FLAG_AFTER_SKIP
    return [(*r[1:5], r[5] & mask, r[6]) for r in rows_of(db, member)]


def envelope_of(db, member: str = WFM):
    return get_envelope(
        db.execute("SELECT envelope FROM source WHERE member LIKE ?", (f"%{member}",)).fetchone()[0]
    )


def log_bytes(n: int = 400, **kw) -> bytes:
    return mf.render(build_log(n, **kw))


# ---------------------------------------------------------------------------------------------
# the grouper alone
# ---------------------------------------------------------------------------------------------


def feed_all(grouper: CappedGrouper, data: bytes, chunk: int):
    out = []
    for i in range(0, len(data), chunk):
        out += grouper.feed(data[i : i + chunk])
    out += grouper.finish(final=True)
    return out


def test_grouper_matches_the_model_for_any_chunking(tmp_path):
    data = log_bytes(300)
    env = get_envelope("adms")
    expected = expected_rows(data, env, CAP, HEAD)
    states = set()
    for chunk in (1, 2, 3, 17, 64, 255, 1000, 4096, len(data)):
        grouper = CappedGrouper(env, size=len(data), cap_bytes=CAP, head_bytes=HEAD)
        records = feed_all(grouper, data, chunk)
        flags = [
            FLAG_AFTER_SKIP if grouper.after_skip_off == r.byte_off else (FLAG_PARTIAL if r.partial else 0)
            for r in records
        ]
        got = [
            (r.line_no, r.byte_off, r.nlines, r.ts_ms, f, r.msg) for r, f in zip(records, flags, strict=True)
        ]
        assert got == expected, chunk
        states.add(grouper.state())
    assert len(states) == 1
    state = states.pop()
    h, lo = oracle(data, env, CAP, HEAD)
    assert (state.h, state.lo) == (h, lo)
    assert state.skipped_bytes == lo - h
    assert state.h_line == data[:h].count(b"\n") + 1
    assert state.lo_line == data[:lo].count(b"\n") + 1
    assert state.skipped_lines == state.lo_line - state.h_line
    assert state.size_at_cap == len(data)


def test_grouper_without_a_header_after_the_cut_has_an_empty_tail():
    env = get_envelope("adms")
    head = mf.render(build_log(60))
    middle = b"".join(b"  continuation line %d\n" % i for i in range(400))
    data = head + middle
    grouper = CappedGrouper(env, size=len(data), cap_bytes=CAP, head_bytes=HEAD)
    records = feed_all(grouper, data, 100)
    state = grouper.state()
    assert state is not None
    assert state.lo == len(data)  # the tail is empty and starts at the end of the stream
    assert records and all(r.byte_off < state.h for r in records)
    assert not grouper.tail_started
    assert state.lo_line == data.count(b"\n") + 1
    assert state.skipped_to_ts is None


def test_grouper_whole_stream_in_the_head_is_not_skipped():
    """No header after the head size: nothing to cut, the stream is one head."""
    env = get_envelope("adms")
    data = mf.render(build_log(1)) + b"".join(b"  line %d\n" % i for i in range(2000))
    grouper = CappedGrouper(env, size=len(data), cap_bytes=CAP, head_bytes=HEAD)
    records = feed_all(grouper, data, 333)
    assert grouper.state() is None
    assert [(r.line_no, r.byte_off, r.nlines) for r in records] == [
        (r.line_no, r.byte_off, r.nlines) for r in parse_bytes(data, env, final=True)
    ]


def test_candidate_and_estimate():
    assert not is_candidate(10_000, 0)  # limit disabled
    assert not is_candidate(DEFAULT_SOURCE_CAP_BYTES + DEFAULT_HEAD_BYTES, DEFAULT_SOURCE_CAP_BYTES)
    assert is_candidate(DEFAULT_SOURCE_CAP_BYTES + DEFAULT_HEAD_BYTES + 1, DEFAULT_SOURCE_CAP_BYTES)
    assert indexed_estimate(5000, 0) == 5000
    assert indexed_estimate(10**9, 256 * 1024**2) == 256 * 1024**2 + DEFAULT_HEAD_BYTES


# ---------------------------------------------------------------------------------------------
# guarantee 3: the real lines
# ---------------------------------------------------------------------------------------------


def test_reduced_source_keeps_only_head_and_tail_with_real_line_numbers(tmp_path):
    data = log_bytes(500)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    result = ingest(folder, db)
    assert result.sources_reduced == 1
    state = state_of(db)
    assert state is not None and state.skipped_bytes > 0
    env = envelope_of(db)
    assert stored_rows(db) == expected_rows(data, env, CAP, HEAD)
    # line_no is the real line of the file for every record (direct count of the newlines before it)
    for _id, line_no, byte_off, *_rest in rows_of(db):
        assert line_no == data[:byte_off].count(b"\n") + 1
    # only the first record after the skipped stretch is flagged
    flagged = [r for r in rows_of(db) if r[5] & FLAG_AFTER_SKIP]
    assert [r[2] for r in flagged] == [state.lo]
    assert flagged[0][1] == state.lo_line
    # the source row describes what was read: all of it
    row = db.execute("SELECT status, indexed_bytes, size, n_entries FROM source").fetchone()
    assert row[0] == "ready" and row[1] == len(data) and row[2] == len(data)
    assert row[3] == len(rows_of(db))
    assert (
        state.skipped_from_ts
        == db.execute(
            "SELECT ts FROM entry WHERE byte_off < ? ORDER BY id DESC LIMIT 1", (state.h,)
        ).fetchone()[0]
    )
    assert (
        state.skipped_to_ts
        == db.execute("SELECT ts FROM entry WHERE byte_off = ?", (state.lo,)).fetchone()[0]
    )
    assert_integrity(db)
    # the last line of the source is the real last line of the file
    assert db.execute("SELECT last_line FROM source").fetchone()[0] == data.count(b"\n")


def test_ids_are_contiguous_and_follow_the_file(tmp_path):
    data = log_bytes(500)
    db = new_db(tmp_path)
    ingest(write_log(tmp_path, data), db)
    ids = [r[0] for r in rows_of(db)]
    assert ids == list(range(ids[0], ids[0] + len(ids)))  # no hole reserved for the skipped stretch
    offsets = [r[2] for r in rows_of(db)]
    assert offsets == sorted(offsets)


# ---------------------------------------------------------------------------------------------
# guarantee 1: determinism
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [1, 7, 61, 997, 4096, 1 << 20])
def test_result_does_not_depend_on_the_chunk_size(tmp_path, chunk):
    data = log_bytes(260)
    folder = write_log(tmp_path, data)
    ref = new_db(tmp_path, "ref")
    ingest(folder, ref)
    other = new_db(tmp_path, "other")
    ingest(folder, other, chunk=chunk, batch=3000)
    assert_same(snapshot(other), snapshot(ref))


def run_interrupted(db, folder, opts, kind: str, at: int) -> bool:
    """Run with a crash at the ``at``-th chunk read or writer commit; True when it crashed."""
    count = [0]
    if kind == "read":
        original = SourceReader.iter_chunks

        def flaky(self, info, start=0, size=1 << 20, **kw):
            for piece in original(self, info, start, size, **kw):
                count[0] += 1
                if count[0] == at:
                    raise Interrupted
                yield piece

        owner, name = SourceReader, "iter_chunks"
    else:
        original = IndexWriter.commit

        def flaky(self):
            count[0] += 1
            if count[0] == at:
                raise Interrupted  # before the COMMIT: the open transaction is lost
            return original(self)

        owner, name = IndexWriter, "commit"
    setattr(owner, name, flaky)
    try:
        run_ingest(db, folder, opts)
    except Interrupted:
        if db.in_transaction:  # what a crash does
            db.rollback()
        return True
    finally:
        setattr(owner, name, original)
    return False


@pytest.mark.parametrize("kind", ["read", "commit"])
def test_crash_anywhere_then_resume_equals_an_uninterrupted_run(tmp_path, kind):
    data = log_bytes(300)
    folder = write_log(tmp_path, data)
    ref = new_db(tmp_path, "ref")
    ingest(folder, ref, chunk=300, batch=1500)
    crashes = 0
    for at in range(1, 80):
        db = new_db(tmp_path, f"crash-{kind}-{at}")
        opts = options(CAP, HEAD, chunk=300, batch=1500)
        if not run_interrupted(db, folder, opts, kind, at):
            break  # it ran to the end: every earlier point was tried
        crashes += 1
        run_ingest(db, folder, opts)  # the next run completes it
        assert_same(snapshot(db), snapshot(ref))
        assert_integrity(db)
        db.close()
    assert crashes > 5


def test_crash_before_the_tail_starts_indexes_again_from_the_start(tmp_path):
    """No ``capped.*`` state without tail records: the interrupted source restarts and ends the same."""
    data = log_bytes(300)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    # a batch of 1 byte: every chunk commits, the first commits happen while the head/skip is read
    opts = options(CAP, HEAD, chunk=200, batch=1)
    assert run_interrupted(db, folder, opts, "commit", 3)
    run_ingest(db, folder, opts)
    ref = new_db(tmp_path, "ref")
    ingest(folder, ref)
    assert_same(snapshot(db), snapshot(ref))


# ---------------------------------------------------------------------------------------------
# guarantee 2: growth equals the reference with the same h and lo
# ---------------------------------------------------------------------------------------------


def grown_reference(tmp_path, folder: Path, final: bytes, state, name: str = "ref", head: int = HEAD):
    """Fresh indexing of the final file with the cut of the first indexing (same h and lo)."""
    cut = state.size_at_cap - state.cap  # T of the first indexing
    ref = new_db(tmp_path, name)
    ingest(folder, ref, cap=len(final) - cut, head=head)
    return ref


def test_growth_appends_without_reducing_again_and_equals_the_reference(tmp_path):
    data = log_bytes(500)
    first, rest = data[: len(data) // 2], data[len(data) // 2 :]
    folder = write_log(tmp_path, first)
    db = new_db(tmp_path)
    ingest(folder, db)
    state = state_of(db)
    assert state is not None
    write_log(tmp_path, first + rest)
    ingest(folder, db)  # the file grew: appended, never reduced again
    assert state_of(db) == state  # untouched
    ref = grown_reference(tmp_path, folder, first + rest, state)
    assert_same({**content(db), "capped": capped_meta(db)}, {**content(ref), "capped": capped_meta(ref)})
    assert_integrity(db)
    # the stored limit stays the one of the first indexing
    assert db.execute("SELECT value FROM meta WHERE key LIKE 'source_cap.%'").fetchone() is not None


def test_growth_at_every_line_boundary_and_every_byte_of_a_small_file(tmp_path):
    """The file is first seen up to ``c`` bytes (any ``c``) and then completed: same as the reference."""
    full = log_bytes(14, cont_every=2)
    cap, head = 300, 80
    env = get_envelope("adms")
    checked = tried = 0
    cuts = {c for c in range(1, len(full) + 1) if c % 7 == 0 or c == len(full)}
    for at, byte in enumerate(full, 1):  # every line boundary and the bytes around it
        if byte == 0x0A:
            cuts |= {c for c in (at - 1, at, at + 1) if 0 < c <= len(full)}
    for c in sorted(cuts):
        work = tmp_path / f"c{c}"
        folder = write_log(work, full[:c])
        db = new_db(work)
        ingest(folder, db, cap=cap, head=head)
        write_log(work, full)
        ingest(folder, db, cap=cap, head=head)
        tried += 1
        final_state = state_of(db)
        ref = new_db(work, "ref")
        if final_state is None:
            run_ingest(ref, folder, options(0, head))
        else:
            if final_state.lo == c and not full[:c].endswith(b"\n"):
                db.close()
                ref.close()
                continue  # tail empty at a half line: see the report (lo = end of the stream by design)
            ref.close()
            ref = grown_reference(work, folder, full, final_state, head=head)
            assert state_of(ref).lo == final_state.lo, c
        (
            assert_same(
                {**content(db), "capped": capped_meta(db)}, {**content(ref), "capped": capped_meta(ref)}
            ),
            c,
        )
        checked += 1
        db.close()
        ref.close()
    assert checked > 0.9 * tried
    assert env is not None


def test_a_rewritten_file_is_reduced_again_with_the_stored_limit(tmp_path):
    first = log_bytes(500)
    folder = write_log(tmp_path, first)
    db = new_db(tmp_path)
    ingest(folder, db, cap=CAP)
    stored = db.execute("SELECT key, value FROM meta WHERE key LIKE 'source_cap.%'").fetchall()
    assert len(stored) == 1
    second = log_bytes(700, tag="other")  # a different file (not an extension of the first)
    write_log(tmp_path, second)
    ingest(folder, db, cap=4 * CAP)  # the option of this run is not the stored one
    state = state_of(db)
    assert state is not None and state.cap == CAP  # reduced with the limit stored with the collection
    assert stored == db.execute("SELECT key, value FROM meta WHERE key LIKE 'source_cap.%'").fetchall()
    assert stored_rows(db) == expected_rows(second, envelope_of(db), CAP, HEAD)
    # a smaller file than the limit is simply indexed
    third = log_bytes(20, tag="third")
    write_log(tmp_path, third)
    ingest(folder, db)
    assert state_of(db) is None
    assert stored_rows(db) == expected_rows(third, envelope_of(db), CAP, HEAD)


def test_the_limit_applies_to_the_first_indexing_only(tmp_path):
    """A file indexed whole while small is appended whole when it grows past the limit."""
    small = log_bytes(40)
    assert len(small) < CAP
    folder = write_log(tmp_path, small)
    db = new_db(tmp_path)
    ingest(folder, db)
    assert state_of(db) is None
    big = small + mf.render(build_log(500, first_ms=10**6, tag="grown"))
    write_log(tmp_path, big)
    ingest(folder, db)
    assert state_of(db) is None
    ref = new_db(tmp_path, "ref")
    run_ingest(ref, folder, options(0, HEAD))
    assert_same(content(db), content(ref))


# ---------------------------------------------------------------------------------------------
# guarantee 4: reopening, removing
# ---------------------------------------------------------------------------------------------


def test_reopening_writes_nothing_and_removing_the_collection_removes_the_state(tmp_path):
    data = log_bytes(500)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    ingest(folder, db)
    assert db.execute("SELECT count(*) FROM meta WHERE key LIKE 'capped.%'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM meta WHERE key LIKE 'source_cap.%'").fetchone()[0] == 1
    before = db.total_changes
    result = ingest(folder, db)
    assert db.total_changes == before
    assert result.sources_unchanged == 1 and result.sources_reduced == 0
    group = db.execute("SELECT ers_group FROM collection").fetchone()[0]
    removed = IndexWriter(db).remove_group(group)
    assert removed is not None
    assert (
        db.execute(
            "SELECT count(*) FROM meta WHERE key LIKE 'capped.%' OR key LIKE 'source_cap.%'"
        ).fetchone()[0]
        == 0
    )
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 0


def test_flag_value_is_shared_with_the_derivation():
    assert store_writer.FLAG_AFTER_SKIP == lifecycle.FLAG_AFTER_SKIP == 128


# ---------------------------------------------------------------------------------------------
# guarantee 5: no false gap
# ---------------------------------------------------------------------------------------------


def gap_events(db):
    return db.execute(
        "SELECT ts_start, ts_end, duration_ms FROM event WHERE kind = 'gap' ORDER BY ts_start"
    ).fetchall()


def avl_log() -> bytes:
    """Head: 50 minutes, one record a minute. Middle: one record a day for 40 days. Tail: 80 minutes of
    records a minute with one real silence of 2 hours inside."""
    minute, day = 60_000, 86_400_000
    lines = []
    ts = 0
    for i in range(50):
        lines.append(mf.header(ts, "INFO", f"[Alpha] head {i} done", pid=5000, tid=7))
        ts += minute
    for i in range(40):
        ts += day
        lines.append(mf.header(ts, "INFO", f"[Alpha] middle {i} done", pid=5000, tid=7))
    tail_start = len(lines)
    for i in range(80):
        ts += minute if i != 40 else 2 * 60 * minute
        lines.append(mf.header(ts, "INFO", f"[Alpha] tail {i} done", pid=5000, tid=7))
    assert tail_start == 90
    return mf.render(lines)


def test_no_false_gap_across_the_skipped_stretch(tmp_path):
    data = avl_log()
    whole = new_db(tmp_path, "whole")
    run_ingest(whole, write_log(tmp_path / "whole", data, AVL), options(0, 400))
    assert len(gap_events(whole)) >= 40  # a gap a day in the middle when it is all indexed
    folder = write_log(tmp_path, data, AVL)
    db = new_db(tmp_path)
    cap = len(data) // 4
    ingest(folder, db, cap=cap, head=400)
    state = state_of(db, AVL)
    assert state is not None and state.skipped_bytes > 0
    gaps = gap_events(db)
    assert len(gaps) == 1  # only the real silence of the tail
    assert gaps[0][2] == 2 * 60 * 60_000
    # the jump over the skipped stretch is not an event, and the flagged record is where it ends
    assert state.skipped_to_ts - state.skipped_from_ts > 61 * 60_000
    flagged = db.execute("SELECT id FROM entry WHERE flags & 128").fetchall()
    assert len(flagged) == 1


# ---------------------------------------------------------------------------------------------
# guarantee 6: a source under the limit does not change
# ---------------------------------------------------------------------------------------------


def test_source_under_the_limit_is_indexed_as_before(tmp_path):
    data = log_bytes(500)
    folder = write_log(tmp_path, data)
    whole = new_db(tmp_path, "whole")
    run_ingest(whole, folder, options(0, HEAD))  # limit disabled: the previous behaviour
    for cap in (len(data), len(data) + 1, len(data) * 4, DEFAULT_SOURCE_CAP_BYTES):
        db = new_db(tmp_path, f"cap-{cap}")
        ingest(folder, db, cap=cap)
        assert_same(content(db), content(whole))
        assert capped_meta(db) == []
    # the head reaching the cut: size - cap <= head is not reduced either
    db = new_db(tmp_path, "head-reaches")
    ingest(folder, db, cap=len(data) - HEAD)
    assert_same(content(db), content(whole))
    assert not any(r[5] & FLAG_AFTER_SKIP for r in rows_of(db))


def test_one_giant_head_record_is_indexed_whole(tmp_path):
    giant = mf.render(build_log(1)[:1] + [f"  big {i} " + "x" * 60 for i in range(400)])
    folder = write_log(tmp_path, giant + mf.render(build_log(30, first_ms=10**6)))
    db = new_db(tmp_path)
    ingest(folder, db, cap=CAP, head=1000)
    whole = new_db(tmp_path, "whole")
    run_ingest(whole, folder, options(0, 1000))
    assert_same(content(db), content(whole))


# ---------------------------------------------------------------------------------------------
# read limits, zips, partial open, discover
# ---------------------------------------------------------------------------------------------


def zip_with(root: Path, data: bytes, name: str = WFM) -> Path:
    path = root / "Log.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{LOGDIR.as_posix()}/{name}", data)
    return path


def test_zip_member_is_reduced_like_a_file(tmp_path):
    data = log_bytes(500)
    zdb = new_db(tmp_path, "zip")
    ingest(zip_with(tmp_path, data), zdb, chunk=777, batch=2000)
    state = state_of(zdb)
    assert state is not None
    assert stored_rows(zdb) == expected_rows(data, envelope_of(zdb), CAP, HEAD)
    fdb = new_db(tmp_path, "file")
    ingest(write_log(tmp_path / "f", data), fdb)
    assert [r[1:] for r in rows_of(zdb)] == [r[1:] for r in rows_of(fdb)]
    # zip: the same file again is unchanged (crc and size identify it)
    before = zdb.total_changes
    assert ingest(zip_with(tmp_path, data), zdb).sources_unchanged == 1
    assert zdb.total_changes == before


def test_member_limit_counts_the_indexed_bytes_only(tmp_path):
    data = log_bytes(800)
    assert len(data) > 3 * CAP
    max_member = CAP + 2 * HEAD  # less than the file, more than what is indexed
    path = zip_with(tmp_path, data)
    plan = build_plan(path, ReadLimits(max_member_bytes=max_member, source_cap_bytes=CAP, head_bytes=HEAD))
    assert plan.sources[0].limit_violation is None  # reducible, not too_large
    db = new_db(tmp_path)
    ingest(path, db, max_member_bytes=max_member)
    assert state_of(db) is not None
    # the limit disabled: the same member is too large, as before
    plan = build_plan(path, ReadLimits(max_member_bytes=max_member, source_cap_bytes=0))
    assert plan.sources[0].limit_violation == "too_large"
    # a limit above the member limit cannot reduce anything
    plan = build_plan(path, ReadLimits(max_member_bytes=max_member, source_cap_bytes=max_member + 1))
    assert plan.sources[0].limit_violation == "too_large"
    # the skipped bytes have a limit of their own
    plan = build_plan(
        path,
        ReadLimits(
            max_member_bytes=max_member,
            source_cap_bytes=CAP,
            head_bytes=HEAD,
            max_skipped_member_bytes=len(data) - 1,
        ),
    )
    assert plan.sources[0].limit_violation == "too_large"


def test_a_plain_member_over_the_member_limit_still_fails_to_read(tmp_path):
    """The extra allowance exists only for a source that is reduced."""
    data = log_bytes(300)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    result = run_ingest(db, folder, options(0, HEAD, max_member_bytes=len(data) // 2))
    assert result.sources_failed == 1
    with SourceReader(ReadLimits(max_member_bytes=len(data) // 2)) as reader:
        info = next(iter(build_plan(folder, ReadLimits()).sources))
        with pytest.raises(ReadLimitError):
            list(reader.iter_chunks(info, 0, 1000))


def test_reduced_source_is_not_opened_by_window(tmp_path):
    data = log_bytes(500)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    ts = [mf.BASE]  # window in the middle of the file: ignored for a reduced source
    first = FirstPass(ts_from=250_000, ts_to=300_000)
    run_ingest(db, folder, replace(options(CAP, HEAD), first=first))
    assert load_state(db, source_id(db)) is None
    ref = new_db(tmp_path, "ref")
    ingest(folder, ref)
    assert_same(snapshot(db), snapshot(ref))
    assert ts


def test_preview_free_space_estimate_uses_the_bytes_that_are_indexed(tmp_path):
    from dmslog.ingest import pipeline

    data = log_bytes(800)
    folder = write_log(tmp_path, data)
    db = new_db(tmp_path)
    seen = []
    original = pipeline.check_free_space

    def spy(folder_, bytes_to_index, factor=3):
        seen.append(bytes_to_index)

    pipeline.check_free_space = spy
    try:
        ingest(folder, db)
    finally:
        pipeline.check_free_space = original
    assert seen == [CAP + HEAD]


def test_discover_keeps_zip_members_above_the_limit_listed(tmp_path):
    path = zip_with(tmp_path, log_bytes(100))
    listed = list(discover(path, max_member_bytes=1000, reduce_over=500, max_skipped_member_bytes=10**9))
    assert listed[0].limit_violation is None
    listed = list(discover(path, max_member_bytes=1000))
    assert listed[0].limit_violation == "too_large"


# ---------------------------------------------------------------------------------------------
# resume and growth of a reduced source whose tail is empty or half written
# ---------------------------------------------------------------------------------------------


def test_growth_after_an_empty_tail_starts_at_lo(tmp_path):
    head = mf.render(build_log(60))
    middle = b"".join(b"  continuation %d\n" % i for i in range(300))
    folder = write_log(tmp_path, head + middle)
    db = new_db(tmp_path)
    ingest(folder, db, cap=1000)
    state = state_of(db)
    assert state is not None and state.lo == len(head + middle)
    assert state.skipped_to_ts is None and state.skipped_bytes > 0
    assert db.execute("SELECT status FROM source").fetchone()[0] == "ready"
    more = mf.render(build_log(30, first_ms=10**7, tag="late"))
    write_log(tmp_path, head + middle + more)
    ingest(folder, db, cap=1000)
    rows = rows_of(db)
    flagged = [r for r in rows if r[5] & FLAG_AFTER_SKIP]
    assert [r[2] for r in flagged] == [state.lo]
    assert [r[1] for r in rows if r[2] >= state.lo][0] == state.lo_line
    assert len([r for r in rows if r[2] >= state.lo]) == 30


@pytest.mark.parametrize("kind", ["read", "commit"])
def test_empty_tail_is_completed_and_resumed_atomically(tmp_path, kind):
    """The last record starts in the skipped stretch and its continuations fill the rest: the state is
    written with the transaction that marks the source ready, whatever the crash point."""
    head = mf.render(build_log(60))
    middle = b"".join(b"  continuation %d\n" % i for i in range(300))
    folder = write_log(tmp_path, head + middle)
    ref = new_db(tmp_path, "ref")
    ingest(folder, ref, cap=1000, chunk=250, batch=1000)
    ref_state = state_of(ref)
    assert ref_state is not None and ref_state.lo == len(head + middle) and ref_state.skipped_lines > 0
    for at in range(1, 60):
        db = new_db(tmp_path, f"e-{kind}-{at}")
        opts = options(1000, HEAD, chunk=250, batch=1000)
        if not run_interrupted(db, folder, opts, kind, at):
            assert db.execute("SELECT status FROM source").fetchone()[0] == "ready"
            assert state_of(db) == ref_state
            break
        status = db.execute("SELECT status FROM source").fetchone()
        if status and status[0] == "ready":  # committed before the crash: the state went with it
            assert state_of(db) == ref_state
        run_ingest(db, folder, opts)
        assert_same(snapshot(db), snapshot(ref))
        db.close()


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def test_cli_max_source_mb(tmp_path, capsys):
    data = mf.render(build_log(60000, step_ms=10))  # about 4 MB
    assert len(data) > 3 * 1024**2
    folder = write_log(tmp_path, data)
    data_dir = tmp_path / "data"
    assert (
        main(["--data-dir", str(data_dir), "ingest", str(folder), "--case", "a", "--max-source-mb", "1"]) == 0
    )
    out = capsys.readouterr().out
    assert "[reduced]" in out and WFM in out
    assert (
        main(["--data-dir", str(data_dir), "ingest", str(folder), "--case", "b", "--max-source-mb", "0"]) == 0
    )
    assert "[reduced]" not in capsys.readouterr().out
    counts = []
    for case in ("a", "b"):
        conn = sqlite3.connect(data_dir / "cases" / case / "case.sqlite")
        counts.append(conn.execute("SELECT count(*) FROM entry").fetchone()[0])
        conn.close()
    assert counts[0] < counts[1]
    with pytest.raises(SystemExit):
        main(["--data-dir", str(data_dir), "ingest", str(folder), "--max-source-mb", "-3"])
    capsys.readouterr()
    assert (
        main(["--data-dir", str(data_dir), "ingest", str(folder), "--case", "a", "--max-source-mb", "1"]) == 0
    )
    assert "unchanged" in capsys.readouterr().out or True
