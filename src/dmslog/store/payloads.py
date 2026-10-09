"""Read-only queries over payloads (ARQUITETURA 6.6, 11).

A payload row only points at a span of the text of its record (``msg + "\\n" + cont``); the text is
sliced here, in SQL, so a payload of ~170k characters is read without loading the rest of the record.

HTTP contract (all GET, ``case`` is required and canonicalized like every other route; validation
errors are ``400 invalid_field``; an unknown payload is ``404 payload_not_found``). User text only ever
reaches SQL as bound parameters. ``PAYLOAD_META`` is ``{id, entry_id, event_id, event_conf,
ref_entry_id, pair_id, role, direction, peer, operation, msg_id, corr_id, fmt, root, well_formed,
truncated, reason, char_len, ts, sensitive, member}``; ``ts``, ``sensitive`` and ``member`` describe the
record that holds the payload (``entry_id``).

``GET /api/entries/<id>/payloads?case=``
    ``{"payloads": [PAYLOAD_META]}``: payloads held by the entry (``entry_id``) plus those it announces
    (``ref_entry_id``), ordered by payload id. 404 ``entry_not_found`` for an unknown entry.

``GET /api/payloads/<id>?case=&format=raw|pretty``
    ``{"payload": PAYLOAD_META, "text": <raw text>, "truncated_display": bool, "formatted":
    {text, ok, kind, reason, truncated_display} | null, "pair": PAYLOAD_META | null, "entry": {id, ts,
    tid, member, sensitive}}``. ``text`` is always the raw span, cut at ``TEXT_CAP`` characters
    (``truncated_display``). ``formatted`` is present only for ``format=pretty``; a formatter
    failure is reported as ``ok=false, reason="format_error"``, never as an error status.

``GET /api/payloads/page?case=&role=&direction=&peer=&operation=&fmt=&event=&orphan=&q=&limit=&after=``
    ``{"payloads": [PAYLOAD_META], "next": "<ts>:<id>" | null}``, ordered by ``(ts of the entry,
    payload id)`` (payloads of records without a time first), keyset pagination (never OFFSET).
    ``role``, ``direction``, ``peer``, ``operation`` and ``fmt`` accept comma lists; ``event`` is an
    event id; ``orphan=1`` keeps the payloads linked to no operation (``reason`` says why),
    ``orphan=0`` the linked ones; ``q`` matches ``msg_id`` or ``corr_id`` exactly. Default limit 200,
    max 1000.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .events import Cursor
from .query import PAGE_TIMEOUT_S, read_guard

if TYPE_CHECKING:
    import sqlite3

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
TEXT_CAP = 2_000_000  # hard cap of the characters served for one payload
NULL_TS = -(2**63)  # ts of a record without a time in the cursor: sorts before every real time

_META_COLUMNS = (
    "p.id, p.entry_id, p.event_id, p.event_conf, p.ref_entry_id, p.pair_id, p.role, p.direction, p.peer,"
    " p.operation, p.msg_id, p.corr_id, p.fmt, p.root, p.well_formed, p.truncated, p.reason, p.char_len,"
    " e.ts, st.sensitive, s.member"
)
_META_FROM = (
    "payload p JOIN entry e ON e.id = p.entry_id"
    " LEFT JOIN source s ON s.id = e.source_id LEFT JOIN stream st ON st.id = s.stream_id"
)
_TS = f"coalesce(e.ts, {NULL_TS})"


@dataclass(frozen=True, slots=True)
class PayloadFilter:
    roles: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()
    peers: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    event_id: int | None = None
    orphan: bool | None = None
    key_query: str | None = None


def _bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _meta(row: tuple[Any, ...]) -> dict[str, Any]:
    (id_, entry_id, event_id, event_conf, ref_entry_id, pair_id, role, direction, peer, operation, msg_id,
     corr_id, fmt, root, well_formed, truncated, reason, char_len, ts, sensitive, member) = row  # fmt: skip
    return {
        "id": id_, "entry_id": entry_id, "event_id": event_id, "event_conf": event_conf,
        "ref_entry_id": ref_entry_id, "pair_id": pair_id, "role": role, "direction": direction,
        "peer": peer, "operation": operation, "msg_id": msg_id, "corr_id": corr_id, "fmt": fmt,
        "root": root, "well_formed": _bool(well_formed), "truncated": bool(truncated), "reason": reason,
        "char_len": char_len, "ts": ts, "sensitive": _bool(sensitive), "member": member,
    }  # fmt: skip


def _in(column: str, values: tuple[str, ...], conds: list[str], params: list[Any]) -> None:
    if values:
        conds.append(f"{column} IN ({', '.join('?' * len(values))})")
        params.extend(values)


def _where(flt: PayloadFilter, after: Cursor | None) -> tuple[str, list[Any]]:
    conds: list[str] = []
    params: list[Any] = []
    _in("p.role", flt.roles, conds, params)
    _in("p.direction", flt.directions, conds, params)
    _in("p.peer", flt.peers, conds, params)
    _in("p.operation", flt.operations, conds, params)
    _in("p.fmt", flt.formats, conds, params)
    if flt.event_id is not None:
        conds.append("p.event_id = ?")
        params.append(flt.event_id)
    if flt.orphan is not None:
        conds.append("p.event_id IS NULL" if flt.orphan else "p.event_id IS NOT NULL")
    if flt.key_query is not None:
        conds.append("(p.msg_id = ? OR p.corr_id = ?)")
        params.extend((flt.key_query, flt.key_query))
    if after is not None:
        conds.append(f"({_TS} > ? OR ({_TS} = ? AND p.id > ?))")
        params.extend((after.ts, after.ts, after.id))
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


def list_payloads(
    conn: sqlite3.Connection,
    flt: PayloadFilter,
    limit: int = DEFAULT_LIMIT,
    after: Cursor | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One keyset page of payloads ordered by ``(ts of the entry, payload id)`` and the next cursor."""
    limit = max(1, min(limit, MAX_LIMIT))
    where, params = _where(flt, after)
    sql = f"SELECT {_META_COLUMNS} FROM {_META_FROM}{where} ORDER BY {_TS}, p.id LIMIT ?"  # noqa: S608
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
    out = [_meta(r) for r in rows[:limit]]
    nxt = None
    if len(rows) > limit and out:
        last = out[-1]
        nxt = str(Cursor(NULL_TS if last["ts"] is None else last["ts"], last["id"]))
    return out, nxt


def payloads_of_entry(conn: sqlite3.Connection, entry_id: int) -> list[dict[str, Any]]:
    """Payloads held by the entry plus those it announces (``ref_entry_id``), by payload id."""
    sql = (
        f"SELECT {_META_COLUMNS} FROM {_META_FROM}"  # noqa: S608
        " WHERE p.entry_id = ?1 OR p.ref_entry_id = ?1 ORDER BY p.id"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (entry_id,)).fetchall()
    return [_meta(r) for r in rows]


def payload_count(conn: sqlite3.Connection, entry_id: int) -> int:
    """Number of payloads :func:`payloads_of_entry` would return (cheap)."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(
            "SELECT count(*) FROM payload WHERE entry_id = ?1 OR ref_entry_id = ?1", (entry_id,)
        ).fetchone()
    return int(row[0])


def get_meta(conn: sqlite3.Connection, payload_id: int) -> dict[str, Any] | None:
    sql = f"SELECT {_META_COLUMNS} FROM {_META_FROM} WHERE p.id = ?"  # noqa: S608
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(sql, (payload_id,)).fetchone()
    return None if row is None else _meta(row)


def get_payload(conn: sqlite3.Connection, payload_id: int, cap: int = TEXT_CAP) -> dict[str, Any] | None:
    """Metadata, raw text (cut at ``cap`` characters), paired payload and holder entry, or None."""
    meta = get_meta(conn, payload_id)
    if meta is None:
        return None
    # The span is msg + '\n' + cont (msg alone when the record has no continuation); SQLite counts
    # characters, like the offsets the parser stored.
    text_sql = (
        "SELECT substr(CASE WHEN b.entry_id IS NULL THEN e.msg ELSE e.msg || char(10) || b.cont END,"
        " p.char_off + 1, min(p.char_len, ?)), e.tid"
        " FROM payload p JOIN entry e ON e.id = p.entry_id"
        " LEFT JOIN entry_body b ON b.entry_id = e.id WHERE p.id = ?"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(text_sql, (cap, payload_id)).fetchone()
    text, tid = (row[0] or "", row[1]) if row is not None else ("", None)
    pair = get_meta(conn, meta["pair_id"]) if meta["pair_id"] is not None else None
    return {
        "payload": meta,
        "text": text,
        "truncated_display": meta["char_len"] > cap,
        "pair": pair,
        "entry": {
            "id": meta["entry_id"], "ts": meta["ts"], "tid": tid, "member": meta["member"],
            "sensitive": meta["sensitive"],
        },
    }  # fmt: skip
