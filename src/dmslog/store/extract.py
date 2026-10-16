"""Ad hoc extraction over the records of a filter (ROADMAP 9.8): temporary columns from a user regex.

The regex grammar, its step budget and the window of characters it may look at are in
:mod:`dmslog.parse.extractor`. This module applies it to records:

* :func:`extract` reads the first ``limit`` records of a filter in a **stable order** -- ``ts`` (records
  without a time first), then the identity of the origin of the source (rotation ``.log.N`` with the
  highest N first and the current ``.log`` last, file name, member, ERS half, collection path), then the
  file order inside the source (``id``). Nothing in it depends on the order in which the sources were
  indexed, so the records read, the values and the summary are the same after a full, an incremental or
  a partial indexing. It returns the matched rows with their values and, per group, the top values with
  their drill-down leaf, a type suggestion and numeric statistics (exact ranks, like ``/api/events/stats``).
* :func:`extract_values` gives the values of up to :data:`MAX_IDS` records by id (the temporary columns
  of the grid page).
* :func:`promote` turns the regex into a template definition (TOML text) checked by the catalog loader;
  nothing is written anywhere (the catalog is packaged and reviewed; ARQUITETURA draft).
* the filter leaf ``{"extract": {"regex", "target", "group", "type", "eq"}}`` (compiled by
  :mod:`filters` through :func:`leaf_sql`) selects the records whose group has that value, through the
  SQL function :data:`EXTRACT_FUNCTION` registered by ``db.connect``: the drill-down of a value, usable
  in ``page``, ``count``, ``distinct``, ``aggregate``, highlights and alerts. Like ``text.regex`` it scans
  rows, so the API asks for a source or time scope with it.

What the regex sees (the same in the scan and in the leaf): for ``target = "msg"`` the first line without
spaces and TABs at both ends; for ``target = "text"`` the first line, a line feed and the continuation
lines. Only the first ``window`` characters are searched; a longer record is marked ``cut``.
"""

from __future__ import annotations

import functools
import json
import math
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .. import messages
from ..parse.extractor import TYPES, Extractor, ExtractorError, compile_extractor, suggest_type
from . import query as Q
from .filters import INT64_MAX, MAX_STR, QueryInputError, RegexDeadline, _deadline

EXTRACT_FUNCTION = "dmslog_extract"
DEFAULT_LIMIT = 1_000
MAX_LIMIT = 10_000
DEFAULT_TOP = 20
MAX_TOP = 100
MAX_IDS = 1_000
MAX_PROMOTE_IDS = 20
PROMOTE_EXAMPLES = 3
EXTRACT_TIMEOUT_S = Q.PAGE_TIMEOUT_S
_UTF8_MAX = 4  # bytes of one character in UTF-8, at most
MAX_RESULT_CHARS = 8 * 1024 * 1024  # captured characters one request may keep (memory and response size)
PERCENTILES = (50, 90, 99)
_EXTRACTOR_CACHE = 64

_REQUEST_KEYS = frozenset({"case", "filter", "regex", "target", "types", "limit", "top"})
_VALUES_KEYS = frozenset({"case", "ids", "regex", "target", "types"})
_PROMOTE_KEYS = frozenset({"case", "ids", "regex", "target", "types", "family", "code"})
_LEAF_KEYS = frozenset({"regex", "target", "group", "type", "eq"})


class ExtractError(QueryInputError):
    messages = messages.EXTRACT_ERRORS


# --------------------------------------------------------------------------- the extractor of a request


@functools.lru_cache(maxsize=_EXTRACTOR_CACHE)
def _compiled(source: str, target: str, types: tuple[tuple[str, str], ...]) -> Extractor:
    return compile_extractor(source, target=target, types=dict(types))  # errors are not cached


def extractor(source: Any, target: Any = "msg", types: Any = None, path: str = "$") -> Extractor:
    """Validate and compile; errors are ExtractError with the path of the offending member."""
    if not isinstance(source, str) or not source:
        raise ExtractError("bad_regex", f"{path}.regex", detail="the regex must be a non-empty text")
    if target not in ("msg", "text"):
        raise ExtractError("bad_target", f"{path}.target")
    if types is None:
        types = {}
    if not isinstance(types, dict) or len(types) > 16:
        raise ExtractError(
            "bad_types", f"{path}.types", detail="an object {group: type} of at most 16 groups"
        )
    for kind in types.values():
        if not isinstance(kind, str) or kind not in TYPES:
            raise ExtractError("bad_types", f"{path}.types", detail=f"a type is one of {', '.join(TYPES)}")
    try:
        return _compiled(source, target, tuple(sorted(types.items())))
    except ExtractorError as exc:
        member = {"bad_target": "target", "bad_types": "types"}.get(exc.code, "regex")
        detail = exc.detail if exc.offset is None else f"{exc.detail} (at offset {exc.offset})"
        raise ExtractError(exc.code, f"{path}.{member}", detail=detail, offset=exc.offset) from None


def describe(ex: Extractor) -> dict[str, Any]:
    """What the UI shows about a compiled regex (also the body of ``POST /api/extract/check``)."""
    return {
        "regex": ex.source,
        "pattern": ("^" if ex.anchored else "") + ex.pattern,
        "target": ex.target,
        "columns": [{"name": g, "type": t} for g, t in zip(ex.groups, ex.types, strict=True)],
        "anchored": ex.anchored,
        "window": ex.window,
        "steps": ex.steps,
    }


def _parts_sql(ex: Extractor) -> tuple[str, list[Any]]:
    """The two SQL expressions over ``e`` whose decoded values (:func:`_subject`) are ``Extractor.parts``.

    Read as bytes (``CAST(... AS BLOB)``): SQLite's ``substr``/``length`` on TEXT stop at a NUL character,
    which would cut a record silently. ``trim`` keeps the NUL; ``substr`` on a BLOB counts bytes, and
    ``window + 1`` characters take at most 4 bytes each in UTF-8."""
    w = _UTF8_MAX * (ex.window + 1)
    if ex.target == "msg":
        return "coalesce(substr(CAST(trim(e.msg, ' ' || char(9)) AS BLOB), 1, ?), X''), NULL", [w]
    # substr of an empty BLOB is NULL: coalesce keeps an empty continuation apart from no continuation
    return (
        "coalesce(substr(CAST(e.msg AS BLOB), 1, ?), X''),"
        " (SELECT coalesce(substr(CAST(b.cont AS BLOB), 1, ?), X'')"
        " FROM entry_body b WHERE b.entry_id = e.id)",
        [w, w],
    )


def _decode(value: Any, chars: int) -> str | None:
    """A part read by :func:`_parts_sql`: the first ``chars`` characters. A character cut by the byte limit
    decodes as U+FFFD, beyond the ``chars`` complete characters before it (4 bytes per character at most)."""
    if value is None:
        return None
    text = value if isinstance(value, str) else bytes(value).decode("utf-8", "replace")
    return text[:chars]


def _subject(ex: Extractor, msg_part: Any, cont_part: Any) -> tuple[str, bool]:
    w1 = ex.window + 1
    return ex.subject(_decode(msg_part, w1), _decode(cont_part, w1))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise Q.QueryTimeout("extraction interrupted")


# --------------------------------------------------------------------------- the filter leaf


@functools.lru_cache(maxsize=_EXTRACTOR_CACHE)
def _leaf(key: str) -> tuple[Extractor, str]:
    regex, target, group, kind = json.loads(key)
    return _compiled(regex, target, ((group, kind),)), group


def sql_extract(key: str, msg_part: str | None, cont_part: str | None) -> Any:
    """SQL function ``dmslog_extract(key, msg_part, cont_part)``: the value of one group, or NULL.

    ``key`` is the JSON ``[regex, target, group, type]`` that :func:`leaf_sql` binds (already validated);
    the parts are the SQL of :func:`_parts_sql`. Stops at the deadline of ``query.read_guard``."""
    deadline = getattr(_deadline, "at", None)
    if deadline is not None and time.monotonic() >= deadline:
        raise RegexDeadline
    ex, group = _leaf(key)
    values = ex.run(_subject(ex, msg_part, cont_part)[0])
    return None if values is None else values[group]


def leaf_sql(arg: Any, path: str) -> tuple[str, list[Any]]:
    """Validate ``{"regex", "target", "group", "type", "eq"}`` and return its SQL over ``e`` with params.

    ``target`` defaults to ``msg`` and ``type`` to ``str``; ``eq`` is a string, a number or null (no
    match, or the group took no part), compared with ``IS`` (value and type)."""
    from .filters import FilterError, _int, _str

    if not isinstance(arg, dict) or not {"regex", "group", "eq"} <= set(arg) or not set(arg) <= _LEAF_KEYS:
        raise FilterError(
            "bad_type", path, expected='{"regex": ..., "group": ..., "eq": ..., "target": ..., "type": ...}'
        )
    group, kind = arg["group"], arg.get("type", "str")
    try:
        ex = extractor(arg["regex"], arg.get("target", "msg"), None, path)
    except ExtractError as exc:
        if exc.code == "bad_target":
            raise FilterError("bad_value", exc.path, detail="'msg' or 'text'") from None
        raise FilterError("bad_regex", exc.path, detail=exc.params.get("detail", exc.code)) from None
    if not isinstance(group, str) or group not in ex.groups:
        raise FilterError("bad_value", f"{path}.group", detail="not a named group of the regex")
    if not isinstance(kind, str) or kind not in TYPES:
        raise FilterError("bad_value", f"{path}.type", detail=f"one of {', '.join(TYPES)}")
    value = arg["eq"]
    if isinstance(value, str):
        _str(value, f"{path}.eq")
    elif isinstance(value, bool) or not (value is None or isinstance(value, (int, float))):
        raise FilterError("bad_type", f"{path}.eq", expected="a string, a number or null")
    elif isinstance(value, int):
        _int(value, f"{path}.eq")
    elif isinstance(value, float) and not math.isfinite(value):
        raise FilterError("bad_value", f"{path}.eq", detail="not a finite number")
    key = json.dumps([ex.source, ex.target, group, kind], ensure_ascii=False, separators=(",", ":"))
    parts, params = _parts_sql(ex)
    return f"({EXTRACT_FUNCTION}(?, {parts}) IS ?)", [key, *params, value]


def drill_leaf(ex: Extractor, group: str, value: Any) -> dict[str, Any] | None:
    """The leaf that selects the records whose ``group`` has ``value`` (None when it cannot be written)."""
    if isinstance(value, str) and len(value) > MAX_STR:
        return None
    kind = ex.types[ex.groups.index(group)]
    return {"extract": {"regex": ex.source, "target": ex.target, "group": group, "type": kind, "eq": value}}


# --------------------------------------------------------------------------- reading records


# Stable order (module docstring): time (none first), origin of the source, file order.
_ORDER_COLUMNS = (
    "e.id AS id, e.ts AS ts, e.source_id AS source_id, s.rotation AS rotation, s.file_name AS file_name,"
    " s.member AS member, coalesce(c.ers_variant, '') AS variant, c.path AS path"
)
# The same order over those columns (SQL and :func:`_order_key` agree: BINARY collation is code point order).
_STABLE_SQL = "ts, (rotation IS NULL), rotation DESC, file_name, member, variant, path, id"


def _order_key(row: tuple) -> tuple:
    _id, ts, _src, rotation, file_name, member, variant, path = row[:8]
    return (
        ts is not None,
        ts or 0,
        rotation is None,
        -(rotation or 0),
        file_name,
        member,
        variant,
        path,
        _id,
    )


_ORIGIN_SQL = (
    "(s.rotation IS NULL), s.rotation DESC, s.file_name, s.member, coalesce(c.ers_variant, ''), c.path"
)


@dataclass(slots=True)
class ExtractResult:
    extractor: Extractor
    rows: list[dict[str, Any]]  # matched records: {id, ts, source, values, cut}
    columns: list[dict[str, Any]]
    scanned: int
    matched: int
    cut: int
    truncated: bool


def parse_request(body: Mapping[str, Any]) -> tuple[Extractor, Any, int, int]:
    """(extractor, filter tree, limit, top) of ``POST /api/entries/extract``."""
    _unknown(body, _REQUEST_KEYS)
    ex = extractor(body.get("regex"), body.get("target", "msg"), body.get("types"))
    flt = body.get("filter")
    if flt is not None and not isinstance(flt, dict):
        raise ExtractError("bad_type", "$.filter", expected="a filter object")
    limit = _bounded(body.get("limit", DEFAULT_LIMIT), "$.limit", "bad_limit", MAX_LIMIT)
    top = _bounded(body.get("top", DEFAULT_TOP), "$.top", "bad_top", MAX_TOP)
    return ex, flt, limit, top


def _unknown(body: Mapping[str, Any], allowed: frozenset[str]) -> None:
    for name in body:
        if name not in allowed:
            raise ExtractError("unknown_member", f"$.{str(name)[:64]}", name=str(name)[:64])


def _bounded(value: Any, path: str, code: str, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
        raise ExtractError(code, path, limit=limit)
    return value


def extract(
    conn: sqlite3.Connection,
    ex: Extractor,
    flt: Any,
    *,
    limit: int = DEFAULT_LIMIT,
    top: int = DEFAULT_TOP,
    timeout_s: float = EXTRACT_TIMEOUT_S,
) -> ExtractResult:
    """Apply ``ex`` to the first ``limit`` records of ``flt`` in the stable order (module docstring).

    Raises ``query.QueryTimeout`` when the reading or the matching does not finish in ``timeout_s``:
    there is never a partial answer, so a result depends only on the records."""
    deadline = time.monotonic() + timeout_s
    cf = Q._compiled(flt)
    parts, part_params = _parts_sql(ex)
    # One statement (one snapshot). ``r`` = the first limit + 1 records of the filter in (ts, id) order,
    # the order of the grid, which the planner serves from the index that suits the filter and stops
    # early. Every record with an earlier time than the last one of ``r`` (time ``k.t``) is in ``r``; ids
    # only order the records of that last time, so when ``r`` is full its time group is read again, in
    # the stable order (origin, then file order), as far as the limit needs: ``u`` holds the first
    # limit + 1 records in the stable order (key columns only). The first ``limit`` of them are read
    # with their parts, streamed (only the parts of one record, up to window + 1 characters of the first
    # line and of the continuation, are in memory at a time); the extra one only tells ``truncated``.
    lim1 = limit + 1
    pick = (
        f"SELECT {_ORDER_COLUMNS} FROM entry e CROSS JOIN source s CROSS JOIN collection c"
        " WHERE s.id = e.source_id AND c.id = s.collection_id"
    )
    before = "FROM r CROSS JOIN k WHERE NOT (k.full AND r.ts IS k.t)"
    sql = (
        "WITH r AS (SELECT e.id AS id, e.ts AS ts FROM entry e"  # noqa: S608 - constants and "?" only
        f" WHERE ({cf.sql}) ORDER BY e.ts, e.id LIMIT ?),"
        " k AS (SELECT count(*) = ? AS full, max(ts) AS t FROM r),"
        f" u AS ({pick} AND e.id IN (SELECT r.id {before})"
        " UNION ALL SELECT * FROM ("
        f"SELECT {_ORDER_COLUMNS} FROM k CROSS JOIN entry e CROSS JOIN source s CROSS JOIN collection c"
        f" WHERE k.full AND e.ts IS k.t AND ({cf.sql}) AND s.id = e.source_id AND c.id = s.collection_id"
        f" ORDER BY {_ORIGIN_SQL}, e.id LIMIT max(0, ? - (SELECT count(*) {before}))))"
        f" SELECT p.*, {parts}, (SELECT count(*) FROM u)"
        f" FROM (SELECT * FROM u ORDER BY {_STABLE_SQL} LIMIT ?) p CROSS JOIN entry e WHERE e.id = p.id"
    )
    params = (*cf.params, lim1, lim1, *cf.params, lim1, *part_params, limit)
    done: list[tuple[tuple, int, Any, int, dict[str, Any] | None, bool]] = []
    budget = _Budget()
    total = 0
    with Q.read_guard(conn, timeout_s):
        for row in conn.execute(sql, params):
            # The deadline is checked before each search; one search is an indivisible call into ``re``
            # (bounded by MAX_STEPS; ARQUITETURA 10.11 gives the measured cost) and after the last one.
            _check_deadline(deadline)
            subject, was_cut = _subject(ex, row[8], row[9])
            raw = _raw(ex, subject)
            budget.add(raw)
            total = row[10]
            done.append((_order_key(row), row[0], row[1], row[2], raw, was_cut))
    _check_deadline(deadline)
    done.sort(key=lambda item: item[0])
    raws = [item[4] for item in done]
    rows = [
        {"id": entry_id, "ts": ts, "source": source, "values": _convert(ex, raw), "cut": was_cut}
        for _key, entry_id, ts, source, raw, was_cut in done
        if raw is not None
    ]
    columns = []
    for j in range(len(ex.groups)):
        _check_deadline(deadline)
        columns.append(_column(ex, j, raws, top, deadline))
    _check_deadline(deadline)
    return ExtractResult(
        ex, rows, columns, len(done), len(rows), sum(item[5] for item in done), total > limit
    )


class _Budget:
    """Characters of the captured values kept by one request (a group can capture a whole record)."""

    def __init__(self) -> None:
        self.chars = 0

    def add(self, raw: Mapping[str, Any] | None) -> None:
        if raw is None:
            return
        self.chars += sum(len(v) for v in raw.values() if v)
        if self.chars > MAX_RESULT_CHARS:
            raise ExtractError("result_too_large", "$", limit=MAX_RESULT_CHARS)


def _raw(ex: Extractor, subject: str) -> dict[str, Any] | None:
    m = ex.regex.match(subject) if ex.anchored else ex.regex.search(subject)
    return None if m is None else m.groupdict()


def _convert(ex: Extractor, raw: Mapping[str, Any]) -> list[Any]:
    out = []
    for name, conv in zip(ex.groups, ex.converters, strict=True):
        v = raw[name]
        out.append(conv(v) if v and conv is not None else v)
    return out


def extract_values(conn: sqlite3.Connection, ex: Extractor, ids: list[int]) -> dict[int, dict[str, Any]]:
    """``{id: {"values": [...], "cut": bool}}`` for the records of ``ids`` the regex matches."""
    wanted = sorted({int(i) for i in ids})
    if len(wanted) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} ids")
    if not wanted:
        return {}
    parts, params = _parts_sql(ex)
    sql = f"SELECT e.id, {parts} FROM entry e WHERE e.id IN (SELECT value FROM json_each(?))"  # noqa: S608
    deadline = time.monotonic() + Q.PAGE_TIMEOUT_S
    out: dict[int, dict[str, Any]] = {}
    budget = _Budget()
    with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
        for entry_id, msg_part, cont_part in conn.execute(sql, (*params, json.dumps(wanted))):
            _check_deadline(deadline)
            subject, was_cut = _subject(ex, msg_part, cont_part)
            raw = _raw(ex, subject)
            budget.add(raw)
            if raw is not None:
                out[entry_id] = {"values": _convert(ex, raw), "cut": was_cut}
    _check_deadline(deadline)
    return out


# --------------------------------------------------------------------------- summary of a column


def _value_key(value: Any) -> tuple[int, Any]:
    """Total order of the values of a column: none first, then numbers, then texts."""
    if value is None:
        return (0, 0)
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, str(value))


def _column(
    ex: Extractor, j: int, raws: list[dict[str, Any] | None], top: int, deadline: float | None = None
) -> dict[str, Any]:
    name, kind, conv = ex.groups[j], ex.types[j], ex.converters[j]
    raw_values = [None if r is None else r[name] for r in raws]
    values = [conv(v) if v and conv is not None else v for v in raw_values]
    counts: dict[tuple[int, Any], int] = {}
    for v in values:
        key = _value_key(v)
        counts[key] = counts.get(key, 0) + 1
    order = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if deadline is not None:
        _check_deadline(deadline)
    total = len(values)
    shown = order[:top]
    top_values = [
        {
            "value": key[1] if key[0] else None,
            "count": n,
            "pct": round(100.0 * n / total, 2) if total else 0.0,
            "leaf": drill_leaf(ex, name, key[1] if key[0] else None),
        }
        for key, n in shown
    ]
    present = [v for v in raw_values if v is not None]
    suggested = suggest_type([v for v in present if isinstance(v, str)])
    return {
        "name": name,
        "type": kind,
        "suggested_type": suggested,
        "present": len(present),
        "distinct": len(counts),
        "values": top_values,
        "others": {"count": total - sum(n for _, n in shown), "groups": max(0, len(order) - len(shown))},
        "numeric": _numeric(kind, suggested, present),
    }


def _numeric(kind: str, suggested: str, present: list[str]) -> dict[str, Any] | None:
    """count/min/max/avg/sum and exact-rank percentiles of the numeric values of a column, or None."""
    numeric_kind = kind if kind in ("int", "hex", "float") else suggested
    if numeric_kind not in ("int", "hex", "float"):
        return None
    from ..parse.templates import _CONVERTERS

    conv = _CONVERTERS[numeric_kind]
    nums = sorted(
        x for x in (conv(v) for v in present if v) if isinstance(x, (int, float)) and not isinstance(x, bool)
    )
    if not nums:
        return None
    cnt = len(nums)
    out: dict[str, Any] = {"type": numeric_kind, "count": cnt, "min": nums[0], "max": nums[-1]}
    if numeric_kind == "float":
        # Every value is finite (the conversion keeps inf and nan as text), but a sum can leave the float
        # range (two 1e308): then it is null, and the mean is the sum of value / count, which stays finite.
        out["sum"] = _finite(lambda: math.fsum(nums))
        out["avg"] = _finite(lambda: math.fsum(x / cnt for x in nums))
    else:
        out["sum"] = sum(nums)  # integers: exact
        out["avg"] = _finite(lambda: out["sum"] / cnt)
    for p in PERCENTILES:
        out[f"p{p}"] = nums[max(1, math.ceil(cnt * p / 100)) - 1]
    return out


def _finite(compute: Any) -> float | None:
    try:
        value = compute()
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def as_json(result: ExtractResult) -> dict[str, Any]:
    """The JSON body of ``POST /api/entries/extract``."""
    return {
        **describe(result.extractor),
        "columns": result.columns,
        "rows": result.rows,
        "scanned": result.scanned,
        "matched": result.matched,
        "cut": result.cut,
        "truncated": result.truncated,
    }


# --------------------------------------------------------------------------- promote to template


def parse_ids(value: Any, path: str, limit: int) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) > limit
        or any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i <= INT64_MAX for i in value)
    ):
        raise ExtractError("bad_ids", path, limit=limit)
    return value


def promote(conn: sqlite3.Connection, body: Mapping[str, Any]) -> dict[str, Any]:
    """A template definition built from the regex and example records; nothing is written.

    Body: ``{ids: [record ids, 1..20], regex, types?, family?, code?}``. Only ``target = "msg"`` exists
    for templates (they match the first line). See :mod:`dmslog.parse.promote` for the checks."""
    from ..parse import promote as P

    _unknown(body, _PROMOTE_KEYS)
    ids = parse_ids(body.get("ids"), "$.ids", MAX_PROMOTE_IDS)
    if not ids:
        raise ExtractError("bad_ids", "$.ids", limit=MAX_PROMOTE_IDS)
    if body.get("target", "msg") != "msg":
        raise ExtractError("promote_needs_msg", "$.target")
    ex = extractor(body.get("regex"), "msg", body.get("types"))
    family = body.get("family")
    if family is not None and (not isinstance(family, str) or not P.FAMILY_RE.fullmatch(family)):
        raise ExtractError("bad_family", "$.family")
    code = body.get("code")
    if code is not None and (not isinstance(code, str) or not P.CODE_RE.fullmatch(code)):
        raise ExtractError("bad_code", "$.code")
    deadline = time.monotonic() + Q.PAGE_TIMEOUT_S
    parts, params = _parts_sql(ex)
    sql = (
        f"SELECT {_ORDER_COLUMNS}, {parts}, st.family, t.code FROM entry e"  # noqa: S608
        " CROSS JOIN source s CROSS JOIN collection c LEFT JOIN stream st ON st.id = s.stream_id"
        " LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0"
        " WHERE s.id = e.source_id AND c.id = s.collection_id AND e.id IN (SELECT value FROM json_each(?))"
    )
    with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
        fetched = sorted(
            conn.execute(sql, (*params, json.dumps(sorted(set(ids))))).fetchall(), key=_order_key
        )
    records = []
    for row in fetched:
        subject, was_cut = _subject(ex, row[8], row[9])
        records.append(P.Example(row[0], subject, was_cut, row[10], row[11]))
    try:
        out = P.promote(ex, records, family=family, code=code, tick=lambda: _check_deadline(deadline))
    except P.PromoteError as exc:
        raise ExtractError(exc.code, "$.ids" if exc.code != "mixed_families" else "$.family") from None
    _check_deadline(deadline)
    return out


__all__ = [
    "DEFAULT_LIMIT",
    "EXTRACT_FUNCTION",
    "MAX_IDS",
    "MAX_LIMIT",
    "ExtractError",
    "ExtractResult",
    "as_json",
    "describe",
    "extract",
    "extract_values",
    "extractor",
    "leaf_sql",
    "parse_request",
    "promote",
    "sql_extract",
]
