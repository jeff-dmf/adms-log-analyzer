"""Service layer of the message queue routes (issue #74, Q5): pure functions over a read-only connection.

The HTTP routes only validate the case, open ``CaseStore.reader`` and call these functions; nothing here
writes to the case database, and no response carries a file path (bank names are file names only) or the
adapter's user name (it is never stored).

Public API (``conn`` is a case reader; ``query`` is the route's ``{name: str}`` query mapping):

* ``summary(conn) -> dict``: ``GET /api/queue/summary``.
* ``items(conn, query) -> dict``: ``GET /api/queue/items`` -> ``{"items": [...], "next": str | None,
  "counts": {"pending", "sent", "other"}}``.
* ``preview(conn, raw_id, query, *, case, generation=0) -> dict``: ``GET /api/queue/items/{id}/preview``.
* ``parse_items_query(query) -> ItemsParams`` (the validation alone).

Errors: ``QueueParamError(code)`` (``http_status`` 400) and its subclass ``QueueNotFound`` (404). Codes:
``bad_limit``, ``bad_cursor``, ``bad_state``, ``bad_type``, ``bad_query``, ``invalid_field``,
``bad_form``, ``bad_queue_item_id``, ``queue_item_not_found``. ``message`` is English text for the
route to use as is (or to replace through ``messages.py``). Store timeouts (``QueryTimeout``) propagate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..payload.pretty import format_payload
from ..store import clock, collections, queue
from ..store.query import Cursor, CursorError
from . import exemplar, nbfx, translate

DEFAULT_LIMIT = queue.DEFAULT_LIMIT
MAX_LIMIT = queue.MAX_LIMIT
MAX_IDS = queue.MAX_FILTER_VALUES
MAX_TYPES = queue.MAX_TYPE_VALUES
MAX_TYPE_LEN = 128
FORMS = ("wire", "stored")
ERROR_CODES = (
    "bad_limit",
    "bad_cursor",
    "bad_state",
    "bad_type",
    "bad_query",
    "invalid_field",
    "bad_form",
    "bad_queue_item_id",
    "queue_item_not_found",
)

MESSAGES = {
    "bad_limit": f"limit must be an integer from 1 to {MAX_LIMIT}",
    "bad_cursor": "invalid cursor (expected '<ts>:<id>')",
    "bad_state": "state must be a comma separated list of pending, sent or other",
    "bad_type": f"type must be a list of up to {MAX_TYPES} message types ({MAX_TYPE_LEN} characters at most)",
    "bad_query": "q must be a message id (GUID) or an item id (digits)",
    "invalid_field": f"{{field}} must be a comma separated list of up to {MAX_IDS} integers",
    "bad_form": "form must be 'wire' or 'stored'",
    "bad_queue_item_id": "the queue item id must be an integer",
    "queue_item_not_found": "queue item {item} not found",
}

_INT = re.compile(r"[0-9]{1,18}")
_DIGITS = re.compile(r"[0-9]{1,19}")
_INT63 = 2**63


class QueueParamError(ValueError):
    """A request parameter is invalid. ``code`` is one of ``ERROR_CODES``; ``http_status`` is 400."""

    http_status = 400

    def __init__(self, code: str, **fields: Any) -> None:
        self.code = code
        self.message = MESSAGES.get(code, code).format(**fields)
        super().__init__(self.message)


class QueueNotFound(QueueParamError):
    """The queue item does not exist (``http_status`` 404)."""

    http_status = 404


@dataclass(frozen=True)
class ItemsParams:
    collections: list[int] | None
    dbs: list[int] | None
    states: list[str] | None
    types: list[str] | None
    message_id: str | None
    item_id: int | None
    limit: int
    after: Cursor | None


# --------------------------------------------------------------------------- parameters


def _id_list(raw: str | None, field: str) -> list[int] | None:
    if raw is None or raw == "":
        return None
    parts = raw.split(",")
    if len(parts) > MAX_IDS or any(not _INT.fullmatch(p) for p in parts):
        raise QueueParamError("invalid_field", field=field)
    return [int(p) for p in parts]


def parse_items_query(query: Mapping[str, str]) -> ItemsParams:
    """Validate the query of ``GET /api/queue/items`` (the case is the route's)."""
    raw_limit = query.get("limit")
    limit = DEFAULT_LIMIT
    if raw_limit not in (None, ""):
        if not _INT.fullmatch(raw_limit) or not 1 <= int(raw_limit) <= MAX_LIMIT:
            raise QueueParamError("bad_limit")
        limit = int(raw_limit)
    try:
        after = Cursor.parse(query.get("after"))
    except CursorError:
        raise QueueParamError("bad_cursor") from None
    states: list[str] | None = None
    raw_state = query.get("state")
    if raw_state:
        states = raw_state.split(",")
        if any(s not in queue.STATES for s in states):
            raise QueueParamError("bad_state")
    types: list[str] | None = None
    raw_type = query.get("type")
    if raw_type:
        types = raw_type.split(",")
        if len(types) > MAX_TYPES or any(not t or len(t) > MAX_TYPE_LEN for t in types):
            raise QueueParamError("bad_type")
    message_id: str | None = None
    item_id: int | None = None
    raw_q = (query.get("q") or "").strip()
    if raw_q:
        guid = queue.normalize_guid(raw_q)
        if guid is not None:
            message_id = guid
        elif _DIGITS.fullmatch(raw_q) and int(raw_q) < _INT63:
            item_id = int(raw_q)
        else:
            raise QueueParamError("bad_query")
    return ItemsParams(
        collections=_id_list(query.get("collection"), "collection"),
        dbs=_id_list(query.get("db"), "db"),
        states=states,
        types=types,
        message_id=message_id,
        item_id=item_id,
        limit=limit,
        after=after,
    )


# --------------------------------------------------------------------------- summary and items


def _warnings(db: Mapping[str, Any]) -> list[str]:
    """Warnings that can be derived from what the store keeps (the reader's own warnings are not stored)."""
    out: list[str] = list(db.get("warnings") or [])
    if db.get("skipped_rows"):
        out.append("rows_skipped")
    if db["status"] == "ready":
        if db["snapshot"] == "no_wal":
            out.append("no_wal")
        elif db["snapshot"] == "wal_failed":
            out.append("wal_failed")
        if db["items"] >= queue.MAX_ITEMS:
            out.append("items_truncated")
        if db["bodies_missing"]:
            out.append("bodies_missing")
    return list(dict.fromkeys(out))


def summary(conn: Any) -> dict[str, Any]:
    """``{enabled, dbs: [<db>], totals: {items, pending, sent, other}}``; a case without the queue tables
    answers disabled and empty."""
    raw = queue.summary(conn)
    tags = collections.group_tags(conn) if raw["dbs"] else {}
    dbs = [
        {
            "id": d["id"],
            "collection": d["collection"],
            "tag": tags.get(d["collection"]),
            "name": d["name"],
            "cache": d["cache"],
            "adapter": d["adapter"],
            "snapshot": d["snapshot"],
            "status": d["status"],
            "error": d["error"],
            "warnings": _warnings(d),
            "items": d["items"],
            "pending": d["pending"],
            "sent": d["sent"],
            "other": d["other"],
            "read_at": d["read_at"],
        }
        for d in raw["dbs"]
    ]
    return {"enabled": raw["enabled"], "dbs": dbs, "totals": raw["totals"]}


def _decorate(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public item shape: tag, string item id, adjusted time (collection clock offset), log links."""
    if not rows:
        return []
    tags = collections.group_tags(conn)
    offsets = clock.load(conn)
    links = queue.log_links(conn, rows)
    out = []
    for r in rows:
        group = r["collection"]
        out.append(
            {
                "id": r["id"],
                "db": r["db"],
                "collection": group,
                "tag": tags.get(group),
                "item_id": str(r["item_id"]),
                "message_id": r["message_id"],
                "msg_type": r["msg_type"],
                "status": r["status"],
                "state": r["state"],
                "file_location": r["file_location"],
                "ts": r["ts"],
                "ts_adjusted": clock.adjusted(r["ts"], offsets.offset_of(group)),
                "size": r["size"],
                "preview": r["preview"],
                "log": links[r["id"]],
            }
        )
    return out


def items(conn: Any, query: Mapping[str, str]) -> dict[str, Any]:
    """One page of items in FIFO order (keyset on ``(ts, id)``): ``{items, next, counts}``.

    ``counts`` are the pending/sent/other totals under the filters other than ``state``. Pages are ordered
    by the stored (raw) time; ``ts_adjusted`` is informative.
    """
    p = parse_items_query(query)
    page = queue.list_items(
        conn,
        collections=p.collections,
        dbs=p.dbs,
        states=p.states,
        types=p.types,
        message_id=p.message_id,
        item_id=p.item_id,
        limit=p.limit,
        after=p.after,
    )
    return {
        "items": _decorate(conn, page.items),
        "next": None if page.next is None else str(page.next),
        "counts": page.counts,
    }


# --------------------------------------------------------------------------- preview


def _formatted_result(res: Any) -> dict[str, Any]:
    """Same shape as ``api._pretty`` (the payload route); the formatter never raises."""
    return {
        "text": res.text,
        "ok": bool(res.ok),
        "kind": res.kind,
        "reason": res.reason,
        "truncated_display": bool(res.truncated_display),
    }


def _formatted(text: str) -> dict[str, Any]:
    return _formatted_result(format_payload(text, "xml"))


def parse_item_id(raw: str) -> int:
    if not isinstance(raw, str) or not _INT.fullmatch(raw):
        raise QueueParamError("bad_queue_item_id")
    return int(raw)


def preview(
    conn: Any, raw_id: str, query: Mapping[str, str], *, case: str, generation: int = 0
) -> dict[str, Any]:
    """``GET /api/queue/items/{id}/preview``: the XML the queued message would send. Nothing is stored.

    ``form=wire`` (default) rebuilds the wire format (shape of the case's own payloads + catalog);
    ``form=stored`` shows the decoded XML as it was stored. ``case`` and ``generation`` key the shape
    cache. A decoding failure is an answer (``decode.ok == false`` with the NBFX code and offset), never
    an exception.
    """
    row_id = parse_item_id(raw_id)
    form = query.get("form") or "wire"
    if form not in FORMS:
        raise QueueParamError("bad_form")
    found = queue.get_item_body(conn, row_id)
    if found is None:
        raise QueueNotFound("queue_item_not_found", item=row_id)
    row, stored_body = found
    item = _decorate(conn, [row])[0]
    out: dict[str, Any] = {
        "item": item,
        "form": form,
        "available": False,
        "reason": None,
        "decode": None,
        "reconstruction": None,
        "formatted": None,
    }
    body = stored_body if row["preview"] == "available" else None
    if body is None:
        out["reason"] = "too_large" if row["preview"] == "too_large" else "no_body"
        return out
    out["available"] = True
    try:
        tree = nbfx.decode(body)
    except nbfx.NbfxError as exc:
        out["decode"] = {"ok": False, "error": exc.code, "offset": exc.offset}
        return out
    out["decode"] = {"ok": True, "error": None, "offset": None}
    if form == "stored":
        try:
            out["formatted"] = _formatted(nbfx.to_xml(tree))
        except nbfx.NbfxError as exc:
            out["formatted"] = {
                "text": "",
                "ok": False,
                "kind": "xml",
                "reason": exc.code,
                "truncated_display": True,
            }
        return out
    catalog = translate.load_catalog()
    root, noun = translate._root_name(tree, set())  # noqa: SLF001 (same package; no public twin)
    shape = exemplar.load_shape(conn, case, root, generation, noun)
    rec = translate.reconstruct(tree, catalog, shape)
    out["reconstruction"] = {"basis": rec.basis, "exemplars": rec.exemplars, "notes": list(rec.notes)}
    out["formatted"] = _formatted_result(translate.format_reconstruction(rec))
    return out


__all__ = [
    "ERROR_CODES",
    "ItemsParams",
    "QueueNotFound",
    "QueueParamError",
    "items",
    "parse_item_id",
    "parse_items_query",
    "preview",
    "summary",
]
