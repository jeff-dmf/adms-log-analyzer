"""Reading the queue databases of a collection into the case (msgqueue/ingest.py; issue #74, Q5).

Everything is synthetic (``tests/support/lps_fixtures.py``).
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import lps_fixtures as fx  # noqa: E402

from dmslog.ingest.partial import FirstPass  # noqa: E402
from dmslog.ingest.pipeline import IngestOptions, run_ingest  # noqa: E402
from dmslog.msgqueue import ingest, lps  # noqa: E402
from dmslog.store import create_case_db, queue  # noqa: E402

NAME = fx.CACHE_NAME
OTHER = "__~TestOtherCache~__TestAdapter__LPS.db3"
CHANGE = "__~TestChangeCache~__TestAdapter__LPS.db3"


class Stop(Exception):
    """What a job's progress callback raises to stop the copy."""


@pytest.fixture
def case(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    conn = create_case_db(case_dir / "case.sqlite")
    conn.execute(
        "INSERT INTO collection(id, kind, path, ers_group, added_at) VALUES (1, 'file', 'x', 1, 't')"
    )
    conn.commit()
    yield conn, case_dir
    conn.close()


def items(*ids, status=""):
    return [fx.SynthItem(i, status=status) for i in ids]


def wal_bank(tmp_path, name=NAME):
    """Main file 1..5; WAL deletes 1, 2 and adds 6: with the WAL applied 3, 4, 5, 6."""
    return fx.make_bank(
        tmp_path / "src", items(1, 2, 3, 4, 5), name=name, wal_inserts=items(6), wal_deletes=[1, 2]
    )


def make_zip(tmp_path, banks, name="ers.zip"):
    return fx.make_ers_zip(tmp_path / name, banks)


def tmp_dirs(case_dir):
    return [p.name for p in case_dir.iterdir() if p.name.startswith(lps.TMP_PREFIX)]


@pytest.fixture
def spy_tmp(monkeypatch):
    calls = []
    real = lps.make_tmp_dir
    monkeypatch.setattr(lps, "make_tmp_dir", lambda d: calls.append(d) or real(d))
    return calls


# --------------------------------------------------------------------------- reading and summary


def test_reads_every_bank_and_summarizes(case, tmp_path):
    conn, case_dir = case
    banks = {
        NAME: wal_bank(tmp_path),
        CHANGE: fx.make_change_request_bank(tmp_path / "src2"),
    }
    zpath = make_zip(tmp_path, banks)
    out = ingest.read_collection(conn, case_dir, zpath, 1)
    assert (out.dbs, out.items, out.pending, out.errors, out.unchanged) == (2, 4, 4, 0, 0)
    assert out.as_dict() == {"dbs": 2, "items": 4, "pending": 4, "errors": 0}
    rows = conn.execute("SELECT status, snapshot, items FROM queue_db ORDER BY member").fetchall()
    assert sorted(rows) == [("not_a_queue", None, 0), ("ready", "wal_applied", 4)]
    assert sorted(r[0] for r in conn.execute("SELECT item_id FROM queue_item")) == [3, 4, 5, 6]
    assert conn.execute("SELECT count(*) FROM queue_body").fetchone()[0] == 4
    assert tmp_dirs(case_dir) == []
    assert queue.is_enabled(conn) is False  # the caller switches the option on


def test_a_folder_is_read_like_a_zip(case, tmp_path):
    conn, case_dir = case
    root = fx.make_ers_folder(tmp_path / "ers", {NAME: wal_bank(tmp_path)})
    out = ingest.read_collection(conn, case_dir, root, 1)
    assert (out.dbs, out.items) == (1, 4)


def test_a_path_without_banks_gives_an_empty_summary(case, tmp_path):
    conn, case_dir = case
    root = tmp_path / "Log"
    (root / "dnaApp").mkdir(parents=True)
    (root / "dnaApp" / "a.log").write_text("x\n", encoding="utf-8")
    assert ingest.read_collection(conn, case_dir, root, 1) == ingest.QueueSummary()


def test_the_adapter_user_name_is_never_stored(case, tmp_path):
    conn, case_dir = case
    ingest.read_collection(conn, case_dir, make_zip(tmp_path, {NAME: wal_bank(tmp_path)}), 1)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in case_dir.iterdir() if p.is_file())
    assert fx.USERNAME_PREFIX.encode() not in blob
    dump = "\n".join(conn.iterdump())
    assert fx.USERNAME_PREFIX not in dump


def test_progress_events_carry_names_only(case, tmp_path):
    conn, case_dir = case
    events = []
    zpath = make_zip(
        tmp_path, {NAME: wal_bank(tmp_path), CHANGE: fx.make_change_request_bank(tmp_path / "s2")}
    )
    ingest.read_collection(conn, case_dir, zpath, 1, progress=events.append)
    assert events and {e.stage for e in events} == {"queue"}
    assert {e.source for e in events} <= {NAME, NAME + "-wal", CHANGE}
    assert all("/" not in e.source and "\\" not in e.source for e in events)
    assert all(0 <= e.bytes_done <= max(e.bytes_total, 1) for e in events)
    done = [e for e in events if e.status == "queue_done"]
    assert sorted(e.outcome for e in done) == ["not_a_queue", "ready"]
    assert events[0].status == "queue_start"


# --------------------------------------------------------------------------- fingerprint


def test_an_unchanged_collection_is_not_read_again(case, tmp_path, spy_tmp):
    conn, case_dir = case
    zpath = make_zip(
        tmp_path, {NAME: wal_bank(tmp_path), CHANGE: fx.make_change_request_bank(tmp_path / "s2")}
    )
    ingest.read_collection(conn, case_dir, zpath, 1)
    assert len(spy_tmp) == 2
    spy_tmp.clear()
    events = []
    again = ingest.read_collection(conn, case_dir, zpath, 1, progress=events.append)
    assert spy_tmp == []  # no temporary folder, no copy
    assert again.unchanged == 2 and (again.dbs, again.items) == (2, 4)
    assert [e.outcome for e in events] == ["unchanged", "unchanged"]


def test_changed_files_are_read_again(case, tmp_path, spy_tmp):
    conn, case_dir = case
    first = make_zip(tmp_path, {NAME: wal_bank(tmp_path)}, "a.zip")
    ingest.read_collection(conn, case_dir, first, 1)
    spy_tmp.clear()
    other = fx.make_bank(tmp_path / "src3", items(10, 11), name=NAME)
    second = make_zip(tmp_path, {NAME: other}, "b.zip")
    out = ingest.read_collection(conn, case_dir, second, 1)
    assert len(spy_tmp) == 1 and out.unchanged == 0
    assert sorted(r[0] for r in conn.execute("SELECT item_id FROM queue_item")) == [10, 11]
    assert conn.execute("SELECT count(*) FROM queue_db").fetchone()[0] == 1


def test_a_folder_bank_changed_in_place_is_read_again(case, tmp_path, spy_tmp):
    conn, case_dir = case
    root = fx.make_ers_folder(tmp_path / "ers", {NAME: wal_bank(tmp_path)})
    ingest.read_collection(conn, case_dir, root, 1)
    spy_tmp.clear()
    wal = next(root.rglob(NAME + "-wal"))
    wal.unlink()  # a checkpoint removed the WAL: other size and mtime
    out = ingest.read_collection(conn, case_dir, root, 1)
    assert len(spy_tmp) == 1 and out.unchanged == 0
    assert conn.execute("SELECT snapshot FROM queue_db").fetchone()[0] == "no_wal"


# --------------------------------------------------------------------------- errors per bank


def corrupt_bank(tmp_path, name):
    dest = tmp_path / "bad"
    dest.mkdir(exist_ok=True)
    path = dest / name
    path.write_bytes(b"this is not a sqlite database at all" * 20)
    return fx.BankPaths(path, None)


def test_a_bad_bank_does_not_stop_the_others_and_is_retried(case, tmp_path, spy_tmp):
    conn, case_dir = case
    zpath = make_zip(tmp_path, {NAME: wal_bank(tmp_path), OTHER: corrupt_bank(tmp_path, OTHER)})
    out = ingest.read_collection(conn, case_dir, zpath, 1)
    assert (out.dbs, out.items, out.errors) == (2, 4, 1)
    bad = conn.execute("SELECT status, error, items FROM queue_db WHERE error IS NOT NULL").fetchall()
    assert bad == [("error", "not_sqlite", 0)]
    assert tmp_dirs(case_dir) == []
    spy_tmp.clear()
    again = ingest.read_collection(conn, case_dir, zpath, 1)
    assert len(spy_tmp) == 1 and again.unchanged == 1 and again.errors == 1  # only the error is retried


def test_a_bank_over_the_size_limit_is_an_error_row(case, tmp_path):
    conn, case_dir = case
    zpath = make_zip(tmp_path, {NAME: wal_bank(tmp_path)})
    out = ingest.read_collection(conn, case_dir, zpath, 1, limits=lps.Limits(max_db_bytes=100))
    assert (out.dbs, out.errors, out.items) == (1, 1, 0)
    assert conn.execute("SELECT error FROM queue_db").fetchone()[0] == "too_large"


def test_a_failure_while_storing_becomes_an_error_row(case, tmp_path, monkeypatch):
    conn, case_dir = case
    real = queue.replace_bank
    calls = []

    def flaky(c, cid, member, bank):
        calls.append(bank.status)
        if bank.status == "ready":
            raise queue.QueueInputError("bad_bank", "$.items", reason="synthetic")
        return real(c, cid, member, bank)

    monkeypatch.setattr(queue, "replace_bank", flaky)
    out = ingest.read_collection(conn, case_dir, make_zip(tmp_path, {NAME: wal_bank(tmp_path)}), 1)
    assert calls == ["ready", "error"]
    assert (out.dbs, out.errors) == (1, 1)
    assert conn.execute("SELECT error FROM queue_db").fetchone()[0] == "read_failed"


# --------------------------------------------------------------------------- cancellation and cleanup


def test_cancellation_before_a_bank_raises_and_leaves_no_temporary(case, tmp_path):
    conn, case_dir = case
    zpath = make_zip(tmp_path, {NAME: wal_bank(tmp_path), OTHER: wal_bank(tmp_path, OTHER)})
    stored = []

    def progress(event):
        if event.status == "queue_done":
            stored.append(event.source)

    with pytest.raises(lps.ReadCancelled):
        ingest.read_collection(
            conn, case_dir, zpath, 1, progress=progress, cancelled=lambda: len(stored) >= 1
        )
    assert len(stored) == 1
    assert conn.execute("SELECT count(*) FROM queue_db").fetchone()[0] == 1  # the first bank stays
    assert tmp_dirs(case_dir) == []


def test_cancellation_in_the_middle_of_a_copy_removes_the_temporary(case, tmp_path):
    conn, case_dir = case
    zpath = make_zip(tmp_path, {NAME: wal_bank(tmp_path)})
    polls = {"n": 0}

    def cancelled():
        polls["n"] += 1
        return polls["n"] > 2  # past the first poll, inside the block loop

    with pytest.raises(lps.ReadCancelled):
        ingest.read_collection(conn, case_dir, zpath, 1, cancelled=cancelled)
    assert tmp_dirs(case_dir) == []
    assert conn.execute("SELECT count(*) FROM queue_db").fetchone()[0] == 0


def test_an_exception_of_the_progress_callback_propagates_after_cleanup(case, tmp_path):
    conn, case_dir = case
    zpath = make_zip(tmp_path, {NAME: wal_bank(tmp_path)})

    def progress(event):
        if event.status == "queue_progress":
            raise Stop

    with pytest.raises(Stop):
        ingest.read_collection(conn, case_dir, zpath, 1, progress=progress)
    assert tmp_dirs(case_dir) == []


def test_sweep_removes_old_temporaries_only(case, tmp_path):
    _conn, case_dir = case
    old = case_dir / (lps.TMP_PREFIX + "a" * 32)
    new = case_dir / (lps.TMP_PREFIX + "b" * 32)
    keep = case_dir / "unrelated-folder"
    for folder in (old, new, keep):
        folder.mkdir()
        (folder / "f").write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    os.utime(keep, (past, past))
    assert ingest.sweep_tmp(case_dir) == [old.name]
    assert not old.exists() and new.exists() and keep.exists()


def test_read_collection_sweeps_leftovers_first(case, tmp_path):
    conn, case_dir = case
    old = case_dir / (lps.TMP_PREFIX + "c" * 32)
    old.mkdir()
    past = time.time() - 7200
    os.utime(old, (past, past))
    ingest.read_collection(conn, case_dir, make_zip(tmp_path, {NAME: wal_bank(tmp_path)}), 1)
    assert not old.exists()


# --------------------------------------------------------------------------- order against run_ingest


def log_text() -> bytes:
    lines = [f"2030-01-15 10:{i // 60:02d}:{i % 60:02d}.000 [TID=1] [INFO] - item {i}" for i in range(120)]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def test_the_queue_is_read_after_run_ingest_and_first_ready_comes_first(tmp_path):
    root = fx.make_ers_folder(tmp_path / "ers", {NAME: wal_bank(tmp_path)})
    log = root / "SchneiderElectricData/ADMS/Log/dnaApp_zone1/SVC/WfmAdapter_1111.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(log_text())
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    conn = create_case_db(case_dir / "case.sqlite")
    events: list[str] = []
    try:
        first = FirstPass(members=None, defer_rest=False)
        run_ingest(
            conn,
            root,
            IngestOptions(scope="all", first=first),
            progress=lambda e: events.append(e.phase),
        )
        assert "first_ready" in events and events[-1] == "done"
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] >= 120
        assert (
            conn.execute("SELECT count(*) FROM queue_db").fetchone()[0] == 0
        )  # run_ingest never reads banks
        cid = conn.execute("SELECT id FROM collection ORDER BY id LIMIT 1").fetchone()[0]
        queue_events: list[str] = []
        out = ingest.read_collection(
            conn, case_dir, root, cid, progress=lambda e: queue_events.append(f"{e.stage}:{e.status}")
        )
        assert (out.dbs, out.items) == (1, 4)
        assert queue_events[0] == "queue:queue_start" and queue_events[-1] == "queue:queue_done"
        # the log index was untouched by the queue stage
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] >= 120
        assert tmp_dirs(case_dir) == []
    finally:
        conn.close()
        shutil.rmtree(root, ignore_errors=True)
