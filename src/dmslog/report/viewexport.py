"""Copy and export of the current view: value lists, original lines and CSV (ARQUITETURA 10.2).

Everything here reads a case through the same keyset paging as the grid (``query.page``, never OFFSET)
and yields text in chunks, so memory stays constant however many rows are exported. The text of the
log is original (U18: the app does not anonymize); there is no HTML anywhere.

CSV: UTF-8, CRLF line ends, comma separator, quotes doubled (RFC 4180), optional BOM for Excel on
Windows. Formula injection: a text cell that starts with ``=``, ``+``, ``-``, ``@``, TAB or CR gets a
leading apostrophe, which spreadsheets show as a text marker (OWASP "CSV injection"). The guard also
changes legitimate text such as ``-5`` or ``+55 11...`` into ``'-5``; numbers written by this module
(ids, durations) are not text and stay untouched. Value lists and original lines are plain text for
pasting into other tools and are NOT guarded.

Limits: ``max_rows`` (default 100 000, at most 1 000 000) counts records (or events) read from the view;
a record text (message plus continuation, one budget) is cut at ``MAX_RECORD_CHARS`` characters in the
line export, with a visible notice. ``unique`` deduplicates in a temporary SQLite file on disk (removed
at the end), so memory stays constant however many distinct values there are.
The first line of a record is the grid preview (``query.MSG_PREVIEW_CHARS`` characters) in the value
list and the view CSV.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from .. import messages
from ..parse.envelope import LEVEL_NAMES
from ..store import collections as store_collections
from ..store import events as store_events
from ..store import query
from ..store.filters import compile_filter

KINDS = ("values", "lines", "view_csv", "events_csv")
DEFAULT_MAX_ROWS = 100_000
MAX_ROWS_CAP = 1_000_000
PAGE_ROWS = 500
LINES_CHUNK = 100  # records whose full text is read at once (memory bound: x MAX_RECORD_CHARS)
MAX_RECORD_CHARS = 200_000
MAX_COLUMNS = 24
CHUNK_CHARS = 64 * 1024
BOM = "﻿"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SEPARATORS = {"newline": "\n", "comma": ","}
QUOTES = ("none", "double", "single")
# collection: the C<n> tag of the record's collection group (U21)
FIXED_COLUMNS = ("ts", "pid", "tid", "level", "template", "source", "collection", "msg")
_FIELD_COLUMN = re.compile(r"fields\.[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_KEY_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_EPOCH = datetime(1970, 1, 1)


class ViewExportError(ValueError):
    """A bad request; ``code`` is stable, the text is English."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- CSV and lists


def guard_formula(text: str) -> str:
    """Prefix an apostrophe to text a spreadsheet could take for a formula."""
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def csv_cell(value: Any) -> str:
    """One RFC 4180 field: None is empty, numbers are written as they are, text is guarded and quoted
    when it holds a comma, a quote or a line break."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = guard_formula(str(value))
    if any(c in text for c in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_row(cells: Iterable[Any]) -> str:
    return ",".join(csv_cell(c) for c in cells) + "\r\n"


def quote_value(value: str, quote: str) -> str:
    if quote == "double":
        return '"' + value.replace('"', '""') + '"'
    if quote == "single":
        return "'" + value.replace("'", "''") + "'"
    return value


def format_list(values: Iterable[str], separator: str = "newline", quote: str = "none",
                unique: bool = False) -> str:  # fmt: skip
    """The whole list as one string (for tests and small inputs; the streaming form is below)."""
    return "".join(_list_chunks(iter(values), separator, quote, unique))


def _list_chunks(values: Iterator[str], separator: str, quote: str, unique: bool) -> Iterator[str]:
    sep = SEPARATORS[separator]
    parts: list[str] = []
    size = 0
    first = True
    with _Dedup(unique) as dedup:
        for value in values:
            if dedup.seen(value):
                continue
            piece = quote_value(value, quote)
            parts.append(piece if first else sep + piece)
            first = False
            size += len(piece) + 1
            if size >= CHUNK_CHARS:
                yield "".join(parts)
                parts, size = [], 0
    if parts:
        yield "".join(parts)


class _Dedup:
    """First-seen test with bounded memory: the values live in a temporary SQLite file (private folder,
    mode 0700 on POSIX), removed on exit. Disabled, it lets every value through."""

    _BATCH = 20_000

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._dir: tempfile.TemporaryDirectory[str] | None = None
        self._conn: sqlite3.Connection | None = None
        self._pending = 0

    def __enter__(self) -> _Dedup:
        if self._enabled:
            self._dir = tempfile.TemporaryDirectory(prefix="dmslog-unique-")  # mkdtemp: private folder
            self._conn = sqlite3.connect(Path(self._dir.name) / "seen.sqlite", isolation_level=None)
            self._conn.execute("PRAGMA journal_mode = OFF")
            self._conn.execute("PRAGMA synchronous = OFF")
            self._conn.execute("CREATE TABLE seen(v TEXT PRIMARY KEY) WITHOUT ROWID")
            self._conn.execute("BEGIN")
        return self

    def seen(self, value: str) -> bool:
        if self._conn is None:
            return False
        added = self._conn.execute("INSERT OR IGNORE INTO seen(v) VALUES (?)", (value,)).rowcount
        self._pending += 1
        if self._pending >= self._BATCH:
            self._conn.execute("COMMIT")
            self._conn.execute("BEGIN")
            self._pending = 0
        return added == 0

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
        if self._dir is not None:
            self._dir.cleanup()


# --------------------------------------------------------------------------- request


@dataclass(frozen=True, slots=True)
class ViewRequest:
    kind: str
    filter: Any = None
    max_rows: int = DEFAULT_MAX_ROWS
    bom: bool = False
    columns: tuple[str, ...] = ()
    column: str | None = None
    key: str | None = None
    separator: str = "newline"
    quote: str = "none"
    unique: bool = False
    events: Mapping[str, Any] = field(default_factory=dict)


def _bad(field_name: str) -> ViewExportError:
    return ViewExportError("invalid_field", messages.VIEWEXPORT_BAD_FIELD.format(field=field_name))


@cache
def catalog_fields() -> frozenset[str]:
    """Field names the adapter templates can extract (the allow-list for ``fields.<name>`` columns)."""
    from ..parse.templates import load_catalog

    names: set[str] = set()
    for tpl in load_catalog().templates():
        names |= tpl.field_names
    return frozenset(names)


def _column_name(name: Any) -> str:
    known = isinstance(name, str) and (
        name in FIXED_COLUMNS or (_FIELD_COLUMN.match(name) and name[len("fields.") :] in catalog_fields())
    )
    if not known:
        raise ViewExportError("unknown_column", messages.VIEWEXPORT_BAD_COLUMN.format(name=str(name)[:64]))
    return name


def _choice(raw: Mapping[str, Any], name: str, choices: Sequence[str], default: str) -> str:
    value = raw.get(name, default)
    if value not in choices:
        raise ViewExportError(
            "invalid_field", messages.VIEWEXPORT_BAD_CHOICE.format(field=name, choices=", ".join(choices))
        )
    return value


def parse_request(raw: Mapping[str, Any]) -> ViewRequest:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ViewExportError("invalid_kind", messages.VIEWEXPORT_BAD_KIND.format(kinds=", ".join(KINDS)))
    flt = raw.get("filter")
    if flt is not None and not isinstance(flt, dict):
        raise _bad("filter")
    max_rows = raw.get("max_rows", DEFAULT_MAX_ROWS)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_ROWS_CAP:
        raise ViewExportError("bad_limit", messages.VIEWEXPORT_BAD_LIMIT.format(limit=MAX_ROWS_CAP))
    bom = raw.get("bom", False)
    if not isinstance(bom, bool):
        raise _bad("bom")
    unique = raw.get("unique", False)
    if not isinstance(unique, bool):
        raise _bad("unique")
    events = raw.get("events") or {}
    if not isinstance(events, dict):
        raise ViewExportError("invalid_field", messages.VIEWEXPORT_BAD_EVENT_FILTER)
    columns: tuple[str, ...] = ()
    column = key = None
    if kind == "view_csv":
        listed = raw.get("columns", [])
        if not isinstance(listed, list):
            raise _bad("columns")
        if len(listed) > MAX_COLUMNS:
            raise ViewExportError(
                "too_many_columns", messages.VIEWEXPORT_TOO_MANY_COLUMNS.format(limit=MAX_COLUMNS)
            )
        columns = tuple(dict.fromkeys(_column_name(name) for name in listed))
    if kind == "values":
        column, key = raw.get("column"), raw.get("key")
        if (column is None) == (key is None):
            raise ViewExportError("invalid_field", messages.VIEWEXPORT_VALUES_NEED_SOURCE)
        if column is not None:
            column = _column_name(column)
        elif not isinstance(key, str) or not _KEY_TYPE.match(key):
            raise ViewExportError("invalid_field", messages.VIEWEXPORT_BAD_KEY_TYPE)
    return ViewRequest(
        kind=kind,
        filter=flt,
        max_rows=max_rows,
        bom=bom,
        columns=columns,
        column=column,
        key=key,
        separator=_choice(raw, "separator", tuple(SEPARATORS), "newline"),
        quote=_choice(raw, "quote", QUOTES, "none"),
        unique=unique,
        events=events,
    )


# --------------------------------------------------------------------------- reading


def format_ts(ms: int | None) -> str:
    """Log time as logged (the milliseconds hold the wall clock read as UTC; U6); empty when unknown."""
    if ms is None:
        return ""
    try:
        moment = _EPOCH + timedelta(milliseconds=ms)
    except OverflowError:
        return ""
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def entry_total(conn: sqlite3.Connection, flt: Any, max_rows: int) -> int:
    """Records the view holds, counted only up to ``max_rows + 1`` (so ``> max_rows`` means truncated)."""
    cf = compile_filter(flt)
    with query.read_guard(conn, query.PAGE_TIMEOUT_S):
        row = conn.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM entry e WHERE ({cf.sql}) LIMIT ?)",  # noqa: S608
            (*cf.params, max_rows + 1),
        ).fetchone()
    return int(row[0])


def event_total(conn: sqlite3.Connection, flt: store_events.EventFilter, max_rows: int) -> int:
    where, params = store_events._where(flt)
    with query.read_guard(conn, query.PAGE_TIMEOUT_S):
        row = conn.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM event ev WHERE {where} LIMIT ?)",  # noqa: S608
            (*params, max_rows + 1),
        ).fetchone()
    return int(row[0])


def _walk(
    conn: sqlite3.Connection, flt: Any, columns: list[str], max_rows: int
) -> Iterator[list[dict[str, Any]]]:
    """Pages of the view by keyset on ``(ts, id)``, at most ``max_rows`` records in all."""
    compiled = compile_filter(flt)
    cursor = None
    remaining = max_rows
    while remaining > 0:
        page = query.page(conn, compiled, cursor, "fwd", min(PAGE_ROWS, remaining), columns)
        if not page.rows:
            return
        yield page.rows
        remaining -= len(page.rows)
        if not page.has_more:
            return
        cursor = page.last


def _source_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {int(i): str(n) for i, n in conn.execute("SELECT id, file_name FROM source")}


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value) if isinstance(value, dict | list) else str(value)


def _cell_text(
    row: Mapping[str, Any], column: str, sources: Mapping[int, str], tags: Mapping[int, str] | None = None
) -> str:
    if column == "ts":
        return format_ts(row["ts"])
    if column == "pid":
        return "" if row["pid"] is None else str(row["pid"])
    if column == "tid":
        return "" if row["tid"] is None else f"0x{row['tid']:x}"
    if column == "level":
        return LEVEL_NAMES.get(row["level"], "") if row["level"] is not None else ""
    if column == "template":
        return row.get("tpl") or ""
    if column == "source":
        return sources.get(row["source_id"], str(row["source_id"]))
    if column == "collection":  # the tag of the collection group, as the collections route numbers it
        group = row.get("collection")
        return "" if group is None else (tags or {}).get(group, str(group))
    if column == "msg":
        return row["msg"] or ""
    return _field_text(row.get(column))


def _extra_columns(names: Iterable[str]) -> list[str]:
    names = list(names)
    extra = ["tpl"] if "template" in names else []
    if "collection" in names:
        extra.append("collection")
    return extra + [n for n in names if n.startswith("fields.")]


def _flush(parts: list[str], size: int, limit: int = CHUNK_CHARS) -> tuple[str | None, list[str], int]:
    if size < limit:
        return None, parts, size
    return "".join(parts), [], 0


# --------------------------------------------------------------------------- the four exports


def stream_values(conn: sqlite3.Connection, req: ViewRequest) -> Iterator[str]:
    """Values of one column or one key type over the view, as a list (separator, quotes, unique)."""

    def values() -> Iterator[str]:
        if req.key is not None:
            for rows in _walk(conn, req.filter, [], req.max_rows):
                by_entry: dict[int, list[str]] = {}
                marks = ",".join("?" * len(rows))
                cur = conn.execute(
                    f"SELECT entry_id, value FROM entry_key WHERE key_type = ? AND entry_id IN ({marks})"  # noqa: S608
                    " ORDER BY entry_id, value",
                    (req.key, *[r["id"] for r in rows]),
                )
                for entry_id, value in cur:
                    by_entry.setdefault(entry_id, []).append(value)
                for row in rows:
                    yield from by_entry.get(row["id"], ())
            return
        assert req.column is not None
        sources = _source_names(conn) if req.column == "source" else {}
        tags = store_collections.group_tags(conn) if req.column == "collection" else {}
        for rows in _walk(conn, req.filter, _extra_columns([req.column]), req.max_rows):
            for row in rows:
                text = _cell_text(row, req.column, sources, tags)
                if text != "":
                    yield text

    yield from _list_chunks(values(), req.separator, req.quote, req.unique)


def _record_head(row: Mapping[str, Any]) -> str:
    """The header of a record as the .txt report shows it: ``ts [PID=n][TID=0x..] LEVEL - ``."""
    if row["ts"] is None:
        return ""
    ids = f"[PID={row['pid']}]" if row["pid"] is not None else ""
    if row["tid"] is not None:
        ids += f"[TID=0x{row['tid']:x}]"
    head = format_ts(row["ts"]) + (" " + ids if ids else "")
    level = LEVEL_NAMES.get(row["level"]) if row["level"] is not None else None
    return f"{head} {level} - " if level else f"{head} "


def stream_lines(conn: sqlite3.Connection, req: ViewRequest) -> Iterator[str]:
    """Original text of the records (header, first line and continuation lines), one after the other."""
    parts: list[str] = []
    size = 0
    for rows in _walk(conn, req.filter, [], req.max_rows):
        for start in range(0, len(rows), LINES_CHUNK):
            group = rows[start : start + LINES_CHUNK]
            marks = ",".join("?" * len(group))
            texts = {
                r[0]: r[1:]
                for r in conn.execute(
                    "SELECT e.id, substr(e.msg, 1, ?), length(e.msg),"  # noqa: S608
                    " substr(b.cont, 1, max(? - min(coalesce(length(e.msg), 0), ?), 0)), length(b.cont)"
                    f" FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id WHERE e.id IN ({marks})",
                    (MAX_RECORD_CHARS, MAX_RECORD_CHARS, MAX_RECORD_CHARS, *[r["id"] for r in group]),
                )
            }
            for row in group:
                msg, msg_len, cont, cont_len = texts.get(row["id"], (row["msg"], None, None, None))
                text = _record_head(row) + (msg or "")
                if cont:
                    text += "\n" + cont
                # one budget for message + continuation; what the query left out is counted for the notice
                omitted = max((msg_len or 0) + (cont_len or 0) - len(msg or "") - len(cont or ""), 0)
                if omitted:
                    text += "\n" + messages.VIEWEXPORT_CUT_NOTICE.format(count=omitted)
                parts.append(text + "\n")
                size += len(text) + 1
            done, parts, size = _flush(parts, size)
            if done:
                yield done
    if parts:
        yield "".join(parts)


def csv_header(columns: Sequence[str]) -> list[str]:
    return [messages.VIEWEXPORT_CSV_HEADERS["id"]] + [
        messages.VIEWEXPORT_CSV_HEADERS.get(c, c[len("fields.") :]) for c in columns
    ]


def _with_bom(bom: bool, chunks: Iterator[str]) -> Iterator[str]:
    if bom:
        yield BOM
    yield from chunks


def stream_view_csv(conn: sqlite3.Connection, req: ViewRequest) -> Iterator[str]:
    """The visible columns (and extracted fields) over the whole filter, not only the loaded rows."""

    def body() -> Iterator[str]:
        yield csv_row(csv_header(req.columns))
        sources = _source_names(conn) if "source" in req.columns else {}
        tags = store_collections.group_tags(conn) if "collection" in req.columns else {}
        for rows in _walk(conn, req.filter, _extra_columns(req.columns), req.max_rows):
            yield "".join(
                csv_row([row["id"], *(_cell_text(row, c, sources, tags) for c in req.columns)])
                for row in rows
            )

    return _with_bom(req.bom, body())


def stream_events_csv(
    conn: sqlite3.Connection, evflt: store_events.EventFilter, req: ViewRequest
) -> Iterator[str]:
    """Events (operations by default) of the filter: duration, status, keys, time, source, collection."""

    def body() -> Iterator[str]:
        yield csv_row(messages.VIEWEXPORT_EVENT_HEADERS)
        tags = store_collections.group_tags(conn)
        after = None
        remaining = req.max_rows
        while remaining > 0:
            rows, nxt = store_events.list_events(conn, evflt, min(store_events.MAX_LIMIT, remaining), after)
            if not rows:
                return
            ids = [r["start_entry_id"] for r in rows if r["start_entry_id"] is not None]
            files: dict[int, tuple[str, int | None]] = {}
            if ids:
                marks = ",".join("?" * len(ids))
                files = {
                    i: (n, g)
                    for i, n, g in conn.execute(
                        "SELECT e.id, s.file_name, st.collection_group FROM entry e"  # noqa: S608
                        " JOIN source s ON s.id = e.source_id LEFT JOIN stream st ON st.id = s.stream_id"
                        f" WHERE e.id IN ({marks})",
                        ids,
                    )
                }
            out = []
            for ev in rows:
                detail = ev["detail"] if isinstance(ev["detail"], dict) else {}
                op_key = detail.get("op_key")
                file_name, group = files.get(ev["start_entry_id"], (None, None))
                out.append(
                    csv_row(
                        [
                            ev["id"],
                            ev["name"],
                            ev["kind"],
                            ev["status"],
                            ev["duration_ms"],
                            format_ts(ev["ts_start"]),
                            format_ts(ev["ts_end"]),
                            ev["key"],
                            op_key if op_key != ev["key"] else None,
                            None if ev["tid"] is None else f"0x{ev['tid']:x}",
                            ev["family"],
                            ev["process"],
                            file_name,
                            None if group is None else tags.get(group, str(group)),
                            ev["n_entries"],
                        ]
                    )  # fmt: skip
                )
            yield "".join(out)
            remaining -= len(rows)
            if nxt is None:
                return
            after = store_events.Cursor.parse(nxt)

    return _with_bom(req.bom, body())


def stream_view(
    conn: sqlite3.Connection, req: ViewRequest, evflt: store_events.EventFilter | None = None
) -> Iterator[str]:
    handlers: dict[str, Callable[[], Iterator[str]]] = {
        "values": lambda: stream_values(conn, req),
        "lines": lambda: stream_lines(conn, req),
        "view_csv": lambda: stream_view_csv(conn, req),
        "events_csv": lambda: stream_events_csv(conn, evflt or store_events.EventFilter(), req),
    }
    return handlers[req.kind]()
