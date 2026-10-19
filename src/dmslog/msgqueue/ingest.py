"""Reading the message queue databases of one collection into the case database (issue #74, Q5).

``read_collection`` runs AFTER ``run_ingest`` (it never changes it): the queue therefore cannot delay the
``first_ready`` of a partial open. For every ``LocalPersistenceStore/*/*.db3`` of the collection it

1. compares the files' fingerprint with the stored one and skips an unchanged, error-free bank (no
   temporary folder, no copy);
2. otherwise lets ``lps.read_bank`` copy, read and clean up the bank, and stores the result with
   ``store.queue.replace_bank`` (one transaction per bank).

A bank that fails (corrupt, too large, ...) is stored as an error row and never stops the others.
Cancellation raises ``ReadCancelled`` (the temporary folder is already gone); banks stored before it stay.

Public API:

* ``read_collection(conn, case_dir, path, collection_id, *, limits=lps.Limits(), progress=None,
  cancelled=None) -> QueueSummary``
* ``sweep_tmp(case_dir) -> list[str]``: removes ``.queue-tmp-*`` folders left by a crashed process.

``progress`` receives ``QueueProgress`` events (``stage == "queue"``, the file NAME only, never a path).
Whatever the callback raises propagates after the temporary folder is deleted, which is how a job's
``JobCancelledError`` stops the copy. This module does not switch the case option on: the caller does
(``store.queue.set_enabled``).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..store import queue as store
from . import lps
from .lps import ReadCancelled

log = logging.getLogger(__name__)

STAGE = "queue"


@dataclass(frozen=True)
class QueueProgress:
    """One progress event of the queue stage. ``status``: ``queue_start`` | ``queue_progress`` |
    ``queue_done``; ``outcome`` (only on ``queue_done``): ``ready`` | ``not_a_queue`` | ``error`` |
    ``unchanged``."""

    status: str
    source: str  # file name only
    bytes_done: int
    bytes_total: int
    outcome: str | None = None
    stage: str = STAGE


@dataclass(frozen=True)
class QueueSummary:
    """What the collection holds after the reading (counted from the case database)."""

    dbs: int = 0
    items: int = 0
    pending: int = 0
    errors: int = 0
    unchanged: int = 0  # banks skipped because their fingerprint did not change (this run only)

    def as_dict(self) -> dict[str, int]:
        """The ``queue`` member of the job's ``done`` event."""
        return {"dbs": self.dbs, "items": self.items, "pending": self.pending, "errors": self.errors}


def sweep_tmp(case_dir: str | Path) -> list[str]:
    """Delete the ``.queue-tmp-*`` folders of ``case_dir`` older than one hour; returns their names."""
    return lps.sweep_tmp_dir(Path(case_dir))


def _to_bank_in(result: lps.BankResult, read_at: str) -> store.BankIn:
    return store.BankIn(
        cache=result.cache,
        adapter=result.adapter,
        db_size=result.db_size,
        wal_size=result.wal_size,
        fingerprint=result.fingerprint,
        status=result.status,
        read_at=read_at,
        snapshot=result.snapshot,
        error=result.error,
        items=[
            store.ItemIn(
                item_id=r.item_id,
                message_id=r.message_id,
                msg_type=r.msg_type,
                status=r.status,
                state=r.state,
                file_location=r.file_location,
                ts=r.ts,
                size=r.size,
                body=r.body,
            )
            for r in result.items
        ],
        warnings=tuple(result.warnings),
        skipped_rows=result.skipped_rows,
    )


def _store_bank(conn: sqlite3.Connection, collection_id: int, result: lps.BankResult, read_at: str) -> str:
    """Store one reading; a failure to store becomes an error row. Returns the stored status."""
    try:
        store.replace_bank(conn, collection_id, result.member, _to_bank_in(result, read_at))
        return result.status
    except (sqlite3.Error, store.QueueInputError) as exc:
        log.warning("queue: storing a bank failed (%s)", type(exc).__name__)
    failed = lps.BankResult(
        member=result.member,
        cache=result.cache,
        adapter=result.adapter,
        db_size=result.db_size,
        wal_size=result.wal_size,
        fingerprint=result.fingerprint,
        status="error",
        error="read_failed",
    )
    try:
        store.replace_bank(conn, collection_id, result.member, _to_bank_in(failed, read_at))
    except (sqlite3.Error, store.QueueInputError):
        log.warning("queue: the error row of a bank could not be stored either")
    return "error"


def _totals(conn: sqlite3.Connection, collection_id: int, unchanged: int) -> QueueSummary:
    if not store.has_queue(conn):
        return QueueSummary(unchanged=unchanged)
    dbs, items, errors = conn.execute(
        "SELECT count(*), coalesce(sum(items), 0), coalesce(sum(status = 'error'), 0)"
        " FROM queue_db WHERE collection_id = ?",
        (collection_id,),
    ).fetchone()
    pending = conn.execute(
        "SELECT count(*) FROM queue_item i JOIN queue_db d ON d.id = i.db_id"
        " WHERE d.collection_id = ? AND i.state = 'pending'",
        (collection_id,),
    ).fetchone()[0]
    return QueueSummary(dbs=dbs, items=items, pending=pending, errors=errors, unchanged=unchanged)


def read_collection(
    conn: sqlite3.Connection,
    case_dir: str | Path,
    path: str | Path,
    collection_id: int,
    *,
    limits: lps.Limits = lps.Limits(),  # noqa: B008 (frozen)
    progress: Callable[[QueueProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> QueueSummary:
    """Read every queue database of the ERS zip or folder ``path`` (collection ``collection_id``).

    ``conn`` is the case writer's connection (no open transaction is needed; an open one is committed).
    Banks whose files did not change since the last reading are skipped. Errors of a bank are stored on
    the bank (``queue_db.status == 'error'``) and counted in ``errors``; discovery errors of the whole
    archive (missing path, unsafe zip) propagate like they do in ``run_ingest``.
    """
    case_dir = Path(case_dir)
    sweep_tmp(case_dir)
    banks = lps.discover(path)
    if conn.in_transaction:
        conn.commit()
    unchanged = 0
    budget = lps.CopyBudget(limits.max_run_bytes)  # one cap for every byte this reading copies

    def emit(status: str, name: str, done: int, total: int, outcome: str | None = None) -> None:
        if progress is not None:
            progress(QueueProgress(status, name, done, total, outcome))

    for files in banks:
        if cancelled is not None and cancelled():
            raise ReadCancelled
        name = files.db.file_name
        total = files.db.size
        known = store.fingerprint_of(conn, collection_id, files.member)
        if known is not None and known[0] == lps.fingerprint_of(files) and known[1] != "error":
            unchanged += 1
            emit("queue_done", name, total, total, "unchanged")
            continue
        emit("queue_start", name, 0, total)

        def copy_progress(file_name: str, done: int, size: int, _name: str = name) -> None:
            emit("queue_progress", file_name, done, size)

        result = lps.read_bank(
            files, case_dir, limits=limits, progress=copy_progress, cancelled=cancelled, budget=budget
        )
        read_at = datetime.now().isoformat(timespec="seconds")
        outcome = _store_bank(conn, collection_id, result, read_at)
        emit("queue_done", name, total, total, outcome)
    return _totals(conn, collection_id, unchanged)


__all__ = ["STAGE", "QueueProgress", "QueueSummary", "ReadCancelled", "read_collection", "sweep_tmp"]
