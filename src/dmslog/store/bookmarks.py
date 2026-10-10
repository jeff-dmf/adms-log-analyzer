"""Bookmarks of a case (table ``bookmark``; user data that survives reindexing) and record compare.

A bookmark is a record plus a color and a note. It is located by ``(collection_id, member, line_no)``
(the stable locator of ARQUITETURA 5), not by entry id: ``entry_id`` is only a cache. Reads resolve
the locator lazily, so a bookmark keeps pointing at the right record after the entry rows were
rebuilt; :func:`reassociate` rewrites the cache after a reindex (the writer calls it; ROADMAP 7.1).
A bookmark whose record no longer exists is an *orphan*: it stays listed (``orphan: true``) and is
re-linked by the next :func:`reassociate` if the line comes back.

Every value reaches SQL as a bound parameter. Notes are untrusted free text: sanitized here
(NFC, control characters removed except newline and tab) and shown by the UI through
``textContent`` only.

This module also holds the line-level diff behind ``GET /api/compare`` (:func:`diff_records`).
"""

from __future__ import annotations

import difflib
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
    """bookmark id -> entry id (None for an orphan), using the cache when it is still right."""
    valid = _cache_valid(conn, [(r[0], r[1], r[2], r[3], r[4]) for r in rows])
    stale = [(r[1], r[2], r[3]) for r in rows if r[0] not in valid]
    found = _resolve(conn, stale) if stale else {}
    return {r[0]: (r[4] if r[0] in valid else found.get((r[1], r[2], r[3]))) for r in rows}


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

    Matches by locator, so it is right even before :func:`reassociate` ran after a reindex.
    """
    if (
        not isinstance(entry_ids, list)
        or not 1 <= len(entry_ids) <= MAX_LOOKUP_IDS
        or not all(is_sql_int(i) for i in entry_ids)
    ):
        raise BookmarkError("bad_ids", limit=MAX_LOOKUP_IDS)
    with read_guard(conn, PAGE_TIMEOUT_S):
        marks = {
            (r[1], r[2], r[3]): r
            for r in conn.execute(f"SELECT {_BM_COLUMNS} FROM bookmark")  # noqa: S608
        }
        if not marks:
            return {}
        out: dict[int, dict[str, Any]] = {}
        unique = sorted(set(entry_ids))
        for start in range(0, len(unique), _CHUNK):
            chunk = unique[start : start + _CHUNK]
            qs = ",".join("?" * len(chunk))
            for eid, collection_id, member, line_no in conn.execute(
                "SELECT e.id, s.collection_id, s.member, e.line_no FROM entry e"  # noqa: S608
                f" JOIN source s ON s.id = e.source_id WHERE e.id IN ({qs})",
                chunk,
            ):
                row = marks.get((collection_id, member, line_no))
                if row is not None:
                    out[eid] = {"id": row[0], "color": row[5], "has_note": bool(row[6])}
        return out


# --------------------------------------------------------------------------- writes


def create_bookmark(
    conn: sqlite3.Connection, entry_id: int, color: Any = None, note: Any = None
) -> dict[str, Any]:
    """Bookmark a record. Raises ``already_bookmarked`` (with ``bookmark_id``) when it already is."""
    clean_color = check_color(color, DEFAULT_COLOR)
    clean = clean_note(note)
    if not is_sql_int(entry_id):
        raise BookmarkError("entry_not_found", entry_id=str(entry_id)[:20])
    with _Tx(conn):
        loc = _locator_of_entry(conn, entry_id)
        if loc is None:
            raise BookmarkError("entry_not_found", entry_id=entry_id)
        existing = conn.execute(
            "SELECT id FROM bookmark WHERE collection_id = ? AND member = ? AND line_no = ?", loc
        ).fetchone()
        if existing:
            raise BookmarkError("already_bookmarked", bookmark_id=existing[0])
        if conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] >= MAX_BOOKMARKS:
            raise BookmarkError("too_many", limit=MAX_BOOKMARKS)
        cur = conn.execute(
            "INSERT INTO bookmark(collection_id, member, line_no, entry_id, color, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*loc, entry_id, clean_color, clean, _now()),
        )
        new_id = cur.lastrowid
    return get_bookmark(conn, new_id)  # type: ignore[arg-type]


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
    """Rewrite the ``entry_id`` cache of every bookmark from its locator (after a reindex).

    Bookmarks whose record is gone get ``entry_id = NULL`` (orphans stay and can come back).
    Returns ``{"bookmarks", "relinked", "orphaned"}``; relinked counts caches that changed.
    """
    with _Tx(conn):
        rows = conn.execute(f"SELECT {_BM_COLUMNS} FROM bookmark").fetchall()  # noqa: S608
        found = _resolve(conn, [(r[1], r[2], r[3]) for r in rows])
        relinked = orphaned = 0
        for r in rows:
            new = found.get((r[1], r[2], r[3]))
            if new is None:
                orphaned += 1
            if new != r[4]:
                relinked += 1
                conn.execute("UPDATE bookmark SET entry_id = ? WHERE id = ?", (new, r[0]))
    return {"bookmarks": len(rows), "relinked": relinked, "orphaned": orphaned}


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
            conn.execute(
                "INSERT INTO bookmark(collection_id, member, line_no, entry_id, color, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (collection_id, member, line_no, entry[0] if entry else None, color, note, created),
            )
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
