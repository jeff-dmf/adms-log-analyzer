"""Store side of the adapters' message queue (``LocalPersistenceStore/*.db3``; issue #74).

Three tables (migration 8, ``db._migrate_to_8``): ``queue_db`` (one queue database read in a collection),
``queue_item`` (one ``CURRENT_INTEGRATION_MESSAGE`` row) and ``queue_body`` (the raw BLOB, only for the
preview). The queue is a parallel source: nothing here touches ``entry``, ``entry_fts``, ``entry_key``,
``payload``, ``event`` or ``link``; it meets the log only through :func:`log_links`, a read.

Rules:

* ``queue_body`` is never read by a list: :func:`get_body` reads ONE item per call, every other query lists
  fixed columns.
* The adapter's ``USERNAME`` column has no place in the schema and is never taken from the caller.
* A case still at schema version 7 (the server opens it read-only and does not migrate) has no queue
  tables: every read answers "option off and empty" instead of failing (:func:`has_queue`).
* Writes (:func:`replace_bank`, :func:`delete_queue`, :func:`set_enabled`) run on the writer's connection;
  ``replace_bank`` and ``delete_queue`` are one transaction each.

The values arrive already normalised (docs/notas/fila-mensagens-desenho.md 1.4); the store only checks the
shape it depends on (``state``) and the size limits.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .query import INT64_MAX, INT64_MIN, PAGE_TIMEOUT_S, Cursor, QueryInputError, read_guard

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_BODY_BYTES = 8 * 1024 * 1024  # a BLOB above this keeps its size but no body (preview "too large")
MAX_BANK_BODY_BYTES = 256 * 1024 * 1024  # sum of the bodies stored for one bank
MAX_ITEMS = 200_000  # items kept per bank; the rest is ignored
DEFAULT_LIMIT = 200
MAX_LIMIT = 1_000
MAX_FILTER_VALUES = 64  # ids per collection/db filter
MAX_TYPE_VALUES = 32
LINK_COUNT_CAP = 1_000  # log records counted per key

ENABLED_KEY = "queue.enabled"
STATES = ("pending", "sent", "other")
BANK_STATUSES = ("ready", "not_a_queue", "error")
SNAPSHOTS = ("wal_applied", "no_wal", "wal_failed")

_TABLES = ("queue_db", "queue_item", "queue_body")
_GUID = re.compile(r"^\{?([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\}?$")


class QueueInputError(QueryInputError):
    messages = {
        "bad_limit": "limit must be an integer from 1 to {limit}",
        "bad_state": "state must be a list of pending, sent or other",
        "bad_type": "type must be a list of up to {limit} message types",
        "bad_filter": "collection and db must be lists of up to {limit} integers",
        "bad_query": "q must be a message id (GUID) or an item id (digits)",
        "bad_bank": "invalid message-queue bank: {reason}",
    }


# --------------------------------------------------------------------------------- input records


@dataclass(slots=True)
class ItemIn:
    """One queue row ready to store. ``body`` is the raw BLOB (None for none)."""

    item_id: int
    message_id: str | None
    msg_type: str | None
    status: str | None
    state: str
    file_location: str | None
    ts: int | None
    size: int
    body: bytes | None = None


@dataclass(slots=True)
class BankIn:
    """What :func:`replace_bank` stores for one ``.db3`` of a collection.

    ``status == 'error'`` (or ``not_a_queue``) stores the ``queue_db`` row only and drops the old items.
    """

    cache: str
    adapter: str | None
    db_size: int
    wal_size: int | None
    fingerprint: str
    status: str
    read_at: str
    snapshot: str | None = None
    error: str | None = None
    items: Iterable[ItemIn] = field(default_factory=tuple)
    warnings: tuple[str, ...] = ()  # codes the reader reported (live_copy_unstable, ...), kept with the bank
    skipped_rows: int = 0  # rows the reader could not use


@dataclass(slots=True)
class QueuePage:
    items: list[dict[str, Any]]
    next: Cursor | None  # cursor of the last row when more rows follow, else None
    counts: dict[str, int]  # pending/sent/other under the filters, ignoring the state filter and the cursor


# -------------------------------------------------------------------------------------- helpers


def has_queue(conn: sqlite3.Connection) -> bool:
    """True when the case database has the queue tables (schema version 8 or later)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)", _TABLES
    ).fetchall()
    return len(rows) == len(_TABLES)


def is_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ENABLED_KEY,)).fetchone()
    return row is not None and row[0] == "1"


def set_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    """Record the case option (``meta['queue.enabled']`` is ``"1"`` or absent). Caller commits."""
    if enabled:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, '1')", (ENABLED_KEY,))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (ENABLED_KEY,))


def normalize_guid(value: str) -> str | None:
    """Lower case, no braces; None when ``value`` is not a GUID."""
    m = _GUID.match(value.strip()) if isinstance(value, str) else None
    return m.group(1).lower() if m else None


def _name(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _preview(body_stored: int, size: int) -> str:
    if body_stored:
        return "available"
    return "too_large" if size > MAX_BODY_BYTES else "no_body"


_GROUP = "coalesce(c.ers_group, c.id)"


# ------------------------------------------------------------------------------------- writes


def replace_bank(conn: sqlite3.Connection, collection_id: int, member: str, bank: BankIn) -> int:
    """Store the reading of one bank, replacing the previous reading of ``(collection_id, member)``.

    One ``BEGIN IMMEDIATE`` transaction (committed here; the connection must have none open): the old
    items and bodies go, the ``queue_db`` row keeps its id and is updated (or inserted), and the new items
    are written. A bank that is not ``ready`` keeps only its row. Returns the ``queue_db`` id. Limits:
    a body above ``MAX_BODY_BYTES`` or past ``MAX_BANK_BODY_BYTES`` for the bank is not stored
    (``body_stored = 0``); items past ``MAX_ITEMS`` are ignored.
    """
    if bank.status not in BANK_STATUSES:
        raise QueueInputError("bad_bank", "$.status", reason=f"status {bank.status!r}")
    if bank.snapshot is not None and bank.snapshot not in SNAPSHOTS:
        raise QueueInputError("bad_bank", "$.snapshot", reason=f"snapshot {bank.snapshot!r}")
    if conn.in_transaction:
        raise RuntimeError("replace_bank needs a connection without an open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id FROM queue_db WHERE collection_id = ? AND member = ?", (collection_id, member)
        ).fetchone()
        if row is not None:
            bank_id = row[0]
            _delete_items(conn, "db_id = ?", (bank_id,))
        ready = bank.status == "ready"
        values = (
            bank.cache,
            bank.adapter,
            bank.db_size,
            bank.wal_size,
            bank.fingerprint,
            bank.snapshot if ready else None,
            bank.status,
            bank.error if bank.status == "error" else None,
            bank.read_at,
            ",".join(w for w in bank.warnings if re.fullmatch(r"[a-z_]{1,40}", w))[:400],
            max(0, int(bank.skipped_rows)),
        )
        if row is None:
            bank_id = conn.execute(
                "INSERT INTO queue_db(collection_id, member, cache, adapter, db_size, wal_size,"
                " fingerprint, snapshot, status, error, items, read_at, warnings, skipped_rows)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (collection_id, member, *values),
            ).lastrowid
        else:
            conn.execute(
                "UPDATE queue_db SET cache = ?, adapter = ?, db_size = ?, wal_size = ?, fingerprint = ?,"
                " snapshot = ?, status = ?, error = ?, items = 0, read_at = ?, warnings = ?, skipped_rows = ?"
                " WHERE id = ?",
                (*values, bank_id),
            )
        stored = _write_items(conn, bank_id, bank.items) if ready else 0
        conn.execute("UPDATE queue_db SET items = ? WHERE id = ?", (stored, bank_id))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return bank_id


def _write_items(conn: sqlite3.Connection, bank_id: int, items: Iterable[ItemIn]) -> int:
    stored = 0
    body_bytes = 0
    seen: set[int] = set()
    for it in items:
        if stored >= MAX_ITEMS:
            break
        if it.state not in STATES:
            raise QueueInputError("bad_bank", "$.items", reason=f"state {it.state!r}")
        if not 0 <= it.item_id <= INT64_MAX or it.item_id in seen:
            continue  # the reader counts such rows in skipped_rows; the store never keeps them
        seen.add(it.item_id)
        body = it.body if it.body else None
        keep = (
            body is not None and len(body) <= MAX_BODY_BYTES and body_bytes + len(body) <= MAX_BANK_BODY_BYTES
        )
        size = it.size if it.size >= 0 else 0
        row_id = conn.execute(
            "INSERT INTO queue_item(db_id, item_id, message_id, msg_type, status, state, file_location,"
            " ts, size, body_stored) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bank_id,
                it.item_id,
                it.message_id,
                it.msg_type,
                it.status,
                it.state,
                it.file_location,
                it.ts,
                size,
                1 if keep else 0,
            ),
        ).lastrowid
        if keep:
            conn.execute("INSERT INTO queue_body(item_id, body) VALUES (?, ?)", (row_id, body))
            body_bytes += len(body)
        stored += 1
    return stored


def _delete_items(conn: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> int:
    """Delete bodies and items of the banks matching ``where`` (on ``queue_item``); returns the item count."""
    conn.execute(
        f"DELETE FROM queue_body WHERE item_id IN (SELECT id FROM queue_item WHERE {where})",  # noqa: S608
        params,
    )
    return conn.execute(f"DELETE FROM queue_item WHERE {where}", params).rowcount  # noqa: S608


def delete_collections(conn: sqlite3.Connection, collection_ids: list[int]) -> tuple[int, int]:
    """Delete every bank, item and body of ``collection_ids`` WITHOUT managing a transaction (the caller's).

    Returns ``(banks, items)``; ``(0, 0)`` on a case without the queue tables.
    """
    if not collection_ids or not has_queue(conn):
        return (0, 0)
    banks = items = 0
    for start in range(0, len(collection_ids), 500):
        chunk = collection_ids[start : start + 500]
        marks = ",".join("?" * len(chunk))
        bank_ids = [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM queue_db WHERE collection_id IN ({marks})",  # noqa: S608
                chunk,
            )
        ]
        for i in range(0, len(bank_ids), 500):
            ids = bank_ids[i : i + 500]
            m = ",".join("?" * len(ids))
            items += _delete_items(conn, f"db_id IN ({m})", tuple(ids))
            conn.execute(f"DELETE FROM queue_db WHERE id IN ({m})", ids)  # noqa: S608
        banks += len(bank_ids)
    return (banks, items)


def delete_queue(conn: sqlite3.Connection, group: int | None = None) -> tuple[int, int]:
    """Delete the queue data of one collection group, or of the whole case, in one transaction.

    Returns ``(banks, items)``. The case option (``queue.enabled``) is left as it is.
    """
    if not has_queue(conn):
        return (0, 0)
    if conn.in_transaction:
        raise RuntimeError("delete_queue needs a connection without an open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if group is None:
            cids = [r[0] for r in conn.execute("SELECT DISTINCT collection_id FROM queue_db")]
        else:
            cids = [r[0] for r in conn.execute(f"SELECT c.id FROM collection c WHERE {_GROUP} = ?", (group,))]
        result = delete_collections(conn, cids)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return result


# -------------------------------------------------------------------------------------- reads


def fingerprint_of(conn: sqlite3.Connection, collection_id: int, member: str) -> tuple[str, str] | None:
    """``(fingerprint, status)`` stored for a bank, or None; lets the reader skip an unchanged bank (2.6)."""
    if not has_queue(conn):
        return None
    row = conn.execute(
        "SELECT fingerprint, status FROM queue_db WHERE collection_id = ? AND member = ?",
        (collection_id, member),
    ).fetchone()
    return None if row is None else (row[0], row[1])


def summary(conn: sqlite3.Connection, *, timeout_s: float | None = PAGE_TIMEOUT_S) -> dict[str, Any]:
    """``{"enabled", "dbs": [<db>], "totals": {"items", "pending", "sent", "other"}}``.

    ``<db> = {id, collection_id, collection, name, member, cache, adapter, snapshot, status, error,
    items, pending, sent, other, bodies_missing, db_size, wal_size, read_at}``. ``collection`` is the
    group id; the caller adds the ``C<n>`` tag. ``name`` is the file name only. A case without the queue
    tables answers disabled and empty.
    """
    empty = {"enabled": False, "dbs": [], "totals": {"items": 0, "pending": 0, "sent": 0, "other": 0}}
    if not has_queue(conn):
        return empty
    dbs: list[dict[str, Any]] = []
    with read_guard(conn, timeout_s):
        counts = {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(
                "SELECT db_id, state, count(*), sum(body_stored = 0 AND size > 0) FROM queue_item"
                " GROUP BY db_id, state"
            )
        }
        for r in conn.execute(
            f"SELECT d.id, d.collection_id, {_GROUP}, d.member, d.cache, d.adapter, d.snapshot, d.status,"
            " d.error, d.items, d.db_size, d.wal_size, d.read_at, d.warnings, d.skipped_rows"
            " FROM queue_db d LEFT JOIN collection c ON c.id = d.collection_id"
            " ORDER BY coalesce(" + _GROUP + ", d.collection_id), d.member, d.id"
        ):
            per = {s: counts.get((r[0], s), (0, 0)) for s in STATES}
            dbs.append(
                {
                    "id": r[0],
                    "collection_id": r[1],
                    "collection": r[2] if r[2] is not None else r[1],
                    "name": _name(r[3]),
                    "member": r[3],
                    "cache": r[4],
                    "adapter": r[5],
                    "snapshot": r[6],
                    "status": r[7],
                    "error": r[8],
                    "items": r[9],
                    "pending": per["pending"][0],
                    "sent": per["sent"][0],
                    "other": per["other"][0],
                    "bodies_missing": sum(v[1] or 0 for v in per.values()),
                    "db_size": r[10],
                    "wal_size": r[11],
                    "read_at": r[12],
                    "warnings": [w for w in r[13].split(",") if w],
                    "skipped_rows": r[14],
                }
            )
    totals = {
        "items": sum(d["items"] for d in dbs),
        "pending": sum(d["pending"] for d in dbs),
        "sent": sum(d["sent"] for d in dbs),
        "other": sum(d["other"] for d in dbs),
    }
    return {"enabled": is_enabled(conn), "dbs": dbs, "totals": totals}


_ITEM_COLUMNS = (
    "i.id, i.db_id, d.collection_id, " + _GROUP + ", i.item_id, i.message_id, i.msg_type, i.status,"
    " i.state, i.file_location, i.ts, i.size, i.body_stored"
)
_ITEM_FROM = (
    " FROM queue_item i JOIN queue_db d ON d.id = i.db_id LEFT JOIN collection c ON c.id = d.collection_id"
)


def _item(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "db": row[1],
        "collection_id": row[2],
        "collection": row[3] if row[3] is not None else row[2],
        "item_id": row[4],
        "message_id": row[5],
        "msg_type": row[6],
        "status": row[7],
        "state": row[8],
        "file_location": row[9],
        "ts": row[10],
        "size": row[11],
        "preview": _preview(row[12], row[11]),
    }


def get_item_body(conn: sqlite3.Connection, row_id: int) -> tuple[dict[str, Any], bytes | None] | None:
    """One item and its raw BLOB from ONE statement (one consistent snapshot), or None.

    The body is None when none is stored. Reading them with two statements could pair the metadata of one
    item with the body of another if the bank was read again in between.
    """
    if not has_queue(conn):
        return None
    row = conn.execute(  # noqa: S608
        f"SELECT {_ITEM_COLUMNS}, b.body{_ITEM_FROM}"
        " LEFT JOIN queue_body b ON b.item_id = i.id WHERE i.id = ?",
        (row_id,),
    ).fetchone()
    if row is None:
        return None
    return _item(row[:-1]), (None if row[-1] is None else bytes(row[-1]))


def get_item(conn: sqlite3.Connection, row_id: int) -> dict[str, Any] | None:
    """One item by its row id (no body), or None."""
    if not has_queue(conn):
        return None
    row = conn.execute(f"SELECT {_ITEM_COLUMNS}{_ITEM_FROM} WHERE i.id = ?", (row_id,)).fetchone()  # noqa: S608
    return None if row is None else _item(row)


def _int_list(values: Iterable[int] | None, name: str) -> list[int] | None:
    if values is None:
        return None
    out = list(values)
    if len(out) > MAX_FILTER_VALUES or any(
        type(v) is not int or not INT64_MIN <= v <= INT64_MAX for v in out
    ):
        raise QueueInputError("bad_filter", f"$.{name}", limit=MAX_FILTER_VALUES)
    return out


def list_items(
    conn: sqlite3.Connection,
    *,
    collections: Iterable[int] | None = None,
    dbs: Iterable[int] | None = None,
    states: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    message_id: str | None = None,
    item_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    after: Cursor | str | None = None,
    timeout_s: float | None = PAGE_TIMEOUT_S,
) -> QueuePage:
    """One page of items in FIFO order, keyset on ``(ts, id)`` (NULL ``ts`` first), never OFFSET.

    Filters (all exact): ``collections`` (group ids), ``dbs`` (``queue_db`` ids), ``states`` (subset of
    pending/sent/other), ``types`` (``msg_type`` values), ``message_id`` (any GUID spelling, matched
    normalised) and ``item_id``. ``counts`` holds the pending/sent/other totals under the filters other than
    ``states`` (for the "N pending" counter). The body is never selected. A case without the queue tables
    answers an empty page.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise QueueInputError("bad_limit", "$.limit", limit=MAX_LIMIT)
    cursor = Cursor.parse(after) if isinstance(after, str) else after
    zero = {s: 0 for s in STATES}
    if not has_queue(conn):
        return QueuePage([], None, zero)
    where: list[str] = []
    params: list[Any] = []
    cols = _int_list(collections, "collection")
    if cols is not None:
        where.append(f"{_GROUP} IN ({','.join('?' * len(cols))})" if cols else "0")
        params += cols
    bank_ids = _int_list(dbs, "db")
    if bank_ids is not None:
        where.append(f"i.db_id IN ({','.join('?' * len(bank_ids))})" if bank_ids else "0")
        params += bank_ids
    if types is not None:
        tlist = list(types)
        if len(tlist) > MAX_TYPE_VALUES or any(not isinstance(t, str) or len(t) > 128 for t in tlist):
            raise QueueInputError("bad_type", "$.type", limit=MAX_TYPE_VALUES)
        where.append(f"i.msg_type IN ({','.join('?' * len(tlist))})" if tlist else "0")
        params += tlist
    if message_id is not None:
        guid = normalize_guid(message_id)
        if guid is None:
            raise QueueInputError("bad_query", "$.q")
        where.append("i.message_id = ?")
        params.append(guid)
    if item_id is not None:
        if type(item_id) is not int or not 0 <= item_id <= INT64_MAX:
            raise QueueInputError("bad_query", "$.q")
        where.append("i.item_id = ?")
        params.append(item_id)
    slist: list[str] | None = None
    if states is not None:
        slist = list(states)
        if any(s not in STATES for s in slist):
            raise QueueInputError("bad_state", "$.state")
    base = " AND ".join(where) or "1"
    page_where = base
    page_params = list(params)
    if slist is not None:
        page_where += f" AND i.state IN ({','.join('?' * len(slist))})" if slist else " AND 0"
        page_params += slist

    phases: list[tuple[str, tuple[Any, ...], str]] = []
    if cursor is None or cursor.ts is None:
        after_id = INT64_MIN if cursor is None else cursor.id
        phases.append(("i.ts IS NULL AND i.id > ?", (after_id,), "i.id"))
        phases.append(("i.ts IS NOT NULL", (), "i.ts, i.id"))
    else:
        phases.append(("(i.ts, i.id) > (?, ?)", (cursor.ts, cursor.id), "i.ts, i.id"))

    rows: list[tuple[Any, ...]] = []
    counts = dict(zero)
    with read_guard(conn, timeout_s):
        for keyset, kparams, order in phases:
            want = limit + 1 - len(rows)
            if want <= 0:
                break
            head = f"SELECT {_ITEM_COLUMNS}{_ITEM_FROM}"
            sql = f"{head} WHERE ({page_where}) AND {keyset} ORDER BY {order} LIMIT ?"  # noqa: S608
            rows.extend(conn.execute(sql, (*page_params, *kparams, want)).fetchall())
        for state, n in conn.execute(
            f"SELECT i.state, count(*){_ITEM_FROM} WHERE {base} GROUP BY i.state",  # noqa: S608
            params,
        ):
            if state in counts:
                counts[state] = n
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_item(r) for r in rows]
    nxt = Cursor(rows[-1][10], rows[-1][0]) if more and rows else None
    return QueuePage(items, nxt, counts)


def get_body(conn: sqlite3.Connection, row_id: int) -> bytes | None:
    """The raw BLOB of ONE item (``queue_item.id``), or None when none is stored."""
    if not has_queue(conn):
        return None
    row = conn.execute("SELECT body FROM queue_body WHERE item_id = ?", (row_id,)).fetchone()
    return None if row is None else bytes(row[0])


def log_links(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Where each item shows up in the log, by row id: ``{"msgid": n, "im_id": n, "payload_id": id | None}``.

    ``msgid`` counts the records keyed by the item's ``message_id`` (lower and upper case spellings, the
    payload keys keep the case they had), ``im_id`` the records keyed by ``str(item_id)`` (GEN-CCH-01);
    both capped at ``LINK_COUNT_CAP``. ``payload_id`` is the most recent outgoing payload whose
    ``msg_id`` is the item's message id (lower, upper and braced spellings; the lookup uses the index).
    Indexed lookups only; meant for one page (at most ``MAX_LIMIT`` items).
    """
    out: dict[int, dict[str, Any]] = {}
    cap = LINK_COUNT_CAP
    count_sql = "SELECT count(*) FROM (SELECT 1 FROM entry_key WHERE key_type = ? AND value = ? LIMIT ?)"
    for it in items:
        mid = it.get("message_id")
        n_msgid = 0
        payload_id: int | None = None
        if mid:
            spellings = list(dict.fromkeys((mid.lower(), mid.upper())))
            for sp in spellings:
                n_msgid += conn.execute(count_sql, ("msgid", sp, cap)).fetchone()[0]
            braced = [f"{{{s}}}" for s in spellings]
            allsp = [*spellings, *braced]
            row = conn.execute(
                "SELECT max(id) FROM payload WHERE direction = 'out'"  # noqa: S608
                f" AND msg_id IN ({','.join('?' * len(allsp))})",
                allsp,
            ).fetchone()
            payload_id = row[0] if row else None
        n_im = conn.execute(count_sql, ("im_id", str(it["item_id"]), cap)).fetchone()[0]
        out[it["id"]] = {"msgid": min(n_msgid, cap), "im_id": n_im, "payload_id": payload_id}
    return out
