"""Regression tests for the cross-review of PR #238 (issue #74): preview snapshot, byte budget, damaged
deflate, stored warnings and the job slot of the queue routes. All data is synthetic."""

from __future__ import annotations

import sys
import zipfile
import zlib
from pathlib import Path

import pytest
from test_api import client, server  # noqa: F401  (fixtures)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import lps_fixtures as fx  # noqa: E402

from dmslog.msgqueue import ingest, lps, service  # noqa: E402
from dmslog.store import create_case_db, queue  # noqa: E402
from dmslog.store.queue import BankIn, ItemIn  # noqa: E402


def make_item(n, body=b"<x/>"):
    return ItemIn(
        item_id=n, message_id=None, msg_type="T", status="", state="pending", file_location=None,
        ts=None, size=len(body), body=body,
    )  # fmt: skip


def make_bank(items, **extra):
    return BankIn(
        cache="C", adapter="A", db_size=1, wal_size=None, fingerprint="f", status="ready",
        read_at="2026-01-01T00:00:00", snapshot="wal_applied", items=items, **extra,
    )  # fmt: skip


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    conn.execute(
        "INSERT INTO collection(id, kind, path, ers_group, added_at) VALUES (1, 'file', 'x', 1, 't')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_row_ids_are_never_reused_after_a_bank_is_read_again(db):
    queue.replace_bank(db, 1, "m", make_bank([make_item(10, b"<a/>"), make_item(20, b"<b/>")]))
    first = {r["item_id"]: r["id"] for r in queue.list_items(db).items}
    queue.replace_bank(db, 1, "m", make_bank([make_item(20, b"<c/>"), make_item(30, b"<d/>")]))
    second = {r["item_id"]: r["id"] for r in queue.list_items(db).items}
    assert set(first.values()).isdisjoint(second.values())  # a row id of the old reading is gone for good
    assert queue.get_item_body(db, first[10]) is None
    item, body = queue.get_item_body(db, second[20])
    assert item["item_id"] == 20 and body == b"<c/>"  # the metadata and the body come together


def test_the_preview_reads_the_item_and_its_body_in_one_statement(db):
    queue.replace_bank(db, 1, "m", make_bank([make_item(10)]))
    row_id = queue.list_items(db).items[0]["id"]
    statements = []
    db.set_trace_callback(statements.append)
    queue.get_item_body(db, row_id)
    db.set_trace_callback(None)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "b.body" in s]
    assert len(selects) == 1 and "queue_item" in selects[0]
    assert [s for s in statements if "FROM queue_body WHERE" in s] == []


def test_stored_warnings_and_skipped_rows_come_back_in_the_summary(db):
    queue.replace_bank(
        db, 1, "m", make_bank([make_item(1)], warnings=("live_copy_unstable", "Bad Code!"), skipped_rows=2)
    )
    (bank,) = queue.summary(db)["dbs"]
    assert bank["warnings"] == ["live_copy_unstable"] and bank["skipped_rows"] == 2  # a stray code is dropped
    (shown,) = service.summary(db)["dbs"]
    assert "live_copy_unstable" in shown["warnings"] and "rows_skipped" in shown["warnings"]


def zipped(tmp_path, names):
    banks = {
        n: fx.make_bank(tmp_path / ("b" + str(i)), [fx.SynthItem(1), fx.SynthItem(2)], name=n)
        for i, n in enumerate(names)
    }
    return fx.make_ers_zip(tmp_path / "ers.zip", banks)


def test_one_budget_caps_all_the_banks_of_a_reading(tmp_path):
    names = [f"__~Cache{i}~__TestAdapter__LPS.db3" for i in range(3)]
    path = zipped(tmp_path, names)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    one = max(f.db.size for f in lps.discover(path))
    budget = lps.CopyBudget(one + one // 2)  # room for one database and a half
    results = [lps.read_bank(f, case_dir, budget=budget) for f in lps.discover(path)]
    assert [r.status for r in results].count("ready") >= 1
    assert "run_budget_exceeded" in {r.error for r in results}
    assert list(case_dir.glob(".queue-tmp-*")) == []  # the temp folders are gone either way


def test_read_collection_applies_the_run_budget(tmp_path):
    path = zipped(tmp_path, [f"__~Cache{i}~__TestAdapter__LPS.db3" for i in range(3)])
    conn = create_case_db(tmp_path / "case.sqlite")
    conn.execute(
        "INSERT INTO collection(id, kind, path, ers_group, added_at) VALUES (1, 'file', 'x', 1, 't')"
    )
    conn.commit()
    (tmp_path / "case").mkdir()
    summary = ingest.read_collection(conn, tmp_path / "case", path, 1, limits=lps.Limits(max_run_bytes=1))
    assert summary.errors == 3
    assert {r[0] for r in conn.execute("SELECT error FROM queue_db")} == {"run_budget_exceeded"}
    conn.close()


def test_a_damaged_deflate_stream_fails_only_its_own_bank(tmp_path, monkeypatch):
    names = ["__~CacheA~__TestAdapter__LPS.db3", "__~CacheB~__TestAdapter__LPS.db3"]
    path = zipped(tmp_path, names)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    real = zipfile.ZipExtFile.read
    calls = {"n": 0}

    def flaky(self, n=-1):
        calls["n"] += 1
        if calls["n"] == 1:
            raise zlib.error("Error -3 while decompressing data: invalid stored block lengths")
        return real(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", flaky)
    results = [lps.read_bank(f, case_dir) for f in lps.discover(path)]
    assert sorted((r.status, r.error) for r in results) == [("error", "extract_failed"), ("ready", None)]
    assert list(case_dir.glob(".queue-tmp-*")) == []


def test_removing_the_queue_data_needs_the_case_job_slot(client, server, tmp_path):  # noqa: F811
    assert client.post("/api/cases", {"name": "q"})[0] == 201
    hub = server.api.jobs.hub("q")
    assert hub.try_begin("held")  # a job is running
    try:
        status, body, _ = client.request("DELETE", "/api/cases/q/queue")
        assert status == 409 and body["error"] == "job_running"
    finally:
        hub.finish({"job_id": "held", "status": "ok", "message": ""})
    status, body, _ = client.request("DELETE", "/api/cases/q/queue")
    assert status == 200 and (body["dbs"], body["items"]) == (0, 0)
