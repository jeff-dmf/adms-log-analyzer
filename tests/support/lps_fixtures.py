"""Synthetic message queue databases for tests (issue #74). Every value is invented.

``make_bank`` builds a real SQLite file in WAL mode: base rows are checkpointed into the main file, then
``wal_inserts``/``wal_deletes`` stay only in the ``-wal``. The files are copied while the connection is
still open (closing it would checkpoint and remove the WAL), so the copy has the state "main file older
than WAL", exactly like an ERS taken from a live adapter.
"""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nbfx_writer import encode, guid, make_message

CACHE_NAME = "__~TestQueueSmartCache~__TestAdapter__LPS.db3"
STORE_DIR = "SchneiderElectricData/ADMS/data/save_cases/LocalPersistenceStore/0000000000000000"
USERNAME_PREFIX = "user-"

TICKS_AT_UNIX_EPOCH = 621_355_968_000_000_000

SCHEMA = """
CREATE TABLE CURRENT_INTEGRATION_MESSAGE (
  _INTEGRATION_MESSAGE_ID INTEGER PRIMARY KEY,
  _INTEGRATION_MESSAGE_STATUS TEXT,
  _INTEGRATION_MESSAGE_FILE_LOCATION TEXT,
  _INTEGRATION_MESSAGE_TYPE TEXT,
  _INTEGRATION_MESSAGE_FINALSTATE TEXT,
  _INTEGRATION_MESSAGE_TIMESTAMP INTEGER,
  _INTEGRATION_MESSAGE_CONTENT BLOB,
  _INTEGRATION_MESSAGE_USERNAME TEXT,
  _IMPORT_CMD INTEGER,
  _RESET_PROGRESS_BAR INTEGER,
  _IS_REPAIR_MODE_ACTIVE INTEGER);
CREATE TABLE CURRENT_METADATA (_KEY TEXT PRIMARY KEY, _VALUE TEXT);
"""


def ticks_of(moment: datetime) -> int:
    """.NET ticks of a naive datetime, taken as local time (no zone conversion)."""
    delta = moment - datetime(1970, 1, 1)
    return TICKS_AT_UNIX_EPOCH + (delta.days * 86_400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


@dataclass(frozen=True)
class SynthItem:
    item_id: int
    status: object = ""
    msg_type: object = "WorksEventMessageType"
    message_id: object = None  # default: a synthetic GUID derived from item_id
    file_location: object = "synthetic-location"
    timestamp: object = None  # default: 2024-05-01 12:00 + item_id minutes
    content: object = None  # default: a synthetic NBFX message
    username: str | None = None

    def row(self) -> tuple[object, ...]:
        message_id = str(guid(self.item_id)) if self.message_id is None else self.message_id
        timestamp = (
            ticks_of(datetime(2024, 5, 1, 12, 0, 0).replace(minute=self.item_id % 60))
            if self.timestamp is None
            else self.timestamp
        )
        content = encode(make_message(self.item_id)) if self.content is None else self.content
        username = self.username if self.username is not None else f"{USERNAME_PREFIX}{self.item_id}"
        return (
            self.item_id,
            self.status,
            self.file_location,
            self.msg_type,
            message_id,
            timestamp,
            content,
            username,
            0,
            0,
            0,
        )


@dataclass(frozen=True)
class BankPaths:
    db: Path
    wal: Path | None


def _insert(conn: sqlite3.Connection, items: Iterable[SynthItem]) -> None:
    conn.executemany(
        "INSERT INTO CURRENT_INTEGRATION_MESSAGE VALUES (?,?,?,?,?,?,?,?,?,?,?)", [i.row() for i in items]
    )


def make_bank(
    dest_dir: Path,
    items: Iterable[SynthItem],
    *,
    name: str = CACHE_NAME,
    wal_inserts: Iterable[SynthItem] = (),
    wal_deletes: Iterable[int] = (),
    keep_wal: bool = True,
    extra_free_pages: int = 0,
) -> BankPaths:
    """Create ``dest_dir/name`` (+ ``-wal``) holding ``items`` in the main file and the WAL changes."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    work = dest_dir / ".live-synthetic"
    work.mkdir()
    live = work / name
    conn = sqlite3.connect(live, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO CURRENT_METADATA VALUES ('version', 'synthetic')")
        _insert(conn, items)
        if extra_free_pages:
            conn.execute("CREATE TABLE filler (b BLOB)")
            conn.execute("INSERT INTO filler VALUES (zeroblob(?))", (extra_free_pages * 4096,))
            conn.execute("DROP TABLE filler")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _insert(conn, wal_inserts)
        for item_id in wal_deletes:
            conn.execute(
                "DELETE FROM CURRENT_INTEGRATION_MESSAGE WHERE _INTEGRATION_MESSAGE_ID = ?", (item_id,)
            )
        db_out = dest_dir / name
        shutil.copyfile(live, db_out)
        wal_out: Path | None = None
        live_wal = work / (name + "-wal")
        if keep_wal and live_wal.exists() and live_wal.stat().st_size > 0:
            wal_out = dest_dir / (name + "-wal")
            shutil.copyfile(live_wal, wal_out)
    finally:
        conn.close()
        shutil.rmtree(work, ignore_errors=True)
    return BankPaths(db_out, wal_out)


def make_change_request_bank(
    dest_dir: Path, *, name: str = "__~TestChangeCache~__TestAdapter__LPS.db3"
) -> BankPaths:
    """A database without the integration-message table (the "created" change-request kind)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE CURRENT_CHANGE_REQUEST (_ID INTEGER PRIMARY KEY, _BODY BLOB);")
    conn.execute("INSERT INTO CURRENT_CHANGE_REQUEST VALUES (1, x'00')")
    conn.commit()
    conn.close()
    return BankPaths(path, None)


def make_custom_bank(dest_dir: Path, script: str, *, name: str = CACHE_NAME) -> BankPaths:
    """A (non-WAL) database built from an arbitrary synthetic SQL script."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()
    return BankPaths(path, None)


def make_ers_zip(
    zip_path: Path,
    banks: dict[str, BankPaths],
    *,
    store_dir: str = STORE_DIR,
    extra: dict[str, bytes] | None = None,
) -> Path:
    """An ERS-like zip: the banks under the LocalPersistenceStore path, plus a synthetic log member."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SchneiderElectricData/ADMS/Log/synthetic.log", "synthetic log line\n")
        for name, paths in banks.items():
            zf.write(paths.db, f"{store_dir}/{name}")
            if paths.wal is not None:
                zf.write(paths.wal, f"{store_dir}/{name}-wal")
        for member, data in (extra or {}).items():
            zf.writestr(member, data)
    return zip_path


def make_ers_folder(root: Path, banks: dict[str, BankPaths], *, store_dir: str = STORE_DIR) -> Path:
    """The same layout as ``make_ers_zip`` on disk."""
    target = root / store_dir
    target.mkdir(parents=True)
    log_dir = root / "SchneiderElectricData/ADMS/Log"
    log_dir.mkdir(parents=True)
    (log_dir / "synthetic.log").write_text("synthetic log line\n", encoding="utf-8")
    for name, paths in banks.items():
        shutil.copyfile(paths.db, target / name)
        if paths.wal is not None:
            shutil.copyfile(paths.wal, target / (name + "-wal"))
    return root
