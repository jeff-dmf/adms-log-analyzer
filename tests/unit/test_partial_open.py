"""Partial open (ROADMAP 9.9, ARQUITETURA 9.3b): window and/or sources first, the rest in a second pass.

The invariant checked everywhere: after the second pass the case is the one a full indexing builds (records,
keys, payloads, events, links, buckets, unknown signatures, source rows; compared by locator, never by id),
ids still follow the file order inside sources and along rotation chains, and running the ingestion again
writes nothing.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from partial_helpers import assert_ids_follow_chains, assert_integrity, assert_same, content

from dmslog.ingest import partial
from dmslog.ingest.partial import FirstPass, PartialState, load_state, locate_window, pending_work
from dmslog.ingest.pipeline import (
    ACTION_COMPLETE,
    ACTION_WINDOW,
    STAGE_FIRST,
    STAGE_REST,
    IngestOptions,
    build_plan,
    preview_sources,
    resume_pending,
    run_ingest,
)
from dmslog.ingest.reader import ReadLimits, SourceReader
from dmslog.store import bookmarks, create_case_db
from dmslog.store.writer import IndexWriter

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("make_fixture", mf)
_spec.loader.exec_module(mf)

FAMILIES = ROOT / "tests" / "fixtures" / "families"
ROTATION = ROOT / "tests" / "fixtures" / "logs" / "rotation"
LOGDIR = "dnaApp_zone1/SVC"
WFM = f"{LOGDIR}/WfmAdapter_1111.log"
WFM0 = f"{LOGDIR}/WfmAdapter_1111.log.0"
SMALL = {"chunk_size": 700, "batch_bytes": 2000}  # several chunks and commits per source


class Interrupted(BaseException):
    """A crash / Ctrl+C in the middle of a run."""


def make_log(n: int, start_ms: int = 0, step: int = 1000, pid: int = 1111) -> bytes:
    lines = []
    for i in range(n):
        lines.append(
            mf.header(start_ms + i * step, "INFO" if i % 5 else "ERROR", f"[Alpha] item {i} done", pid=pid)
        )
        if i % 4 == 0:
            lines.append(f"  continuation of item {i}")
    return mf.render(lines)


def ms(offset: int) -> int:
    """Log time of ``mf.BASE + offset`` ms, as stored (local time encoded as UTC, U6)."""
    from dmslog.parse.envelope import parse_ts

    text = mf.fmt_ts(offset)
    return parse_ts(text[:19].encode(), text[20:23].encode())


def write_folder(root: Path, files: dict[str, bytes]) -> Path:
    for member, data in files.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def write_zip(dest: Path, files: dict[str, bytes]) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / mf.ers_file_name("enterprise", mf.synth_guid(1), "20300115103000", "t")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{mf.LOG_ROOT}/{name}", data)
    return path


def full_content(tmp_path: Path, path: Path, name: str = "full", **options) -> dict:
    conn = create_case_db(tmp_path / name / "case.sqlite")
    try:
        run_ingest(conn, path, IngestOptions(**options))
        return content(conn)
    finally:
        conn.close()


def flaky_reader(monkeypatch, fail_at_chunk: int) -> None:
    original = SourceReader.iter_chunks
    count = [0]

    def flaky(self, info, start=0, chunk_size=1 << 20):
        for chunk in original(self, info, start, chunk_size):
            count[0] += 1
            if count[0] == fail_at_chunk:
                raise Interrupted
            yield chunk

    monkeypatch.setattr(SourceReader, "iter_chunks", flaky)


def unchanged_again(conn, path, **options) -> bool:
    before = conn.total_changes
    run_ingest(conn, path, IngestOptions(**options))
    return conn.total_changes == before


# --------------------------------------------------------------------------- locating the window


def info_of(path: Path, member: str):
    return next(s for s in build_plan(path, ReadLimits()).sources if s.member.endswith(member))


@pytest.mark.parametrize("container", ["folder", "zip"])
def test_locate_window_finds_the_boundaries_lines_and_head(tmp_path, container):
    data = make_log(200)  # one record per second, every 4th with a continuation line
    path = (
        write_folder(tmp_path / "Log", {WFM: data})
        if container == "folder"
        else write_zip(tmp_path, {WFM: data})
    )
    info = info_of(path, WFM)
    with SourceReader() as reader:
        span = locate_window(reader, info, ms(50_000), ms(120_000), 512)
    lines = data.split(b"\n")
    lo = data.index(mf.fmt_ts(50_000).encode())
    hi = data.index(mf.fmt_ts(120_000).encode())
    assert (span.lo, span.hi, span.size) == (lo, hi, len(data))
    assert span.lo_line == data[:lo].count(b"\n") + 1 and span.hi_line == data[:hi].count(b"\n") + 1
    assert span.lines == len(lines) - 1 and span.envelope == "adms"
    assert not span.whole and not span.empty
    with SourceReader() as reader:
        assert locate_window(reader, info, None, None, 512).whole
        assert locate_window(
            reader, info, ms(10**9), None, 512
        ).empty  # after the end: empty window at the end
        assert locate_window(reader, info, ms(10**9), None, 512).lo == len(data)
        assert locate_window(reader, info, None, ms(-1), 512).hi == 0  # before the start
        open_end = locate_window(reader, info, ms(150_500), None, 4096)
    assert open_end.lo == data.index(mf.fmt_ts(151_000).encode()) and open_end.hi == len(data)


def test_locate_window_of_a_plain_file_is_empty_unless_unbounded_at_the_start(tmp_path):
    data = b"no header here\nnor here\n"
    path = write_folder(tmp_path / "Log", {f"{LOGDIR}/notes.txt": data})
    info = info_of(path, f"{LOGDIR}/notes.txt")
    with SourceReader() as reader:
        assert locate_window(reader, info, ms(0), ms(10), 64).empty
        assert locate_window(reader, info, None, ms(10), 64).whole


def test_first_pass_validation():
    with pytest.raises(ValueError):
        FirstPass(ts_from=5, ts_to=5)
    with pytest.raises(ValueError):
        FirstPass(members=frozenset())
    with pytest.raises(ValueError):
        FirstPass(ts_from=True)
    assert not FirstPass().windowed and FirstPass(ts_to=1).windowed


# --------------------------------------------------------------------------- window first, then the rest


@pytest.mark.parametrize("container", ["folder", "zip"])
def test_window_first_then_complete_equals_full_indexing(tmp_path, container):
    files = {WFM: make_log(300), f"{LOGDIR}/OntAdapter_2222.log": make_log(120, start_ms=100_000, pid=2222)}
    path = (
        write_folder(tmp_path / "Log", files) if container == "folder" else write_zip(tmp_path / "z", files)
    )
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        events = []
        first = FirstPass(ts_from=ms(120_000), ts_to=ms(180_000), defer_rest=True)
        result = run_ingest(conn, path, IngestOptions(first=first, **SMALL), events.append)
        assert result.deferred and result.partial_left == 2 and result.first_sources == 2
        assert {d.action for d in result.details} == {ACTION_WINDOW}
        stored = [r[0] for r in conn.execute("SELECT ts FROM entry")]
        assert stored and all(ms(120_000) <= ts < ms(180_000) for ts in stored)
        assert len(stored) == 60 + 60  # 60 records per source in the window
        assert [e.phase for e in events][-2:] == ["first_ready", "done"]
        assert {e.stage for e in events} == {STAGE_FIRST, STAGE_REST}
        assert conn.execute("SELECT DISTINCT status FROM source").fetchall() == [("indexing",)]
        pending = pending_work(conn)
        assert [(p.partial, p.pending, p.scope) for p in pending] == [(2, 0, "default")]
        assert_integrity(conn)
        assert_ids_follow_chains(conn)
        # the rest
        [done] = resume_pending(conn, IngestOptions(**SMALL))
        assert {d.action for d in done.details} == {ACTION_COMPLETE}
        assert pending_work(conn) == []
        assert_same(content(conn), full_content(tmp_path, path))
        assert_integrity(conn)
        assert_ids_follow_chains(conn)
        assert unchanged_again(conn, path)
        assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'partial%'").fetchone()[0] == 0
    finally:
        conn.close()


def test_one_run_does_both_passes_and_reports_first_ready_before_the_rest(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(200)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        seen = []

        def progress(event):
            n = conn.execute("SELECT count(*) FROM entry").fetchone()[0]
            seen.append((event.phase, event.stage, n))

        result = run_ingest(
            conn, path, IngestOptions(first=FirstPass(ts_from=ms(100_000), ts_to=ms(110_000))), progress
        )
        ready = [n for phase, _stage, n in seen if phase == "first_ready"]
        assert ready == [10]  # only the window was there when the first pass was announced
        assert result.first_records == 10 and result.records == 200 and not result.deferred
        assert result.partial_left == 0 and result.first_seconds > 0
        assert_same(content(conn), full_content(tmp_path, path))
    finally:
        conn.close()


def test_families_window_in_the_middle_equals_full_indexing(tmp_path):
    """Every family (operations, payloads, normal<->sensitive pairs, statistics, unknown records)."""
    path = tmp_path / "Log"
    shutil.copytree(FAMILIES, path)
    reference = full_content(tmp_path, path)
    times = sorted(r[5] for r in reference["entries"] if r[5] is not None)
    lo, hi = times[len(times) // 3], times[2 * len(times) // 3]
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(conn, path, IngestOptions(first=FirstPass(ts_from=lo, ts_to=hi, defer_rest=True), **SMALL))
        assert 0 < conn.execute("SELECT count(*) FROM entry").fetchone()[0] < len(reference["entries"])
        assert conn.execute("SELECT count(*) FROM event").fetchone()[0] > 0  # the first pass is derived
        resume_pending(conn, IngestOptions(**SMALL))
        assert_same(content(conn), reference)
        assert_ids_follow_chains(conn)
        assert unchanged_again(conn, path)
    finally:
        conn.close()


def test_rotation_chain_window_across_both_files_keeps_ids_in_chain_order(tmp_path):
    old = make_log(100)
    new = make_log(100, start_ms=100_000)
    path = write_folder(tmp_path / "Log", {WFM0: old, WFM: new})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(ts_from=ms(80_000), ts_to=ms(130_000), defer_rest=True)
        run_ingest(conn, path, IngestOptions(first=first, **SMALL))
        assert_ids_follow_chains(conn)
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] == 50
        resume_pending(conn, IngestOptions(**SMALL))
        assert_same(content(conn), full_content(tmp_path, path))
        assert_ids_follow_chains(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- sources first


def test_sources_first_brings_their_rotation_chain_and_leaves_the_rest_pending(tmp_path):
    ont = f"{LOGDIR}/OntAdapter_2222.log"
    path = write_folder(
        tmp_path / "Log", {WFM0: make_log(50), WFM: make_log(50, 50_000), ont: make_log(40, pid=2222)}
    )
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(members=frozenset({WFM, "nope.log"}), defer_rest=True)
        result = run_ingest(conn, path, IngestOptions(first=first))
        assert result.first_unmatched == ["nope.log"] and result.first_sources == 2
        status = dict(conn.execute("SELECT member, status FROM source"))
        assert status == {WFM0: "ready", WFM: "ready", ont: "pending"}
        assert [(p.partial, p.pending) for p in pending_work(conn)] == [(0, 1)]
        resume_pending(conn)
        assert_same(content(conn), full_content(tmp_path, path))
        assert pending_work(conn) == []
    finally:
        conn.close()


def test_sources_and_window_together(tmp_path):
    ont = f"{LOGDIR}/OntAdapter_2222.log"
    path = write_folder(tmp_path / "Log", {WFM: make_log(100), ont: make_log(100, pid=2222)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(members=frozenset({ont}), ts_from=ms(10_000), ts_to=ms(20_000), defer_rest=True)
        run_ingest(conn, path, IngestOptions(first=first))
        per_member = dict(
            conn.execute(
                "SELECT s.member, count(e.id) FROM source s"
                " LEFT JOIN entry e ON e.source_id = s.id GROUP BY 1"
            )
        )
        assert per_member == {WFM: 0, ont: 10}
        run_ingest(conn, path)  # any later run of the same path completes it
        assert_same(content(conn), full_content(tmp_path, path))
    finally:
        conn.close()


# --------------------------------------------------------------------------- interruptions


@pytest.mark.parametrize("container", ["folder", "zip"])
@pytest.mark.parametrize("fail_at_chunk", [3, 9, 15, 24, 33, 47, 60])
def test_interrupted_anywhere_resumes_to_the_full_result(tmp_path, monkeypatch, container, fail_at_chunk):
    files = {WFM0: make_log(120), WFM: make_log(120, start_ms=120_000)}
    path = (
        write_folder(tmp_path / "Log", files) if container == "folder" else write_zip(tmp_path / "z", files)
    )
    options = IngestOptions(first=FirstPass(ts_from=ms(90_000), ts_to=ms(150_000)), **SMALL)
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        with monkeypatch.context() as patch:
            flaky_reader(patch, fail_at_chunk)
            try:
                run_ingest(conn, path, options)
            except Interrupted:
                pass
        assert_integrity(conn)
        assert_ids_follow_chains(conn)
        run_ingest(conn, path, IngestOptions(**SMALL))  # reopening the same path: no first pass needed
        assert_same(content(conn), full_content(tmp_path, path))
        assert_ids_follow_chains(conn)
        assert unchanged_again(conn, path)
    finally:
        conn.close()


def test_reopening_with_a_first_pass_after_completion_writes_nothing(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        options = IngestOptions(first=FirstPass(ts_from=ms(10_000), ts_to=ms(20_000)))
        run_ingest(conn, path, options)
        before = conn.total_changes
        result = run_ingest(conn, path, options)
        assert conn.total_changes == before
        assert result.records == 0 and result.partial_left == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- the source changes in between


def test_live_file_that_grew_between_the_passes_is_completed_then_appended(tmp_path):
    root = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(
            conn, root, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000), defer_rest=True))
        )
        (root / WFM).write_bytes(make_log(130))  # the same 100 records and 30 more
        run_ingest(conn, root, IngestOptions(**SMALL))
        assert_same(content(conn), full_content(tmp_path, root))
        assert unchanged_again(conn, root)
    finally:
        conn.close()


def test_file_rewritten_between_the_passes_is_indexed_again_from_scratch(tmp_path):
    root = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(
            conn, root, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000), defer_rest=True))
        )
        (root / WFM).write_bytes(make_log(100, start_ms=7))  # same size, other content
        run_ingest(conn, root)
        assert_same(content(conn), full_content(tmp_path, root))
        assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'partial.%'").fetchone()[0] == 0
    finally:
        conn.close()


def test_records_that_do_not_fit_the_reserved_ids_fall_back_to_a_full_reindex(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(
            conn, path, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000), defer_rest=True))
        )
        sid = conn.execute("SELECT id FROM source").fetchone()[0]
        state = load_state(conn, sid)
        assert state is not None and state.segment == partial.SEG_PREFIX
        shrunk = PartialState(**{**state.__dict__, "win": state.base + 3})  # the prefix cannot fit any more
        conn.execute("UPDATE meta SET value = ? WHERE key = ?", (shrunk.to_json(), partial.state_key(sid)))
        run_ingest(conn, path)
        assert_same(content(conn), full_content(tmp_path, path))
    finally:
        conn.close()


# --------------------------------------------------------------------------- user data and collections


def test_bookmark_on_a_window_record_keeps_its_record_through_the_completion(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(
            conn, path, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000), defer_rest=True))
        )
        eid, line = conn.execute("SELECT id, line_no FROM entry ORDER BY id LIMIT 1 OFFSET 3").fetchone()
        last_eid = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
        mark = bookmarks.create_bookmark(conn, eid, note="window")
        last_mark = bookmarks.create_bookmark(conn, last_eid, note="last of the window")
        resume_pending(conn, IngestOptions(**SMALL))
        rows = dict(
            conn.execute(
                "SELECT b.id, e.line_no FROM bookmark b JOIN entry e ON e.id = b.entry_id"
                " WHERE b.id IN (?, ?)",
                (mark["id"], last_mark["id"]),
            )
        )
        assert rows[mark["id"]] == line
        assert last_mark["id"] in rows
        fps = dict(conn.execute("SELECT id, fp FROM bookmark"))
        assert all(fp.startswith("f:") for fp in fps.values())  # the window's last record got a later one
    finally:
        conn.close()


def test_removing_a_collection_with_a_partial_source_removes_its_state(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(
            conn, path, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000), defer_rest=True))
        )
        group = conn.execute("SELECT ers_group FROM collection").fetchone()[0]
        assert IndexWriter(conn).remove_group(group) is not None
        assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'partial.%'").fetchone()[0] == 0
        assert pending_work(conn) == []
        run_ingest(conn, path)
        assert_same(content(conn), full_content(tmp_path, path))
    finally:
        conn.close()


# --------------------------------------------------------------------------- preview


@pytest.mark.parametrize("container", ["folder", "zip"])
def test_preview_lists_sources_with_their_time_range_without_writing(tmp_path, container):
    ont = f"{LOGDIR}/OntAdapter_2222.log"
    files = {WFM: make_log(5000), ont: make_log(10, start_ms=7_000, pid=2222), f"{LOGDIR}/notes.txt": b"x\n"}
    path = (
        write_folder(tmp_path / "Log", files) if container == "folder" else write_zip(tmp_path / "z", files)
    )
    rows = {p.name: p for p in preview_sources(path)}
    wfm = rows["WfmAdapter_1111.log"]
    assert (wfm.family, wfm.pid, wfm.sensitive, wfm.selected, wfm.indexable) == (
        "AWFM",
        1111,
        False,
        True,
        True,
    )
    assert wfm.first_ts == ms(0) and rows["OntAdapter_2222.log"].first_ts == ms(7_000)
    if container == "folder":
        assert wfm.last_ts == ms(4_999_000) and rows["OntAdapter_2222.log"].last_ts == ms(16_000)
    else:
        assert wfm.last_ts is None  # a zip member is not read to its end
    assert wfm.size == len(files[WFM])
    assert not list(tmp_path.rglob("*.sqlite"))
    with pytest.raises(ValueError):
        preview_sources(path, scope="x")


@pytest.mark.parametrize("container", ["folder", "zip"])
def test_utf16_source_window_first_equals_full_indexing(tmp_path, container):
    data = b"\xff\xfe" + make_log(150).decode("utf-8").encode("utf-16-le")
    path = (
        write_folder(tmp_path / "Log", {WFM: data})
        if container == "folder"
        else write_zip(tmp_path / "z", {WFM: data})
    )
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(ts_from=ms(60_000), ts_to=ms(90_000), defer_rest=True)
        run_ingest(conn, path, IngestOptions(first=first, **SMALL))
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] == 30
        run_ingest(conn, path, IngestOptions(**SMALL))
        assert_same(content(conn), full_content(tmp_path, path))
        assert unchanged_again(conn, path)
    finally:
        conn.close()


# --------------------------------------------------------------------------- review of PR #193 (regressions)


def test_locating_the_window_keeps_memory_bounded_by_the_chunk(tmp_path):
    import tracemalloc

    data = make_log(400_000)  # ~17 MiB
    path = write_folder(tmp_path / "Log", {WFM: data})
    info = info_of(path, WFM)
    with SourceReader() as reader:
        tracemalloc.start()
        try:
            span = locate_window(reader, info, ms(10_000), ms(20_000), 1 << 20)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert span.size == len(data)
    assert peak < 8 * (1 << 20), peak  # a few chunks, not the whole source


def test_interrupted_at_first_ready_of_a_sources_first_open_is_resumable(tmp_path):
    ont = f"{LOGDIR}/OntAdapter_2222.log"
    path = write_folder(tmp_path / "Log", {WFM: make_log(50), ont: make_log(40, pid=2222)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:

        def stop_at_first_ready(event):
            if event.phase == "first_ready":
                raise Interrupted

        with pytest.raises(Interrupted):
            run_ingest(
                conn, path, IngestOptions(first=FirstPass(members=frozenset({WFM}))), stop_at_first_ready
            )
        assert dict(conn.execute("SELECT member, status FROM source")) == {WFM: "ready", ont: "pending"}
        assert [(p.partial, p.pending) for p in pending_work(conn)] == [(0, 1)]
        resume_pending(conn)
        assert_same(content(conn), full_content(tmp_path, path))
        assert pending_work(conn) == []
    finally:
        conn.close()


@pytest.mark.parametrize("where", ["window", "prefix_done"])
def test_change_inside_indexed_content_beyond_the_head_is_detected(tmp_path, monkeypatch, where):
    root = write_folder(tmp_path / "Log", {WFM: make_log(300)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(ts_from=ms(200_000), ts_to=ms(250_000), defer_rest=True)
        run_ingest(conn, root, IngestOptions(first=first, **SMALL))
        if where == "prefix_done":  # interrupt the completion in the middle of the prefix
            with monkeypatch.context() as patch:
                flaky_reader(patch, 30)
                with pytest.raises(Interrupted):
                    run_ingest(conn, root, IngestOptions(**SMALL))
            target = b"item 80 done"  # inside the part of the prefix already stored
            sid = conn.execute("SELECT id FROM source").fetchone()[0]
            assert load_state(conn, sid).segment == partial.SEG_PREFIX
            assert conn.execute("SELECT 1 FROM entry WHERE msg = '[Alpha] item 90 done'").fetchone()
        else:
            target = b"item 220 done"  # inside the window, far beyond the first 4 KiB
        data = (root / WFM).read_bytes()
        assert data.index(target) > 4096
        (root / WFM).write_bytes(data.replace(target, target.upper(), 1))  # same size
        run_ingest(conn, root, IngestOptions(**SMALL))
        assert_same(content(conn), full_content(tmp_path, root))
        assert unchanged_again(conn, root)
    finally:
        conn.close()


def test_unknown_example_follows_the_order_of_the_collections(tmp_path):
    other = f"{LOGDIR}/WfmAdapter_3333.log"
    beta = mf.render([mf.header(i * 1000, "INFO", f"[Beta] thing {i} seen", pid=3333) for i in range(5)])
    a = write_folder(tmp_path / "A", {WFM: make_log(20), other: beta})
    b_beta = mf.render([mf.header(i * 1000, "INFO", f"[Beta] thing {i} seen", pid=4444) for i in range(5)])
    b = write_folder(tmp_path / "B", {f"{LOGDIR}/WfmAdapter_4444.log": b_beta})
    ref = create_case_db(tmp_path / "ref" / "case.sqlite")
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        run_ingest(ref, a)
        run_ingest(ref, b)
        run_ingest(conn, a, IngestOptions(first=FirstPass(members=frozenset({WFM}), defer_rest=True)))
        run_ingest(conn, b)
        resume_pending(conn)
        assert_same(content(conn), content(ref))
    finally:
        conn.close()
        ref.close()


def test_reservation_beyond_int64_falls_back_without_touching_the_case(tmp_path):
    path = write_folder(tmp_path / "Log", {WFM: make_log(100)})
    top = 2**63 - 1
    floor = top - 110  # 100 records fit, the 125 lines of the file do not
    ref = create_case_db(tmp_path / "ref" / "case.sqlite")
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        for c in (ref, conn):
            c.execute("INSERT INTO meta(key, value) VALUES ('id_floor.entry', ?)", (str(floor),))
        run_ingest(ref, path)
        run_ingest(conn, path, IngestOptions(first=FirstPass(ts_from=ms(30_000), ts_to=ms(40_000))))
        assert_same(content(conn), content(ref))
        stored = int(conn.execute("SELECT value FROM meta WHERE key = 'id_floor.entry'").fetchone()[0])
        assert stored <= top
        assert conn.execute("SELECT max(id) FROM entry").fetchone()[0] <= top
    finally:
        conn.close()
        ref.close()


def test_changed_older_file_of_a_chain_keeps_the_chain_in_id_order(tmp_path):
    root = write_folder(tmp_path / "Log", {WFM0: make_log(100), WFM: make_log(100, start_ms=100_000)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        first = FirstPass(ts_from=ms(80_000), ts_to=ms(130_000), defer_rest=True)
        run_ingest(conn, root, IngestOptions(first=first, **SMALL))
        data = (root / WFM0).read_bytes()
        (root / WFM0).write_bytes(data.replace(b"item 0 done", b"ITEM 0 DONE", 1))  # new head, same size
        run_ingest(conn, root, IngestOptions(**SMALL))
        assert_ids_follow_chains(conn)
        assert_same(content(conn), full_content(tmp_path, root))
    finally:
        conn.close()


def test_reopening_a_complete_sources_first_selection_writes_nothing(tmp_path):
    ont = f"{LOGDIR}/WfmAdapter_2222.log"  # same family, after WFM in the order
    path = write_folder(tmp_path / "Log", {WFM: make_log(50), ont: make_log(40, pid=2222)})
    conn = create_case_db(tmp_path / "p" / "case.sqlite")
    try:
        options = IngestOptions(first=FirstPass(members=frozenset({ont})))
        run_ingest(conn, path, options)
        assert unchanged_again(conn, path, first=FirstPass(members=frozenset({ont})))
    finally:
        conn.close()
