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

1. ``tpl``/``source``/``sev`` with a filter that the per-minute ``bucket`` table can express:
   exact from ``bucket`` (``bucket_sev`` when the template is not involved), in one statement, plus
   either the entries without a time (no ``ts`` condition; ``bucket`` does not hold them) or, when
   the ``ts`` conditions form a window ANDed to the rest (the timeline range), the entries of the
   partial minutes at the two ends of the window (whole minutes come from ``bucket``).
   ``method="bucket"``.
2. Otherwise, exact ``GROUP BY`` over ``entry`` (joined to ``entry_key`` for keys) within
   ``timeout_s``. ``method="exact"``. Skipped when the bucket table bounds the filter to more than
   ``DISTINCT_DIRECT_SAMPLE_ROWS`` entries: such a grouping cannot fit the p95 target, so the
   estimate (step 3) answers directly. ``exact=True`` (the viewer's "Count exactly", on demand)
   always tries it within ``timeout_s`` and still falls back to the estimate on timeout. ``level``
   has no index nor bucket column, so it has no cheaper exact path than this one.
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
from functools import cache
from typing import Any, Literal

from .. import messages
from . import query as Q
from .filters import (
    BUCKET_MS,
    INT64_MAX,
    INT64_MIN,
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
# Above this many entries in the filter (bucket bound) the exact grouping of a field outside bucket
# cannot fit the p95 target (about 1 M rows/s on the 2 GB worst case): skip it and sample directly.
DISTINCT_DIRECT_SAMPLE_ROWS = 1_000_000

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
    values: list[dict[str, Any]]  # {value, count, pct, leaf[, label[, code]][, first_ts, last_ts]}
    others: int  # count of the groups after the top ones
    other_groups: int
    total: int | None  # sum of every group's count; None when nothing could be computed in time
    groups: int  # distinct values (seen in the sample when method == "sample")
    exact: bool
    method: Method


@cache
def catalog_names() -> tuple[frozenset[str], frozenset[str]]:
    """(field names, key types) the packaged adapter templates can produce."""
    from ..parse.templates import load_catalog

    fields: set[str] = set()
    key_types: set[str] = set()
    for tpl in load_catalog().templates():
        fields |= tpl.field_names
        key_types |= {k.type for k in tpl.keys}
    return frozenset(fields), frozenset(key_types)


def check_known(conn: sqlite3.Connection, spec: FieldSpec) -> None:
    """Reject a well-formed but unknown field: ``fields.<name>`` must be a field of the template
    catalog, ``key.<type>`` a key type of the catalog or present in this case (payload keys)."""
    fields, key_types = catalog_names()
    if spec.kind == "field" and spec.arg not in fields:
        raise DistinctError("bad_field", "$.field", name=spec.name[:64])
    if spec.kind == "key" and spec.arg not in key_types:
        with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
            sql = "SELECT EXISTS(SELECT 1 FROM entry_key WHERE key_type = ?)"
            present = conn.execute(sql, (spec.arg,)).fetchone()[0]
        if not present:
            raise DistinctError("bad_field", "$.field", name=spec.name[:64])


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
    spec: FieldSpec, cf: CompiledFilter, span: bool, *, entry_first: bool = False
) -> tuple[str, list[Any]]:
    """``SELECT g, n[, lo, hi]`` grouped over ``entry`` (and ``entry_key``) for the filter.

    Keys: by default the ``key_type`` range of the primary key drives the join (values come in
    order, no temporary B-tree for the grouping; cost = rows of that type). ``entry_first`` walks
    the filtered entries instead and reads their keys through ``ix_key_entry`` (covering), which is
    cheaper when the filter keeps few entries (a short time window)."""
    agg = ", min(e.ts) AS lo, max(e.ts) AS hi" if span else ""
    if spec.kind == "key":
        if entry_first:
            sql = (
                f"SELECT k.value AS g, count(*) AS n{agg} FROM entry e CROSS JOIN entry_key k "
                "INDEXED BY ix_key_entry ON k.entry_id = e.id AND k.key_type = ? "
                f"WHERE ({cf.sql}) GROUP BY k.value"
            )
        else:
            sql = (
                f"SELECT k.value AS g, count(*) AS n{agg} FROM entry_key k JOIN entry e ON e.id = k.entry_id "
                f"WHERE k.key_type = ? AND ({cf.sql}) GROUP BY k.value"
            )
        return sql, [spec.arg, *cf.params]
    if spec.kind == "field":
        sql = (
            f"SELECT json_extract(e.fields, ?) AS g, count(*) AS n{agg} FROM entry e "
            f"WHERE ({cf.sql}) GROUP BY g"
        )
        return sql, ["$." + spec.arg, *cf.params]
    column = COLUMN_FIELDS[spec.arg][0]
    return (
        f"SELECT {column} AS g, count(*) AS n{agg} FROM entry e WHERE ({cf.sql}) GROUP BY g",
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


Edge = tuple[str, list[Any]]  # extra entry branch: (complete WHERE condition over ``entry e``, params)


def _null_ts_edge(cf: CompiledFilter) -> Edge:
    return f"e.ts IS NULL AND ({cf.sql})", [*cf.params]


@dataclass(frozen=True, slots=True)
class BucketPlan:
    """Exact answer from bucket: ``bf`` over the bucket table, ``edges`` over entry, and the filter
    and time bounds that bound the span refinement (``rest`` has no ts condition; lo <= ts < hi)."""

    bf: CompiledFilter
    edges: list[Edge]
    rest: CompiledFilter
    lo: int | None = None
    hi: int | None = None


def _bucket_group_sql(
    spec: FieldSpec, bf: CompiledFilter, table: str, span: bool, edges: list[Edge]
) -> tuple[str, list[Any]]:
    """Grouping from the bucket table plus entry branches (the entries without ts, or the partial
    minutes at the edges of a time window), in one statement: one snapshot, also while the indexer
    writes. ``lo``/``hi`` are epoch ms: the bounds of the minute for bucket rows, exact for entries."""
    ecol, bcol = COLUMN_FIELDS[spec.arg]
    if span:
        lo_b, hi_b, lo_e = f"b.minute * {BUCKET_MS}", f"b.minute * {BUCKET_MS} + {BUCKET_MS - 1}", "e.ts"
    else:
        lo_b = hi_b = lo_e = "NULL"
    parts = [f"SELECT {bcol} AS g, b.n AS n, {lo_b} AS lo, {hi_b} AS hi FROM {table} b WHERE {bf.sql}"]
    params: list[Any] = [*bf.params]
    for where, extra in edges:
        parts.append(f"SELECT {ecol}, 1, {lo_e}, {lo_e} FROM entry e WHERE {where}")
        params += extra
    sql = (
        "SELECT g, sum(n) AS n, min(lo) AS lo, max(hi) AS hi FROM ("
        + " UNION ALL ".join(parts)
        + ") GROUP BY g"
    )
    return sql, params


def _conjuncts(tree: Any) -> list[Any]:
    """Top-level AND members of a (validated) tree, nested ANDs flattened."""
    out: list[Any] = []

    def flatten(node: Any) -> None:
        if isinstance(node, dict) and len(node) == 1 and isinstance(node.get("and"), list):
            for child in node["and"]:
                flatten(child)
        else:
            out.append(node)

    flatten(tree)
    return out


def _excludes_null_ts(tree: Any) -> bool:
    """True when no entry without ts can match: a ``ts`` leaf is a top-level conjunct (leaves are
    false on NULL). A ``ts`` only under or/not leaves the other branches free to match them."""
    return any(isinstance(n, dict) and len(n) == 1 and "ts" in n for n in _conjuncts(tree))


def _ts_window(tree: Any) -> tuple[list[Any], int | None, int | None] | None:
    """``(other conjuncts, lo, hi)`` when the (validated) tree is an AND of ``ts`` comparisons
    (``ge``/``gt``/``le``/``lt``) and of nodes without ``ts``: the window is ``lo <= ts < hi``
    (``None`` = open). None for any other use of ``ts`` (lists, ``eq``, ``ne``, under or/not)."""
    conjuncts = _conjuncts(tree)
    lo: int | None = None
    hi: int | None = None
    rest: list[Any] = []
    found = False
    for node in conjuncts:
        if isinstance(node, dict) and len(node) == 1 and "ts" in node:
            arg = node["ts"]
            if not isinstance(arg, dict):
                return None
            for cmp, v in arg.items():
                if cmp in ("ge", "gt"):
                    a = v + (cmp == "gt")
                    lo = a if lo is None else max(lo, a)
                elif cmp in ("le", "lt"):
                    b = v + (cmp == "le")
                    hi = b if hi is None else min(hi, b)
                else:
                    return None
            found = True
        elif Q._uses_ts(node):  # noqa: SLF001 - same package
            return None
        else:
            rest.append(node)
    return (rest, lo, hi) if found else None


def _bucket_plan(tree: Any, cf: CompiledFilter) -> BucketPlan | None:
    """How the bucket table answers ``tree`` exactly, or None.

    * no ``ts`` condition: the bucket rows of the filter plus the entries without ts;
    * a time window (:func:`_ts_window`): the bucket rows of the whole minutes inside it plus the
      entries of the partial minutes at its two ends (an indexed ``ts`` range of under a minute each).
      The edges carry only their own ``ts`` bounds plus the rest of the filter: with the window's
      bounds also present, SQLite may pick those for the index range and read the whole window.
    """
    if isinstance(tree, CompiledFilter):
        return None
    if not Q._uses_ts(tree):  # noqa: SLF001 - same package
        bf = compile_bucket_filter(tree)
        return None if bf is None else BucketPlan(bf, [_null_ts_edge(cf)], cf)
    window = _ts_window(tree)
    if window is None:
        return None
    rest, lo, hi = window
    if hi is not None and hi > INT64_MAX:
        hi = None  # le INT64_MAX: no upper bound (every ts is at most INT64_MAX)
    if lo is not None and lo > INT64_MAX:
        return None  # gt INT64_MAX: an empty window, which the entry path answers at once
    inner_lo = None if lo is None else -(-lo // BUCKET_MS) * BUCKET_MS
    inner_hi = None if hi is None else hi // BUCKET_MS * BUCKET_MS
    bounds = [b for b in (inner_lo, inner_hi) if b is not None]
    if any(not INT64_MIN <= b <= INT64_MAX for b in bounds) or (
        inner_lo is not None and inner_hi is not None and inner_lo >= inner_hi
    ):
        return None  # no whole minute inside the window: the entry path is cheap anyway
    whole: dict[str, int] = {}
    if inner_lo is not None:
        whole["ge"] = inner_lo
    if inner_hi is not None:
        whole["lt"] = inner_hi
    if not whole:
        whole["ge"] = INT64_MIN  # only "ts is set" (le INT64_MAX): every bucket minute
    bf = compile_bucket_filter({"and": [*rest, {"ts": whole}]})
    if bf is None:
        return None
    rest_cf = compile_filter({"and": rest})
    edges: list[Edge] = []
    cond = f"e.ts >= ? AND e.ts < ? AND ({rest_cf.sql})"
    if lo is not None and lo < inner_lo:  # type: ignore[operator]
        edges.append((cond, [lo, inner_lo, *rest_cf.params]))
    if hi is not None and inner_hi < hi:  # type: ignore[operator]
        edges.append((cond, [inner_hi, hi, *rest_cf.params]))
    return BucketPlan(bf, edges, rest_cf, lo, hi)


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
    conn: sqlite3.Connection, spec: FieldSpec, plan: BucketPlan, rows: list[tuple]
) -> list[tuple]:
    """Exact first/last ts of each value: the true one lies in the minute of the bound found (the
    bucket minute, or an exact entry ts), so one indexed range read per bound finds it. The range is
    that minute clipped to the window, and the filter is the rest without ts (one index range)."""
    ecol = COLUMN_FIELDS[spec.arg][0]
    rest = plan.rest
    sql_lo = f"SELECT min(e.ts) FROM entry e WHERE {ecol} = ? AND e.ts >= ? AND e.ts < ? AND ({rest.sql})"
    sql_hi = f"SELECT max(e.ts) FROM entry e WHERE {ecol} = ? AND e.ts >= ? AND e.ts < ? AND ({rest.sql})"

    def clip(minute_start: int) -> tuple[int, int]:
        a, b = minute_start, minute_start + BUCKET_MS
        return (a if plan.lo is None else max(a, plan.lo)), (b if plan.hi is None else min(b, plan.hi))

    out = []
    for g, n, lo, hi in rows:
        first, last = lo, hi
        if lo is not None and g is not None:
            found = conn.execute(sql_lo, (g, *clip(lo // BUCKET_MS * BUCKET_MS), *rest.params)).fetchone()[0]
            first = found if found is not None else lo
            found = conn.execute(sql_hi, (g, *clip(hi // BUCKET_MS * BUCKET_MS), *rest.params)).fetchone()[0]
            last = found if found is not None else hi
        out.append((g, n, first, last))
    return out


def _bucket_bound(conn: sqlite3.Connection, tree: Any, timeout_s: float | None) -> int | None:
    """Entries the filter can keep at most, from the bucket table (``ts`` rounded to whole minutes,
    so a superset) plus the entries without ts unless a top-level ts condition excludes them (a ts
    under or/not does not); None when bucket is empty, cannot express the filter or does not answer
    in time."""
    if isinstance(tree, CompiledFilter):
        return None
    bf = compile_bucket_filter(tree)
    if bf is None:
        return None
    try:
        with Q.read_guard(conn, timeout_s):
            if not conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0]:
                return None
            table = "bucket_sev" if "tpl_id" not in bf.sql and _has_table(conn, "bucket_sev") else "bucket"
            n = conn.execute(
                f"SELECT coalesce(sum(b.n), 0) FROM {table} b WHERE {bf.sql}", bf.params
            ).fetchone()[0]
            if not _excludes_null_ts(tree):
                n += conn.execute("SELECT count(*) FROM entry WHERE ts IS NULL").fetchone()[0]
    except Q.QueryTimeout:
        return None
    return n


def _sample_group_sql(spec: FieldSpec, cf: CompiledFilter, span: bool) -> tuple[str, list[Any]]:
    """Grouping over the id slices of the sample (their ranges are the trailing parameters).

    The filter is evaluated per row in the select list (as ``query.count`` does), never in WHERE:
    there the planner could prefer a ts or source index over the id ranges and read far more rows
    than the 2% sample. Values that no sampled row of the filter has are dropped (``n > 0``)."""
    match = f"({cf.sql})"
    params: list[Any] = []
    if spec.kind == "key":
        g = "k.value"
    elif spec.kind == "field":
        g = "json_extract(e.fields, ?)"
        params.append("$." + spec.arg)
    else:
        g = COLUMN_FIELDS[spec.arg][0]
    agg = f", sum({match}) AS n"
    params += cf.params
    if span:
        agg += f", min(CASE WHEN {match} THEN e.ts END) AS lo, max(CASE WHEN {match} THEN e.ts END) AS hi"
        params += [*cf.params, *cf.params]
    source = "entry e"
    if spec.kind == "key":
        source += " CROSS JOIN entry_key k INDEXED BY ix_key_entry ON k.entry_id = e.id AND k.key_type = ?"
        params.append(spec.arg)
    slices = " OR ".join(["e.id BETWEEN ? AND ?"] * Q.SAMPLE_SLICES)
    sql = f"SELECT {g} AS g{agg} FROM {source} WHERE ({slices}) GROUP BY g HAVING n > 0"
    return sql, params


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
    exact: bool = False,
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
    if not isinstance(exact, bool):
        raise DistinctError("bad_exact", "$.exact")
    check_known(conn, spec)
    tree = flt
    cf = compile_filter(tree) if not isinstance(tree, CompiledFilter) else tree
    has_bucket_column = spec.kind == "column" and COLUMN_FIELDS[spec.arg][1] is not None
    bucket_filled = False
    if has_bucket_column and not isinstance(tree, CompiledFilter):
        try:
            with Q.read_guard(conn, bucket_timeout_s):
                bucket_filled = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0])
        except Q.QueryTimeout:
            bucket_filled = False

    # 1. exact from bucket: the filter's bucket rows plus the entries bucket cannot answer.
    plan = _bucket_plan(tree, cf) if bucket_filled else None
    if plan is not None:
        try:
            with Q.read_guard(conn, bucket_timeout_s):
                table = _bucket_table(conn, spec, plan.bf)
                sql, params = _bucket_group_sql(spec, plan.bf, table, span, plan.edges)
                rows, total, groups = _run_top(conn, sql, params, span, top)
                if span:
                    rows = _refine_span(conn, spec, plan, rows)
            return _result(conn, spec, rows, total, groups, True, "bucket", span)
        except Q.QueryTimeout:
            pass

    # 2. exact over entry, unless bucket shows the filter is too large to group in time.
    bound = _bucket_bound(conn, tree, bucket_timeout_s)
    if exact or bound is None or bound <= DISTINCT_DIRECT_SAMPLE_ROWS:
        entry_first = spec.kind == "key" and bound is not None and bound <= KEY_ENTRY_FIRST_MAX
        try:
            with Q.read_guard(conn, timeout_s):
                sql, params = _entry_group_sql(spec, cf, span, entry_first=entry_first)
                rows, total, groups = _run_top(conn, sql, params, span, top)
            return _result(conn, spec, rows, total, groups, True, "exact", span)
        except Q.QueryTimeout:
            pass

    # 3a. estimate from bucket (whole minutes: a superset when the filter has a ts condition).
    bf = compile_bucket_filter(tree) if bucket_filled else None
    if bf is not None:
        edges = [] if _excludes_null_ts(tree) else [_null_ts_edge(cf)]
        try:
            with Q.read_guard(conn, estimate_timeout_s):
                table = _bucket_table(conn, spec, bf)
                sql, params = _bucket_group_sql(spec, bf, table, span, edges)
                rows, total, groups = _run_top(conn, sql, params, span, top)
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
            sql, params = _sample_group_sql(spec, cf, span)
            rows, total, groups = _run_top(conn, sql, [*params, *ranges], span, top)
        scale = total_entries / sampled
        rest = total - sum(r[1] for r in rows)
        rows = [(g, round(n * scale), lo, hi) for g, n, lo, hi in rows]
        # Scaled parts rounded one by one: the total is their sum, so top N + others = total holds.
        total = sum(r[1] for r in rows) + round(rest * scale)
        return _result(conn, spec, rows, total, groups, False, "sample", span)
    except Q.QueryTimeout:
        return _empty(spec)


def _empty(spec: FieldSpec) -> Distinct:
    return Distinct(spec.name, [], 0, 0, None, 0, False, "none")


def _labels(conn: sqlite3.Connection, spec: FieldSpec, values: list[Any]) -> dict[Any, tuple[str, str]]:
    """``{id: (label, code)}``: a template's readable title (its code when it has none) and code, or a
    source's file name (twice)."""
    ids = [v for v in values if isinstance(v, int)]
    if spec.kind != "column" or spec.arg not in ("tpl", "source") or not ids:
        return {}
    marks = ",".join("?" * len(ids))
    if spec.arg == "tpl":
        sql = f"SELECT id, coalesce(nullif(title, ''), code), code FROM template WHERE id IN ({marks})"
    else:
        sql = f"SELECT id, file_name, file_name FROM source WHERE id IN ({marks})"
    with Q.read_guard(conn, Q.PAGE_TIMEOUT_S):
        return {r[0]: (r[1], r[2]) for r in conn.execute(sql, ids).fetchall()}


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
            label, code = labels.get(g, (None, None))
            item["label"] = label
            if spec.arg == "tpl":
                item["code"] = code
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
