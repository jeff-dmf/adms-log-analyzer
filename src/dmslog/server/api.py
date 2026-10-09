"""JSON API: cases, collections (background ingest), progress, sources and entry queries.

The router is a plain table of (method, regex, handler). Handlers receive an ``Request`` and
return a ``Response`` (or a ``Stream`` for Server-Sent Events); errors are ``ApiError``.
Nothing coming from the client is used as a file system path except the collection ``path``,
which is only handed to the ingest as its input; case ids are validated strictly.
"""

from __future__ import annotations

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
from ..store import db, query, views
from ..store.filters import QueryInputError, compile_filter
from ..store.writer import IndexWriter
from .sse import ProgressHub

MAX_BODY_BYTES = 256 * 1024
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


def _family_param(request: Request) -> str | None:
    raw = request.query.get("family")
    if raw is None or raw == "":
        return None
    if not _FAMILY.match(raw):
        raise ApiError(400, "invalid_field", messages.API_BAD_FAMILY)
    return raw


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


# --------------------------------------------------------------------------- the API object


class Api:
    """Routes and state (cases on disk, ingest jobs) of one server."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.store = CaseStore(self.data_dir)
        self.jobs = IngestJobs(self.store)
        self._routes: list[tuple[str, re.Pattern[str], Callable[[Request], Response | Stream]]] = [
            ("GET", re.compile(r"/api/cases\Z"), self.list_cases),
            ("POST", re.compile(r"/api/cases\Z"), self.create_case),
            ("DELETE", re.compile(r"/api/cases/(?P<case>[^/]+)\Z"), self.delete_case),
            ("POST", re.compile(r"/api/cases/(?P<case>[^/]+)/collections\Z"), self.add_collection),
            ("GET", re.compile(r"/api/cases/(?P<case>[^/]+)/progress\Z"), self.progress),
            ("GET", re.compile(r"/api/sources\Z"), self.list_sources),
            ("GET", re.compile(r"/api/templates\Z"), self.list_templates),
            ("GET", re.compile(r"/api/unknown\Z"), self.list_unknown),
            ("POST", re.compile(r"/api/entries/page\Z"), self.entries_page),
            ("POST", re.compile(r"/api/entries/count\Z"), self.entries_count),
            ("POST", re.compile(r"/api/histogram\Z"), self.histogram),
            ("GET", re.compile(r"/api/views\Z"), self.list_views),
            ("POST", re.compile(r"/api/views\Z"), self.create_view),
            ("DELETE", re.compile(r"/api/views/(?P<id>[^/]+)\Z"), self.delete_view),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)\Z"), self.get_entry),
            ("GET", re.compile(r"/api/entries/(?P<id>[^/]+)/context\Z"), self.entry_context),
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
        with self.store.reader(cid) as conn:
            entry = query.get_entry(conn, entry_id)
        if entry is None:
            raise ApiError(
                404, "entry_not_found", messages.API_ENTRY_NOT_FOUND.format(entry_id=entry_id, case=cid)
            )
        return Response(200, _entry_json(entry))

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

    @contextmanager
    def _writer(self, case_id: str) -> Iterator[sqlite3.Connection]:
        """A short-lived read-write connection (user data only; the ingest has its own)."""
        cid = self.store.require(case_id)
        conn = db.connect(self.store.db_path(cid))
        with closing(conn):
            yield conn
