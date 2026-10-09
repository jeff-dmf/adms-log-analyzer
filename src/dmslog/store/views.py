"""Saved filters of a case (table ``saved_view``; user data that survives reindexing).

A saved view is a name plus a filter tree (ARQUITETURA 10) and, optionally, a list of
column names. The tree is validated with :mod:`dmslog.store.filters` before it is stored,
and it is stored as compact JSON text. Names are sanitized (NFC, control characters
removed, whitespace collapsed) and unique ignoring letter case within a case.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .filters import MAX_JSON_BYTES, compile_filter

MAX_VIEWS = 100
MAX_NAME_CHARS = 80
MAX_COLUMNS = 64
MAX_COLUMN_CHARS = 64

ERROR_MESSAGES = {
    "bad_name": "the name must be a non-empty text of at most {limit} characters",
    "bad_filter": "the filter must be a non-empty JSON object",
    "filter_too_large": "the filter is larger than {limit} bytes",
    "bad_columns": "columns must be a list of at most {limit} short names",
    "duplicate_name": "a saved filter named {name!r} already exists",
    "too_many_views": "a case can hold at most {limit} saved filters",
    "not_found": "saved filter {view_id} does not exist",
}


class ViewError(ValueError):
    """A saved-view operation failed; ``code`` is stable and ``params`` fill the message."""

    messages: Mapping[str, str] = ERROR_MESSAGES

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


def sanitize_name(raw: Any) -> str:
    """Normalize a user-provided name; raises ViewError('bad_name') when unusable."""
    if not isinstance(raw, str):
        raise ViewError("bad_name", limit=MAX_NAME_CHARS)
    text = unicodedata.normalize("NFC", raw)
    # Drop control and format characters (including bidi overrides); whitespace -> one space.
    text = "".join(
        " " if ch.isspace() else ch for ch in text if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    text = " ".join(text.split())
    if not text or len(text) > MAX_NAME_CHARS:
        raise ViewError("bad_name", limit=MAX_NAME_CHARS)
    return text


def _columns(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if (
        not isinstance(raw, list)
        or len(raw) > MAX_COLUMNS
        or not all(isinstance(c, str) and 0 < len(c) <= MAX_COLUMN_CHARS for c in raw)
    ):
        raise ViewError("bad_columns", limit=MAX_COLUMNS)
    return list(raw)


def validate_filter(tree: Any) -> str:
    """Validate ``tree`` with the query compiler and return its compact JSON text."""
    if not isinstance(tree, dict) or not tree:
        raise ViewError("bad_filter")
    compile_filter(tree)  # raises FilterError with a code and a path
    text = json.dumps(tree, separators=(",", ":"), ensure_ascii=False, sort_keys=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ViewError("filter_too_large", limit=MAX_JSON_BYTES)
    return text


def _row(r: tuple[Any, ...]) -> dict[str, Any]:
    try:
        flt = json.loads(r[2])
    except ValueError:
        flt = None
    try:
        columns = json.loads(r[3]) if r[3] else None
    except ValueError:
        columns = None
    return {"id": r[0], "name": r[1], "filter": flt, "columns": columns, "created_at": r[4]}


def list_views(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, filter, columns, created_at FROM saved_view ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    return [_row(r) for r in rows]


def create_view(conn: sqlite3.Connection, name: Any, tree: Any, columns: Any = None) -> dict[str, Any]:
    """Insert a saved view atomically (limit and duplicate checks inside one write transaction)."""
    clean = sanitize_name(name)
    flt_text = validate_filter(tree)
    cols = _columns(columns)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    previous = conn.isolation_level
    conn.isolation_level = None  # explicit transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM saved_view")]
            if len(names) >= MAX_VIEWS:
                raise ViewError("too_many_views", limit=MAX_VIEWS)
            if any(n.casefold() == clean.casefold() for n in names):
                raise ViewError("duplicate_name", name=clean)
            cur = conn.execute(
                "INSERT INTO saved_view(name, filter, columns, created_at) VALUES (?, ?, ?, ?)",
                (clean, flt_text, json.dumps(cols) if cols is not None else None, created),
            )
            view_id = cur.lastrowid
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = previous
    return {"id": view_id, "name": clean, "filter": json.loads(flt_text), "columns": cols,
            "created_at": created}  # fmt: skip


def delete_view(conn: sqlite3.Connection, view_id: int) -> None:
    with conn:
        cur = conn.execute("DELETE FROM saved_view WHERE id = ?", (view_id,))
    if cur.rowcount == 0:
        raise ViewError("not_found", view_id=view_id)
