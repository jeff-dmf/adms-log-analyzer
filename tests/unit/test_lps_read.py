"""Reading the message queue databases: WAL handling, normalization, limits, temp cleanup (issue #74, Q2).

All databases are synthetic (``tests/support/lps_fixtures.py``); no real data is used anywhere.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import stat
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import lps_fixtures as fx  # noqa: E402

from dmslog.ingest.discover import UnsafeArchiveError  # noqa: E402
from dmslog.ingest.reader import InsufficientDiskSpaceError  # noqa: E402
from dmslog.msgqueue import lps  # noqa: E402

NAME = fx.CACHE_NAME
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and symlinks")


class Boom(Exception):
    """An exception type the module does not map to a bank error: it must propagate."""


def items(*ids: int, status: object = "") -> list[fx.SynthItem]:
    return [fx.SynthItem(i, status=status) for i in ids]


def wal_bank(tmp_path: Path, name: str = NAME) -> fx.BankPaths:
    """Main file: items 1..5. WAL: deletes 1 and 2, inserts 6. With the WAL applied: 3, 4, 5, 6."""
    return fx.make_bank(
        tmp_path / "src", items(1, 2, 3, 4, 5), name=name, wal_inserts=items(6), wal_deletes=[1, 2]
    )


def zip_files(tmp_path: Path, banks: dict[str, fx.BankPaths]) -> lps.BankFiles:
    zpath = fx.make_ers_zip(tmp_path / "ers.zip", banks)
    found = lps.discover(zpath)
    assert len(found) == 1
    return found[0]


def folder_files(tmp_path: Path, banks: dict[str, fx.BankPaths]) -> lps.BankFiles:
    root = fx.make_ers_folder(tmp_path / "ers", banks)
    found = lps.discover(root)
    assert len(found) == 1
    return found[0]


@pytest.fixture
def case_dir(tmp_path: Path) -> Path:
    path = tmp_path / "case"
    path.mkdir()
    return path


def leftovers(case_dir: Path) -> list[str]:
    return sorted(p.name for p in case_dir.iterdir() if p.name.startswith(lps.TMP_PREFIX))


def ids(result: lps.BankResult) -> list[int]:
    return [r.item_id for r in result.items]


# --------------------------------------------------------------------------- the fixture itself


def test_fixture_state_differs_between_main_file_and_wal(tmp_path):
    bank = wal_bank(tmp_path)
    assert bank.wal is not None and bank.wal.stat().st_size > 0
    main_only = sqlite3.connect(bank.db.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        rows = main_only.execute("SELECT _INTEGRATION_MESSAGE_ID FROM CURRENT_INTEGRATION_MESSAGE").fetchall()
    finally:
        main_only.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]


# --------------------------------------------------------------------------- discovery


def test_discover_zip_pairs_db_and_wal_and_parses_name(tmp_path):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    assert files.member == f"{fx.STORE_DIR}/{NAME}"
    assert files.wal is not None and files.wal.member == files.member + "-wal"
    assert (files.cache, files.adapter) == ("TestQueueSmartCache", "TestAdapter")


def test_discover_folder_and_loose_log_folder(tmp_path):
    files = folder_files(tmp_path, {NAME: wal_bank(tmp_path)})
    assert files.db.container_kind == "folder" and files.wal is not None
    log_only = tmp_path / "Log"
    log_only.mkdir()
    (log_only / "x.log").write_text("line\n", encoding="utf-8")
    assert lps.discover(log_only) == []


def test_discover_ignores_non_matching_paths_and_matches_case_insensitively(tmp_path):
    bank = wal_bank(tmp_path)
    zpath = tmp_path / "ers.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(bank.db, "SchneiderElectricData/ADMS/data/save_cases/localpersistencestore/0/other-name.db3")
        zf.write(bank.db, "SchneiderElectricData/ADMS/data/save_cases/LocalPersistenceStore/direct.db3")
        zf.write(bank.db, "SchneiderElectricData/elsewhere/0/x.db3")
        zf.write(
            bank.wal, "SchneiderElectricData/ADMS/data/save_cases/localpersistencestore/0/other-name.db3-WAL"
        )
    found = lps.discover(zpath)
    assert [b.member.rsplit("/", 1)[-1] for b in found] == ["other-name.db3"]
    assert found[0].wal is not None
    assert (found[0].cache, found[0].adapter) == ("other-name", None)


def test_parse_bank_name_truncates_long_unknown_names():
    cache, adapter = lps.parse_bank_name("x" * 300 + ".db3")
    assert len(cache) == 128 and adapter is None


def test_zip_slip_member_is_refused_by_discovery(tmp_path):
    bank = wal_bank(tmp_path)
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(bank.db, "a/../../escape/LocalPersistenceStore/0/x.db3")
    with pytest.raises(UnsafeArchiveError):
        lps.discover(zpath)
    assert not (tmp_path.parent / "escape").exists()


def test_backslash_member_names_of_windows_tools_are_read(tmp_path):
    bank = wal_bank(tmp_path)
    zpath = tmp_path / "win.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(bank.db, "Data\\LocalPersistenceStore\\0\\" + NAME)
        zf.write(bank.wal, "Data\\LocalPersistenceStore\\0\\" + NAME + "-wal")
    (files,) = lps.discover(zpath)
    case = tmp_path / "case"
    case.mkdir()
    result = lps.read_bank(files, case)
    assert result.status == "ready" and ids(result) == [3, 4, 5, 6]


# --------------------------------------------------------------------------- WAL snapshots


@pytest.mark.parametrize("kind", ["zip", "folder"])
def test_wal_applied_state_differs_from_main_file(tmp_path, case_dir, kind):
    make = zip_files if kind == "zip" else folder_files
    files = make(tmp_path, {NAME: wal_bank(tmp_path)})
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.snapshot, result.error) == ("ready", "wal_applied", None)
    assert ids(result) == [3, 4, 5, 6]
    assert result.wal_size and result.db_size
    assert leftovers(case_dir) == []


def test_no_wal_reads_the_main_file_state(tmp_path, case_dir):
    bank = fx.make_bank(
        tmp_path / "src", items(1, 2, 3, 4, 5), wal_inserts=items(6), wal_deletes=[1, 2], keep_wal=False
    )
    files = zip_files(tmp_path, {NAME: bank})
    assert files.wal is None
    result = lps.read_bank(files, case_dir)
    assert (result.snapshot, result.wal_size) == ("no_wal", None)
    assert ids(result) == [1, 2, 3, 4, 5]


def test_empty_wal_counts_as_no_wal(tmp_path, case_dir):
    bank = fx.make_bank(tmp_path / "src", items(1, 2), keep_wal=False)
    wal = bank.db.with_name(bank.db.name + "-wal")
    wal.write_bytes(b"")
    files = folder_files(tmp_path, {NAME: fx.BankPaths(bank.db, wal)})
    result = lps.read_bank(files, case_dir)
    assert result.snapshot == "no_wal" and ids(result) == [1, 2]
    assert leftovers(case_dir) == []


def test_wal_failed_falls_back_to_main_file_only(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    real_open = lps._open_connection
    opened: list[tuple[bool, bool]] = []

    def failing_with_wal(tmp_db, *, immutable):
        has_wal = (tmp_db.parent / "q.db3-wal").exists()
        opened.append((has_wal, immutable))
        if has_wal:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_open(tmp_db, immutable=immutable)

    monkeypatch.setattr(lps, "_open_connection", failing_with_wal)
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.snapshot) == ("ready", "wal_failed")
    assert ids(result) == [1, 2, 3, 4, 5]  # the last checkpoint, older than the collection
    assert opened == [(True, False), (False, True)]
    assert leftovers(case_dir) == []


def test_wal_failed_that_also_fails_without_wal_is_an_error(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})

    def always_failing(tmp_db, *, immutable):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(lps, "_open_connection", always_failing)
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error, result.snapshot) == ("error", "corrupt", None)
    assert leftovers(case_dir) == []


def test_garbage_wal_never_invents_rows(tmp_path, case_dir):
    bank = wal_bank(tmp_path)
    data = bytearray(bank.wal.read_bytes())
    for i in range(32, len(data), 7):  # damage every frame, keep the header
        data[i] ^= 0xFF
    bank.wal.write_bytes(bytes(data))
    result = lps.read_bank(folder_files(tmp_path, {NAME: bank}), case_dir)
    assert result.status == "ready"
    # SQLite drops frames with a bad checksum without an error: the state is the last checkpoint, and the
    # app cannot tell this from a truncated WAL (a declared limit of the design, section 1.1).
    assert ids(result) == [1, 2, 3, 4, 5]
    assert leftovers(case_dir) == []


# --------------------------------------------------------------------------- not a queue, bad schema


def test_change_request_bank_is_not_a_queue(tmp_path, case_dir):
    files = zip_files(tmp_path, {NAME: fx.make_change_request_bank(tmp_path / "src")})
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error, result.items) == ("not_a_queue", None, ())


def test_view_with_the_table_name_is_not_a_queue(tmp_path, case_dir):
    script = (
        "CREATE TABLE t (_INTEGRATION_MESSAGE_ID INTEGER, _INTEGRATION_MESSAGE_STATUS, "
        "_INTEGRATION_MESSAGE_FILE_LOCATION, _INTEGRATION_MESSAGE_TYPE, _INTEGRATION_MESSAGE_FINALSTATE, "
        "_INTEGRATION_MESSAGE_TIMESTAMP, _INTEGRATION_MESSAGE_CONTENT);"
        "INSERT INTO t VALUES (1, '', '', '', '', 0, x'00');"
        "CREATE VIEW CURRENT_INTEGRATION_MESSAGE AS SELECT * FROM t;"
    )
    files = zip_files(tmp_path, {NAME: fx.make_custom_bank(tmp_path / "src", script)})
    assert lps.read_bank(files, case_dir).status == "not_a_queue"


def test_missing_columns(tmp_path, case_dir):
    script = (
        "CREATE TABLE CURRENT_INTEGRATION_MESSAGE (_INTEGRATION_MESSAGE_ID INTEGER, "
        "_INTEGRATION_MESSAGE_CONTENT BLOB);"
    )
    files = zip_files(tmp_path, {NAME: fx.make_custom_bank(tmp_path / "src", script)})
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error) == ("error", "missing_columns")
    assert leftovers(case_dir) == []


def test_file_that_is_not_sqlite(tmp_path, case_dir):
    src = tmp_path / "src"
    src.mkdir()
    fake = src / NAME
    fake.write_bytes(b"this is not a database " * 100)
    files = zip_files(tmp_path, {NAME: fx.BankPaths(fake, None)})
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error) == ("error", "not_sqlite")
    assert leftovers(case_dir) == []


def test_database_with_a_damaged_page_is_corrupt(tmp_path, case_dir):
    bank = fx.make_bank(tmp_path / "src", items(1, 2, 3), keep_wal=False, extra_free_pages=4)
    data = bytearray(bank.db.read_bytes())
    data[4096 : 4096 + 64] = b"\xff" * 64  # damage the first page after the header
    bank.db.write_bytes(bytes(data))
    result = lps.read_bank(zip_files(tmp_path, {NAME: bank}), case_dir)
    assert (result.status, result.error) == ("error", "corrupt")
    assert leftovers(case_dir) == []


# --------------------------------------------------------------------------- limits and normalization


def test_blob_over_the_limit_keeps_size_but_no_body(tmp_path, case_dir):
    big = fx.SynthItem(2, content=b"B" * 5000)
    small = fx.SynthItem(1, content=b"s" * 100)
    files = zip_files(tmp_path, {NAME: fx.make_bank(tmp_path / "src", [small, big])})
    result = lps.read_bank(files, case_dir, limits=lps.Limits(max_body_bytes=1000))
    by_id = {r.item_id: r for r in result.items}
    assert by_id[1].body == b"s" * 100 and by_id[1].size == 100
    assert by_id[2].body is None and by_id[2].size == 5000


def test_bank_body_budget_stops_storing_bodies(tmp_path, case_dir):
    rows = [fx.SynthItem(i, content=b"x" * 400) for i in range(1, 6)]
    files = zip_files(tmp_path, {NAME: fx.make_bank(tmp_path / "src", rows)})
    result = lps.read_bank(files, case_dir, limits=lps.Limits(max_bank_body_bytes=1000))
    assert [r.body is not None for r in result.items] == [True, True, False, False, False]
    assert "bodies_truncated" in result.warnings
    assert all(r.size == 400 for r in result.items)


def test_item_limit_truncates_with_warning(tmp_path, case_dir):
    files = zip_files(tmp_path, {NAME: fx.make_bank(tmp_path / "src", items(*range(1, 8)))})
    result = lps.read_bank(files, case_dir, limits=lps.Limits(max_items=5))
    assert ids(result) == [1, 2, 3, 4, 5] and "items_truncated" in result.warnings
    exact = lps.read_bank(files, case_dir, limits=lps.Limits(max_items=7))
    assert len(exact.items) == 7 and "items_truncated" not in exact.warnings


def test_empty_and_null_content_have_no_body(tmp_path, case_dir):
    rows = [fx.SynthItem(1, content=b""), fx.SynthItem(2, content=None)]
    bank = fx.make_bank(tmp_path / "src", rows)
    conn = sqlite3.connect(bank.db)
    conn.execute(
        "UPDATE CURRENT_INTEGRATION_MESSAGE SET _INTEGRATION_MESSAGE_CONTENT = NULL "
        "WHERE _INTEGRATION_MESSAGE_ID = 2"
    )
    conn.commit()
    conn.close()
    result = lps.read_bank(zip_files(tmp_path, {NAME: bank}), case_dir)
    by_id = {r.item_id: r for r in result.items}
    assert by_id[1].body is None and by_id[1].size == 0
    assert by_id[2].body is None and by_id[2].size == 0


def test_text_normalization(tmp_path, case_dir):
    guid_text = "{ABCDEF01-2345-6789-ABCD-EF0123456789}"
    rows = [
        fx.SynthItem(1, message_id=guid_text, status="Sent"),
        fx.SynthItem(2, message_id="not-a-guid", status="  sent "),
        fx.SynthItem(3, message_id=None, status=None),
        fx.SynthItem(4, status="Failed", msg_type="Typ\x00e\x07-1", file_location="f" * 400),
        fx.SynthItem(5, status=7, msg_type=b"\x01\x02", message_id=42),
    ]
    bank = fx.make_bank(tmp_path / "src", rows)
    conn = sqlite3.connect(bank.db)
    conn.execute(
        "UPDATE CURRENT_INTEGRATION_MESSAGE SET _INTEGRATION_MESSAGE_TYPE = CAST(x'ff41' AS TEXT) "
        "WHERE _INTEGRATION_MESSAGE_ID = 3"
    )
    conn.commit()
    conn.close()
    result = lps.read_bank(zip_files(tmp_path, {NAME: bank}), case_dir)
    by_id = {r.item_id: r for r in result.items}
    assert by_id[1].message_id == "abcdef01-2345-6789-abcd-ef0123456789"
    assert by_id[1].state == "sent"
    assert by_id[2].message_id is None and by_id[2].state == "sent"
    assert by_id[3].state == "pending" and by_id[3].status is None
    assert by_id[3].msg_type is None  # invalid UTF-8
    assert by_id[4].state == "other" and by_id[4].msg_type == "Type-1"
    assert len(by_id[4].file_location or "") == 256
    assert by_id[5].status == "7" and by_id[5].state == "other"
    assert by_id[5].msg_type is None and by_id[5].message_id is None  # BLOB / number -> NULL for the id


def test_message_id_variants():
    assert (
        lps.normalize_message_id("ABCDEF01-2345-6789-ABCD-EF0123456789")
        == "abcdef01-2345-6789-abcd-ef0123456789"
    )
    assert (
        lps.normalize_message_id("{abcdef01-2345-6789-abcd-ef0123456789}")
        == "abcdef01-2345-6789-abcd-ef0123456789"
    )
    assert lps.normalize_message_id("abcdef01-2345-6789-abcd-ef01234567") is None
    assert lps.normalize_message_id(None) is None


def test_ticks_to_ms():
    ts = fx.ticks_of(datetime(2024, 5, 1, 12, 30, 15))
    assert (
        lps.ticks_to_ms(ts)
        == int((datetime(2024, 5, 1, 12, 30, 15) - datetime(1970, 1, 1)).total_seconds()) * 1000
    )
    assert lps.ticks_to_ms(ts | (0b10 << 62)) == lps.ticks_to_ms(
        ts
    )  # DateTime.ToBinary kind bits are dropped
    assert lps.ticks_to_ms((ts | (0b10 << 62)) - (1 << 64)) == lps.ticks_to_ms(
        ts
    )  # same value as signed int64
    assert lps.ticks_to_ms(0) is None  # year 1
    assert lps.ticks_to_ms(fx.ticks_of(datetime(2101, 1, 1))) is None
    assert lps.ticks_to_ms("123") is None and lps.ticks_to_ms(None) is None and lps.ticks_to_ms(True) is None


def test_ticks_are_returned_as_local_wall_clock_ms(tmp_path, case_dir):
    moment = datetime(2024, 5, 1, 12, 30, 15)
    files = zip_files(
        tmp_path, {NAME: fx.make_bank(tmp_path / "src", [fx.SynthItem(1, timestamp=fx.ticks_of(moment))])}
    )
    (item,) = lps.read_bank(files, case_dir).items
    assert item.ts == int((moment - datetime(1970, 1, 1)).total_seconds()) * 1000  # no zone conversion


def test_non_integer_item_ids_are_skipped_and_counted(tmp_path, case_dir):
    cols = (
        "_INTEGRATION_MESSAGE_ID, _INTEGRATION_MESSAGE_STATUS, _INTEGRATION_MESSAGE_FILE_LOCATION, "
        "_INTEGRATION_MESSAGE_TYPE, _INTEGRATION_MESSAGE_FINALSTATE, _INTEGRATION_MESSAGE_TIMESTAMP, "
        "_INTEGRATION_MESSAGE_CONTENT"
    )
    script = (
        f"CREATE TABLE CURRENT_INTEGRATION_MESSAGE ({cols});"
        "INSERT INTO CURRENT_INTEGRATION_MESSAGE VALUES (5, '', '', 'T', '', 0, x'01');"
        "INSERT INTO CURRENT_INTEGRATION_MESSAGE VALUES ('abc', '', '', 'T', '', 0, x'01');"
        "INSERT INTO CURRENT_INTEGRATION_MESSAGE VALUES (-3, '', '', 'T', '', 0, x'01');"
        "INSERT INTO CURRENT_INTEGRATION_MESSAGE VALUES (NULL, '', '', 'T', '', 0, x'01');"
    )
    files = zip_files(tmp_path, {NAME: fx.make_custom_bank(tmp_path / "src", script)})
    result = lps.read_bank(files, case_dir)
    assert ids(result) == [5] and result.skipped_rows == 3 and "skipped_rows" in result.warnings


# --------------------------------------------------------------------------- username, hardening


def test_username_is_never_selected_or_returned(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    assert "USERNAME" not in lps._QUERY.upper() and "*" not in lps._QUERY
    assert all("USERNAME" not in c for c in lps.COLUMNS)
    read_columns: list[str] = []
    real_open = lps._open_connection

    def spying_open(tmp_db, *, immutable):
        conn = real_open(tmp_db, immutable=immutable)

        def authorizer(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_READ and arg1 == "CURRENT_INTEGRATION_MESSAGE":
                read_columns.append(arg2)
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorizer)
        return conn

    monkeypatch.setattr(lps, "_open_connection", spying_open)
    result = lps.read_bank(files, case_dir)
    assert result.status == "ready"
    assert set(read_columns) == set(lps.COLUMNS)
    blob = repr(result)
    assert fx.USERNAME_PREFIX not in blob and all(fx.USERNAME_PREFIX not in repr(r) for r in result.items)


def test_connection_is_hardened(tmp_path):
    bank = fx.make_bank(tmp_path / "src", items(1), keep_wal=False)
    other = fx.make_custom_bank(tmp_path / "other", "CREATE TABLE a (x);", name="other.db3")
    conn = lps._open_connection(bank.db, immutable=True)
    try:
        lps._harden(conn, lps.Limits())
        with pytest.raises(sqlite3.Error):
            conn.execute("ATTACH DATABASE ? AS other", (str(other.db),))
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert conn.execute("PRAGMA mmap_size").fetchone()[0] == 0
        assert conn.execute("PRAGMA cell_size_check").fetchone()[0] == 1
        with pytest.raises(sqlite3.Error):  # extension loading is never enabled
            conn.execute("SELECT load_extension('nothing')")
        assert conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == lps.MAX_BODY_BYTES + 1
    finally:
        conn.close()


def test_query_only_after_wal_recovery(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    seen: list[object] = []
    real_read_queue = lps._read_queue

    def spying(conn, limits):
        seen.append(conn.execute("PRAGMA query_only").fetchone()[0])
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM CURRENT_INTEGRATION_MESSAGE")
        return real_read_queue(conn, limits)

    monkeypatch.setattr(lps, "_read_queue", spying)
    assert lps.read_bank(files, case_dir).status == "ready"
    assert seen == [1]


def test_read_timeout(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    monkeypatch.setattr(lps, "_PROGRESS_STEPS", 1)
    result = lps.read_bank(files, case_dir, limits=lps.Limits(read_timeout_s=-1.0))
    assert (result.status, result.error) == ("error", "read_timeout")
    assert leftovers(case_dir) == []


# --------------------------------------------------------------------------- per-bank errors


def test_too_large_by_declared_size_and_by_counted_bytes(tmp_path, case_dir):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    assert lps.read_bank(files, case_dir, limits=lps.Limits(max_db_bytes=10)).error == "too_large"
    assert lps.read_bank(files, case_dir, limits=lps.Limits(max_wal_bytes=10)).error == "too_large"
    # a header that lies about the size: the counted bytes still stop the copy
    lying = dataclasses.replace(files, db=dataclasses.replace(files.db, size=1))
    result = lps.read_bank(lying, case_dir, limits=lps.Limits(max_db_bytes=5000))
    assert (result.status, result.error) == ("error", "too_large")
    assert leftovers(case_dir) == []


def test_encrypted_member(tmp_path, case_dir):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    flagged = dataclasses.replace(files, db=dataclasses.replace(files.db, limit_violation="encrypted"))
    result = lps.read_bank(flagged, case_dir)
    assert (result.status, result.error) == ("error", "encrypted_member")
    assert leftovers(case_dir) == []


def test_insufficient_space(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})

    def no_space(folder, needed):
        raise InsufficientDiskSpaceError("no space")

    monkeypatch.setattr(lps, "check_free_space", no_space)
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error) == ("error", "insufficient_space")
    assert leftovers(case_dir) == []


def test_damaged_zip_member_is_extract_failed(tmp_path, case_dir):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    zpath = files.db.container
    raw = bytearray(zpath.read_bytes())
    raw[60:120] = b"\x00" * 60  # inside the compressed data of the first member
    zpath.write_bytes(bytes(raw))
    result = lps.read_bank(files, case_dir)
    assert result.status == "error" and result.error in {"extract_failed", "not_sqlite", "corrupt"}
    assert leftovers(case_dir) == []


def test_missing_source_is_extract_failed(tmp_path, case_dir):
    files = folder_files(tmp_path, {NAME: wal_bank(tmp_path)})
    (files.db.container / files.db.member).unlink()
    result = lps.read_bank(files, case_dir)
    assert (result.status, result.error) == ("error", "extract_failed")
    assert leftovers(case_dir) == []


def test_live_folder_that_keeps_changing_warns_and_still_reads(tmp_path, case_dir, monkeypatch):
    files = folder_files(tmp_path, {NAME: wal_bank(tmp_path)})
    counter = iter(range(1000))
    monkeypatch.setattr(lps, "_stat_key", lambda path: (next(counter), 0))
    result = lps.read_bank(files, case_dir)
    assert result.status == "ready" and "live_copy_unstable" in result.warnings
    assert ids(result) == [3, 4, 5, 6]


def test_live_folder_that_changes_once_is_copied_again(tmp_path, case_dir, monkeypatch):
    files = folder_files(tmp_path, {NAME: wal_bank(tmp_path)})
    real = lps._stat_key
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        return (calls["n"], 0) if calls["n"] == 1 else real(path)

    monkeypatch.setattr(lps, "_stat_key", flaky)
    result = lps.read_bank(files, case_dir)
    assert result.status == "ready" and "live_copy_unstable" not in result.warnings


# --------------------------------------------------------------------------- fingerprint


def test_fingerprint_zip_is_stable_and_tracks_content(tmp_path):
    bank = wal_bank(tmp_path)
    a = zip_files(tmp_path, {NAME: bank})
    assert lps.fingerprint_of(a) == lps.fingerprint_of(a)
    other = tmp_path / "second"
    other.mkdir()
    bank2 = fx.make_bank(other / "src", items(1, 2, 3, 4, 5), wal_inserts=items(6, 7), wal_deletes=[1, 2])
    b = lps.discover(fx.make_ers_zip(other / "ers.zip", {NAME: bank2}))[0]
    assert lps.fingerprint_of(a) != lps.fingerprint_of(b)
    no_wal = lps.discover(fx.make_ers_zip(other / "nowal.zip", {NAME: fx.BankPaths(bank.db, None)}))[0]
    assert lps.fingerprint_of(no_wal).endswith("|-")


def test_fingerprint_folder_tracks_mtime_and_size(tmp_path):
    files = folder_files(tmp_path, {NAME: wal_bank(tmp_path)})
    before = lps.fingerprint_of(files)
    target = files.db.container / files.db.member
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert lps.fingerprint_of(files) != before
    assert before.startswith("d")


# --------------------------------------------------------------------------- temporary folder


def test_temp_folder_is_gone_after_success_and_private(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    inside: list[tuple[str, int, int]] = []
    real_read = lps.read_db_file

    def spying(tmp_db, limits, **kw):
        folder = tmp_db.parent
        inside.append((folder.name, stat.S_IMODE(folder.stat().st_mode), stat.S_IMODE(tmp_db.stat().st_mode)))
        return real_read(tmp_db, limits, **kw)

    monkeypatch.setattr(lps, "read_db_file", spying)
    assert lps.read_bank(files, case_dir).status == "ready"
    assert len(inside) == 1 and lps.TMP_RE.match(inside[0][0])
    if os.name != "nt":
        assert inside[0][1] == 0o700 and inside[0][2] == 0o600
    assert leftovers(case_dir) == []


def test_member_name_never_becomes_a_path(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    names: list[list[str]] = []
    real_read = lps.read_db_file

    def spying(tmp_db, limits, **kw):
        names.append(sorted(p.name for p in tmp_db.parent.iterdir()))
        return real_read(tmp_db, limits, **kw)

    monkeypatch.setattr(lps, "read_db_file", spying)
    lps.read_bank(files, case_dir)
    assert names[0][0] == "q.db3" and "q.db3-wal" in names[0]
    assert not any(n.endswith("-shm") for n in names[0])  # the copy never brings an -shm


@pytest.mark.parametrize("point", ["copy", "open", "query", "progress"])
def test_temp_folder_is_removed_when_a_stage_fails(tmp_path, case_dir, monkeypatch, point):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    progress = None

    def boom(*args, **kwargs):
        raise Boom

    if point == "copy":
        monkeypatch.setattr(lps, "_copy_one", boom)
    elif point == "open":
        monkeypatch.setattr(lps, "_open_connection", boom)
    elif point == "query":
        monkeypatch.setattr(lps, "_read_queue", boom)
    else:
        progress = boom
    with pytest.raises(Boom):
        lps.read_bank(files, case_dir, progress=progress)
    assert leftovers(case_dir) == []


def test_failed_wal_fallback_cleans_both_folders(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})

    def open_then_boom(tmp_db, *, immutable):
        if not (tmp_db.parent / "q.db3-wal").exists():
            raise Boom
        raise sqlite3.DatabaseError("malformed")

    monkeypatch.setattr(lps, "_open_connection", open_then_boom)
    with pytest.raises(Boom):
        lps.read_bank(files, case_dir)
    assert leftovers(case_dir) == []


def test_cancellation_before_during_copy_and_during_the_read(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    with pytest.raises(lps.ReadCancelled):
        lps.read_bank(files, case_dir, cancelled=lambda: True)
    assert leftovers(case_dir) == []

    state = {"stop": False}

    def progress(name, done, total):  # the first copied block asks to stop; the next poll raises
        state["stop"] = True

    with pytest.raises(lps.ReadCancelled):
        lps.read_bank(files, case_dir, progress=progress, cancelled=lambda: state["stop"])
    assert leftovers(case_dir) == []

    # cancellation once SQLite is reading: flagged when the connection is opened
    opened = {"yes": False}
    real_open = lps._open_connection

    def tracking_open(tmp_db, *, immutable):
        opened["yes"] = True
        return real_open(tmp_db, immutable=immutable)

    monkeypatch.setattr(lps, "_open_connection", tracking_open)
    monkeypatch.setattr(lps, "_PROGRESS_STEPS", 1)
    with pytest.raises(lps.ReadCancelled):
        lps.read_bank(files, case_dir, cancelled=lambda: opened["yes"])
    assert leftovers(case_dir) == []


def test_connection_is_closed_before_the_folder_is_removed(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    events: list[str] = []
    real_close = lps._close_connection
    real_remove = lps.remove_tmp_dir

    def close(conn):
        events.append("close")
        real_close(conn)

    def remove(path):
        events.append("remove")
        return real_remove(path)

    monkeypatch.setattr(lps, "_close_connection", close)
    monkeypatch.setattr(lps, "remove_tmp_dir", remove)
    lps.read_bank(files, case_dir)
    assert events == ["close", "remove"]


def test_remove_tmp_dir_retries_on_permission_error(tmp_path, monkeypatch):
    target = tmp_path / ".queue-tmp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    target.mkdir()
    (target / "q.db3").write_bytes(b"x")
    monkeypatch.setattr(lps, "_RMTREE_DELAY_S", 0)
    real = lps.shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("held by a scanner")
        return real(path, *a, **kw)

    monkeypatch.setattr(lps.shutil, "rmtree", flaky)
    assert lps.remove_tmp_dir(target) is True and calls["n"] == 3 and not target.exists()


def test_remove_tmp_dir_gives_up_without_raising(tmp_path, monkeypatch):
    target = tmp_path / ".queue-tmp-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    target.mkdir()
    monkeypatch.setattr(lps, "_RMTREE_DELAY_S", 0)

    def always(path, *a, **kw):
        raise PermissionError("held")

    monkeypatch.setattr(lps.shutil, "rmtree", always)
    assert lps.remove_tmp_dir(target) is False and target.exists()


def test_read_bank_survives_a_stuck_removal(tmp_path, case_dir, monkeypatch):
    files = zip_files(tmp_path, {NAME: wal_bank(tmp_path)})
    monkeypatch.setattr(lps, "_RMTREE_DELAY_S", 0)
    monkeypatch.setattr(lps.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("held")))
    result = lps.read_bank(files, case_dir)
    assert result.status == "ready"  # the sweep deletes the leftover later


# --------------------------------------------------------------------------- sweep


def test_sweep_removes_only_old_matching_folders(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    old = case / (lps.TMP_PREFIX + "a" * 32)
    new = case / (lps.TMP_PREFIX + "b" * 32)
    odd = case / (lps.TMP_PREFIX + "short")
    plain = case / "other-folder"
    file_named_like = case / (lps.TMP_PREFIX + "c" * 32 + "x")
    for d in (old, new, odd, plain):
        d.mkdir()
        (d / "q.db3").write_bytes(b"x")
    file_named_like.write_bytes(b"x")
    os.utime(old, (1, 1))
    os.utime(odd, (1, 1))
    removed = lps.sweep_tmp_dir(case)
    assert removed == [old.name]
    assert not old.exists() and new.exists() and odd.exists() and plain.exists() and file_named_like.exists()


def test_sweep_with_explicit_clock_and_age(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    folder = case / (lps.TMP_PREFIX + "d" * 32)
    folder.mkdir()
    mtime = folder.stat().st_mtime
    assert lps.sweep_tmp_dir(case, now=mtime + 10) == []
    assert lps.sweep_tmp_dir(case, now=mtime + 4000) == [folder.name]


def test_sweep_of_a_missing_folder_is_empty(tmp_path):
    assert lps.sweep_tmp_dir(tmp_path / "nope") == []


@POSIX_ONLY
def test_sweep_does_not_follow_symlinks(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep", encoding="utf-8")
    link = case / (lps.TMP_PREFIX + "e" * 32)
    link.symlink_to(precious, target_is_directory=True)
    assert lps.sweep_tmp_dir(case, older_than_s=-10) == []
    assert (precious / "keep.txt").exists() and link.is_symlink()


def test_make_tmp_dir_is_unique_and_inside_the_case(tmp_path):
    a = lps.make_tmp_dir(tmp_path / "case")
    b = lps.make_tmp_dir(tmp_path / "case")
    assert a != b and a.parent == tmp_path / "case" and lps.TMP_RE.match(a.name)
    if os.name != "nt":
        assert stat.S_IMODE(a.stat().st_mode) == 0o700
