"""Declarative aggregation: a JSON question compiled to parameterized SQL (ARQUITETURA 10.6, ROADMAP 9.6).

Questions the screens do not foresee ("p90 of SendIncident per hour, only when there was a retry")
without free SQL. :func:`parse_request` validates the JSON against fixed tables; :func:`aggregate`
compiles it and runs it under the same time policy as ``count``/``distinct``. Nothing the client sends
becomes SQL text: identifiers come only from the allow-lists of this module, values are ``?``
parameters, and the only numbers written into the SQL (bucket widths, percentile ranks) come from
constant tables too. It is a library function: the route ``POST /api/entries/aggregate`` and later
panels (9.11) only call it.

Request (all members optional except where noted)::

    {"rows": "entries" | "events",                      what is counted (default entries)
     "filter": <filter tree>,                           ARQUITETURA 10; the view's final filter
     "events": {"kind": strs, "name": strs, "status": strs},   rows=events only (ANDed)
     "group_by": [dim, ...],                            0..3 dimensions (0 = one total row)
     "measures": [measure, ...],                        1..8 (default ["count"])
     "order": {"by": <dim or measure id>, "dir": "asc" | "desc"},
     "limit": 1..1000 (default 100),
     "lookups": [lookup, ...],                          knowledge pack columns (0..4)
     "exact": bool}                                     skip the direct sample (as distinct)

Dimensions: entries ``tpl``, ``source``, ``collection``, ``family``, ``sev``, ``level``, ``tid``,
``pid``, ``fields.<name>``, ``key.<type>`` (at most 2; an entry counts once per value, as in
distinct) and ``bucket:<1m|5m|15m|1h|1d>`` (``ts`` floored to the width, epoch ms); events ``name``,
``kind``, ``status``, ``family``, ``collection``, ``sev``, ``tid``, ``key`` and ``bucket:<w>``
(``ts_start``).

Measures: ``count``; ``distinct:<dim>`` (a non-bucket dimension of the same rows; NULL is not a
value); ``<fn>:<value>`` with ``fn`` in min, max, avg, sum, p50, p90, p95, p99 and ``value`` =
``duration`` (``event.duration_ms``) or ``entries`` (``event.n_entries``) for events, or
``fields.<name>`` (numeric parsed field; other types are ignored) for entries. On entries,
``distinct:key.<type>`` cannot be combined with numeric measures (its join repeats rows).

Percentiles are exact, nearest rank, as ``/api/events/stats``: with the ``cnt`` non-NULL values of a
group in ascending order, pXX is the value at rank ``ceil(cnt * XX / 100)``, picked with
``row_number()``/``count()`` window functions partitioned by the group (standard SQL, no extension).

Lookups (knowledge pack, optional): ``error.reason`` and ``error.severity`` need the dimension
``key.errcode`` (entries); ``operation.direction`` and ``operation.service`` need ``name`` (events).
The family comes from the ``family`` dimension when present, else every family of the case is tried
and the value is kept only when they agree. Without an installed pack the lookup columns are left
out and ``lookup`` says why.

Time policy (as distinct): entries with only ``count`` and dimensions held by ``bucket`` answer exactly
from it plus the entries it cannot answer (``method="bucket"``); otherwise the exact statement within
``timeout_s`` (``exact``), skipped when ``bucket`` bounds the filter above ``DIRECT_SAMPLE_ROWS``
unless ``exact`` is asked; then, within ``estimate_timeout_s``, ``bucket_estimate`` (count only) or a
2% sample of id slices (``sample``: count and sum extrapolated; min, max, avg, percentiles and distinct
from the sample). When nothing fits, :class:`AggregateTimeout` (HTTP 408) carries the bucket bound of
the filter as ``estimated_rows``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .. import messages
from . import distinct as D
from . import query as Q
from .filters import (
    INT64_MAX,
    MAX_STR,
    CompiledFilter,
    QueryInputError,
    compile_bucket_filter,
    compile_filter,
)

ROWS = ("entries", "events")
BUCKETS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "1d": 86_400_000}
FUNCTIONS = ("min", "max", "avg", "sum", "p50", "p90", "p95", "p99")
PERCENTILES = {"p50": 50, "p90": 90, "p95": 95, "p99": 99}
MAX_GROUP_BY = 3
MAX_MEASURES = 8
MAX_LOOKUPS = 4
MAX_KEY_DIMS = 2
MAX_EVENT_LIST = 50
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
AGG_TIMEOUT_S = Q.COUNT_TIMEOUT_S
ESTIMATE_TIMEOUT_S = Q.ESTIMATE_TIMEOUT_S
BUCKET_TIMEOUT_S = 2.0
DIRECT_SAMPLE_ROWS = D.DISTINCT_DIRECT_SAMPLE_ROWS
# events: above this many events (event conditions only) the exact grouping does not fit the p95
# target (about 1 s per million operations with percentiles): sample directly unless exact
EVENTS_DIRECT_SAMPLE_ROWS = 1_000_000
EVENTS_ROWWISE_MAX = 50_000  # up to this many events, check the view's filter per event
AVG_DIGITS = 3

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_KEY_TYPE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
MAX_EVENT_WORD = 200

_FAMILY_OF_SOURCE = (
    "(SELECT st.family FROM source s JOIN stream st ON st.id = s.stream_id WHERE s.id = {src})"
)
# dimension -> SQL over ``entry e`` (constants only)
ENTRY_DIMS: dict[str, str] = {
    "tpl": "e.tpl_id",
    "source": "e.source_id",
    "collection": D.COLUMN_FIELDS["collection"][0],
    "family": _FAMILY_OF_SOURCE.format(src="e.source_id"),
    "sev": "e.sev",
    "level": "e.level",
    "tid": "e.tid",
    "pid": "e.pid",
}
# the same dimensions over ``bucket b`` (count only; the rest is not in bucket)
BUCKET_DIMS: dict[str, str] = {
    "tpl": "b.tpl_id",
    "source": "b.source_id",
    "collection": D.COLUMN_FIELDS["collection"][1],
    "family": _FAMILY_OF_SOURCE.format(src="b.source_id"),
    "sev": "b.sev",
}
EVENT_DIMS: dict[str, str] = {
    "name": "ev.name",
    "kind": "ev.kind",
    "status": "ev.status",
    "family": "(SELECT st.family FROM stream st WHERE st.id = ev.stream_id)",
    "collection": "(SELECT st.collection_group FROM stream st WHERE st.id = ev.stream_id)",
    "sev": "ev.sev",
    "tid": "ev.tid",
    "key": "ev.key",
}
EVENT_VALUES = {"duration": "ev.duration_ms", "entries": "ev.n_entries"}
EVENT_FILTERS = {"kind": "ev.kind", "name": "ev.name", "status": "ev.status"}
# lookup -> (dimension it needs, knowledge kind, attribute)
LOOKUPS: dict[str, tuple[str, str, str]] = {
    "error.reason": ("key.errcode", "error", "reason"),
    "error.severity": ("key.errcode", "error", "adms_severity"),
    "operation.direction": ("name", "operation", "direction"),
    "operation.service": ("name", "operation", "service"),
}
_LABELLED = ("tpl", "source", "collection")
# drill-down leaf of an event dimension: records of the group's events (filters.py)
_EVENT_LEAVES = {"name": "event_name", "kind": "event_kind", "status": "event_status"}

Method = Literal["bucket", "exact", "bucket_estimate", "sample"]


class AggregateError(QueryInputError):
    messages = messages.AGGREGATE_ERRORS


class AggregateTimeout(Q.QueryTimeout):
    """Neither the exact answer nor an estimate fitted their time; ``estimated_rows`` is the bucket
    bound of the filter (rows it can keep at most) or None."""

    def __init__(self, estimated_rows: int | None) -> None:
        super().__init__("aggregation interrupted")
        self.estimated_rows = estimated_rows


@dataclass(frozen=True, slots=True)
class Dim:
    id: str  # as sent: "tpl", "key.msgid", "bucket:1h"
    kind: Literal["column", "field", "key", "bucket"]
    arg: str  # column name, field name, key type or bucket width name
    width: int = 0  # bucket width in ms


@dataclass(frozen=True, slots=True)
class Measure:
    id: str  # as sent: "count", "distinct:tpl", "p90:duration"
    fn: str
    dim: Dim | None = None  # distinct
    value: str | None = None  # numeric functions: "duration", "entries" or "fields.<name>"


@dataclass(frozen=True, slots=True)
class AggRequest:
    rows: str
    tree: Any
    dims: tuple[Dim, ...]
    measures: tuple[Measure, ...]
    order_by: str
    order_desc: bool
    limit: int
    lookups: tuple[str, ...] = ()
    events: tuple[tuple[str, tuple[str, ...]], ...] = ()
    exact: bool = False


@dataclass(slots=True)
class Aggregate:
    request: AggRequest
    columns: list[dict[str, str]]  # {id, role: group | measure | lookup}
    rows: list[dict[str, Any]]  # {values: [...], labels: [...], drill: tree | None}
    groups: int  # groups found (seen in the sample when method == "sample")
    total: int | None  # sum of the count measure over every group; None without a count measure
    exact: bool
    method: Method
    lookup: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.groups > len(self.rows)


# --- validation -------------------------------------------------------------------------------


def _bad(code: str, path: str, **params: Any) -> AggregateError:
    return AggregateError(code, path, **params)


def parse_dim(name: Any, rows: str, path: str) -> Dim:
    """A dimension from the allow-list of ``rows`` (module docstring)."""
    if not isinstance(name, str) or len(name) > 80:
        raise _bad("bad_dimension", path, name=str(name)[:64])
    if name.startswith("bucket:"):
        width = BUCKETS.get(name[7:])
        if width is None:
            raise _bad("bad_bucket", path, name=name[:64], widths=", ".join(BUCKETS))
        return Dim(name, "bucket", name[7:], width)
    if rows == "events":
        if name in EVENT_DIMS:
            return Dim(name, "column", name)
        raise _bad("bad_dimension", path, name=name[:64])
    if name in ENTRY_DIMS:
        return Dim(name, "column", name)
    if name.startswith("fields.") and _FIELD_NAME.match(name[7:]):
        return Dim(name, "field", name[7:])
    if name.startswith("key.") and _KEY_TYPE.match(name[4:]):
        return Dim(name, "key", name[4:])
    raise _bad("bad_dimension", path, name=name[:64])


def parse_measure(name: Any, rows: str, path: str) -> Measure:
    if not isinstance(name, str) or len(name) > 90:
        raise _bad("bad_measure", path, name=str(name)[:64])
    if name == "count":
        return Measure(name, "count")
    fn, sep, arg = name.partition(":")
    if not sep:
        raise _bad("bad_measure", path, name=name[:64])
    if fn == "distinct":
        dim = parse_dim(arg, rows, path)
        if dim.kind == "bucket":
            raise _bad("bad_measure", path, name=name[:64])
        return Measure(name, "distinct", dim=dim)
    if fn not in FUNCTIONS:
        raise _bad("bad_measure", path, name=name[:64])
    if rows == "events":
        if arg not in EVENT_VALUES:
            raise _bad("bad_value", path, name=arg[:64], values=", ".join(EVENT_VALUES))
        return Measure(name, fn, value=arg)
    if arg in EVENT_VALUES:
        raise _bad("needs_events", path, name=name[:64])
    if arg.startswith("fields.") and _FIELD_NAME.match(arg[7:]):
        return Measure(name, fn, value=arg)
    raise _bad("bad_value", path, name=arg[:64], values="fields.<name>")


def _list(raw: Any, path: str, limit: int, code: str) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad("bad_type", path, expected="a list")
    if len(raw) > limit:
        raise _bad(code, path, limit=limit)
    return raw


def _events_filter(raw: Any, rows: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if raw is None:
        return ()
    if rows != "events":
        raise _bad("events_needs_events", "$.events")
    if not isinstance(raw, dict):
        raise _bad("bad_type", "$.events", expected="an object")
    out = []
    for name, value in raw.items():
        path = f"$.events.{name}" if isinstance(name, str) and name.isidentifier() else "$.events"
        if name not in EVENT_FILTERS:
            raise _bad("bad_events", path, name=str(name)[:64])
        items = value if isinstance(value, list) else [value]
        if not items or len(items) > MAX_EVENT_LIST:
            raise _bad("bad_events_list", path, limit=MAX_EVENT_LIST)
        for k, item in enumerate(items):
            if not isinstance(item, str) or not 0 < len(item) <= MAX_EVENT_WORD or not item.isprintable():
                raise _bad("bad_events_value", f"{path}[{k}]")
        out.append((name, tuple(items)))
    return tuple(out)


def parse_request(raw: Any) -> AggRequest:
    """Validate a request body (module docstring). The filter is compiled later by :func:`aggregate`
    (``compile_filter`` raises ``FilterError`` with its own path)."""
    if not isinstance(raw, Mapping):
        raise _bad("bad_type", "$", expected="an object")
    rows = raw.get("rows", "entries")
    if rows not in ROWS:
        raise _bad("bad_rows", "$.rows")
    tree = raw.get("filter")
    if tree is not None and not isinstance(tree, dict):
        raise _bad("bad_type", "$.filter", expected="a filter object")
    dims = tuple(
        parse_dim(name, rows, f"$.group_by[{k}]")
        for k, name in enumerate(_list(raw.get("group_by"), "$.group_by", MAX_GROUP_BY, "too_many_dims"))
    )
    raw_measures = raw.get("measures")
    if raw_measures is None:
        raw_measures = ["count"]
    measures = tuple(
        parse_measure(name, rows, f"$.measures[{k}]")
        for k, name in enumerate(_list(raw_measures, "$.measures", MAX_MEASURES, "too_many_measures"))
    )
    if not measures:
        raise _bad("no_measure", "$.measures")
    ids = [d.id for d in dims] + [m.id for m in measures]
    if len(set(ids)) != len(ids):
        raise _bad("duplicate", "$", name=next(i for i in ids if ids.count(i) > 1)[:64])
    if sum(1 for d in dims if d.kind == "bucket") > 1:
        raise _bad("one_bucket", "$.group_by")
    if sum(1 for d in dims if d.kind == "key") > MAX_KEY_DIMS:
        raise _bad("too_many_keys", "$.group_by", limit=MAX_KEY_DIMS)
    key_measures = [m for m in measures if m.dim is not None and m.dim.kind == "key"]
    if len(key_measures) > MAX_KEY_DIMS:
        raise _bad("too_many_keys", "$.measures", limit=MAX_KEY_DIMS)
    if key_measures and any(m.value is not None for m in measures):
        raise _bad("key_with_numeric", "$.measures")
    order = raw.get("order")
    if order is None:
        bucket = next((d.id for d in dims if d.kind == "bucket"), None)
        order_by, desc = (bucket, False) if bucket else (measures[0].id, True)
    else:
        if not isinstance(order, dict) or not set(order) <= {"by", "dir"}:
            raise _bad("bad_type", "$.order", expected='{"by": id, "dir": "asc" | "desc"}')
        order_by = order.get("by", measures[0].id)
        if order_by not in ids:
            raise _bad("bad_order", "$.order.by", name=str(order_by)[:64])
        direction = order.get("dir", "desc")
        if direction not in ("asc", "desc"):
            raise _bad("bad_direction", "$.order.dir")
        desc = direction == "desc"
    limit = raw.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise _bad("bad_limit", "$.limit", limit=MAX_LIMIT)
    lookups = []
    dim_ids = {d.id for d in dims}
    for k, name in enumerate(_list(raw.get("lookups"), "$.lookups", MAX_LOOKUPS, "too_many_lookups")):
        path = f"$.lookups[{k}]"
        if not isinstance(name, str) or name not in LOOKUPS:
            raise _bad("bad_lookup", path, name=str(name)[:64], lookups=", ".join(LOOKUPS))
        needed = LOOKUPS[name][0]
        if needed not in dim_ids or (needed == "name") != (rows == "events"):
            raise _bad("lookup_needs", path, name=name, dim=needed)
        if name in lookups:
            raise _bad("duplicate", path, name=name)
        lookups.append(name)
    exact = raw.get("exact", False)
    if not isinstance(exact, bool):
        raise _bad("bad_exact", "$.exact")
    return AggRequest(
        rows, tree, dims, measures, order_by, desc, limit, tuple(lookups),
        _events_filter(raw.get("events"), rows), exact,
    )  # fmt: skip


def check_known(conn: sqlite3.Connection, req: AggRequest) -> None:
    """Well-formed but unknown fields and key types are ``400 bad_dimension`` (as distinct)."""
    names = [(d, f"$.group_by[{k}]") for k, d in enumerate(req.dims)]
    names += [(m.dim, f"$.measures[{k}]") for k, m in enumerate(req.measures) if m.dim is not None]
    for k, m in enumerate(req.measures):
        if m.value is not None and m.value.startswith("fields."):
            names.append((Dim(m.value, "field", m.value[7:]), f"$.measures[{k}]"))
    for dim, path in names:
        if dim is None or dim.kind not in ("field", "key"):
            continue
        try:
            D.check_known(conn, D.FieldSpec(dim.id, dim.kind, dim.arg))
        except D.DistinctError:
            raise _bad("bad_dimension", path, name=dim.id[:64]) from None


# --- SQL ---------------------------------------------------------------------------------------


def _floor(expr: str, width: int) -> str:
    """``expr`` floored to a multiple of ``width`` (a constant), also for negative values; NULL stays."""
    return (
        f"((CASE WHEN {expr} >= 0 THEN {expr} / {width} ELSE ({expr} - {width - 1}) / {width} END) * {width})"
    )


@dataclass(slots=True)
class _Parts:
    """Pieces of the base SELECT, each with its parameters in text order."""

    select: list[tuple[str, list[Any]]] = field(default_factory=list)
    joins: list[tuple[str, list[Any]]] = field(default_factory=list)
    where: list[tuple[str, list[Any]]] = field(default_factory=list)
    repeats: bool = False  # a join repeats rows inside a group (distinct of a key)

    def sql(self, table: str) -> tuple[str, list[Any]]:
        params: list[Any] = []
        cols = []
        for text, p in self.select:
            cols.append(text)
            params += p
        joins = []
        for text, p in self.joins:
            joins.append(text)
            params += p
        conds = []
        for text, p in self.where:
            conds.append(text)
            params += p
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return f"SELECT {', '.join(cols)} FROM {table}{' ' if joins else ''}{' '.join(joins)}{where}", params


def _entry_dim(dim: Dim, k: int, parts: _Parts, alias: str, per_row: bool) -> None:
    if dim.kind == "column":
        parts.select.append((f"{ENTRY_DIMS[dim.arg]} AS {alias}", []))
    elif dim.kind == "field":
        parts.select.append((f"json_extract(e.fields, ?) AS {alias}", ["$." + dim.arg]))
    elif dim.kind == "bucket":
        parts.select.append((f"{_floor('e.ts', dim.width)} AS {alias}", []))
    else:  # key: one row per (entry, value), entries without the key are left out (as distinct)
        # per_row: walk the entries and read their keys (few entries, or the sample); else the key
        # type range of the primary key drives the join (as distinct)
        join = "CROSS JOIN entry_key k{k} INDEXED BY ix_key_entry" if per_row else "JOIN entry_key k{k}"
        parts.joins.append((f"{join.format(k=k)} ON k{k}.entry_id = e.id AND k{k}.key_type = ?", [dim.arg]))
        parts.select.append((f"k{k}.value AS {alias}", []))


def _entry_parts(req: AggRequest, cf: CompiledFilter, sample: bool, per_row: bool) -> _Parts:
    parts = _Parts()
    for i, dim in enumerate(req.dims):
        _entry_dim(dim, i, parts, f"g{i}", per_row or sample)
    for j, m in enumerate(req.measures):
        if m.value is not None:  # numeric parsed field: other JSON types are ignored
            path = "$." + m.value[7:]
            expr = (
                "CASE WHEN json_type(e.fields, ?) IN ('integer', 'real') THEN json_extract(e.fields, ?) END"
            )
            parts.select.append((f"{expr} AS v{j}", [path, path]))
        elif m.dim is not None:
            if m.dim.kind == "key":
                parts.joins.append(
                    (
                        f"LEFT JOIN entry_key m{j} INDEXED BY ix_key_entry ON m{j}.entry_id = e.id"
                        f" AND m{j}.key_type = ?",
                        [m.dim.arg],
                    )
                )
                parts.select.append((f"m{j}.value AS d{j}", []))
                parts.repeats = True
            elif m.dim.kind == "field":
                parts.select.append((f"json_extract(e.fields, ?) AS d{j}", ["$." + m.dim.arg]))
            else:
                parts.select.append((f"{ENTRY_DIMS[m.dim.arg]} AS d{j}", []))
    parts.select.append(("e.id AS rid", []))
    if sample:
        parts.select.append((f"({cf.sql}) AS ok", list(cf.params)))
    else:
        parts.where.append((f"({cf.sql})", list(cf.params)))
    return parts


def _event_conditions(req: AggRequest, cf: CompiledFilter, rowwise: bool) -> tuple[str, list[Any]]:
    """Event conditions plus "at least one record of the event is in the view" (any record, not only
    the first one). ``rowwise``: per event through ``ix_entry_event`` (cheap for few events; ``cf``
    must then be compiled with ``rowwise=True``), else the set of the view's events (cheap for a
    selective filter)."""
    conds: list[str] = []
    params: list[Any] = []
    for name, values in req.events:
        conds.append(f"{EVENT_FILTERS[name]} IN ({','.join('?' * len(values))})")
        params += values
    if cf.sql != "1":
        if rowwise:
            conds.append(
                "EXISTS (SELECT 1 FROM entry e INDEXED BY ix_entry_event WHERE e.event_id = ev.id"
                f" AND e.event_id IS NOT NULL AND ({cf.sql}))"
            )
        else:
            conds.append(
                f"ev.id IN (SELECT e.event_id FROM entry e WHERE e.event_id IS NOT NULL AND ({cf.sql}))"
            )
        params += cf.params
    return " AND ".join(conds) or "1", params


def _event_bound(conn: sqlite3.Connection, req: AggRequest, timeout_s: float) -> int | None:
    """Events the event conditions keep (before the view's filter), or None when not known in time."""
    cond, params = _event_conditions(req, compile_filter(None), False)
    try:
        with Q.read_guard(conn, timeout_s):
            return conn.execute(f"SELECT count(*) FROM event ev WHERE {cond}", params).fetchone()[0]
    except Q.QueryTimeout:
        return None


def _event_parts(req: AggRequest, cf: CompiledFilter, sample: bool, rowwise: bool) -> _Parts:
    parts = _Parts()
    for i, dim in enumerate(req.dims):
        expr = _floor("ev.ts_start", dim.width) if dim.kind == "bucket" else EVENT_DIMS[dim.arg]
        parts.select.append((f"{expr} AS g{i}", []))
    for j, m in enumerate(req.measures):
        if m.value is not None:
            parts.select.append((f"{EVENT_VALUES[m.value]} AS v{j}", []))
        elif m.dim is not None:
            parts.select.append((f"{EVENT_DIMS[m.dim.arg]} AS d{j}", []))
    parts.select.append(("ev.id AS rid", []))
    cond, params = _event_conditions(req, cf, rowwise)
    if sample:
        parts.select.append((f"({cond}) AS ok", params))
    elif cond != "1":
        parts.where.append((cond, params))
    return parts


def _grouped_sql(req: AggRequest, base: str, repeats: bool, sample: bool) -> str:
    """Aggregation over the base rows (columns g*, v*, d*, rid[, ok]) as a constant SQL text.

    Percentiles rank only the non-NULL values of their column, in their own CTE (``r<j>`` ranks,
    ``p<j>`` picks the ranks per group) joined back to the other measures (``a``) by the group values
    (``IS``: NULL groups match; SQLite builds an automatic index). Ranking every row with ``NULLS LAST``
    in the same pass costs about 1.5 to 2 times more (measured on 1.3 M operations)."""
    groups = [f"g{i}" for i in range(len(req.dims))]
    partition = f"PARTITION BY {', '.join(groups)}" if groups else ""
    source = "b"
    ctes = [f"b AS {'MATERIALIZED ' if sample else ''}({base})"]
    if sample:
        ctes.append("s AS (SELECT * FROM b WHERE ok)")
        source = "s"
    group_by = f" GROUP BY {', '.join(groups)}" if groups else ""
    cols = list(groups)
    for j, m in enumerate(req.measures):
        if m.fn == "count":
            cols.append(f"{'count(DISTINCT rid)' if repeats else 'count(*)'} AS m{j}")
        elif m.fn == "distinct":
            cols.append(f"count(DISTINCT d{j}) AS m{j}")
        elif m.fn not in PERCENTILES:
            cols.append(f"{m.fn}(v{j}) AS m{j}")
    cols.append("count(*) AS n_rows")  # never an empty select list (only percentiles, no groups)
    ctes.append(f"a AS (SELECT {', '.join(cols)} FROM {source}{group_by})")
    # one ranking per value: p50/p90/p99 of the same value share it (their v columns are equal)
    rank_of: dict[str, int] = {}
    for j, m in enumerate(req.measures):
        if m.fn in PERCENTILES and (m.value or "") not in rank_of:
            rank_of[m.value or ""] = j
    joins = []
    for value, r in rank_of.items():
        picks = [
            f"max(CASE WHEN rn = (cnt * {PERCENTILES[m.fn]} + 99) / 100 THEN v END) AS m{j}"
            for j, m in enumerate(req.measures)
            if m.fn in PERCENTILES and (m.value or "") == value
        ]
        keep = "".join(f"{g}, " for g in groups)
        ctes.append(
            f"r{r} AS (SELECT {keep}v{r} AS v, row_number() OVER ({partition} ORDER BY v{r}) AS rn,"
            f" count(*) OVER ({partition}) AS cnt FROM {source} WHERE v{r} IS NOT NULL)"
        )
        ctes.append(f"p{r} AS (SELECT {keep}{', '.join(picks)} FROM r{r}{group_by})")
        on = " AND ".join(f"p{r}.{g} IS a.{g}" for g in groups) or "1"
        joins.append(f" LEFT JOIN p{r} ON {on}")
    out = [f"a.{g}" for g in groups]
    for j, m in enumerate(req.measures):
        out.append(f"p{rank_of[m.value or '']}.m{j} AS m{j}" if m.fn in PERCENTILES else f"a.m{j} AS m{j}")
    return f"WITH {', '.join(ctes)} SELECT {', '.join(out)} FROM a{''.join(joins)}"


def _final_sql(req: AggRequest, grouped: str) -> str:
    """Top ``limit`` groups plus the number of groups and the sum of the count measure (one statement,
    one snapshot)."""
    ids = [d.id for d in req.dims] + [m.id for m in req.measures]
    names = [f"g{i}" for i in range(len(req.dims))] + [f"m{j}" for j in range(len(req.measures))]
    by = names[ids.index(req.order_by)]
    count = next((f"m{j}" for j, m in enumerate(req.measures) if m.fn == "count"), "NULL")
    ties = [f"g{i}" for i in range(len(req.dims)) if f"g{i}" != by]
    order = f"{by} {'DESC' if req.order_desc else 'ASC'} NULLS LAST" + "".join(f", {t}" for t in ties)
    return (
        f"SELECT {', '.join(names)}, count(*) OVER () AS n_groups, sum({count}) OVER () AS n_total "
        f"FROM ({grouped}) ORDER BY {order} LIMIT ?"
    )


def _bucket_eligible(req: AggRequest) -> bool:
    return (
        req.rows == "entries"
        and [m.fn for m in req.measures] == ["count"]
        and all(d.kind == "bucket" or (d.kind == "column" and d.arg in BUCKET_DIMS) for d in req.dims)
    )


def _bucket_grouped(req: AggRequest, table: str, bf: CompiledFilter, edges: list[D.Edge]) -> tuple[str, list]:
    groups = [f"g{i}" for i in range(len(req.dims))]
    bcols, ecols = [], []
    for i, d in enumerate(req.dims):
        if d.kind == "bucket":
            bcols.append(f"{_floor(f'(b.minute * {D.BUCKET_MS})', d.width)} AS g{i}")
            ecols.append(f"{_floor('e.ts', d.width)} AS g{i}")
        else:
            bcols.append(f"{BUCKET_DIMS[d.arg]} AS g{i}")
            ecols.append(f"{ENTRY_DIMS[d.arg]} AS g{i}")
    parts = [f"SELECT {', '.join([*bcols, 'b.n AS n'])} FROM {table} b WHERE {bf.sql}"]
    params: list[Any] = [*bf.params]
    for where, extra in edges:
        parts.append(f"SELECT {', '.join([*ecols, '1 AS n'])} FROM entry e WHERE {where}")
        params += extra
    group_by = f" GROUP BY {', '.join(groups)}" if groups else ""
    cols = ", ".join([*groups, "coalesce(sum(n), 0) AS m0"])
    sql = f"SELECT {cols} FROM ({' UNION ALL '.join(parts)}){group_by}"
    return sql, params


def _bucket_table(conn: sqlite3.Connection, req: AggRequest, bf: CompiledFilter) -> str:
    uses_tpl = any(d.arg == "tpl" for d in req.dims) or "tpl_id" in bf.sql
    return "bucket" if uses_tpl or not D._has_table(conn, "bucket_sev") else "bucket_sev"  # noqa: SLF001


def _run(conn: sqlite3.Connection, req: AggRequest, sql: str, params: list[Any]):
    rows = conn.execute(sql, (*params, req.limit)).fetchall()
    n = len(req.dims) + len(req.measures)
    groups = rows[0][n] if rows else 0
    total = rows[0][n + 1] if rows else 0
    return [tuple(r[:n]) for r in rows], groups, total


def _event_sample(conn: sqlite3.Connection) -> tuple[list[int], int, int] | None:
    lo, hi, total = conn.execute("SELECT min(id), max(id), count(*) FROM event").fetchone()
    if not total:
        return None
    span = hi - lo + 1
    width = max(1, int(span * Q.SAMPLE_FRACTION / Q.SAMPLE_SLICES))
    step = span / Q.SAMPLE_SLICES
    ranges: list[int] = []
    for k in range(Q.SAMPLE_SLICES):
        start = lo + int(k * step)
        ranges += [start, min(hi, start + width - 1)]
    where = " OR ".join(["id BETWEEN ? AND ?"] * Q.SAMPLE_SLICES)
    sampled = conn.execute(f"SELECT count(*) FROM event WHERE ({where})", ranges).fetchone()[0]
    return ranges, total, sampled


def _statement(
    req: AggRequest, cf: CompiledFilter, sample_alias: str | None, per_row: bool = False
) -> tuple[str, list[Any], bool]:
    """(grouped SQL, its parameters, repeats) for the exact path, or for the sample when
    ``sample_alias`` is the alias whose id the slices bound (``e`` or ``ev``). ``per_row``: events
    check the view's filter per event (``cf`` compiled ``rowwise``), entries read their keys per entry."""
    sample = sample_alias is not None
    if req.rows == "events":
        parts = _event_parts(req, cf, sample, per_row)
    else:
        parts = _entry_parts(req, cf, sample, per_row)
    if sample:
        slices = " OR ".join([f"{sample_alias}.id BETWEEN ? AND ?"] * Q.SAMPLE_SLICES)
        parts.where.append((f"({slices})", []))  # the ranges are appended by the caller
    table = "event ev" if req.rows == "events" else "entry e"
    base, params = parts.sql(table)
    return _grouped_sql(req, base, parts.repeats, sample), params, parts.repeats


# --- main entry point --------------------------------------------------------------------------


def aggregate(
    conn: sqlite3.Connection,
    request: AggRequest | Mapping[str, Any],
    *,
    knowledge: Any = None,
    timeout_s: float = AGG_TIMEOUT_S,
    estimate_timeout_s: float = ESTIMATE_TIMEOUT_S,
    bucket_timeout_s: float = BUCKET_TIMEOUT_S,
) -> Aggregate:
    """Run one aggregation (module docstring). ``knowledge`` (``knowledge.resolve.Knowledge``) answers
    the lookups; None = no pack."""
    req = request if isinstance(request, AggRequest) else parse_request(request)
    tree = req.tree
    cf = compile_filter(tree)
    check_known(conn, req)

    if req.rows == "entries":
        # 1. exact from bucket (count only, dimensions held by bucket)
        bucket_filled = False
        if _bucket_eligible(req):
            try:
                with Q.read_guard(conn, bucket_timeout_s):
                    bucket_filled = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0])
            except Q.QueryTimeout:
                bucket_filled = False
        plan = D._bucket_plan(tree, cf) if bucket_filled else None  # noqa: SLF001
        if plan is not None:
            try:
                with Q.read_guard(conn, bucket_timeout_s):
                    grouped, params = _bucket_grouped(
                        req, _bucket_table(conn, req, plan.bf), plan.bf, plan.edges
                    )
                    rows, groups, total = _run(conn, req, _final_sql(req, grouped), params)
                return _result(conn, req, rows, groups, total, True, "bucket", knowledge)
            except Q.QueryTimeout:
                pass
        # 2. exact, unless bucket shows the filter is too large to group in time
        bound = D._bucket_bound(conn, tree, bucket_timeout_s)  # noqa: SLF001
        try_exact = req.exact or bound is None or bound <= DIRECT_SAMPLE_ROWS
        per_row = bound is not None and bound <= D.KEY_ENTRY_FIRST_MAX
    else:
        # events: the event conditions bound the rows; with few events the view's filter is checked
        # per event, else as a set; too many events (or a broad view over many) go to the sample
        bucket_filled = False
        bound = _event_bound(conn, req, bucket_timeout_s)
        entries_filtered = cf.sql != "1"
        entries = D._bucket_bound(conn, tree, bucket_timeout_s) if entries_filtered else None  # noqa: SLF001
        # per event when there are few events and fewer than the view's records (else the set)
        per_row = (
            entries_filtered
            and bound is not None
            and bound <= EVENTS_ROWWISE_MAX
            and (entries is None or bound < entries)
        )
        if per_row:
            try_exact = True
        else:
            # the set costs the view's records; then at most min(events, records) events are grouped
            rows_bound = min((b for b in (bound, entries) if b is not None), default=None)
            fits = (entries is None or entries <= DIRECT_SAMPLE_ROWS) and (
                rows_bound is None or rows_bound <= EVENTS_DIRECT_SAMPLE_ROWS
            )
            try_exact = req.exact or fits
            if entries is not None and bound is not None:
                bound = min(bound, entries)
    if try_exact:
        try:
            with Q.read_guard(conn, timeout_s):
                ecf = compile_filter(tree, rowwise=True) if per_row and req.rows == "events" else cf
                grouped, params, _ = _statement(req, ecf, None, per_row)
                rows, groups, total = _run(conn, req, _final_sql(req, grouped), params)
            return _result(conn, req, rows, groups, total, True, "exact", knowledge)
        except Q.QueryTimeout:
            pass

    # 3a. estimate from bucket (whole minutes: a superset when the filter has a ts condition)
    bf = compile_bucket_filter(tree) if bucket_filled else None
    if bf is not None:
        edges = [] if D._excludes_null_ts(tree) else [D._null_ts_edge(cf)]  # noqa: SLF001
        try:
            with Q.read_guard(conn, estimate_timeout_s):
                grouped, params = _bucket_grouped(req, _bucket_table(conn, req, bf), bf, edges)
                rows, groups, total = _run(conn, req, _final_sql(req, grouped), params)
            return _result(conn, req, rows, groups, total, False, "bucket_estimate", knowledge)
        except Q.QueryTimeout:
            pass

    # 3b. estimate from a sample of id slices
    try:
        with Q.read_guard(conn, estimate_timeout_s):
            if req.rows == "events":
                sample = _event_sample(conn)
                alias = "ev"
            else:
                sample = D._sample_slices(conn)  # noqa: SLF001
                alias = "e"
            if sample is None:  # empty table: the exact answer is the empty one
                grouped, params, _ = _statement(req, cf, None)
                rows, groups, total = _run(conn, req, _final_sql(req, grouped), params)
                return _result(conn, req, rows, groups, total, True, "exact", knowledge)
            ranges, n_all, n_sampled = sample
            if n_sampled:
                # the sample checks the view's filter per row: per event through the records' index
                scf = compile_filter(tree, rowwise=True) if req.rows == "events" else cf
                grouped, params, _ = _statement(req, scf, alias, per_row=True)
                rows, groups, total = _run(conn, req, _final_sql(req, grouped), [*params, *ranges])
    except Q.QueryTimeout:
        raise AggregateTimeout(bound) from None
    if not n_sampled:
        raise AggregateTimeout(bound)
    scale = n_all / n_sampled
    scaled = {j for j, m in enumerate(req.measures) if m.fn in ("count", "sum")}
    n = len(req.dims)
    rows = [
        tuple(v if k < n or (k - n) not in scaled or v is None else _scale(v, scale) for k, v in enumerate(r))
        for r in rows
    ]
    total = None if total is None else round(total * scale)
    return _result(conn, req, rows, groups, total, False, "sample", knowledge)


def _scale(value: Any, scale: float) -> Any:
    return round(value * scale) if isinstance(value, int) else value * scale


# --- result ------------------------------------------------------------------------------------


def _labels(conn: sqlite3.Connection, dim: Dim, values: list[Any]) -> dict[Any, str]:
    if dim.kind != "column" or dim.arg not in _LABELLED:
        return {}
    found = D._labels(conn, D.FieldSpec(dim.id, "column", dim.arg), values)  # noqa: SLF001
    return {k: v[0] for k, v in found.items()}


def _ts_leaf(lo: Any, width: int) -> dict[str, Any] | None:
    if not isinstance(lo, int) or isinstance(lo, bool):
        return None
    hi = lo + width
    return {"ts": {"ge": lo, "lt": hi}} if hi <= INT64_MAX else {"ts": {"ge": lo}}


def drill_leaf(rows: str, dim: Dim, value: Any) -> dict[str, Any] | None:
    """Filter leaf that selects the records of one group value, or None when the grammar cannot say it.

    Events: the leaves select the records of the group's events (``event_name``, ``event_kind``,
    ``event_status``); a time bucket bounds the records' own ``ts``."""
    if dim.kind == "bucket":
        return _ts_leaf(value, dim.width)
    if rows == "events":
        if dim.arg in _EVENT_LEAVES:
            ok = isinstance(value, str) and len(value) <= MAX_STR
            return {_EVENT_LEAVES[dim.arg]: value} if ok else None
        if dim.arg == "family":
            return {"family": value} if isinstance(value, str) and len(value) <= MAX_STR else None
        if dim.arg == "collection":
            return {"collection": value} if isinstance(value, int) else None
        return None  # event severity, TID and key say nothing about each record
    if dim.kind == "column" and dim.arg == "family":
        return {"family": value} if isinstance(value, str) and len(value) <= MAX_STR else None
    return D.drill_leaf(D.FieldSpec(dim.id, dim.kind, dim.arg), value)  # type: ignore[arg-type]


def _drill(req: AggRequest, values: Sequence[Any]) -> dict[str, Any] | None:
    if not req.dims:
        return None
    leaves = [drill_leaf(req.rows, d, values[i]) for i, d in enumerate(req.dims)]
    if any(leaf is None for leaf in leaves):
        return None
    return leaves[0] if len(leaves) == 1 else {"and": leaves}


def _round(m: Measure, value: Any) -> Any:
    if isinstance(value, float) and m.fn in ("avg", "sum"):
        return round(value, AVG_DIGITS)
    return value


def _result(
    conn: sqlite3.Connection,
    req: AggRequest,
    rows: list[tuple],
    groups: int,
    total: int | None,
    exact: bool,
    method: Method,
    knowledge: Any,
) -> Aggregate:
    n = len(req.dims)
    labels = [_labels(conn, d, [r[i] for r in rows]) for i, d in enumerate(req.dims)]  # own read guard
    columns = [{"id": d.id, "role": "group"} for d in req.dims]
    columns += [{"id": m.id, "role": "measure"} for m in req.measures]
    out = []
    for r in rows:
        values = list(r[:n]) + [_round(m, r[n + j]) for j, m in enumerate(req.measures)]
        out.append({
            "values": values,
            "labels": [labels[i].get(r[i]) for i in range(n)],
            "drill": _drill(req, r),
        })  # fmt: skip
    has_count = any(m.fn == "count" for m in req.measures)
    result = Aggregate(req, columns, out, groups, total if has_count else None, exact, method)
    if req.lookups:
        _apply_lookups(conn, result, knowledge)
    return result


def _apply_lookups(conn: sqlite3.Connection, result: Aggregate, knowledge: Any) -> None:
    """Knowledge pack columns for the returned rows (at most ``limit``), or the reason they are absent."""
    from ..knowledge.pack import STATE_NOT_INSTALLED

    req = result.request
    if knowledge is None or not knowledge.has_pack:
        state = knowledge.pack_status.state if knowledge is not None else STATE_NOT_INSTALLED
        text = (
            messages.KNOWLEDGE_PACK_INVALID if state == "invalid" else messages.KNOWLEDGE_PACK_NOT_INSTALLED
        )
        result.lookup = {"state": state, "columns": [], "message": text}
        result.notes.append("lookups_without_pack")
        return
    ids = [d.id for d in req.dims]
    family_at = ids.index("family") if "family" in ids else None
    with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
        families = [r[0] for r in conn.execute("SELECT DISTINCT family FROM stream ORDER BY family")]
    ambiguous = False
    for name in req.lookups:
        needed, kind, attr = LOOKUPS[name]
        at = ids.index(needed)
        result.columns.append({"id": name, "role": "lookup"})
        for row in result.rows:
            key = row["values"][at]
            fams = [row["values"][family_at]] if family_at is not None else families
            found = {v for v in (_lookup(knowledge, kind, attr, f, key) for f in fams) if v is not None}
            if len(found) > 1:
                ambiguous = True
            row["values"].append(found.pop() if len(found) == 1 else None)
    result.lookup = {"state": "installed", "columns": list(req.lookups), "message": None}
    if ambiguous:
        result.notes.append("lookup_ambiguous")


def _lookup(knowledge: Any, kind: str, attr: str, family: Any, key: Any) -> str | None:
    if not isinstance(family, str) or not isinstance(key, str) or not key:
        return None
    if kind == "error":
        info = knowledge.error(family, key)
        return None if info is None else getattr(info, attr)
    acronym = knowledge.acronym_for_family(family)
    for op in knowledge.pack.operations:
        if op.acronym == acronym and op.operation == key:
            return getattr(op, attr) or None
    return None


def as_json(result: Aggregate) -> dict[str, Any]:
    """The JSON body of ``POST /api/entries/aggregate``."""
    body: dict[str, Any] = {
        "rows_kind": result.request.rows,
        "columns": result.columns,
        "rows": result.rows,
        "groups": result.groups,
        "truncated": result.truncated,
        "total": result.total,
        "exact": result.exact,
        "method": result.method,
        "notes": result.notes,
    }
    if result.method == "sample":
        body["sample_fraction"] = Q.SAMPLE_FRACTION
    if result.request.lookups:
        body["lookup"] = result.lookup
    return body


__all__ = [
    "BUCKETS",
    "DEFAULT_LIMIT",
    "FUNCTIONS",
    "LOOKUPS",
    "MAX_LIMIT",
    "AggRequest",
    "Aggregate",
    "AggregateError",
    "AggregateTimeout",
    "aggregate",
    "as_json",
    "drill_leaf",
    "parse_request",
]
