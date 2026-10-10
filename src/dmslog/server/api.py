"""JSON API: cases, collections (background ingest), progress, sources and entry queries.

The router is a plain table of (method, regex, handler). Handlers receive an ``Request`` and
return a ``Response`` (or a ``Stream`` for Server-Sent Events); errors are ``ApiError``.
Nothing coming from the client is used as a file system path except the collection ``path``,
which is only handed to the ingest as its input; case ids are validated strictly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .. import __version__, messages
from ..ingest import pipeline
from ..ingest.discover import SourceNotFoundError, UnsafeArchiveError
from ..ingest.ers import (
    KIND_ERS_ZIP,
    KIND_FILE,
    KIND_FOLDER,
    CollectionConflictError,
    ErsIdentity,
    check_can_add,
    parse_ers_name,
)
from ..ingest.reader import ReadLimitError
from ..knowledge.installation import read_installation
from ..knowledge.registry import RegistryError
from ..knowledge.resolve import Knowledge
from ..store import bookmarks, db, events, keys, payloads, query, views
from ..store import config as config_store
from ..store.filters import QueryInputError, compile_filter
from ..store.writer import IndexWriter
from .sse import ProgressHub

MAX_BODY_BYTES = 256 * 1024
# Bookmarks import takes a whole export: sized from the export cap (bookmarks.MAX_EXPORT_BYTES) plus margin.
MAX_IMPORT_BODY_BYTES = bookmarks.MAX_EXPORT_BYTES + 4 * 1024 * 1024
_BODY_LIMITS = {"/api/bookmarks/import": MAX_IMPORT_BODY_BYTES}


def body_limit(path: str) -> int:
    """Largest request body accepted by a route."""
    return _BODY_LIMITS.get(path, MAX_BODY_BYTES)


MAX_PAGE_LIMIT = 500
DEFAULT_PAGE_LIMIT = 200
MAX_PATH_CHARS = 4096
CASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
                              *(f"lpt{n}" for n in range(1, 10))})  # fmt: skip
SCOPES = (pipeline.SCOPE_DEFAULT, pipeline.SCOPE_ALL)
SCOPE_LEAVES = frozenset({"source", "stream", "family", "ts"})
_ENTRY_ID = re.compile(r"\d{1,18}\Z")
_SMALL_INT = re.compile(r"\d{1,6}\Z")
_VIEW_STATUS = {"filter_too_large": 413, "duplicate_name": 409, "too_many_views": 409, "not_found": 404}
_BOOKMARK_STATUS = {
    "not_found": 404, "entry_not_found": 404, "already_bookmarked": 409, "too_many": 409,
    "import_too_large": 413,
}  # fmt: skip


class ApiError(Exception):
    """An error with an HTTP status, a stable code and an English message."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


@dataclass(slots=True)
class Response:
    status: int
    body: Any = None


@dataclass(slots=True)
class Stream:
    """Marker for an SSE response; the HTTP layer drives ``sse.stream`` with this hub."""

    hub: ProgressHub


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    read_body: Callable[[], dict[str, Any]]
    match: re.Match[str] = field(default=None)  # type: ignore[assignment]


class JobCancelledError(Exception):
    """Raised inside the progress callback to stop an ingest when the server shuts down."""


# --------------------------------------------------------------------------- cases on disk


class CaseStore:
    """Cases live in ``<data-dir>/cases/<id>/case.sqlite``; the id is the folder name."""

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "cases"
        self._create_lock = threading.Lock()

    def validate_id(self, value: Any) -> str:
        if (
            not isinstance(value, str)
            or not CASE_ID_PATTERN.match(value)
            or value[-1] in ".-"
            or value.split(".", 1)[0].lower() in WINDOWS_RESERVED
        ):
            raise ApiError(400, "invalid_case_id", messages.API_INVALID_CASE_ID)
        return value

    def case_dir(self, case_id: str) -> Path:
        return self.root / self.validate_id(case_id)

    def db_path(self, case_id: str) -> Path:
        return self.case_dir(case_id) / pipeline.CASE_DB_FILENAME

    def canonical_case_id(self, raw: Any) -> str:
        """Resolve a received id to the real folder name (case-insensitive match).

        Every job, SSE hub, delete and database access keys on this value, so ``Demo`` and
        ``demo`` can never be treated as two cases on case-insensitive filesystems.
        """
        cid = self.validate_id(raw)
        matches = [name for name in self.ids() if name.casefold() == cid.casefold()]
        if not matches:
            raise ApiError(404, "case_not_found", messages.API_CASE_NOT_FOUND.format(case=cid))
        if len(matches) > 1:
            raise ApiError(409, "case_ambiguous", messages.API_CASE_AMBIGUOUS.format(case=cid))
        return matches[0]

    def require(self, case_id: Any) -> str:
        """Validate the id and make sure the case exists; returns the canonical id."""
        return self.canonical_case_id(case_id)

    @contextmanager
    def reader(self, case_id: str) -> Iterator[sqlite3.Connection]:
        """A read-only connection owned by the calling thread."""
        cid = self.require(case_id)
        try:
            conn = db.connect(self.db_path(cid), readonly=True)
        except sqlite3.Error as exc:
            raise ApiError(500, "database_error", messages.API_DATABASE_ERROR) from exc
        with closing(conn):
            yield conn

    def ids(self) -> list[str]:
        try:
            names = sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return []
        valid = []
        for name in names:
            try:
                self.validate_id(name)
            except ApiError:
                continue
            if self.db_path(name).is_file():
                valid.append(name)
        return valid

    def create(self, name: Any) -> str:
        cid = self.validate_id(name)
        with self._create_lock:
            existing = {n.lower() for n in self.ids()}
            if cid.lower() in existing:
                raise ApiError(409, "case_exists", messages.API_CASE_EXISTS.format(case=cid))
            try:
                conn = db.create_case_db(self.db_path(cid))
                try:
                    IndexWriter(conn).init_meta(cid, __version__)
                finally:
                    conn.close()
            except (OSError, sqlite3.Error) as exc:
                shutil.rmtree(self.root / cid, ignore_errors=True)
                raise ApiError(
                    500, "case_create_failed", messages.API_CASE_CREATE_FAILED.format(case=cid, error=exc)
                ) from exc
        return cid

    def delete(self, case_id: str) -> None:
        cid = self.require(case_id)
        try:
            shutil.rmtree(self.root / cid)
        except OSError as exc:
            raise ApiError(
                409, "case_busy", messages.API_CASE_DELETE_FAILED.format(case=cid, error=exc)
            ) from exc

    def summary(self, case_id: str) -> dict[str, Any]:
        folder = self.root / case_id
        size = 0
        for dirpath, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    size += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
        info: dict[str, Any] = {
            "id": case_id,
            "name": case_id,
            "size_bytes": size,
            "sources": 0,
            "records": 0,
        }
        try:
            with self.reader(case_id) as conn:
                row = conn.execute("SELECT value FROM meta WHERE key='name'").fetchone()
                if row and row[0]:
                    info["name"] = row[0]
                n_sources, n_records = conn.execute(
                    "SELECT count(*), coalesce(sum(n_entries), 0) FROM source"
                ).fetchone()
                info["sources"], info["records"] = n_sources, n_records
        except (ApiError, sqlite3.Error):
            pass
        return info


# --------------------------------------------------------------------------- ingest jobs


class IngestJobs:
    """One background ingest per case; progress is published to the case's ``ProgressHub``."""

    def __init__(self, store: CaseStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._hubs: dict[str, ProgressHub] = {}
        self._threads: set[threading.Thread] = set()
        self.stopping = threading.Event()

    def hub(self, case_id: str) -> ProgressHub:
        case_id = self._store.canonical_case_id(case_id)
        with self._lock:
            return self._hubs.setdefault(case_id, ProgressHub())

    def is_running(self, case_id: str) -> bool:
        try:
            case_id = self._store.canonical_case_id(case_id)
        except ApiError:
            return False
        with self._lock:
            hub = self._hubs.get(case_id)
        return hub is not None and hub.running

    def start(self, case_id: str, path: Path, scope: str) -> str:
        case_id = self._store.canonical_case_id(case_id)
        hub = self.hub(case_id)
        job_id = uuid.uuid4().hex[:12]
        if self.stopping.is_set() or not hub.try_begin(job_id):
            raise ApiError(409, "job_running", messages.API_CASE_BUSY.format(case=case_id))
        thread = threading.Thread(
            target=self._run, args=(case_id, job_id, path, scope, hub), name=f"ingest-{case_id}", daemon=True
        )
        with self._lock:
            self._threads.add(thread)
        thread.start()
        return job_id

    def shutdown(self, timeout: float = 10.0) -> None:
        """Ask running jobs to stop at the next progress callback and wait for them."""
        self.stopping.set()
        with self._lock:
            threads = list(self._threads)
            hubs = list(self._hubs.values())
        for hub in hubs:
            hub.wake_all()
        for thread in threads:
            thread.join(timeout)

    def _run(self, case_id: str, job_id: str, path: Path, scope: str, hub: ProgressHub) -> None:
        conn: sqlite3.Connection | None = None
        done: dict[str, Any] = {"job_id": job_id, "status": "error", "message": ""}

        def progress(event: pipeline.ProgressEvent) -> None:
            if self.stopping.is_set():
                raise JobCancelledError
            hub.publish(
                "progress",
                {
                    "job_id": job_id,
                    "source": event.member,
                    "bytes_done": event.bytes_done,
                    "bytes_total": event.bytes_total,
                    "records": event.records,
                    "status": event.phase,
                },
                coalesce=event.phase == "progress",
            )

        try:
            conn = db.create_case_db(self._store.db_path(case_id))
            options = pipeline.IngestOptions(scope=scope, case_name=case_id)
            result = pipeline.run_ingest(conn, path, options, progress)
            done["records"] = result.records
            done["failed"] = result.sources_failed
            if result.sources_failed:
                done["message"] = messages.JOB_DONE_WITH_FAILURES.format(
                    records=result.records, failed=result.sources_failed, sources=result.sources_selected
                )
            else:
                done["status"] = "ok"
                done["message"] = messages.JOB_DONE_OK.format(
                    records=result.records, sources=result.sources_selected
                )
        except JobCancelledError:
            done.update(code="cancelled", message=messages.JOB_CANCELLED)
        except CollectionConflictError as exc:
            done.update(code=exc.reason, message=str(exc))
        except UnsafeArchiveError as exc:
            done.update(code="unsafe_archive", message=messages.JOB_UNSAFE.format(error=exc))
        except (SourceNotFoundError, ReadLimitError, OSError, ValueError, sqlite3.Error) as exc:
            done.update(code="ingest_failed", message=messages.JOB_FAILED.format(error=exc))
        except Exception as exc:  # the thread must always publish a final event
            done.update(code="internal_error", message=messages.JOB_FAILED.format(error=type(exc).__name__))
        finally:
            if conn is not None:
                conn.close()
            hub.finish(done)
            with self._lock:
                self._threads.discard(threading.current_thread())


# --------------------------------------------------------------------------- helpers


def _identity_of_row(kind: str, guid: str | None, stamp: str | None, variant: str | None, label: str | None,
                     path: str) -> ErsIdentity | None:  # fmt: skip
    if kind != KIND_ERS_ZIP or not guid or not stamp:
        return None
    return ErsIdentity(guid, stamp, variant or "enterprise", "Enterprise", label or "", Path(path).name)


def precheck_conflict(conn: sqlite3.Connection, path: Path) -> None:
    """Raise CollectionConflictError synchronously (the ingest repeats the same rule)."""
    rows = conn.execute(
        "SELECT kind, path, ers_guid, ers_ts, ers_variant, label FROM collection ORDER BY id"
    ).fetchall()
    if not rows:
        return
    if path.is_dir():
        kind = KIND_FOLDER
    elif path.is_file() and zipfile.is_zipfile(path):
        kind = KIND_ERS_ZIP
    else:
        kind = KIND_FILE
    ident = parse_ers_name(path.name) if kind == KIND_ERS_ZIP else None
    resolved = str(path.resolve())
    for r_kind, r_path, guid, stamp, variant, _label in rows:
        if r_path == resolved and r_kind == kind:
            return
        if ident is not None and (guid, stamp, variant) == (ident.guid, ident.timestamp, ident.variant):
            return
    check_can_add([_identity_of_row(r[0], r[2], r[3], r[4], r[5], r[1]) for r in rows], ident)


def _query_int(request: Request, name: str, default: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    if not _SMALL_INT.match(raw):
        raise ApiError(400, "invalid_field", messages.API_BAD_INT.format(field=name))
    return int(raw)


_FAMILY = re.compile(r"[A-Za-z0-9_]{1,32}\Z")
_PRODUCT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_TEMPLATE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_ERROR_CODE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}\Z")


def _family_param(request: Request) -> str | None:
    raw = request.query.get("family")
    if raw is None or raw == "":
        return None
    if not _FAMILY.match(raw):
        raise ApiError(400, "invalid_field", messages.API_BAD_FAMILY)
    return raw


_KEY_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_KEY_VALUE_MAX = 256
_CONT_LINES_MAX = 20000
_INT64 = 2**63
_SIDE_BY_SIDE_LINES = 2000

_EVENT_WORD = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_EVENT_LIST_MAX = 20
_EVENT_TEXT_MAX = 128
_METHOD_MAX = 256


def _printable(value: str) -> bool:
    return value.isprintable()


def _bad(message: str) -> ApiError:
    return ApiError(400, "invalid_field", message)


def _csv_param(request: Request, name: str, word_only: bool, message: str) -> tuple[str, ...]:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return ()
    values = tuple(dict.fromkeys(raw.split(",")))
    if len(values) > _EVENT_LIST_MAX or any(
        not v or len(v) > _EVENT_TEXT_MAX or not _printable(v) or (word_only and not _EVENT_WORD.match(v))
        for v in values
    ):
        raise _bad(message)
    return values


def _id_param(request: Request, name: str) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return None
    if not _ENTRY_ID.match(raw):
        raise _bad(messages.API_BAD_STREAM_ID.format(field=name))
    return int(raw)


def _time_param(request: Request, name: str) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return None
    value = events.parse_log_time(raw)
    if value is None:
        raise _bad(messages.API_BAD_TIME.format(field=name))
    return value


def _event_filter(request: Request, default_kinds: tuple[str, ...] = ()) -> events.EventFilter:
    kinds = _csv_param(request, "kind", True, messages.API_BAD_EVENT_KIND) or default_kinds
    statuses = _csv_param(request, "status", True, messages.API_BAD_EVENT_STATUS)
    names = _csv_param(
        request,
        "name",
        False,
        messages.API_BAD_EVENT_LIST.format(field="name", limit=_EVENT_LIST_MAX),
    )
    key_query = request.query.get("q")
    if (
        key_query is not None
        and key_query != ""
        and (len(key_query) > _EVENT_TEXT_MAX or not _printable(key_query))
    ):
        raise _bad(messages.API_BAD_EVENT_QUERY)
    return events.EventFilter(
        kinds=kinds,
        names=names,
        statuses=statuses,
        stream_id=_id_param(request, "stream"),
        source_id=_id_param(request, "source"),
        key_query=key_query or None,
        ts_from=_time_param(request, "from"),
        ts_to=_time_param(request, "to"),
    )


def _cont_lines_param(request: Request) -> int | None:
    raw = request.query.get("cont_lines")
    if raw is None or raw == "":
        return None
    if not _ENTRY_ID.match(raw) or not 1 <= int(raw) <= _CONT_LINES_MAX:
        raise _bad(messages.API_BAD_CONT_LINES.format(limit=_CONT_LINES_MAX))
    return int(raw)


def _key_type_param(raw: str) -> str:
    if not _KEY_TYPE.match(raw):
        raise _bad(messages.API_BAD_KEY_TYPE)
    return raw


def _key_value_param(raw: str) -> str:
    """Percent-decode a key value taken from the path; strict UTF-8, bounded, printable."""
    bad = _bad(messages.API_BAD_KEY_VALUE.format(limit=_KEY_VALUE_MAX))
    if len(raw) > _KEY_VALUE_MAX * 9:
        raise bad
    try:
        value = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise bad from None
    if not value or len(value) > _KEY_VALUE_MAX or not value.isprintable():
        raise bad
    return value


def _bookmark_extra(exc: bookmarks.BookmarkError) -> dict[str, Any]:
    return {"bookmark_id": exc.params["bookmark_id"]} if exc.code == "already_bookmarked" else {}


def _int_field(body: dict[str, Any], name: str) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < _INT64:
        raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field=name))
    return value


def _case_from_body(store: CaseStore, body: dict[str, Any]) -> str:
    return store.require(body.get("case"))


def _case_from_query(store: CaseStore, request: Request) -> str:
    return store.require(request.query.get("case"))


def _has_scope(tree: Any) -> bool:
    """True when a source/time leaf is part of the top-level conjunction of the filter."""
    if not isinstance(tree, dict) or len(tree) != 1:
        return False
    ((op, arg),) = tree.items()
    if op in SCOPE_LEAVES:
        return True
    return op == "and" and isinstance(arg, list) and any(_has_scope(child) for child in arg)


def _check_scan_filter(flt: Any) -> None:
    """Substring/regex scan rows: ARQUITETURA 9.4 requires a source or time condition with them."""
    compiled = compile_filter(flt)  # raises FilterError on invalid trees
    if (compiled.uses_substr or getattr(compiled, "uses_regex", False)) and not _has_scope(flt):
        raise ApiError(400, "substr_needs_scope", messages.API_SUBSTR_NEEDS_SCOPE)


def _page_args(body: dict[str, Any]) -> dict[str, Any]:
    flt = body.get("filter")
    if flt is not None and not isinstance(flt, dict):
        raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="filter"))
    cursor = body.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="cursor"))
    columns = body.get("columns")
    if columns is not None and not isinstance(columns, list):
        raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="columns"))
    limit = body.get("limit", DEFAULT_PAGE_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_LIMIT:
        raise ApiError(400, "bad_limit", messages.API_BAD_LIMIT.format(limit=MAX_PAGE_LIMIT))
    direction = body.get("dir", "fwd")
    if direction not in ("fwd", "back"):
        raise query.ColumnError("bad_direction", "$.dir")
    return {"filter": flt, "cursor": cursor, "dir": direction, "limit": limit, "columns": columns}


ENTRY_FIELDS = (
    "id",
    "source_id",
    "ts",
    "pid",
    "tid",
    "level",
    "sev",
    "msg",
    "nlines",
    "flags",
    "line_no",
    "tpl_id",
)


def _entry_json(entry: dict[str, Any]) -> dict[str, Any]:
    out = {k: entry.get(k) for k in ENTRY_FIELDS}
    cont = entry.get("cont")
    out["text"] = entry["msg"] + ("\n" + cont if cont else "")
    out["tpl"] = entry.get("tpl_code")
    out["tpl_title"] = entry.get("tpl_title")
    out["member"] = entry.get("member")
    out["file_name"] = entry.get("file_name")
    out["envelope"] = entry.get("envelope")
    out["fields"] = entry.get("fields") or {}
    out["keys"] = entry.get("keys") or []
    out["event"] = entry.get("event")
    out["links"] = entry.get("links") or []
    return out


def _pretty(text: str, fmt: str, truncated_display: bool) -> dict[str, Any]:
    """Format a payload for display; any failure of the formatter is reported, never raised."""
    try:
        from ..payload.pretty import format_payload

        res = format_payload(text, fmt)
        return {
            "text": res.text,
            "ok": bool(res.ok),
            "kind": res.kind,
            "reason": res.reason,
            "truncated_display": bool(res.truncated_display) or truncated_display,
        }
    except Exception:
        return {
            "text": text,
            "ok": False,
            "kind": fmt,
            "reason": "format_error",
            "truncated_display": truncated_display,
        }


# --------------------------------------------------------------------------- the API object


class Api:
    """Routes and state (cases on disk, ingest jobs) of one server."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.store = CaseStore(self.data_dir)
        self.jobs = IngestJobs(self.store)
        self._knowledge_lock = threading.Lock()
        self._pack_knowledge: Knowledge | None = None
        self._case_knowledge: dict[str, tuple[Any, Knowledge]] = {}
        self._routes: list[tuple[str, re.Pattern[str], Callable[[Request], Response | Stream]]] = [
            ("GET", re.compile(r"/api/cases\Z"), self.list_cases),
            ("POST", re.compile(r"/api/cases\Z"), self.create_case),
            ("DELETE", re.compile(r"/api/cases/(?P<case>[^/]+)\Z"), self.delete_case),
            ("POST", re.compile(r"/api/cases/(?P<case>[^/]+)/collections\Z"), self.add_collection),
            ("GET", re.compile(r"/api/cases/(?P<case>[^/]+)/progress\Z"), self.progress),
            ("GET", re.compile(r"/api/sources\Z"), self.list_sources),
            ("GET", re.compile(r"/api/templates\Z"), self.list_templates),
            ("GET", re.compile(r"/api/unknown\Z"), self.list_unknown),
            ("GET", re.compile(r"/api/config\Z"), self.get_config),
            ("GET", re.compile(r"/api/knowledge\Z"), self.get_knowledge),
            ("GET", re.compile(r"/api/knowledge/error\Z"), self.get_knowledge_error),
            ("POST", re.compile(r"/api/entries/page\Z"), self.entries_page),
            ("POST", re.compile(r"/api/entries/count\Z"), self.entries_count),
            ("POST", re.compile(r"/api/histogram\Z"), self.histogram),
            ("GET", re.compile(r"/api/views\Z"), self.list_views),
            ("POST", re.compile(r"/api/views\Z"), self.create_view),
            ("DELETE", re.compile(r"/api/views/(?P<id>[^/]+)\Z"), self.delete_view),
            ("PUT", re.compile(r"/api/views/(?P<id>[^/]+)\Z"), self.update_view),
            ("GET", re.compile(r"/api/bookmarks\Z"), self.list_bookmarks),
            ("POST", re.compile(r"/api/bookmarks\Z"), self.create_bookmark),
            ("POST", re.compile(r"/api/bookmarks/lookup\Z"), self.lookup_bookmarks),
            ("GET", re.compile(r"/api/bookmarks/export\Z"), self.export_bookmarks),
            ("POST", re.compile(r"/api/bookmarks/import\Z"), self.import_bookmarks),
            ("GET", re.compile(r"/api/bookmarks/(?P<id>[^/]+)\Z"), self.get_bookmark),
            ("PATCH", re.compile(r"/api/bookmarks/(?P<id>[^/]+)\Z"), self.update_bookmark),
            ("DELETE", re.compile(r"/api/bookmarks/(?P<id>[^/]+)\Z"), self.delete_bookmark),
            ("GET", re.compile(r"/api/compare\Z"), self.compare),
            ("GET", re.compile(r"/api/events\Z"), self.list_events),
            ("GET", re.compile(r"/api/events/stats\Z"), self.events_stats),
            ("GET", re.compile(r"/api/events/histogram\Z"), self.events_histogram),
            ("GET", re.compile(r"/api/events/(?P<id>[^/]+)\Z"), self.get_event),
            ("GET", re.compile(r"/api/stats/series\Z"), self.stats_series),
            ("GET", re.compile(r"/api/stats/methods\Z"), self.stats_methods),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)\Z"), self.get_entry),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)/context\Z"), self.entry_context),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)/links\Z"), self.entry_links),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)/side-by-side\Z"), self.entry_side_by_side),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)/payloads\Z"), self.entry_payloads),
            ("GET", re.compile(r"/api/payloads/page\Z"), self.payloads_page),
            ("GET", re.compile(r"/api/payloads/(?P<id>[^/]+)\Z"), self.get_payload),
            ("GET", re.compile(r"/api/keys/types\Z"), self.key_types),
            ("GET", re.compile(r"/api/keys/(?P<type>[^/]+)/(?P<value>[^/]+)\Z"), self.key_records),
        ]

    def shutdown(self) -> None:
        self.jobs.shutdown()

    # -- dispatch

    def dispatch(self, method: str, path: str, query_params: dict[str, str],
                 read_body: Callable[[], dict[str, Any]]) -> Response | Stream:  # fmt: skip
        try:
            allowed = False
            for route_method, pattern, handler in self._routes:
                match = pattern.match(path)
                if match is None:
                    continue
                allowed = True
                if route_method == method:
                    return handler(Request(method, path, query_params, read_body, match))
            if allowed:
                raise ApiError(405, "method_not_allowed", messages.API_METHOD_NOT_ALLOWED)
            raise ApiError(404, "not_found", messages.API_NOT_FOUND)
        except ApiError as exc:
            return Response(exc.status, exc.body())
        except views.ViewError as exc:
            status = _VIEW_STATUS.get(exc.code, 400)
            return Response(status, {"error": exc.code, "message": str(exc)})
        except bookmarks.BookmarkError as exc:
            status = _BOOKMARK_STATUS.get(exc.code, 400)
            return Response(status, {"error": exc.code, "message": str(exc), **_bookmark_extra(exc)})
        except QueryInputError as exc:
            return Response(400, {"error": exc.code, "message": str(exc), "path": exc.path})
        except query.QueryTimeout:
            return Response(408, {"error": "query_timeout", "message": messages.API_QUERY_TIMEOUT})
        except sqlite3.Error:
            return Response(500, {"error": "database_error", "message": messages.API_DATABASE_ERROR})
        except Exception:
            return Response(500, {"error": "internal_error", "message": messages.API_INTERNAL_ERROR})

    # -- cases

    def list_cases(self, request: Request) -> Response:
        cases = [self.store.summary(cid) for cid in self.store.ids()]
        for info in cases:
            info["ingesting"] = self.jobs.is_running(info["id"])
        return Response(200, {"cases": cases})

    def create_case(self, request: Request) -> Response:
        body = request.read_body()
        cid = self.store.create(body.get("name"))
        return Response(201, {"id": cid, "name": cid})

    def delete_case(self, request: Request) -> Response:
        cid = self.store.require(request.match["case"])
        if self.jobs.is_running(cid):
            raise ApiError(409, "job_running", messages.API_CASE_BUSY.format(case=cid))
        self.store.delete(cid)
        return Response(204)

    def add_collection(self, request: Request) -> Response:
        cid = self.store.require(request.match["case"])
        body = request.read_body()
        raw_path = body.get("path")
        if (
            not isinstance(raw_path, str)
            or not raw_path.strip()
            or len(raw_path) > MAX_PATH_CHARS
            or "\x00" in raw_path
        ):
            raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="path"))
        scope = body.get("scope", pipeline.SCOPE_DEFAULT)
        if scope not in SCOPES:
            raise ApiError(400, "bad_scope", messages.API_BAD_SCOPE)
        path = Path(raw_path.strip()).expanduser()
        if not path.is_absolute():
            raise ApiError(400, "path_not_absolute", messages.API_PATH_NOT_ABSOLUTE)
        if not path.exists():
            raise ApiError(400, "path_not_found", messages.API_PATH_NOT_FOUND.format(path=path))
        if self.jobs.is_running(cid):
            raise ApiError(409, "job_running", messages.API_CASE_BUSY.format(case=cid))
        try:
            with self.store.reader(cid) as conn:
                precheck_conflict(conn, path)
        except CollectionConflictError as exc:
            raise ApiError(409, exc.reason, str(exc)) from exc
        except (OSError, UnsafeArchiveError) as exc:
            raise ApiError(400, "path_unreadable", messages.JOB_FAILED.format(error=exc)) from exc
        job_id = self.jobs.start(cid, path, scope)
        return Response(202, {"job_id": job_id})

    def progress(self, request: Request) -> Stream:
        cid = self.store.require(request.match["case"])
        return Stream(self.jobs.hub(cid))

    # -- sources

    def list_sources(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            rows = conn.execute(
                "SELECT s.id, s.file_name, s.member, t.family, t.process, t.pid, t.sensitive, s.rotation,"
                " s.status, s.error, s.n_entries, s.first_ts, s.last_ts, s.size,"
                " (SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id"
                "  ORDER BY e.id DESC LIMIT 1)"
                " FROM source s LEFT JOIN stream t ON t.id = s.stream_id ORDER BY s.id"
            ).fetchall()
        sources = [
            {
                "id": r[0], "name": r[1], "member": r[2], "family": r[3], "process": r[4], "pid": r[5],
                "sensitive": bool(r[6]), "rotation": r[7], "status": r[8], "error": r[9],
                "records": r[10], "lines": r[14] or 0, "ts_min": r[11], "ts_max": r[12], "size": r[13],
            }
            for r in rows
        ]  # fmt: skip
        return Response(200, {"sources": sources})

    # -- templates and unknown signatures

    def list_templates(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        family = _family_param(request)
        with self.store.reader(cid) as conn:
            templates = query.list_templates(conn, family)
        return Response(200, {"case": cid, "family": family, "templates": templates})

    def list_unknown(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        family = _family_param(request)
        limit = _query_int(request, "limit", DEFAULT_PAGE_LIMIT)
        if not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ApiError(400, "bad_limit", messages.API_BAD_LIMIT.format(limit=MAX_PAGE_LIMIT))
        with self.store.reader(cid) as conn:
            rows = query.unknown_signatures(conn, family, limit)
            totals = query.unknown_totals(conn, family)
        return Response(200, {"case": cid, "family": family, "unknown": rows, **totals})

    # -- installation configuration

    def get_config(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        product = request.query.get("product")
        group = request.query.get("group")
        if group is not None and not _SMALL_INT.match(group):
            raise ApiError(400, "invalid_field", messages.API_BAD_INT.format(field="group"))
        if product is not None and not _PRODUCT.match(product):
            raise ApiError(400, "invalid_field", messages.API_BAD_PRODUCT)
        with self.store.reader(cid) as conn:
            if product is None:
                return Response(200, {"case": cid, "products": config_store.config_products(conn)})
            result = config_store.config_for(conn, product, int(group) if group is not None else None)
        if result is None:
            raise ApiError(404, "config_not_found", messages.API_CONFIG_NOT_FOUND.format(product=product))
        return Response(200, {"case": cid, **result})

    # -- knowledge (vault pack + installation configuration)

    def _knowledge(self, conn: sqlite3.Connection, cid: str, with_ers: bool) -> Knowledge:
        """``Knowledge`` over the pack and, when ``with_ers``, the ERS of the case (read once per version)."""
        if not with_ers:
            with self._knowledge_lock:
                if self._pack_knowledge is None:
                    self._pack_knowledge = Knowledge()
                return self._pack_knowledge
        paths = [Path(r[0]) for r in conn.execute("SELECT path FROM collection ORDER BY id")]
        stamps = []
        for path in paths:
            try:
                stamps.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                stamps.append((str(path), 0))
        row = conn.execute("SELECT value FROM meta WHERE key = 'ingest_generation'").fetchone()
        key = (cid, row[0] if row else None, tuple(stamps))
        with self._knowledge_lock:
            cached = self._case_knowledge.get(cid)
            if cached is not None and cached[0] == key:
                return cached[1]
        installations: dict[int, Any] = {}
        for path in paths:
            try:
                installation = read_installation(path)
            except (RegistryError, OSError, zipfile.BadZipFile):
                continue
            if installation.products:
                installations[len(installations)] = installation
        knowledge = Knowledge(installations=installations)
        with self._knowledge_lock:
            self._case_knowledge[cid] = (key, knowledge)
        return knowledge

    def get_knowledge(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        code = request.query.get("template")
        if code is None or not _TEMPLATE_CODE.match(code):
            raise ApiError(400, "invalid_field", messages.API_BAD_TEMPLATE)
        with self.store.reader(cid) as conn:
            row = conn.execute("SELECT family, title FROM template WHERE code = ?", (code,)).fetchone()
            if row is None:
                message = messages.API_TEMPLATE_NOT_FOUND.format(template=code)
                raise ApiError(404, "template_not_found", message)
            knowledge = self._knowledge(conn, cid, with_ers=False)
        family, title = row
        hint = knowledge.hint(code)
        adapter = knowledge.pack.adapter(family)
        params = []
        for name in hint.params if hint else ():
            found = knowledge.param(0, family, name, scope="vault")
            params.append({
                "name": name,
                "default": found.default if found else None,
                "description": found.description if found else None,
                "log_relevant": found.log_relevant if found else None,
            })  # fmt: skip
        return Response(200, {
            "case": cid,
            "template": code,
            "family": family,
            "title": title,
            "hint": None if hint is None else {
                "text": hint.text, "params": list(hint.params), "note": hint.note,
            },
            "params": params,
            "adapter": None if adapter is None else {
                "acronym": adapter.acronym,
                "title": adapter.title,
                "adapter": adapter.adapter,
                "type": adapter.type_label or adapter.type,
                "operations": list(adapter.operations),
                "document_title": adapter.document_title,
                "document_version": adapter.document_version,
            },
            "vault_note": hint.note if hint and hint.note else knowledge.vault_note(family),
        })  # fmt: skip

    def get_knowledge_error(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        family = _family_param(request)
        if family is None:
            raise ApiError(400, "invalid_field", messages.API_BAD_FAMILY)
        code = request.query.get("code")
        if code is None or not _ERROR_CODE.match(code):
            raise ApiError(400, "invalid_field", messages.API_BAD_ERROR_CODE)
        operation = request.query.get("operation")
        if operation is not None and not _PRODUCT.match(operation):
            raise ApiError(400, "invalid_field", messages.API_BAD_OPERATION)
        with self.store.reader(cid) as conn:
            knowledge = self._knowledge(conn, cid, with_ers=True)
        info = knowledge.error(family, code, operation)
        if info is None:
            message = messages.API_ERROR_NOT_FOUND.format(code=code, family=family)
            raise ApiError(404, "error_not_found", message)
        return Response(200, {
            "case": cid,
            "family": info.family,
            "code": info.code,
            "operation": info.operation,
            "severity": info.severity,
            "adms_severity": info.adms_severity,
            "reason": info.reason,
            "details": info.details,
            "source": info.source,
            "out_of_vault": info.out_of_vault,
            "config_path": info.config_path,
        })  # fmt: skip

    # -- entries

    def entries_page(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        args = _page_args(body)
        _check_scan_filter(args["filter"])
        with self.store.reader(cid) as conn:
            page = query.page(
                conn, args["filter"], args["cursor"], args["dir"], args["limit"], args["columns"]
            )
        has_rows = bool(page.rows)
        forward = args["dir"] == "fwd"
        has_cursor = args["cursor"] not in (None, "")
        nxt = has_rows and ((forward and page.has_more) or (not forward and has_cursor))
        prev = has_rows and ((not forward and page.has_more) or (forward and has_cursor))
        return Response(
            200,
            {
                "rows": page.rows,
                "next": str(page.last) if nxt else None,
                "prev": str(page.first) if prev else None,
            },
        )

    def entries_count(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        flt = body.get("filter")
        if flt is not None and not isinstance(flt, dict):
            raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="filter"))
        _check_scan_filter(flt)
        with self.store.reader(cid) as conn:
            result = query.count(conn, flt)
        return Response(200, {"count": result.n, "exact": result.exact})

    def histogram(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        flt = body.get("filter")
        if flt is not None and not isinstance(flt, dict):
            raise ApiError(400, "invalid_field", messages.API_MISSING_FIELD.format(field="filter"))
        _check_scan_filter(flt)
        with self.store.reader(cid) as conn:
            result = query.histogram(conn, flt, body.get("bucket_ms"), body.get("split", "sev"))
        return Response(
            200,
            {
                "bucket_ms": result.bucket_ms,
                "start": result.start,
                "n_buckets": result.n_buckets,
                "split": result.split,
                "series": result.series,
                "total": result.total,
                "exact": result.exact,
                "method": result.method,
            },
        )

    @staticmethod
    def _entry_id(request: Request) -> int:
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise ApiError(400, "invalid_entry_id", messages.API_BAD_ENTRY_ID)
        return int(raw)

    def get_entry(self, request: Request) -> Response:
        entry_id = self._entry_id(request)
        cid = _case_from_query(self.store, request)
        max_lines = _cont_lines_param(request)
        with self.store.reader(cid) as conn:
            entry = query.get_entry(conn, entry_id, max_cont_lines=max_lines)
            n_payloads = payloads.payload_count(conn, entry_id) if entry is not None else 0
        if entry is None:
            raise ApiError(
                404, "entry_not_found", messages.API_ENTRY_NOT_FOUND.format(entry_id=entry_id, case=cid)
            )
        out = _entry_json(entry)
        out["payload_count"] = n_payloads
        if max_lines is not None:
            out["cont_truncated"] = bool(entry.get("cont_truncated"))
        return Response(200, out)

    def entry_context(self, request: Request) -> Response:
        entry_id = self._entry_id(request)
        cid = _case_from_query(self.store, request)
        before = _query_int(request, "before", 50)
        after = _query_int(request, "after", 50)
        same = request.query.get("same", "source")
        with self.store.reader(cid) as conn:
            ctx = query.context(conn, entry_id, before, after, same)
        if ctx is None:
            raise ApiError(
                404, "entry_not_found", messages.API_ENTRY_NOT_FOUND.format(entry_id=entry_id, case=cid)
            )
        return Response(200, {"rows": ctx.rows, "anchor": ctx.anchor, "same": ctx.same})

    # -- derived events and statistics

    def list_events(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        flt = _event_filter(request)
        raw_limit = request.query.get("limit")
        limit = events.DEFAULT_LIMIT
        if raw_limit is not None:
            if not _SMALL_INT.match(raw_limit) or not 1 <= int(raw_limit) <= events.MAX_LIMIT:
                raise _bad(messages.API_BAD_EVENT_LIMIT.format(limit=events.MAX_LIMIT))
            limit = int(raw_limit)
        after = None
        raw_after = request.query.get("after")
        if raw_after:
            after = events.Cursor.parse(raw_after)
            if after is None:
                raise _bad(messages.API_BAD_CURSOR)
        with self.store.reader(cid) as conn:
            rows, nxt = events.list_events(conn, flt, limit, after)
        return Response(200, {"events": rows, "next": nxt})

    def get_event(self, request: Request) -> Response:
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise _bad(messages.API_BAD_EVENT_ID)
        event_id = int(raw)
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            result = events.get_event(conn, event_id)
        if result is None:
            raise ApiError(
                404, "event_not_found", messages.API_EVENT_NOT_FOUND.format(event_id=event_id, case=cid)
            )
        return Response(200, result)

    def events_stats(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        flt = _event_filter(request, default_kinds=("operation",))
        with self.store.reader(cid) as conn:
            result = events.event_stats(conn, flt)
        return Response(200, {"kind": ",".join(flt.kinds), **result})

    def events_histogram(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        flt = _event_filter(request)
        bucket = request.query.get("bucket", "minute")
        if bucket not in events.BUCKET_MS:
            raise _bad(messages.API_BAD_BUCKET)
        with self.store.reader(cid) as conn:
            result = events.event_histogram(conn, flt, bucket)
        return Response(200, result)

    def stats_series(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        side = request.query.get("side") or None
        if side is not None and side not in ("client", "service"):
            raise _bad(messages.API_BAD_STAT_SIDE)
        method = request.query.get("method") or None
        if method is not None and (len(method) > _METHOD_MAX or not _printable(method)):
            raise _bad(messages.API_BAD_STAT_METHOD)
        with self.store.reader(cid) as conn:
            samples, truncated = events.stat_series(
                conn,
                stream_id=_id_param(request, "stream"),
                side=side,
                method=method,
                ts_from=_time_param(request, "from"),
                ts_to=_time_param(request, "to"),
            )
        return Response(200, {"samples": samples, "truncated": truncated})

    def stats_methods(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        stream_id = _id_param(request, "stream")
        with self.store.reader(cid) as conn:
            methods = events.stat_methods(conn, stream_id)
        return Response(200, {"methods": methods})

    # -- correlation keys and links

    def key_types(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            types = keys.key_types(conn)
        return Response(200, {"types": types})

    def key_records(self, request: Request) -> Response:
        key_type = _key_type_param(request.match["type"])
        value = _key_value_param(request.match["value"])
        cid = _case_from_query(self.store, request)
        limit = keys.DEFAULT_LIMIT
        raw_limit = request.query.get("limit")
        if raw_limit is not None:
            if not _SMALL_INT.match(raw_limit) or not 1 <= int(raw_limit) <= keys.MAX_LIMIT:
                raise _bad(messages.API_BAD_KEY_LIMIT.format(limit=keys.MAX_LIMIT))
            limit = int(raw_limit)
        after = None
        raw_after = request.query.get("after")
        if raw_after:
            after = events.Cursor.parse(raw_after)
            if after is None or not (-_INT64 <= after.ts < _INT64 and 0 <= after.id < _INT64):
                raise _bad(messages.API_BAD_CURSOR)
        with self.store.reader(cid) as conn:
            records, nxt = keys.records_with_key(conn, key_type, value, limit, after)
        return Response(200, {"records": records, "next": nxt})

    def _require_entry(self, conn: sqlite3.Connection, entry_id: int, cid: str) -> None:
        if not keys.entry_exists(conn, entry_id):
            raise ApiError(
                404, "entry_not_found", messages.API_ENTRY_NOT_FOUND.format(entry_id=entry_id, case=cid)
            )

    def entry_links(self, request: Request) -> Response:
        entry_id = self._entry_id(request)
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            self._require_entry(conn, entry_id, cid)
            links = keys.links_of(conn, entry_id)
        return Response(200, {"links": links})

    def entry_side_by_side(self, request: Request) -> Response:
        entry_id = self._entry_id(request)
        cid = _case_from_query(self.store, request)
        max_lines = _cont_lines_param(request) or _SIDE_BY_SIDE_LINES
        with self.store.reader(cid) as conn:
            self._require_entry(conn, entry_id, cid)
            other = keys.best_counterpart(conn, entry_id)
            if other is None:
                raise ApiError(404, "no_counterpart", messages.API_NO_COUNTERPART.format(entry_id=entry_id))
            mine = query.get_entry(conn, entry_id, max_cont_lines=max_lines)
            theirs = query.get_entry(conn, other.entry_id, max_cont_lines=max_lines)
            mine_sensitive = keys.sensitivity_of(conn, entry_id)
            their_sensitive = keys.sensitivity_of(conn, other.entry_id)
        if mine is None or theirs is None:  # removed between the statements
            raise ApiError(404, "no_counterpart", messages.API_NO_COUNTERPART.format(entry_id=entry_id))
        left, right = (theirs, mine) if mine_sensitive and not their_sensitive else (mine, theirs)
        sides = {id(mine): mine_sensitive, id(theirs): their_sensitive}
        out = []
        for side in (left, right):
            item = _entry_json(side)
            item["cont_truncated"] = bool(side.get("cont_truncated"))
            item["sensitive"] = sides[id(side)]
            out.append(item)
        return Response(200, {"a": out[0], "b": out[1], "kind": other.kind, "confidence": other.confidence})

    # -- payloads

    def entry_payloads(self, request: Request) -> Response:
        entry_id = self._entry_id(request)
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            self._require_entry(conn, entry_id, cid)
            found = payloads.payloads_of_entry(conn, entry_id)
        return Response(200, {"payloads": found})

    def payloads_page(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)

        def csv(name: str) -> tuple[str, ...]:
            message = messages.API_BAD_PAYLOAD_LIST.format(field=name, limit=_EVENT_LIST_MAX)
            return _csv_param(request, name, False, message)

        orphan = None
        raw_orphan = request.query.get("orphan")
        if raw_orphan:
            if raw_orphan not in ("0", "1"):
                raise _bad(messages.API_BAD_ORPHAN)
            orphan = raw_orphan == "1"
        key_query = request.query.get("q")
        if key_query and (len(key_query) > _EVENT_TEXT_MAX or not _printable(key_query)):
            raise _bad(messages.API_BAD_PAYLOAD_QUERY)
        flt = payloads.PayloadFilter(
            roles=csv("role"),
            directions=csv("direction"),
            peers=csv("peer"),
            operations=csv("operation"),
            formats=csv("fmt"),
            event_id=_id_param(request, "event"),
            orphan=orphan,
            key_query=key_query or None,
        )
        limit = payloads.DEFAULT_LIMIT
        raw_limit = request.query.get("limit")
        if raw_limit is not None:
            if not _SMALL_INT.match(raw_limit) or not 1 <= int(raw_limit) <= payloads.MAX_LIMIT:
                raise _bad(messages.API_BAD_EVENT_LIMIT.format(limit=payloads.MAX_LIMIT))
            limit = int(raw_limit)
        after = None
        raw_after = request.query.get("after")
        if raw_after:
            after = events.Cursor.parse(raw_after)
            if after is None or not (-_INT64 <= after.ts < _INT64 and 0 <= after.id < _INT64):
                raise _bad(messages.API_BAD_CURSOR)
        with self.store.reader(cid) as conn:
            rows, nxt = payloads.list_payloads(conn, flt, limit, after)
        return Response(200, {"payloads": rows, "next": nxt})

    def get_payload(self, request: Request) -> Response:
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise _bad(messages.API_BAD_PAYLOAD_ID)
        payload_id = int(raw)
        cid = _case_from_query(self.store, request)
        fmt = request.query.get("format", "raw")
        if fmt not in ("raw", "pretty"):
            raise _bad(messages.API_BAD_PAYLOAD_FORMAT)
        with self.store.reader(cid) as conn:
            result = payloads.get_payload(conn, payload_id)
        if result is None:
            raise ApiError(
                404,
                "payload_not_found",
                messages.API_PAYLOAD_NOT_FOUND.format(payload_id=payload_id, case=cid),
            )
        result["formatted"] = None
        if fmt == "pretty":
            fmt_name = result["payload"]["fmt"]
            result["formatted"] = _pretty(result["text"], fmt_name, result["truncated_display"])
        return Response(200, result)

    # -- saved filters (per case, table saved_view)

    def list_views(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            return Response(200, {"views": views.list_views(conn)})

    def create_view(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        flt = body.get("filter")
        if flt is not None and isinstance(flt, dict) and flt:
            _check_scan_filter(flt)
        with self._writer(cid) as conn:
            created = views.create_view(conn, body.get("name"), flt, body.get("columns"))
        return Response(201, created)

    def delete_view(self, request: Request) -> Response:
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise ApiError(400, "invalid_view_id", messages.API_VIEW_BAD_ID)
        cid = _case_from_query(self.store, request)
        with self._writer(cid) as conn:
            views.delete_view(conn, int(raw))
        return Response(204)

    def update_view(self, request: Request) -> Response:
        """Rename and/or overwrite the filter (and columns) of a saved filter; ``name`` is required."""
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise ApiError(400, "invalid_view_id", messages.API_VIEW_BAD_ID)
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        name = views.sanitize_name(body.get("name"))
        flt = body.get("filter")
        if isinstance(flt, dict) and flt:
            _check_scan_filter(flt)
        flt_text = views.validate_filter(flt)
        cols = body.get("columns")
        cols_text = None if cols is None else json.dumps(views._columns(cols))
        with self._writer(cid) as conn:
            with conn:
                if any(
                    r[0].casefold() == name.casefold()
                    for r in conn.execute("SELECT name FROM saved_view WHERE id <> ?", (int(raw),))
                ):
                    raise views.ViewError("duplicate_name", name=name)
                cur = conn.execute(
                    "UPDATE saved_view SET name = ?, filter = ?, columns = ? WHERE id = ?",
                    (name, flt_text, cols_text, int(raw)),
                )
                if cur.rowcount == 0:
                    raise views.ViewError("not_found", view_id=int(raw))
            row = next(r for r in views.list_views(conn) if r["id"] == int(raw))
        return Response(200, row)

    # -- bookmarks (per case, table bookmark) and compare

    @staticmethod
    def _bookmark_id(request: Request) -> int:
        raw = request.match["id"]
        if not _ENTRY_ID.match(raw):
            raise ApiError(400, "invalid_bookmark_id", messages.API_BAD_BOOKMARK_ID)
        return int(raw)

    def list_bookmarks(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        limit = bookmarks.DEFAULT_LIMIT
        raw_limit = request.query.get("limit")
        if raw_limit is not None:
            if not _SMALL_INT.match(raw_limit) or not 1 <= int(raw_limit) <= bookmarks.MAX_LIMIT:
                raise _bad(messages.API_BAD_KEY_LIMIT.format(limit=bookmarks.MAX_LIMIT))
            limit = int(raw_limit)
        after = None
        raw_after = request.query.get("after")
        if raw_after:
            if not _ENTRY_ID.match(raw_after):
                raise _bad(messages.API_BAD_CURSOR)
            after = int(raw_after)
        with self.store.reader(cid) as conn:
            items, nxt = bookmarks.list_bookmarks(
                conn,
                color=request.query.get("color") or None,
                text=request.query.get("q") or None,
                limit=limit,
                after=after,
            )
        return Response(200, {"bookmarks": items, "next": nxt})

    def get_bookmark(self, request: Request) -> Response:
        bookmark_id = self._bookmark_id(request)
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            return Response(200, bookmarks.get_bookmark(conn, bookmark_id))

    def create_bookmark(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        entry_id = _int_field(body, "entry_id")
        with self._writer(cid) as conn:
            made = bookmarks.create_bookmark(conn, entry_id, body.get("color"), body.get("note"))
        return Response(201, made)

    def update_bookmark(self, request: Request) -> Response:
        bookmark_id = self._bookmark_id(request)
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        changes = {k: body[k] for k in ("color", "note") if k in body}
        with self._writer(cid) as conn:
            changed = bookmarks.update_bookmark(conn, bookmark_id, **changes)
        return Response(200, changed)

    def delete_bookmark(self, request: Request) -> Response:
        bookmark_id = self._bookmark_id(request)
        cid = _case_from_query(self.store, request)
        with self._writer(cid) as conn:
            bookmarks.delete_bookmark(conn, bookmark_id)
        return Response(204)

    def lookup_bookmarks(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        with self.store.reader(cid) as conn:
            found = bookmarks.for_entries(conn, body.get("entry_ids"))
        return Response(200, {"bookmarks": {str(k): v for k, v in found.items()}})

    def export_bookmarks(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        with self.store.reader(cid) as conn:
            return Response(200, bookmarks.export_bookmarks(conn))

    def import_bookmarks(self, request: Request) -> Response:
        body = request.read_body()
        cid = _case_from_body(self.store, body)
        with self._writer(cid) as conn:
            counts = bookmarks.import_bookmarks(conn, body.get("data"))
        return Response(200, counts)

    def compare(self, request: Request) -> Response:
        cid = _case_from_query(self.store, request)
        ids = []
        for name in ("a", "b"):
            raw = request.query.get(name, "")
            if not _ENTRY_ID.match(raw):
                raise ApiError(400, "invalid_entry_id", messages.API_BAD_ENTRY_ID)
            ids.append(int(raw))
        sides = []
        with self.store.reader(cid) as conn:
            for entry_id in ids:
                entry = query.get_entry(conn, entry_id, max_cont_lines=bookmarks.COMPARE_MAX_LINES)
                if entry is None:
                    raise ApiError(
                        404,
                        "entry_not_found",
                        messages.API_ENTRY_NOT_FOUND.format(entry_id=entry_id, case=cid),
                    )
                sides.append(entry)
        out = []
        for side in sides:
            item = _entry_json(side)
            item["cont_truncated"] = bool(side.get("cont_truncated"))
            out.append(item)
        diff = bookmarks.diff_records(sides[0], sides[1])
        diff["truncated"] = diff["truncated"] or any(i["cont_truncated"] for i in out)
        return Response(200, {"a": out[0], "b": out[1], "diff": diff})

    @contextmanager
    def _writer(self, case_id: str) -> Iterator[sqlite3.Connection]:
        """A short-lived read-write connection (user data only; the ingest has its own)."""
        cid = self.store.require(case_id)
        conn = db.connect(self.store.db_path(cid))
        with closing(conn):
            yield conn
