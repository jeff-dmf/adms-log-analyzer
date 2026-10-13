"""Read-only queries for the grid and the detail panel (ARQUITETURA 9.4, 10).

Every function runs on a read connection (``db.connect(path, readonly=True)``) and,
as defense in depth, under an SQLite authorizer that only allows reads and a progress
handler that enforces a time budget. Filters come from :mod:`filters` and are always
bound as parameters.

Global order is ``(ts, id)`` with NULL ``ts`` (``plain`` envelope) first, as SQLite
sorts NULLs. Cursors are ``"<ts>:<id>"``; a NULL ``ts`` is written ``null:<id>``.
To jump to an instant ``t``, page forward from the cursor ``"<t>:-1"``.

``histogram`` (task 2.8) counts entries per time bucket for the timeline, split by severity,
source or template. It reads the per-minute ``bucket`` table when the filter allows and falls
back to ``entry`` (and, on timeout, to an estimate).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from .filters import (
    BUCKET_MS,
    INT64_MAX,
    INT64_MIN,
    CompiledFilter,
    QueryInputError,
    compile_bucket_filter,
    compile_filter,
    set_regex_deadline,
)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MSG_PREVIEW_CHARS = 4096  # grid rows carry at most this much of msg; get_entry returns all
MAX_COLUMNS = 32
PAGE_TIMEOUT_S = 5.0
COUNT_TIMEOUT_S = 2.0
ESTIMATE_TIMEOUT_S = 1.0
MAX_CONTEXT = 500
PROGRESS_OPS = 10_000  # SQLite VM instructions between deadline checks
SAMPLE_SLICES = 20
SAMPLE_FRACTION = 0.02

BASE_COLUMNS = (
    "id",
    "ts",
    "source_id",
    "line_no",
    "nlines",
    "pid",
    "tid",
    "level",
    "sev",
    "tpl_id",
    "flags",
)
_BASE_SELECT = (
    "e.id, e.ts, e.source_id, e.line_no, e.nlines, e.pid, e.tid, e.level, e.sev, e.tpl_id, e.flags, "
    f"substr(e.msg, 1, {MSG_PREVIEW_CHARS}) AS msg, length(e.msg) AS msg_len"
)
# Optional columns: name -> SQL (constants only). Dynamic "fields.<name>" are added below.
EXTRA_COLUMNS = {
    "event_id": "e.event_id",
    "byte_off": "e.byte_off",
    "tpl": "(SELECT t.code FROM template t WHERE t.id = e.tpl_id)",
    "has_body": "((e.flags & 2) <> 0)",
}
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")

_ALLOWED_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    if hasattr(sqlite3, name)
)

_ALLOWED_PRAGMAS = frozenset({"data_version"})

Direction = Literal["fwd", "back"]


class QueryTimeout(RuntimeError):
    """The query exceeded its time budget and was interrupted."""


class CursorError(QueryInputError):
    messages = {"bad_cursor": "invalid cursor {value!r} (expected '<ts>:<id>' or 'null:<id>')"}


class ColumnError(QueryInputError):
    messages = {
        "unknown_column": "unknown column {name!r}",
        "too_many_columns": "more than {limit} columns requested",
        "bad_limit": "limit must be an integer from 1 to {limit}",
        "bad_direction": "direction must be 'fwd' or 'back'",
        "bad_same": "same must be 'tid', 'source' or 'merge'",
        "bad_context": "before and after must be integers from 0 to {limit}",
    }


@dataclass(frozen=True, slots=True)
class Cursor:
    ts: int | None
    id: int

    @classmethod
    def parse(cls, text: str | None) -> Cursor | None:
        if text is None or text == "":
            return None
        if not isinstance(text, str) or len(text) > 64:
            raise CursorError("bad_cursor", "$.cursor", value=str(text)[:64])
        ts_text, sep, id_text = text.partition(":")
        try:
            if not sep:
                raise ValueError
            ts = None if ts_text == "null" else int(ts_text)
            id_ = int(id_text)
        except ValueError:
            raise CursorError("bad_cursor", "$.cursor", value=text) from None
        for v in (ts, id_):
            if v is not None and not INT64_MIN <= v <= INT64_MAX:
                raise CursorError("bad_cursor", "$.cursor", value=text)
        return cls(ts, id_)

    def __str__(self) -> str:
        return f"{'null' if self.ts is None else self.ts}:{self.id}"


@dataclass(slots=True)
class Page:
    rows: list[dict[str, Any]]  # always in ascending (ts, id) order
    first: Cursor | None  # cursor of rows[0] (use with "back")
    last: Cursor | None  # cursor of rows[-1] (use with "fwd")
    has_more: bool  # more rows exist in the requested direction
    columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CountResult:
    n: int | None  # None when not even an estimate was possible in time
    exact: bool
    method: Literal["exact", "bucket", "sample", "none"]


@dataclass(slots=True)
class Context:
    rows: list[dict[str, Any]]  # ascending, anchor included
    anchor: int  # index of the requested entry in rows
    same: str


# --- guards ---------------------------------------------------------------------------------


def _authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, src: str | None) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    # FTS5 reads "PRAGMA data_version" internally (no argument = read only).
    if action == sqlite3.SQLITE_PRAGMA and arg1 in _ALLOWED_PRAGMAS and arg2 is None:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def read_guard(conn: sqlite3.Connection, timeout_s: float | None) -> Iterator[Callable[[], bool]]:
    """Allow only reads on ``conn`` and interrupt statements after ``timeout_s`` seconds.

    Yields a function that tells whether the deadline was reached.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    expired = False

    def progress() -> int:
        nonlocal expired
        if deadline is not None and time.monotonic() >= deadline:  # >= so timeout 0 expires on coarse clocks
            expired = True
            return 1
        return 0

    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(progress, PROGRESS_OPS)
    previous_regex_deadline = set_regex_deadline(deadline)  # the text.regex function checks it per row
    try:
        yield lambda: expired
    except sqlite3.OperationalError as exc:
        if expired or (deadline is not None and time.monotonic() >= deadline):
            raise QueryTimeout(f"query interrupted after {timeout_s} s") from exc
        raise
    finally:
        set_regex_deadline(previous_regex_deadline)
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)


def _compiled(flt: Any) -> CompiledFilter:
    return flt if isinstance(flt, CompiledFilter) else compile_filter(flt)


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _select_list(columns: list[str] | None) -> tuple[str, list[Any], list[str]]:
    """Build the SELECT list for ``columns`` (whitelist) and its parameters."""
    columns = list(columns or [])
    if len(columns) > MAX_COLUMNS:
        raise ColumnError("too_many_columns", "$.columns", limit=MAX_COLUMNS)
    parts = [_BASE_SELECT]
    params: list[Any] = []
    names: list[str] = []
    for k, name in enumerate(columns):
        if not isinstance(name, str):
            raise ColumnError("unknown_column", f"$.columns[{k}]", name=str(name)[:64])
        if name in names:
            continue
        if name in EXTRA_COLUMNS:
            parts.append(f'{EXTRA_COLUMNS[name]} AS "{name}"')
        elif name.startswith("fields.") and _FIELD_NAME.match(name[7:]):
            # The alias is checked against _FIELD_NAME, the JSON path is a parameter.
            parts.append(f'json_extract(e.fields, ?) AS "{name}"')
            params.append("$." + name[7:])
        else:
            raise ColumnError("unknown_column", f"$.columns[{k}]", name=name[:64])
        names.append(name)
    return ", ".join(parts), params, names


# --- page -----------------------------------------------------------------------------------


def page(
    conn: sqlite3.Connection,
    flt: Any,
    cursor: Cursor | str | None,
    direction: Direction = "fwd",
    limit: int = DEFAULT_LIMIT,
    columns: list[str] | None = None,
    *,
    timeout_s: float | None = PAGE_TIMEOUT_S,
) -> Page:
    """One page of the grid by keyset on ``(ts, id)``, never OFFSET.

    ``fwd`` returns the rows after ``cursor`` (from the start when None); ``back`` the
    rows before it (the last page when None). Rows come back in ascending order.
    """
    if direction not in ("fwd", "back"):
        raise ColumnError("bad_direction", "$.dir")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ColumnError("bad_limit", "$.limit", limit=MAX_LIMIT)
    if isinstance(cursor, str):
        cursor = Cursor.parse(cursor)
    cf = _compiled(flt)
    select, select_params, names = _select_list(columns)
    base = f"SELECT {select} FROM entry e WHERE ({cf.sql}) AND "

    # Each phase: (keyset SQL, keyset params, ORDER BY). NULL ts sort first.
    phases: list[tuple[str, tuple[Any, ...], str]] = []
    asc, desc = "e.ts, e.id", "e.ts DESC, e.id DESC"
    if direction == "fwd":
        if cursor is None or cursor.ts is None:
            after = INT64_MIN if cursor is None else cursor.id
            phases.append(("e.ts IS NULL AND e.id > ?", (after,), "e.id"))
            phases.append(("e.ts IS NOT NULL", (), asc))
        else:
            phases.append(("(e.ts, e.id) > (?, ?)", (cursor.ts, cursor.id), asc))
    else:
        if cursor is None:
            phases.append(("e.ts IS NOT NULL", (), desc))
            phases.append(("e.ts IS NULL", (), "e.id DESC"))
        elif cursor.ts is None:
            phases.append(("e.ts IS NULL AND e.id < ?", (cursor.id,), "e.id DESC"))
        else:
            phases.append(("(e.ts, e.id) < (?, ?)", (cursor.ts, cursor.id), desc))
            phases.append(("e.ts IS NULL", (), "e.id DESC"))

    rows: list[dict[str, Any]] = []
    with read_guard(conn, timeout_s):
        for keyset, kparams, order in phases:
            want = limit + 1 - len(rows)
            if want <= 0:
                break
            sql = f"{base}{keyset} ORDER BY {order} LIMIT ?"
            rows.extend(_rows(conn.execute(sql, (*select_params, *cf.params, *kparams, want))))
    has_more = len(rows) > limit
    rows = rows[:limit]
    if direction == "back":
        rows.reverse()
    first = Cursor(rows[0]["ts"], rows[0]["id"]) if rows else None
    last = Cursor(rows[-1]["ts"], rows[-1]["id"]) if rows else None
    return Page(rows, first, last, has_more, [*BASE_COLUMNS, "msg", "msg_len", *names])


# --- count ----------------------------------------------------------------------------------


def count(
    conn: sqlite3.Connection,
    flt: Any,
    timeout_s: float = COUNT_TIMEOUT_S,
    *,
    estimate_timeout_s: float = ESTIMATE_TIMEOUT_S,
) -> CountResult:
    """Exact ``count(*)`` within ``timeout_s``; otherwise an estimate.

    The estimate comes from the per-minute ``bucket`` table when the filter can be
    expressed on it (source/sev/tpl/ts) and the table is filled, else from a sample of
    ``id`` slices extrapolated to the whole table.
    """
    tree = flt
    cf = _compiled(flt)
    try:
        with read_guard(conn, timeout_s):
            n = conn.execute(f"SELECT count(*) FROM entry e WHERE {cf.sql}", cf.params).fetchone()[0]
        return CountResult(n, True, "exact")
    except QueryTimeout:
        pass

    if not isinstance(tree, CompiledFilter):
        bf = compile_bucket_filter(tree)
        if bf is not None:
            try:
                with read_guard(conn, estimate_timeout_s):
                    if conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0]:
                        sql = f"SELECT coalesce(sum(b.n), 0) FROM bucket b WHERE {bf.sql}"
                        return CountResult(conn.execute(sql, bf.params).fetchone()[0], False, "bucket")
            except QueryTimeout:
                pass

    try:
        with read_guard(conn, estimate_timeout_s):
            return _sample_estimate(conn, cf)
    except QueryTimeout:
        return CountResult(None, False, "none")


def _sample_estimate(conn: sqlite3.Connection, cf: CompiledFilter) -> CountResult:
    lo, hi, total = conn.execute("SELECT min(id), max(id), count(*) FROM entry").fetchone()
    if not total:
        return CountResult(0, True, "exact")
    span = hi - lo + 1
    width = max(1, int(span * SAMPLE_FRACTION / SAMPLE_SLICES))
    step = span / SAMPLE_SLICES
    ranges: list[int] = []
    for k in range(SAMPLE_SLICES):
        start = lo + int(k * step)
        ranges += [start, min(hi, start + width - 1)]
    where = " OR ".join(["e.id BETWEEN ? AND ?"] * SAMPLE_SLICES)
    sampled, matched = conn.execute(
        f"SELECT count(*), coalesce(sum({cf.sql}), 0) FROM entry e WHERE ({where})",
        (*cf.params, *ranges),
    ).fetchone()
    if not sampled:
        return CountResult(None, False, "none")
    return CountResult(round(matched * total / sampled), False, "sample")


# --- histogram ------------------------------------------------------------------------------

HISTOGRAM_TIMEOUT_S = 3.0
HISTOGRAM_BUCKET_TIMEOUT_S = 2.0
HISTOGRAM_ESTIMATE_TIMEOUT_S = 1.0
MAX_BUCKETS = 2000
TARGET_BUCKETS = 400
MIN_BUCKET_MS = 1000
MAX_BUCKET_MS = 7 * 86_400_000
MAX_SERIES = 20
# Bucket widths the automatic choice picks from (ms): seconds, minutes, hours, days.
BUCKET_LADDER_MS = (
    1000, 2000, 5000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000,
    3_600_000, 7_200_000, 10_800_000, 21_600_000, 43_200_000, 86_400_000, 172_800_000, 604_800_000,
)  # fmt: skip
SPLITS = ("sev", "source", "tpl")
_SPLIT_COLUMNS = {
    "sev": ("e.sev", "b.sev"),
    "source": ("e.source_id", "b.source_id"),
    "tpl": ("e.tpl_id", "b.tpl_id"),
}


class HistogramError(QueryInputError):
    messages = {
        "bad_split": "split must be 'sev', 'source' or 'tpl'",
        "bad_bucket_ms": "bucket_ms must be null or an integer from {lo} to {hi}",
    }


@dataclass(slots=True)
class Histogram:
    bucket_ms: int  # width actually used (may differ from the request)
    start: int | None  # epoch ms (log clock read as UTC) of the first bucket; None when empty
    n_buckets: int
    split: str
    series: list[dict[str, Any]]  # {"key": int | None, "counts": [int] * n_buckets}; key None = "other"
    exact: bool
    method: Literal["bucket", "entry", "bucket_estimate", "sample"]
    total: int = 0


def _uses_ts(tree: Any) -> bool:
    """True when a ``ts`` leaf appears anywhere in the (already validated) filter tree."""
    if isinstance(tree, dict):
        return any(op == "ts" or _uses_ts(arg) for op, arg in tree.items())
    if isinstance(tree, list):
        return any(_uses_ts(child) for child in tree)
    return False


def pick_bucket_ms(span_ms: int, requested: int | None, min_ms: int) -> int:
    """Bucket width for a window of ``span_ms``: the request, raised so that at most
    MAX_BUCKETS buckets result; automatically about TARGET_BUCKETS buckets. Never below ``min_ms``."""
    if requested is None:
        for width in BUCKET_LADDER_MS:
            if width >= min_ms and span_ms / width <= TARGET_BUCKETS:
                return width
        return BUCKET_LADDER_MS[-1]
    width = max(requested, min_ms)
    if span_ms / width <= MAX_BUCKETS:
        return width
    for ladder in BUCKET_LADDER_MS:
        if ladder >= width and span_ms / ladder <= MAX_BUCKETS:
            return ladder
    return BUCKET_LADDER_MS[-1]


def _layout(wmin: int, wmax: int, requested: int | None, min_ms: int) -> tuple[int, int, int]:
    """(bucket_ms, start, n_buckets) covering ``wmin..wmax`` (inclusive epoch ms)."""
    width = pick_bucket_ms(wmax - wmin + 1, requested, min_ms)
    start = wmin - wmin % width
    n = (wmax - start) // width + 1
    if n > MAX_BUCKETS:  # only possible with the widest ladder step on a huge window
        raise HistogramError("bad_bucket_ms", "$.bucket_ms", lo=MIN_BUCKET_MS, hi=MAX_BUCKET_MS)
    return width, start, n


def _assemble(
    cells: list[tuple[int, int, int]], width: int, start: int, n: int, split: str, exact: bool, method: str
) -> Histogram:
    """``cells`` are ``(bucket_start_ms, key, count)``; builds dense series, merging the tail into 'other'."""
    per_key: dict[int, list[int]] = {}
    for bstart, key, cnt in cells:
        idx = (bstart - start) // width
        if 0 <= idx < n:
            per_key.setdefault(key, [0] * n)[idx] += cnt
    if per_key:  # trim empty buckets at both ends so every path reports the extent of the data
        first = min(next(i for i, c in enumerate(v) if c) for v in per_key.values() if any(v))
        last = max(
            n - 1 - next(i for i, c in enumerate(reversed(v)) if c) for v in per_key.values() if any(v)
        )
        per_key = {k: v[first : last + 1] for k, v in per_key.items()}
        start, n = start + first * width, last - first + 1
    totals = {k: sum(v) for k, v in per_key.items()}
    keys = sorted(per_key, key=lambda k: (-totals[k], k)) if split != "sev" else sorted(per_key)
    series: list[dict[str, Any]] = []
    if len(keys) > MAX_SERIES:
        head, tail = keys[: MAX_SERIES - 1], keys[MAX_SERIES - 1 :]
        other = [sum(per_key[k][i] for k in tail) for i in range(n)]
        series = [{"key": k, "counts": per_key[k]} for k in head] + [{"key": None, "counts": other}]
    else:
        series = [{"key": k, "counts": per_key[k]} for k in keys]
    return Histogram(width, start if series else None, n if series else 0, split, series, exact, method,
                     sum(totals.values()))  # fmt: skip


def histogram(
    conn: sqlite3.Connection,
    flt: Any,
    bucket_ms: int | None = None,
    split: str = "sev",
    *,
    timeout_s: float = HISTOGRAM_TIMEOUT_S,
    bucket_timeout_s: float = HISTOGRAM_BUCKET_TIMEOUT_S,
    estimate_timeout_s: float = HISTOGRAM_ESTIMATE_TIMEOUT_S,
) -> Histogram:
    """Entries per time bucket, split by ``sev``, ``source`` or ``tpl``.

    ``ts`` is epoch ms of the log clock read as UTC. Entries without ts are not counted.
    Order of attempts: (1) the per-minute ``bucket`` table (exact) when the filter is expressible
    on it, has no ``ts`` condition and the width is a whole number of minutes; (2) exact
    aggregation over ``entry`` within ``timeout_s``; (3) on timeout, an estimate from the bucket
    table (minute-rounded) or from a sample of the ``entry`` table. ``exact`` tells which.
    Raises QueryTimeout when even the estimate does not fit.
    """
    if split not in SPLITS:
        raise HistogramError("bad_split", "$.split")
    if bucket_ms is not None and (
        isinstance(bucket_ms, bool)
        or not isinstance(bucket_ms, int)
        or not MIN_BUCKET_MS <= bucket_ms <= MAX_BUCKET_MS
    ):
        raise HistogramError("bad_bucket_ms", "$.bucket_ms", lo=MIN_BUCKET_MS, hi=MAX_BUCKET_MS)
    tree = flt
    cf = _compiled(flt)
    bf = None if isinstance(tree, CompiledFilter) else compile_bucket_filter(tree)
    entry_col, bucket_col = _SPLIT_COLUMNS[split]
    minute_ms = BUCKET_MS
    use_bucket = False
    if bf is not None:
        with read_guard(conn, bucket_timeout_s):
            use_bucket = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket)").fetchone()[0])
    bucket_ok = use_bucket and (bucket_ms is None or bucket_ms % minute_ms == 0)
    exact_ok = bucket_ok and not _uses_ts(tree)

    def bucket_window() -> tuple[int, int] | None:
        with read_guard(conn, bucket_timeout_s):
            lo, hi = conn.execute(
                f"SELECT min(b.minute), max(b.minute) FROM bucket b WHERE {bf.sql}",
                bf.params,  # type: ignore[union-attr]
            ).fetchone()
        return None if lo is None else (lo * minute_ms, hi * minute_ms + minute_ms - 1)

    def entry_window() -> tuple[int, int] | None:
        with read_guard(conn, bucket_timeout_s):
            lo = conn.execute("SELECT min(ts) FROM entry WHERE ts IS NOT NULL").fetchone()[0]
            if lo is None:
                return None
            hi = conn.execute("SELECT max(ts) FROM entry WHERE ts IS NOT NULL").fetchone()[0]
        return lo, hi

    def rollup_ok() -> bool:
        # bucket_sev is template-free: usable when the filter never touches tpl_id and the split is
        # sev or source (it is exact, like bucket). Old databases without the table fall back.
        if bf is None or split == "tpl" or "tpl_id" in bf.sql:
            return False
        try:
            with read_guard(conn, bucket_timeout_s):
                return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM bucket_sev)").fetchone()[0])
        except sqlite3.OperationalError:
            return False

    def run_bucket(exact: bool) -> Histogram:
        # One scan grouped by (minute, key); the window and the coarser buckets come from regrouping.
        table = "bucket_sev" if exact and rollup_ok() else "bucket"
        with read_guard(conn, bucket_timeout_s):
            rows = conn.execute(
                f"SELECT b.minute, {bucket_col} AS g, sum(b.n) FROM {table} b "
                f"WHERE {bf.sql} GROUP BY b.minute, g",  # type: ignore[union-attr]
                bf.params,  # type: ignore[union-attr]
            ).fetchall()
        if not rows:
            return Histogram(bucket_ms or minute_ms, None, 0, split, [], exact, "bucket")
        lo = min(r[0] for r in rows) * minute_ms
        hi = max(r[0] for r in rows) * minute_ms + minute_ms - 1
        width, start, n = _layout(lo, hi, bucket_ms, minute_ms)
        cells = [(m * minute_ms // width * width, g, c) for m, g, c in rows]
        return _assemble(cells, width, start, n, split, exact, "bucket" if exact else "bucket_estimate")

    if exact_ok:
        return run_bucket(True)

    win = bucket_window() if bucket_ok else entry_window()
    if win is None:
        return Histogram(bucket_ms or MIN_BUCKET_MS, None, 0, split, [], True, "entry")
    width, start, n = _layout(win[0], win[1], bucket_ms, MIN_BUCKET_MS)
    try:
        with read_guard(conn, timeout_s):
            rows = conn.execute(
                f"SELECT e.ts / ? AS k, {entry_col} AS g, count(*) FROM entry e "
                f"WHERE e.ts IS NOT NULL AND ({cf.sql}) GROUP BY k, g",
                (width, *cf.params),
            ).fetchall()
        return _assemble([(k * width, g, c) for k, g, c in rows], width, start, n, split, True, "entry")
    except QueryTimeout:
        pass
    if bucket_ok:
        try:
            return run_bucket(False)
        except QueryTimeout:
            pass
    with read_guard(conn, estimate_timeout_s):
        return _sample_histogram(conn, cf, entry_col, width, start, n, split)


def _sample_histogram(
    conn: sqlite3.Connection, cf: CompiledFilter, entry_col: str, width: int, start: int, n: int, split: str
) -> Histogram:
    lo, hi, total = conn.execute("SELECT min(id), max(id), count(*) FROM entry").fetchone()
    if not total:
        return Histogram(width, None, 0, split, [], True, "entry")
    span = hi - lo + 1
    slice_w = max(1, int(span * SAMPLE_FRACTION / SAMPLE_SLICES))
    step = span / SAMPLE_SLICES
    ranges: list[int] = []
    for k in range(SAMPLE_SLICES):
        first = lo + int(k * step)
        ranges += [first, min(hi, first + slice_w - 1)]
    where = " OR ".join(["e.id BETWEEN ? AND ?"] * SAMPLE_SLICES)
    sampled = conn.execute(f"SELECT count(*) FROM entry e WHERE ({where})", ranges).fetchone()[0]
    if not sampled:
        return Histogram(width, None, 0, split, [], False, "sample")
    rows = conn.execute(
        f"SELECT e.ts / ? AS k, {entry_col} AS g, count(*) FROM entry e "
        f"WHERE ({where}) AND e.ts IS NOT NULL AND ({cf.sql}) GROUP BY k, g",
        (width, *ranges, *cf.params),
    ).fetchall()
    scale = total / sampled
    cells = [(k * width, g, round(c * scale)) for k, g, c in rows]
    return _assemble(cells, width, start, n, split, False, "sample")


# --- context and detail ---------------------------------------------------------------------


def context(
    conn: sqlite3.Connection,
    entry_id: int,
    before: int = 50,
    after: int = 50,
    same: str = "source",
    *,
    timeout_s: float | None = PAGE_TIMEOUT_S,
) -> Context | None:
    """Neighbours of one entry. None when the entry does not exist.

    ``source``: same file, file order (id). ``tid``: same file and same TID (or no TID
    when the entry has none), file order. ``merge``: all sources, global (ts, id) order;
    entries without ts fall back to ``source``.
    """
    for v in (before, after):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_CONTEXT:
            raise ColumnError("bad_context", "$", limit=MAX_CONTEXT)
    if same not in ("tid", "source", "merge"):
        raise ColumnError("bad_same", "$.same")
    select = f"SELECT {_BASE_SELECT} FROM entry e WHERE "
    with read_guard(conn, timeout_s):
        anchor_rows = _rows(conn.execute(select + "e.id = ?", (entry_id,)))
        if not anchor_rows:
            return None
        a = anchor_rows[0]
        if same == "merge" and a["ts"] is not None:
            prev_sql = select + "(e.ts, e.id) < (?, ?) ORDER BY e.ts DESC, e.id DESC LIMIT ?"
            next_sql = select + "(e.ts, e.id) > (?, ?) ORDER BY e.ts, e.id LIMIT ?"
            key: tuple[Any, ...] = (a["ts"], a["id"])
        elif same == "tid":
            tid_sql = "e.tid IS NULL" if a["tid"] is None else "e.tid = ?"
            tid_par = () if a["tid"] is None else (a["tid"],)
            prev_sql = select + f"e.source_id = ? AND {tid_sql} AND e.id < ? ORDER BY e.id DESC LIMIT ?"
            next_sql = select + f"e.source_id = ? AND {tid_sql} AND e.id > ? ORDER BY e.id LIMIT ?"
            key = (a["source_id"], *tid_par, a["id"])
        else:
            prev_sql = select + "e.source_id = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?"
            next_sql = select + "e.source_id = ? AND e.id > ? ORDER BY e.id LIMIT ?"
            key = (a["source_id"], a["id"])
        prev = _rows(conn.execute(prev_sql, (*key, before))) if before else []
        nxt = _rows(conn.execute(next_sql, (*key, after))) if after else []
    prev.reverse()
    return Context([*prev, a, *nxt], len(prev), same)


def get_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    max_cont_lines: int | None = None,
    max_links: int = 200,
    timeout_s: float | None = PAGE_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Full record: msg and continuation lines, fields, template, source, keys, event, links.

    ``max_cont_lines`` limits the continuation lines returned (``cont_truncated`` tells
    whether lines were left out); None returns everything.
    """
    with read_guard(conn, timeout_s):
        rows = _rows(
            conn.execute(
                "SELECT e.*, b.cont AS cont, t.code AS tpl_code, t.title AS tpl_title, "
                "t.class AS tpl_class, s.member AS member, s.file_name AS file_name, "
                "s.envelope AS envelope, s.stream_id AS stream_id "
                "FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id "
                "LEFT JOIN template t ON t.id = e.tpl_id LEFT JOIN source s ON s.id = e.source_id "
                "WHERE e.id = ?",
                (entry_id,),
            )
        )
        if not rows:
            return None
        entry = rows[0]
        entry["keys"] = _rows(
            conn.execute(
                "SELECT key_type, value FROM entry_key WHERE entry_id = ? ORDER BY key_type, value",
                (entry_id,),
            )
        )
        entry["event"] = None
        if entry["event_id"] is not None:
            ev = _rows(
                conn.execute(
                    "SELECT id, kind, name, key, status, ts_start, ts_end, duration_ms, sev "
                    "FROM event WHERE id = ?",
                    (entry["event_id"],),
                )
            )
            entry["event"] = ev[0] if ev else None
        entry["links"] = _rows(
            conn.execute(
                "SELECT b_entry_id AS entry_id, kind, confidence FROM link WHERE a_entry_id = ? "
                "UNION ALL SELECT a_entry_id, kind, confidence FROM link WHERE b_entry_id = ? LIMIT ?",
                (entry_id, entry_id, max_links),
            )
        )
    cont = entry["cont"]
    entry["cont_truncated"] = False
    if cont is not None and max_cont_lines is not None:
        lines = cont.split("\n", max_cont_lines)
        if len(lines) > max_cont_lines:
            entry["cont"] = "\n".join(lines[:max_cont_lines])
            entry["cont_truncated"] = True
    raw_fields = entry.get("fields")
    try:
        entry["fields"] = json.loads(raw_fields) if raw_fields else {}
    except ValueError:
        entry["fields"] = {}
    return entry


# --------------------------------------------------------------------------- templates and unknowns

TEMPLATES_TIMEOUT_S = 5.0
UNKNOWN_DEFAULT_LIMIT = 200


def list_templates(conn: sqlite3.Connection, family: str | None = None) -> list[dict[str, Any]]:
    """Every template of the case with its record count and time span (0 records included).

    Ordered by family, then code. ``family`` restricts the list to one family code.
    """
    sql = (
        "SELECT t.id, t.code, t.family, t.class, t.sev, t.title, t.fts, t.def_version,"
        " coalesce(c.n, 0), c.first_ts, c.last_ts FROM template t"
        " LEFT JOIN (SELECT tpl_id, count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts FROM entry"
        " WHERE tpl_id > 0 GROUP BY tpl_id) c ON c.tpl_id = t.id"
    )
    params: list[Any] = []
    if family is not None:
        sql += " WHERE t.family = ?"
        params.append(family)
    sql += " ORDER BY t.family, t.code"
    with read_guard(conn, TEMPLATES_TIMEOUT_S):
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r[0], "code": r[1], "family": r[2], "class": r[3], "sev": r[4], "title": r[5],
            "fts": bool(r[6]), "def_version": r[7], "n": r[8], "first_ts": r[9], "last_ts": r[10],
        }
        for r in rows
    ]  # fmt: skip


def unknown_signatures(
    conn: sqlite3.Connection, family: str | None = None, limit: int = UNKNOWN_DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Signatures of records without a template, most frequent first (``limit`` rows)."""
    sql = "SELECT family, signature, n, first_entry_id FROM unknown_sig"
    params: list[Any] = []
    if family is not None:
        sql += " WHERE family = ?"
        params.append(family)
    sql += " ORDER BY n DESC, family, signature LIMIT ?"
    params.append(limit)
    with read_guard(conn, TEMPLATES_TIMEOUT_S):
        rows = conn.execute(sql, params).fetchall()
    return [{"family": r[0], "signature": r[1], "n": r[2], "first_entry_id": r[3]} for r in rows]


def unknown_totals(conn: sqlite3.Connection, family: str | None = None) -> dict[str, int]:
    """``{"signatures": distinct signatures, "records": records without a template}``."""
    sql = "SELECT count(*), coalesce(sum(n), 0) FROM unknown_sig"
    params: list[Any] = []
    if family is not None:
        sql += " WHERE family = ?"
        params.append(family)
    with read_guard(conn, TEMPLATES_TIMEOUT_S):
        r = conn.execute(sql, params).fetchone()
    return {"signatures": int(r[0]), "records": int(r[1])}


__all__ = [
    "BUCKET_MS",
    "ColumnError",
    "Context",
    "Histogram",
    "CountResult",
    "Cursor",
    "CursorError",
    "Page",
    "QueryTimeout",
    "context",
    "count",
    "get_entry",
    "histogram",
    "list_templates",
    "page",
    "read_guard",
    "unknown_signatures",
    "unknown_totals",
]
