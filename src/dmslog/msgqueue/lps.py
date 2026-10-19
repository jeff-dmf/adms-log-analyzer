"""Reading the adapters' message queue databases (``LocalPersistenceStore/*/*.db3``), issue #74.

The adapters keep their pending messages in SQLite files (WAL mode). An ERS carries the ``.db3`` and its
``-wal``; this module copies both to a private temporary folder, lets SQLite apply the WAL there, reads
the rows of ``CURRENT_INTEGRATION_MESSAGE`` and always deletes the temporary folder. The original is
never opened by SQLite. Nothing from the database is ever concatenated into SQL or used as a path.

Public API (pure functions, no case store involved; the caller persists the results):

* ``discover(path, *, sources=None) -> list[BankFiles]``: queue database candidates of a zip or folder
  (``.../LocalPersistenceStore/<dir>/<name>.db3`` plus the ``-wal`` next to it). Listing only: nothing
  is decompressed. A loose ``Log`` folder gives an empty list.
* ``fingerprint_of(files) -> str``: identity of the files that would be read (zip: crc32 and size; folder:
  size and ``mtime_ns``); the caller skips the re-read when it matches a stored one.
* ``read_bank(files, case_dir, *, limits=Limits(), progress=None, cancelled=None) -> BankResult``:
  copy, read and clean up one database. Errors of the database become ``BankResult.status == "error"``
  with a stable code; they never raise. Cancellation raises ``ReadCancelled`` (or whatever the
  ``progress`` callback raises); the temporary folder is deleted in every case.
* ``read_db_file(tmp_db, limits, *, cancelled=None) -> DbRead``: the low-level read of one private
  copy (an already extracted ``.db3`` with its ``-wal`` beside it).
* ``sweep_tmp_dir(case_dir, *, older_than_s=3600, now=None) -> list[str]``: deletes ``.queue-tmp-*``
  folders left by a crashed process (older than one hour); never follows symlinks.
* ``make_tmp_dir(case_dir)`` / ``remove_tmp_dir(path)``: the temporary folder lifecycle.

``progress(file_name, bytes_done, bytes_total)`` is called after every copied block (file name only).
``cancelled()`` is polled on every block and by SQLite's progress handler.

Error codes (``BankResult.error``): ``encrypted_member``, ``too_large``, ``insufficient_space``,
``extract_failed``, ``not_sqlite``, ``corrupt``, ``missing_columns``, ``read_timeout``, ``read_failed``.
Warnings: ``live_copy_unstable``, ``items_truncated``, ``bodies_truncated``, ``skipped_rows``.
Snapshots: ``wal_applied`` | ``no_wal`` | ``wal_failed``. The status ``not_a_queue`` means the database
has no ``CURRENT_INTEGRATION_MESSAGE`` table (for example the "created" change-request databases).

The query lists fixed columns; the ``_INTEGRATION_MESSAGE_USERNAME`` column is never selected.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dmslog.ingest.discover import SourceInfo, normalize_member_name
from dmslog.ingest.discover import discover as _discover_sources
from dmslog.ingest.reader import InsufficientDiskSpaceError, check_free_space
from dmslog.paths import private_mkdir

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_BANK_BODY_BYTES = 256 * 1024 * 1024
MAX_ITEMS = 200_000
MAX_DB_BYTES = 2 * 1024**3
MAX_WAL_BYTES = 1024**3
MAX_RUN_BYTES = (
    8 * 1024**3
)  # every byte copied by one reading of a collection (retries and fallbacks included)
READ_TIMEOUT_S = 60.0
COPY_BLOCK = 1024 * 1024
COPY_ATTEMPTS = 3
TMP_PREFIX = ".queue-tmp-"
TMP_RE = re.compile(r"^\.queue-tmp-[0-9a-f]{32}$")
SWEEP_AGE_S = 3600
_PROGRESS_STEPS = 10_000  # SQLite VM steps between two progress-handler calls
_RMTREE_TRIES = 5
_RMTREE_DELAY_S = 0.05

TABLE = "CURRENT_INTEGRATION_MESSAGE"
COLUMNS = (
    "_INTEGRATION_MESSAGE_ID",
    "_INTEGRATION_MESSAGE_STATUS",
    "_INTEGRATION_MESSAGE_FILE_LOCATION",
    "_INTEGRATION_MESSAGE_TYPE",
    "_INTEGRATION_MESSAGE_FINALSTATE",
    "_INTEGRATION_MESSAGE_TIMESTAMP",
    "_INTEGRATION_MESSAGE_CONTENT",
)
_QUERY = (
    "SELECT _INTEGRATION_MESSAGE_ID, _INTEGRATION_MESSAGE_STATUS, _INTEGRATION_MESSAGE_FILE_LOCATION, "
    "_INTEGRATION_MESSAGE_TYPE, _INTEGRATION_MESSAGE_FINALSTATE, _INTEGRATION_MESSAGE_TIMESTAMP, "
    "length(_INTEGRATION_MESSAGE_CONTENT), "
    "CASE WHEN length(_INTEGRATION_MESSAGE_CONTENT) <= :max_body THEN _INTEGRATION_MESSAGE_CONTENT END "
    "FROM CURRENT_INTEGRATION_MESSAGE ORDER BY _INTEGRATION_MESSAGE_ID LIMIT :limit"
)

_BANK_MEMBER_RE = re.compile(r"(?:^|/)LocalPersistenceStore/[^/]+/[^/]+\.db3$", re.IGNORECASE)
_BANK_NAME_RE = re.compile(r"^__~(?P<cache>[^~]{1,128})~__(?P<adapter>[A-Za-z0-9_]{1,64})__LPS\.db3$")
_GUID_RE = re.compile(r"^\{?[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}?$")
_SQLITE_MAGIC = b"SQLite format 3\x00"

_TICKS_AT_UNIX_EPOCH = 621_355_968_000_000_000
_TICKS_MASK = 0x3FFF_FFFF_FFFF_FFFF
_TS_MIN_MS = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp() * 1000)
_TS_MAX_MS = int(datetime(2100, 1, 1, tzinfo=UTC).timestamp() * 1000)
_INT63 = 2**63
_BAD = object()  # marker for TEXT that is not valid UTF-8


class ReadCancelled(Exception):
    """The caller asked to stop (``cancelled()`` returned true)."""


class _BankError(Exception):
    """Internal: a database-level failure with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CopyBudget:
    """The bytes one reading may still copy, shared by all its banks (many valid databases are no bomb)."""

    def __init__(self, total: int) -> None:
        self.remaining = int(total)

    def take(self, size: int) -> None:
        self.remaining -= size
        if self.remaining < 0:
            raise _BankError("run_budget_exceeded")


@dataclass(frozen=True)
class Limits:
    max_body_bytes: int = MAX_BODY_BYTES
    max_bank_body_bytes: int = MAX_BANK_BODY_BYTES
    max_items: int = MAX_ITEMS
    max_db_bytes: int = MAX_DB_BYTES
    max_wal_bytes: int = MAX_WAL_BYTES
    max_run_bytes: int = MAX_RUN_BYTES
    read_timeout_s: float = READ_TIMEOUT_S
    copy_attempts: int = COPY_ATTEMPTS


@dataclass(frozen=True)
class BankFiles:
    member: str  # POSIX path of the .db3 inside the zip/folder
    db: SourceInfo
    wal: SourceInfo | None
    cache: str
    adapter: str | None


@dataclass(frozen=True)
class QueueRow:
    item_id: int
    message_id: str | None  # lower case, no braces
    msg_type: str | None
    status: str | None
    state: str  # sent | pending | other
    file_location: str | None
    ts: int | None  # ms of local time (not converted), None when out of range
    size: int
    body: bytes | None  # None when empty or over a limit (``size`` still reports it)


@dataclass(frozen=True)
class DbRead:
    status: str  # ready | not_a_queue
    items: tuple[QueueRow, ...] = ()
    warnings: tuple[str, ...] = ()
    skipped_rows: int = 0


@dataclass(frozen=True)
class BankResult:
    member: str
    cache: str
    adapter: str | None
    db_size: int
    wal_size: int | None
    fingerprint: str
    status: str  # ready | not_a_queue | error
    snapshot: str | None = None  # wal_applied | no_wal | wal_failed, only when ready
    error: str | None = None
    warnings: tuple[str, ...] = ()
    items: tuple[QueueRow, ...] = field(default=(), repr=False)
    skipped_rows: int = 0


# --------------------------------------------------------------------------- discovery


def discover(path: str | os.PathLike[str], *, sources: list[SourceInfo] | None = None) -> list[BankFiles]:
    """Queue database candidates of an ERS zip or folder (listing only, nothing is read)."""
    if sources is None:
        sources = list(
            _discover_sources(
                path,
                max_member_bytes=1 << 62,  # databases are bounded by Limits, not by the log rules
                max_ratio=float("inf"),
                max_total_bytes=1 << 62,
            )
        )
    by_name = {s.member.lower(): s for s in sources}
    banks: list[BankFiles] = []
    for src in sources:
        if src.container_kind == "file" or not _BANK_MEMBER_RE.search(src.member):
            continue
        wal = by_name.get(src.member.lower() + "-wal")
        cache, adapter = parse_bank_name(src.file_name)
        banks.append(BankFiles(member=src.member, db=src, wal=wal, cache=cache, adapter=adapter))
    banks.sort(key=lambda b: b.member.lower())
    return banks


def parse_bank_name(file_name: str) -> tuple[str, str | None]:
    """``__~<cache>~__<adapter>__LPS.db3`` -> (cache, adapter); other names -> (stem, None)."""
    m = _BANK_NAME_RE.match(file_name)
    if m:
        return m.group("cache"), m.group("adapter")
    stem = file_name[: -len(".db3")] if file_name.lower().endswith(".db3") else file_name
    return stem[:128], None


def fingerprint_of(files: BankFiles) -> str:
    """Identity of the files to read: zip = ``crc32:size``; folder = ``size:mtime_ns`` (db, then wal)."""
    return f"{_part(files.db)}|{_part(files.wal) if files.wal else '-'}"


def _part(src: SourceInfo) -> str:
    if src.container_kind == "zip":
        return f"z{(src.zip_crc or 0):08x}:{src.size}"
    try:
        st = (src.container / src.member).stat()
    except OSError:
        return f"d{src.size}:0"
    return f"d{st.st_size}:{st.st_mtime_ns}"


# --------------------------------------------------------------------------- temporary folder


def make_tmp_dir(case_dir: Path) -> Path:
    """A fresh private ``.queue-tmp-<hex>`` folder inside the case folder (0700 on POSIX)."""
    return private_mkdir(Path(case_dir) / f"{TMP_PREFIX}{uuid.uuid4().hex}")


def remove_tmp_dir(path: Path) -> bool:
    """Delete a temporary folder; retries on ``PermissionError`` (Windows scanners). True when gone."""
    for attempt in range(_RMTREE_TRIES):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < _RMTREE_TRIES:
                time.sleep(_RMTREE_DELAY_S)
        else:
            return True
    log.warning("tmp_left: a temporary queue folder could not be deleted; the sweep will retry")
    return False


def sweep_tmp_dir(
    case_dir: Path, *, older_than_s: float = SWEEP_AGE_S, now: float | None = None
) -> list[str]:
    """Delete ``.queue-tmp-*`` folders older than ``older_than_s`` (symlinks are never followed)."""
    cutoff = (time.time() if now is None else now) - older_than_s
    removed: list[str] = []
    try:
        entries = list(os.scandir(case_dir))
    except OSError:
        return removed
    for entry in entries:
        if not TMP_RE.match(entry.name):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
        except OSError:
            continue
        if remove_tmp_dir(Path(entry.path)):
            removed.append(entry.name)
    return removed


# --------------------------------------------------------------------------- copy


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


class _Source:
    """Open container of a bank: reads members as byte streams, never by an on-disk name."""

    def __init__(self, files: BankFiles) -> None:
        self.files = files
        self.zip: zipfile.ZipFile | None = None
        self.infos: dict[str, zipfile.ZipInfo] = {}

    def __enter__(self) -> _Source:
        if self.files.db.container_kind == "zip":
            self.zip = zipfile.ZipFile(self.files.db.container)
            for info in self.zip.infolist():
                if info.is_dir():
                    continue
                self.infos[normalize_member_name(info.filename)] = info
        return self

    def __exit__(self, *exc: object) -> None:
        if self.zip is not None:
            self.zip.close()

    def open(self, src: SourceInfo):  # noqa: ANN201 (binary file object)
        if self.zip is not None:
            info = self.infos.get(src.member)
            if info is None:
                raise FileNotFoundError(src.member)
            return self.zip.open(info)
        return open(src.container / src.member, "rb")  # noqa: SIM115 (caller closes)


def _copy_one(
    source: _Source,
    src: SourceInfo,
    dest: Path,
    max_bytes: int,
    progress: Callable[[str, int, int], None] | None,
    cancelled: Callable[[], bool] | None,
    budget: CopyBudget | None = None,
) -> int:
    """Copy one member to ``dest`` (fixed private name, ``O_EXCL``), counting the bytes really read.

    Every block also counts against the shared ``budget`` of the run. A damaged stream (bad deflate data, CRC)
    is ``extract_failed`` for this bank only.
    """
    if src.size > max_bytes:
        raise _BankError("too_large")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    done = 0
    fd = os.open(dest, flags, 0o600)
    try:
        with source.open(src) as reader, os.fdopen(fd, "wb", closefd=False) as out:
            while True:
                if cancelled is not None and cancelled():
                    raise ReadCancelled
                block = reader.read(COPY_BLOCK)
                if not block:
                    break
                done += len(block)
                if done > max_bytes:
                    raise _BankError("too_large")
                if budget is not None:
                    budget.take(len(block))
                out.write(block)
                if progress is not None:
                    progress(src.file_name, done, src.size)
    except (zlib.error, zipfile.BadZipFile, EOFError) as exc:
        raise _BankError("extract_failed") from exc
    finally:
        os.close(fd)
    return done


def _copy_bank(
    source: _Source,
    files: BankFiles,
    folder: Path,
    *,
    with_wal: bool,
    limits: Limits,
    progress: Callable[[str, int, int], None] | None,
    cancelled: Callable[[], bool] | None,
    budget: CopyBudget | None = None,
) -> tuple[bool, list[str]]:
    """Copy ``q.db3`` (and ``q.db3-wal``) into ``folder``. Returns (wal_copied, warnings).

    A live folder is stat'ed before and after; a change repeats the copy (up to ``copy_attempts``).
    """
    db_dest = folder / "q.db3"
    wal_dest = folder / "q.db3-wal"
    live = files.db.container_kind == "folder"
    wal_src = files.wal if with_wal else None
    warnings: list[str] = []
    attempts = max(1, limits.copy_attempts) if live else 1
    for attempt in range(1, attempts + 1):
        before = (
            _stat_key(files.db.container / files.db.member),
            _stat_key(wal_src.container / wal_src.member) if wal_src else None,
        )
        for leftover in (db_dest, wal_dest):
            leftover.unlink(missing_ok=True)
        _copy_one(source, files.db, db_dest, limits.max_db_bytes, progress, cancelled, budget)
        wal_copied = False
        if wal_src is not None:
            try:
                _copy_one(source, wal_src, wal_dest, limits.max_wal_bytes, progress, cancelled, budget)
                wal_copied = wal_dest.stat().st_size > 0
            except FileNotFoundError:
                wal_copied = False  # a checkpoint removed it between the listing and the copy
            if not wal_copied:
                wal_dest.unlink(missing_ok=True)
        if not live:
            return wal_copied, warnings
        after = (
            _stat_key(files.db.container / files.db.member),
            _stat_key(wal_src.container / wal_src.member) if wal_src else None,
        )
        if after == before:
            return wal_copied, warnings
        if attempt == attempts:
            warnings.append("live_copy_unstable")
            return wal_copied, warnings
    return False, warnings  # pragma: no cover (loop always returns)


# --------------------------------------------------------------------------- read


def _close_connection(conn: sqlite3.Connection) -> None:
    conn.close()


def _open_connection(tmp_db: Path, *, immutable: bool) -> sqlite3.Connection:
    uri = tmp_db.as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=rw")
    return sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=True, cached_statements=0)


def _decode_text(raw: bytes) -> object:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return _BAD


def _harden(conn: sqlite3.Connection, limits: Limits) -> None:
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_body_bytes + 1)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 10_000)
    setconfig = getattr(conn, "setconfig", None)  # Python >= 3.12
    if setconfig is not None:
        for name, value in (
            ("SQLITE_DBCONFIG_DEFENSIVE", True),
            ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
            ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False),
            ("SQLITE_DBCONFIG_ENABLE_VIEW", False),
        ):
            const = getattr(sqlite3, name, None)
            if const is not None:
                setconfig(const, value)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA cell_size_check=ON")
    conn.execute("PRAGMA mmap_size=0")
    conn.text_factory = _decode_text


def _clean_text(value: object, max_len: int) -> str | None:
    if value is _BAD or value is None or isinstance(value, bytes | bytearray | memoryview):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cc")
    return text[:max_len]


def normalize_message_id(value: object) -> str | None:
    text = _clean_text(value, 64)
    if text is None or not _GUID_RE.match(text):
        return None
    return text.strip("{}").lower()


def state_of(status: str | None) -> str:
    if status is None or not status.strip():
        return "pending"
    return "sent" if status.strip().lower() == "sent" else "other"


def ticks_to_ms(value: object) -> int | None:
    """.NET ticks (optionally ``DateTime.ToBinary`` with the kind bits) -> ms, no time zone conversion."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    ticks = value & _TICKS_MASK
    ms = (ticks - _TICKS_AT_UNIX_EPOCH) // 10_000
    if not _TS_MIN_MS <= ms < _TS_MAX_MS:
        return None
    return ms


def read_db_file(
    tmp_db: Path,
    limits: Limits = Limits(),  # noqa: B008 (frozen)
    *,
    immutable: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> DbRead:
    """Read the queue table of a private copy. ``sqlite3.DatabaseError`` propagates (the caller decides
    the fallback); the connection is closed before returning or raising."""
    state = {"cancel": False}
    deadline = time.monotonic() + limits.read_timeout_s

    def _progress() -> int:
        if cancelled is not None and cancelled():
            state["cancel"] = True
            return 1
        return 1 if time.monotonic() > deadline else 0

    conn = _open_connection(tmp_db, immutable=immutable)
    try:
        _harden(conn, limits)
        conn.set_progress_handler(_progress, _PROGRESS_STEPS)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()  # runs the WAL recovery
            if not immutable:
                conn.execute("PRAGMA query_only=ON")
            return _read_queue(conn, limits)
        except sqlite3.OperationalError as exc:
            if state["cancel"]:
                raise ReadCancelled from None
            if "interrupt" in str(exc).lower():
                raise _BankError("read_timeout") from None
            raise
    finally:
        _close_connection(conn)


def _read_queue(conn: sqlite3.Connection, limits: Limits) -> DbRead:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name = 'CURRENT_INTEGRATION_MESSAGE'").fetchone()
    if row is None or row[0] != "table":
        return DbRead(status="not_a_queue")
    present = {str(r[1]).upper() for r in conn.execute('PRAGMA table_info("CURRENT_INTEGRATION_MESSAGE")')}
    if not set(COLUMNS) <= present:
        raise _BankError("missing_columns")
    cursor = conn.execute(_QUERY, {"max_body": limits.max_body_bytes, "limit": limits.max_items + 1})
    items: list[QueueRow] = []
    warnings: list[str] = []
    skipped = 0
    bank_bytes = 0
    bodies_truncated = False
    for raw in cursor:
        if len(items) >= limits.max_items:
            warnings.append("items_truncated")
            break
        item_id, status, location, msg_type, final_state, ts, length, content = raw
        if isinstance(item_id, bool) or not isinstance(item_id, int) or not 0 <= item_id < _INT63:
            skipped += 1
            continue
        status_text = _clean_text(status, 64)
        size = length if isinstance(length, int) else 0
        body: bytes | None = None
        if isinstance(content, str):
            content = content.encode("utf-8")
            size = len(content)
        if isinstance(content, bytes) and 0 < len(content) <= limits.max_body_bytes:
            if bank_bytes + len(content) <= limits.max_bank_body_bytes:
                body = content
                bank_bytes += len(content)
            else:
                bodies_truncated = True
        items.append(
            QueueRow(
                item_id=item_id,
                message_id=normalize_message_id(final_state),
                msg_type=_clean_text(msg_type, 128),
                status=status_text,
                state=state_of(status_text),
                file_location=_clean_text(location, 256),
                ts=ticks_to_ms(ts),
                size=size,
                body=body,
            )
        )
    if bodies_truncated:
        warnings.append("bodies_truncated")
    if skipped:
        warnings.append("skipped_rows")
    return DbRead(status="ready", items=tuple(items), warnings=tuple(warnings), skipped_rows=skipped)


def _classify_db_error(exc: sqlite3.Error) -> str:
    text = str(exc).lower()
    if "not a database" in text:
        return "not_sqlite"
    if isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError):
        return "corrupt"
    if "malformed" in text or "corrupt" in text:
        return "corrupt"
    return "read_failed"


def _has_sqlite_header(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(_SQLITE_MAGIC))
    except OSError:
        return False
    return head == _SQLITE_MAGIC or (path.stat().st_size == 0)


# --------------------------------------------------------------------------- bank


def read_bank(
    files: BankFiles,
    case_dir: Path,
    *,
    limits: Limits = Limits(),  # noqa: B008 (frozen)
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    budget: CopyBudget | None = None,
) -> BankResult:
    """Copy one database (and its WAL) to a private temporary folder, read it, delete the folder.

    ``budget`` (shared by the banks of one reading) caps every byte copied, repeats and the fallback included.
    """
    base = {
        "member": files.member,
        "cache": files.cache,
        "adapter": files.adapter,
        "db_size": files.db.size,
        "wal_size": files.wal.size if files.wal else None,
        "fingerprint": fingerprint_of(files),
    }

    def fail(code: str, warnings: tuple[str, ...] = ()) -> BankResult:
        return BankResult(status="error", error=code, warnings=warnings, **base)

    if files.db.limit_violation == "encrypted" or (files.wal and files.wal.limit_violation == "encrypted"):
        return fail("encrypted_member")
    if files.db.size > limits.max_db_bytes or (files.wal and files.wal.size > limits.max_wal_bytes):
        return fail("too_large")
    if cancelled is not None and cancelled():
        raise ReadCancelled
    case_dir = Path(case_dir)
    try:
        check_free_space(case_dir, files.db.size + (files.wal.size if files.wal else 0))
    except InsufficientDiskSpaceError:
        return fail("insufficient_space")
    except OSError:
        return fail("extract_failed")

    tmp_dirs: list[Path] = []
    warnings: list[str] = []
    try:
        try:
            with _Source(files) as source:
                first = make_tmp_dir(case_dir)
                tmp_dirs.append(first)
                wal_copied, copy_warnings = _copy_bank(
                    source,
                    files,
                    first,
                    with_wal=True,
                    limits=limits,
                    progress=progress,
                    cancelled=cancelled,
                    budget=budget,
                )
                warnings.extend(copy_warnings)
                if not _has_sqlite_header(first / "q.db3"):
                    return fail("not_sqlite", tuple(warnings))
                snapshot = "wal_applied" if wal_copied else "no_wal"
                try:
                    read = read_db_file(first / "q.db3", limits, cancelled=cancelled)
                except sqlite3.DatabaseError as exc:
                    if not wal_copied:
                        return fail(_classify_db_error(exc), tuple(warnings))
                    # The WAL could not be applied: read only the main file, in a fresh copy.
                    second = make_tmp_dir(case_dir)
                    tmp_dirs.append(second)
                    _copy_bank(
                        source,
                        files,
                        second,
                        with_wal=False,
                        limits=limits,
                        progress=progress,
                        cancelled=cancelled,
                        budget=budget,
                    )
                    snapshot = "wal_failed"
                    try:
                        read = read_db_file(second / "q.db3", limits, immutable=True, cancelled=cancelled)
                    except sqlite3.DatabaseError as exc2:
                        return fail(_classify_db_error(exc2), tuple(warnings))
        except _BankError as exc:
            return fail(exc.code, tuple(warnings))
        except (OSError, zipfile.BadZipFile, EOFError, RuntimeError, ValueError):
            # Includes zlib/CRC failures of a damaged member; an injected cancellation or a callback's
            # own exception types are not in this list and propagate.
            return fail("extract_failed", tuple(warnings))
        except sqlite3.Error as exc:
            return fail(_classify_db_error(exc), tuple(warnings))
    finally:
        for folder in reversed(tmp_dirs):
            remove_tmp_dir(folder)

    all_warnings = tuple(dict.fromkeys([*warnings, *read.warnings]))
    if read.status == "not_a_queue":
        return BankResult(status="not_a_queue", warnings=all_warnings, **base)
    return BankResult(
        status="ready",
        snapshot=snapshot,
        warnings=all_warnings,
        items=read.items,
        skipped_rows=read.skipped_rows,
        **base,
    )


__all__ = [
    "COLUMNS",
    "BankFiles",
    "BankResult",
    "CopyBudget",
    "DbRead",
    "Limits",
    "QueueRow",
    "ReadCancelled",
    "discover",
    "fingerprint_of",
    "make_tmp_dir",
    "parse_bank_name",
    "read_bank",
    "read_db_file",
    "remove_tmp_dir",
    "sweep_tmp_dir",
]
