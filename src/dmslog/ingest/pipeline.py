"""Ingestion pipeline: selected sources -> reader -> RecordGrouper -> writer (ARQUITETURA 9.2, 9.3).

``run_ingest`` is idempotent: running it again on the same zip, folder or file writes nothing
when nothing changed, appends only the tail of files that grew, follows rotations
(``X.log`` -> ``X.log.0``) and reindexes files that were rewritten or truncated.

Commit points: one transaction per batch of about ``batch_bytes`` fed bytes (16 MiB by
default) or ``MAX_BATCH_RECORDS`` records. At every commit the source row holds a consistent
state (``indexed_bytes``, ``tail_hash``, counts, ``status='indexing'``). Resuming (after an
interruption or file growth) always deletes the last stored record of the source and parses
again from its ``byte_off``/``line_no``, because that record may have gained lines.
"""

from __future__ import annotations

import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dmslog import __version__
from dmslog.ingest.discover import SourceInfo, discover, resolve_scope
from dmslog.ingest.ers import (
    KIND_ERS_ZIP,
    KIND_FILE,
    KIND_FOLDER,
    CollectionPlan,
    ErsIdentity,
    check_can_add,
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
from dmslog.ingest.reader import ReadLimitError, ReadLimits, SourceReader, relax_ratio_violations
from dmslog.knowledge.installation import InstallationConfig, read_installation
from dmslog.knowledge.registry import RegistryError
from dmslog.parse.envelope import detect_envelope, get_envelope
from dmslog.parse.records import DEFAULT_MAX_RECORD_BYTES, RecordGrouper, RecordTooLargeError
from dmslog.parse.severity import generic_severity, infer_severity
from dmslog.parse.templates import Catalog, load_catalog
from dmslog.store.config import config_param_rows, product_for_process
from dmslog.store.writer import MAX_BATCH_RECORDS, BatchStats, CollectionRow, IndexWriter, SourceRow

CASE_DB_FILENAME = "case.sqlite"
DEFAULT_BATCH_BYTES = 16 * 1024 * 1024
DETECT_BYTES = 64 * 1024  # bytes examined to pick the envelope of a new source ...
DETECT_LINES = 50  # ... or this many lines, whichever comes first (what detect_envelope looks at)

SCOPE_DEFAULT = "default"
SCOPE_ALL = "all"

# Reasons stored in source.error for sources that are listed but not indexed.
SKIP_OUT_OF_SCOPE = "out_of_scope"
SKIP_NOT_INDEXABLE = "not_indexable"
FAIL_RECORD_TOO_LARGE = "record_too_large"


@dataclass(frozen=True)
class IngestOptions:
    scope: str = SCOPE_DEFAULT  # "default" or "all"
    case_name: str | None = None
    batch_bytes: int = DEFAULT_BATCH_BYTES
    chunk_size: int = 1 << 20
    limits: ReadLimits = field(default_factory=ReadLimits)
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES
    catalog: Catalog | None = None  # template catalog; None = the packaged adapters/*.toml


@dataclass(frozen=True)
class ProgressEvent:
    """Reported to the progress callback. ``phase``: source_start, progress, source_done, done."""

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

    @property
    def mb_per_s(self) -> float:
        return self.bytes / 1e6 / self.seconds if self.seconds > 0 else 0.0


# --------------------------------------------------------------------------- planning


def build_plan(path: str | Path, limits: ReadLimits) -> CollectionPlan:
    """Like ``plan_collection`` but applying the run's limits and the ratio policy."""
    target = Path(path)
    sources = list(
        discover(
            target,
            max_member_bytes=limits.max_member_bytes,
            max_ratio=limits.max_ratio,
            max_members=limits.max_members,
            max_total_bytes=limits.max_total_bytes,
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


def default_case_name(plan: CollectionPlan) -> str:
    """Case name from the ERS GUID and capture time, or from the folder or file name."""
    if plan.identity is not None:
        return f"{plan.identity.guid[:8].lower()}-{plan.identity.timestamp}"
    return _slug(plan.path.stem if plan.kind == KIND_FILE else plan.path.name)


def validate_case_name(name: str) -> str:
    """Return ``name`` if it is a safe single folder name, else raise ValueError."""
    if not name or name != _slug(name) or name in (".", ".."):
        raise ValueError(f"invalid case name: {name!r} (use letters, digits, '.', '_' and '-')")
    return name


def case_db_path(data_dir: Path, case_name: str) -> Path:
    return data_dir / "cases" / validate_case_name(case_name) / CASE_DB_FILENAME


def _identity_of(row: CollectionRow) -> ErsIdentity | None:
    if row.kind != KIND_ERS_ZIP or not row.ers_guid or not row.ers_ts:
        return None
    return ErsIdentity(
        guid=row.ers_guid,
        timestamp=row.ers_ts,
        variant=row.ers_variant or "enterprise",
        product="Enterprise",
        description=row.label or "",
        file_name=Path(row.path).name,
    )


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

    def emit(self, phase: str, source_bytes: int = 0, source_total: int = 0, source_records: int = 0) -> None:
        if self.progress is None:
            return
        self.progress(
            ProgressEvent(
                phase, self.current_member, self.current_action, source_bytes, source_total, source_records,
                self.bytes_done,
                self.bytes_total, self.result.records, self.sources_done, self.sources_total,
            )
        )  # fmt: skip

    # ---- collection

    def resolve_collection(self) -> int:
        plan, w = self.plan, self.w
        existing = w.collections()
        ident = plan.identity
        same = next((c for c in existing if c.path == str(plan.path.resolve()) and c.kind == plan.kind), None)
        if same is None and ident is not None:
            same = next(
                (
                    c for c in existing
                    if (c.ers_guid, c.ers_ts, c.ers_variant) == (ident.guid, ident.timestamp, ident.variant)
                ),
                None,
            )  # fmt: skip
            if same is not None and same.path != str(plan.path.resolve()):
                w.update_collection_path(same.id, str(plan.path.resolve()))
        if same is not None:
            return same.id
        check_can_add([_identity_of(c) for c in existing], ident)
        group: int | None = None
        if ident is not None:
            group = next(
                (c.ers_group for c in existing if c.ers_guid == ident.guid and c.ers_ts == ident.timestamp),
                None,
            )
        return w.add_collection(
            kind=plan.kind,
            path=str(plan.path.resolve()),
            ers_guid=ident.guid if ident else None,
            ers_ts=ident.timestamp if ident else None,
            ers_variant=ident.variant if ident else None,
            ers_group=group,
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
            installation = read_installation(self.plan.path)
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
        is_zip = info.container_kind == "zip"
        known = KnownSource(
            row.status, row.size, row.zip_crc, row.head_hash, row.indexed_bytes, row.tail_hash
        )
        try:
            action = self._decide(info, known, reader, is_zip)
        except (OSError, zipfile.BadZipFile, ReadLimitError) as exc:
            self._fail(row, info, exc)
            return
        self.current_action = action.value
        if action is Action.UNCHANGED:
            self.result.sources_unchanged += 1
            self.result.details.append(SourceResult(info.member, action.value))
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
        self.result.details.append(SourceResult(info.member, action.value, stats.records, stats.lines, fed))
        self._finish_source(info, fed, stats.records)

    def _finish_source(self, info: SourceInfo, fed: int, records: int = 0) -> None:
        self.bytes_done += info.size
        self.sources_done += 1
        self.emit("source_done", fed, info.size, records)

    def _fail(self, row: SourceRow, info: SourceInfo, exc: Exception, reason: str | None = None) -> None:
        text = f"{reason}: {exc}" if reason else f"{type(exc).__name__}: {exc}"
        self.w.update_source(row.id, status="error", error=text[:500])
        self.w.commit()
        self.result.sources_failed += 1
        self.result.details.append(SourceResult(info.member, "error", error=text))
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


def _bump_ingest_generation(conn) -> None:
    """Increment ``meta.ingest_generation`` (readers use it to invalidate per-case caches)."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'ingest_generation'").fetchone()
    generation = int(row[0]) + 1 if row and str(row[0]).isdigit() else 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('ingest_generation', ?)", (str(generation),)
    )
    conn.commit()


def run_ingest(
    conn,  # sqlite3.Connection of a migrated case database
    path: str | Path,
    options: IngestOptions | None = None,
    progress: ProgressFn | None = None,
) -> IngestResult:
    """Index (or re-index) ``path`` into the case database ``conn``.

    Raises ``CollectionConflictError`` when the case already holds another ERS,
    ``UnsafeArchiveError`` for unsafe zips and ``SourceNotFoundError`` for missing paths.
    """
    options = options or IngestOptions()
    if options.scope not in (SCOPE_DEFAULT, SCOPE_ALL):
        raise ValueError(f"scope must be 'default' or 'all', got {options.scope!r}")
    t0 = time.perf_counter()
    changes_before = conn.total_changes
    plan = build_plan(path, options.limits)
    run_hook = SeverityHook()
    writer = IndexWriter(conn, severity=generic_severity, infer_severity=run_hook)
    writer.init_meta(options.case_name or default_case_name(plan), __version__)
    run = _Run(writer, plan, options, progress)
    run.severity = run_hook
    writer.sync_templates(run.catalog)
    writer.commit()
    cid = run.resolve_collection()
    writer.commit()
    run.result.collection_id = cid
    selected = select_sources(plan.sources, options.scope)
    selected_members = {s.member for s in selected}
    run.result.sources_listed = len(plan.sources)
    run.result.sources_selected = len(selected)
    run.result.sources_skipped = len(plan.sources) - len(selected)
    run.bytes_total = sum(s.size for s in selected)
    run.sources_total = len(selected)
    with SourceReader(options.limits) as reader:
        by_member = {s.member: s for s in plan.sources}
        run.follow_rotations(cid, by_member, reader)
        run.register_sources(cid, list(plan.sources), selected_members)
        run.store_installation(cid, list(selected))
        rows = writer.sources(cid)
        for info in selected:
            run.process(rows[info.member], info, reader)
    if conn.total_changes != changes_before:  # a no-op re-ingest keeps the generation (and the caches)
        _bump_ingest_generation(conn)
    run.result.seconds = time.perf_counter() - t0
    run.current_member = None
    run.emit("done")
    return run.result
