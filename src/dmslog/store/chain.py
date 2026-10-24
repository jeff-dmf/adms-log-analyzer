"""Read-only chain of a correlation key: its operations and the handoffs between processes (R2c).

HTTP contract (``GET``; ``case`` is required; validation of ``type``, ``value`` and ``collection`` is the one
of the records route and errors are ``400 invalid_field``):

``GET /api/keys/<type>/<value>/chain?case=&collection=``
    ``{"key": {"type", "value"}, "operations": [<event> + {"collection"}], "handoffs": [<handoff>],
    "scanned", "truncated"}``.

    * ``<event>`` is the object of ``/api/events`` (``events._event_json``) of every event of kind
      ``operation`` that owns a record carrying the key, ordered by ``(ts_start, id)``, at most
      ``CHAIN_OPS``.
    * ``<handoff>`` = ``{kind, confidence, dt_ms, from: <end>, to: <end>}`` for every link of kind in
      ``CHAIN_LINK_KINDS`` whose two records carry the key, ordered by ``(from.ts, from.entry_id,
      to.entry_id)`` (records without a time first), at most ``CHAIN_LINKS``. ``dt_ms`` is
      ``to.ts - from.ts`` (``null`` when a time is missing). ``<end>`` = ``{entry_id, ts, stream_id,
      event_id, family, process, pool, instance, tpl_code, collection}``; ``event_id`` may be ``null``.
    * ``scanned`` is the number of records of the key read (at most ``CHAIN_SCAN``); ``truncated`` is true
      when any of the three limits was hit (nothing is estimated).

Only records of the template origin are read (``origin = 0``). The result does not depend on the order in
which the records were indexed: every list is sorted on stable columns before the limits are applied.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .events import _EVENT_COLUMNS, _EVENT_FROM, _event_json
from .query import PAGE_TIMEOUT_S, read_guard

if TYPE_CHECKING:
    import sqlite3

CHAIN_SCAN = 5000
CHAIN_OPS = 200
CHAIN_LINKS = 500
CHAIN_LINK_KINDS = ("handoff",)

_CHUNK = 500
_N_EVENT_COLUMNS = 18  # columns of events._EVENT_COLUMNS; the collection group follows

PoolOf = Callable[[str | None, str | None], "tuple[str, int] | None"]


def _chunks(items: list[int]) -> list[list[int]]:
    return [items[i : i + _CHUNK] for i in range(0, len(items), _CHUNK)]


def _operations(
    conn: sqlite3.Connection, ids: list[int], collection: int | None
) -> tuple[list[dict[str, Any]], bool]:
    event_ids: set[int] = set()
    for part in _chunks(ids):
        marks = ",".join("?" * len(part))
        rows = conn.execute(
            f"SELECT DISTINCT event_id FROM entry WHERE id IN ({marks}) AND event_id IS NOT NULL",  # noqa: S608
            part,
        )
        event_ids.update(r[0] for r in rows)
    found: list[tuple[Any, ...]] = []
    cond = "" if collection is None else " AND st.collection_group = ?"
    for part in _chunks(sorted(event_ids)):
        marks = ",".join("?" * len(part))
        params: list[Any] = [*part] + ([] if collection is None else [collection])
        found.extend(
            conn.execute(
                f"SELECT {_EVENT_COLUMNS}, st.collection_group FROM {_EVENT_FROM}"  # noqa: S608
                f" WHERE ev.id IN ({marks}) AND ev.kind = 'operation'{cond}",
                params,
            )
        )
    found.sort(key=lambda r: (r[11], r[0]))  # ts_start, id
    operations = []
    for row in found[:CHAIN_OPS]:
        event = _event_json(row[:_N_EVENT_COLUMNS])
        event["collection"] = row[_N_EVENT_COLUMNS]
        operations.append(event)
    return operations, len(found) > CHAIN_OPS


def _ends(conn: sqlite3.Connection, entry_ids: set[int], pool_of: PoolOf | None) -> dict[int, dict[str, Any]]:
    ends: dict[int, dict[str, Any]] = {}
    for part in _chunks(sorted(entry_ids)):
        marks = ",".join("?" * len(part))
        rows = conn.execute(
            "SELECT e.id, e.ts, st.id, e.event_id, st.family, st.process, t.code, st.collection_group"  # noqa: S608
            " FROM entry e LEFT JOIN source s ON s.id = e.source_id"
            " LEFT JOIN stream st ON st.id = s.stream_id"
            f" LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0 WHERE e.id IN ({marks})",
            part,
        )
        for id_, ts, stream_id, event_id, family, process, code, group in rows:
            pooled = pool_of(family, process) if pool_of is not None else None
            ends[id_] = {
                "entry_id": id_, "ts": ts, "stream_id": stream_id, "event_id": event_id, "family": family,
                "process": process, "pool": pooled[0] if pooled else None,
                "instance": pooled[1] if pooled else None, "tpl_code": code, "collection": group,
            }  # fmt: skip
    return ends


def _handoff_order(h: dict[str, Any]) -> tuple[Any, ...]:
    ts = h["from"]["ts"]
    return (ts is not None, ts or 0, h["from"]["entry_id"], h["to"]["entry_id"], h["kind"])


def _handoffs(
    conn: sqlite3.Connection, ids: list[int], pool_of: PoolOf | None
) -> tuple[list[dict[str, Any]], bool]:
    inside = set(ids)
    kinds = ",".join("?" * len(CHAIN_LINK_KINDS))
    links: list[tuple[int, int, str, int]] = []
    for a in ids:
        rows = conn.execute(
            f"SELECT b_entry_id, kind, confidence FROM link WHERE a_entry_id = ? AND kind IN ({kinds})",  # noqa: S608
            (a, *CHAIN_LINK_KINDS),
        )
        links.extend((a, b, kind, conf) for b, kind, conf in rows if b in inside)
    ends = _ends(conn, {x for a, b, _k, _c in links for x in (a, b)}, pool_of)
    items = []
    for a, b, kind, conf in links:
        src, dst = ends[a], ends[b]
        dt = None if src["ts"] is None or dst["ts"] is None else dst["ts"] - src["ts"]
        items.append({"kind": kind, "confidence": conf, "dt_ms": dt, "from": src, "to": dst})
    items.sort(key=_handoff_order)
    return items[:CHAIN_LINKS], len(items) > CHAIN_LINKS


def key_chain(
    conn: sqlite3.Connection,
    key_type: str,
    value: str,
    collection: int | None = None,
    pool_of: PoolOf | None = None,
) -> dict[str, Any]:
    """Operations and handoffs of the records carrying ``(key_type, value)`` (see the module docstring).

    ``pool_of(family, process)`` optionally returns ``(pool, instance)`` to annotate the handoff ends."""
    params: list[Any] = [key_type, value]
    cond = ""
    if collection is not None:
        cond = (
            " AND k.entry_id IN (SELECT e.id FROM entry e JOIN source s ON s.id = e.source_id"
            " JOIN stream st ON st.id = s.stream_id WHERE st.collection_group = ?)"
        )
        params.append(collection)
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(
            "SELECT k.entry_id FROM entry_key k WHERE k.key_type = ? AND k.value = ? AND k.origin = 0"  # noqa: S608
            f"{cond} ORDER BY k.entry_id LIMIT ?",
            (*params, CHAIN_SCAN + 1),
        ).fetchall()
        ids = [r[0] for r in rows[:CHAIN_SCAN]]
        truncated = len(rows) > CHAIN_SCAN
        operations, ops_cut = _operations(conn, ids, collection)
        handoffs, links_cut = _handoffs(conn, ids, pool_of)
    return {
        "key": {"type": key_type, "value": value},
        "operations": operations,
        "handoffs": handoffs,
        "scanned": len(ids),
        "truncated": truncated or ops_cut or links_cut,
    }
