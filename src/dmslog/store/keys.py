"""Read-only queries over correlation keys and record links (ARQUITETURA 5 and 8).

HTTP contract (all GET, ``case`` is required and canonicalized like every other route; validation
errors are ``400 invalid_field``; an unknown entry is ``404 entry_not_found``). A key value reaches
SQL only as a bound parameter; the route percent-decodes it and limits its length first.

``GET /api/keys/types?case=``
    ``{"types": [{"type", "count", "values"}]}``: every key type of the case with the number of
    ``entry_key`` rows and of distinct values, ordered by type.

``GET /api/keys/<type>/<value>?case=&limit=&after=``
    ``{"records": [<record>], "next": "<ts>:<id>" | null}``: every record carrying that key in ANY
    source of the case, ordered by ``(ts, id)`` (records without a time first), keyset pagination
    (``after`` is the ``next`` of the previous page; never OFFSET). Default limit 200, max 1000.
    ``<record>`` = ``{id, source_id, member, family, process, sensitive, ts, tid, level, sev,
    tpl_code, msg}`` with ``msg`` cut at 300 characters.

``GET /api/entries/<id>/links?case=``
    ``{"links": [<link>]}``: links of the entry in both directions, at most 200, ordered by the
    counterpart ``(ts, id)``. ``<link>`` = ``{entry_id, kind, confidence, direction, source_id,
    source: {member, sensitive}, family, process, ts, tid, tpl_code, msg}``; ``direction`` is
    ``"out"`` when the entry is side ``a`` of the link and ``"in"`` when it is side ``b``.

``GET /api/entries/<id>/side-by-side?case=``
    ``{"a": <entry>, "b": <entry>, "kind", "confidence"}`` for the best counterpart (kind
    ``normal_sensitive`` first, then the highest confidence, then the closest in time). ``a`` is
    the non-sensitive record and ``b`` the sensitive one; when both have the same sensitivity ``a``
    is the requested entry. 404 ``no_counterpart`` when the entry has no link.
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
MSG_CHARS = 300
LINKS_LIMIT = 200
NULL_TS = -(2**63)  # ts of a record without a time in the cursor: sorts before every real time

_RECORD_COLUMNS = (
    "e.id, e.source_id, s.member, st.family, st.process, st.sensitive, e.ts, e.tid, e.level, e.sev,"
    " t.code, substr(e.msg, 1, ?)"
)


@dataclass(frozen=True, slots=True)
class Counterpart:
    entry_id: int
    kind: str
    confidence: int
    direction: str


def _sensitive(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _record(row: tuple[Any, ...]) -> dict[str, Any]:
    (id_, source_id, member, family, process, sensitive, ts, tid, level, sev, code, msg) = row
    return {
        "id": id_, "source_id": source_id, "member": member, "family": family, "process": process,
        "sensitive": _sensitive(sensitive), "ts": ts, "tid": tid, "level": level, "sev": sev,
        "tpl_code": code, "msg": msg,
    }  # fmt: skip


def key_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Key types of the case with row and distinct-value counts."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(
            "SELECT key_type, count(*), count(DISTINCT value) FROM entry_key"
            " GROUP BY key_type ORDER BY key_type"
        ).fetchall()
    return [{"type": r[0], "count": r[1], "values": r[2]} for r in rows]


def records_with_key(
    conn: sqlite3.Connection,
    key_type: str,
    value: str,
    limit: int = DEFAULT_LIMIT,
    after: Cursor | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One keyset page of the records carrying ``(key_type, value)``, ordered by ``(ts, id)``."""
    limit = max(1, min(limit, MAX_LIMIT))
    ts_expr = f"coalesce(e.ts, {NULL_TS})"
    params: list[Any] = [MSG_CHARS, key_type, value]
    cond = ""
    if after is not None:
        cond = f" AND ({ts_expr} > ? OR ({ts_expr} = ? AND e.id > ?))"
        params.extend((after.ts, after.ts, after.id))
    sql = (
        f"SELECT {_RECORD_COLUMNS} FROM entry_key k JOIN entry e ON e.id = k.entry_id"  # noqa: S608
        " LEFT JOIN source s ON s.id = e.source_id LEFT JOIN stream st ON st.id = s.stream_id"
        " LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0"
        f" WHERE k.key_type = ? AND k.value = ?{cond}"
        f" ORDER BY {ts_expr}, e.id LIMIT ?"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
    records = [_record(r) for r in rows[:limit]]
    nxt = None
    if len(rows) > limit and records:
        last = records[-1]
        nxt = str(Cursor(NULL_TS if last["ts"] is None else last["ts"], last["id"]))
    return records, nxt


def entry_exists(conn: sqlite3.Connection, entry_id: int) -> bool:
    with read_guard(conn, PAGE_TIMEOUT_S):
        return conn.execute("SELECT 1 FROM entry WHERE id = ?", (entry_id,)).fetchone() is not None


_LINK_SIDES = (
    "SELECT b_entry_id AS other, kind, confidence, 'out' AS direction FROM link WHERE a_entry_id = ?1"
    " UNION ALL "
    "SELECT a_entry_id, kind, confidence, 'in' FROM link WHERE b_entry_id = ?1"
)


def links_of(conn: sqlite3.Connection, entry_id: int) -> list[dict[str, Any]]:
    """Links of one entry in both directions with a summary of each counterpart."""
    sql = (
        "SELECT l.other, l.kind, l.confidence, l.direction, e.source_id, s.member, st.sensitive, st.family,"  # noqa: S608
        " st.process, e.ts, e.tid, t.code, substr(e.msg, 1, ?2)"
        f" FROM ({_LINK_SIDES}) l JOIN entry e ON e.id = l.other"
        " LEFT JOIN source s ON s.id = e.source_id LEFT JOIN stream st ON st.id = s.stream_id"
        " LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0"
        f" ORDER BY coalesce(e.ts, {NULL_TS}), l.other, l.kind LIMIT ?3"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (entry_id, MSG_CHARS, LINKS_LIMIT)).fetchall()
    return [
        {
            "entry_id": r[0], "kind": r[1], "confidence": r[2], "direction": r[3], "source_id": r[4],
            "source": {"member": r[5], "sensitive": _sensitive(r[6])}, "family": r[7], "process": r[8],
            "ts": r[9], "tid": r[10], "tpl_code": r[11], "msg": r[12],
        }
        for r in rows
    ]  # fmt: skip


def best_counterpart(conn: sqlite3.Connection, entry_id: int) -> Counterpart | None:
    """The preferred linked record: normal_sensitive first, highest confidence, closest in time."""
    sql = (
        "SELECT l.other, l.kind, l.confidence, l.direction"  # noqa: S608
        f" FROM ({_LINK_SIDES}) l JOIN entry o ON o.id = l.other JOIN entry me ON me.id = ?1"
        " ORDER BY (l.kind = 'normal_sensitive') DESC, l.confidence DESC,"
        " abs(coalesce(o.ts, 0) - coalesce(me.ts, 0)), l.other, l.kind LIMIT 1"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(sql, (entry_id,)).fetchone()
    return None if row is None else Counterpart(row[0], row[1], row[2], row[3])


def sensitivity_of(conn: sqlite3.Connection, entry_id: int) -> bool | None:
    """Whether the entry belongs to a sensitive stream (None when unknown)."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(
            "SELECT st.sensitive FROM entry e JOIN source s ON s.id = e.source_id"
            " JOIN stream st ON st.id = s.stream_id WHERE e.id = ?",
            (entry_id,),
        ).fetchone()
    return None if row is None else _sensitive(row[0])
