"""Repeated records (ROADMAP 9.12): a record that repeats an earlier one of the same key within a window.

Definition (the contract; computed at query time, nothing is stored)
--------------------------------------------------------------------
A record ``e`` with a timestamp is a **repeat** when another record ``p`` exists with:

* the same **stream** (one process: family, PID and sensitivity of one collection; every chain of the
  stream, so the boundary between ``X.log.0`` and ``X.log`` is covered);
* the same **template** (``tpl_id``) and the same **key** (below);
* ``e.ts - window_ms <= p.ts <= e.ts`` (the window looks back only);
* ``p`` comes **before** ``e``: an earlier ``ts``, or the same ``ts`` and, in the same source, a smaller
  ``id`` (file order); in another source of the stream, the order of ``(rotation: .log.N with the
  highest N first and the current .log last, file_name, member, collection_id)``.

The first record of a burst is not a repeat; every later one is. Key:

* default (no ``fields``): the extracted fields (``entry.fields``, the same JSON text for the same
  values of one template) when the record has any, else the whole text (first line ``msg`` and the
  continuation lines, so two different payloads or stack traces under the same first line differ).
  Unknown records (``tpl_id = 0``) never have fields, so their key is their whole text;
* ``fields = [names]``: the values of those fields (``json_extract`` compared with ``IS``: value and
  type; a missing field is NULL and equals a missing field), and the whole text for unknown records;
* ``tid = true`` adds the thread (``IS``: two records without a thread are equal).

Why at query time and with this order: the answer is a function of the records and of attributes
of their sources that do not depend on the order in which they were indexed (``ts``, file order
inside a source, rotation, names). Ids only order records of one source, and there they follow
the file in a full, an incremental and a partial indexing alike (ARQUITETURA 9.3, 9.3b), so the
three give the same repeats (property tested). Nothing changes in the schema, the derivation or
the ingestion.

Known limit: at the end of daylight saving time the same hour is logged twice; a record of the
second pass can repeat one of the first pass when both fall within the window (the ``clock_jump``
event marks that hour).

Cost: one range of ``ix_entry_tpl_ts`` (template, ``ts`` in the window) per record tested, plus
two source lookups per candidate. Used as a filter leaf (``{"repeat": {...}}``, :mod:`filters`) it
scans like ``text.substr``: pair it with a source or time scope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .filters import _FIELD_NAME, INT64_MIN, FilterError, _int, _str
from .query import PAGE_TIMEOUT_S, read_guard

DEFAULT_WINDOW_MS = 1_000
MAX_WINDOW_MS = 3_600_000
MAX_KEY_FIELDS = 16
MAX_MARK_IDS = 1_000

_SPEC_KEYS = frozenset({"window_ms", "fields", "tid"})


@dataclass(frozen=True, slots=True)
class RepeatSpec:
    """What makes two records "the same" and how far back to look."""

    window_ms: int = DEFAULT_WINDOW_MS
    fields: tuple[str, ...] = ()  # () = default key (all fields, else the first line)
    tid: bool = False


def parse_spec(arg: Any, path: str = "$") -> RepeatSpec:
    """Validate ``{"window_ms": int, "fields": [name, ...], "tid": bool}`` (every key optional).

    Raises :class:`FilterError` (``bad_type``, ``bad_value``, ``unknown_operator``, ``empty_list``,
    ``list_too_long``) with the path of the offending value.
    """
    if arg is None:
        return RepeatSpec()
    if not isinstance(arg, dict):
        raise FilterError("bad_type", path, expected='{"window_ms": ..., "fields": [...], "tid": ...}')
    for name in arg:
        if name not in _SPEC_KEYS:
            raise FilterError("unknown_operator", path, name=str(name)[:64])
    window = arg.get("window_ms", DEFAULT_WINDOW_MS)
    window = _int(window, f"{path}.window_ms", 0, MAX_WINDOW_MS)
    names: list[str] = []
    if "fields" in arg:
        raw = arg["fields"]
        if not isinstance(raw, list):
            raise FilterError("bad_type", f"{path}.fields", expected="a list of field names")
        if not raw:
            raise FilterError("empty_list", f"{path}.fields")
        if len(raw) > MAX_KEY_FIELDS:
            raise FilterError("list_too_long", f"{path}.fields", limit=MAX_KEY_FIELDS)
        for k, value in enumerate(raw):
            name = _str(value, f"{path}.fields[{k}]", 64)
            if not _FIELD_NAME.match(name):
                raise FilterError(
                    "bad_value", f"{path}.fields[{k}]", detail="a field name is letters, digits and '_'"
                )
            if name not in names:
                names.append(name)
    tid = arg.get("tid", False)
    if not isinstance(tid, bool):
        raise FilterError("bad_type", f"{path}.tid", expected="true or false")
    return RepeatSpec(window, tuple(names), tid)


# The whole text of the record: first line and continuation lines (a payload or a stack trace).
_SAME_TEXT = (
    "(rp.msg = e.msg AND (rp.flags & 2) = (e.flags & 2)"
    " AND (SELECT b.cont FROM entry_body b WHERE b.entry_id = rp.id)"
    " IS (SELECT b.cont FROM entry_body b WHERE b.entry_id = e.id))"
)


def _match(spec: RepeatSpec) -> tuple[str, list[Any]]:
    """``FROM ... WHERE ...`` of the earlier records ``rp`` that ``e`` repeats (constants and ``?``).

    ``e`` is the outer alias (table ``entry``). The join order is forced (CROSS JOIN): the source of
    ``e``, then ``ix_entry_tpl_ts`` on (template, window), then the source of each candidate.
    """
    w = spec.window_ms
    params: list[Any] = [INT64_MIN + w, INT64_MIN, w]
    key: list[str] = []
    if spec.fields:
        for name in spec.fields:
            key.append("json_extract(rp.fields, ?) IS json_extract(e.fields, ?)")
            params.extend(("$." + name, "$." + name))
        key.append(f"(e.tpl_id <> 0 OR {_SAME_TEXT})")
    else:
        key.append(f"rp.fields IS e.fields AND (e.fields IS NOT NULL OR {_SAME_TEXT})")
    if spec.tid:
        key.append("rp.tid IS e.tid")
    sql = (
        "FROM source rse CROSS JOIN entry rp CROSS JOIN source rsp"
        " WHERE rse.id = e.source_id"
        " AND rp.tpl_id = e.tpl_id"
        " AND rp.ts >= (CASE WHEN e.ts < ? THEN ? ELSE e.ts - ? END) AND rp.ts <= e.ts"
        " AND rp.id <> e.id"
        " AND rsp.id = rp.source_id AND rsp.stream_id = rse.stream_id"
        " AND (rp.ts < e.ts"
        "  OR (rp.source_id = e.source_id AND rp.id < e.id)"
        "  OR (rp.source_id <> e.source_id AND ("
        "   (rsp.rotation IS NOT NULL AND rse.rotation IS NULL)"
        "   OR (rsp.rotation > rse.rotation)"
        "   OR (rsp.rotation IS rse.rotation AND (rsp.file_name, rsp.member, rsp.collection_id)"
        "    < (rse.file_name, rse.member, rse.collection_id)))))"
        " AND " + " AND ".join(key)
    )
    return sql, params


def predicate_sql(spec: RepeatSpec) -> tuple[str, list[Any]]:
    """Two-valued SQL condition over alias ``e``: the record repeats an earlier one (module docstring)."""
    match, params = _match(spec)
    return f"(e.ts IS NOT NULL AND EXISTS (SELECT 1 {match}))", params


# Closest earlier record first: the reverse of the "before" order of the module docstring.
_CLOSEST = (
    " ORDER BY rp.ts DESC, (rsp.rotation IS NULL) DESC, rsp.rotation ASC, rsp.file_name DESC,"
    " rsp.member DESC, rsp.collection_id DESC, rp.id DESC LIMIT 1"
)


def repeat_marks(
    conn: sqlite3.Connection,
    entry_ids: list[int],
    spec: RepeatSpec,
    *,
    timeout_s: float | None = PAGE_TIMEOUT_S,
) -> dict[int, dict[str, int]]:
    """Which of ``entry_ids`` (at most :data:`MAX_MARK_IDS`) are repeats, and of what.

    ``{entry_id: {"prev": id, "dt_ms": e.ts - prev.ts, "earlier": n}}`` for the repeats only:
    ``prev`` is the closest earlier record of the same key in the window (the reverse of the "before"
    order) and ``earlier`` counts the earlier records of the same key in the window (at least 1).
    Unknown ids and records that are not repeats are left out. Raises ``ValueError`` for too many ids.
    """
    ids = sorted({int(i) for i in entry_ids})
    if len(ids) > MAX_MARK_IDS:
        raise ValueError(f"at most {MAX_MARK_IDS} ids")
    if not ids:
        return {}
    match, params = _match(spec)
    sql = (
        f"SELECT e.id, e.ts, (SELECT rp.id {match}{_CLOSEST}),"  # noqa: S608 (constants only)
        f" (SELECT rp.ts {match}{_CLOSEST}), (SELECT count(*) {match})"
        " FROM entry e WHERE e.ts IS NOT NULL AND e.id IN (SELECT value FROM json_each(?))"
    )
    out: dict[int, dict[str, int]] = {}
    with read_guard(conn, timeout_s):
        rows = conn.execute(sql, (*params, *params, *params, _json_ids(ids))).fetchall()
    for entry_id, ts, prev, prev_ts, earlier in rows:
        if prev is None:
            continue
        out[entry_id] = {"prev": prev, "dt_ms": ts - prev_ts, "earlier": earlier}
    return out


def _json_ids(ids: list[int]) -> str:
    return "[" + ",".join(str(i) for i in ids) + "]"
