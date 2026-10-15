"""Ingestion pipeline: selected sources -> reader -> RecordGrouper -> writer (ARQUITETURA 9.2, 9.3).

``run_ingest`` is idempotent: running it again on the same zip, folder or file writes nothing
when nothing changed, appends only the tail of files that grew, follows rotations
(``X.log`` -> ``X.log.0``) and reindexes files that were rewritten or truncated.

Partial open (ROADMAP 9.9, ``IngestOptions.first``): a first pass indexes a time window and/or some sources,
derives and correlates what it has and reports ``first_ready``; the second pass completes every source
(``ingest/partial.py``). Any later run of the same path completes what an interrupted run left.

Commit points: one transaction per batch of about ``batch_bytes`` fed bytes (16 MiB by
default) or ``MAX_BATCH_RECORDS`` records. At every commit the source row holds a consistent
state (``indexed_bytes``, ``tail_hash``, counts, ``status='indexing'``). Resuming (after an
interruption or file growth) always deletes the last stored record of the source and parses
again from its ``byte_off``/``line_no``, because that record may have gained lines.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dmslog import __version__
from dmslog.aliases import Aliases
from dmslog.diagnostics import component, describe_error, error_label
from dmslog.ingest.discover import SourceInfo, discover, resolve_scope
from dmslog.ingest.ers import (
    ADD_PAIR,
    ADD_SAME,
    KIND_ERS_ZIP,
    KIND_FILE,
    KIND_FOLDER,
    CollectionPlan,
    HeldCollection,
    classify_add,
    held_identity,
    parse_ers_name,
    select_sources,
)
from dmslog.ingest.fingerprint import (
    HASH_SPAN,
    Action,
    HeadWindow,
    KnownSource,
    TailWindow,
    decide_action,
    sha1_hex,
    span_for,
)
from dmslog.ingest.partial import (
    DETECT_BYTES,
    DETECT_LINES,
    SCOPE_KEY_PREFIX,
    SEG_PREFIX,
    SEG_SUFFIX,
    SEG_WINDOW,
    FirstPass,
    PartialState,
    drop_state,
    hash_stream,
    head_hash_of,
    load_state,
    locate_window,
    pending_work,
    save_state,
)
from dmslog.ingest.reader import (
    ReadLimitError,
    ReadLimits,
    SourceReader,
    check_free_space,
    relax_ratio_violations,
)
from dmslog.knowledge.installation import InstallationConfig, read_installation
from dmslog.knowledge.registry import RegistryError
from dmslog.parse.envelope import detect_envelope, get_envelope, is_known_envelope
from dmslog.parse.records import DEFAULT_MAX_RECORD_BYTES, RecordGrouper, RecordTooLargeError
from dmslog.parse.severity import generic_severity, infer_severity
from dmslog.parse.templates import Catalog, load_catalog
from dmslog.store.config import config_param_rows, product_for_process
from dmslog.store.writer import (
    FLAG_TS_OUT_OF_ORDER,
    MAX_BATCH_RECORDS,
    PARTIAL_ORDER_PREFIX,
    BatchStats,
    CollectionRow,
    IndexWriter,
    SourceRow,
    cached_signature,
    next_row_id,
)

CASE_DB_FILENAME = "case.sqlite"
DEFAULT_BATCH_BYTES = 16 * 1024 * 1024

PLAIN_ENVELOPE = "plain"  # Envelope.name of the fallback envelope

SCOPE_DEFAULT = "default"
SCOPE_ALL = "all"

# Reasons stored in source.error for sources that are listed but not indexed.
SKIP_OUT_OF_SCOPE = "out_of_scope"
SKIP_NOT_INDEXABLE = "not_indexable"
FAIL_RECORD_TOO_LARGE = "record_too_large"

# SourceResult.action / ProgressEvent.action of a partial open (besides the Action values)
ACTION_WINDOW = "window"  # first pass: only the window of the source was indexed
ACTION_PARTIAL = "partial"  # first pass: the source already holds a window from an earlier partial open
ACTION_COMPLETE = "complete"  # second pass: the rest of a source indexed out of order
STAGE_FIRST = "first"
STAGE_REST = "rest"


@dataclass(frozen=True)
class IngestOptions:
    scope: str = SCOPE_DEFAULT  # "default" or "all"
    case_name: str | None = None
    batch_bytes: int = DEFAULT_BATCH_BYTES
    chunk_size: int = 1 << 20
    limits: ReadLimits = field(default_factory=ReadLimits)
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES
    catalog: Catalog | None = None  # template catalog; None = the packaged adapters/*.toml
    aliases: Aliases | None = None  # local aliases (real -> public names); None = no aliases
    first: FirstPass | None = (
        None  # partial open (ROADMAP 9.9): what to index first; None = everything at once
    )


@dataclass(frozen=True)
class ProgressEvent:
    """Reported to the progress callback. ``phase``: source_start, progress, source_done, done, and with a
    partial open ``first_ready`` (the first pass is indexed, derived and correlated: the UI can query it).
    ``stage``: None without a partial open, else ``first`` or ``rest``; the totals then count the stage."""

    phase: str
    member: str | None
    action: str | None
    source_bytes: int  # bytes fed so far for the current source
    source_total: int  # size of the current source
    source_records: int  # records written for the current source
    bytes_done: int  # bytes processed in the run (unchanged sources count as done)
    bytes_total: int  # total size of the selected sources
    records: int  # records written in the run
    sources_done: int
    sources_total: int
    stage: str | None = None


ProgressFn = Callable[[ProgressEvent], None]


@dataclass
class SourceResult:
    member: str
    action: str  # Action value, "skipped" or "error"
    records: int = 0
    lines: int = 0
    bytes: int = 0
    error: str | None = None


@dataclass
class IngestResult:
    collection_id: int = 0
    sources_listed: int = 0
    sources_selected: int = 0
    sources_indexed: int = 0  # new + append + resume + reindex
    sources_unchanged: int = 0
    sources_skipped: int = 0  # listed, not selected
    sources_failed: int = 0
    records: int = 0
    lines: int = 0
    bytes: int = 0  # stream bytes read and parsed in this run
    seconds: float = 0.0
    details: list[SourceResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # non-fatal problems (derivation failures)
    events_derived: int = 0  # events written by the derivation step of this run
    derive_seconds: float = 0.0  # time of the derivation step (included in ``seconds``)
    links_created: int = 0  # links written by the correlation step of this run
    correlate_seconds: float = 0.0  # time of the correlation step (included in ``seconds``)
    payloads: int = 0  # payload rows extracted in this run (parse/payload.py)
    payloads_linked: int = 0  # payloads linked to an operation by the linking step of this run
    payload_seconds: float = 0.0  # time of the payload linking step (included in ``seconds``)
    # partial open (``IngestOptions.first``)
    first_seconds: float = 0.0  # time until ``first_ready`` (first pass, its derivation and correlation)
    first_sources: int = 0  # sources of the first pass (chains included)
    first_records: int = 0  # records written by the first pass
    first_unmatched: list[str] = field(default_factory=list)  # requested members that are not selected
    deferred: bool = False  # ``defer_rest``: the second pass did not run
    partial_left: int = 0  # sources of the collection still incomplete at the end (partial or pending)

    @property
    def mb_per_s(self) -> float:
        return self.bytes / 1e6 / self.seconds if self.seconds > 0 else 0.0


# --------------------------------------------------------------------------- planning


def build_plan(path: str | Path, limits: ReadLimits, aliases: Aliases | None = None) -> CollectionPlan:
    """Like ``plan_collection`` but applying the run's limits and the ratio policy."""
    target = Path(path)
    sources = list(
        discover(
            target,
            max_member_bytes=limits.max_member_bytes,
            max_ratio=limits.max_ratio,
            max_members=limits.max_members,
            max_total_bytes=limits.max_total_bytes,
            aliases=aliases,
        )
    )
    if target.is_dir():
        kind = KIND_FOLDER
    elif target.is_file() and zipfile.is_zipfile(target):
        kind = KIND_ERS_ZIP
    else:
        kind = KIND_FILE
    sources = resolve_scope(relax_ratio_violations(sources, limits), explicit=kind == KIND_FILE)
    identity = parse_ers_name(target.name) if kind == KIND_ERS_ZIP else None
    zones = sorted({s.zone for s in sources if s.zone})
    label = identity.description if identity and identity.description else target.name
    return CollectionPlan(
        kind=kind,
        path=target,
        identity=identity,
        system=zones[0] if len(zones) == 1 else None,
        machine=None,
        label=label,
        sources=tuple(sources),
    )


_DEFAULT_CATALOG: Catalog | None = None


def default_catalog() -> Catalog:
    """The packaged catalog, loaded once per process."""
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = load_catalog()
    return _DEFAULT_CATALOG


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return slug[:80] or "case"


def default_case_name(plan: CollectionPlan, aliases: Aliases | None = None) -> str:
    """Case name from the ERS GUID and capture time, or from the folder or file name.

    With `aliases` the folder or file name carries the public spelling (the case name is stored and shown).
    """
    if plan.identity is not None:
        return f"{plan.identity.guid[:8].lower()}-{plan.identity.timestamp}"
    name = plan.path.stem if plan.kind == KIND_FILE else plan.path.name
    return _slug(aliases.apply_name(name) if aliases else name)


WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
                              *(f"lpt{n}" for n in range(1, 10))})  # fmt: skip


def is_reserved_case_name(name: str) -> bool:
    """True for names Windows reserves for devices (CON, NUL, COM1, ``nul.txt``...).

    The one rule shared by the CLI (`validate_case_name`) and the API (`CaseStore.validate_id`).
    """
    return name.split(".", 1)[0].lower() in WINDOWS_RESERVED


def validate_case_name(name: str) -> str:
    """Return ``name`` if it is a safe single folder name, else raise ValueError."""
    if not name or name != _slug(name) or name in (".", "..") or is_reserved_case_name(name):
        raise ValueError(f"invalid case name: {name!r} (use letters, digits, '.', '_' and '-')")
    return name


def case_db_path(data_dir: Path, case_name: str) -> Path:
    return data_dir / "cases" / validate_case_name(case_name) / CASE_DB_FILENAME


def held_collections(rows: list[CollectionRow]) -> list[HeldCollection]:
    """The case's ``collection`` rows as the add rules see them (``ers.classify_add``)."""
    return [
        HeldCollection(
            id=r.id,
            kind=r.kind,
            path=r.path,
            identity=held_identity(r.kind, r.ers_guid, r.ers_ts, r.ers_variant, r.label, r.path),
            group=r.ers_group if r.ers_group is not None else r.id,
        )
        for r in rows
    ]


# --------------------------------------------------------------------------- severity by code


class SeverityHook:
    """``infer_severity`` with error codes resolved through ``Knowledge`` (ERS configuration first).

    The writer calls it as ``hook(match, record)``; the run sets ``family`` before each source and
    ``installation`` once the Registry has been read. Nothing is loaded until a template actually
    exposes an error code; resolutions are memoized per ``(family, code)``.
    """

    def __init__(self) -> None:
        self.family: str | None = None
        self.installation: InstallationConfig | None = None
        self._knowledge = None
        self._memo: dict[tuple[str | None, str], int | None] = {}

    def set_installation(self, installation: InstallationConfig | None) -> None:
        self.installation = installation
        self._knowledge = None
        self._memo.clear()

    def __call__(self, match, record) -> int:
        return infer_severity(match, record, self._resolve)

    def _resolve(self, code: str) -> int | None:
        key = (self.family, code)
        memo = self._memo
        if key in memo:
            return memo[key]
        severity = None
        if self.family:
            try:
                if self._knowledge is None:
                    from dmslog.knowledge.resolve import Knowledge

                    self._knowledge = Knowledge(installations=self.installation)
                info = self._knowledge.error(self.family, code)
                severity = None if info is None else info.severity
            except Exception:  # a missing or broken pack must not stop the ingestion
                severity = None
        memo[key] = severity
        return severity


# --------------------------------------------------------------------------- the run


# Version of the payload extraction. A case written before the payloads existed (or by an older extraction)
# has records without payload rows, and unchanged sources are not read again, so the version is tracked PER
# SOURCE in ``meta["payload_extraction_version.<source_id>"]``: a source selected with a lower (or no) mark is
# re-indexed once; a source that is not selected keeps its old mark and is upgraded when it is selected.
# ``meta["payload_extraction_version"]`` only says "every indexed source is current".
# Version 2 added the payload keys (``entry_key`` origin 1, issue #67).
PAYLOAD_EXTRACTION_VERSION = 2
PAYLOAD_VERSION_KEY = "payload_extraction_version"
# (from, to) upgrades in which only the payload keys changed: the source is NOT reindexed, its keys are
# rewritten from the stored payloads (``IndexWriter.refresh_payload_keys``). Any other older mark reindexes.
KEY_ONLY_UPGRADES = frozenset({(1, 2)})


def _source_mark(conn, source_id: int) -> int:
    key = f"{PAYLOAD_VERSION_KEY}.{source_id}"
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _mark_source(conn, source_id: int) -> None:
    if _source_mark(conn, source_id) != PAYLOAD_EXTRACTION_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{PAYLOAD_VERSION_KEY}.{source_id}", str(PAYLOAD_EXTRACTION_VERSION)),
        )


def _store_global_version(conn) -> None:
    """Set the global marker when every indexed source carries the current mark."""
    stale = conn.execute(
        "SELECT 1 FROM source s WHERE s.n_entries > 0 AND coalesce((SELECT CAST(value AS INTEGER) FROM meta"
        " WHERE key = ? || '.' || s.id), 0) < ? LIMIT 1",
        (PAYLOAD_VERSION_KEY, PAYLOAD_EXTRACTION_VERSION),
    ).fetchone()
    if stale is None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (PAYLOAD_VERSION_KEY,)).fetchone()
        if row is None or row[0] != str(PAYLOAD_EXTRACTION_VERSION):
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                (PAYLOAD_VERSION_KEY, str(PAYLOAD_EXTRACTION_VERSION)),
            )


class _Run:
    def __init__(
        self, writer: IndexWriter, plan: CollectionPlan, options: IngestOptions, progress: ProgressFn | None
    ):
        self.w = writer
        self.plan = plan
        self.opt = options
        self.progress = progress
        self.result = IngestResult()
        self.bytes_total = 0
        self.bytes_done = 0
        self.sources_total = 0
        self.sources_done = 0
        self.current_member: str | None = None
        self.current_action: str | None = None
        self.catalog: Catalog = options.catalog if options.catalog is not None else default_catalog()
        self.severity = SeverityHook()
        self.stage: str | None = None
        self.pending: dict[str, int | None] = {}  # pending_bytes per member (None: unchanged)
        self.reset_members: set[str] = set()  # sources reset during the run (their rows must be read again)

    def _shown(self, text: str | None) -> str | None:
        """A name as presented or stored (progress, results): public spelling, control characters escaped."""
        return component(self.opt.aliases, text) if text else text

    def _exc_text(self, exc: BaseException) -> str:
        """``Class: detail`` of a failure. With local aliases the detail is the class-based fixed text:
        exception text cannot be proven free of (escaped) real names."""
        aliases = self.opt.aliases
        return error_label(exc, aliases) if aliases else f"{type(exc).__name__}: {exc}"

    def _detail(self, message: str) -> str:
        """Derivation/link messages (``Class: detail``) as presented: class name only with aliases."""
        if not self.opt.aliases:
            return message
        match = re.match(r"[A-Za-z_][\w.]*(?=:|$)", message)
        return match.group(0) if match else "error"

    def _warn(self, message: str) -> None:
        """Warnings are fixed text plus describe_error() output: names-free, never re-sanitized."""
        self.result.warnings.append(message)

    def emit(self, phase: str, source_bytes: int = 0, source_total: int = 0, source_records: int = 0) -> None:
        if self.progress is None:
            return
        self.progress(
            ProgressEvent(
                phase, self._shown(self.current_member), self.current_action,
                source_bytes, source_total, source_records,
                self.bytes_done,
                self.bytes_total, self.result.records, self.sources_done, self.sources_total, self.stage,
            )
        )  # fmt: skip

    # ---- derivation

    def derive(self) -> None:
        """Derive the events of the streams that changed (after the entries are committed).

        A failure never aborts the ingestion: it is recorded in ``result.warnings``.
        """
        from dmslog.derive.engine import derive_pending

        started = time.perf_counter()
        try:
            summary = derive_pending(self.w.conn, self.catalog)
        except Exception as exc:  # e.g. a locked database
            self.result.derive_seconds += time.perf_counter() - started
            self._warn(f"derivation failed: {self._exc_text(exc)}")
            return
        self.result.derive_seconds += time.perf_counter() - started
        self.result.events_derived += summary.events
        for stream_id, message in summary.errors:
            self._warn(f"derivation failed for stream {stream_id}: {self._detail(message)}")

    def correlate(self) -> None:
        """Correlate the groups that changed (after the derivation): normal<->sensitive pairing and, when
        the module exists, the callback/retry/response links (``dmslog.derive.links``).

        A failure never aborts the ingestion: it is recorded in ``result.warnings``.
        """
        import importlib

        from dmslog.derive.correlate import correlate_pending

        started = time.perf_counter()
        steps = [("correlation", correlate_pending)]
        try:
            links = importlib.import_module("dmslog.derive.links")
        except ModuleNotFoundError as exc:
            if exc.name != "dmslog.derive.links":
                self._warn(f"link rules failed: {self._exc_text(exc)}")
        except Exception as exc:  # a broken module must not abort the ingestion
            self._warn(f"link rules failed: {self._exc_text(exc)}")
        else:
            steps.append(("link rules", links.link_pending))
        for name, step in steps:
            try:
                summary = step(self.w.conn, self.catalog)
            except Exception as exc:  # e.g. a locked database
                self._warn(f"{name} failed: {self._exc_text(exc)}")
                continue
            self.result.links_created += summary.links
            for group, message in summary.errors:
                self._warn(f"{name} failed for group {group}: {self._detail(message)}")
        self.result.correlate_seconds += time.perf_counter() - started

    def link_payloads(self) -> None:
        """Link the payloads of the groups that changed (after derivation and correlation): announcement,
        operation and request/response pair (``dmslog.derive.payloads``).

        A failure never aborts the ingestion: it is recorded in ``result.warnings``.
        """
        from dmslog.derive.payloads import link_payloads

        started = time.perf_counter()
        self.result.payloads = self.w.payloads_written
        try:
            summary = link_payloads(self.w.conn, self.catalog)
        except Exception as exc:  # e.g. a locked database
            self.result.payload_seconds += time.perf_counter() - started
            self._warn(f"payload linking failed: {self._exc_text(exc)}")
            return
        self.result.payload_seconds += time.perf_counter() - started
        self.result.payloads_linked += summary.linked
        for group, message in summary.errors:
            self._warn(f"payload linking failed for group {group}: {self._detail(message)}")

    # ---- collection

    def resolve_collection(self) -> int:
        """The collection the run writes into: the one already in the case (same path, or the same ERS half
        moved elsewhere: the path is re-pointed), a new one in the group of its other half, or a new one in a
        group of its own (``ers.classify_add``, U21). Never refuses: duplicates are an API decision."""
        plan, w = self.plan, self.w
        path = str(plan.path.resolve())
        held = held_collections(w.collections())
        decision = classify_add(held, plan.kind, path, plan.identity)
        if decision.action == ADD_SAME and decision.collection_id is not None:
            current = next(c for c in held if c.id == decision.collection_id)
            if current.path != path:
                w.update_collection_path(current.id, path)
            return current.id
        ident = plan.identity
        return w.add_collection(
            kind=plan.kind,
            path=path,
            ers_guid=ident.guid if ident else None,
            ers_ts=ident.timestamp if ident else None,
            ers_variant=ident.variant if ident else None,
            ers_group=decision.group if decision.action == ADD_PAIR else None,
            system=plan.system,
            machine=plan.machine,
            label=plan.label,
        )

    # ---- installation configuration

    def store_installation(self, cid: int, selected: list[SourceInfo]) -> None:
        """Read the Registry of the collection once and store ``config_param`` for the in-scope products.

        In scope = products that configure a process among the selected sources (``WfmAdapter`` ->
        ``AdapterWfm``); other products (CoreSDK, ...) are not stored. A collection without a Registry
        (a plain Log folder, a loose file) stores nothing and leaves earlier rows alone.
        """
        if self.plan.kind == KIND_FILE:
            return
        processes = {s.process for s in selected if s.process}
        if not processes:
            return
        try:
            installation = read_installation(self.plan.path, aliases=self.opt.aliases)
        except (RegistryError, OSError, zipfile.BadZipFile):
            return
        self.severity.set_installation(installation)
        names = {p.product for p in installation.products}
        wanted = {product_for_process(proc, names) for proc in processes} - {None}
        rows = []
        for product in installation.products:
            if product.product in wanted:
                rows.extend(config_param_rows(product))
        self.w.replace_config_params(self.w.collection_group(cid), rows)
        self.w.commit()

    # ---- rotation

    def follow_rotations(self, cid: int, infos: dict[str, SourceInfo], reader: SourceReader) -> None:
        """Re-point sources whose content now lives under another name (``X.log`` -> ``X.log.0``).

        A source is recognized by the hash of its first bytes. The moved source keeps its records;
        the new file under the old name becomes a source of its own and is indexed from scratch.
        """
        if self.plan.kind == KIND_ERS_ZIP:
            return
        known = self.w.sources(cid)
        heads: dict[str, bytes] = {}

        def head_hash(info: SourceInfo, span: int) -> str:
            if info.member not in heads:
                heads[info.member] = reader.read_head(info)
            return sha1_hex(heads[info.member][:span])

        def still_there(row: SourceRow) -> bool:
            info = infos.get(row.member)
            return info is not None and head_hash(info, span_for(row.indexed_bytes)) == row.head_hash

        renames: dict[int, tuple[str, str, int | None]] = {}
        taken: set[str] = set()
        for row in known.values():
            if row.status not in ("ready", "indexing") or row.indexed_bytes <= 0 or not row.head_hash:
                continue
            if still_there(row):
                continue
            span = span_for(row.indexed_bytes)
            for cand in infos.values():
                if cand.member == row.member or cand.member in taken or cand.stream_key != row.stream_key:
                    continue
                other = known.get(cand.member)
                if other is not None and other.id != row.id and other.head_hash and still_there(other):
                    continue  # that name holds its own, unchanged source
                if head_hash(cand, span) == row.head_hash:
                    renames[row.id] = (cand.member, cand.file_name, cand.rotation)
                    taken.add(cand.member)
                    break
        for member in taken:  # a stale source under a destination name is dropped
            holder = known.get(member)
            if holder is not None and holder.id not in renames:
                self.w.delete_source_entries(holder.id)
                drop_state(self.w.conn, holder.id)
                self.w.conn.execute("DELETE FROM source WHERE id=?", (holder.id,))
        self.w.rename_sources(renames)
        self.w.commit()

    # ---- listing and stream rows

    def register_sources(self, cid: int, all_infos: list[SourceInfo], selected: set[str]) -> None:
        w = self.w
        group = w.collection_group(cid)
        known = w.sources(cid)
        for info in all_infos:
            row = known.get(info.member)
            if info.member in selected:
                stream_id = w.get_or_create_stream(
                    collection_group=group, family=info.family, process=info.process, pid=info.pid,
                    account=info.account, service=info.service, sensitive=info.sensitive,
                )  # fmt: skip
                if row is None:
                    w.add_source(
                        collection_id=cid, stream_id=stream_id, member=info.member, file_name=info.file_name,
                        rotation=info.rotation, envelope=info.envelope, size=None, mtime=info.mtime,
                        zip_crc=info.zip_crc, status="pending",
                    )  # fmt: skip
                elif row.stream_id != stream_id:
                    w.update_source(row.id, stream_id=stream_id)
                continue
            reason = self._skip_reason(info)
            if row is None:
                w.add_source(
                    collection_id=cid, stream_id=None, member=info.member, file_name=info.file_name,
                    rotation=info.rotation, envelope=info.envelope, size=info.size, mtime=info.mtime,
                    zip_crc=info.zip_crc, status="skipped", error=reason,
                )  # fmt: skip
            elif row.status == "skipped" and row.error != reason:
                w.update_source(row.id, error=reason)
        w.commit()

    @staticmethod
    def _skip_reason(info: SourceInfo) -> str:
        if info.limit_violation:
            return info.limit_violation
        if not info.indexable:
            return SKIP_NOT_INDEXABLE
        return SKIP_OUT_OF_SCOPE

    # ---- one source

    def process(self, row: SourceRow, info: SourceInfo, reader: SourceReader) -> None:
        self.current_member = info.member
        self.current_action = None
        state = load_state(self.w.conn, row.id)
        if state is not None:
            if row.status == "indexing":
                outcome = self._complete_partial(row, info, reader, state)
                if outcome == _DONE:
                    return
                # _ABANDONED: indexed again from scratch below; _GROWN: completed, its growth appended below
            else:  # failed since (status error): the regular decision reindexes it
                drop_state(self.w.conn, row.id)
                self.w.commit()
            row = self.w.sources(row.collection_id)[row.member]
            self.current_action = None
        try:
            action, key_only = self.plan_action(row, info, reader)
        except (OSError, zipfile.BadZipFile, ReadLimitError) as exc:
            self._fail(row, info, exc)
            return
        if key_only:
            # only the payload keys changed: rewrite them from the stored payloads, no reindex
            self.w.refresh_payload_keys(row.id)
            _mark_source(self.w.conn, row.id)
            self.w.commit()
        if action in (Action.NEW, Action.REINDEX):
            _mark_source(self.w.conn, row.id)  # from here on every record of it is extracted by this code
        self.current_action = action.value
        if action is Action.UNCHANGED:
            self.result.sources_unchanged += 1
            self.result.details.append(SourceResult(self._shown(info.member), action.value))
            self._finish_source(info, 0)
            return
        self.emit("source_start", 0, info.size)
        stats = BatchStats()
        try:
            fed = self._index(info, row, action, reader, stats)
        except (OSError, zipfile.BadZipFile, ReadLimitError) as exc:
            self.w.rollback()
            self._fail(row, info, exc)
            return
        except RecordTooLargeError as exc:
            # Batches committed before the oversized record stay (complete records only).
            self.w.rollback()
            self._fail(row, info, exc, reason=FAIL_RECORD_TOO_LARGE)
            return
        except BaseException:
            self.w.rollback()
            raise
        self.result.sources_indexed += 1
        self.result.records += stats.records
        self.result.lines += stats.lines
        self.result.bytes += fed
        self.result.details.append(
            SourceResult(self._shown(info.member), action.value, stats.records, stats.lines, fed)
        )
        self._finish_source(info, fed, stats.records)

    def plan_action(self, row: SourceRow, info: SourceInfo, reader: SourceReader) -> tuple[Action, bool]:
        """What to do with one source, WITHOUT writing: ``(action, key_only_upgrade)``.

        The single decision function: ``process`` acts on it and ``pending_bytes`` (the disk-space check)
        sizes it, so the plan and the run can never disagree. ``key_only_upgrade`` means the records are
        kept and only their payload keys are rewritten (the action stays UNCHANGED/APPEND/RESUME).
        """
        known = KnownSource(
            row.status, row.size, row.zip_crc, row.head_hash, row.indexed_bytes, row.tail_hash
        )
        action = self._decide(info, known, reader, info.container_kind == "zip")
        key_only = False
        if action in (Action.UNCHANGED, Action.APPEND, Action.RESUME):
            mark = _source_mark(self.w.conn, row.id)
            if (mark, PAYLOAD_EXTRACTION_VERSION) in KEY_ONLY_UPGRADES:
                key_only = True
            elif mark < PAYLOAD_EXTRACTION_VERSION:
                action = Action.REINDEX  # its records predate the current payload extraction
        if (
            action in (Action.APPEND, Action.RESUME)
            and row.envelope == PLAIN_ENVELOPE
            and row.indexed_bytes < DETECT_BYTES
        ):
            # picked as plain from a first, still tiny read (a live file): detect the envelope again
            action = Action.REINDEX
        if action in (Action.APPEND, Action.RESUME) and not is_known_envelope(row.envelope):
            action = Action.REINDEX  # its [[envelope]] was removed or renamed in families.toml
        return action, key_only

    def pending_bytes(
        self, selected: list[SourceInfo], rows: dict[str, SourceRow], reader: SourceReader
    ) -> int:
        """Bytes the run will add to the index, from ``plan_action`` of every source (nothing is written).
        The per-member detail stays in ``self.pending`` (None = unchanged), which the partial open uses."""
        self.pending = self.pending_by_member(selected, rows, reader)
        return sum(n for n in self.pending.values() if n)

    def pending_by_member(
        self, selected: list[SourceInfo], rows: dict[str, SourceRow], reader: SourceReader
    ) -> dict[str, int | None]:
        """Per member, the bytes the run will add to the index (None: unchanged, nothing to write).

        Nothing for an unchanged source, only the new tail for an append or a resume, the whole source for
        a new one, a re-index or a partial one. A source that cannot be read here counts as zero bytes of
        work (``process`` reports it).
        """
        out: dict[str, int | None] = {}
        for info in selected:
            row = rows.get(info.member)
            if row is None or load_state(self.w.conn, row.id) is not None:
                out[info.member] = info.size
                continue
            try:
                action, _key_only = self.plan_action(row, info, reader)
            except (OSError, zipfile.BadZipFile, ReadLimitError):
                out[info.member] = 0
                continue
            if action in (Action.APPEND, Action.RESUME):
                out[info.member] = max(info.size - row.indexed_bytes, 0)
            elif action is not Action.UNCHANGED:
                out[info.member] = info.size
            else:
                out[info.member] = None
        return out

    def _finish_source(self, info: SourceInfo, fed: int, records: int = 0) -> None:
        self.bytes_done += info.size
        self.sources_done += 1
        self.emit("source_done", fed, info.size, records)

    def _fail(self, row: SourceRow, info: SourceInfo, exc: Exception, reason: str | None = None) -> None:
        detail = describe_error(exc, self.opt.aliases)
        text = f"{reason}: {detail}" if reason else f"{type(exc).__name__}: {detail}"
        self.w.update_source(row.id, status="error", error=text[:500])
        drop_state(self.w.conn, row.id)  # an error is reindexed whole by the next run
        self.w.commit()
        self.result.sources_failed += 1
        self.result.details.append(SourceResult(self._shown(info.member), "error", error=text))
        self._finish_source(info, 0)

    @staticmethod
    def _file_path(info: SourceInfo) -> Path:
        if info.container_kind == "file":
            return info.container
        return info.container.joinpath(*info.member.split("/"))

    def _decide(self, info: SourceInfo, known: KnownSource, reader: SourceReader, is_zip: bool) -> Action:
        started = known.status in ("ready", "indexing")
        utf16 = started and not is_zip and reader.is_utf16(info)
        span = span_for(known.indexed_bytes)

        def head_matches() -> bool:
            return sha1_hex(reader.read_head(info)[:span]) == known.head_hash

        def tail_matches() -> bool:
            data = reader.read_raw_range(info, known.indexed_bytes - span, span)
            return len(data) == span and sha1_hex(data) == known.tail_hash

        size = info.size
        if started and not is_zip:
            size = self._file_path(info).stat().st_size  # the file may have grown since the listing
        return decide_action(
            known, is_zip=is_zip, utf16=utf16, size=size, zip_crc=info.zip_crc,
            head_matches=head_matches, tail_matches=tail_matches,
        )  # fmt: skip

    def _index(
        self, info: SourceInfo, row: SourceRow, action: Action, reader: SourceReader, stats: BatchStats
    ) -> int:
        """Parse and store one source; returns the stream bytes fed in this run."""
        w = self.w
        is_zip = info.container_kind == "zip"
        fixed_size = is_zip or reader.is_utf16(info)  # size cannot be derived from the stream position
        w.begin()
        start_off, start_line, prev_ts = 0, 1, None
        envelope = None
        head = HeadWindow()
        tail = TailWindow()
        if action is Action.REINDEX:
            w.delete_source_entries(row.id)
        elif action in (Action.APPEND, Action.RESUME):
            last = w.last_entry(row.id)
            if last is not None:
                w.truncate_from(row.id, last.byte_off, min_id=last.id)
                start_off, start_line = last.byte_off, last.line_no
                prev_ts = w.last_ts(row.id)
                envelope = get_envelope(row.envelope)
                head = HeadWindow(reader.read_head(info))  # the head of the file is already known to match
        self.severity.family = info.family
        w.start_source(
            row.id, prev_ts, self.catalog.for_family(info.family, sensitive=info.sensitive), info.family
        )

        read_from = start_off - min(start_off, HASH_SPAN)  # bytes before the cut only feed the tail hash
        skip = start_off - read_from
        max_rec = self.opt.max_record_bytes
        grouper = (
            RecordGrouper(envelope, start_off=start_off, start_line=start_line, max_record_bytes=max_rec)
            if envelope
            else None
        )
        fed = start_off  # position in the stream of the bytes fed so far
        batch_start = fed
        pending: list[bytes] = []
        pending_len = 0

        def commit(status: str) -> None:
            nonlocal batch_start
            assert grouper is not None
            w.flush()
            n, first_ts, last_ts = w.source_totals(row.id)
            w.update_source(
                row.id, indexed_bytes=fed, tail_hash=tail.hexdigest(),
                head_hash=head.hexdigest(span_for(fed)),
                n_entries=n, first_ts=first_ts, last_ts=last_ts, status=status, error=None,
                envelope=grouper.envelope.name, mtime=info.mtime, zip_crc=info.zip_crc,
                size=info.size if fixed_size else fed,
            )  # fmt: skip
            w.commit()
            batch_start = fed

        chunks = reader.iter_chunks(info, read_from, self.opt.chunk_size)
        try:
            for chunk in chunks:
                if skip:
                    cut = chunk[:skip]
                    tail.update(cut)
                    chunk = chunk[len(cut) :]
                    skip -= len(cut)
                    if not chunk:
                        continue
                if grouper is None:
                    pending.append(chunk)
                    pending_len += len(chunk)
                    if pending_len < DETECT_BYTES and sum(c.count(b"\n") for c in pending) < DETECT_LINES:
                        continue
                    chunk = b"".join(pending)
                    pending.clear()
                    grouper = RecordGrouper(detect_envelope(chunk), max_record_bytes=max_rec)
                head.update(chunk)
                tail.update(chunk)
                fed += len(chunk)
                w.append(row.id, grouper.feed(chunk), stats)
                if fed - batch_start >= self.opt.batch_bytes or w.buffered >= MAX_BATCH_RECORDS:
                    commit("indexing")
                    w.begin()
                    self.emit("progress", fed, info.size, stats.records)
        finally:
            chunks.close()
        if grouper is None:  # source shorter than the detection size (possibly empty)
            data = b"".join(pending)
            grouper = RecordGrouper(
                detect_envelope(data) if data else get_envelope(info.envelope), max_record_bytes=max_rec
            )
            head.update(data)
            tail.update(data)
            fed += len(data)
            w.append(row.id, grouper.feed(data), stats)
        # Resuming always re-parses the last record, so a live file needs no special flag: a record is
        # partial only when the data ends without a newline.
        w.append(row.id, grouper.finish(final=True), stats)
        commit("ready")
        return fed - start_off

    # ---- partial open (ROADMAP 9.9, ingest/partial.py)

    def process_first(self, row: SourceRow, info: SourceInfo, reader: SourceReader, first: FirstPass) -> bool:
        """First pass of a partial open for one source; True when the source is left incomplete (window only).

        Only a source never indexed is opened partially; any other one (already indexed, grown, failed) goes
        through the regular ``process``, as does a source whose window is the whole stream.
        """
        self.current_member = info.member
        self.current_action = None
        w = self.w
        if load_state(w.conn, row.id) is not None and row.status == "indexing":
            # an earlier partial open already holds its window; the second pass completes it
            self.current_action = ACTION_PARTIAL
            self.result.details.append(SourceResult(self._shown(info.member), ACTION_PARTIAL))
            self._finish_source(info, 0)
            return True
        if not first.windowed or row.status not in ("pending", "skipped"):
            self.process(row, info, reader)
            return False
        try:
            span = locate_window(reader, info, first.ts_from, first.ts_to, self.opt.chunk_size)
        except (OSError, zipfile.BadZipFile, ReadLimitError) as exc:
            self._fail(row, info, exc)
            return False
        if span.whole:
            self.process(row, info, reader)
            return False
        fixed_size = info.container_kind == "zip" or reader.is_utf16(info)
        w.set_next_id(next_row_id(w.conn, "entry"), on_top=True)
        base = w.next_id
        # the reservation (``end`` exclusive, and every id below it) must fit SQLite ids: else index it whole,
        # before anything of it is written
        if base + span.lines + 1 > INT64_MAX:
            self.process(row, info, reader)
            return False
        state = PartialState(
            segment=SEG_WINDOW, base=base, win=base + span.lo_line - 1, end=base + span.lines + 1,
            lo=span.lo, lo_line=span.lo_line, hi=span.hi, hi_line=span.hi_line, size=span.size,
            raw_size=info.size, zip_crc=info.zip_crc, head_hash=span.head_hash, envelope=span.envelope,
        )  # fmt: skip
        w.begin()
        w.reserve_entry_ids(state.end)
        save_state(w.conn, row.id, state)
        w.update_source(
            row.id, status="indexing", error=None, envelope=span.envelope, head_hash=None, tail_hash=None,
            indexed_bytes=0, size=info.size if fixed_size else span.size, mtime=info.mtime,
            zip_crc=info.zip_crc,
        )  # fmt: skip
        _mark_source(w.conn, row.id)  # every record of it is extracted by this code
        w.commit()
        self.current_action = ACTION_WINDOW
        self.emit("source_start", 0, info.size)
        stats = BatchStats()
        try:
            fed = self._run_segments(row, info, reader, state, stats, stop_after=SEG_WINDOW)
        except _RangeOverflow:
            self._abandon_partial(row, info)
            self.process(w.sources(row.collection_id)[row.member], info, reader)
            return False
        except (OSError, zipfile.BadZipFile, ReadLimitError, RecordTooLargeError) as exc:
            w.rollback()
            reason = FAIL_RECORD_TOO_LARGE if isinstance(exc, RecordTooLargeError) else None
            self._fail(row, info, exc, reason=reason)
            return False
        except BaseException:
            w.rollback()
            raise
        finally:
            w.set_next_id(next_row_id(w.conn, "entry"), on_top=True)
        self.result.records += stats.records
        self.result.lines += stats.lines
        self.result.bytes += fed
        self.result.details.append(
            SourceResult(self._shown(info.member), ACTION_WINDOW, stats.records, stats.lines, fed)
        )
        self._finish_source(info, fed, stats.records)
        return True

    def _complete_partial(
        self, row: SourceRow, info: SourceInfo, reader: SourceReader, state: PartialState
    ) -> str:
        """Second pass for a source indexed out of order: the rest of its segments, then a normal source.

        ``_DONE``; ``_GROWN`` when a live file grew after its window was located (completed up to that size,
        the caller appends the rest the regular way); ``_ABANDONED`` when the source changed: its records and
        state are dropped and the caller indexes it the regular way.
        """
        w = self.w
        try:
            valid = self._partial_valid(info, state, reader)
        except (OSError, zipfile.BadZipFile, ReadLimitError):
            valid = False
        if not valid:
            self._abandon_partial(row, info)
            return _ABANDONED
        self.current_action = ACTION_COMPLETE
        self.emit("source_start", 0, info.size)
        stats = BatchStats()
        # the prefix (and the suffix of an older file of the chain) get ids below records the derivation has
        # seen: the chain is derived again from its start (engine.derive_stream: no record = from scratch)
        w.begin()
        if row.stream_id is not None:
            from dmslog.derive.engine import META_PREFIX, chain_of

            w.conn.execute(
                "DELETE FROM meta WHERE key = ?",
                (f"{META_PREFIX}{row.stream_id}.{chain_of(info.file_name)}",),
            )
        try:
            fed = self._run_segments(row, info, reader, state, stats, stop_after=None)
        except _RangeOverflow:
            self._abandon_partial(row, info)
            return _ABANDONED
        except (OSError, zipfile.BadZipFile, ReadLimitError, RecordTooLargeError) as exc:
            w.rollback()
            reason = FAIL_RECORD_TOO_LARGE if isinstance(exc, RecordTooLargeError) else None
            self._fail(row, info, exc, reason=reason)
            return _DONE
        except BaseException:
            w.rollback()
            raise
        finally:
            w.set_next_id(next_row_id(w.conn, "entry"), on_top=True)
        self.result.records += stats.records
        self.result.lines += stats.lines
        self.result.bytes += fed
        self.result.details.append(
            SourceResult(self._shown(info.member), ACTION_COMPLETE, stats.records, stats.lines, fed)
        )
        if info.container_kind != "zip" and self._file_path(info).stat().st_size > state.raw_size:
            # the regular append takes the rest (and reports the source as done); its records get ids above
            # sources indexed before and collections added meanwhile: the family's unknown_sig examples are
            # set again at the end
            _remember_reorder(w, row.collection_id, {info.family})
            w.commit()
            return _GROWN
        self.result.sources_indexed += 1
        self._finish_source(info, fed, stats.records)
        return _DONE

    def _partial_valid(self, info: SourceInfo, state: PartialState, reader: SourceReader) -> bool:
        """The source still has the content its window came from (same member, or a file that only grew)."""
        if info.container_kind == "zip":
            return info.zip_crc == state.zip_crc and info.size == state.raw_size
        size = self._file_path(info).stat().st_size
        if reader.is_utf16(info):
            if size != state.raw_size:
                return False
        elif size < state.size:
            return False
        if head_hash_of(reader, info, state.size) != state.head_hash:
            return False
        # every byte range whose records are kept must still hold the same bytes (not only the head)
        return all(
            hash_stream(reader, info, start, end, self.opt.chunk_size) == digest
            for start, end, digest in state.hashes.values()
            if end > start
        )

    def _abandon_partial(self, row: SourceRow, info: SourceInfo) -> None:
        """Drop the records and the state of a partial source; it becomes a source never indexed.

        It is indexed again with new (higher) ids, so the newer files of its rotation chain that already hold
        records are reset too and indexed after it (ids must grow along the chain); the run picks them up
        (``reset_members``). Its family is remembered for the ``unknown_sig`` example repair (``both``).
        """
        from dmslog.derive.engine import chain_of

        w = self.w
        w.rollback()
        victims = [row.id]
        if row.stream_id is not None:
            chain = chain_of(info.file_name)
            newer = (
                (lambda r: r is None) if row.rotation is None else (lambda r: r is None or r < row.rotation)
            )
            for sid, member, file_name, rotation, n in w.conn.execute(
                "SELECT id, member, file_name, rotation, n_entries FROM source"
                " WHERE stream_id = ? AND id <> ?",
                (row.stream_id, row.id),
            ).fetchall():
                if chain_of(file_name) == chain and newer(rotation) and (n or load_state(w.conn, sid)):
                    victims.append(sid)
                    self.reset_members.add(member)
        for sid in victims:
            w.delete_source_entries(sid)
            drop_state(w.conn, sid)
            w.update_source(
                sid, status="pending", error=None, indexed_bytes=0, head_hash=None, tail_hash=None,
                n_entries=0, first_ts=None, last_ts=None,
            )  # fmt: skip
        _remember_reorder(w, row.collection_id, {info.family})
        w.commit()
        w.set_next_id(next_row_id(w.conn, "entry"), on_top=True)

    def _run_segments(
        self,
        row: SourceRow,
        info: SourceInfo,
        reader: SourceReader,
        state: PartialState,
        stats: BatchStats,
        stop_after: str | None,
    ) -> int:
        """Index the segments of a partial source from ``state.segment`` on (until ``stop_after``, included);
        returns the stream bytes fed. Every segment end is committed with the next state."""
        fed_total = 0
        order = (SEG_WINDOW, SEG_PREFIX, SEG_SUFFIX)
        while True:
            seg = state.segment
            fed, tail = self._fill(row, info, reader, state, seg, stats)
            fed_total += fed
            w = self.w
            conn = w.conn
            if seg == SEG_WINDOW:
                state.segment = SEG_PREFIX
            elif seg == SEG_PREFIX:
                self._fix_order_flag(row.id, state.win)
                top = conn.execute("SELECT max(id) FROM entry WHERE source_id = ?", (row.id,)).fetchone()[0]
                state.sfx = max(state.win, (top or 0) + 1)
                state.segment = SEG_SUFFIX
            else:
                self._finish_partial(row, info, reader, state, tail)
                w.commit()
                return fed_total
            save_state(conn, row.id, state)
            w.commit()
            if stop_after is not None and order.index(seg) >= order.index(stop_after):
                return fed_total

    def _fill(
        self,
        row: SourceRow,
        info: SourceInfo,
        reader: SourceReader,
        state: PartialState,
        seg: str,
        stats: BatchStats,
    ) -> tuple[int, TailWindow]:
        """Parse one segment of a partial source into its id range; resumes after its last stored record.

        Leaves the last batch uncommitted (the caller commits it with the next state). Raises
        ``_RangeOverflow`` when the records do not fit the reserved range (the file changed under us).
        """
        w = self.w
        conn = w.conn
        if seg == SEG_WINDOW:
            start, line, stop, lo_id, hi_id = state.lo, state.lo_line, state.hi, state.win, state.end
        elif seg == SEG_PREFIX:
            start, line, stop, lo_id, hi_id = 0, 1, state.lo, state.base, state.win
        else:
            assert state.sfx is not None
            start, line, stop, lo_id, hi_id = state.hi, state.hi_line, state.size, state.sfx, state.end
        w.begin()
        last = conn.execute(
            "SELECT id, byte_off, line_no FROM entry WHERE source_id = ? AND id >= ? AND id < ?"
            " ORDER BY id DESC LIMIT 1",
            (row.id, lo_id, hi_id),
        ).fetchone()
        next_id = lo_id
        if last is not None:  # resume: its last record may be incomplete, parse it again
            w.delete_id_range(row.id, last[0], hi_id, last[1], max(stop, last[1] + 1))
            start, line, next_id = last[1], last[2], last[0]
        w.set_next_id(next_id, on_top=False)
        prev = conn.execute(
            "SELECT ts FROM entry WHERE source_id = ? AND id < ? AND ts IS NOT NULL ORDER BY id DESC LIMIT 1",
            (row.id, next_id),
        ).fetchone()
        self.severity.family = info.family
        w.start_source(
            row.id, prev[0] if prev else None, self.catalog.for_family(info.family, sensitive=info.sensitive),
            info.family,
        )  # fmt: skip
        fixed_size = info.container_kind == "zip" or reader.is_utf16(info)
        grouper = RecordGrouper(
            get_envelope(state.envelope),
            start_off=start,
            start_line=line,
            max_record_bytes=self.opt.max_record_bytes,
        )
        tail = TailWindow()
        seg_start = {SEG_WINDOW: state.lo, SEG_PREFIX: 0, SEG_SUFFIX: state.hi}[seg]
        # files: the bytes of the segment are hashed from its start (a resume reads its stored part again) so
        # that the next run can check them before it reuses the records; zip members have their CRC
        hashing = info.container_kind != "zip"
        digest = hashlib.sha1()
        base_from = seg_start if hashing else start
        read_from = base_from - min(base_from, HASH_SPAN) if seg == SEG_SUFFIX else base_from
        fed = batch_start = start

        def covered() -> int:
            if seg == SEG_WINDOW:
                return fed - state.lo
            if seg == SEG_PREFIX:
                return state.hi - state.lo + fed
            return fed

        def commit() -> None:
            w.flush()
            if hashing:
                state.hashes[seg] = [seg_start, fed, digest.copy().hexdigest()]
                save_state(conn, row.id, state)
            n, first_ts, last_ts = w.source_totals(row.id)
            w.update_source(
                row.id, indexed_bytes=covered(), n_entries=n, first_ts=first_ts, last_ts=last_ts,
                status="indexing", error=None, envelope=state.envelope, mtime=info.mtime,
                zip_crc=info.zip_crc, size=info.size if fixed_size else state.size,
            )  # fmt: skip

        if start < stop:
            chunks = reader.iter_chunks(info, read_from, self.opt.chunk_size)
            pos = read_from
            try:
                for chunk in chunks:
                    if pos + len(chunk) > stop:
                        chunk = chunk[: stop - pos]
                    end = pos + len(chunk)
                    tail.update(chunk)  # bytes before the segment only feed the tail hash
                    if hashing and end > seg_start:
                        digest.update(chunk[max(seg_start - pos, 0) :])
                    if end <= start:  # the part of a resumed segment that is already stored
                        pos = end
                        if pos >= stop:
                            break
                        continue
                    part = chunk[max(start - pos, 0) :]
                    pos = fed = end
                    w.append(row.id, grouper.feed(part), stats)
                    if w.next_id > hi_id:
                        raise _RangeOverflow
                    if fed >= stop:
                        break
                    if fed - batch_start >= self.opt.batch_bytes or w.buffered >= MAX_BATCH_RECORDS:
                        commit()
                        w.commit()
                        w.begin()
                        batch_start = fed
                        self.emit("progress", covered(), info.size, stats.records)
            finally:
                chunks.close()
        elif seg == SEG_SUFFIX and start > read_from:  # empty suffix: the tail hash needs the bytes before it
            tail.update(_stream_range(reader, info, read_from, start - read_from, self.opt.chunk_size))
        if fed < stop:
            raise _RangeOverflow  # the stream ended before the segment: the source changed
        w.append(row.id, grouper.finish(final=True), stats)
        if w.next_id > hi_id:
            raise _RangeOverflow
        commit()
        return fed - start, tail

    def _fix_order_flag(self, source_id: int, follower_from: int) -> None:
        """Flag 32 (time goes back) of the first timed record at or after id ``follower_from``: it was written
        without the record before it (the prefix came later)."""
        conn = self.w.conn
        first = conn.execute(
            "SELECT id, ts FROM entry WHERE source_id = ? AND id >= ? AND ts IS NOT NULL ORDER BY id LIMIT 1",
            (source_id, follower_from),
        ).fetchone()
        if first is None:
            return
        prev = conn.execute(
            "SELECT ts FROM entry WHERE source_id = ? AND id < ? AND ts IS NOT NULL ORDER BY id DESC LIMIT 1",
            (source_id, first[0]),
        ).fetchone()
        back = prev is not None and first[1] < prev[0]
        conn.execute(
            f"UPDATE entry SET flags = CASE WHEN ? THEN flags | {FLAG_TS_OUT_OF_ORDER}"
            f" ELSE flags & ~{FLAG_TS_OUT_OF_ORDER} END WHERE id = ?",
            (int(back), first[0]),
        )

    def _finish_partial(
        self, row: SourceRow, info: SourceInfo, reader: SourceReader, state: PartialState, tail: TailWindow
    ) -> None:
        """Every segment is in: the source becomes a regular one (as if indexed in one go up to ``size``)."""
        w = self.w
        fixed_size = info.container_kind == "zip" or reader.is_utf16(info)
        n, first_ts, last_ts = w.source_totals(row.id)
        w.update_source(
            row.id, indexed_bytes=state.size, tail_hash=tail.hexdigest(), head_hash=state.head_hash,
            n_entries=n, first_ts=first_ts, last_ts=last_ts, status="ready", error=None,
            envelope=state.envelope, mtime=info.mtime, zip_crc=info.zip_crc,
            size=info.size if fixed_size else state.size,
        )  # fmt: skip
        drop_state(w.conn, row.id)


INT64_MAX = 2**63 - 1  # largest SQLite rowid
_DONE, _GROWN, _ABANDONED = "done", "grown", "abandoned"  # outcomes of _Run._complete_partial


class _RangeOverflow(Exception):
    """The records of a partial source do not fit its reserved ids (its content changed): start it over."""


def _stream_range(reader: SourceReader, info: SourceInfo, start: int, length: int, chunk_size: int) -> bytes:
    """``length`` bytes of the UTF-8 stream of ``info`` from ``start`` (zip members, transcoded files)."""
    out = bytearray()
    chunks = reader.iter_chunks(info, start, chunk_size)
    try:
        for chunk in chunks:
            out += chunk
            if len(out) >= length:
                break
    finally:
        chunks.close()
    return bytes(out[:length])


def _oldest_first(selected: list[SourceInfo]) -> list[SourceInfo]:
    """Order the sources so that the files of one stream come oldest first (``.log.N`` ... ``.log``).

    Entry ids then grow along the stream order, which the derivation checkpoint relies on. Streams keep
    the position of their first source; the order of the streams themselves is unchanged.
    """
    first: dict[tuple, int] = {}
    for i, info in enumerate(selected):
        first.setdefault(info.stream_key, i)
    return sorted(
        selected,
        key=lambda s: (
            first[s.stream_key],
            s.rotation is None,
            -(s.rotation if s.rotation is not None else 0),
        ),
    )


def _bump_ingest_generation(conn) -> None:
    """Increment ``meta.ingest_generation`` (readers use it to invalidate per-case caches)."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'ingest_generation'").fetchone()
    generation = int(row[0]) + 1 if row and str(row[0]).isdigit() else 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('ingest_generation', ?)", (str(generation),)
    )
    conn.commit()


def _check_free_space(conn, bytes_to_index: int) -> None:
    """Refuse to index when the case database's disk cannot hold it (in-memory databases are skipped)."""
    row = next((r for r in conn.execute("PRAGMA database_list") if r[1] == "main"), None)
    if row is None or not row[2]:
        return
    check_free_space(Path(row[2]).parent, bytes_to_index)


def _first_members(order: list[SourceInfo], first: FirstPass, shown) -> tuple[list[SourceInfo], list[str]]:
    """The sources of the first pass, in processing order: the requested ones and their rotation chains
    (ids must grow along a chain); plus the requested names that match no selected source."""
    if first.members is None:
        return list(order), []
    from dmslog.derive.engine import chain_of

    wanted = set(first.members)
    hit: set[str] = set()
    chains: set[tuple] = set()
    for info in order:
        names = {info.member, shown(info.member)}
        if names & wanted:
            hit |= names & wanted
            chains.add((info.stream_key, chain_of(info.file_name)))
    picked = [info for info in order if (info.stream_key, chain_of(info.file_name)) in chains]
    return picked, sorted(wanted - hit)


def _reorder_families(order: list[SourceInfo], picked: list[SourceInfo], work: set[str]) -> set[str]:
    """Families whose ``unknown_sig`` examples a sources-first open may misplace inside the collection (only
    sources this run writes count): a picked source comes after a source of the same family that is indexed
    later, so it gets lower ids. Across collections nothing is needed: the writer keeps the example of the
    earliest collection whatever the ids (``writer._UNKNOWN_SQL``)."""
    chosen = {i.member for i in picked}
    both: set[str] = set()
    later: set[str] = set()
    for info in order:
        if info.member not in work:
            continue
        if info.member not in chosen:
            later.add(info.family)
        elif info.family in later:
            both.add(info.family)
    return both


def _remember_reorder(writer: IndexWriter, cid: int, families: set[str]) -> None:
    """Merge families into ``meta['partial_order.<cid>']`` (in the caller's transaction)."""
    if not families:
        return
    key = f"{PARTIAL_ORDER_PREFIX}{cid}"
    value = json.dumps(sorted(_load_reorder(writer.conn, cid) | set(families)))
    writer.begin()
    writer.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def _load_reorder(conn, cid: int) -> set[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"{PARTIAL_ORDER_PREFIX}{cid}",)).fetchone()
    try:
        data = json.loads(row[0]) if row else []
        if isinstance(data, dict):  # an older draft stored {"both": [...], "rest": [...]}
            data = data.get("both", [])
        return {str(f) for f in data}
    except (TypeError, ValueError, AttributeError):
        return set()


def _repair_unknown_first(writer: IndexWriter, cid: int, order: list[SourceInfo]) -> None:
    """``unknown_sig.first_entry_id`` as a full indexing sets it inside this collection (the first record of
    the signature in the regular source order), for the families ``_remember_reorder`` noted; then forget
    them. An example in an earlier collection stays (collection precedence, as in the writer)."""
    conn = writer.conn
    key = f"{PARTIAL_ORDER_PREFIX}{cid}"
    if conn.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is None:
        return
    families = _load_reorder(conn, cid)
    rows = writer.sources(cid)
    first: dict[tuple[str, str], int] = {}
    for info in order:
        if info.family not in families or info.member not in rows:
            continue
        for eid, msg in conn.execute(
            "SELECT id, msg FROM entry WHERE source_id = ? AND tpl_id = 0 ORDER BY id",
            (rows[info.member].id,),
        ):
            sig = cached_signature(msg)
            if sig:
                first.setdefault((info.family, sig), eid)
    writer.begin()
    for (family, sig), eid in first.items():
        conn.execute(
            "UPDATE unknown_sig SET first_entry_id = ? WHERE family = ? AND signature = ?"
            " AND (SELECT s.collection_id FROM entry e JOIN source s ON s.id = e.source_id"
            " WHERE e.id = unknown_sig.first_entry_id) >= ? AND first_entry_id <> ?",
            (eid, family, sig, cid, eid),
        )
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    writer.commit()


def _finish_pass(run: _Run, conn, changes_before: int) -> None:
    _store_global_version(conn)
    run.derive()
    run.correlate()
    run.link_payloads()
    if conn.total_changes != changes_before:  # a no-op re-ingest keeps the generation (and the caches)
        _bump_ingest_generation(conn)


def _left_incomplete(conn, cid: int) -> int:
    from dmslog.ingest.partial import partial_source_ids

    partial = partial_source_ids(conn)
    return sum(
        1
        for sid, status in conn.execute("SELECT id, status FROM source WHERE collection_id = ?", (cid,))
        if sid in partial or status in ("pending", "indexing")
    )


def run_ingest(
    conn,  # sqlite3.Connection of a migrated case database
    path: str | Path,
    options: IngestOptions | None = None,
    progress: ProgressFn | None = None,
) -> IngestResult:
    """Index (or re-index) ``path`` into the case database ``conn``.

    A case groups collections (U21): another ERS, folder or file becomes a collection group of its own,
    the other half of a held capture joins its group. Raises ``UnsafeArchiveError`` for unsafe zips,
    ``SourceNotFoundError`` for missing paths and
    ``InsufficientDiskSpaceError`` (a ``ReadLimitError``) when the disk is too full to index.

    With ``options.first`` (partial open, ROADMAP 9.9) the run has two passes: the first indexes the chosen
    window/sources, derives and correlates them and emits ``first_ready``; the second completes everything
    (skipped with ``defer_rest``). Interrupted anywhere, a later run of the same path continues.
    """
    options = options or IngestOptions()
    if options.scope not in (SCOPE_DEFAULT, SCOPE_ALL):
        raise ValueError(f"scope must be 'default' or 'all', got {options.scope!r}")
    t0 = time.perf_counter()
    changes_before = conn.total_changes
    plan = build_plan(path, options.limits, options.aliases)
    run_hook = SeverityHook()
    writer = IndexWriter(conn, severity=generic_severity, infer_severity=run_hook)
    writer.init_meta(options.case_name or default_case_name(plan), __version__)
    run = _Run(writer, plan, options, progress)
    run.severity = run_hook
    writer.sync_templates(run.catalog)
    writer.commit()
    cid = run.resolve_collection()
    if (
        options.first is not None
        and not conn.execute("SELECT 1 FROM source WHERE collection_id = ? LIMIT 1", (cid,)).fetchone()
    ):  # a new collection: its partial open is visible to pending_work from its first commit on
        writer.begin()
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{SCOPE_KEY_PREFIX}{cid}", options.scope),
        )
    writer.commit()
    run.result.collection_id = cid
    selected = select_sources(plan.sources, options.scope)
    selected_members = {s.member for s in selected}
    run.result.sources_listed = len(plan.sources)
    run.result.sources_selected = len(selected)
    run.result.sources_skipped = len(plan.sources) - len(selected)
    run.bytes_total = sum(s.size for s in selected)
    run.sources_total = len(selected)
    first = options.first
    with SourceReader(options.limits) as reader:
        by_member = {s.member: s for s in plan.sources}
        # refuse BEFORE writing any source state, so a refusal leaves the case as it was
        _check_free_space(conn, run.pending_bytes(selected, writer.sources(cid), reader))
        pending = run.pending
        order = canonical = _oldest_first(selected)
        done: set[str] = set()
        if first is not None:
            picked, run.result.first_unmatched = _first_members(order, first, run._shown)
            work = {m for m, n in pending.items() if n is not None}
            if (
                work
            ):  # before anything that writes or can be interrupted: pending_work must see the collection
                writer.begin()
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    (f"{SCOPE_KEY_PREFIX}{cid}", options.scope),
                )
                _remember_reorder(writer, cid, _reorder_families(order, picked, work))
                writer.commit()
        run.follow_rotations(cid, by_member, reader)
        run.register_sources(cid, list(plan.sources), selected_members)
        run.store_installation(cid, list(selected))
        rows = writer.sources(cid)
        if first is not None:
            run.stage = STAGE_FIRST
            run.sources_total = run.result.first_sources = len(picked)
            run.bytes_total = sum(s.size for s in picked)
            for info in picked:
                if info.member in run.reset_members:  # reset by an abandoned older file of its chain
                    rows = writer.sources(cid)
                    run.reset_members.discard(info.member)
                    done.discard(info.member)
                if not run.process_first(rows[info.member], info, reader, first):
                    done.add(info.member)
            _finish_pass(run, conn, changes_before)
            changes_before = conn.total_changes
            run.result.first_seconds = time.perf_counter() - t0
            run.result.first_records = run.result.records
            run.current_member = None
            run.emit("first_ready")
            rest = [info for info in order if info.member not in done or info.member in run.reset_members]
            run.stage = STAGE_REST
            run.sources_total, run.sources_done = len(rest), 0
            run.bytes_total, run.bytes_done = sum(s.size for s in rest), 0
            if first.defer_rest:
                run.result.deferred = True
                order = []
            else:
                order = rest
            rows = writer.sources(cid)
        queued = {info.member for info in order}
        for info in canonical:
            # a source reset by an abandoned older file of its chain is indexed again, even if this run had
            # already finished it (it comes later in the chain order, so the loop has not passed it yet)
            if info.member in run.reset_members:
                rows = writer.sources(cid)
                run.reset_members.discard(info.member)
            elif info.member not in queued:
                continue
            run.process(rows[info.member], info, reader)
    if not _left_incomplete(conn, cid):
        _repair_unknown_first(writer, cid, canonical)
    if first is None or not first.defer_rest:
        _finish_pass(run, conn, changes_before)
    run.result.partial_left = _left_incomplete(conn, cid)
    if (
        not run.result.partial_left
        and conn.execute("SELECT 1 FROM meta WHERE key = ?", (f"{SCOPE_KEY_PREFIX}{cid}",)).fetchone()
    ):
        writer.begin()
        conn.execute("DELETE FROM meta WHERE key = ?", (f"{SCOPE_KEY_PREFIX}{cid}",))
        writer.commit()
    run.result.seconds = time.perf_counter() - t0
    run.current_member = None
    run.emit("done")
    return run.result


def resume_pending(
    conn, options: IngestOptions | None = None, progress: ProgressFn | None = None
) -> list[IngestResult]:
    """Complete what partial opens left incomplete (``partial.pending_work``): one ``run_ingest`` per
    collection, with the scope it was opened with. Returns the results (empty: nothing pending)."""
    from dataclasses import replace

    options = options or IngestOptions()
    out: list[IngestResult] = []
    for pending in pending_work(conn):
        run_options = replace(options, scope=pending.scope, first=None)
        out.append(run_ingest(conn, pending.path, run_options, progress))
    return out


# --------------------------------------------------------------------------- preview (partial open UI)

PREVIEW_PROBE_BYTES = 64 * 1024  # bytes read at the start (and, for files, at the end) of a source


@dataclass(frozen=True)
class SourcePreview:
    """A source as the partial open chooser lists it before anything is indexed (``preview_sources``)."""

    member: str  # as presented (local aliases applied): what ``FirstPass.members`` accepts
    name: str  # file name only
    family: str
    process: str | None
    pid: int | None
    sensitive: bool
    rotation: int | None
    size: int
    selected: bool  # indexed with the requested scope
    indexable: bool
    first_ts: int | None  # first timed header in the first 64 KiB (None: none found or unreadable)
    last_ts: int | None  # last timed header in the last 64 KiB; files only (zip members: None, not read)


def _timed_headers(data: bytes, envelope) -> list[int]:
    scan = getattr(envelope, "scan", None)
    if scan is None:
        return []
    return [h.ts_ms for _off, h in scan(data, 0, len(data)) if h.ts_ms is not None]


def preview_sources(
    path: str | Path,
    scope: str = SCOPE_DEFAULT,
    limits: ReadLimits | None = None,
    aliases: Aliases | None = None,
) -> list[SourcePreview]:
    """List the sources of ``path`` with their sizes and time range, reading at most 64 KiB at the start of
    each one (and 64 KiB at the end of a plain file): what the UI needs to choose a window and the sources
    to index first. Writes nothing. Raises like ``build_plan`` for missing or unsafe inputs."""
    if scope not in (SCOPE_DEFAULT, SCOPE_ALL):
        raise ValueError(f"scope must be 'default' or 'all', got {scope!r}")
    limits = limits or ReadLimits()
    plan = build_plan(path, limits, aliases)
    selected = {s.member for s in select_sources(plan.sources, scope)}
    out: list[SourcePreview] = []
    with SourceReader(limits) as reader:
        for info in plan.sources:
            first_ts = last_ts = None
            if info.indexable and not info.limit_violation:
                try:
                    head = reader.read_head(info, PREVIEW_PROBE_BYTES)
                    envelope = detect_envelope(head) if head else get_envelope(info.envelope)
                    if head and head.rfind(b"\n") > 0:
                        head = head[: head.rfind(b"\n") + 1]
                    times = _timed_headers(head, envelope)
                    first_ts = times[0] if times else None
                    if info.container_kind != "zip" and not reader.is_utf16(info):
                        size = _Run._file_path(info).stat().st_size
                        start = max(size - PREVIEW_PROBE_BYTES, 0)
                        tail = reader.read_raw_range(info, start, PREVIEW_PROBE_BYTES)
                        if start:
                            tail = tail[tail.find(b"\n") + 1 :]
                        times = _timed_headers(tail, envelope)
                        last_ts = times[-1] if times else None
                except (OSError, zipfile.BadZipFile, ReadLimitError):
                    pass
            shown = component(aliases, info.member) if aliases else info.member
            out.append(
                SourcePreview(
                    member=shown,
                    name=component(aliases, info.file_name) if aliases else info.file_name,
                    family=info.family,
                    process=component(aliases, info.process) if aliases and info.process else info.process,
                    pid=info.pid,
                    sensitive=info.sensitive,
                    rotation=info.rotation,
                    size=info.size,
                    selected=info.member in selected,
                    indexable=info.indexable and not info.limit_violation,
                    first_ts=first_ts,
                    last_ts=last_ts,
                )  # fmt: skip
            )
    return out
