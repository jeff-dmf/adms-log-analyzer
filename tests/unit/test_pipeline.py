"""Tests for dmslog.ingest.pipeline: counts, idempotent re-ingestion, resume, growth, rotation, limits."""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

from dmslog.ingest.ers import plan_collection, select_sources
from dmslog.ingest.pipeline import (
    IngestOptions,
    ProgressEvent,
    case_db_path,
    default_case_name,
    generic_severity,
    run_ingest,
    validate_case_name,
)
from dmslog.ingest.reader import ReadLimits, SourceReader
from dmslog.parse.envelope import detect_envelope
from dmslog.parse.records import parse_bytes
from dmslog.store import create_case_db

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)

FIXTURES = ROOT / "tests" / "fixtures" / "logs"
GUID_A = mf.synth_guid(1)
GUID_B = mf.synth_guid(2)
LOGDIR = "dnaApp_zone1/SVC"
WFM = f"{LOGDIR}/WfmAdapter_1111.log"
WFM_ROTATED = f"{LOGDIR}/WfmAdapter_1111.log.0"


class Interrupted(BaseException):
    """Simulates Ctrl+C / a crash in the middle of a batch."""


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def fresh_db(tmp_path: Path, name: str):
    return create_case_db(tmp_path / name / "case.sqlite")


def lines_of(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def make_log(n: int, start: int = 0, pid: int = 1111, cont_every: int = 4) -> bytes:
    lines = []
    for i in range(n):
        lines.append(
            mf.header(start + i * 10, "INFO" if i % 7 else "ERROR", f"[Alpha] item {start + i} done", pid=pid)
        )
        if i % cont_every == 0:
            lines.append(f"  continuation of item {start + i}")
    return mf.render(lines)


def write_folder(root: Path, files: dict[str, bytes]) -> Path:
    for member, data in files.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def write_ers(
    dest: Path, files: dict[str, bytes], *, kind="enterprise", guid=GUID_A, stamp="20300115103000"
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / mf.ers_file_name(kind, guid, stamp, "t")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{mf.LOG_ROOT}/{name}", data)
    return path


def changed_actions(result) -> dict[str, str]:
    return {d.member: d.action for d in result.details if d.action != "unchanged"}


def snapshot(conn) -> dict:
    """Everything that must be identical between two routes to the same content (ids excluded)."""
    entries = conn.execute(
        "SELECT s.member, e.line_no, e.byte_off, e.nlines, e.ts, e.pid, e.tid, e.level, e.sev, e.flags,"
        " e.msg,"
        " b.cont FROM entry e JOIN source s ON s.id = e.source_id LEFT JOIN entry_body b ON b.entry_id = e.id"
        " ORDER BY s.member, e.byte_off"
    ).fetchall()
    buckets = conn.execute(
        "SELECT s.member, k.minute, k.sev, k.tpl_id, k.n FROM bucket k JOIN source s ON s.id = k.source_id"
        " ORDER BY 1, 2, 3, 4"
    ).fetchall()
    sources = conn.execute(
        "SELECT member, status, error, n_entries, indexed_bytes, size, head_hash, tail_hash, envelope,"
        " first_ts,"
        " last_ts, last_line FROM source ORDER BY member"
    ).fetchall()
    fts = conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0]
    return {"entries": entries, "buckets": buckets, "sources": sources, "fts": fts}


def assert_last_line_matches_records(conn) -> None:
    """``source.last_line`` (schema 7) is the last line of the source's highest-id record (NULL: none)."""
    rows = conn.execute(
        "SELECT s.id, s.last_line, (SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id"
        " ORDER BY e.id DESC LIMIT 1) FROM source s"
    ).fetchall()
    assert [(sid, stored) for sid, stored, _ in rows] == [(sid, ref) for sid, _, ref in rows]


def assert_consistent(conn) -> None:
    assert_last_line_matches_records(conn)
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
    n_entries = conn.execute("SELECT count(*) FROM entry").fetchone()[0]
    assert conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0] == n_entries
    assert (
        conn.execute(
            "SELECT count(*) FROM entry_body WHERE entry_id NOT IN (SELECT id FROM entry)"
        ).fetchone()[0]
        == 0
    )
    per_source = conn.execute("SELECT id, n_entries FROM source WHERE status='ready'").fetchall()
    for sid, n in per_source:
        assert conn.execute("SELECT count(*) FROM entry WHERE source_id=?", (sid,)).fetchone()[0] == n
        buckets = conn.execute("SELECT coalesce(sum(n), 0) FROM bucket WHERE source_id=?", (sid,)).fetchone()[
            0
        ]
        with_ts = conn.execute("SELECT count(ts) FROM entry WHERE source_id=?", (sid,)).fetchone()[0]
        assert buckets == with_ts


def clean_snapshot(tmp_path: Path, path: Path, name: str, **options) -> dict:
    conn = fresh_db(tmp_path, name)
    try:
        run_ingest(conn, path, IngestOptions(**options))
        return snapshot(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- counts


def test_ingest_zip_counts_match_independent_parse(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z")
    plan = plan_collection(zpath)
    selected = select_sources(plan.sources)
    result = run_ingest(db, zpath)
    assert result.sources_selected == len(selected) > 0
    assert result.sources_indexed == len(selected)
    assert result.sources_listed == len(plan.sources)
    total_lines = 0
    with zipfile.ZipFile(zpath) as zf:
        for info in selected:
            data = zf.read(info.member)
            expected = parse_bytes(data, detect_envelope(data[:65536]), final=True)
            row = db.execute(
                "SELECT n_entries, status, indexed_bytes, size FROM source WHERE member=?", (info.member,)
            ).fetchone()
            assert row[1] == "ready"
            assert row[0] == len(expected)
            assert row[2] == row[3] == len(data)
            phys = db.execute(
                "SELECT coalesce(sum(nlines), 0) FROM entry e JOIN source s ON s.id = e.source_id"
                " WHERE s.member=?",
                (info.member,),
            ).fetchone()[0]
            assert phys == lines_of(data)  # nlines adds up to the physical lines, exactly
            total_lines += phys
    assert result.lines == total_lines
    assert result.records == db.execute("SELECT count(*) FROM entry").fetchone()[0]
    assert_consistent(db)


def test_collection_stream_and_source_rows(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z", desc="mycase")
    run_ingest(db, zpath)
    coll = db.execute(
        "SELECT kind, ers_guid, ers_ts, ers_variant, ers_group, id, label, system FROM collection"
    ).fetchall()
    assert len(coll) == 1
    kind, guid, ts, variant, group, cid, label, system = coll[0]
    assert (kind, guid, ts, variant, label, system) == (
        "ers_zip",
        GUID_A.upper(),
        "20300115103000",
        "enterprise",
        "mycase",
        "mpdmz",
    )
    assert group == cid
    streams = db.execute(
        "SELECT family, process, pid, sensitive, collection_group FROM stream ORDER BY family, pid"
    ).fetchall()
    assert ("AWFM", "WfmAdapter", 1111, 0, group) in streams
    rows = dict(db.execute("SELECT member, status FROM source").fetchall())
    skipped = {
        m: db.execute("SELECT error FROM source WHERE member=?", (m,)).fetchone()[0]
        for m, s in rows.items()
        if s == "skipped"
    }
    assert set(skipped.values()) <= {"out_of_scope", "not_indexable"}
    assert any(m.endswith("NMC.log") and e == "out_of_scope" for m, e in skipped.items())
    assert any(m.endswith(".evtx") and e == "not_indexable" for m, e in skipped.items())
    rotated = db.execute(
        "SELECT s.rotation, s.stream_id = t.id FROM source s JOIN stream t ON t.pid = 1111"
        " WHERE s.member LIKE '%.log.0'"
    ).fetchall()
    assert rotated == [(0, 1)]
    assert dict(db.execute("SELECT key, value FROM meta").fetchall())["name"] == default_case_name(
        plan_collection(zpath)
    )


def test_all_logs_scope_indexes_the_rest_without_touching_the_first_pass(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z")
    first = run_ingest(db, zpath)
    before = snapshot(db)
    second = run_ingest(db, zpath, IngestOptions(scope="all"))
    assert second.sources_selected > first.sources_selected
    assert second.sources_unchanged == first.sources_selected
    after = snapshot(db)
    kept = {e[:3] for e in before["entries"]}
    assert kept <= {e[:3] for e in after["entries"]}
    nmc = db.execute("SELECT status, error FROM source WHERE member LIKE '%/NMC.log'").fetchone()
    assert nmc == ("ready", None)
    assert_consistent(db)


def test_script_literal_stays_intact(tmp_path, db):
    data = (FIXTURES / "script_literal.log").read_bytes()
    zpath = write_ers(tmp_path / "z", {f"{LOGDIR}/WfmAdapter_1111.log": data})
    run_ingest(db, zpath)
    msgs = [r[0] for r in db.execute("SELECT msg FROM entry ORDER BY id")]
    assert "User text: <script>alert(1)</script> end." in msgs
    cont = db.execute("SELECT cont FROM entry_body").fetchone()[0]
    assert cont == '<a href="x" onclick="alert(1)"><script>alert(1)</script></a>'


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*.log")))
def test_every_fixture_indexes_with_exact_line_count(tmp_path, name):
    data = (FIXTURES / name).read_bytes()
    zpath = write_ers(tmp_path / "z", {f"{LOGDIR}/WfmAdapter_1111.log": data})
    conn = fresh_db(tmp_path, "c")
    try:
        run_ingest(conn, zpath)
        total = conn.execute("SELECT coalesce(sum(nlines), 0), count(*) FROM entry").fetchone()
        assert total[0] == lines_of(data)
        assert_consistent(conn)
    finally:
        conn.close()


def test_batch_size_does_not_change_the_result(tmp_path):
    data = make_log(400)
    zpath = write_ers(tmp_path / "z", {WFM: data})
    base = clean_snapshot(tmp_path, zpath, "a")
    small = clean_snapshot(tmp_path, zpath, "b", batch_bytes=1500, chunk_size=700)
    assert small["entries"] == base["entries"]
    assert small["buckets"] == base["buckets"]


def test_severity_inference_is_provisional_and_simple():
    assert generic_severity(1, "x") == 1
    assert generic_severity(2, "x") == 1
    assert generic_severity(3, "plain") == 2
    assert generic_severity(4, "(ERROR) boom") == 2
    assert generic_severity(7, "Non Fatal Exception System.X: y") == 2
    assert generic_severity(7, "request timed out") == 3
    assert generic_severity(6, "x") == 3
    assert generic_severity(7, "all good") == 4
    assert generic_severity(None, "orphan") == 4


def test_progress_events(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z")
    events: list[ProgressEvent] = []
    result = run_ingest(db, zpath, progress=events.append)
    assert events[-1].phase == "done"
    assert events[-1].sources_done == events[-1].sources_total == result.sources_selected
    assert [e.bytes_done for e in events] == sorted(e.bytes_done for e in events)
    assert events[-1].bytes_done == events[-1].bytes_total
    done = [e for e in events if e.phase == "source_done"]
    assert sum(e.source_records for e in done) == result.records


# --------------------------------------------------------------------------- idempotence


def test_reingesting_the_same_zip_writes_nothing(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z")
    first = run_ingest(db, zpath)
    before = snapshot(db)
    changes = db.total_changes
    second = run_ingest(db, zpath)
    assert db.total_changes == changes  # not a single row inserted, updated or deleted
    assert snapshot(db) == before
    assert second.sources_unchanged == first.sources_selected
    assert (second.sources_indexed, second.records, second.bytes) == (0, 0, 0)


def test_reingesting_a_moved_copy_of_the_zip_only_updates_the_path(tmp_path, db):
    zpath = mf.build_ers_zip(tmp_path / "z")
    run_ingest(db, zpath)
    moved = tmp_path / "elsewhere" / zpath.name
    moved.parent.mkdir()
    moved.write_bytes(zpath.read_bytes())
    before = snapshot(db)
    result = run_ingest(db, moved)
    assert result.records == 0
    assert snapshot(db) == before
    assert db.execute("SELECT count(*) FROM collection").fetchone()[0] == 1
    assert db.execute("SELECT path FROM collection").fetchone()[0] == str(moved.resolve())


def test_reingesting_a_folder_writes_nothing(tmp_path, db):
    root = write_folder(tmp_path / "Log", {WFM: make_log(50), WFM_ROTATED: make_log(30, start=9000)})
    run_ingest(db, root)
    before = snapshot(db)
    changes = db.total_changes
    result = run_ingest(db, root)
    assert db.total_changes == changes
    assert snapshot(db) == before
    assert result.sources_unchanged == 2


def test_zip_member_with_different_crc_is_reindexed(tmp_path, db):
    first = write_ers(tmp_path / "z1", {WFM: make_log(40)})
    run_ingest(db, first)
    second = write_ers(tmp_path / "z2", {WFM: make_log(60, start=5)})  # same capture name, other content
    result = run_ingest(db, second)
    assert [d.action for d in result.details if d.member.endswith("WfmAdapter_1111.log")] == ["reindex"]
    assert snapshot(db) == clean_snapshot(tmp_path, second, "clean")
    assert_consistent(db)


# --------------------------------------------------------------------------- interruption


def flaky_reader(monkeypatch, fail_at_chunk: int) -> None:
    original = SourceReader.iter_chunks

    def flaky(self, info, start=0, chunk_size=1 << 20):
        for i, chunk in enumerate(original(self, info, start, chunk_size)):
            if i == fail_at_chunk:
                raise Interrupted
            yield chunk

    monkeypatch.setattr(SourceReader, "iter_chunks", flaky)


@pytest.mark.parametrize("container", ["zip", "folder"])
@pytest.mark.parametrize("fail_at_chunk", [5, 6, 7, 12])
def test_resume_after_interruption_in_the_middle_of_a_batch(tmp_path, monkeypatch, container, fail_at_chunk):
    data = make_log(300)
    if container == "zip":
        path = write_ers(tmp_path / "z", {WFM: data})
    else:
        path = write_folder(tmp_path / "Log", {WFM: data})
    options = IngestOptions(chunk_size=1000, batch_bytes=2500)  # a commit every three chunks
    conn = fresh_db(tmp_path, "interrupted")
    try:
        with monkeypatch.context() as patch:
            flaky_reader(patch, fail_at_chunk)
            with pytest.raises(Interrupted):
                run_ingest(conn, path, options)
        state = conn.execute(
            "SELECT status, indexed_bytes, n_entries FROM source WHERE member LIKE '%Wfm%'"
        ).fetchone()
        assert state[0] == "indexing"
        assert state[1] < len(data)
        assert_consistent_partial(conn)
        result = run_ingest(conn, path, options)
        assert [d.action for d in result.details] == ["resume"]
        assert snapshot(conn) == clean_snapshot(tmp_path, path, "clean")
        assert_consistent(conn)
        again = db_changes(conn, lambda: run_ingest(conn, path, options))
        assert again == 0
    finally:
        conn.close()


def db_changes(conn, action) -> int:
    before = conn.total_changes
    action()
    return conn.total_changes - before


def assert_consistent_partial(conn) -> None:
    """After a rollback the committed rows are still coherent (FTS, buckets and last lines match entries)."""
    assert_last_line_matches_records(conn)
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
    assert (
        conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0]
        == conn.execute("SELECT count(*) FROM entry").fetchone()[0]
    )
    assert (
        conn.execute("SELECT coalesce(sum(n), 0) FROM bucket").fetchone()[0]
        == conn.execute("SELECT count(ts) FROM entry").fetchone()[0]
    )


def test_interruption_before_the_first_commit_leaves_nothing_to_resume(tmp_path, monkeypatch):
    path = write_ers(tmp_path / "z", {WFM: make_log(100)})
    conn = fresh_db(tmp_path, "c")
    try:
        with monkeypatch.context() as patch:
            flaky_reader(patch, 0)
            with pytest.raises(Interrupted):
                run_ingest(conn, path, IngestOptions(chunk_size=500))
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] == 0
        run_ingest(conn, path)
        assert snapshot(conn) == clean_snapshot(tmp_path, path, "clean")
    finally:
        conn.close()


def test_unreadable_source_is_marked_as_error_and_the_run_continues(tmp_path, db):
    root = write_folder(
        tmp_path / "Log", {WFM: make_log(200), f"{LOGDIR}/OntAdapter_2222.log": make_log(20, pid=2222)}
    )
    result = run_ingest(db, root, IngestOptions(limits=ReadLimits(max_total_bytes=3000)))
    assert result.sources_failed >= 1
    states = dict(db.execute("SELECT member, status FROM source").fetchall())
    assert "error" in states.values()
    assert (
        db.execute("SELECT error FROM source WHERE status='error'").fetchone()[0].startswith("ReadLimitError")
    )
    assert_consistent_partial(db)
    again = run_ingest(db, root)  # a failed source is indexed again from scratch
    assert again.sources_failed == 0
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")


def test_record_too_large_marks_the_source_as_error_and_the_run_continues(tmp_path, db):
    good = make_log(300)
    huge = make_log(200, pid=2222) + mf.render(
        [mf.header(5000, "INFO", "dump")] + [f"row {i}" for i in range(400)]
    )
    root = write_folder(tmp_path / "Log", {WFM: good, f"{LOGDIR}/OntAdapter_2222.log": huge})
    opts = IngestOptions(chunk_size=1000, batch_bytes=4000, max_record_bytes=3000)
    result = run_ingest(db, root, opts)
    assert result.sources_failed == 1
    assert [d.error for d in result.details if d.action == "error"][0].startswith("record_too_large")
    rows = dict(db.execute("SELECT member, status FROM source").fetchall())
    assert rows[WFM] == "ready" and rows[f"{LOGDIR}/OntAdapter_2222.log"] == "error"
    assert (
        db.execute("SELECT error FROM source WHERE status='error'")
        .fetchone()[0]
        .startswith("record_too_large")
    )
    assert_consistent_partial(db)
    # Records committed before the oversized one stay complete and queryable.
    sid = db.execute("SELECT id FROM source WHERE status='error'").fetchone()[0]
    kept = db.execute("SELECT count(*) FROM entry WHERE source_id=?", (sid,)).fetchone()[0]
    assert 0 < kept < 200 + 1
    assert (
        db.execute("SELECT count(*) FROM entry WHERE source_id=? AND msg='dump'", (sid,)).fetchone()[0] == 0
    )
    # Ingesting again with the same limit repeats the failure without duplicating anything.
    before = db.execute("SELECT count(*) FROM entry").fetchone()[0]
    again = run_ingest(db, root, opts)
    assert again.sources_failed == 1
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == before
    assert_consistent_partial(db)
    # With the default limit the same content indexes normally.
    final = run_ingest(db, root)
    assert final.sources_failed == 0
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")


# --------------------------------------------------------------------------- growth, rotation, truncation


def test_grown_file_only_rewrites_its_tail(tmp_path, db):
    base = make_log(200)
    root = write_folder(tmp_path / "Log", {WFM: base})
    run_ingest(db, root)
    ids_before = [r[0] for r in db.execute("SELECT id FROM entry ORDER BY id")]
    grown = base + b"  late continuation of the last record\n" + make_log(80, start=100_000)
    (root / WFM).write_bytes(grown)
    result = run_ingest(db, root)
    assert [d.action for d in result.details] == ["append"]
    assert result.records < len(ids_before) // 2 + 80
    ids_after = [r[0] for r in db.execute("SELECT id FROM entry ORDER BY id")]
    assert ids_after[: len(ids_before) - 1] == ids_before[:-1]  # everything but the last record was kept
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")
    assert_consistent(db)
    grown_again = grown + make_log(10, start=900_000)
    (root / WFM).write_bytes(grown_again)
    assert [d.action for d in run_ingest(db, root).details] == ["append"]
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean2")


def test_a_growing_log_keeps_its_partial_last_record_correct(tmp_path, db):
    full = (FIXTURES / "truncated.log").read_bytes()
    cut = full.rfind(b"second continuation")  # inside the last record, which has no final newline
    root = write_folder(tmp_path / "Log", {WFM: full[: cut + 5]})
    run_ingest(db, root)
    assert db.execute("SELECT flags & 1 FROM entry ORDER BY id DESC LIMIT 1").fetchone()[0] == 1
    (root / WFM).write_bytes(full + b"\n" + mf.header(500, "INFO", "Next.").encode() + b"\n")
    result = run_ingest(db, root)
    assert [d.action for d in result.details] == ["append"]
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")
    last_two = db.execute("SELECT flags & 1 FROM entry ORDER BY id DESC LIMIT 2").fetchall()
    assert last_two == [(0,), (0,)]


def test_rotation_moves_the_source_and_indexes_the_new_file_from_scratch(tmp_path, db):
    old = make_log(120)
    root = write_folder(tmp_path / "Log", {WFM: old})
    run_ingest(db, root)
    old_id = db.execute("SELECT id FROM source").fetchone()[0]
    ids_before = [r[0] for r in db.execute("SELECT id FROM entry ORDER BY id")]
    (root / WFM).rename(root / WFM_ROTATED)  # the adapter rotates, then starts a new .log
    new = make_log(50, start=50_000)
    (root / WFM).write_bytes(new)
    result = run_ingest(db, root)
    assert changed_actions(result) == {WFM: "new"} and result.sources_unchanged == 1
    moved = db.execute("SELECT id, rotation, file_name FROM source WHERE member=?", (WFM_ROTATED,)).fetchone()
    assert moved == (old_id, 0, "WfmAdapter_1111.log.0")  # same source, its records were kept
    assert [
        r[0] for r in db.execute("SELECT id FROM entry WHERE source_id=? ORDER BY id", (old_id,))
    ] == ids_before
    assert db.execute("SELECT count(DISTINCT stream_id) FROM source").fetchone()[0] == 1
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")
    assert_consistent(db)


def test_rotation_after_the_old_file_grew_appends_its_tail(tmp_path, db):
    old = make_log(120)
    root = write_folder(tmp_path / "Log", {WFM: old})
    run_ingest(db, root)
    (root / WFM).unlink()
    (root / WFM_ROTATED).write_bytes(old + make_log(15, start=70_000))  # grew before the rotation
    (root / WFM).write_bytes(make_log(30, start=90_000))
    result = run_ingest(db, root)
    assert changed_actions(result) == {WFM_ROTATED: "append", WFM: "new"}
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")


def test_second_rotation_shifts_the_chain(tmp_path, db):
    a, b, c = make_log(40), make_log(40, start=10_000), make_log(40, start=20_000)
    root = write_folder(tmp_path / "Log", {WFM: a})
    run_ingest(db, root)
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(b)
    run_ingest(db, root)
    (root / WFM_ROTATED).rename(root / f"{LOGDIR}/WfmAdapter_1111.log.1")
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(c)
    result = run_ingest(db, root)
    assert changed_actions(result) == {WFM: "new"}
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")
    assert_consistent(db)


def test_shrunk_file_is_reindexed(tmp_path, db):
    data = make_log(150)
    root = write_folder(tmp_path / "Log", {WFM: data})
    run_ingest(db, root)
    (root / WFM).write_bytes(data[: len(data) // 3])
    result = run_ingest(db, root)
    assert [d.action for d in result.details] == ["reindex"]
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")
    assert_consistent(db)


def test_rewritten_file_with_same_prefix_length_is_reindexed(tmp_path, db):
    data = make_log(150)
    root = write_folder(tmp_path / "Log", {WFM: data})
    run_ingest(db, root)
    changed = bytearray(data + make_log(5, start=80_000))
    changed[len(data) - 20] ^= 0x01  # the bytes just before the indexed offset differ
    (root / WFM).write_bytes(bytes(changed))
    result = run_ingest(db, root)
    assert [d.action for d in result.details] == ["reindex"]
    assert snapshot(db) == clean_snapshot(tmp_path, root, "clean")


def test_truncated_fixture_is_indexed_with_a_partial_last_record(tmp_path, db):
    data = (FIXTURES / "truncated.log").read_bytes()
    root = write_folder(tmp_path / "Log", {WFM: data})
    run_ingest(db, root)
    flags = [r[0] for r in db.execute("SELECT flags FROM entry ORDER BY id")]
    assert flags[-1] & 1 and not any(f & 1 for f in flags[:-1])
    assert db.execute("SELECT coalesce(sum(nlines), 0) FROM entry").fetchone()[0] == lines_of(data)


# --------------------------------------------------------------------------- UTF-16


UTF16_TEXT = mf.render([
    mf.header(0, "INFO", "Installer step caf\u00e9 \U0001f600 started"),
    "\tdetail line one",
    mf.header(10, "ERROR", "Installer step failed"),
], eol="\r\n").decode("utf-8")  # fmt: skip


def utf16(text: str, bom: bytes = b"\xff\xfe", codec: str = "utf-16-le") -> bytes:
    return bom + text.encode(codec)


@pytest.mark.parametrize(
    ("bom", "codec"), [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")], ids=["le", "be"]
)
@pytest.mark.parametrize("container", ["zip", "folder"])
def test_utf16_source_is_transcoded_and_indexed(tmp_path, db, bom, codec, container):
    data = utf16(UTF16_TEXT, bom, codec)
    if container == "zip":
        path = write_ers(tmp_path / "z", {WFM: data})
    else:
        path = write_folder(tmp_path / "Log", {WFM: data})
    run_ingest(db, path, IngestOptions(chunk_size=5))
    rows = db.execute("SELECT msg, nlines, level, ts FROM entry ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["Installer step caf\u00e9 \U0001f600 started", "Installer step failed"]
    assert [r[1] for r in rows] == [2, 1]
    assert db.execute("SELECT envelope FROM source").fetchone()[0] == "adms"
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'installer'").fetchone()[0] == 2
    assert db.execute("SELECT size FROM source").fetchone()[0] == len(data)  # the size of the file on disk
    assert_consistent(db)
    changes = db.total_changes
    assert run_ingest(db, path, IngestOptions(chunk_size=5)).sources_unchanged == 1
    assert db.total_changes == changes


def test_utf16_file_that_changes_is_reindexed(tmp_path, db):
    root = write_folder(tmp_path / "Log", {WFM: utf16(UTF16_TEXT)})
    run_ingest(db, root)
    (root / WFM).write_bytes(utf16(UTF16_TEXT + mf.header(20, "INFO", "More.") + "\r\n"))
    result = run_ingest(db, root)
    assert [d.action for d in result.details] == ["reindex"]
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 3
    assert_consistent(db)


def test_utf16_plain_text_without_headers_still_indexes(tmp_path, db):
    root = write_folder(
        tmp_path / "Log", {"installer.log": utf16("Action start 10:00:00\r\nAction ended\r\n")}
    )
    run_ingest(db, root, IngestOptions(scope="all"))
    assert db.execute("SELECT envelope, n_entries FROM source WHERE member='installer.log'").fetchone() == (
        "plain",
        2,
    )
    assert db.execute("SELECT sum(nlines) FROM entry").fetchone()[0] == 2


# --------------------------------------------------------------------------- zip bomb


BOMB = b"abcdefgh\n" * 140_000  # 1.2 MB of text that deflates far beyond 200x


def test_zip_bomb_over_the_absolute_cap_is_listed_but_not_indexed(tmp_path, db):
    zpath = write_ers(tmp_path / "z", {WFM: BOMB, f"{LOGDIR}/OntAdapter_2222.log": make_log(10, pid=2222)})
    options = IngestOptions(limits=ReadLimits(ratio_exempt_max_bytes=1024 * 1024))
    result = run_ingest(db, zpath, options)
    assert result.sources_failed == 0 and result.sources_indexed == 1
    assert db.execute("SELECT status, error FROM source WHERE member LIKE '%Wfm%'").fetchone() == (
        "skipped",
        "ratio",
    )
    assert (
        db.execute(
            "SELECT count(*) FROM entry e JOIN source s ON s.id=e.source_id WHERE s.member LIKE '%Wfm%'"
        ).fetchone()[0]
        == 0
    )
    # asking for every log does not override the limit either
    run_ingest(db, zpath, IngestOptions(scope="all", limits=options.limits))
    assert db.execute("SELECT status FROM source WHERE member LIKE '%Wfm%'").fetchone()[0] == "skipped"


def test_high_ratio_below_the_cap_is_indexed(tmp_path, db):
    zpath = write_ers(tmp_path / "z", {WFM: BOMB})
    result = run_ingest(db, zpath)
    assert result.sources_indexed == 1
    assert db.execute("SELECT sum(nlines) FROM entry").fetchone()[0] == lines_of(BOMB)


def test_oversized_member_is_skipped(tmp_path, db):
    zpath = write_ers(tmp_path / "z", {WFM: make_log(200)})
    run_ingest(db, zpath, IngestOptions(limits=ReadLimits(max_member_bytes=1000)))
    assert db.execute("SELECT status, error FROM source WHERE member LIKE '%Wfm%'").fetchone() == (
        "skipped",
        "too_large",
    )


def test_archive_declaring_too_much_data_is_refused(tmp_path, db):
    zpath = write_ers(tmp_path / "z", {WFM: make_log(200)})
    from dmslog.ingest.discover import UnsafeArchiveError

    with pytest.raises(UnsafeArchiveError):
        run_ingest(db, zpath, IngestOptions(limits=ReadLimits(max_total_bytes=1000)))
    with pytest.raises(UnsafeArchiveError):
        run_ingest(db, zpath, IngestOptions(limits=ReadLimits(max_members=0)))
    assert db.execute("SELECT count(*) FROM collection").fetchone()[0] == 0


# --------------------------------------------------------------------------- collections of a case (U21)


def test_another_ers_becomes_a_second_group_and_leaves_the_first_untouched(tmp_path, db):
    a = mf.build_ers_zip(tmp_path / "a", guid=GUID_A)
    b = mf.build_ers_zip(tmp_path / "b", guid=GUID_B)
    run_ingest(db, a)
    before = snapshot(db)
    run_ingest(db, b)
    groups = db.execute("SELECT ers_guid, ers_group, id FROM collection ORDER BY id").fetchall()
    assert [(g[0], g[1] == g[2]) for g in groups] == [(GUID_A, True), (GUID_B, True)]
    first = snapshot(db)["entries"]
    assert [e for e in before["entries"] if e in first] == before["entries"]  # nothing of A changed
    assert_consistent(db)


def test_another_capture_and_a_folder_are_groups_of_their_own(tmp_path, db):
    run_ingest(db, mf.build_ers_zip(tmp_path / "a", stamp="20300115103000"))
    run_ingest(db, mf.build_ers_zip(tmp_path / "b", stamp="20300116103000"))
    run_ingest(db, write_folder(tmp_path / "Log", {WFM: make_log(5)}))
    rows = db.execute("SELECT kind, ers_ts, ers_group = id FROM collection ORDER BY id").fetchall()
    assert rows == [("ers_zip", "20300115103000", 1), ("ers_zip", "20300116103000", 1), ("folder", None, 1)]
    assert db.execute("SELECT count(DISTINCT collection_group) FROM stream").fetchone()[0] == 3


@pytest.mark.parametrize("order", [("enterprise", "sensitive"), ("sensitive", "enterprise")])
def test_the_other_half_of_the_pair_is_accepted_in_any_order(tmp_path, db, order):
    zips = {k: mf.build_ers_zip(tmp_path / k, kind=k) for k in order}
    for kind in order:
        run_ingest(db, zips[kind])
    colls = db.execute("SELECT ers_variant, ers_group, id FROM collection ORDER BY id").fetchall()
    assert [c[0] for c in colls] == list(order)
    assert colls[0][1] == colls[1][1] == colls[0][2]  # one group, named after the first collection
    streams = db.execute(
        "SELECT sensitive, collection_group, pid FROM stream WHERE family='AWFM' ORDER BY sensitive"
    ).fetchall()
    assert streams == [(0, colls[0][1], 1111), (1, colls[0][1], 1111)]  # normal and sensitive of one process
    assert (
        db.execute(
            "SELECT count(*) FROM source WHERE member LIKE '%.sensitive.log' AND status='ready'"
        ).fetchone()[0]
        == 3
    )
    for kind in order:  # and both halves can be ingested again without changes
        changes = db.total_changes
        run_ingest(db, zips[kind])
        assert db.total_changes == changes
    assert_consistent(db)


def test_folder_collection_gets_its_own_group_and_label(tmp_path, db):
    root = write_folder(tmp_path / "Log", {WFM: make_log(5)})
    run_ingest(db, root)
    row = db.execute("SELECT kind, ers_guid, ers_group = id, label FROM collection").fetchone()
    assert row == ("folder", None, 1, "Log")


def test_single_file_is_always_in_scope(tmp_path, db):
    path = tmp_path / "whatever.log"
    path.write_bytes(make_log(10))
    result = run_ingest(db, path)
    assert result.sources_indexed == 1
    assert db.execute("SELECT kind FROM collection").fetchone()[0] == "file"


# --------------------------------------------------------------------------- names


def test_default_case_names(tmp_path):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert default_case_name(plan_collection(zpath)) == "00000000-20300115103000"
    root = write_folder(tmp_path / "My Logs (Prod)", {WFM: b""})
    assert default_case_name(plan_collection(root)) == "My-Logs-Prod"
    f = tmp_path / "some file.log"
    f.write_bytes(b"")
    assert default_case_name(plan_collection(f)) == "some-file"


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a b", "a\\b"])
def test_case_names_cannot_escape_the_data_dir(bad):
    with pytest.raises(ValueError):
        validate_case_name(bad)


def test_case_db_path_layout(tmp_path):
    assert case_db_path(tmp_path, "abc") == tmp_path / "cases" / "abc" / "case.sqlite"
