"""Bookmarks of a case (table ``bookmark``; user data that survives reindexing) and record compare.

A bookmark is a record plus a color and a note (ARQUITETURA 9.3). Its identity has three parts:

* the locator ``(collection_id, member, line_no)``: stable, exported, shown when the record is gone;
* the lineage ``source_id``: the ``source`` row (one physical file, followed across rotations) the
  bookmark belongs to. A bookmark only ever links to records of its own lineage. When the source row
  is deleted (a stale file dropped under a rotation), a trigger sets the lineage to NULL: the bookmark
  stays as a *detached* orphan and never links again on its own;
* the fingerprint ``fp`` of the record content (byte offset, timestamp, first line and continuation
  lines). ``f:<digest>`` covers the whole content of a complete record. ``p<n>:<digest>`` covers the
  first ``n`` characters of an *open* record (the last record of its source, which a growing file can
  still extend) and matches a record whose content starts with them. Fingerprints only get stronger
  (:func:`linked_fp`): a complete one is final, an open one becomes complete or a longer prefix.

A random ``uid`` names the bookmark itself; export carries it and import deduplicates by it.

``entry_id`` is a cache kept by the writer in the same transaction that changes the records:
:func:`detach_entries` before records are deleted, :func:`attach_entries` after records are inserted
(orphans of the same lineage whose fingerprint matches are linked again; an open fingerprint becomes a
complete one once a later record exists) and :func:`move_members` when a rotation renames a source.
NULL means *orphan*. A linked bookmark is never unlinked in favor of an orphan, and one record has at
most one bookmark (unique index). :func:`reassociate` rebuilds every cache (schema migration 4, rows
changed outside the writer). On a case still at schema 3 (read-only connections) reads keep the
legacy resolution by locator.

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
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
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
EXPORT_VERSION = 2  # 2 adds uid, fp, orphan and detached; version 1 files are still read
IMPORT_VERSIONS = (1, 2)
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
_FP = re.compile(r"(?:f|p(?:0|[1-9][0-9]{0,11})):[0-9a-f]{32}\Z")
_UID = re.compile(r"[0-9a-f]{32}\Z")

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


# --------------------------------------------------------------------------- fingerprints


def record_text(msg: str, cont: str | None) -> str:
    """The content a fingerprint covers: first line, then the continuation lines (``\\n`` separated)."""
    return msg if cont is None else f"{msg}\n{cont}"


def _digest(kind: bytes, byte_off: int, ts: int | None, text: str) -> str:
    head = f"{byte_off}\x1f{'' if ts is None else ts}\x1f".encode()
    return hashlib.blake2b(kind + head + text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


def fingerprint(byte_off: int, ts: int | None, text: str, *, open_record: bool = False) -> str:
    """Fingerprint of a record (``text`` from :func:`record_text`).

    A complete record gets ``f:<digest>`` of its whole content. An open record (the last one of its
    source, which may still grow) gets ``p<n>:<digest>`` of its first ``n`` characters; replacement
    characters at the very end are left out, since they may be a character cut by the end of the data.
    """
    if not open_record:
        return f"f:{_digest(b'F', byte_off, ts, text)}"
    n = len(text.rstrip("�"))
    return f"p{n}:{_digest(b'P', byte_off, ts, text[:n])}"


def fp_matches(fp: str | None, byte_off: int, ts: int | None, text: str) -> bool:
    """True when the record has the bookmarked identity; a NULL fingerprint (older cases) matches nothing."""
    if not isinstance(fp, str) or not _FP.match(fp):
        return False
    if fp.startswith("f:"):
        return fp == f"f:{_digest(b'F', byte_off, ts, text)}"
    n = int(fp[1 : fp.index(":")])
    return len(text) >= n and fp[fp.index(":") + 1 :] == _digest(b"P", byte_off, ts, text[:n])


def is_open(fp: str | None) -> bool:
    return fp is not None and fp.startswith("p")


def linked_fp(fp: str | None, byte_off: int, ts: int | None, text: str, *, last: bool) -> str:
    """Fingerprint a bookmark keeps on a record its identity matched (``fp_matches``) or just took.

    Identities only get stronger. A complete fingerprint is final: kept as is, wherever the record sits
    now (a record that became the last one again, after a truncation, is still compared with its whole
    known content). An open fingerprint, or none, is taken again from the record: complete once a later
    record exists, else a prefix at least as long as the one it had.
    """
    if fp is not None and not is_open(fp):
        return fp
    return fingerprint(byte_off, ts, text, open_record=last)


def has_bookmarks(conn: sqlite3.Connection) -> bool:
    """One probe; the writer calls it before any bookmark work, so an empty table costs nothing more."""
    return conn.execute("SELECT 1 FROM bookmark LIMIT 1").fetchone() is not None


def _schema_v4(conn: sqlite3.Connection) -> bool:
    """True on a case at schema 4 (lineage and fingerprint), False on one still at 3 (read-only reads)."""
    # Column names from a cursor: PRAGMA is not allowed under read_guard.
    return "source_id" in {d[0] for d in conn.execute("SELECT * FROM bookmark LIMIT 0").description}


_Content = tuple[int, int, int, int | None, str]  # source_id, line_no, byte_off, ts, text


def _contents(conn: sqlite3.Connection, entry_ids: Iterable[int]) -> Iterator[tuple[int, _Content]]:
    """``(entry id, (source_id, line_no, byte_off, ts, text))``, one record in memory at a time."""
    ids = sorted(set(entry_ids))
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for eid, sid, line_no, byte_off, ts, msg, cont in conn.execute(
            "SELECT e.id, e.source_id, e.line_no, e.byte_off, e.ts, e.msg, b.cont FROM entry e"  # noqa: S608
            f" LEFT JOIN entry_body b ON b.entry_id = e.id WHERE e.id IN ({marks})",
            chunk,
        ):
            yield eid, (sid, line_no, byte_off, ts, record_text(msg, cont))


def _last_id(conn: sqlite3.Connection, source_id: int) -> int | None:
    """Id of the last record of a source (records of a source have ascending ids in file order)."""
    return conn.execute("SELECT max(id) FROM entry WHERE source_id = ?", (source_id,)).fetchone()[0]


def _fp_of_entry(conn: sqlite3.Connection, entry_id: int, last_id: int | None) -> str | None:
    for eid, (_sid, _line, byte_off, ts, text) in _contents(conn, [entry_id]):
        return fingerprint(byte_off, ts, text, open_record=eid == last_id)
    return None


# --------------------------------------------------------------------------- reads

_BM_COLUMNS = "id, collection_id, member, line_no, entry_id, color, note, created_at"
_INSERT_SQL = (
    "INSERT INTO bookmark(collection_id, member, line_no, entry_id, color, note, created_at, source_id, fp,"
    " uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def new_uid() -> str:
    """A random stable identity for a new bookmark (32 hex characters)."""
    return uuid.uuid4().hex


def _valid_caches(conn: sqlite3.Connection, bookmark_ids: list[int]) -> set[int]:
    """Bookmarks whose cached record exists in their lineage at their line (schema 4)."""
    valid: set[int] = set()
    for start in range(0, len(bookmark_ids), _CHUNK):
        chunk = bookmark_ids[start : start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        valid.update(
            r[0]
            for r in conn.execute(
                "SELECT b.id FROM bookmark b CROSS JOIN entry e ON e.id = b.entry_id"  # noqa: S608
                f" WHERE b.id IN ({marks}) AND e.source_id = b.source_id AND e.line_no = b.line_no",
                chunk,
            )
        )
    return valid


def _resolve_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> dict[int, int | None]:
    """bookmark id -> entry id (None for an orphan); rows start with ``_BM_COLUMNS``.

    Schema 4: the writer keeps the cache, so a cache that holds a record of the bookmark's lineage at
    its line is the answer and anything else is an orphan (rows changed outside the writer show as
    orphans until :func:`reassociate`; they are never shown on another record). Schema 3: the legacy
    resolution by locator.
    """
    if not _schema_v4(conn):
        return _legacy_resolve_rows(conn, rows)
    valid = _valid_caches(conn, [r[0] for r in rows if r[4] is not None])
    return {r[0]: (r[4] if r[0] in valid else None) for r in rows}


def _legacy_locators(
    conn: sqlite3.Connection, locators: Iterable[tuple[int, str, int]]
) -> dict[tuple[int, str, int], int]:
    """Entry id of each ``(collection_id, member, line_no)`` that exists (schema 3; one scan per source)."""
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


def _legacy_resolve_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> dict[int, int | None]:
    """Schema 3: a cache that still holds its locator, else whatever record sits at the locator now."""
    valid: set[int] = set()
    for bid, collection_id, member, line_no, entry_id in (r[:5] for r in rows):
        if (
            entry_id is not None
            and conn.execute(
                "SELECT 1 FROM entry e JOIN source s ON s.id = e.source_id"
                " WHERE e.id = ? AND e.line_no = ? AND s.collection_id = ? AND s.member = ?",
                (entry_id, line_no, collection_id, member),
            ).fetchone()
        ):
            valid.add(bid)
    stale = [(r[1], r[2], r[3]) for r in rows if r[0] not in valid]
    found = _legacy_locators(conn, stale) if stale else {}
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

    Schema 4: a bookmark marks the record its valid cache points at (one lookup per id through the
    unique index); an orphan marks nothing, even when another record now sits at its line.
    Schema 3: the legacy match by locator.
    """
    if (
        not isinstance(entry_ids, list)
        or not 1 <= len(entry_ids) <= MAX_LOOKUP_IDS
        or not all(is_sql_int(i) for i in entry_ids)
    ):
        raise BookmarkError("bad_ids", limit=MAX_LOOKUP_IDS)
    with read_guard(conn, PAGE_TIMEOUT_S):
        if not _schema_v4(conn):
            return _legacy_for_entries(conn, entry_ids)
        out: dict[int, dict[str, Any]] = {}
        unique = sorted(set(entry_ids))
        for start in range(0, len(unique), _CHUNK):
            chunk = unique[start : start + _CHUNK]
            qs = ",".join("?" * len(chunk))
            for eid, bid, color, note in conn.execute(
                "SELECT b.entry_id, b.id, b.color, b.note FROM bookmark b"  # noqa: S608
                f" CROSS JOIN entry e ON e.id = b.entry_id WHERE b.entry_id IN ({qs})"
                " AND e.source_id = b.source_id AND e.line_no = b.line_no",
                chunk,
            ):
                out[eid] = {"id": bid, "color": color, "has_note": bool(note)}
        return out


def _legacy_for_entries(conn: sqlite3.Connection, entry_ids: list[int]) -> dict[int, dict[str, Any]]:
    sql = f"SELECT {_BM_COLUMNS} FROM bookmark ORDER BY id DESC"  # noqa: S608
    marks = {(r[1], r[2], r[3]): r for r in conn.execute(sql)}  # the oldest bookmark of a locator wins
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


def _holder(conn: sqlite3.Connection, entry_id: int) -> int | None:
    """Bookmark that validly holds a record; a stale holder of the id (rows changed outside the
    writer, or an id reused after a deletion) is turned into an orphan so the record is free."""
    row = conn.execute("SELECT id FROM bookmark WHERE entry_id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    if _valid_caches(conn, [row[0]]):
        return row[0]
    conn.execute("UPDATE bookmark SET entry_id = NULL WHERE id = ?", (row[0],))
    return None


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
        loc = conn.execute(
            "SELECT e.source_id, s.collection_id, s.member, e.line_no FROM entry e"
            " JOIN source s ON s.id = e.source_id WHERE e.id = ?",
            (entry_id,),
        ).fetchone()
        if loc is None:
            raise BookmarkError("entry_not_found", entry_id=entry_id)
        existing = _holder(conn, entry_id)
        if existing is not None:  # orphans at the same line belong to other records: they do not count
            raise BookmarkError("already_bookmarked", bookmark_id=existing)
        if conn.execute("SELECT count(*) FROM bookmark").fetchone()[0] >= MAX_BOOKMARKS:
            raise BookmarkError("too_many", limit=MAX_BOOKMARKS)
        source_id, collection_id, member, line_no = loc
        cur = conn.execute(
            _INSERT_SQL,
            (collection_id, member, line_no, entry_id, clean_color, clean, _now(), source_id,
             _fp_of_entry(conn, entry_id, _last_id(conn, source_id)), new_uid()),
        )  # fmt: skip
        new_id = cur.lastrowid
    return get_bookmark(conn, new_id)  # type: ignore[arg-type]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring a case created by an older dmslog to the current schema before writing a bookmark."""
    from .db import SCHEMA_VERSION, get_user_version, migrate

    if get_user_version(conn) < SCHEMA_VERSION:
        migrate(conn)


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
    """Rebuild the ``entry_id`` cache of every bookmark (full pass; idempotent).

    The writer keeps the cache right on its own; this pass is for the schema migration and for rows
    changed outside the writer. Rules, in this order: a bookmark whose cached record is still at its
    line in its lineage and has its fingerprint keeps it (continuity); then each other bookmark with a
    fingerprint takes the record at its line in its lineage when the fingerprint matches and nobody
    holds the record; then each bookmark without a fingerprint (older cases) takes the record at its
    line when it is free, and gets its fingerprint. Everything else is an orphan. Detached bookmarks
    (no lineage) stay orphans. Returns ``{"bookmarks", "relinked", "orphaned"}``; relinked counts caches
    that changed.
    """
    with _Tx(conn):
        return relink_all(conn)


def relink_all(conn: sqlite3.Connection) -> dict[str, int]:
    """:func:`reassociate` inside the caller's transaction (used by the schema migration)."""
    rows = conn.execute("SELECT id, source_id, line_no, entry_id, fp FROM bookmark ORDER BY id").fetchall()
    wanted: dict[int, set[int]] = {}
    for _bid, sid, line_no, _cached, _fp in rows:
        if sid is not None:
            wanted.setdefault(sid, set()).add(line_no)
    at_line: dict[tuple[int, int], int] = {}  # (source_id, line_no) -> entry id
    last: dict[int, int | None] = {}
    for sid, lines in wanted.items():
        for eid, line_no in conn.execute("SELECT id, line_no FROM entry WHERE source_id = ?", (sid,)):
            if line_no in lines:
                at_line[(sid, line_no)] = eid
        last[sid] = _last_id(conn, sid)
    candidates = {r[0]: at_line.get((r[1], r[2])) for r in rows if r[1] is not None}
    targets: dict[int, list[tuple[int, str | None]]] = {}
    for bid, _sid, _line, _cached, fp in rows:
        eid = candidates.get(bid)
        if eid is not None:
            targets.setdefault(eid, []).append((bid, fp))
    matched: dict[int, bool] = {}  # bookmark id -> its fingerprint matches the record at its line
    fresh: dict[int, str] = {}  # bookmark id -> fingerprint it keeps if it takes the record at its line
    for eid, (sid, _line, byte_off, ts, text) in _contents(conn, targets):
        for bid, fp in targets[eid]:
            matched[bid] = fp_matches(fp, byte_off, ts, text)
            fresh[bid] = linked_fp(fp, byte_off, ts, text, last=eid == last.get(sid))

    new: dict[int, int | None] = {r[0]: None for r in rows}
    taken: set[int] = set()
    phases = (
        lambda bid, cached, fp, eid: cached == eid and matched[bid],  # continuity first
        lambda bid, cached, fp, eid: matched[bid],
        lambda bid, cached, fp, eid: fp is None,
    )
    for accept in phases:
        for bid, _sid, _line, cached, fp in rows:
            eid = candidates.get(bid)
            if new[bid] is None and bid in fresh and eid not in taken and accept(bid, cached, fp, eid):
                new[bid] = eid
                taken.add(eid)  # type: ignore[arg-type]
    relinked = orphaned = 0
    updates: list[tuple[int | None, str | None, int]] = []
    for bid, _sid, _line, cached, fp in rows:
        eid = new[bid]
        new_fp = fp if eid is None else fresh[bid]
        orphaned += eid is None
        relinked += eid != cached
        if eid != cached or new_fp != fp:
            updates.append((eid, new_fp, bid))
    # Two steps: a record can move from one bookmark to another (the unique index on entry_id).
    conn.executemany("UPDATE bookmark SET entry_id = NULL WHERE id = ?", [(u[2],) for u in updates])
    conn.executemany("UPDATE bookmark SET entry_id = ?, fp = ? WHERE id = ?", updates)
    return {"bookmarks": len(rows), "relinked": relinked, "orphaned": orphaned}


def adopt_lineage(conn: sqlite3.Connection) -> None:
    """Schema migration 4: the lineage of each bookmark is the source that holds its member today.

    A bookmark whose member has no source any more (a stale file dropped by a rotation) is detached.
    """
    conn.execute(
        "UPDATE bookmark SET source_id = (SELECT s.id FROM source s"
        " WHERE s.collection_id = bookmark.collection_id AND s.member = bookmark.member)"
    )


# --------------------------------------------------------------------------- writer hooks
# Called by store/writer.py inside its open transaction, so the cache changes atomically with the
# records (a rollback undoes both). Each starts with one probe and returns at once on an empty table.

_NewRow = tuple[int, int, int, int, int | None, str]  # entry_id, source_id, line_no, byte_off, ts, msg


def detach_entries(conn: sqlite3.Connection, source_id: int, from_byte_off: int = 0) -> int:
    """Before the records of a source at or after ``from_byte_off`` are deleted: orphan their bookmarks.

    Each keeps its fingerprint, made stronger (:func:`linked_fp`) from its record before the record
    disappears: complete when a later record exists in the source, open for the last record (a growing
    file re-parses it); a complete fingerprint stays as it is.
    Each also gets a new ``orphan_seq`` (one per call): when several orphans of a line match a record
    inserted later, the one orphaned last takes it, so a bookmark whose record is parsed again (an
    open record that grew) keeps it even when an older orphan's identity now matches too.
    Returns the count.
    """
    if not has_bookmarks(conn):
        return 0
    rows = conn.execute(
        "SELECT b.id, b.fp, b.entry_id FROM bookmark b CROSS JOIN entry e ON e.id = b.entry_id"
        " WHERE b.source_id = ? AND b.entry_id IS NOT NULL AND e.source_id = b.source_id AND e.byte_off >= ?",
        (source_id, from_byte_off),
    ).fetchall()
    if not rows:
        return 0
    last = _last_id(conn, source_id)
    refresh = {eid: fp for _bid, fp, eid in rows if is_open(fp) or fp is None}  # complete ones are final
    fresh = {
        eid: linked_fp(refresh[eid], byte_off, ts, text, last=eid == last)
        for eid, (_sid, _line, byte_off, ts, text) in _contents(conn, refresh)
    }
    seq = (conn.execute("SELECT max(orphan_seq) FROM bookmark").fetchone()[0] or 0) + 1
    conn.executemany(
        "UPDATE bookmark SET entry_id = NULL, fp = ?, orphan_seq = ? WHERE id = ?",
        [(fresh.get(eid, fp), seq, bid) for bid, fp, eid in rows],
    )
    return len(rows)


def attach_entries(conn: sqlite3.Connection, rows: Iterable[_NewRow]) -> int:
    """After records were inserted: link orphans of the same lineage and close open fingerprints.

    ``rows`` holds ``(entry_id, source_id, line_no, byte_off, ts, msg)`` of the inserted records (new
    ids are the highest of their source). Work is driven by two partial indexes: the orphans with a
    lineage and the linked bookmarks with an open fingerprint, so the cost follows those bookmarks,
    not the size of the table; ``rows`` is only read (one pass, filtered by source) when one exists.

    * An orphan links to the inserted record at its line when the fingerprint matches (a bookmark
      without one, from an older case, takes the record if no fingerprint claims it). One bookmark
      per record; among matching orphans the one orphaned last wins (``orphan_seq``), so a bookmark
      whose record is only parsed again keeps it: a linked bookmark is never displaced.
    * A linked bookmark with an open fingerprint gets the complete one as soon as a later record of
      its source exists (its record can no longer grow).

    Returns the number of bookmarks linked.
    """
    if not has_bookmarks(conn):
        return 0
    orphan_sources = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_id FROM bookmark WHERE entry_id IS NULL AND source_id IS NOT NULL"
        )
    }
    open_sources = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_id FROM bookmark WHERE entry_id IS NOT NULL AND substr(fp, 1, 1) = 'p'"
        )
    }
    busy = orphan_sources | open_sources
    if not busy:
        return 0
    by_source: dict[int, list[_NewRow]] = {}
    for row in rows:
        if row[1] in busy:
            by_source.setdefault(row[1], []).append(row)
    linked = 0
    for sid, new_rows in by_source.items():
        newest = new_rows[-1][0]
        if sid in open_sources:
            _close_open(conn, sid, newest)
        if sid in orphan_sources:
            linked += _link_orphans(conn, sid, new_rows, newest)
    return linked


def _close_open(conn: sqlite3.Connection, source_id: int, newest: int) -> None:
    """Open fingerprints of records that now have a later record become complete ones."""
    done = [
        (bid, eid)
        for bid, eid in conn.execute(
            "SELECT id, entry_id FROM bookmark WHERE source_id = ? AND entry_id IS NOT NULL"
            " AND substr(fp, 1, 1) = 'p'",
            (source_id,),
        )
        if eid < newest
    ]
    if not done:
        return
    full = {eid: fingerprint(c[2], c[3], c[4]) for eid, c in _contents(conn, [eid for _bid, eid in done])}
    conn.executemany(
        "UPDATE bookmark SET fp = ? WHERE id = ?", [(full[eid], bid) for bid, eid in done if eid in full]
    )


def _link_orphans(conn: sqlite3.Connection, source_id: int, new_rows: list[_NewRow], newest: int) -> int:
    wanted: dict[int, list[tuple[int, str | None]]] = {}
    for bid, line_no, fp in conn.execute(
        "SELECT id, line_no, fp FROM bookmark WHERE source_id = ? AND entry_id IS NULL"
        " ORDER BY fp IS NULL, orphan_seq DESC, id",
        (source_id,),
    ):
        wanted.setdefault(line_no, []).append((bid, fp))
    links: list[tuple[int, str, int]] = []
    for entry_id, _sid, line_no, byte_off, ts, msg in new_rows:
        candidates = wanted.get(line_no)
        if not candidates:
            continue
        body = conn.execute("SELECT cont FROM entry_body WHERE entry_id = ?", (entry_id,)).fetchone()
        text = record_text(msg, body[0] if body else None)
        pick = next((c for c in candidates if fp_matches(c[1], byte_off, ts, text)), None)
        if pick is None:
            pick = next((c for c in candidates if c[1] is None), None)
        if pick is None:
            continue
        links.append((entry_id, linked_fp(pick[1], byte_off, ts, text, last=entry_id == newest), pick[0]))
        candidates.remove(pick)
    if links:
        # A holder of a new id can only be stale (rows changed outside the writer): it lets go first.
        conn.executemany("UPDATE bookmark SET entry_id = NULL WHERE entry_id = ?", [(e[0],) for e in links])
        conn.executemany("UPDATE bookmark SET entry_id = ?, fp = ? WHERE id = ?", links)
    return len(links)


def move_members(conn: sqlite3.Connection, renames: Mapping[int, str]) -> None:
    """When sources are renamed (rotation, ``{source_id: new member}``): their bookmarks follow them.

    Only bookmarks of the renamed source's lineage move; detached orphans (whose source was dropped)
    stay where they are, so they never travel with the source that took their file name. Cached entry
    ids stay right: the records keep their ids.
    """
    if not renames or not has_bookmarks(conn):
        return
    moves = [(member, sid) for sid, member in renames.items()]
    conn.executemany("UPDATE bookmark SET member = ? WHERE source_id = ?", moves)


# --------------------------------------------------------------------------- export / import


def export_bookmarks(conn: sqlite3.Connection) -> dict[str, Any]:
    """A shareable JSON document (version 2).

    Each item has the locator, color, note, date and ERS identity of the collection, the stable
    identity of the bookmark (``uid``; null on a case still at schema 3) and the identity of the record:
    ``fp`` (null for a bookmark from an older case that never saw its record), ``orphan`` (no record
    now) and ``detached`` (its source was dropped; it never links on its own).
    """
    with read_guard(conn, PAGE_TIMEOUT_S):
        if _schema_v4(conn):
            extra = "b.fp, b.source_id IS NULL, b.uid"
        else:  # schema 3: no identities; detached when no source holds the member any more
            extra = (
                "NULL, NOT EXISTS (SELECT 1 FROM source s"
                " WHERE s.collection_id = b.collection_id AND s.member = b.member), NULL"
            )
        rows = conn.execute(
            "SELECT b.id, b.collection_id, b.member, b.line_no, b.entry_id, b.color, b.note, b.created_at,"  # noqa: S608
            f" c.ers_guid, c.ers_ts, {extra} FROM bookmark b LEFT JOIN collection c ON c.id = b.collection_id"
            " ORDER BY b.id"
        ).fetchall()
        resolved: dict[int, int | None] = {}
        for start in range(0, len(rows), _CHUNK):
            resolved.update(_resolve_rows(conn, [r[:8] for r in rows[start : start + _CHUNK]]))
    out: list[dict[str, Any]] = []
    size = 256
    truncated = False
    for r in rows:
        item = {"uid": r[12], "ers_guid": r[8], "ers_ts": r[9], "member": r[2], "line_no": r[3],
                "color": r[5], "note": r[6], "created_at": r[7], "fp": r[10],
                "orphan": resolved[r[0]] is None, "detached": bool(r[11])}  # fmt: skip
        size += len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if size > MAX_EXPORT_BYTES:
            truncated = True  # never produce a document the import route would refuse
            break
        out.append(item)
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "truncated": truncated, "bookmarks": out}


def _collection_for(conn: sqlite3.Connection, guid: str | None, stamp: str | None, member: str) -> int | None:
    """Collection of an item. With an ERS identity: the collection of that capture that holds the member (the
    Enterprise and the Sensitive zip share GUID and capture time), else its first one. Without one: the only
    collection that holds the member, or the case's only collection (a case groups collections, U21)."""
    if guid is not None:
        rows = conn.execute(
            "SELECT id FROM collection WHERE ers_guid = ? AND (? IS NULL OR ers_ts = ?) ORDER BY id",
            (guid, stamp, stamp),
        ).fetchall()
        if not rows:
            return None
        ids = [r[0] for r in rows]
        holder = next((cid for cid in ids if _holds(conn, cid, member)), None)
        return ids[0] if holder is None else holder
    rows = conn.execute("SELECT id FROM collection").fetchall()
    if len(rows) == 1:
        return rows[0][0]
    holders = [r[0] for r in rows if r[0] is not None and _holds(conn, r[0], member)]
    return holders[0] if len(holders) == 1 else None


def _holds(conn: sqlite3.Connection, collection_id: int, member: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM source WHERE collection_id = ? AND member = ?", (collection_id, member)
    ).fetchone()
    return row is not None


# member, line_no, color, note, created_at, ers_guid, ers_ts, fp, detached, uid, orphan
_Parsed = tuple[str, int, str, str | None, str, str | None, str | None, str | None, bool, str | None, bool]


def import_bookmarks(conn: sqlite3.Connection, data: Any) -> dict[str, int]:
    """Merge an export into the case. Returns the counts per outcome.

    Deduplication follows the bookmark, never the record alone: an item with a ``uid`` is ``existing``
    when the case holds a bookmark with that uid (wherever it is now: a rotation may have moved it),
    else it is imported keeping its uid, so importing a file twice changes nothing and two distinct
    bookmarks that share a locator and a record identity both survive. A version-2 item without a uid
    (a file edited by hand) is ``existing`` when an unclaimed bookmark at its locator has its identity;
    each bookmark of the case answers for one item at most, so the multiplicity of the file is kept.
    The uids are resolved first, for the whole file, so a bookmark named by its uid is never taken by
    an item without one, whatever the order of the items.
    A version-1 item is ``existing`` when any bookmark sits at its locator.

    The record identity decides the link, never the line alone: an item links to the record at its
    locator only when its fingerprint matches (a version-1 item, which has none, takes the record if it
    is free) and nobody holds it; otherwise it is kept as an orphan of that source, so no note is lost
    or moved to another record. A complete fingerprint stays complete. A detached item stays detached.
    Items with a fingerprint are placed first, those linked when exported before the orphans.
    """
    if (
        not isinstance(data, dict)
        or data.get("format") != EXPORT_FORMAT
        or data.get("version") not in IMPORT_VERSIONS
        or isinstance(data.get("version"), bool)
        or not isinstance(data.get("bookmarks"), list)
    ):
        raise BookmarkError("bad_import", format=_short(data, "format"), version=_short(data, "version"))
    items = data["bookmarks"]
    if len(items) > MAX_BOOKMARKS:
        raise BookmarkError("import_too_large", limit=MAX_BOOKMARKS)
    legacy = data["version"] == 1
    counts = {"imported": 0, "existing": 0, "unmatched": 0, "invalid": 0}
    parsed: list[_Parsed] = []
    seen_uids: set[str] = set()
    for item in items:
        one = _parse_item(item, legacy)
        if one is not None and one[9] is not None:
            if one[9] in seen_uids:  # one bookmark appears once; a repeated uid is a malformed file
                one = None
            else:
                seen_uids.add(one[9])
        if one is None:
            counts["invalid"] += 1
        else:
            parsed.append(one)
    # Stable: identified items claim their records first, and among them the ones that were linked
    # when exported (an orphan with the same identity never takes the record from its holder).
    parsed.sort(key=lambda p: (p[7] is None, p[10]))
    _ensure_schema(conn)
    with _Tx(conn):
        total = conn.execute("SELECT count(*) FROM bookmark").fetchone()[0]
        claimed: set[int] = set()  # bookmarks that already answered for an item of this file
        # First pass: every item whose uid the case holds reserves that bookmark before any item
        # without a uid looks for one by identity, whatever the order of the file; otherwise the
        # fallback could take the very bookmark a later item names and one note of the file would
        # be lost. A bookmark never answers for two items.
        rest: list[_Parsed] = []
        for one in parsed:
            row = None
            if one[9] is not None:
                row = conn.execute("SELECT id FROM bookmark WHERE uid = ?", (one[9],)).fetchone()
            if row is None:
                rest.append(one)
            elif row[0] in claimed:  # unreachable with unique uids in file and case; never reuse
                counts["invalid"] += 1
            else:
                claimed.add(row[0])
                counts["existing"] += 1
        # Second pass: the rest (new uids and items without one), in placement order.
        for one in rest:
            outcome = _import_one(conn, one, legacy, total, claimed)
            counts[outcome] += 1
            total += outcome == "imported"
    return counts


def _import_one(conn: sqlite3.Connection, one: _Parsed, legacy: bool, total: int, claimed: set[int]) -> str:
    """Place one item the first pass did not resolve: its uid, if any, is not in the case."""
    member, line_no, color, note, created, guid, stamp, fp, detached, uid, _orphan = one
    collection_id = _collection_for(conn, guid, stamp, member)
    if collection_id is None:
        return "unmatched"
    src = conn.execute(
        "SELECT id FROM source WHERE collection_id = ? AND member = ?", (collection_id, member)
    ).fetchone()
    if src is None and not detached:
        return "unmatched"
    if uid is None:
        here = conn.execute(
            "SELECT id, source_id, entry_id, fp FROM bookmark"
            " WHERE collection_id = ? AND member = ? AND line_no = ? ORDER BY id",
            (collection_id, member, line_no),
        ).fetchall()
        same = _same_bookmark(conn, [h for h in here if h[0] not in claimed], fp, legacy, detached)
        if same is not None:
            claimed.add(same)
            return "existing"
    if total >= MAX_BOOKMARKS:
        raise BookmarkError("too_many", limit=MAX_BOOKMARKS)
    source_id = None if detached else src[0]
    entry_id = None
    new_fp = fp
    if source_id is not None:
        found = conn.execute(
            "SELECT id FROM entry WHERE source_id = ? AND line_no = ?", (source_id, line_no)
        ).fetchone()
        if found is not None and _holder(conn, found[0]) is None:
            _eid, (_s, _l, byte_off, ts, text) = next(_contents(conn, [found[0]]))
            if fp is None or fp_matches(fp, byte_off, ts, text):
                entry_id = found[0]
                new_fp = linked_fp(fp, byte_off, ts, text, last=entry_id == _last_id(conn, source_id))
    cur = conn.execute(
        _INSERT_SQL,
        (collection_id, member, line_no, entry_id, color, note, created, source_id, new_fp, uid or new_uid()),
    )
    claimed.add(cur.lastrowid)  # type: ignore[arg-type]
    return "imported"


def _same_bookmark(
    conn: sqlite3.Connection, here: list[tuple[Any, ...]], fp: str | None, legacy: bool, detached: bool
) -> int | None:
    """A bookmark at the locator (not yet claimed) that already is the item (same identity), or None."""
    if legacy:
        return here[0][0] if here else None
    for bid, source_id, entry_id, bfp in here:
        if (source_id is None) != detached:
            continue
        if bfp == fp:
            return bid
        if fp is not None and entry_id is not None:
            for _eid, (_s, _l, byte_off, ts, text) in _contents(conn, [entry_id]):
                if fp_matches(fp, byte_off, ts, text):
                    return bid
    return None


def _short(data: Any, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    return json.dumps(value)[:40]


def _parse_item(item: Any, legacy: bool) -> _Parsed | None:
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
    fp, detached, uid, orphan = None, False, None, False
    if not legacy:
        fp, detached, uid = item.get("fp"), item.get("detached", False), item.get("uid")
        orphan = item.get("orphan", False)
        if fp is not None and not (isinstance(fp, str) and _FP.match(fp)):
            return None
        if uid is not None and not (isinstance(uid, str) and _UID.match(uid)):
            return None
        if not isinstance(detached, bool) or not isinstance(orphan, bool):
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
    return member, line_no, color or DEFAULT_COLOR, note, created, guid, stamp, fp, detached, uid, orphan


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
