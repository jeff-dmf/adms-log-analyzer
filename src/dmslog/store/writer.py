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

Bookmarks (``store/bookmarks.py``) are user data located by ``(collection, member, line_no)``, tied to
the lineage of their source and identified by a record fingerprint; their ``entry_id`` cache is kept
here, in the same transaction as the records: orphaned before records are deleted, linked again after
records are inserted (same lineage, matching fingerprint), and moved with a source that a rotation
renames. Each hook costs one probe when the case has no bookmarks.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from dmslog.parse.payload import extract_payload, payload_keys
from dmslog.parse.records import Record
from dmslog.parse.signature import signature
from dmslog.store import bookmarks
from dmslog.store.db import LAST_LINE_SQL

if TYPE_CHECKING:
    from dmslog.parse.templates import Catalog, TemplateSet

MINUTE_MS = 60_000
MAX_BATCH_RECORDS = 50_000

FLAG_PARTIAL = 1
FLAG_HAS_CONT = 2
FLAG_REF_SENSITIVE = 4
FLAG_TS_OUT_OF_ORDER = 32
FLAG_HAS_PAYLOAD = 64

SeverityFn = Callable[[int | None, str], int]

_ENTRY_SQL = (
    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev,"
    " flags, fields, msg, tpl_id)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_KEY_SQL = "INSERT OR IGNORE INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)"
# Keys read from a payload (origin 1), written after the template keys: a pair both give stays origin 0.
_PAYLOAD_KEY_SQL = "INSERT OR IGNORE INTO entry_key(key_type, value, entry_id, origin) VALUES (?,?,?,1)"
KEY_ORIGIN_PAYLOAD = 1
_XML_FORMATS = ("xml", "xml_seq")
_UNKNOWN_SQL = (
    "INSERT INTO unknown_sig(family, signature, n, first_entry_id) VALUES (?,?,?,?)"
    " ON CONFLICT(family, signature) DO UPDATE SET n = n + excluded.n,"
    " first_entry_id = min(first_entry_id, excluded.first_entry_id)"
)
_BODY_SQL = "INSERT INTO entry_body(entry_id, cont) VALUES (?,?)"
_FTS_INSERT_SQL = (
    "INSERT INTO entry_fts(rowid, msg, cont) SELECT rowid, msg, cont FROM entry_text"
    " WHERE rowid >= ? AND rowid < ?"
)
_CONFIG_OBSERVED_SQL = (
    "INSERT OR REPLACE INTO config_observed(stream_id, name, value, is_default, entry_id) VALUES (?,?,?,?,?)"
)
# Startup templates of the _common catalog whose `param`/`value` fields feed config_observed
# (docs/ARQUITETURA.md 7.2.1 item 6): GEN-CFG-01 = value read from the Registry, GEN-CFG-03 = default.
CONFIG_OBSERVED_TEMPLATES = {"GEN-CFG-01": 0, "GEN-CFG-03": 1}  # code -> is_default
_PAYLOAD_SQL = (
    "INSERT INTO payload(entry_id, role, direction, peer, operation, msg_id, corr_id, fmt, root, char_off,"
    " char_len, well_formed, truncated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_BUCKET_SQL = (
    "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) VALUES (?,?,?,?,?)"
    " ON CONFLICT(source_id, minute, sev, tpl_id) DO UPDATE SET n = n + excluded.n"
)

_SET_LAST_LINE_SQL = "UPDATE source SET last_line = ? WHERE id = ?"

_BUCKET_SEV_SQL = (
    "INSERT INTO bucket_sev(source_id, minute, sev, n) VALUES (?,?,?,?)"
    " ON CONFLICT(source_id, minute, sev) DO UPDATE SET n = n + excluded.n"
)

_SOURCE_FIELDS = frozenset(
    {
        "stream_id", "envelope", "size", "mtime", "zip_crc", "head_hash", "indexed_bytes", "tail_hash",
        "first_ts", "last_ts", "n_entries", "status", "error", "rotation", "member", "file_name",
    }
)  # fmt: skip


# Removing a collection group (U21) must never let a later insert reuse a removed id: saved views, presets and
# open UI state name sources, streams, events and collections by id. ``meta['id_floor.<table>']`` keeps the
# highest id ever removed and every insert of these tables starts above it.
ID_FLOOR_PREFIX = "id_floor."
ID_FLOOR_TABLES = ("collection", "stream", "source", "entry", "event")
# Per-group counter bumped when entries of the group are deleted (ARQUITETURA 5, "Chaves de meta"); the global
# ``entries_epoch`` of older cases stays as it was and both go into the correlation signatures.
EPOCH_KEY = "entries_epoch"


def id_floor(conn: sqlite3.Connection, table: str) -> int:
    """Highest id ever removed from ``table`` by a collection removal (0 when none)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ID_FLOOR_PREFIX + table,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def next_row_id(conn: sqlite3.Connection, table: str) -> int:
    """The id the next row of ``table`` gets: above every live row and every removed one."""
    if table not in ID_FLOOR_TABLES:
        raise ValueError(f"no id floor for table {table!r}")
    top = conn.execute(f"SELECT coalesce(max(id), 0) FROM {table}").fetchone()[0]  # noqa: S608 - constant
    return max(int(top), id_floor(conn, table)) + 1


def bump_group_epoch(conn: sqlite3.Connection, group: int | None) -> None:
    """Tell the correlation engines that entries of ``group`` were deleted (``entries_epoch.<group>``)."""
    key = EPOCH_KEY if group is None else f"{EPOCH_KEY}.{int(group)}"
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?,"
        " CAST(coalesce((SELECT value FROM meta WHERE key = ?), '0') AS INTEGER) + 1)",
        (key, key),
    )


_DIGIT_CLASSES = str.maketrans("0123456789", "0111111111")  # keeps "0" apart: "0x1f" is hex, "5x1f" is not
_SIG_CACHE_MAX = 200_000
_SIG_CACHE_MAX_MSG = 2048  # signature() reads whitespace-collapsed text, so longer lines skip the cache
_sig_cache: dict[str, str] = {}


def cached_signature(msg: str) -> str:
    """``signature(msg)`` memoized on the message with the digits 1-9 replaced by ``1``.

    The signature depends on digits only as a class (zero or not, because of ``0x``), and the
    replacement keeps the length, so two messages with the same key have the same signature. Most
    unknown records repeat a few shapes that differ in numbers, so this removes most of the regex work.
    """
    if len(msg) > _SIG_CACHE_MAX_MSG:
        return signature(msg)
    key = msg.translate(_DIGIT_CLASSES)
    sig = _sig_cache.get(key)
    if sig is None:
        if len(_sig_cache) >= _SIG_CACHE_MAX:
            _sig_cache.clear()
        sig = _sig_cache[key] = signature(msg)
    return sig


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


@dataclass(frozen=True)
class RemovedGroup:
    group: int
    collections: int
    sources: int
    records: int
    bookmarks: int


@dataclass
class BatchStats:
    records: int = 0
    lines: int = 0


class IndexWriter:
    """Inserts and removes the rows of one case. Not thread-safe."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        severity: SeverityFn | None = None,
        infer_severity: Callable[[Any, Any], int] | None = None,
    ) -> None:
        self.conn = conn
        conn.isolation_level = None  # explicit transactions (begin/commit)
        self._severity: SeverityFn = severity or (lambda _level, _msg: 4)
        self._infer = infer_severity  # (TemplateMatch | None, Record) -> sev; wins over ``severity``
        self._next_id = next_row_id(conn, "entry")
        self._in_tx = False
        self._rows: list[tuple[Any, ...]] = []
        self._bodies: list[tuple[int, str]] = []
        self._buckets: dict[tuple[int, int, int, int], int] = {}
        self._keys: list[tuple[str, str, int]] = []
        self._payload_keys: list[tuple[str, str, int]] = []  # entry_key rows read from payloads (origin 1)
        self._unknown: dict[tuple[str, str], list[int]] = {}  # (family, signature) -> [n, first_entry_id]
        self._config: list[tuple[Any, ...]] = []  # config_observed rows of the open batch
        self._payloads: list[tuple[Any, ...]] = []  # payload rows of the open batch (entry_id first)
        self.payloads_written = 0  # payload rows inserted by this writer (flushed)
        self._stream_id: int | None = None  # stream of the source being appended
        self._tset: TemplateSet | None = None
        self._family: str | None = None
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
        self._keys.clear()
        self._payload_keys.clear()
        self._unknown.clear()
        self._config.clear()
        self._payloads.clear()
        if self._in_tx:
            self.conn.execute("ROLLBACK")
            self._in_tx = False
        self._next_id = next_row_id(self.conn, "entry")
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

    def sync_templates(self, catalog: Catalog) -> dict[str, int]:
        """Make the ``template`` table hold every catalog template; bind their ids to the catalog.

        Ids are stable per code across runs. A changed definition updates the row in place; when
        ``fts`` changes for a template that already has records, the FTS index is rebuilt so it
        stays consistent with the ``entry_text`` view. Returns ``{code: template.id}``.
        """
        self.begin()
        conn = self.conn
        existing = {
            r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT code, id, def_version, fts FROM template")
        }
        rebuild = False
        for t in catalog.templates():
            row = (t.family, t.cls, t.sev, t.title, t.pattern, int(t.fts), t.def_version)
            old = existing.get(t.code)
            if old is None:
                conn.execute(
                    "INSERT INTO template(code, family, class, sev, title, pattern, fts, def_version)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (t.code, *row),
                )
            elif old[1] != t.def_version or old[2] != int(t.fts):
                if (
                    old[2] != int(t.fts)
                    and conn.execute("SELECT 1 FROM entry WHERE tpl_id = ? LIMIT 1", (old[0],)).fetchone()
                ):
                    rebuild = True
                    conn.execute("UPDATE template SET fts=? WHERE id=?", (int(t.fts), old[0]))
                conn.execute(
                    "UPDATE template SET family=?, class=?, sev=?, title=?, pattern=?, fts=?, def_version=?"
                    " WHERE id=?",
                    (*row, old[0]),
                )
        if rebuild:
            conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
        mapping = {code: int(tid) for code, tid in conn.execute("SELECT code, id FROM template")}
        catalog.bind_ids(mapping)
        return mapping

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
        cid = next_row_id(self.conn, "collection")
        self.conn.execute(
            "INSERT INTO collection(id, kind, path, ers_guid, ers_ts, ers_variant, ers_group, machine,"
            " system, label, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, kind, path, ers_guid, ers_ts, ers_variant, ers_group, machine, system, label, _now()),
        )
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
        sid = next_row_id(self.conn, "stream")
        self.conn.execute(
            "INSERT INTO stream(id, collection_group, family, process, pid, account, service, sensitive)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, collection_group, family, process, pid, account, service, int(sensitive)),
        )
        return sid

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
        source_id = next_row_id(self.conn, "source")
        self.conn.execute(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, rotation, envelope, size,"
            " mtime, zip_crc, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
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
        return source_id

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
        bookmarks.move_members(self.conn, {sid: move[0] for sid, move in renames.items()})
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

    def _refresh_last_line(self, source_id: int) -> None:
        """Recompute ``source.last_line`` from the records left (NULL when none): after any deletion."""
        self.conn.execute(
            f"UPDATE source SET last_line = ({LAST_LINE_SQL.format(source='?')}) WHERE id = ?",  # noqa: S608
            (source_id, source_id),
        )

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

    # ------------------------------------------------------------------ installation configuration

    def replace_config_params(self, collection_group: int, rows: list[tuple[Any, ...]]) -> None:
        """Replace ``config_param`` for the products present in ``rows``.

        Each row is ``(product, scope, key_path, name, value, source_file, layer, layer_label,
        effective, status)``. Products absent from ``rows`` keep what they had (re-ingest of a
        collection that does not carry the Registry must not wipe an earlier reading).
        """
        if not rows:
            return
        self.begin()
        conn = self.conn
        for product in sorted({r[0] for r in rows}):
            conn.execute(
                "DELETE FROM config_param WHERE collection_group=? AND product=?", (collection_group, product)
            )
        conn.executemany(
            "INSERT OR REPLACE INTO config_param(collection_group, product, scope, key_path, name, value,"
            " source_file, layer, layer_label, effective, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(collection_group, *r) for r in rows],
        )

    # ------------------------------------------------------------------ removal

    def truncate_from(self, source_id: int, byte_off: int, min_id: int | None = None) -> int:
        """Delete every record of the source at or after ``byte_off`` (resume / tail removal).

        Removes the rows from ``entry``, ``entry_body``, ``entry_key``, the FTS index and the
        ``bucket`` and ``bucket_sev`` counts. ``min_id`` (an id known to be at or before the cut) only
        speeds the scan. Returns the number of records removed.
        """
        self.begin()
        where = "source_id = ? AND byte_off >= ?"
        params: list[Any] = [source_id, byte_off]
        if min_id is not None:
            where += " AND id >= ?"
            params.append(min_id)
        return self._delete_where(where, params, source_id, byte_off)

    def delete_source_entries(self, source_id: int) -> int:
        """Delete every record of the source (full reindex)."""
        self.begin()
        return self._delete_where("source_id = ?", [source_id], source_id, 0)

    def _delete_where(self, where: str, params: list[Any], source_id: int, from_byte_off: int) -> int:
        """Delete the records matching ``where``: those of ``source_id`` at or after ``from_byte_off``."""
        conn = self.conn
        first = conn.execute(f"SELECT min(id), count(*) FROM entry WHERE {where}", params).fetchone()
        if not first[1]:
            return 0
        bookmarks.detach_entries(conn, source_id, from_byte_off)
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
        by_sev: dict[tuple[int, int], int] = {}
        for minute, sev, _tpl, n in counts:
            by_sev[(minute, sev)] = by_sev.get((minute, sev), 0) + n
        conn.executemany(
            "UPDATE bucket_sev SET n = n - ? WHERE source_id = ? AND minute = ? AND sev = ?",
            [(n, source_id, minute, sev) for (minute, sev), n in by_sev.items()],
        )
        conn.execute("DELETE FROM bucket_sev WHERE source_id = ? AND n <= 0", (source_id,))
        stale = self._remove_unknown(where, params, source_id)
        ids = f"SELECT id FROM entry WHERE {where}"
        conn.execute(
            "INSERT INTO entry_fts(entry_fts, rowid, msg, cont)"
            f" SELECT 'delete', rowid, msg, cont FROM entry_text WHERE rowid IN ({ids})",
            params,
        )
        conn.execute(f"DELETE FROM entry_body WHERE entry_id IN ({ids})", params)
        conn.execute(f"DELETE FROM entry_key WHERE entry_id IN ({ids})", params)
        conn.execute(f"DELETE FROM config_observed WHERE entry_id IN ({ids})", params)
        self._delete_payloads(ids, params)
        # Links incident to the removed records (any kind) go with them; the epoch tells the correlation that
        # entries were removed even when the sources look the same afterwards (a reindex).
        conn.execute(f"DELETE FROM link WHERE a_entry_id IN ({ids})", params)
        conn.execute(f"DELETE FROM link WHERE b_entry_id IN ({ids})", params)
        group = conn.execute(
            "SELECT st.collection_group FROM source s JOIN stream st ON st.id = s.stream_id WHERE s.id = ?",
            (source_id,),
        ).fetchone()
        bump_group_epoch(conn, group[0] if group else None)  # only this group's correlation is recomputed
        conn.execute(
            "UPDATE stream SET derive_checkpoint = ? WHERE derive_checkpoint > ?"
            " AND id = (SELECT stream_id FROM source WHERE id = ?)",
            (first[0], first[0], source_id),
        )
        conn.execute(f"DELETE FROM entry WHERE {where}", params)
        self._refresh_last_line(source_id)  # same transaction as the deletion
        if stale:
            self._repair_unknown_first(stale)
        return int(first[1])

    def refresh_payload_keys(self, source_id: int) -> int:
        """Rewrite the payload keys (``entry_key`` origin 1) of the records of one source from its stored
        payloads, without reindexing it; returns the number of key rows written.

        Used once per source when only the key extraction changed (``ingest/pipeline.py``,
        ``KEY_ONLY_UPGRADES``). Reads the ``payload`` rows of the source (offsets) and the text of their
        records; linear in the payloads of the source, in batches. Template keys (origin 0) stay.
        """
        self.flush()
        self.begin()
        conn = self.conn
        conn.execute(
            "DELETE FROM entry_key WHERE origin = ? AND entry_id IN"
            " (SELECT p.entry_id FROM payload p JOIN entry e ON e.id = p.entry_id WHERE e.source_id = ?)",
            (KEY_ORIGIN_PAYLOAD, source_id),
        )
        cur = conn.execute(
            "SELECT p.entry_id, p.char_off, p.char_len, p.fmt, e.msg, b.cont FROM payload p"
            " JOIN entry e ON e.id = p.entry_id LEFT JOIN entry_body b ON b.entry_id = e.id"
            " WHERE e.source_id = ? ORDER BY p.id",
            (source_id,),
        )
        written = 0
        while True:
            rows = cur.fetchmany(1000)
            if not rows:
                break
            batch: list[tuple[str, str, int]] = []
            for entry_id, off, length, fmt, msg, cont in rows:
                if fmt not in _XML_FORMATS or off is None or length is None:
                    continue
                text = f"{msg}\n{cont}" if cont is not None else msg
                batch.extend((k, v, entry_id) for k, v in payload_keys(text, off, off + length))
            if batch:
                written += conn.executemany(_PAYLOAD_KEY_SQL, batch).rowcount
        return written

    def _delete_payloads(self, ids: str, params: list[Any]) -> None:
        """Remove the payload rows of the removed records and the references other rows hold to them.

        ``ref_entry_id`` (the announcing record) and ``pair_id`` (the paired payload) of the surviving rows
        become NULL; ``derive/payloads.py`` recomputes the group because the epoch changes.
        """
        conn = self.conn
        if conn.execute("SELECT 1 FROM payload LIMIT 1").fetchone() is None:
            return
        conn.execute(f"UPDATE payload SET ref_entry_id = NULL WHERE ref_entry_id IN ({ids})", params)
        conn.execute(
            "UPDATE payload SET pair_id = NULL WHERE pair_id IN"
            f" (SELECT id FROM payload WHERE entry_id IN ({ids}))",
            params,
        )
        conn.execute(f"DELETE FROM payload WHERE entry_id IN ({ids})", params)

    def _remove_unknown(self, where: str, params: list[Any], source_id: int) -> list[tuple[str, set[str]]]:
        """Subtract the removed records from ``unknown_sig``.

        Returns the (family, signatures) whose ``first_entry_id`` pointed at a removed record and
        still have records; ``_repair_unknown_first`` fixes them once the rows are gone.
        """
        conn = self.conn
        row = conn.execute(
            "SELECT t.family FROM source s JOIN stream t ON t.id = s.stream_id WHERE s.id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return []
        family = row[0]
        counts: dict[str, int] = {}
        for (msg,) in conn.execute(f"SELECT msg FROM entry WHERE {where} AND tpl_id = 0", params):
            sig = cached_signature(msg)
            if sig:
                counts[sig] = counts.get(sig, 0) + 1
        if not counts:
            return []
        stale: set[str] = set()
        ids = f"SELECT id FROM entry WHERE {where}"
        for sig, n in counts.items():
            r = conn.execute(
                "SELECT n, first_entry_id FROM unknown_sig WHERE family=? AND signature=?", (family, sig)
            ).fetchone()
            if r is None:
                continue
            if r[0] - n <= 0:
                conn.execute("DELETE FROM unknown_sig WHERE family=? AND signature=?", (family, sig))
                continue
            conn.execute("UPDATE unknown_sig SET n = n - ? WHERE family=? AND signature=?", (n, family, sig))
            if conn.execute(
                f"SELECT 1 FROM entry WHERE id = ? AND id IN ({ids})", [r[1], *params]
            ).fetchone():
                stale.add(sig)
        return [(family, stale)] if stale else []

    def _repair_unknown_first(self, stale: list[tuple[str, set[str]]]) -> None:
        """Recompute ``first_entry_id`` for signatures whose first record was removed."""
        conn = self.conn
        for family, sigs in stale:
            todo = set(sigs)
            for entry_id, msg in conn.execute(
                "SELECT e.id, e.msg FROM entry e JOIN source s ON s.id = e.source_id"
                " JOIN stream t ON t.id = s.stream_id WHERE t.family = ? AND e.tpl_id = 0 ORDER BY e.id",
                (family,),
            ):
                sig = cached_signature(msg)
                if sig in todo:
                    todo.discard(sig)
                    conn.execute(
                        "UPDATE unknown_sig SET first_entry_id=? WHERE family=? AND signature=?",
                        (entry_id, family, sig),
                    )
                    if not todo:
                        break
            for sig in todo:  # defensive: counts said records remain, none were found
                conn.execute("DELETE FROM unknown_sig WHERE family=? AND signature=?", (family, sig))

    def remove_group(self, group: int) -> RemovedGroup | None:
        """Remove one collection group (a capture: its Enterprise and Sensitive zips, or a folder or file) and
        everything derived from it, in ONE transaction; None when the case has no such group (U21).

        Gone: records (with bodies, FTS rows, keys, payloads, buckets, unknown signatures and every link that
        touches them), events, statistics samples, observed and Registry configuration, streams, sources,
        collections, the group's bookmarks and its ``meta`` keys (derivation checkpoints, correlation
        signatures, epoch, payload extraction marks). The other groups are not touched: their signatures do
        not change, so nothing of theirs is derived again. Removed ids are never reused (``id_floor``).
        """
        self.flush()
        self.begin()
        conn = self.conn
        in_group = "SELECT id FROM collection WHERE ers_group = ?"
        cids = [r[0] for r in conn.execute(f"{in_group} ORDER BY id", (group,))]
        if not cids:
            self.rollback()
            return None
        sources = [
            r[0] for r in conn.execute(f"SELECT id FROM source WHERE collection_id IN ({in_group})", (group,))
        ]
        streams = [r[0] for r in conn.execute("SELECT id FROM stream WHERE collection_group = ?", (group,))]
        tops = {
            "collection": max(cids),
            "source": max(sources, default=0),
            "stream": max(streams, default=0),
            "entry": conn.execute(
                "SELECT coalesce(max(e.id), 0) FROM entry e JOIN source s ON s.id = e.source_id"
                f" WHERE s.collection_id IN ({in_group})",
                (group,),
            ).fetchone()[0],
            "event": conn.execute(
                "SELECT coalesce(max(id), 0) FROM event"
                " WHERE stream_id IN (SELECT id FROM stream WHERE collection_group = ?)",
                (group,),
            ).fetchone()[0],
        }
        records = 0
        for source_id in sources:  # the regular deletion path: FTS, buckets, keys, payloads, links, unknowns
            records += self._delete_where("source_id = ?", [source_id], source_id, 0)
        removed_bookmarks = conn.execute(
            f"DELETE FROM bookmark WHERE collection_id IN ({in_group})", (group,)
        ).rowcount
        by_stream = "SELECT id FROM stream WHERE collection_group = ?"
        for table in ("event", "stat_sample", "config_observed"):
            conn.execute(f"DELETE FROM {table} WHERE stream_id IN ({by_stream})", (group,))  # noqa: S608
        by_source = f"SELECT id FROM source WHERE collection_id IN ({in_group})"
        for table in ("bucket", "bucket_sev"):
            conn.execute(f"DELETE FROM {table} WHERE source_id IN ({by_source})", (group,))  # noqa: S608
        conn.execute(f"DELETE FROM source WHERE collection_id IN ({in_group})", (group,))
        conn.execute("DELETE FROM stream WHERE collection_group = ?", (group,))
        conn.execute("DELETE FROM config_param WHERE collection_group = ?", (group,))
        conn.execute("DELETE FROM error_config WHERE collection_group = ?", (group,))
        conn.execute("DELETE FROM collection WHERE ers_group = ?", (group,))
        keys = [f"{p}{group}" for p in ("correlate.", "correlate.links.", "payloads.", f"{EPOCH_KEY}.")]
        keys += [f"payload_extraction_version.{sid}" for sid in sources]
        conn.executemany("DELETE FROM meta WHERE key = ?", [(k,) for k in keys])
        for sid in streams:  # derive.<stream>.<chain>: '/' is the character after '.'
            conn.execute("DELETE FROM meta WHERE key >= ? AND key < ?", (f"derive.{sid}.", f"derive.{sid}/"))
        for table, top in tops.items():
            if top > id_floor(conn, table):
                floor = (ID_FLOOR_PREFIX + table, str(top))
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", floor)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('ingest_generation',"
            " CAST(coalesce((SELECT value FROM meta WHERE key = 'ingest_generation'), '0') AS INTEGER) + 1)"
        )
        self.commit()
        self._next_id = next_row_id(conn, "entry")
        self._batch_first_id = self._next_id
        return RemovedGroup(group, len(cids), len(sources), records, removed_bookmarks)

    # ------------------------------------------------------------------ insertion

    def start_source(
        self,
        source_id: int,
        prev_ts: int | None,
        templates: TemplateSet | None = None,
        family: str | None = None,
    ) -> None:
        """Begin appending records of a source; ``prev_ts`` is the ts of the record before the first.

        ``templates`` (a catalog set for the source's family and sensitivity) classifies the records;
        without it every record is stored as unknown (tpl_id 0). ``family`` names the unknown
        signatures (none are stored when it is None).
        """
        self.flush()
        self.prev_ts = prev_ts
        row = self.conn.execute("SELECT stream_id FROM source WHERE id=?", (source_id,)).fetchone()
        self._stream_id = row[0] if row else None
        self._tset = templates
        self._family = family

    def append(self, source_id: int, records: Iterable[Record], stats: BatchStats | None = None) -> None:
        """Buffer records for insertion (call ``flush`` or ``commit`` to write them)."""
        rows = self._rows
        bodies = self._bodies
        buckets = self._buckets
        severity = self._severity
        infer = self._infer
        tset = self._tset
        classify = tset.classify if tset is not None and len(tset) else None
        payload_defaults = tset.payload_defaults if tset is not None else {}
        payloads_out = self._payloads
        family = self._family
        keys_out = self._keys
        payload_keys_out = self._payload_keys
        unknown = self._unknown
        dumps = json.dumps
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
            m = classify(r) if classify is not None else None
            fields_json = None
            tpl = 0
            if m is not None:
                tpl = m.tpl_id
                sev = infer(m, r) if infer is not None else (m.sev_hint or severity(r.level, r.msg))
                if m.fields:
                    fields_json = dumps(m.fields, ensure_ascii=False, separators=(",", ":"), default=str)
                if m.code in CONFIG_OBSERVED_TEMPLATES and self._stream_id is not None:
                    param = m.fields.get("param")
                    if param:
                        self._config.append(
                            (self._stream_id, param, m.fields.get("value"), CONFIG_OBSERVED_TEMPLATES[m.code],
                             next_id)
                        )  # fmt: skip
                if m.keys:
                    for key_type, value in m.keys:
                        keys_out.append((key_type, value, next_id))
                if m.refs_sensitive:
                    flags |= FLAG_REF_SENSITIVE
                if m.payload is not None:
                    info = extract_payload(m.payload, r.msg, cont, m.match_end, r.partial, payload_defaults)
                    if info is not None:
                        flags |= FLAG_HAS_PAYLOAD
                        payloads_out.append((next_id, *info.row()))
                        for key_type, value in info.keys:
                            payload_keys_out.append((key_type, value, next_id))
            else:
                sev = infer(None, r) if infer is not None else severity(r.level, r.msg)
                if family is not None:
                    sig = cached_signature(r.msg)
                    if sig:
                        slot = unknown.get((family, sig))
                        if slot is None:
                            unknown[(family, sig)] = [1, next_id]
                        else:
                            slot[0] += 1
            if ts is not None:
                if prev_ts is not None and ts < prev_ts:
                    flags |= FLAG_TS_OUT_OF_ORDER
                prev_ts = ts
                key = (source_id, ts // MINUTE_MS, sev, tpl)
                buckets[key] = buckets.get(key, 0) + 1
            rows.append((next_id, source_id, r.line_no, r.byte_off, r.nlines, ts, r.pid, r.tid, r.level, sev,
                         flags, fields_json, r.msg, tpl))  # fmt: skip
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
        # source.last_line (schema 7): ids only grow, so the last buffered row of a source is its highest id.
        last_lines = {r[1]: r[2] + r[4] - 1 for r in self._rows}
        conn.executemany(_SET_LAST_LINE_SQL, [(line, sid) for sid, line in last_lines.items()])
        if self._bodies:
            conn.executemany(_BODY_SQL, self._bodies)
        conn.execute(_FTS_INSERT_SQL, (self._batch_first_id, self._next_id))
        if self._keys:
            conn.executemany(_KEY_SQL, self._keys)
        if self._payload_keys:
            conn.executemany(_PAYLOAD_KEY_SQL, self._payload_keys)
        if self._config:
            conn.executemany(_CONFIG_OBSERVED_SQL, self._config)
        if self._payloads:
            conn.executemany(_PAYLOAD_SQL, self._payloads)
            self.payloads_written += len(self._payloads)
        bookmarks.attach_entries(conn, ((r[0], r[1], r[2], r[3], r[5], r[12]) for r in self._rows))
        if self._unknown:
            conn.executemany(_UNKNOWN_SQL, [(f, sg, v[0], v[1]) for (f, sg), v in self._unknown.items()])
        if self._buckets:
            conn.executemany(
                _BUCKET_SQL, [(s, m, sev, tpl, n) for (s, m, sev, tpl), n in self._buckets.items()]
            )
            by_sev: dict[tuple[int, int, int], int] = {}
            for (s, m, sev, _tpl), n in self._buckets.items():
                by_sev[(s, m, sev)] = by_sev.get((s, m, sev), 0) + n
            conn.executemany(_BUCKET_SEV_SQL, [(s, m, sev, n) for (s, m, sev), n in by_sev.items()])
        self._rows.clear()
        self._bodies.clear()
        self._buckets.clear()
        self._keys.clear()
        self._payload_keys.clear()
        self._unknown.clear()
        self._config.clear()
        self._payloads.clear()
        self._batch_first_id = self._next_id
