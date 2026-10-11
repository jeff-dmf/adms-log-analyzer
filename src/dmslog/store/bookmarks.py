"""Bookmarks of a case (table ``bookmark``; user data that survives reindexing) and record compare.

A bookmark is a record plus a color and a note. It is located by ``(collection_id, member, line_no)``
(the stable locator of ARQUITETURA 5) plus ``fp``, a fingerprint of the record (byte offset, timestamp
and the start of the message), because after a truncation another record can sit at the same line.
``entry_id`` is a cache that the writer keeps right in the same transaction that changes the records
(ARQUITETURA 9.3): :func:`detach_entries` before records are deleted, :func:`attach_entries` after
records are inserted and :func:`move_members` when a rotation renames a source. A bookmark whose
record no longer exists is an *orphan* (``entry_id`` NULL, listed with ``orphan: true``); it is
re-linked when a record with the same locator and fingerprint is indexed again. :func:`reassociate`
rebuilds every cache from the locators (schema migration 4, or after rows were changed outside the
writer); reads also re-check a cache that does not hold its locator any more.

Every value reaches SQL as a bound parameter. Notes are untrusted free text: sanitized here
(NFC, control characters removed except newline and tab) and shown by the UI through
``textContent`` only.

This module also holds the line-level diff behind ``GET /api/compare`` (:func:`diff_records`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from .query import PAGE_TIMEOUT_S, QueryTimeout, read_guard

COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "gray")
DEFAULT_COLOR = "yellow"
MAX_BOOKMARKS = 5000
MAX_NOTE_CHARS = 2000
MAX_MEMBER_CHARS = 4096
MAX_LOOKUP_IDS = 500
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MSG_CHARS = 300
EXPORT_FORMAT = "dmslog-bookmarks"
EXPORT_VERSION = 1
COMPARE_MAX_LINES = 1500  # per side, message line included
COMPARE_MAX_LINE_CHARS = 4000
_CHUNK = 200
SQLITE_MAX_INT = 2**63 - 1
MAX_EXPORT_BYTES = 12 * 1024 * 1024  # compact UTF-8 size of an export document; export stops adding beyond it
MAX_SEARCH_MSG_CHARS = 65_536  # search horizon per message, same as the full-text index of the continuation
MAX_SEARCH_BATCH_CHARS = 1_000_000  # message characters held in memory at once by the text filter
MAX_COMPARE_PAIRS = 20_000  # line pairs the exact diff may examine; beyond it a positional diff is used
MAX_COMPARE_CHARS = 400_000
FP_MSG_CHARS = 64  # message characters in the fingerprint (a partial last record may be shorter)
_IDENT = re.compile(r"[^\x00-\x1f\x7f]{1,128}\Z")

ERROR_MESSAGES = {
    "bad_color": "color must be one of: {colors}",
    "bad_note": "the note must be a text of at most {limit} characters",
    "bad_text": "the text filter must be a text of at most 128 characters",
    "entry_not_found": "entry {entry_id} does not exist",
    "already_bookmarked": "this record is already bookmarked",
    "too_many": "a case can hold at most {limit} bookmarks",
    "not_found": "bookmark {bookmark_id} does not exist",
    "bad_import": "the file is not a dmslog bookmarks export (format {format!r}, version {version})",
    "import_too_large": "the file holds more than {limit} bookmarks",
    "no_changes": "nothing to update",
    "bad_ids": "entry ids must be a list of 1 to {limit} integers",
}


class BookmarkError(ValueError):
    """A bookmark operation failed; ``code`` is stable and ``params`` fill the message."""

    messages: Mapping[str, str] = ERROR_MESSAGES

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


# --------------------------------------------------------------------------- validation


def check_color(raw: Any, default: str | None = None) -> str | None:
    """Return a palette color; None/'' gives ``default``. Anything else raises ``bad_color``."""
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or raw not in COLORS:
        raise BookmarkError("bad_color", colors=", ".join(COLORS))
    return raw


def clean_note(raw: Any) -> str | None:
    """NFC, no control characters except newline and tab; empty becomes None."""
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > MAX_NOTE_CHARS * 4 or not encodable(raw):
        raise BookmarkError("bad_note", limit=MAX_NOTE_CHARS)
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C").strip()
    if len(text) > MAX_NOTE_CHARS:
        raise BookmarkError("bad_note", limit=MAX_NOTE_CHARS)
    return text or None


def encodable(value: str) -> bool:
    """True when the text can be stored and sent as UTF-8 (no lone surrogates)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def is_sql_int(value: Any, minimum: int = 0) -> bool:
    """True for a real int (not bool) inside the SQLite integer range and >= minimum."""
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= SQLITE_MAX_INT


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK on a connection (restores its isolation level)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.previous = conn.isolation_level

    def __enter__(self) -> sqlite3.Connection:
        self.conn.isolation_level = None
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.conn.isolation_level = self.previous


# --------------------------------------------------------------------------- location


def _locator_of_entry(conn: sqlite3.Connection, entry_id: int) -> tuple[int, str, int] | None:
    row = conn.execute(
        "SELECT s.collection_id, s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
        " WHERE e.id = ?",
        (entry_id,),
    ).fetchone()
    return None if row is None else (row[0], row[1], row[2])


def fingerprint(byte_off: int, ts: int | None, msg: str, length: int | None = None) -> str | None:
    """``"<n>:<digest>"`` of a record: byte offset, timestamp and the first ``n`` message characters.

    ``n`` is ``min(len(msg), FP_MSG_CHARS)`` unless given; None when the message is shorter than ``length``.
    Keeping ``n`` lets a partial last record (cut inside its first line) match the completed record.
    """
    n = min(len(msg), FP_MSG_CHARS) if length is None else length
    if n < 0 or len(msg) < n:
        return None
    text = f"{byte_off}\x1f{'' if ts is None else ts}\x1f{msg[:n]}".encode("utf-8", "surrogatepass")
    return f"{n}:{hashlib.blake2b(text, digest_size=10).hexdigest()}"


def fp_matches(fp: str | None, byte_off: int, ts: int | None, msg: str) -> bool:
    """True when the record has the bookmarked fingerprint (no fingerprint: the locator alone decides)."""
    if fp is None:
        return True
    head, sep, _ = fp.partition(":")
    if not sep or not head.isdigit() or int(head) > FP_MSG_CHARS:
        return False
    return fingerprint(byte_off, ts, msg, int(head)) == fp


def has_bookmarks(conn: sqlite3.Connection) -> bool:
    """One probe; the writer calls it before any bookmark work, so an empty table costs nothing more."""
    return conn.execute("SELECT 1 FROM bookmark LIMIT 1").fetchone() is not None


def _fp_column(conn: sqlite3.Connection) -> str:
    """``fp`` on a migrated case, ``NULL`` on a case still at schema 3 (read-only connections)."""
    # Column names from a cursor: PRAGMA is not allowed under read_guard.
    columns = {d[0] for d in conn.execute("SELECT * FROM bookmark LIMIT 0").description}
    return "fp" if "fp" in columns else "NULL"


_Facts = tuple[int, int | None, str]  # byte_off, ts, start of msg


def _entry_facts(conn: sqlite3.Connection, entry_ids: Iterable[int]) -> dict[int, _Facts]:
    """``entry id -> (byte_off, ts, start of msg)``: what a fingerprint needs."""
    ids = sorted(set(entry_ids))
    out: dict[int, _Facts] = {}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for eid, byte_off, ts, msg in conn.execute(
            f"SELECT id, byte_off, ts, substr(msg, 1, {FP_MSG_CHARS}) FROM entry WHERE id IN ({marks})",  # noqa: S608
            chunk,
        ):
            out[eid] = (byte_off, ts, msg)
    return out


def _resolve(
    conn: sqlite3.Connection, locators: Iterable[tuple[int, str, int]]
) -> dict[tuple[int, str, int], int]:
    """Entry id of each ``(collection_id, member, line_no)`` that still exists (one scan per source)."""
    wanted: dict[tuple[int, str], set[int]] = {}
    for collection_id, member, line_no in locators:
        wanted.setdefault((collection_id, member), set()).add(line_no)
    found: dict[tuple[int, str, int], int] = {}
    for (collection_id, member), lines in wanted.items():
        src = conn.execute(
            "SELECT id FROM source WHERE collection_id = ? AND member = ?", (collection_id, member)
        ).fetchone()
        if src is None:
            continue
        for entry_id, line_no in conn.execute("SELECT id, line_no FROM entry WHERE source_id = ?", (src[0],)):
            if line_no in lines:
                found[(collection_id, member, line_no)] = entry_id
    return found


def _cache_valid(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> set[int]:
    """Bookmark ids whose cached ``entry_id`` still holds the stored locator (primary-key lookups)."""
    valid: set[int] = set()
    for bid, collection_id, member, line_no, entry_id in rows:
        if entry_id is None:
            continue
        hit = conn.execute(
            "SELECT 1 FROM entry e JOIN source s ON s.id = e.source_id"
            " WHERE e.id = ? AND e.line_no = ? AND s.collection_id = ? AND s.member = ?",
            (entry_id, line_no, collection_id, member),
        ).fetchone()
        if hit:
            valid.add(bid)
    return valid


_BM_COLUMNS = "id, collection_id, member, line_no, entry_id, color, note, created_at"


def _resolve_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> dict[int, int | None]:
    """bookmark id -> entry id (None for an orphan).

    A cache that still holds its locator is used as is; NULL is an orphan the writer already
    detached. A cache pointing elsewhere (rows rebuilt outside the writer, or a case not yet
    migrated) is resolved by locator and accepted only when the record has the bookmark's fingerprint.
    """
    valid = _cache_valid(conn, [(r[0], r[1], r[2], r[3], r[4]) for r in rows])
    stale = [r for r in rows if r[4] is not None and r[0] not in valid]
    out: dict[int, int | None] = {r[0]: (r[4] if r[0] in valid else None) for r in rows}
    if not stale:
        return out
    found = _resolve(conn, [(r[1], r[2], r[3]) for r in stale])
    facts = _entry_facts(conn, found.values())
    fps = _fingerprints(conn, [r[0] for r in stale])
    for r in stale:
        eid = found.get((r[1], r[2], r[3]))
        if eid is not None and eid in facts and fp_matches(fps.get(r[0]), *facts[eid]):
            out[r[0]] = eid
    return out


def _fingerprints(conn: sqlite3.Connection, bookmark_ids: list[int]) -> dict[int, str | None]:
    column = _fp_column(conn)
    out: dict[int, str | None] = {}
    for start in range(0, len(bookmark_ids), _CHUNK):
        chunk = bookmark_ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        out.update(conn.execute(f"SELECT id, {column} FROM bookmark WHERE id IN ({marks})", chunk))  # noqa: S608
    return out


def _summaries(conn: sqlite3.Connection, entry_ids: list[int]) -> dict[int, tuple[Any, ...]]:
    out: dict[int, tuple[Any, ...]] = {}
    for start in range(0, len(entry_ids), _CHUNK):
        chunk = entry_ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT id, source_id, ts, sev, substr(msg, 1, ?) FROM entry WHERE id IN ({marks})",  # noqa: S608
            (MSG_CHARS, *chunk),
        ):
            out[row[0]] = row
    return out


def _matching_messages(
    conn: sqlite3.Connection, entry_ids: list[int], needle: str, deadline: float | None
) -> set[int]:
    """Ids whose complete first-line message contains ``needle`` (casefolded).

    Only the first MAX_SEARCH_MSG_CHARS characters of each message are searched (the horizon of the
    full-text index); the cut is made in SQL, so Python never holds more than that per message.
    Messages are read in groups of at most MAX_SEARCH_BATCH_CHARS characters, and the deadline is
    checked before and after every message: SQLite's progress handler cannot interrupt this Python work.
    """

    def check() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise QueryTimeout("bookmark text search ran out of time")

    sizes: list[tuple[int, int]] = []
    for start in range(0, len(entry_ids), _CHUNK):
        chunk = entry_ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        sizes.extend(
            conn.execute(
                f"SELECT id, min(length(msg), {MAX_SEARCH_MSG_CHARS}) FROM entry WHERE id IN ({marks})", chunk
            )
        )  # noqa: S608
    found: set[int] = set()
    group: list[int] = []
    total = 0

    def scan() -> None:
        marks = ",".join("?" * len(group))
        sql = f"SELECT id, substr(msg, 1, {MAX_SEARCH_MSG_CHARS}) FROM entry WHERE id IN ({marks})"  # noqa: S608
        for eid, msg in conn.execute(sql, group):
            check()  # between the fetch and the casefold
            if needle in msg.casefold():
                found.add(eid)

    for eid, length in sizes:
        if group and total + (length or 0) > MAX_SEARCH_BATCH_CHARS:
            scan()
            group, total = [], 0
        group.append(eid)
        total += length or 0
    if group:
        scan()
    check()
    return found


def _item(row: tuple[Any, ...], entry_id: int | None, summary: tuple[Any, ...] | None) -> dict[str, Any]:
    bid, collection_id, member, line_no, _cache, color, note, created = row
    return {
        "id": bid, "collection_id": collection_id, "member": member, "line_no": line_no,
        "entry_id": entry_id, "orphan": entry_id is None, "color": color, "note": note,
        "created_at": created,
        "source_id": summary[1] if summary else None, "ts": summary[2] if summary else None,
        "sev": summary[3] if summary else None, "msg": summary[4] if summary else None,
    }  # fmt: skip


def _items(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    resolved = _resolve_rows(conn, rows)
    summaries = _summaries(conn, sorted({v for v in resolved.values() if v is not None}))
    return [_item(r, resolved[r[0]], summaries.get(resolved[r[0]])) for r in rows]  # type: ignore[arg-type]


# --------------------------------------------------------------------------- reads


def list_bookmarks(
    conn: sqlite3.Connection,
    *,
    color: str | None = None,
    text: str | None = None,
    limit: int = DEFAULT_LIMIT,
    after: int | None = None,
    timeout_s: float = PAGE_TIMEOUT_S,
) -> tuple[list[dict[str, Any]], int | None]:
    """One keyset page (ascending bookmark id; ``after`` is the id of the previous page's last item).

    ``color`` keeps one palette color. ``text`` keeps bookmarks whose note or record message
    contains it (case-insensitive; the message of an orphan is unknown).
    """
    limit = max(1, min(limit, MAX_LIMIT))
    if after is not None and not is_sql_int(after, -1):
        raise BookmarkError("bad_text")
    color = check_color(color)
    needle = None
    if text is not None:
        if not isinstance(text, str) or len(text) > 128 or not encodable(text):
            raise BookmarkError("bad_text")
        needle = text.strip().casefold() or None
    sql = f"SELECT {_BM_COLUMNS} FROM bookmark WHERE id > ?"  # noqa: S608
    params: list[Any] = [-1 if after is None else after]
    if color is not None:
        sql += " AND color = ?"
        params.append(color)
    sql += " ORDER BY id"
    out: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_s
    with read_guard(conn, timeout_s):
        rows = conn.execute(sql, params).fetchall()
        for start in range(0, len(rows), _CHUNK):
            items = _items(conn, rows[start : start + _CHUNK])
            hits: set[int] = set()
            if needle is not None:
                ids = [i["entry_id"] for i in items if i["entry_id"] is not None]
                hits = _matching_messages(conn, ids, needle, deadline)
            for item in items:
                if needle is not None:
                    if item["entry_id"] not in hits and needle not in (item["note"] or "").casefold():
                        continue
                out.append(item)
                if len(out) > limit:
                    break
            if len(out) > limit:
                break
    nxt = out[limit - 1]["id"] if len(out) > limit else None
    return out[:limit], nxt


def get_bookmark(conn: sqlite3.Connection, bookmark_id: int) -> dict[str, Any]:
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(f"SELECT {_BM_COLUMNS} FROM bookmark WHERE id = ?", (bookmark_id,)).fetchone()  # noqa: S608
        if row is None:
            raise BookmarkError("not_found", bookmark_id=bookmark_id)
        return _items(conn, [row])[0]


def for_entries(conn: sqlite3.Connection, entry_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Bookmarks of the given records (the grid's marker column): ``entry_id -> {id, color, note}``.

    A bookmark marks a record when its cache points at it, or, for a cache that is stale (rows
    rebuilt outside the writer), when the record has the bookmark's locator and fingerprint. An
    orphan (cache NULL) marks nothing, even when another record now sits at its line.
    """
    if (
        not isinstance(entry_ids, list)
        or not 1 <= len(entry_ids) <= MAX_LOOKUP_IDS
        or not all(is_sql_int(i) for i in entry_ids)
    ):
        raise BookmarkError("bad_ids", limit=MAX_LOOKUP_IDS)
    with read_guard(conn, PAGE_TIMEOUT_S):
        marks: dict[tuple[int, str, int], list[tuple[Any, ...]]] = {}
        for r in conn.execute(
            f"SELECT id, collection_id, member, line_no, entry_id, color, note, {_fp_column(conn)}"  # noqa: S608
            " FROM bookmark WHERE entry_id IS NOT NULL ORDER BY id"
        ):
            marks.setdefault((r[1], r[2], r[3]), []).append(r)
        if not marks:
            return {}
        out: dict[int, dict[str, Any]] = {}
        unique = sorted(set(entry_ids))
        for start in range(0, len(unique), _CHUNK):
            chunk = unique[start : start + _CHUNK]
            qs = ",".join("?" * len(chunk))
            for eid, collection_id, member, line_no, byte_off, ts, msg in conn.execute(
                "SELECT e.id, s.collection_id, s.member, e.line_no, e.byte_off, e.ts,"  # noqa: S608
                f" substr(e.msg, 1, {FP_MSG_CHARS}) FROM entry e"
                f" JOIN source s ON s.id = e.source_id WHERE e.id IN ({qs})",
                chunk,
            ):
                candidates = marks.get((collection_id, member, line_no), [])
                row = next((c for c in candidates if c[4] == eid), None)
                if row is None:
                    stale = (c for c in candidates if not _cache_holds(conn, c))
                    row = next((c for c in stale if fp_matches(c[7], byte_off, ts, msg)), None)
                if row is not None:
                    out[eid] = {"id": row[0], "color": row[5], "has_note": bool(row[6])}
        return out


def _cache_holds(conn: sqlite3.Connection, row: tuple[Any, ...]) -> bool:
    """True when the cached entry of a bookmark row still sits at the bookmark's locator."""
    return bool(_cache_valid(conn, [(row[0], row[1], row[2], row[3], row[4])]))


# --------------------------------------------------------------------------- writes


def create_bookmark(
    conn: sqlite3.Connection, entry_id: int, color: Any = None, note: Any = None
) -> dict[str, Any]:
    """Bookmark a record. Raises ``already_bookmarked`` (with ``bookmark_id``) when it already is."""
    clean_color = check_color(color, DEFAULT_COLOR)
    clean = clean_note(note)
    if not is_sql_int(entry_id):
        raise BookmarkError("entry_not_found", entry_id=str(entry_id)[:20])
    _ensure_schema(conn)
    with _Tx(conn):
        loc = _locator_of_entry(conn, entry_id)
        if loc is None:
            raise BookmarkError("entry_not_found", entry_id=entry_id)
        rows = conn.execute(
            f"SELECT {_BM_COLUMNS} FROM bookmark WHERE collection_id = ? AND member = ? AND line_no = ?"  # noqa: S608
            " ORDER BY id",
            loc,
        ).fetchall()
        resolved = _resolve_rows(conn, rows) if rows else {}
        existing = next((r[0] for r in rows if resolved[r[0]] == entry_id), None)
        if existing is not None:  # orphans at the same line belong to another record: they do not count
            raise BookmarkError("already_bookmarked", bookmark_id=existing)
        if conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] >= MAX_BOOKMARKS:
            raise BookmarkError("too_many", limit=MAX_BOOKMARKS)
        cur = conn.execute(
            "INSERT INTO bookmark(collection_id, member, line_no, entry_id, color, note, created_at, fp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*loc, entry_id, clean_color, clean, _now(), _fp_of_entry(conn, entry_id)),
        )
        new_id = cur.lastrowid
    return get_bookmark(conn, new_id)  # type: ignore[arg-type]


def _fp_of_entry(conn: sqlite3.Connection, entry_id: int) -> str | None:
    facts = _entry_facts(conn, [entry_id]).get(entry_id)
    return None if facts is None else fingerprint(*facts)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring a case created by an older dmslog to the current schema before writing a bookmark."""
    from .db import SCHEMA_VERSION, get_user_version, migrate

    if get_user_version(conn) < SCHEMA_VERSION:
        migrate(conn)
        conn.commit()


_UNSET: Any = object()


def update_bookmark(
    conn: sqlite3.Connection, bookmark_id: int, color: Any = _UNSET, note: Any = _UNSET
) -> dict[str, Any]:
    """Change the color and/or the note (pass only what changes; ``note=None`` clears it)."""
    if not is_sql_int(bookmark_id):
        raise BookmarkError("not_found", bookmark_id=str(bookmark_id)[:20])
    sets: list[str] = []
    params: list[Any] = []
    if color is not _UNSET:
        sets.append("color = ?")
        params.append(check_color(color, DEFAULT_COLOR))
    if note is not _UNSET:
        sets.append("note = ?")
        params.append(clean_note(note))
    if not sets:
        raise BookmarkError("no_changes")
    with _Tx(conn):
        cur = conn.execute(
            f"UPDATE bookmark SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            (*params, bookmark_id),
        )
        if cur.rowcount == 0:
            raise BookmarkError("not_found", bookmark_id=bookmark_id)
    return get_bookmark(conn, bookmark_id)


def delete_bookmark(conn: sqlite3.Connection, bookmark_id: int) -> None:
    with _Tx(conn):
        cur = conn.execute("DELETE FROM bookmark WHERE id = ?", (bookmark_id,))
        if cur.rowcount == 0:
            raise BookmarkError("not_found", bookmark_id=bookmark_id)


def reassociate(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild the ``entry_id`` cache (and missing fingerprints) of every bookmark from its locator.

    A bookmark is linked to the record at its locator when the record has its fingerprint (a
    bookmark without one takes the record and gets its fingerprint); otherwise it becomes an orphan
    (``entry_id = NULL``). One record gets at most one bookmark. Idempotent. The writer keeps the cache
    right on its own; this full pass is for the schema migration and for rows changed outside it.
    Returns ``{"bookmarks", "relinked", "orphaned"}``; relinked counts caches that changed.
    """
    with _Tx(conn):
        return relink_all(conn)


def relink_all(conn: sqlite3.Connection) -> dict[str, int]:
    """:func:`reassociate` inside the caller's transaction (used by the schema migration)."""
    rows = conn.execute(
        "SELECT id, collection_id, member, line_no, entry_id, fp FROM bookmark ORDER BY fp IS NULL, id"
    ).fetchall()
    found = _resolve(conn, [(r[1], r[2], r[3]) for r in rows])
    facts = _entry_facts(conn, found.values())
    taken: set[int] = set()
    relinked = orphaned = 0
    for bid, collection_id, member, line_no, cached, fp in rows:
        eid = found.get((collection_id, member, line_no))
        ok = eid is not None and eid not in taken and eid in facts and fp_matches(fp, *facts[eid])
        new = eid if ok else None
        new_fp = fingerprint(*facts[eid]) if ok and fp is None else fp  # type: ignore[index]
        if new is None:
            orphaned += 1
        else:
            taken.add(new)
        if new != cached:
            relinked += 1
        if new != cached or new_fp != fp:
            conn.execute("UPDATE bookmark SET entry_id = ?, fp = ? WHERE id = ?", (new, new_fp, bid))
    return {"bookmarks": len(rows), "relinked": relinked, "orphaned": orphaned}


# --------------------------------------------------------------------------- writer hooks
# Called by store/writer.py inside its open transaction, so the cache changes atomically with the
# records (a rollback undoes both). Each starts with one probe and returns at once on an empty table.

_NewRow = tuple[int, int, int, int, int | None, str]  # entry_id, source_id, line_no, byte_off, ts, msg


def detach_entries(conn: sqlite3.Connection, source_id: int, from_byte_off: int = 0) -> int:
    """Before the records of a source at or after ``from_byte_off`` are deleted: orphan their bookmarks.

    The fingerprint of a bookmark that has none yet is taken from the record before it disappears,
    so a different record indexed later at the same line is not taken for it. Returns the count.
    """
    if not has_bookmarks(conn):
        return 0
    rows = conn.execute(
        "SELECT b.id, b.fp, e.byte_off, e.ts, substr(e.msg, 1, ?) FROM bookmark b"
        " CROSS JOIN entry e ON e.id = b.entry_id"  # bookmarks drive the loop: never a scan of the source
        " WHERE e.source_id = ? AND e.byte_off >= ? AND e.line_no = b.line_no",
        (FP_MSG_CHARS, source_id, from_byte_off),
    ).fetchall()
    for bid, fp, byte_off, ts, msg in rows:
        conn.execute(
            "UPDATE bookmark SET entry_id = NULL, fp = ? WHERE id = ?",
            (fp if fp is not None else fingerprint(byte_off, ts, msg), bid),
        )
    return len(rows)


def attach_entries(conn: sqlite3.Connection, rows: Iterable[_NewRow]) -> int:
    """After records were inserted: link the orphan bookmarks whose record they are.

    ``rows`` holds ``(entry_id, source_id, line_no, byte_off, ts, msg)`` of the inserted records and
    is only consumed when some orphan bookmark lives in one of their sources. A bookmark with a
    fingerprint needs a match; one without (older cases) takes the record at its line, unless a
    bookmark with a matching fingerprint claims it. Returns the number of bookmarks linked.
    """
    if not has_bookmarks(conn):
        return 0
    wanted: dict[tuple[int, int], list[tuple[int, str | None]]] = {}
    for bid, sid, line_no, fp in conn.execute(
        "SELECT b.id, s.id, b.line_no, b.fp FROM bookmark b"
        " JOIN source s ON s.collection_id = b.collection_id AND s.member = b.member"
        " WHERE b.entry_id IS NULL ORDER BY b.fp IS NULL, b.id"
    ):
        wanted.setdefault((sid, line_no), []).append((bid, fp))
    if not wanted:
        return 0
    sources = {sid for sid, _line in wanted}
    linked = 0
    for entry_id, source_id, line_no, byte_off, ts, msg in rows:
        if source_id not in sources:
            continue
        candidates = wanted.get((source_id, line_no))
        if not candidates:
            continue
        for bid, fp in candidates:
            if fp_matches(fp, byte_off, ts, msg):
                conn.execute(
                    "UPDATE bookmark SET entry_id = ?, fp = ? WHERE id = ?",
                    (entry_id, fp if fp is not None else fingerprint(byte_off, ts, msg), bid),
                )
                linked += 1
                candidates.remove((bid, fp))
                break  # one bookmark per record
    return linked


def move_members(conn: sqlite3.Connection, renames: Mapping[int, str]) -> None:
    """Before sources are renamed (rotation, ``{source_id: new member}``): move their bookmarks along.

    In two steps, like the sources, because a rotation can swap names (``.log`` -> ``.log.0`` while
    ``.log.0`` -> ``.log.1``). Cached entry ids stay right: the records keep their ids.
    """
    if not renames or not has_bookmarks(conn):
        return
    moves: list[tuple[int, str, str, str]] = []
    for sid, new_member in renames.items():
        row = conn.execute("SELECT collection_id, member FROM source WHERE id = ?", (sid,)).fetchone()
        if row is not None and row[1] != new_member:
            moves.append((row[0], row[1], f"{row[1]}\x00{sid}", new_member))
    sql = "UPDATE bookmark SET member = ? WHERE collection_id = ? AND member = ?"
    for collection_id, old, parked, _new in moves:
        conn.execute(sql, (parked, collection_id, old))
    for collection_id, _old, parked, new in moves:
        conn.execute(sql, (new, collection_id, parked))


# --------------------------------------------------------------------------- export / import


def export_bookmarks(conn: sqlite3.Connection) -> dict[str, Any]:
    """A shareable JSON document: locator, color, note, date and the ERS identity of the collection."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(
            "SELECT b.member, b.line_no, b.color, b.note, b.created_at, c.ers_guid, c.ers_ts"
            " FROM bookmark b LEFT JOIN collection c ON c.id = b.collection_id ORDER BY b.id"
        ).fetchall()
    out: list[dict[str, Any]] = []
    size = 256
    truncated = False
    for r in rows:
        item = {"ers_guid": r[5], "ers_ts": r[6], "member": r[0], "line_no": r[1], "color": r[2],
                "note": r[3], "created_at": r[4]}  # fmt: skip
        size += len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if size > MAX_EXPORT_BYTES:
            truncated = True  # never produce a document the import route would refuse
            break
        out.append(item)
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "truncated": truncated, "bookmarks": out}


def _collection_for(conn: sqlite3.Connection, guid: str | None, stamp: str | None) -> int | None:
    """Collection of an item: by ERS identity when it has one, else the case's only collection."""
    if guid is not None:
        row = conn.execute(
            "SELECT id FROM collection WHERE ers_guid = ? AND (? IS NULL OR ers_ts = ?) ORDER BY id LIMIT 1",
            (guid, stamp, stamp),
        ).fetchone()
        return None if row is None else row[0]
    rows = conn.execute("SELECT id FROM collection").fetchall()
    return rows[0][0] if len(rows) == 1 else None  # one ERS per case: unambiguous without identity


def import_bookmarks(conn: sqlite3.Connection, data: Any) -> dict[str, int]:
    """Merge an export into the case; existing locators are kept. Returns the counts per outcome."""
    if (
        not isinstance(data, dict)
        or data.get("format") != EXPORT_FORMAT
        or data.get("version") != EXPORT_VERSION
        or not isinstance(data.get("bookmarks"), list)
    ):
        raise BookmarkError("bad_import", format=_short(data, "format"), version=_short(data, "version"))
    items = data["bookmarks"]
    if len(items) > MAX_BOOKMARKS:
        raise BookmarkError("import_too_large", limit=MAX_BOOKMARKS)
    counts = {"imported": 0, "existing": 0, "unmatched": 0, "invalid": 0}
    _ensure_schema(conn)
    with _Tx(conn):
        total = conn.execute("SELECT count(*) FROM bookmark").fetchone()[0]
        for item in items:
            parsed = _parse_item(item)
            if parsed is None:
                counts["invalid"] += 1
                continue
            member, line_no, color, note, created, guid, stamp = parsed
            collection_id = _collection_for(conn, guid, stamp)
            src = None
            if collection_id is not None:
                src = conn.execute(
                    "SELECT id FROM source WHERE collection_id = ? AND member = ?", (collection_id, member)
                ).fetchone()
            if src is None:
                counts["unmatched"] += 1
                continue
            if conn.execute(
                "SELECT 1 FROM bookmark WHERE collection_id = ? AND member = ? AND line_no = ?",
                (collection_id, member, line_no),
            ).fetchone():
                counts["existing"] += 1
                continue
            if total >= MAX_BOOKMARKS:
                raise BookmarkError("too_many", limit=MAX_BOOKMARKS)
            entry = conn.execute(
                "SELECT id FROM entry WHERE source_id = ? AND line_no = ?", (src[0], line_no)
            ).fetchone()
            entry_id = entry[0] if entry else None
            if (
                entry_id is not None
                and conn.execute("SELECT 1 FROM bookmark WHERE entry_id = ?", (entry_id,)).fetchone()
            ):
                entry_id = None  # never two bookmarks on one record
            conn.execute(
                "INSERT INTO bookmark(collection_id, member, line_no, entry_id, color, note, created_at, fp)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (collection_id, member, line_no, entry_id, color, note, created,
                 None if entry_id is None else _fp_of_entry(conn, entry_id)),
            )  # fmt: skip
            total += 1
            counts["imported"] += 1
    return counts


def _short(data: Any, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    return json.dumps(value)[:40]


def _parse_item(item: Any) -> tuple[str, int, str, str | None, str, str | None, str | None] | None:
    if not isinstance(item, dict):
        return None
    member, line_no = item.get("member"), item.get("line_no")
    if not isinstance(member, str) or not 0 < len(member) <= MAX_MEMBER_CHARS or not encodable(member):
        return None
    if not is_sql_int(line_no, 1):
        return None
    guid, stamp = item.get("ers_guid"), item.get("ers_ts")
    for ident in (guid, stamp):  # absent/None, or a bounded printable string; anything else is invalid
        if ident is not None and not (isinstance(ident, str) and _IDENT.match(ident) and encodable(ident)):
            return None
    try:
        color = check_color(item.get("color"), DEFAULT_COLOR)
        note = clean_note(item.get("note"))
    except BookmarkError:
        return None
    created = item.get("created_at")
    if not (
        isinstance(created, str)
        and len(created) == 20
        and created[10] == "T"
        and created[-1] == "Z"
        and created.isascii()
    ):
        created = _now()
    return member, line_no, color or DEFAULT_COLOR, note, created, guid, stamp


# --------------------------------------------------------------------------- compare


def record_lines(record: Mapping[str, Any]) -> tuple[list[str], bool]:
    """Message line plus continuation lines of an entry dict, bounded; True when cut."""
    msg = record.get("msg")
    lines = [str(msg) if msg is not None else ""]
    cont = record.get("cont")
    if cont:
        lines.extend(str(cont).split("\n"))
    cut = len(lines) > COMPARE_MAX_LINES or any(len(x) > COMPARE_MAX_LINE_CHARS for x in lines)
    return [x[:COMPARE_MAX_LINE_CHARS] for x in lines[:COMPARE_MAX_LINES]], cut


def _opcodes_positional(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    """Cheap fallback: compare line N with line N (runs of equal / replaced lines, then the tail)."""
    ops: list[tuple[str, int, int, int, int]] = []
    common = min(len(a), len(b))
    start = 0
    while start < common:
        same = a[start] == b[start]
        end = start
        while end < common and (a[end] == b[end]) == same:
            end += 1
        ops.append(("equal" if same else "replace", start, end, start, end))
        start = end
    if len(a) > common:
        ops.append(("delete", common, len(a), common, common))
    elif len(b) > common:
        ops.append(("insert", common, common, common, len(b)))
    return ops


def _too_costly(a: list[str], b: list[str]) -> bool:
    """True when an exact diff could be quadratic: too many characters or too many matching line pairs."""
    if sum(map(len, a)) + sum(map(len, b)) > MAX_COMPARE_CHARS:
        return True
    counts = Counter(b)
    return sum(counts[line] for line in a) > MAX_COMPARE_PAIRS


def diff_lines(a: list[str], b: list[str]) -> dict[str, Any]:
    """Line-level diff: opcodes ``equal|replace|delete|insert`` with the lines of each side.

    Inputs whose exact diff could be slow (see :func:`_too_costly`) get a positional diff and
    ``approximate: true``; the result is still correct, only less minimal.
    """
    approximate = _too_costly(a, b)
    if approximate:
        opcodes = _opcodes_positional(a, b)
    else:
        opcodes = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    ops: list[dict[str, Any]] = []
    stats = {"equal": 0, "changed": 0, "removed": 0, "added": 0}
    for tag, i1, i2, j1, j2 in opcodes:
        ops.append({"tag": tag, "a": a[i1:i2], "b": b[j1:j2], "a_start": i1 + 1, "b_start": j1 + 1})
        if tag == "equal":
            stats["equal"] += i2 - i1
        elif tag == "replace":
            stats["changed"] += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            stats["removed"] += i2 - i1
        else:
            stats["added"] += j2 - j1
    return {"identical": a == b, "ops": ops, "stats": stats, "approximate": approximate}


def diff_records(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Diff of ``msg`` + ``cont`` of two entry dicts (as returned by ``query.get_entry``)."""
    lines_a, cut_a = record_lines(a)
    lines_b, cut_b = record_lines(b)
    out = diff_lines(lines_a, lines_b)
    out["truncated"] = cut_a or cut_b
    out["max_lines"] = COMPARE_MAX_LINES
    return out
