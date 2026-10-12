"""Distinct values of one field inside a filter, with counts (ARQUITETURA 9.4 and 10, ROADMAP 9.2).

:func:`distinct` groups the entries that match a filter tree (``store/filters.py``) by one field and
returns the ``top`` most frequent values with their counts and share, the remainder as "others",
the total and the number of distinct values. Each value carries the filter leaf that selects it
(drill-down: the viewer ANDs the leaf, or its negation, into the current filter). It is a library
function: the API route (``POST /api/entries/distinct``) and later panels only call it.

Fields (``field``), all checked against fixed tables before any SQL is built:

* ``tpl``, ``source``, ``sev``, ``level``, ``tid``, ``pid``: columns of ``entry``;
* ``fields.<name>``: a parsed field (``<name>`` matches ``[A-Za-z_][A-Za-z0-9_]{0,63}`` and reaches
  SQL only as the bound JSON path ``$.<name>``, as the grid's dynamic columns do);
* ``key.<type>``: values of ``entry_key`` of that type, both origins (template and payload keys),
  exactly what the ``key`` filter leaf matches. An entry with several values of the type counts once
  per value, so for keys ``total`` is the number of (entry, value) pairs.

Count policy (the same budget as :func:`query.count`):

1. ``tpl``/``source``/``sev`` with a filter that the per-minute ``bucket`` table can express and no
   ``ts`` condition: exact from ``bucket`` (``bucket_sev`` when the template is not involved) plus
   the entries without a time, which ``bucket`` does not hold, in one statement. ``method="bucket"``.
2. Otherwise, exact ``GROUP BY`` over ``entry`` (joined to ``entry_key`` for keys) within
   ``timeout_s``. ``method="exact"``.
3. On timeout, an estimate within ``estimate_timeout_s``: the ``bucket`` table when the filter can
   be expressed on it (``ts`` rounded to whole minutes, so a superset; ``method="bucket_estimate"``),
   else a sample of ``id`` slices of ``entry`` extrapolated to the whole table (``method="sample"``;
   ``groups`` is then the number of values seen in the sample, a lower bound).
4. When even the estimate does not fit: no values, ``total=None``, ``method="none"``.

Groups are ordered by count (descending), then by value (NULL first). ``span=True`` adds the first
and last ``ts`` of each returned value (exact on paths 1 and 2; on path 1 the minute found in
``bucket`` is refined with one indexed query per value; minute precision on ``bucket_estimate``).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from .. import messages
from . import query as Q
from .filters import (
    BUCKET_MS,
    MAX_STR,
    CompiledFilter,
    QueryInputError,
    compile_bucket_filter,
    compile_filter,
)

DEFAULT_TOP = 20
MAX_TOP = 500
DISTINCT_TIMEOUT_S = Q.COUNT_TIMEOUT_S
ESTIMATE_TIMEOUT_S = Q.ESTIMATE_TIMEOUT_S
BUCKET_TIMEOUT_S = 2.0
KEY_ENTRY_FIRST_MAX = 100_000  # keys: walk the entries when the filter keeps at most this many

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_KEY_TYPE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")  # same shape the key routes accept

# field -> (entry column, bucket column or None). Constants only: never user text.
COLUMN_FIELDS: dict[str, tuple[str, str | None]] = {
    "tpl": ("e.tpl_id", "b.tpl_id"),
    "source": ("e.source_id", "b.source_id"),
    "sev": ("e.sev", "b.sev"),
    "level": ("e.level", None),
    "tid": ("e.tid", None),
    "pid": ("e.pid", None),
}
_ROLLUP_FIELDS = frozenset({"source", "sev"})  # bucket_sev holds them (no template)

Method = Literal["bucket", "exact", "bucket_estimate", "sample", "none"]


class DistinctError(QueryInputError):
    messages = messages.DISTINCT_ERRORS


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """A validated field: ``kind`` is column, field or key; ``arg`` the column name, field or type."""

    name: str
    kind: Literal["column", "field", "key"]
    arg: str


@dataclass(slots=True)
class Distinct:
    field: str
    values: list[dict[str, Any]]  # {value, count, pct, leaf[, label][, first_ts, last_ts]}
    others: int  # count of the groups after the top ones
    other_groups: int
    total: int | None  # sum of every group's count; None when nothing could be computed in time
    groups: int  # distinct values (seen in the sample when method == "sample")
    exact: bool
    method: Method


def parse_field(name: Any) -> FieldSpec:
    """Validate a field name sent by the client (allow-list; see the module docstring)."""
    if not isinstance(name, str) or len(name) > 80:
        raise DistinctError("bad_field", "$.field", name=str(name)[:64])
    if name in COLUMN_FIELDS:
        return FieldSpec(name, "column", name)
    if name.startswith("fields.") and _FIELD_NAME.match(name[7:]):
        return FieldSpec(name, "field", name[7:])
    if name.startswith("key.") and _KEY_TYPE.match(name[4:]):
        return FieldSpec(name, "key", name[4:])
    raise DistinctError("bad_field", "$.field", name=name[:64])


def drill_leaf(spec: FieldSpec, value: Any) -> dict[str, Any] | None:
    """Filter leaf that selects the entries of one group, or None when no leaf can express it."""
    if spec.kind == "key":
        return (
            {"key": {"type": spec.arg, "eq": value}}
            if isinstance(value, str) and len(value) <= MAX_STR
            else None
        )
    if spec.kind == "field":
        if isinstance(value, str) and len(value) > MAX_STR:
            return None
        return {"field": {"name": spec.arg, "eq": value}}
    if value is None:
        # level is the only nullable column whose NULL group the grammar can select (levels are 1..8).
        return {"not": {"level": {"ge": 1}}} if spec.arg == "level" else None
    return {spec.arg: value}


# --- SQL ---------------------------------------------------------------------------------------


def _entry_group_sql(
    spec: FieldSpec, cf: CompiledFilter, span: bool, where_extra: str = "", *, entry_first: bool = False
) -> tuple[str, list[Any]]:
    """``SELECT g, n[, lo, hi]`` grouped over ``entry`` (and ``entry_key``) for the filter.

    Keys: by default the ``key_type`` range of the primary key drives the join (values come in
    order, no temporary B-tree for the grouping; cost = rows of that type). ``entry_first`` walks
    the filtered entries instead and reads their keys through ``ix_key_entry`` (covering), which is
    cheaper when the filter keeps few entries (ts window, sample slices)."""
    agg = ", min(e.ts) AS lo, max(e.ts) AS hi" if span else ""
    if spec.kind == "key":
        if entry_first:
            sql = (
                f"SELECT k.value AS g, count(*) AS n{agg} FROM entry e CROSS JOIN entry_key k "
                "INDEXED BY ix_key_entry ON k.entry_id = e.id AND k.key_type = ? "
                f"WHERE ({cf.sql}){where_extra} GROUP BY k.value"
            )
        else:
            sql = (
                f"SELECT k.value AS g, count(*) AS n{agg} FROM entry_key k JOIN entry e ON e.id = k.entry_id "
                f"WHERE k.key_type = ? AND ({cf.sql}){where_extra} GROUP BY k.value"
            )
        return sql, [spec.arg, *cf.params]
    if spec.kind == "field":
        sql = (
            f"SELECT json_extract(e.fields, ?) AS g, count(*) AS n{agg} FROM entry e "
            f"WHERE ({cf.sql}){where_extra} GROUP BY g"
        )
        return sql, ["$." + spec.arg, *cf.params]
    column = COLUMN_FIELDS[spec.arg][0]
    return (
        f"SELECT {column} AS g, count(*) AS n{agg} FROM entry e WHERE ({cf.sql}){where_extra} GROUP BY g",
        [*cf.params],
    )


def _run_top(conn: sqlite3.Connection, group_sql: str, params: list[Any], span: bool, top: int):
    """Top rows of a grouped statement plus the total and the number of groups, in one statement
    (one snapshot, also while the indexer writes). The grouped SELECT names its columns g, n[, lo, hi]."""
    span_cols = "lo, hi" if span else "NULL, NULL"
    sql = (
        f"SELECT g, n, {span_cols}, sum(n) OVER (), count(*) OVER () FROM ({group_sql}) "
        "ORDER BY n DESC, g LIMIT ?"
    )
    rows = conn.execute(sql, (*params, top)).fetchall()
    total = rows[0][4] if rows else 0
    groups = rows[0][5] if rows else 0
    return [(g, n, lo, hi) for g, n, lo, hi, _t, _c in rows], total, groups


def _bucket_group_sql(spec: FieldSpec, bf: CompiledFilter, cf: CompiledFilter, table: str, span: bool):
    """Exact grouping from the bucket table plus the entries without ts (one statement, one snapshot)."""
    ecol, bcol = COLUMN_FIELDS[spec.arg]
    lo_b, hi_b = ("b.minute", "b.minute") if span else ("NULL", "NULL")
    sql = (
        "SELECT g, sum(n) AS n, min(lo) AS lo, max(hi) AS hi FROM ("
        f"SELECT {bcol} AS g, b.n AS n, {lo_b} AS lo, {hi_b} AS hi FROM {table} b WHERE {bf.sql} "
        f"UNION ALL SELECT {ecol}, 1, NULL, NULL FROM entry e WHERE e.ts IS NULL AND ({cf.sql})"
        ") GROUP BY g"
    )
    return sql, [*bf.params, *cf.params]


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 0").fetchall()
    except sqlite3.OperationalError:
        return False
    return True


def _bucket_table(conn: sqlite3.Connection, spec: FieldSpec, bf: CompiledFilter) -> str:
    if spec.arg in _ROLLUP_FIELDS and "tpl_id" not in bf.sql and _has_table(conn, "bucket_sev"):
        return "bucket_sev"
    return "bucket"


def _refine_span(
    conn: sqlite3.Connection, spec: FieldSpec, cf: CompiledFilter, rows: list[tuple]
) -> list[tuple]:
    """Exact first/last ts inside the first/last bucket minute of each value (indexed range reads)."""
    ecol = COLUMN_FIELDS[spec.arg][0]
    sql_lo = f"SELECT min(e.ts) FROM entry e WHERE {ecol} = ? AND e.ts >= ? AND e.ts < ? AND ({cf.sql})"
    sql_hi = f"SELECT max(e.ts) FROM entry e WHERE {ecol} = ? AND e.ts >= ? AND e.ts < ? AND ({cf.sql})"
    out = []
    for g, n, lo, hi in rows:
        first = last = None
        if lo is not None:
            first = conn.execute(sql_lo, (g, lo * BUCKET_MS, (lo + 1) * BUCKET_MS, *cf.params)).fetchone()[0]
            last = conn.execute(sql_hi, (g, hi * BUCKET_MS, (hi + 1) * BUCKET_MS, *cf.params)).fetchone()[0]
        out.append(
            (g, n, first if first is not None else _ms(lo), last if last is not None else _ms(hi, end=True))
        )
    return out


def _ms(minute: int | None, *, end: bool = False) -> int | None:
    if minute is None:
        return None
    return minute * BUCKET_MS + (BUCKET_MS - 1 if end else 0)


def _few_entries(conn: sqlite3.Connection, tree: Any, timeout_s: float) -> bool:
    """True when the bucket table bounds the filter to at most KEY_ENTRY_FIRST_MAX entries (a cheap
    superset: ts is rounded to whole minutes), so walking those entries beats the key type range."""
    if tree is None or isinstance(tree, CompiledFilter):
        return False
    bf = compile_bucket_filter(tree)
    if bf is None or bf.sql == "1":
        return False
    try:
        with Q.read_guard(conn, timeout_s):
            if not conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0]:
                return False
            n = conn.execute(
                f"SELECT coalesce(sum(b.n), 0) FROM bucket b WHERE {bf.sql}", bf.params
            ).fetchone()[0]
            n += conn.execute("SELECT count(*) FROM entry WHERE ts IS NULL").fetchone()[0]
    except Q.QueryTimeout:
        return False
    return n <= KEY_ENTRY_FIRST_MAX


def _sample_slices(conn: sqlite3.Connection) -> tuple[list[int], int, int] | None:
    lo, hi, total = conn.execute("SELECT min(id), max(id), count(*) FROM entry").fetchone()
    if not total:
        return None
    span = hi - lo + 1
    width = max(1, int(span * Q.SAMPLE_FRACTION / Q.SAMPLE_SLICES))
    step = span / Q.SAMPLE_SLICES
    ranges: list[int] = []
    for k in range(Q.SAMPLE_SLICES):
        start = lo + int(k * step)
        ranges += [start, min(hi, start + width - 1)]
    where = " OR ".join(["e.id BETWEEN ? AND ?"] * Q.SAMPLE_SLICES)
    sampled = conn.execute(f"SELECT count(*) FROM entry e WHERE ({where})", ranges).fetchone()[0]
    return ranges, total, sampled


# --- main entry point --------------------------------------------------------------------------


def distinct(
    conn: sqlite3.Connection,
    flt: Any,
    field_name: str | FieldSpec,
    top: int = DEFAULT_TOP,
    *,
    span: bool = False,
    timeout_s: float = DISTINCT_TIMEOUT_S,
    estimate_timeout_s: float = ESTIMATE_TIMEOUT_S,
    bucket_timeout_s: float = BUCKET_TIMEOUT_S,
) -> Distinct:
    """Top ``top`` values of ``field_name`` among the entries matching ``flt`` (module docstring)."""
    spec = field_name if isinstance(field_name, FieldSpec) else parse_field(field_name)
    if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= MAX_TOP:
        raise DistinctError("bad_top", "$.limit", limit=MAX_TOP)
    if not isinstance(span, bool):
        raise DistinctError("bad_span", "$.span")
    tree = flt
    cf = compile_filter(tree) if not isinstance(tree, CompiledFilter) else tree
    bucket_col = COLUMN_FIELDS[spec.arg][1] if spec.kind == "column" else None
    bf = None if bucket_col is None or isinstance(tree, CompiledFilter) else compile_bucket_filter(tree)
    bucket_filled = False
    if bf is not None:
        try:
            with Q.read_guard(conn, bucket_timeout_s):
                bucket_filled = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0])
        except Q.QueryTimeout:
            bucket_filled = False

    # 1. exact from bucket (no ts condition: whole minutes would make it a superset).
    if bucket_filled and not Q._uses_ts(tree):  # noqa: SLF001 - same package
        try:
            with Q.read_guard(conn, bucket_timeout_s):
                table = _bucket_table(conn, spec, bf)  # type: ignore[arg-type]
                sql, params = _bucket_group_sql(spec, bf, cf, table, span)  # type: ignore[arg-type]
                rows, total, groups = _run_top(conn, sql, params, span, top)
                if span:
                    rows = _refine_span(conn, spec, cf, rows)
            return _result(conn, spec, rows, total, groups, True, "bucket", span)
        except Q.QueryTimeout:
            pass

    # 2. exact over entry.
    entry_first = spec.kind == "key" and _few_entries(conn, tree, bucket_timeout_s)
    try:
        with Q.read_guard(conn, timeout_s):
            sql, params = _entry_group_sql(spec, cf, span, entry_first=entry_first)
            rows, total, groups = _run_top(conn, sql, params, span, top)
        return _result(conn, spec, rows, total, groups, True, "exact", span)
    except Q.QueryTimeout:
        pass

    # 3a. estimate from bucket (superset when the filter has a ts condition).
    if bucket_filled:
        try:
            with Q.read_guard(conn, estimate_timeout_s):
                table = _bucket_table(conn, spec, bf)  # type: ignore[arg-type]
                sql, params = _bucket_group_sql(spec, bf, cf, table, span)  # type: ignore[arg-type]
                rows, total, groups = _run_top(conn, sql, params, span, top)
                rows = [(g, n, _ms(lo), _ms(hi, end=True)) for g, n, lo, hi in rows]
            return _result(conn, spec, rows, total, groups, False, "bucket_estimate", span)
        except Q.QueryTimeout:
            pass

    # 3b. estimate from a sample of id slices.
    try:
        with Q.read_guard(conn, estimate_timeout_s):
            sample = _sample_slices(conn)
            if sample is None:
                return _result(conn, spec, [], 0, 0, True, "exact", span)
            ranges, total_entries, sampled = sample
            if not sampled:
                return _empty(spec)
            where = " AND (" + " OR ".join(["e.id BETWEEN ? AND ?"] * Q.SAMPLE_SLICES) + ")"
            sql, params = _entry_group_sql(spec, cf, span, where, entry_first=True)
            rows, total, groups = _run_top(conn, sql, [*params, *ranges], span, top)
        scale = total_entries / sampled
        rows = [(g, round(n * scale), lo, hi) for g, n, lo, hi in rows]
        return _result(conn, spec, rows, round(total * scale), groups, False, "sample", span)
    except Q.QueryTimeout:
        return _empty(spec)


def _empty(spec: FieldSpec) -> Distinct:
    return Distinct(spec.name, [], 0, 0, None, 0, False, "none")


def _labels(conn: sqlite3.Connection, spec: FieldSpec, values: list[Any]) -> dict[Any, str]:
    ids = [v for v in values if isinstance(v, int)]
    if spec.kind != "column" or spec.arg not in ("tpl", "source") or not ids:
        return {}
    marks = ",".join("?" * len(ids))
    if spec.arg == "tpl":
        sql = f"SELECT id, code FROM template WHERE id IN ({marks})"
    else:
        sql = f"SELECT id, file_name FROM source WHERE id IN ({marks})"
    with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
        return {r[0]: r[1] for r in conn.execute(sql, ids).fetchall()}


def _result(
    conn: sqlite3.Connection,
    spec: FieldSpec,
    rows: list[tuple],
    total: int,
    groups: int,
    exact: bool,
    method: Method,
    span: bool,
) -> Distinct:
    labels = _labels(conn, spec, [r[0] for r in rows])
    values: list[dict[str, Any]] = []
    for g, n, lo, hi in rows:
        item: dict[str, Any] = {
            "value": g,
            "count": n,
            "pct": round(100.0 * n / total, 2) if total else 0.0,
            "leaf": drill_leaf(spec, g),
        }
        if spec.kind == "column" and spec.arg in ("tpl", "source"):
            item["label"] = labels.get(g)
        if span:
            item["first_ts"] = lo
            item["last_ts"] = hi
        values.append(item)
    shown = sum(v["count"] for v in values)
    return Distinct(spec.name, values, max(0, total - shown), max(0, groups - len(values)), total, groups,
                    exact, method)  # fmt: skip


def as_json(result: Distinct) -> dict[str, Any]:
    """The JSON body of ``POST /api/entries/distinct``."""
    return {
        "field": result.field,
        "values": result.values,
        "others": {"count": result.others, "groups": result.other_groups},
        "total": result.total,
        "groups": result.groups,
        "exact": result.exact,
        "method": result.method,
    }


__all__ = [
    "COLUMN_FIELDS",
    "DEFAULT_TOP",
    "MAX_TOP",
    "Distinct",
    "DistinctError",
    "FieldSpec",
    "as_json",
    "distinct",
    "drill_leaf",
    "parse_field",
]
