"""Read-only queries for the grid and the detail panel (ARQUITETURA 9.4, 10).

Every function runs on a read connection (``db.connect(path, readonly=True)``) and,
as defense in depth, under an SQLite authorizer that only allows reads and a progress
handler that enforces a time budget. Filters come from :mod:`filters` and are always
bound as parameters.

Global order is ``(ts, id)`` with NULL ``ts`` (``plain`` envelope) first, as SQLite
sorts NULLs. Cursors are ``"<ts>:<id>"``; a NULL ``ts`` is written ``null:<id>``.
To jump to an instant ``t``, page forward from the cursor ``"<t>:-1"``.

``histogram`` (bucket timeline) is part of M2 (task 2.8) and is not implemented here.
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
    try:
        yield lambda: expired
    except sqlite3.OperationalError as exc:
        if expired:
            raise QueryTimeout(f"query interrupted after {timeout_s} s") from exc
        raise
    finally:
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


__all__ = [
    "BUCKET_MS",
    "ColumnError",
    "Context",
    "CountResult",
    "Cursor",
    "CursorError",
    "Page",
    "QueryTimeout",
    "context",
    "count",
    "get_entry",
    "page",
    "read_guard",
]
