"""Highlight rules of a case (table ``highlight_rule``; ARQUITETURA 10b, ROADMAP 9.1).

A rule marks records with a color **without filtering them**. It is a name, a color of the fixed
palette, a condition written in the filter language (the JSON tree of ``store/filters.py``), an
``enabled`` flag and a position: the rules are ordered and the first enabled rule whose condition
matches a record gives it its color.

What is stored is only what the user typed or chose (the condition tree, the name, the color); a
rule never copies text of the log. "Highlight this key" stores the key the user clicked
(``{"key": {"type": ..., "eq": ...}}``), which is the condition itself.

Evaluation (everything on the server, one code path with the grid filters):

* :func:`mark` takes the conditions (at most :data:`MAX_RULES`) and the ids of the rows the grid
  shows (at most :data:`MAX_MARK_IDS`) and returns ``{id: index of the first matching condition}``.
  One statement: ``CASE WHEN c0 THEN 0 WHEN c1 THEN 1 ... END`` over ``e.id IN (ids)``, with the
  conditions compiled in rowwise mode (FTS, keys and payloads as per-row lookups).
* :func:`next_match` is the ``]``/``[`` navigation: the first record after (or before) a cursor that
  matches the view filter AND any of the conditions, by keyset on ``(ts, id)`` (``query.page`` with
  ``limit=1``), never paging the rows in between. It also returns the index of the first matching
  condition of that record.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import query
from .filters import MAX_JSON_BYTES, MAX_PARAMS, CompiledFilter, FilterError, compile_filter
from .views import ViewError, sanitize_name

MAX_RULES = 20  # rules per case, and conditions per mark/next request
MAX_MARK_IDS = 500  # ids per mark request (the grid asks for the rows it shows)
MAX_NAME_CHARS = 80
MARK_TIMEOUT_S = 2.0
NEXT_TIMEOUT_S = 5.0  # same budget as a grid page: the next match may be far away
TABLE = "highlight_rule"

# Fixed palette (names only; the colors are CSS tokens of css/highlight.css, checked for contrast in
# both themes by tests/web/highlight-contrast.test.mjs).
PALETTE = ("yellow", "orange", "red", "pink", "purple", "blue", "cyan", "green")
DEFAULT_COLOR = "yellow"

ERROR_MESSAGES = {
    "bad_name": "the name must be a non-empty text of at most {limit} characters",
    "bad_color": "the color must be one of: {colors}",
    "bad_cond": "the condition must be a non-empty filter object",
    "cond_too_large": "the condition is larger than {limit} bytes",
    "bad_enabled": "enabled must be true or false",
    "bad_rules": "rules must be a list of at most {limit} filter objects",
    "bad_ids": "ids must be a list of at most {limit} record ids",
    "bad_order": "ids must list every rule of the case exactly once",
    "too_many_rules": "a case can hold at most {limit} highlight rules",
    "too_many_params": "the conditions together have more than {limit} values",
    "not_found": "highlight rule {rule_id} does not exist",
    "bad_key": "type and value of the key must be non-empty texts",
}


class HighlightError(ValueError):
    """A highlight operation failed; ``code`` is stable and ``params`` fill the message."""

    messages: Mapping[str, str] = ERROR_MESSAGES

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


@dataclass(slots=True)
class NextMatch:
    row: dict[str, Any] | None  # grid row (query.page columns) or None when nothing matches
    rule: int | None  # index (in the request) of the first condition the row matches


# --------------------------------------------------------------------------- validation


def clean_name(raw: Any) -> str:
    try:
        return sanitize_name(raw)
    except ViewError:
        raise HighlightError("bad_name", limit=MAX_NAME_CHARS) from None


def clean_color(raw: Any) -> str:
    if raw not in PALETTE:
        raise HighlightError("bad_color", colors=", ".join(PALETTE))
    return raw


def cond_text(tree: Any) -> str:
    """Validate a condition with the filter compiler and return its compact JSON text."""
    if not isinstance(tree, dict) or not tree:
        raise HighlightError("bad_cond")
    text = json.dumps(tree, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise HighlightError("cond_too_large", limit=MAX_JSON_BYTES)
    compile_filter(tree)  # FilterError (code + path) when invalid
    return text


def _enabled(raw: Any) -> int:
    if not isinstance(raw, bool):
        raise HighlightError("bad_enabled")
    return 1 if raw else 0


def compile_conditions(rules: Any, *, rowwise: bool) -> list[CompiledFilter]:
    """Validate and compile the conditions of a mark/next request (a list of filter trees)."""
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise HighlightError("bad_rules", limit=MAX_RULES)
    compiled = []
    for k, tree in enumerate(rules):
        if not isinstance(tree, dict) or not tree:
            raise HighlightError("bad_rules", limit=MAX_RULES)
        try:
            compiled.append(compile_filter(tree, rowwise=rowwise))
        except FilterError as exc:
            raise FilterError(exc.code, f"$.rules[{k}]{exc.path[1:]}", **exc.params) from None
    if sum(len(c.params) for c in compiled) > MAX_PARAMS:
        raise HighlightError("too_many_params", limit=MAX_PARAMS)
    return compiled


def _ids(raw: Any) -> list[int]:
    if not isinstance(raw, list) or len(raw) > MAX_MARK_IDS:
        raise HighlightError("bad_ids", limit=MAX_MARK_IDS)
    out = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 2**63:
            raise HighlightError("bad_ids", limit=MAX_MARK_IDS)
        out.append(v)
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- storage


def has_table(conn: sqlite3.Connection) -> bool:
    """False for a case written by a dmslog older than schema 6 (opened read-only, not migrated)."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone()
    return row is not None


def _row(r: tuple[Any, ...]) -> dict[str, Any]:
    try:
        cond = json.loads(r[4])
    except ValueError:
        cond = None
    return {"id": r[0], "pos": r[1], "name": r[2], "color": r[3], "cond": cond, "enabled": bool(r[5]),
            "created_at": r[6]}  # fmt: skip


_SELECT = "SELECT id, pos, name, color, cond, enabled, created_at FROM highlight_rule"


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Rules in priority order (an old case without the table has none)."""
    if not has_table(conn):
        return []
    return [_row(r) for r in conn.execute(f"{_SELECT} ORDER BY pos, id")]


def get_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any]:
    row = conn.execute(f"{_SELECT} WHERE id = ?", (rule_id,)).fetchone() if has_table(conn) else None
    if row is None:
        raise HighlightError("not_found", rule_id=rule_id)
    return _row(row)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring a case created by an older dmslog to the current schema before writing a rule."""
    from .db import SCHEMA_VERSION, get_user_version, migrate

    if get_user_version(conn) < SCHEMA_VERSION:
        migrate(conn)


class _Write:
    """``BEGIN IMMEDIATE`` ... ``COMMIT`` on a read-write connection (rolled back on any error)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.previous: str | None = None

    def __enter__(self) -> sqlite3.Connection:
        _ensure_schema(self.conn)
        self.previous = self.conn.isolation_level
        self.conn.isolation_level = None
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.conn.isolation_level = self.previous
            raise
        return self.conn

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.conn.isolation_level = self.previous


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_INSERT = "INSERT INTO highlight_rule(pos, name, color, cond, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)"


def _insert(w: sqlite3.Connection, name: str, color: str, text: str, enabled: int) -> int:
    """Append a rule inside an open write transaction (limit checked under the lock)."""
    n, last = w.execute("SELECT count(*), coalesce(max(pos), -1) FROM highlight_rule").fetchone()
    if n >= MAX_RULES:
        raise HighlightError("too_many_rules", limit=MAX_RULES)
    return w.execute(_INSERT, (last + 1, name, color, text, enabled, _now())).lastrowid  # type: ignore[return-value]


def create_rule(
    conn: sqlite3.Connection, name: Any, color: Any, cond: Any, enabled: Any = True
) -> dict[str, Any]:
    """Append a rule (lowest priority) after validating everything."""
    clean = clean_name(name)
    hue = clean_color(color)
    text = cond_text(cond)
    on = _enabled(enabled)
    with _Write(conn) as w:
        rule_id = _insert(w, clean, hue, text, on)
    return get_rule(conn, rule_id)


_UNSET: Any = object()


def update_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    *,
    name: Any = _UNSET,
    color: Any = _UNSET,
    cond: Any = _UNSET,
    enabled: Any = _UNSET,
) -> dict[str, Any]:
    """Change what is given (name, color, condition, enabled); the position stays."""
    sets: list[str] = []
    values: list[Any] = []
    if name is not _UNSET:
        sets.append("name = ?")
        values.append(clean_name(name))
    if color is not _UNSET:
        sets.append("color = ?")
        values.append(clean_color(color))
    if cond is not _UNSET:
        sets.append("cond = ?")
        values.append(cond_text(cond))
    if enabled is not _UNSET:
        sets.append("enabled = ?")
        values.append(_enabled(enabled))
    with _Write(conn) as w:
        if sets:
            cur = w.execute(f"UPDATE highlight_rule SET {', '.join(sets)} WHERE id = ?", (*values, rule_id))
            found = cur.rowcount > 0
        else:
            found = w.execute("SELECT 1 FROM highlight_rule WHERE id = ?", (rule_id,)).fetchone() is not None
        if not found:
            raise HighlightError("not_found", rule_id=rule_id)
    return get_rule(conn, rule_id)


def _renumber(conn: sqlite3.Connection, ids: Sequence[int]) -> None:
    for pos, rule_id in enumerate(ids):
        conn.execute("UPDATE highlight_rule SET pos = ? WHERE id = ?", (pos, rule_id))


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    with _Write(conn) as w:
        if w.execute("DELETE FROM highlight_rule WHERE id = ?", (rule_id,)).rowcount == 0:
            raise HighlightError("not_found", rule_id=rule_id)
        _renumber(w, [r[0] for r in w.execute("SELECT id FROM highlight_rule ORDER BY pos, id")])


def reorder(conn: sqlite3.Connection, ids: Any) -> list[dict[str, Any]]:
    """Set the priority order: ``ids`` lists every rule of the case once, first = highest priority."""
    if (
        not isinstance(ids, list)
        or len(ids) > MAX_RULES
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in ids)
    ):
        raise HighlightError("bad_order")
    with _Write(conn) as w:
        current = {r[0] for r in w.execute("SELECT id FROM highlight_rule")}
        if len(ids) != len(set(ids)) or set(ids) != current:
            raise HighlightError("bad_order")
        _renumber(w, ids)
    return list_rules(conn)


def key_condition(key_type: Any, value: Any) -> dict[str, Any]:
    if not isinstance(key_type, str) or not key_type or not isinstance(value, str) or not value:
        raise HighlightError("bad_key")
    return {"key": {"type": key_type, "eq": value}}


def highlight_key(
    conn: sqlite3.Connection, key_type: Any, value: Any, color: Any = None
) -> tuple[dict[str, Any], bool]:
    """Highlight this key: every record with this key (normal and sensitive streams alike, since the
    key is a key of the record, ``entry_key``) gets the color. Updates the color of the rule with this
    exact condition when there is one (and enables it), else creates it. Returns ``(rule, created)``."""
    cond = key_condition(key_type, value)
    text = cond_text(cond)
    hue = clean_color(DEFAULT_COLOR if color is None else color)
    name = clean_name(f"{key_type} = {value}"[:MAX_NAME_CHARS])
    with _Write(conn) as w:
        found = w.execute("SELECT id FROM highlight_rule WHERE cond = ? ORDER BY pos LIMIT 1", (text,))
        row = found.fetchone()
        if row is not None:
            w.execute("UPDATE highlight_rule SET color = ?, enabled = 1 WHERE id = ?", (hue, row[0]))
            rule_id, created = row[0], False
        else:
            rule_id, created = _insert(w, name, hue, text, 1), True
    return get_rule(conn, rule_id), created


# --------------------------------------------------------------------------- evaluation


def mark(
    conn: sqlite3.Connection, rules: Any, ids: Any, *, timeout_s: float | None = MARK_TIMEOUT_S
) -> dict[int, int]:
    """``{id: index of the first condition that matches}`` for the given record ids (unmatched ids are
    left out). Conditions in rowwise mode: each one is a lookup of the row, never a scan."""
    conds = compile_conditions(rules, rowwise=True)
    wanted = _ids(ids)
    if not conds or not wanted:
        return {}
    case = " ".join(f"WHEN ({c.sql}) THEN {k}" for k, c in enumerate(conds))  # k is an int of this loop
    params = [p for c in conds for p in c.params]
    sql = (
        f"SELECT id, k FROM (SELECT e.id AS id, CASE {case} END AS k FROM entry e"
        f" WHERE e.id IN ({','.join('?' * len(wanted))})) WHERE k IS NOT NULL"
    )
    with query.read_guard(conn, timeout_s):
        rows = conn.execute(sql, (*params, *wanted)).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def next_match(
    conn: sqlite3.Connection,
    view: Any,
    rules: Any,
    cursor: query.Cursor | str | None,
    direction: query.Direction = "fwd",
    *,
    timeout_s: float | None = NEXT_TIMEOUT_S,
) -> NextMatch:
    """The first record after (``fwd``) or before (``back``) ``cursor`` that matches the view filter and
    any of the conditions (keyset on ``(ts, id)``, one row, no paging in between)."""
    conds = compile_conditions(rules, rowwise=False)
    if not conds:
        return NextMatch(None, None)
    flt = compile_filter(view)
    if len(flt.params) + sum(len(c.params) for c in conds) > MAX_PARAMS:
        raise HighlightError("too_many_params", limit=MAX_PARAMS)
    combined = CompiledFilter(
        f"({flt.sql}) AND (" + " OR ".join(f"({c.sql})" for c in conds) + ")",
        (*flt.params, *(p for c in conds for p in c.params)),
        flt.uses_fts or any(c.uses_fts for c in conds),
        flt.uses_substr or any(c.uses_substr for c in conds),
        flt.uses_regex or any(c.uses_regex for c in conds),
    )
    found = query.page(conn, combined, cursor, direction, 1, timeout_s=timeout_s)
    if not found.rows:
        return NextMatch(None, None)
    row = found.rows[0]
    marks = mark(conn, rules, [row["id"]], timeout_s=timeout_s)
    return NextMatch(row, marks.get(row["id"]))
