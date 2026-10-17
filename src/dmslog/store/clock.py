"""Clock offset between the collections of a case (ROADMAP 9.15, ARQUITETURA 10.13).

Each collection group (one ERS capture, folder or file; U21) logs with the clock of its own server. The user
aligns them by picking two records that happened at the same instant, an ANCHOR in one group and a TARGET in
another: the target's group gets ``clock_offset_ms = (anchor.ts + offset of the anchor's group) - target.ts``.
A record's adjusted time is ``ts_adjusted = ts + clock_offset_ms`` of its group; ``ts`` itself never changes.

Where the offset lives: ``collection.clock_offset_ms`` (in the schema since v1, so no migration), written on
every row of the group and READ from the group's head row (``id = ers_group``, the first collection of the
group, which is never removed alone). How it was obtained (pair or manual, with the stable locators of the
two records) is kept in ``meta['clock.<group>']``. Nothing derived is rewritten: ids, hashes, events, links
and buckets stay as indexed, so a full, incremental or partial indexing give the same result and an offset
of 0 everywhere is the behavior before 9.15, byte for byte (the read functions below then call
:mod:`query` unchanged).

How the reads apply it (only when some group has a non-zero offset): groups are split into CLASSES of equal
offset, each read with the ``collection`` leaf of its groups (always, so a group unknown to the map takes no
offset). A read uses ONE map and one database snapshot from start to end (:func:`snapshot`). Inside
a class adjusted time is raw time plus a constant, so the class can be read in ``(ts, id)`` order on the
usual indexes; time conditions (``ts`` leaves) are shifted by ``-offset`` per class in the filter TREE
(:func:`shift_tree`), never by adding the offset in SQL. Grid pages merge the classes by
``(ts_adjusted, id)`` (records without time first, by id, as before); the histogram buckets each class by its
adjusted time. Arithmetic on times is done in Python integers (exact); values outside the int64 range of
SQLite are turned into the constant they imply (always or never true), never clamped into a wrong value.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import query
from .filters import (
    BUCKET_MS,
    INT64_MAX,
    INT64_MIN,
    MAX_PARAMS,
    CompiledFilter,
    FilterError,
    QueryInputError,
    compile_bucket_filter,
    compile_filter,
)

MAX_OFFSET_MS = 31 * 86_400_000  # an offset of more than a month is a wrong pair, not a clock skew
META_PREFIX = "clock."  # meta['clock.<group>']: how the offset was obtained (writer.remove_group drops it)
# Bound of the parameters of a filter compiled once per class (SQLite allows 32 766).
MAX_CLOCK_PARAMS = 30_000
_FALSE: dict[str, Any] = {"or": []}
_ANY_TIME: dict[str, Any] = {"ts": {"ge": INT64_MIN}}  # every record that has a time

# Records that have a time, whatever their adjusted value (never shifted: the leaf above, shifted, would drop
# adjusted values below INT64_MIN).
_HAS_TIME = CompiledFilter("e.ts IS NOT NULL", ())

Shifted = Callable[[int], CompiledFilter]  # delta -> the filter compiled with its times moved by delta


class ClockError(QueryInputError):
    """Invalid clock request. ``entry_not_found`` and ``collection_not_found`` are 404 in the API."""

    messages = {
        "bad_request": "give either offset_ms, or anchor and target (entry ids)",
        "bad_offset": "offset_ms must be an integer from {lo} to {hi}",
        "bad_entry": "{field} must be a positive integer entry id",
        "entry_not_found": "entry {entry_id} does not exist in this case",
        "collection_not_found": "collection {group} does not exist in this case",
        "no_time": "entry {entry_id} has no time",
        "same_collection": "the anchor and the target must be in different collections",
        "target_not_in_collection": "the target entry {entry_id} is not in collection {group}",
        "offset_too_large": "the two records are {offset_ms} ms apart; the limit is {limit} ms",
    }


# --------------------------------------------------------------------------- the offsets of a case


@dataclass(frozen=True, slots=True)
class ClockMap:
    """Offset of every collection group (0 included), as read at the start of a request."""

    offsets: Mapping[int, int]

    @property
    def active(self) -> bool:
        return any(self.offsets.values())

    def classes(self) -> list[tuple[int, tuple[int, ...] | None]]:
        """``[(offset, groups)]`` in ascending offset, groups ascending. Every class is restricted to its
        groups, even a single class: a group the map does not know never takes another group's offset."""
        by_offset: dict[int, list[int]] = {}
        for group, offset in self.offsets.items():
            by_offset.setdefault(offset, []).append(group)
        return [(o, tuple(sorted(by_offset[o]))) for o in sorted(by_offset)]

    def offset_of(self, group: int) -> int:
        return self.offsets.get(group, 0)


NO_CLOCK = ClockMap({})


def _as_offset(value: Any) -> int | None:
    """A stored offset, or None when it is not an integer within +-MAX_OFFSET_MS (edited by hand)."""
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_OFFSET_MS:
        return None
    return value


def load(conn: sqlite3.Connection) -> ClockMap:
    """The offset of every group of the case (one small query on ``collection``, head rows only).

    A read must use ONE map from start to end (:func:`snapshot`): records of a group the map does not hold
    belong to no class, and an offset set meanwhile is not seen."""
    offsets: dict[int, int] = {}
    for group, value in conn.execute("SELECT id, clock_offset_ms FROM collection WHERE id = ers_group"):
        offsets[int(group)] = _as_offset(value) or 0  # invalid value: no offset
    return ClockMap(offsets)


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[ClockMap]:
    """The clock map of the case and ONE read snapshot of the database for the whole read.

    Opens a read transaction (WAL: every statement until the end sees the same database, so an offset set or
    a collection added meanwhile is invisible to this read) and ends it on exit. A connection already in a
    transaction keeps its own."""
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")
    try:
        yield load(conn)
    finally:
        if began and conn.in_transaction:
            conn.rollback()


def entry_group(conn: sqlite3.Connection, entry_id: int) -> int | None:
    """The collection group of an entry, or None when it does not exist. Its offset comes from the map of
    the read (:meth:`ClockMap.offset_of`), never from a second read of ``collection``."""
    row = conn.execute(
        "SELECT c.ers_group FROM entry e JOIN source s ON s.id = e.source_id"
        " JOIN collection c ON c.id = s.collection_id WHERE e.id = ?",
        (entry_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def adjusted(ts: int | None, offset: int) -> int | None:
    return None if ts is None else ts + offset


# --------------------------------------------------------------------------- filter trees


def uses_ts(tree: Any) -> bool:
    """True when a ``ts`` leaf appears anywhere in the filter tree."""
    if isinstance(tree, dict):
        return any(op == "ts" or uses_ts(arg) for op, arg in tree.items())
    if isinstance(tree, list):
        return any(uses_ts(child) for child in tree)
    return False


# Truth of "ts <op> v" for EVERY int64 ts when v is above INT64_MAX (first) or below INT64_MIN (second).
_CONSTANT = {
    "eq": (False, False),
    "ne": (True, True),
    "lt": (True, False),
    "le": (True, False),
    "gt": (False, True),
    "ge": (False, True),
}


def _shift_ts(arg: Any, delta: int) -> dict[str, Any]:
    """The ``ts`` leaf whose values are moved by ``delta``, exact for every int64 ``ts`` (validated input)."""
    if isinstance(arg, dict):
        ops: dict[str, int] = {}
        for op, value in arg.items():
            moved = value + delta
            if INT64_MIN <= moved <= INT64_MAX:
                ops[op] = moved
            elif not _CONSTANT[op][0 if moved > INT64_MAX else 1]:
                return _FALSE
        return {"ts": ops} if ops else _ANY_TIME
    values = arg if isinstance(arg, list) else [arg]
    kept = [v + delta for v in values if INT64_MIN <= v + delta <= INT64_MAX]
    if not kept:
        return _FALSE
    return {"ts": kept if isinstance(arg, list) else kept[0]}


def shift_tree(tree: Any, delta: int) -> Any:
    """``tree`` with every time condition moved by ``delta`` ms (other nodes are shared, not copied).

    For a group whose offset is ``o``, ``shift_tree(tree, -o)`` on the raw ``ts`` selects exactly the records
    that ``tree`` selects on ``ts + o``. The tree must already be valid (:func:`compile_filter`)."""
    if delta == 0 or not isinstance(tree, dict) or not tree:
        return tree
    out: dict[str, Any] = {}
    for op, arg in tree.items():
        if op in ("and", "or"):
            out[op] = [shift_tree(child, delta) for child in arg]
        elif op == "not":
            out[op] = shift_tree(arg, delta)
        elif op == "ts":
            return _shift_ts(arg, delta)
        else:
            out[op] = arg
    return out


def _and(*parts: CompiledFilter) -> CompiledFilter:
    return CompiledFilter(
        " AND ".join(f"({p.sql})" for p in parts),
        tuple(v for p in parts for v in p.params),
        any(p.uses_fts for p in parts),
        any(p.uses_substr for p in parts),
        any(p.uses_regex for p in parts),
    )


def _restriction(groups: tuple[int, ...] | None) -> dict[str, Any] | None:
    """Filter tree of the records of a class; None for the single class (every record)."""
    return None if groups is None else {"collection": list(groups)}


def _class_filter(groups: tuple[int, ...] | None, flt: Any, delta: int) -> CompiledFilter:
    """Records of one class (``groups``) that satisfy ``flt`` with its times moved by ``delta``."""
    restriction = compile_filter(_restriction(groups))
    if callable(flt):
        body = flt(delta)
    elif isinstance(flt, CompiledFilter):
        body = flt  # a compiled filter cannot be shifted: callers only pass one without time conditions
    else:
        body = compile_filter(shift_tree(flt, delta))
    return _and(restriction, body)


def compile_clocked(tree: Any, clock: ClockMap) -> CompiledFilter:
    """``tree`` on ADJUSTED time, as one SQL expression (an OR of one branch per class).

    Without offsets, or without a time condition, this is :func:`compile_filter` itself."""
    plain = compile_filter(tree)  # validation first: errors and their paths are those of the user's tree
    if not clock.active or not uses_ts(tree):
        return plain
    parts = [_class_filter(groups, tree, -offset) for offset, groups in clock.classes()]
    params = tuple(v for p in parts for v in p.params)
    if len(params) > MAX_CLOCK_PARAMS:
        raise FilterError("too_many_params", limit=MAX_PARAMS)
    return CompiledFilter(
        "(" + " OR ".join(f"({p.sql})" for p in parts) + ")",
        params,
        plain.uses_fts,
        plain.uses_substr,
        plain.uses_regex,
    )


# --------------------------------------------------------------------------- grid page


_ADJ_LIMIT = INT64_MAX + MAX_OFFSET_MS  # an adjusted time can pass int64 by at most the largest offset


def parse_cursor(text: Any) -> query.Cursor | None:
    """A grid cursor in ADJUSTED time (``"<ts_adjusted>:<id>"`` or ``"null:<id>"``)."""
    if text is None or text == "":
        return None
    if isinstance(text, query.Cursor):
        return text
    if not isinstance(text, str) or len(text) > 64:
        raise query.CursorError("bad_cursor", "$.cursor", value=str(text)[:64])
    ts_text, sep, id_text = text.partition(":")
    try:
        if not sep:
            raise ValueError
        ts = None if ts_text == "null" else int(ts_text)
        id_ = int(id_text)
    except ValueError:
        raise query.CursorError("bad_cursor", "$.cursor", value=text) from None
    if not INT64_MIN <= id_ <= INT64_MAX or (ts is not None and not -_ADJ_LIMIT - 1 <= ts <= _ADJ_LIMIT):
        raise query.CursorError("bad_cursor", "$.cursor", value=text)
    return query.Cursor(ts, id_)


_ASC, _DESC = "e.ts, e.id", "e.ts DESC, e.id DESC"
Phase = tuple[str, tuple[Any, ...], str]


def _phases(cursor: query.Cursor | None, direction: str, offset: int) -> list[Phase]:
    """Keyset phases of one class in RAW time for an adjusted ``cursor`` (records without time first)."""
    if direction == "fwd":
        if cursor is None or cursor.ts is None:
            null_phase: Phase = (
                ("e.ts IS NULL", (), "e.id")
                if cursor is None
                else ("e.ts IS NULL AND e.id > ?", (cursor.id,), "e.id")
            )
            return [null_phase, ("e.ts IS NOT NULL", (), _ASC)]
        raw = cursor.ts - offset
        if raw > INT64_MAX:
            return []
        if raw < INT64_MIN:
            return [("e.ts IS NOT NULL", (), _ASC)]
        return [("(e.ts, e.id) > (?, ?)", (raw, cursor.id), _ASC)]
    nulls: Phase = ("e.ts IS NULL", (), "e.id DESC")
    if cursor is None:
        return [("e.ts IS NOT NULL", (), _DESC), nulls]
    if cursor.ts is None:
        return [("e.ts IS NULL AND e.id < ?", (cursor.id,), "e.id DESC")]
    raw = cursor.ts - offset
    if raw > INT64_MAX:
        return [("e.ts IS NOT NULL", (), _DESC), nulls]
    if raw < INT64_MIN:
        return [nulls]
    return [("(e.ts, e.id) < (?, ?)", (raw, cursor.id), _DESC), nulls]


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise query.QueryTimeout("query interrupted: time budget spent")
    return left


def _order_key(row: dict[str, Any]) -> tuple[int, int, int]:
    adj = row["ts_adjusted"]
    return (0, 0, row["id"]) if adj is None else (1, adj, row["id"])


def page(
    conn: sqlite3.Connection,
    flt: Any,
    cursor: query.Cursor | str | None,
    direction: query.Direction = "fwd",
    limit: int = query.DEFAULT_LIMIT,
    columns: list[str] | None = None,
    *,
    clock: ClockMap,
    timeout_s: float | None = query.PAGE_TIMEOUT_S,
) -> query.Page:
    """:func:`query.page` on adjusted time: rows in ``(ts_adjusted, id)`` order, each with ``ts_adjusted``.

    ``flt`` is a filter tree, a :class:`Shifted` callable, or a compiled filter WITHOUT time conditions.
    Cursors are adjusted (``"<ts_adjusted>:<id>"``). Without offsets this is :func:`query.page` unchanged.
    One keyset read per class, all within ``timeout_s`` together."""
    if not clock.active:
        return query.page(conn, flt, cursor, direction, limit, columns, timeout_s=timeout_s)
    if direction not in ("fwd", "back"):
        raise query.ColumnError("bad_direction", "$.dir")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= query.MAX_LIMIT:
        raise query.ColumnError("bad_limit", "$.limit", limit=query.MAX_LIMIT)
    if not callable(flt) and not isinstance(flt, CompiledFilter):
        compile_filter(flt)  # validation first, with the paths of the user's tree
    cur = parse_cursor(cursor)
    select, select_params, names = query._select_list(columns)
    classes = [(o, _class_filter(groups, flt, -o)) for o, groups in clock.classes()]
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    pool: list[dict[str, Any]] = []
    for offset, cf in classes:
        base = f"SELECT {select} FROM entry e WHERE ({cf.sql}) AND "
        got: list[dict[str, Any]] = []
        with query.read_guard(conn, _remaining(deadline)):
            for keyset, kparams, order in _phases(cur, direction, offset):
                want = limit + 1 - len(got)
                if want <= 0:
                    break
                sql = f"{base}{keyset} ORDER BY {order} LIMIT ?"
                got.extend(query._rows(conn.execute(sql, (*select_params, *cf.params, *kparams, want))))
        for row in got:
            row["ts_adjusted"] = adjusted(row["ts"], offset)
        pool.extend(got)
    pool.sort(key=_order_key, reverse=direction == "back")
    has_more = len(pool) > limit
    rows = pool[:limit]
    if direction == "back":
        rows.reverse()
    first = query.Cursor(rows[0]["ts_adjusted"], rows[0]["id"]) if rows else None
    last = query.Cursor(rows[-1]["ts_adjusted"], rows[-1]["id"]) if rows else None
    return query.Page(
        rows, first, last, has_more, [*query.BASE_COLUMNS, "msg", "msg_len", *names, "ts_adjusted"]
    )


# --------------------------------------------------------------------------- count, context, entry


def count(conn: sqlite3.Connection, flt: Any, *, clock: ClockMap, **kwargs: Any) -> query.CountResult:
    """:func:`query.count` with time conditions on adjusted time (unchanged without them)."""
    if not clock.active or isinstance(flt, CompiledFilter) or not uses_ts(flt):
        return query.count(conn, flt, **kwargs)
    return query.count(conn, compile_clocked(flt, clock), **kwargs)


def context(
    conn: sqlite3.Connection,
    entry_id: int,
    before: int = 50,
    after: int = 50,
    same: str = "source",
    *,
    clock: ClockMap,
    timeout_s: float | None = query.PAGE_TIMEOUT_S,
) -> query.Context | None:
    """:func:`query.context`; ``merge`` follows adjusted time and every row has ``ts_adjusted``."""
    if not clock.active:
        return query.context(conn, entry_id, before, after, same, timeout_s=timeout_s)
    for v in (before, after):  # the contract of query.context, on the values asked for
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= query.MAX_CONTEXT:
            raise query.ColumnError("bad_context", "$", limit=query.MAX_CONTEXT)
    if same not in ("tid", "source", "merge"):
        raise query.ColumnError("bad_same", "$.same")
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    merge = same == "merge"
    ctx = query.context(conn, entry_id, 0 if merge else before, 0 if merge else after, same,
                        timeout_s=_remaining(deadline))  # fmt: skip
    if ctx is None:
        return None
    with query.read_guard(conn, _remaining(deadline)):
        group = entry_group(conn, entry_id)
    offset = 0 if group is None else clock.offset_of(group)  # the map of the read, like the neighbors
    anchor = ctx.rows[ctx.anchor]
    if not merge or anchor["ts"] is None:
        if merge:  # no time: the source neighbors, as query.context does
            ctx = query.context(conn, entry_id, before, after, "source", timeout_s=_remaining(deadline))
            if ctx is None:
                return None
        for row in ctx.rows:  # one source: one offset
            row["ts_adjusted"] = adjusted(row["ts"], offset)
        return query.Context(ctx.rows, ctx.anchor, same)
    anchor["ts_adjusted"] = adjusted(anchor["ts"], offset)
    at = query.Cursor(anchor["ts_adjusted"], anchor["id"])
    timed = _HAS_TIME  # merge leaves records without time out, as before (raw presence, not adjusted)
    prev = (
        page(conn, timed, at, "back", before, clock=clock, timeout_s=_remaining(deadline)).rows
        if before
        else []
    )
    nxt = (
        page(conn, timed, at, "fwd", after, clock=clock, timeout_s=_remaining(deadline)).rows if after else []
    )
    return query.Context([*prev, anchor, *nxt], len(prev), "merge")


def decorate_entry(conn: sqlite3.Connection, entry: dict[str, Any], clock: ClockMap) -> dict[str, Any]:
    """Add ``ts_adjusted`` and ``clock_offset_ms`` to a full record when the case has offsets."""
    if clock.active:
        group = entry_group(conn, entry["id"])
        offset = 0 if group is None else clock.offset_of(group)
        entry["clock_offset_ms"] = offset
        entry["ts_adjusted"] = adjusted(entry.get("ts"), offset)
    return entry


# --------------------------------------------------------------------------- histogram


def _bucket_class(groups: tuple[int, ...] | None, tree: Any) -> CompiledFilter | None:
    restriction = compile_bucket_filter(_restriction(groups))
    body = compile_bucket_filter(tree)
    return None if restriction is None or body is None else _and(restriction, body)


def _clamp_window(lo: int, hi: int) -> tuple[int, int, int] | None:
    """``(lo_c, hi_c, c)``: the raw window clamped to int64 and the correction ``lo_c - lo`` (>= 0)."""
    lo_c, hi_c = max(lo, INT64_MIN), min(hi, INT64_MAX)
    if lo_c > hi_c:
        return None
    return lo_c, hi_c, lo_c - lo


def histogram(
    conn: sqlite3.Connection,
    flt: Any,
    bucket_ms: int | None = None,
    split: str = "sev",
    *,
    clock: ClockMap,
    timeout_s: float = query.HISTOGRAM_TIMEOUT_S,
    bucket_timeout_s: float = query.HISTOGRAM_BUCKET_TIMEOUT_S,
    estimate_timeout_s: float = query.HISTOGRAM_ESTIMATE_TIMEOUT_S,
) -> query.Histogram:
    """:func:`query.histogram` on adjusted time (``start`` is adjusted). Unchanged without offsets.

    Per class: the per-minute ``bucket`` table when the class offset is a whole number of minutes, the
    width too and the filter has no time condition (exact); otherwise an aggregation over ``entry`` by
    adjusted time. All the ``entry`` aggregations share ``timeout_s``; past it, every class not done yet
    is estimated (bucket table with the offset applied to the minute, else a sample) and ``exact`` is
    False."""
    if not clock.active:
        return query.histogram(
            conn, flt, bucket_ms, split,
            timeout_s=timeout_s, bucket_timeout_s=bucket_timeout_s, estimate_timeout_s=estimate_timeout_s,
        )  # fmt: skip
    if split not in query.SPLITS:
        raise query.HistogramError("bad_split", "$.split")
    if bucket_ms is not None and (
        isinstance(bucket_ms, bool)
        or not isinstance(bucket_ms, int)
        or not query.MIN_BUCKET_MS <= bucket_ms <= query.MAX_BUCKET_MS
    ):
        raise query.HistogramError(
            "bad_bucket_ms", "$.bucket_ms", lo=query.MIN_BUCKET_MS, hi=query.MAX_BUCKET_MS
        )
    compile_filter(flt)  # validation first
    timed = uses_ts(flt)
    entry_col, bucket_col = query._SPLIT_COLUMNS[split]
    minute = BUCKET_MS
    classes = []
    for offset, groups in clock.classes():
        tree = shift_tree(flt, -offset)
        cf = _class_filter(groups, tree, 0)
        restriction = compile_filter(_restriction(groups))
        bf = _bucket_class(groups, tree)
        classes.append((offset, cf, restriction, bf))
    with query.read_guard(conn, bucket_timeout_s):
        has_bucket = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0])
        try:
            has_rollup = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket_sev)").fetchone()[0])
        except sqlite3.OperationalError:
            has_rollup = False

    def bucket_eligible(offset: int, bf: CompiledFilter | None, width: int | None) -> bool:
        return (has_bucket and bf is not None and not timed and offset % minute == 0
                and (width is None or width % minute == 0))  # fmt: skip

    all_bucket = all(bucket_eligible(o, bf, bucket_ms) for o, _cf, _r, bf in classes)

    # window in adjusted time
    lows: list[int] = []
    highs: list[int] = []
    for offset, _cf, restriction, bf in classes:
        with query.read_guard(conn, bucket_timeout_s):
            if all_bucket:
                lo, hi = conn.execute(
                    f"SELECT min(b.minute), max(b.minute) FROM bucket b WHERE {bf.sql}",  # type: ignore[union-attr]
                    bf.params,  # type: ignore[union-attr]
                ).fetchone()
                if lo is not None:
                    lows.append(lo * minute + offset)
                    highs.append(hi * minute + minute - 1 + offset)
                continue
            sql = f"SELECT e.ts FROM entry e WHERE e.ts IS NOT NULL AND ({restriction.sql}) ORDER BY e.ts"
            lo = conn.execute(f"{sql} LIMIT 1", restriction.params).fetchone()
            if lo is not None:
                hi = conn.execute(f"{sql} DESC LIMIT 1", restriction.params).fetchone()
                lows.append(lo[0] + offset)
                highs.append(hi[0] + offset)
    if not lows:
        empty_width = bucket_ms or (minute if all_bucket else query.MIN_BUCKET_MS)
        return query.Histogram(empty_width, None, 0, split, [], True, "bucket" if all_bucket else "entry")
    width, start, n = query._layout(
        min(lows), max(highs), bucket_ms, minute if all_bucket else query.MIN_BUCKET_MS
    )
    end = start + n * width - 1

    cells: list[tuple[int, int, int]] = []

    def bucket_cells(offset: int, bf: CompiledFilter, exact: bool) -> None:
        table = (
            "bucket_sev" if exact and has_rollup and split != "tpl" and "tpl_id" not in bf.sql else "bucket"
        )
        rows = conn.execute(
            f"SELECT b.minute, {bucket_col} AS g, sum(b.n) FROM {table} b"
            f" WHERE {bf.sql} GROUP BY b.minute, g",
            bf.params,
        ).fetchall()
        cells.extend(((m * minute + offset) // width * width, g, c) for m, g, c in rows)

    def entry_cells(
        offset: int, cf: CompiledFilter, extra: str = "", extra_params: tuple[Any, ...] = ()
    ) -> list:
        window = _clamp_window(start - offset, end - offset)
        if window is None:
            return []
        lo_c, hi_c, correction = window
        rows = conn.execute(
            f"SELECT (e.ts - ? + ?) / ? AS k, {entry_col} AS g, count(*) FROM entry e"
            f" WHERE e.ts BETWEEN ? AND ?{extra} AND ({cf.sql}) GROUP BY k, g",
            (lo_c, correction, width, lo_c, hi_c, *extra_params, *cf.params),
        ).fetchall()
        return [(start + k * width, g, c) for k, g, c in rows]

    exact_all = True
    methods: set[str] = set()
    pending: list[tuple[int, CompiledFilter, CompiledFilter | None]] = []
    for offset, cf, _unused, bf in classes:
        if bucket_eligible(offset, bf, width):
            with query.read_guard(conn, bucket_timeout_s):
                bucket_cells(offset, bf, True)  # type: ignore[arg-type]
            methods.add("bucket")
        else:
            pending.append((offset, cf, bf))
    deadline = time.monotonic() + timeout_s
    done = 0
    try:
        for offset, cf, _bf in pending:
            with query.read_guard(conn, _remaining(deadline)):
                cells.extend(entry_cells(offset, cf))
            done += 1
            methods.add("entry")
    except query.QueryTimeout:
        exact_all = False
    if not exact_all:
        estimate_deadline = time.monotonic() + estimate_timeout_s
        sample: tuple[str, tuple[int, ...], float] | None = None
        for offset, cf, bf in pending[done:]:
            if has_bucket and bf is not None:
                with query.read_guard(conn, _remaining(estimate_deadline)):
                    bucket_cells(offset, bf, False)
                methods.add("bucket_estimate")
                continue
            with query.read_guard(conn, _remaining(estimate_deadline)):
                if sample is None:
                    sample = _sample_slices(conn)
                where, ranges, scale = sample
                if scale:
                    got = entry_cells(offset, cf, f" AND ({where})", ranges)
                    cells.extend((b, g, round(c * scale)) for b, g, c in got)
            methods.add("sample")
    if exact_all:
        method = "bucket" if methods <= {"bucket"} else "entry"
    else:
        method = "sample" if "sample" in methods else "bucket_estimate"
    return query._assemble(cells, width, start, n, split, exact_all, method)


def _sample_slices(conn: sqlite3.Connection) -> tuple[str, tuple[int, ...], float]:
    """The id slices of :func:`query._sample_histogram` and the scale (0 when the table is empty)."""
    lo, hi, total = conn.execute("SELECT min(id), max(id), count(*) FROM entry").fetchone()
    if not total:
        return "0", (), 0.0
    span = hi - lo + 1
    slice_w = max(1, int(span * query.SAMPLE_FRACTION / query.SAMPLE_SLICES))
    step = span / query.SAMPLE_SLICES
    ranges: list[int] = []
    for k in range(query.SAMPLE_SLICES):
        first = lo + int(k * step)
        ranges += [first, min(hi, first + slice_w - 1)]
    where = " OR ".join(["e.id BETWEEN ? AND ?"] * query.SAMPLE_SLICES)
    sampled = conn.execute(f"SELECT count(*) FROM entry e WHERE ({where})", ranges).fetchone()[0]
    return where, tuple(ranges), (total / sampled if sampled else 0.0)


# --------------------------------------------------------------------------- setting an offset


def _entry_ref(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT e.id, e.ts, h.id, h.clock_offset_ms, c.id, s.member, e.line_no FROM entry e"
        " JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id"
        " JOIN collection h ON h.id = c.ers_group WHERE e.id = ?",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise ClockError("entry_not_found", "$", entry_id=entry_id)
    eid, ts, group, offset, collection, member, line_no = row
    return {"entry_id": eid, "ts": ts, "group": group, "offset": _as_offset(offset) or 0,
            "collection": collection, "member": member, "line_no": line_no}  # fmt: skip


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= INT64_MAX:
        raise ClockError("bad_entry", f"$.{field}", field=field)
    return value


def _source(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def describe(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """``{group: {"clock_offset_ms", "clock_source"}}`` for every group (``clock_source`` None: no record)."""
    sources = {
        key[len(META_PREFIX) :]: value
        for key, value in conn.execute(
            "SELECT key, value FROM meta WHERE key >= ? AND key < ?", (META_PREFIX, "clock/")
        )
    }
    out: dict[int, dict[str, Any]] = {}
    for group, value in conn.execute("SELECT id, clock_offset_ms FROM collection WHERE id = ers_group"):
        offset = _as_offset(value) or 0
        out[int(group)] = {
            "clock_offset_ms": offset,
            "clock_source": _source(sources.get(str(group))) if offset else None,
        }
    return out


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def set_offset(conn: sqlite3.Connection, group: int, body: Mapping[str, Any]) -> dict[str, Any]:
    """Set the offset of ``group`` from ``{"offset_ms": n}`` or ``{"anchor": id, "target": id}`` (the target
    in ``group``, the anchor in another group), with ``"dry_run": true`` to compute without writing.

    One write transaction (``BEGIN IMMEDIATE``): the offset on every row of the group and its record in
    ``meta['clock.<group>']`` (removed when the offset becomes 0). Returns the new state."""
    manual = "offset_ms" in body
    pair = "anchor" in body or "target" in body
    if manual == pair:
        raise ClockError("bad_request", "$")
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ClockError("bad_request", "$.dry_run")
    if manual:
        offset = body["offset_ms"]
        if isinstance(offset, bool) or not isinstance(offset, int) or abs(offset) > MAX_OFFSET_MS:
            raise ClockError("bad_offset", "$.offset_ms", lo=-MAX_OFFSET_MS, hi=MAX_OFFSET_MS)
    else:
        anchor_id = _positive_id(body.get("anchor"), "anchor")
        target_id = _positive_id(body.get("target"), "target")
    own = not conn.in_transaction
    if own and not dry_run:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT clock_offset_ms FROM collection WHERE id = ? AND ers_group = ?", (group, group)
        ).fetchone()
        if row is None:
            raise ClockError("collection_not_found", "$", group=group)
        previous = _as_offset(row[0]) or 0
        result: dict[str, Any] = {"group": group, "previous_ms": previous}
        if manual:
            record: dict[str, Any] = {"method": "manual"}
        else:
            anchor = _entry_ref(conn, anchor_id)
            target = _entry_ref(conn, target_id)
            for ref in (anchor, target):
                if ref["ts"] is None:
                    raise ClockError("no_time", "$", entry_id=ref["entry_id"])
            if target["group"] != group:
                raise ClockError("target_not_in_collection", "$.target", entry_id=target_id, group=group)
            if anchor["group"] == group:
                raise ClockError("same_collection", "$.anchor")
            offset = anchor["ts"] + anchor["offset"] - target["ts"]
            if abs(offset) > MAX_OFFSET_MS:
                raise ClockError("offset_too_large", "$", offset_ms=offset, limit=MAX_OFFSET_MS)
            record = {
                "method": "pair",
                "anchor": {k: anchor[k] for k in ("group", "collection", "member", "line_no", "ts")},
                "target": {k: target[k] for k in ("group", "collection", "member", "line_no", "ts")},
            }
            result["anchor"] = {
                "entry_id": anchor_id,
                "ts": anchor["ts"],
                "ts_adjusted": anchor["ts"] + anchor["offset"],
            }
            result["target"] = {
                "entry_id": target_id,
                "ts": target["ts"],
                "ts_adjusted": target["ts"] + offset,
            }
            result["skew_ms"] = -offset  # how far the target's clock runs ahead of the anchor's (adjusted)
        record["set_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        result["clock_offset_ms"] = offset
        result["clock_source"] = record if offset else None
        result["applied"] = not dry_run
        if not dry_run:
            conn.execute("UPDATE collection SET clock_offset_ms = ? WHERE ers_group = ?", (offset, group))
            key = f"{META_PREFIX}{group}"
            if offset:
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, _canonical(record))
                )
            else:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        if own and not dry_run:
            conn.commit()
    except BaseException:
        if own and not dry_run and conn.in_transaction:
            conn.rollback()
        raise
    return result


def reset_offset(conn: sqlite3.Connection, group: int) -> dict[str, Any]:
    """Offset of ``group`` back to 0 (its record removed)."""
    return set_offset(conn, group, {"offset_ms": 0})


__all__ = [
    "MAX_OFFSET_MS",
    "META_PREFIX",
    "NO_CLOCK",
    "ClockError",
    "ClockMap",
    "compile_clocked",
    "context",
    "count",
    "decorate_entry",
    "describe",
    "entry_group",
    "histogram",
    "load",
    "page",
    "parse_cursor",
    "reset_offset",
    "set_offset",
    "shift_tree",
    "snapshot",
    "uses_ts",
]
