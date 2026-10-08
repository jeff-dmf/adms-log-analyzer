"""Write side of the case database: collections, streams, sources and entry batches.

One writer per case, one connection (single-writer model, docs/ARQUITETURA.md section 9.4).
Transactions are explicit: ``begin()`` ... ``commit()``; the pipeline commits once per batch
of bytes, and a rollback undoes the whole open batch.

``entry_fts`` is an external-content FTS5 table over the ``entry_text`` view and has no
triggers, so this module keeps it in sync by hand:

* insert: ``INSERT INTO entry_fts(rowid, msg, cont) SELECT ... FROM entry_text`` over the ids
  of the batch, after the entry rows and their bodies exist (one statement, no round trip of
  the text through Python);
* delete: the FTS5 ``'delete'`` command with the values the index holds, read from
  ``entry_text`` before the rows disappear.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dmslog.parse.records import Record

MINUTE_MS = 60_000
MAX_BATCH_RECORDS = 50_000

FLAG_PARTIAL = 1
FLAG_HAS_CONT = 2
FLAG_TS_OUT_OF_ORDER = 32

SeverityFn = Callable[[int | None, str], int]

_ENTRY_SQL = (
    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev, tpl_id,"
    " flags, msg)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)"
)
_BODY_SQL = "INSERT INTO entry_body(entry_id, cont) VALUES (?,?)"
_FTS_INSERT_SQL = (
    "INSERT INTO entry_fts(rowid, msg, cont) SELECT rowid, msg, cont FROM entry_text"
    " WHERE rowid >= ? AND rowid < ?"
)
_BUCKET_SQL = (
    "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) VALUES (?,?,?,0,?)"
    " ON CONFLICT(source_id, minute, sev, tpl_id) DO UPDATE SET n = n + excluded.n"
)

_SOURCE_FIELDS = frozenset(
    {
        "stream_id", "envelope", "size", "mtime", "zip_crc", "head_hash", "indexed_bytes", "tail_hash",
        "first_ts", "last_ts", "n_entries", "status", "error", "rotation", "member", "file_name",
    }
)  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CollectionRow:
    id: int
    kind: str
    path: str
    ers_guid: str | None
    ers_ts: str | None
    ers_variant: str | None
    ers_group: int | None
    label: str | None


@dataclass(frozen=True)
class SourceRow:
    id: int
    collection_id: int
    stream_id: int | None
    member: str
    file_name: str
    rotation: int | None
    envelope: str
    size: int | None
    mtime: float | None
    zip_crc: int | None
    head_hash: str | None
    indexed_bytes: int
    tail_hash: str | None
    n_entries: int
    status: str
    error: str | None
    stream_key: tuple[str, str | None, int | None, bool] | None  # family, process, pid, sensitive


@dataclass(frozen=True)
class LastEntry:
    id: int
    byte_off: int
    line_no: int


@dataclass
class BatchStats:
    records: int = 0
    lines: int = 0


class IndexWriter:
    """Inserts and removes the rows of one case. Not thread-safe."""

    def __init__(self, conn: sqlite3.Connection, severity: SeverityFn | None = None) -> None:
        self.conn = conn
        conn.isolation_level = None  # explicit transactions (begin/commit)
        self._severity: SeverityFn = severity or (lambda _level, _msg: 4)
        self._next_id = (conn.execute("SELECT coalesce(max(id), 0) FROM entry").fetchone()[0]) + 1
        self._in_tx = False
        self._rows: list[tuple[Any, ...]] = []
        self._bodies: list[tuple[int, str]] = []
        self._buckets: dict[tuple[int, int, int], int] = {}
        self._batch_first_id = self._next_id
        self.prev_ts: int | None = None  # ts of the last record appended (for flag 32)

    # ------------------------------------------------------------------ transactions

    @property
    def buffered(self) -> int:
        return len(self._rows)

    def begin(self) -> None:
        if not self._in_tx:
            self.conn.execute("BEGIN")
            self._in_tx = True

    def commit(self) -> None:
        self.flush()
        if self._in_tx:
            self.conn.execute("COMMIT")
            self._in_tx = False

    def rollback(self) -> None:
        """Discard the open batch (database and buffers)."""
        self._rows.clear()
        self._bodies.clear()
        self._buckets.clear()
        if self._in_tx:
            self.conn.execute("ROLLBACK")
            self._in_tx = False
        self._next_id = self.conn.execute("SELECT coalesce(max(id), 0) FROM entry").fetchone()[0] + 1
        self._batch_first_id = self._next_id

    # ------------------------------------------------------------------ case, collections

    def init_meta(self, name: str, version: str) -> None:
        """Write the case metadata once (no-op when the case already has it)."""
        if self.conn.execute("SELECT 1 FROM meta WHERE key='case_id'").fetchone():
            return
        self.begin()
        rows = [
            ("case_id", uuid.uuid4().hex),
            ("name", name),
            ("created_at", _now()),
            ("dmslog_version", version),
        ]
        self.conn.executemany("INSERT INTO meta(key, value) VALUES (?,?)", rows)
        self.commit()

    def collections(self) -> list[CollectionRow]:
        rows = self.conn.execute(
            "SELECT id, kind, path, ers_guid, ers_ts, ers_variant, ers_group, label"
            " FROM collection ORDER BY id"
        )
        return [CollectionRow(*r) for r in rows]

    def add_collection(
        self,
        *,
        kind: str,
        path: str,
        ers_guid: str | None,
        ers_ts: str | None,
        ers_variant: str | None,
        ers_group: int | None,
        system: str | None,
        machine: str | None,
        label: str | None,
    ) -> int:
        """Insert a collection; ``ers_group`` None means "its own id" (a group of one)."""
        self.begin()
        cur = self.conn.execute(
            "INSERT INTO collection(kind, path, ers_guid, ers_ts, ers_variant, ers_group, machine, system,"
            " label,"
            " added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, path, ers_guid, ers_ts, ers_variant, ers_group, machine, system, label, _now()),
        )
        cid = int(cur.lastrowid or 0)
        if ers_group is None:
            self.conn.execute("UPDATE collection SET ers_group=? WHERE id=?", (cid, cid))
        return cid

    def update_collection_path(self, collection_id: int, path: str) -> None:
        self.begin()
        self.conn.execute("UPDATE collection SET path=? WHERE id=?", (path, collection_id))

    def collection_group(self, collection_id: int) -> int:
        row = self.conn.execute("SELECT ers_group FROM collection WHERE id=?", (collection_id,)).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ streams, sources

    def get_or_create_stream(
        self,
        *,
        collection_group: int,
        family: str,
        process: str | None,
        pid: int | None,
        account: str | None,
        service: str | None,
        sensitive: bool,
    ) -> int:
        """The logical stream of a process; ``.log`` and ``.log.N`` of one process share it."""
        row = self.conn.execute(
            "SELECT id FROM stream WHERE collection_group=? AND family=? AND process IS ? AND pid IS ?"
            " AND sensitive=?",
            (collection_group, family, process, pid, int(sensitive)),
        ).fetchone()
        if row:
            return int(row[0])
        self.begin()
        cur = self.conn.execute(
            "INSERT INTO stream(collection_group, family, process, pid, account, service, sensitive)"
            " VALUES (?,?,?,?,?,?,?)",
            (collection_group, family, process, pid, account, service, int(sensitive)),
        )
        return int(cur.lastrowid or 0)

    def sources(self, collection_id: int) -> dict[str, SourceRow]:
        """All sources of a collection by member path."""
        rows = self.conn.execute(
            "SELECT s.id, s.collection_id, s.stream_id, s.member, s.file_name, s.rotation, s.envelope,"
            " s.size,"
            " s.mtime, s.zip_crc, s.head_hash, s.indexed_bytes, s.tail_hash, s.n_entries, s.status, s.error,"
            " t.family, t.process, t.pid, t.sensitive"
            " FROM source s LEFT JOIN stream t ON t.id = s.stream_id WHERE s.collection_id=?",
            (collection_id,),
        )
        out: dict[str, SourceRow] = {}
        for r in rows:
            key = (r[16], r[17], r[18], bool(r[19])) if r[16] is not None else None
            out[r[3]] = SourceRow(*r[:16], key)
        return out

    def add_source(
        self,
        *,
        collection_id: int,
        stream_id: int | None,
        member: str,
        file_name: str,
        rotation: int | None,
        envelope: str,
        size: int | None,
        mtime: float | None,
        zip_crc: int | None,
        status: str,
        error: str | None = None,
    ) -> int:
        self.begin()
        cur = self.conn.execute(
            "INSERT INTO source(collection_id, stream_id, member, file_name, rotation, envelope, size, mtime,"
            " zip_crc, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                collection_id,
                stream_id,
                member,
                file_name,
                rotation,
                envelope,
                size,
                mtime,
                zip_crc,
                status,
                error,
            ),
        )
        return int(cur.lastrowid or 0)

    def update_source(self, source_id: int, **fields: Any) -> None:
        unknown = set(fields) - _SOURCE_FIELDS
        if unknown:
            raise ValueError(f"unknown source fields: {sorted(unknown)}")
        if not fields:
            return
        self.begin()
        assignments = ", ".join(f"{name}=?" for name in fields)
        self.conn.execute(f"UPDATE source SET {assignments} WHERE id=?", (*fields.values(), source_id))

    def rename_sources(self, renames: dict[int, tuple[str, str, int | None]]) -> None:
        """Move sources to new members (rotation): ``{id: (member, file_name, rotation)}``.

        Done in two steps because (collection_id, member) is unique and the moves can swap names.
        """
        if not renames:
            return
        self.begin()
        for sid in renames:
            self.conn.execute("UPDATE source SET member = member || char(0) || id WHERE id=?", (sid,))
        for sid, (member, file_name, rotation) in renames.items():
            self.conn.execute(
                "UPDATE source SET member=?, file_name=?, rotation=? WHERE id=?",
                (member, file_name, rotation, sid),
            )

    def source_totals(self, source_id: int) -> tuple[int, int | None, int | None]:
        row = self.conn.execute(
            "SELECT count(*), min(ts), max(ts) FROM entry WHERE source_id=?", (source_id,)
        ).fetchone()
        return int(row[0]), row[1], row[2]

    def last_entry(self, source_id: int) -> LastEntry | None:
        """The last record of a source in file order (highest id)."""
        row = self.conn.execute(
            "SELECT id, byte_off, line_no FROM entry"
            " WHERE id = (SELECT max(id) FROM entry WHERE source_id=?)",
            (source_id,),
        ).fetchone()
        return LastEntry(*row) if row else None

    def last_ts(self, source_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT ts FROM entry WHERE id = (SELECT max(id) FROM entry WHERE source_id=?)", (source_id,)
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ removal

    def truncate_from(self, source_id: int, byte_off: int, min_id: int | None = None) -> int:
        """Delete every record of the source at or after ``byte_off`` (resume / tail removal).

        Removes the rows from ``entry``, ``entry_body``, ``entry_key``, the FTS index and the
        ``bucket`` counts. ``min_id`` (an id known to be at or before the cut) only speeds the
        scan. Returns the number of records removed.
        """
        self.begin()
        where = "source_id = ? AND byte_off >= ?"
        params: list[Any] = [source_id, byte_off]
        if min_id is not None:
            where += " AND id >= ?"
            params.append(min_id)
        return self._delete_where(where, params, source_id)

    def delete_source_entries(self, source_id: int) -> int:
        """Delete every record of the source (full reindex)."""
        self.begin()
        return self._delete_where("source_id = ?", [source_id], source_id)

    def _delete_where(self, where: str, params: list[Any], source_id: int) -> int:
        conn = self.conn
        first = conn.execute(f"SELECT min(id), count(*) FROM entry WHERE {where}", params).fetchone()
        if not first[1]:
            return 0
        # Bucket counts first: subtract what the removed records contributed.
        counts = conn.execute(
            f"SELECT ts / {MINUTE_MS}, sev, tpl_id, count(*) FROM entry WHERE {where} AND ts IS NOT NULL"
            " GROUP BY 1, 2, 3",
            params,
        ).fetchall()
        conn.executemany(
            "UPDATE bucket SET n = n - ? WHERE source_id = ? AND minute = ? AND sev = ? AND tpl_id = ?",
            [(n, source_id, minute, sev, tpl) for minute, sev, tpl, n in counts],
        )
        conn.execute("DELETE FROM bucket WHERE source_id = ? AND n <= 0", (source_id,))
        ids = f"SELECT id FROM entry WHERE {where}"
        conn.execute(
            "INSERT INTO entry_fts(entry_fts, rowid, msg, cont)"
            f" SELECT 'delete', rowid, msg, cont FROM entry_text WHERE rowid IN ({ids})",
            params,
        )
        conn.execute(f"DELETE FROM entry_body WHERE entry_id IN ({ids})", params)
        conn.execute(f"DELETE FROM entry_key WHERE entry_id IN ({ids})", params)
        conn.execute(
            "UPDATE stream SET derive_checkpoint = ? WHERE derive_checkpoint > ?"
            " AND id = (SELECT stream_id FROM source WHERE id = ?)",
            (first[0], first[0], source_id),
        )
        conn.execute(f"DELETE FROM entry WHERE {where}", params)
        return int(first[1])

    # ------------------------------------------------------------------ insertion

    def start_source(self, source_id: int, prev_ts: int | None) -> None:
        """Begin appending records of a source; ``prev_ts`` is the ts of the record before the first."""
        self.flush()
        self.prev_ts = prev_ts

    def append(self, source_id: int, records: Iterable[Record], stats: BatchStats | None = None) -> None:
        """Buffer records for insertion (call ``flush`` or ``commit`` to write them)."""
        rows = self._rows
        bodies = self._bodies
        buckets = self._buckets
        severity = self._severity
        prev_ts = self.prev_ts
        next_id = self._next_id
        n = lines = 0
        for r in records:
            ts = r.ts_ms
            flags = FLAG_PARTIAL if r.partial else 0
            cont = r.cont
            if cont is not None:
                flags |= FLAG_HAS_CONT
                bodies.append((next_id, cont))
            sev = severity(r.level, r.msg)
            if ts is not None:
                if prev_ts is not None and ts < prev_ts:
                    flags |= FLAG_TS_OUT_OF_ORDER
                prev_ts = ts
                key = (source_id, ts // MINUTE_MS, sev)
                buckets[key] = buckets.get(key, 0) + 1
            rows.append((next_id, source_id, r.line_no, r.byte_off, r.nlines, ts, r.pid, r.tid, r.level, sev,
                         flags, r.msg))  # fmt: skip
            next_id += 1
            n += 1
            lines += r.nlines
        self.prev_ts = prev_ts
        self._next_id = next_id
        if stats is not None:
            stats.records += n
            stats.lines += lines

    def flush(self) -> None:
        """Write the buffered records (entries, bodies, FTS rows, bucket counts) in the open transaction."""
        if not self._rows:
            return
        self.begin()
        conn = self.conn
        conn.executemany(_ENTRY_SQL, self._rows)
        if self._bodies:
            conn.executemany(_BODY_SQL, self._bodies)
        conn.execute(_FTS_INSERT_SQL, (self._batch_first_id, self._next_id))
        if self._buckets:
            conn.executemany(_BUCKET_SQL, [(s, m, sev, n) for (s, m, sev), n in self._buckets.items()])
        self._rows.clear()
        self._bodies.clear()
        self._buckets.clear()
        self._batch_first_id = self._next_id
