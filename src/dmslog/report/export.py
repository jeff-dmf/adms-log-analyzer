"""Plain-text report (``.txt``) of a selection, to paste into a support ticket (ARQUITETURA 10.1, U10).

Layout (all fixed texts live in :mod:`dmslog.messages`)::

    dmslog report - <case label>[ - part k/N]
    Generated: <local time> - dmslog <version> - pack <version>
    Source: ERS <time> (<machine>) - Enterprise+Sensitive
    Redaction: OFF (original text)      or, when asked: Redaction: ON (profile "default", strict mode)
    Time shown as logged (local server time, no conversion).

    == Summary ==
    == Events ==        events selected by id, with their records
    == Records ==       selected records (ids or a filter) with ``context`` records around them
    == Payloads ==      only with ``include_payloads``; excerpts then point to ``P<n>``
    == Redaction legend ==  only when anonymization was asked for

Format rules: UTF-8 without BOM, CRLF line ends, ASCII frame, log lines literal (never wrapped).
A part is at most ``max_bytes`` UTF-8 bytes: longer reports are split in numbered parts
(``split``) or cut with a notice.

Privacy rules:

* the report is the original text by default (U18: the app runs locally and does not anonymize);
  anonymization is a development tool that the caller asks for explicitly (``options.anonymize``);
  a redaction profile given without it is an error, never silently ignored;
* with anonymization on, EVERY text taken from the case (record text, keys, notes, file names, the
  case label, the machine name) goes through the :class:`~dmslog.report.redact.Redactor`; payloads
  also get the per-element rules; the legend lists categories and counts, never values;
* in the profile's strict mode (the default) a record is redacted by its structure: the template's
  literal text stays, its fields and everything the template does not describe go through the strict
  nets (:meth:`~dmslog.report.redact.Redactor.record_spans`); event keys are replaced whole;
* one structural state per record: the record (first line + continuation, payloads included) is
  analysed as ONE text and every piece shown (text around payloads, payload bodies) is never less
  redacted than that: a payload body gets the record's spans cut to it UNITED with its own
  default-deny redaction; every payload and the continuation of a record cut by a read cap (state
  unknown) are shown as one value;
* payloads stay out unless ``include_payloads``; the part names carry only the time of the export.

Bounded work: at most :data:`MAX_SELECTED` selected records or events, :data:`MAX_ENTRIES` records in
all and :data:`MAX_TOTAL_CHARS` characters of log text per report; one record is cut at
:data:`MAX_ENTRY_CHARS` and one payload at :data:`MAX_PAYLOAD_CHARS`. Read-only SQL under
``query.read_guard``.
"""

from __future__ import annotations

import functools
import heapq
import json
import re
import sqlite3
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import resources
from itertools import groupby
from typing import Any

from .. import __version__, messages
from ..parse.envelope import LEVEL_NAMES
from ..store import query
from .redact import CATEGORIES, Profile, Redactor, default_profile

DEFAULT_MAX_BYTES = 30_000
MIN_MAX_BYTES = 2_000
MAX_MAX_BYTES = 2_000_000
MAX_CONTEXT = 100
MAX_SELECTED = 1_000
MAX_ENTRIES = 2_000
ROW_MSG_CHARS = 50_000  # text read per record: msg and continuation are cut in SQL
ROW_CONT_CHARS = 100_000
MAX_EVENT_ENTRIES = 500
MAX_ENTRY_CHARS = 20_000
MAX_PAYLOAD_CHARS = 100_000
MAX_TOTAL_CHARS = 5_000_000
MAX_SUMMARY_GROUPS = 25
MAX_LABEL_CHARS = 200
EXPORT_TIMEOUT_S = 30.0
IN_CHUNK = 400
EXCERPT_INDENT = "    "
MARK_SELECTED = "  > "
SQL_TS_NULL = -(2**63)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")  # lone surrogates cannot be encoded
_EPOCH = datetime(1970, 1, 1)


class ExportError(ValueError):
    """Invalid export request. ``code`` is stable; the message is English and has no log content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- request objects


@dataclass(frozen=True, slots=True)
class Selection:
    """What to export: records matching ``filter``, records by ``ids``, events by ``events``."""

    filter: dict[str, Any] | None = None
    ids: tuple[int, ...] = ()
    events: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ExportOptions:
    anonymize: bool = False  # U18: original text unless anonymization is asked for explicitly
    include_payloads: bool = False
    context: int = 0
    max_bytes: int = DEFAULT_MAX_BYTES
    split: bool = True
    profile: Profile | None = None  # only with anonymize; None = the default profile (everything on)

    def __post_init__(self) -> None:
        if not isinstance(self.anonymize, bool):
            raise ExportError(
                "bad_option",
                messages.EXPORT_BAD_OPTION.format(field="anonymize", problem="true or false"),
            )
        if self.profile is not None and not self.anonymize:
            raise ExportError("redaction_not_requested", messages.EXPORT_REDACTION_NOT_REQUESTED)


@dataclass(frozen=True, slots=True)
class Part:
    name: str
    text: str


@dataclass(slots=True)
class ExportResult:
    parts: list[Part]
    legend: list[dict[str, Any]]
    anonymized: bool
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    redactor: Redactor | None = None  # holds the local value map; never serialize it


def _id_list(raw: Any, name: str, message: str) -> tuple[int, ...]:
    if raw is None:
        return ()
    if (
        not isinstance(raw, list)
        or not 1 <= len(raw) <= MAX_SELECTED
        or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v < 2**62 for v in raw)
    ):
        raise ExportError(f"bad_{name}", message)
    return tuple(dict.fromkeys(raw))


def parse_selection(raw: Any) -> Selection:
    """Validate the ``selection`` object of a request."""
    if not isinstance(raw, Mapping):
        raise ExportError("bad_selection", messages.EXPORT_BAD_SELECTION)
    flt = raw.get("filter")
    if flt is not None and not isinstance(flt, dict):
        raise ExportError("bad_filter", messages.EXPORT_BAD_FILTER)
    ids = _id_list(raw.get("ids"), "ids", messages.EXPORT_BAD_IDS.format(limit=MAX_SELECTED))
    events = raw.get("events")
    if events is None and raw.get("event") is not None:
        events = [raw.get("event")]
    event_ids = _id_list(events, "events", messages.EXPORT_BAD_EVENTS.format(limit=MAX_SELECTED))
    if not flt and not ids and not event_ids:
        raise ExportError("bad_selection", messages.EXPORT_BAD_SELECTION)
    return Selection(flt or None, ids, event_ids)


def _int_option(raw: Mapping[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ExportError(
            "bad_option",
            messages.EXPORT_BAD_OPTION.format(field=name, problem=f"an integer from {low} to {high}"),
        )
    return value


def _bool_option(raw: Mapping[str, Any], name: str, default: bool) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise ExportError(
            "bad_option", messages.EXPORT_BAD_OPTION.format(field=name, problem="true or false")
        )
    return value


def parse_options(raw: Any, profile: Profile | None = None) -> ExportOptions:
    """Validate the ``options`` object (absent = every default). ``anonymize`` defaults to false (U18);
    a ``profile`` without ``anonymize`` true is refused (``redaction_not_requested``)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ExportError("bad_options", messages.EXPORT_BAD_OPTIONS)
    return ExportOptions(
        anonymize=_bool_option(raw, "anonymize", False),
        include_payloads=_bool_option(raw, "include_payloads", False),
        context=_int_option(raw, "context", 0, 0, MAX_CONTEXT),
        max_bytes=_int_option(raw, "max_bytes", DEFAULT_MAX_BYTES, MIN_MAX_BYTES, MAX_MAX_BYTES),
        split=_bool_option(raw, "split", True),
        profile=profile,
    )


# --------------------------------------------------------------------------- small helpers


def format_ts(ms: int | None) -> str:
    """Log time as logged (the milliseconds encode the wall clock read as UTC; U6)."""
    if ms is None:
        return messages.EXPORT_UNKNOWN_VALUE
    try:
        moment = _EPOCH + timedelta(milliseconds=ms)
    except OverflowError:
        return messages.EXPORT_UNKNOWN_VALUE
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _format_ers_ts(value: str | None) -> str:
    """The ERS capture time, or a constant: any other text from the case never reaches the header."""
    if value and re.fullmatch(r"[0-9]{14}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:]}"
    return messages.EXPORT_UNKNOWN_VALUE


_VARIANT_NAMES = {"enterprise": "Enterprise", "sensitive": "Sensitive"}


def _clean(text: str) -> str:
    return _CONTROL.sub("?", text.replace("\r\n", "\n").replace("\r", "\n"))


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


def _cut_to_bytes(line: str, budget: int) -> str:
    """``line`` shortened so that it plus the cut notice fits in ``budget`` UTF-8 bytes."""
    notice = messages.EXPORT_LINE_CUT
    keep = max(0, budget - _utf8_len(notice))
    cut = line.encode("utf-8", errors="replace")[:keep].decode("utf-8", errors="ignore")
    return cut + notice


def _clip_spans(
    spans: Sequence[tuple[int, int, str]], ranges: Sequence[tuple[int, int]]
) -> list[list[tuple[int, int, str]]]:
    """For each ``(start, end)`` range, the spans that overlap it, cut to it and made relative to its
    start. One sorted sweep (spans by start, a heap of the open ones by end), so the work is linear in
    the spans, the ranges and the pieces returned, never ranges x spans; ``ranges`` must be sorted by
    start (overlapping ranges are fine)."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    out: list[list[tuple[int, int, str]]] = []
    open_spans: list[tuple[int, int]] = []  # (end, index)
    nxt = 0
    for start, end in ranges:
        while nxt < len(order) and spans[order[nxt]][0] < end:
            index = order[nxt]
            heapq.heappush(open_spans, (spans[index][1], index))
            nxt += 1
        while open_spans and open_spans[0][0] <= start:
            heapq.heappop(open_spans)
        out.append(
            [
                (max(a, start) - start, min(b, end) - start, category)
                for a, b, category in (spans[index] for _e, index in open_spans)
                if a < end and b > max(a, start)
            ]
        )
    return out


def _chunks(values: Sequence[int]) -> Iterable[Sequence[int]]:
    for index in range(0, len(values), IN_CHUNK):
        yield values[index : index + IN_CHUNK]


def _sort_key(row: Mapping[str, Any]) -> tuple[int, int]:
    return (row["ts"] if row["ts"] is not None else SQL_TS_NULL, row["id"])


# --------------------------------------------------------------------------- reading the case


@dataclass(slots=True)
class _Row:
    id: int
    source_id: int
    line_no: int
    nlines: int
    ts: int | None
    pid: int | None
    tid: int | None
    level: int | None
    flags: int
    event_id: int | None
    msg: str
    cont: str | None
    tpl: str | None
    file_name: str
    omitted: int = 0  # characters of msg and continuation that were not read (over the read caps)
    cut: bool = False  # a read cap cut the record: its structural state past the cut is unknown


@dataclass(slots=True)
class _Payload:
    entry_id: int
    role: str
    direction: str
    peer: str
    operation: str
    fmt: str
    start: int
    end: int


@dataclass(slots=True)
class _Event:
    id: int
    family: str
    process: str
    sensitive: bool | None
    kind: str
    name: str
    key: str | None
    ts_start: int
    ts_end: int | None
    duration_ms: int | None
    status: str
    detail: dict[str, Any]
    entry_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Block:
    """Records of one source shown together; selected are the ones asked for, the rest is context."""

    ids: list[int]
    selected: set[int]
    event_id: int | None = None


_ROW_SQL = (
    "SELECT e.id, e.source_id, e.line_no, e.nlines, e.ts, e.pid, e.tid, e.level, e.flags, e.event_id, "
    "substr(e.msg, 1, {msg_cap}), substr(b.cont, 1, {cont_cap}), t.code, s.file_name, "
    "length(e.msg), length(b.cont) "
    "FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id "
    "LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0 "
    "LEFT JOIN source s ON s.id = e.source_id WHERE e.id IN ({marks})"
)
_EVENT_SQL = (
    "SELECT ev.id, st.family, st.process, st.sensitive, ev.kind, ev.name, ev.key, ev.ts_start, ev.ts_end, "
    "ev.duration_ms, ev.status, ev.detail FROM event ev LEFT JOIN stream st ON st.id = ev.stream_id "
    "WHERE ev.id IN ({marks})"
)


_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


def _catalog_codes(conn: sqlite3.Connection) -> set[str]:
    """Codes the case itself defines: template codes and the error codes of the ERS configuration."""
    codes: set[str] = set()
    for sql in (
        "SELECT code FROM template",
        "SELECT adms_id FROM error_config",
        "SELECT ext_code FROM error_config",
    ):
        try:
            codes.update(str(r[0]) for r in conn.execute(sql) if r[0])
        except sqlite3.OperationalError:
            continue
    return codes


@functools.lru_cache(maxsize=1)
def _catalog_names() -> tuple[frozenset[str], frozenset[str]]:
    """(family codes, static operation names) of the shipped catalog (the adapter TOML files)."""
    families: set[str] = set()
    operations: set[str] = set()
    try:
        folder = resources.files("dmslog.adapters")
        for entry in folder.iterdir():
            if entry.name.endswith(".toml") and not entry.name.startswith("_"):
                families.add(entry.name[: -len(".toml")].upper())
                data = tomllib.loads(entry.read_text(encoding="utf-8"))
                operations.update(
                    str(op["name"]) for op in data.get("operation", []) if isinstance(op.get("name"), str)
                )
        rules = tomllib.loads(resources.files("dmslog").joinpath("families.toml").read_text(encoding="utf-8"))
        families.update(str(r["family"]) for r in rules.get("rule", []) if isinstance(r.get("family"), str))
    except (OSError, ValueError):
        pass
    return frozenset(families), frozenset(operations)


TRAILING_DROP_CHARS = 128  # a record cut by a read cap loses this much of its end (and more, to a blank)


def _drop_partial_token(text: str) -> str:
    """``text`` without its end: a record cut by a read cap may end inside any value (a GUID, a phone,
    an address), so the last :data:`TRAILING_DROP_CHARS` characters and the token they end in go."""
    keep = text[:-TRAILING_DROP_CHARS] if len(text) > TRAILING_DROP_CHARS else ""
    cut = max((keep.rfind(ch) for ch in " \t\r\n"), default=-1)
    return keep[:cut] if cut > 0 else ""


def _fetch_rows(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, _Row]:
    rows: dict[int, _Row] = {}
    for chunk in _chunks(list(ids)):
        sql = _ROW_SQL.format(
            marks=",".join("?" * len(chunk)), msg_cap=ROW_MSG_CHARS, cont_cap=ROW_CONT_CHARS
        )
        for r in conn.execute(sql, tuple(chunk)):
            msg, cont = r[10] or "", r[11]
            omitted = 0
            if (r[14] or 0) > ROW_MSG_CHARS:  # cut by the read cap: drop the partial token at the cut
                msg = _drop_partial_token(msg)
                omitted += r[14] - len(msg)
            if cont is not None and (r[15] or 0) > ROW_CONT_CHARS:
                cont = _drop_partial_token(cont)
                omitted += r[15] - len(cont)
            rows[r[0]] = _Row(
                id=r[0], source_id=r[1], line_no=r[2], nlines=r[3] or 1, ts=r[4], pid=r[5], tid=r[6],
                level=r[7], flags=r[8] or 0, event_id=r[9], msg=msg, cont=cont, tpl=r[12],
                file_name=r[13] or "", omitted=omitted, cut=omitted > 0,
            )  # fmt: skip
    return rows


def _fetch_events(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, _Event]:
    events: dict[int, _Event] = {}
    for chunk in _chunks(list(ids)):
        sql = _EVENT_SQL.format(marks=",".join("?" * len(chunk)))
        for r in conn.execute(sql, tuple(chunk)):
            try:
                detail = json.loads(r[11]) if r[11] else {}
            except ValueError:
                detail = {}
            events[r[0]] = _Event(
                id=r[0], family=r[1] or "", process=r[2] or "",
                sensitive=None if r[3] is None else bool(r[3]), kind=r[4] or "", name=r[5] or "",
                key=r[6], ts_start=r[7], ts_end=r[8], duration_ms=r[9], status=r[10] or "",
                detail=detail if isinstance(detail, dict) else {},
            )  # fmt: skip
    return events


def _fetch_payloads(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, list[_Payload]]:
    found: dict[int, list[_Payload]] = {}
    try:
        for chunk in _chunks(list(ids)):
            sql = (
                "SELECT entry_id, role, direction, peer, operation, fmt, char_off, char_len FROM payload "
                f"WHERE entry_id IN ({','.join('?' * len(chunk))}) ORDER BY entry_id, char_off"
            )
            for r in conn.execute(sql, tuple(chunk)):
                if r[6] is None or r[7] is None or r[6] < 0 or r[7] <= 0:
                    continue
                found.setdefault(r[0], []).append(
                    _Payload(
                        r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5] or "", r[6], r[6] + r[7]
                    )
                )
    except sqlite3.OperationalError:  # a case written before payloads existed
        return {}
    return found


def _fetch_notes(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, list[str]]:
    notes: dict[int, list[str]] = {}
    try:
        for chunk in _chunks(list(ids)):
            sql = (
                "SELECT entry_id, note FROM bookmark WHERE note IS NOT NULL AND note <> '' "
                f"AND entry_id IN ({','.join('?' * len(chunk))}) ORDER BY id"
            )
            for entry_id, note in conn.execute(sql, tuple(chunk)):
                notes.setdefault(entry_id, []).append(note)
    except sqlite3.OperationalError:
        return {}
    return notes


def _neighbours(
    conn: sqlite3.Connection, entry_id: int, before: int, after: int
) -> tuple[list[int], list[int]]:
    """Ids of the records before (ascending) and after the record, in the same source, file order."""
    prev: list[int] = []
    nxt: list[int] = []
    if before:
        sql = (
            "SELECT id FROM entry WHERE source_id = (SELECT source_id FROM entry WHERE id = ?) "
            "AND id < ? ORDER BY id DESC LIMIT ?"
        )
        prev = [r[0] for r in conn.execute(sql, (entry_id, entry_id, before))][::-1]
    if after:
        sql = (
            "SELECT id FROM entry WHERE source_id = (SELECT source_id FROM entry WHERE id = ?) "
            "AND id > ? ORDER BY id LIMIT ?"
        )
        nxt = [r[0] for r in conn.execute(sql, (entry_id, entry_id, after))]
    return prev, nxt


@dataclass(slots=True)
class _CaseInfo:
    label: str
    pack_version: str | None
    kind: str | None
    ers_ts: str | None
    machine: str | None
    variants: set[str]
    accounts: set[str]
    # more than one collection group (U21): one header line per group and a tag (C1, C2...) on every record
    collections: list[tuple[str, str, str | None, str | None, list[str], str | None]] = field(
        default_factory=list
    )  # (tag, kind, ers_ts, guid, variants, machine or zone)
    source_tags: dict[int, str] = field(default_factory=dict)  # source id -> tag


def _collections(conn: sqlite3.Connection) -> tuple[list[Any], dict[int, str]]:
    """Groups of a case with more than one collection group, in the numbering of the collections route."""
    rows = conn.execute(
        "SELECT coalesce(ers_group, id), kind, ers_ts, ers_guid, ers_variant, coalesce(machine, system)"
        " FROM collection ORDER BY coalesce(ers_group, id), id"
    ).fetchall()
    groups: dict[int, list[Any]] = {}
    for group, kind, stamp, guid, variant, host in rows:
        item = groups.setdefault(group, [f"C{len(groups) + 1}", kind, stamp, guid, [], host])
        if variant and variant not in item[4]:
            item[4].append(variant)
        item[5] = item[5] or host
    if len(groups) < 2:
        return [], {}
    tags = {group: item[0] for group, item in groups.items()}
    sources = conn.execute(
        "SELECT s.id, coalesce(c.ers_group, c.id) FROM source s JOIN collection c ON c.id = s.collection_id"
    )
    return [tuple(item) for item in groups.values()], {sid: tags[g] for sid, g in sources}


def _case_info(conn: sqlite3.Connection) -> _CaseInfo:
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('name', 'pack_version')"))
    row = conn.execute("SELECT kind, ers_ts, machine FROM collection ORDER BY id LIMIT 1").fetchone()
    variants = {r[0] for r in conn.execute("SELECT DISTINCT ers_variant FROM collection") if r[0]}
    accounts = {r[0] for r in conn.execute("SELECT DISTINCT account FROM stream") if r[0]}
    machines = [r[0] for r in conn.execute("SELECT DISTINCT machine FROM collection") if r[0]]
    groups, tags = _collections(conn)
    return _CaseInfo(
        label=meta.get("name") or "case",
        pack_version=meta.get("pack_version"),
        kind=row[0] if row else None,
        ers_ts=row[1] if row else None,
        machine=", ".join(machines) if machines else None,
        variants=variants,
        accounts=accounts,
        collections=groups,
        source_tags=tags,
    )


# --------------------------------------------------------------------------- building the report


@dataclass(slots=True)
class _PayloadItem:
    number: int
    ts: int | None
    file_name: str
    line_no: int
    info: _Payload | None
    body: str
    length: int
    opaque: bool = False  # a read cap cut the record: its state is unknown, one value, no skeleton
    start: int = 0  # offset of the body in the record text
    row_id: int = 0
    spans: list[tuple[int, int, str]] | None = None  # whole-record spans cut to the body (strict mode)


class _Builder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        selection: Selection,
        options: ExportOptions,
        label: str | None,
        now: datetime,
        pack_version: str | None,
        version: str,
    ) -> None:
        self.conn = conn
        self.selection = selection
        self.options = options
        self.label_override = label
        self.now = now
        self.pack_override = pack_version
        self.version = version
        self.warnings: list[str] = []
        self.redactor: Redactor | None = None
        self.payload_numbers: dict[tuple[int, int], int] = {}
        self.payload_items: list[_PayloadItem] = []
        self.text_chars = 0
        self.rows_shown = 0
        self.text_limit_hit = False
        self._label_text = ""
        self.catalog: set[str] = set()
        self._machine_text = ""
        self._profile_text = ""
        self._kind_text = ""
        self._pack_text = "n/a"
        self._collection_lines: list[str] = []  # header lines of a case with several collection groups
        self._source_tags: dict[int, str] = {}

    def R(self, text: str, *, payload: bool = False, spans: Any = None, identifier: bool = False) -> str:
        """Anonymize a text taken from the case (identity when anonymization is off). ``spans`` are the
        strict structural spans of a record piece; ``identifier`` replaces the whole text (strict)."""
        if self.redactor is None:
            return text
        return self.redactor.redact(text, payload=payload, spans=spans, identifier=identifier)

    # -- selection and planning

    def _selected_records(self) -> list[tuple[int | None, int]]:
        """(ts, id) of the records chosen by ``ids`` and ``filter``, in report order."""
        found: dict[int, int | None] = {}
        sel = self.selection
        if sel.ids:
            with query.read_guard(self.conn, EXPORT_TIMEOUT_S):
                for chunk in _chunks(list(sel.ids)):
                    marks = ",".join("?" * len(chunk))
                    found.update(
                        (r[0], r[1])
                        for r in self.conn.execute(
                            f"SELECT id, ts FROM entry WHERE id IN ({marks})", tuple(chunk)
                        )
                    )
            if len(found) < len(sel.ids):
                self.warnings.append(messages.EXPORT_WARN_MISSING.format(count=len(sel.ids) - len(found)))
        if sel.filter:
            page = query.page(
                self.conn, sel.filter, None, "fwd", MAX_SELECTED, [], timeout_s=EXPORT_TIMEOUT_S
            )
            found.update((r["id"], r["ts"]) for r in page.rows)
            if page.has_more:
                self.warnings.append(messages.EXPORT_WARN_RECORD_LIMIT.format(limit=MAX_SELECTED))
        ordered = [(ts, entry_id) for entry_id, ts in found.items()]
        ordered.sort(key=lambda item: (SQL_TS_NULL if item[0] is None else item[0], item[1]))
        return ordered

    def _plan(self) -> tuple[_CaseInfo, list[_Event], list[_Block], list[_Block], dict[int, _Event]]:
        """Read the case: events, record blocks (selected records with context) and the event index."""
        records = self._selected_records()
        context = self.options.context
        with query.read_guard(self.conn, EXPORT_TIMEOUT_S):
            info = _case_info(self.conn)
            self.catalog = _catalog_codes(self.conn)
            events = _fetch_events(self.conn, self.selection.events) if self.selection.events else {}
            missing = sum(1 for event_id in self.selection.events if event_id not in events)
            if missing:
                self.warnings.append(messages.EXPORT_WARN_MISSING.format(count=missing))
            ordered_events = sorted(events.values(), key=lambda e: (e.ts_start, e.id))
            planned = 0
            event_blocks: list[_Block] = []
            for event in list(ordered_events):
                if planned >= MAX_ENTRIES:
                    self.warnings.append(messages.EXPORT_WARN_ENTRY_LIMIT.format(limit=MAX_ENTRIES))
                    ordered_events = ordered_events[: ordered_events.index(event)]
                    break
                sql = "SELECT id FROM entry WHERE event_id = ? ORDER BY id LIMIT ?"
                ids = [r[0] for r in self.conn.execute(sql, (event.id, MAX_EVENT_ENTRIES + 1))]
                if len(ids) > MAX_EVENT_ENTRIES:
                    ids = ids[:MAX_EVENT_ENTRIES]
                    self.warnings.append(
                        messages.EXPORT_WARN_EVENT_LIMIT.format(limit=MAX_EVENT_ENTRIES, event=event.id)
                    )
                event.entry_ids = ids
                if not ids:
                    event_blocks.append(_Block([], set(), event.id))
                    continue
                before, _ = _neighbours(self.conn, ids[0], context, 0)
                _, after = _neighbours(self.conn, ids[-1], 0, context)
                event_blocks.append(_Block([*before, *ids, *after], set(ids), event.id))
                planned += len(before) + len(ids) + len(after)
            owner: dict[int, _Block] = {}  # record id -> the record block that shows it
            record_blocks: list[_Block] = []
            selected_all = {entry_id for _ts, entry_id in records}
            for _ts, entry_id in records:
                if entry_id in owner:
                    continue
                if planned >= MAX_ENTRIES:
                    self.warnings.append(messages.EXPORT_WARN_ENTRY_LIMIT.format(limit=MAX_ENTRIES))
                    break
                before, after = _neighbours(self.conn, entry_id, context, context)
                ids = [*before, entry_id, *after]
                shared = next((owner[i] for i in ids if i in owner), None)
                if shared is None:  # a new excerpt
                    shared = _Block([], set())
                    record_blocks.append(shared)
                # a context window that touches an earlier excerpt of the same source extends it
                merged = sorted(set(shared.ids) | set(ids))
                planned += len(merged) - len(shared.ids)
                shared.ids = merged
                shared.selected |= selected_all & set(ids)
                owner.update(dict.fromkeys(ids, shared))
            if not event_blocks and not record_blocks:
                raise ExportError("nothing", messages.EXPORT_NOTHING)
            self.rows = _fetch_rows(
                self.conn,
                sorted({i for b in (*event_blocks, *record_blocks) for i in b.ids}),
            )
            wanted = list(self.rows)
            self.table_payloads = _fetch_payloads(self.conn, wanted)
            self.notes = _fetch_notes(self.conn, wanted)
            index = dict(events)
            owners = {
                r.event_id
                for b in record_blocks
                for i in b.selected
                if (r := self.rows.get(i)) is not None and r.event_id is not None
            }
            extra = [i for i in owners if i not in index]
            if extra:
                index.update(_fetch_events(self.conn, extra))
        self.selected_ids = selected_all
        return info, ordered_events, event_blocks, record_blocks, index

    # -- building

    def build(self) -> ExportResult:
        info, ordered_events, event_blocks, record_blocks, index = self._plan()
        if self.options.anonymize:
            self.redactor = self._make_redactor(info, index.values(), self.rows.values())
        self._prime(info)  # header texts first, so their markers get the first numbers

        event_lines: list[str] = []
        by_event = {b.event_id: b for b in event_blocks}
        for number, event in enumerate(ordered_events, 1):
            event_lines.extend(self._event_lines(number, event, by_event.get(event.id)))
        record_lines: list[str] = []
        for number, block in enumerate(record_blocks, 1):
            record_lines.extend(self._record_lines(number, block, index))

        selected_rows = [self.rows[i] for i in sorted(self.selected_ids) if i in self.rows]
        counts = {
            "records": len(selected_rows) + sum(len(e.entry_ids) for e in ordered_events),
            "events": len(ordered_events),
            "payloads": len(self.payload_numbers),
        }
        starts = [r.ts for r in selected_rows if r.ts is not None] + [e.ts_start for e in ordered_events]
        ends = [r.ts for r in selected_rows if r.ts is not None] + [
            e.ts_end if e.ts_end is not None else e.ts_start for e in ordered_events
        ]
        body = self._summary_lines(
            counts, min(starts) if starts else None, max(ends) if ends else None, index.values()
        )
        if event_lines:
            body.extend(["", messages.EXPORT_SECTION_EVENTS, *event_lines])
        if record_lines:
            body.extend(["", messages.EXPORT_SECTION_RECORDS, *record_lines])
        if self.options.include_payloads and self.payload_items:
            body.extend(["", messages.EXPORT_SECTION_PAYLOADS, *self._payload_lines()])
        if self.text_limit_hit:
            self.warnings.append(messages.EXPORT_WARN_TEXT_LIMIT.format(limit=MAX_TOTAL_CHARS))
        tail = self._legend_lines()
        parts = self._pack(info, body, tail)
        legend = self.redactor.legend() if self.redactor is not None else []
        return ExportResult(parts, legend, self.options.anonymize, self.warnings, counts, self.redactor)

    def _make_redactor(self, info: _CaseInfo, events: Iterable[_Event], rows: Iterable[_Row]) -> Redactor:
        """Catalog names and codes are the only automatic exceptions, and only when no redaction
        category would touch them (``inc0045123`` or a plate is never an exception, whatever it is
        called). The machine and the process accounts of the case are always replaced."""
        profile = self.options.profile or default_profile()
        known: dict[str, list[str]] = {}
        if info.machine:
            known["host"] = [m.strip() for m in info.machine.split(",") if m.strip()]
        if info.accounts:
            known["user"] = sorted(info.accounts)
        # The probe runs the strict nets, except in standard mode with every category off: there it
        # redacts nothing, so every safe catalog name or code stays an exception (as it always has).
        # In strict mode the nets stay on with every category off, so the exceptions stay narrow.
        any_on = any(profile.enabled(c) for c in CATEGORIES)
        probe_mode = "strict" if profile.mode == "strict" or any_on else "standard"
        probe = Redactor(
            Profile(categories=dict(profile.categories), payload_names=(), mode=probe_mode), known=known
        )
        families, operations = _catalog_names()
        candidates: set[str] = set()
        for event in events:
            if event.family in families:
                candidates.add(event.family)
            if event.name in operations:
                candidates.add(event.name)
            codes = event.detail.get("error_codes")
            if isinstance(codes, list):  # only codes the case's catalog defines; never free text
                candidates.update(str(c) for c in codes if str(c) in self.catalog)
        candidates.update(r.tpl for r in rows if r.tpl)
        exceptions = {c for c in candidates if _SAFE_CODE.match(c) and probe.redact(c) == c}
        return Redactor(profile, exceptions=exceptions, known=known)

    def _prime(self, info: _CaseInfo) -> None:
        label = _clean(self.label_override or info.label).replace("\n", " ")
        self._label_text = self.R(label)[:MAX_LABEL_CHARS]  # anonymize first, then cut
        self._machine_text = self.R(info.machine) if info.machine else messages.EXPORT_UNKNOWN_VALUE
        # every other header text that comes from the profile or the case goes through the redactor too
        profile = self.options.profile or default_profile()
        self._profile_text = self.R(_clean(profile.name).replace("\n", " "))
        shown_kind = info.kind and not (info.kind == "ers_zip" or info.variants)
        self._kind_text = self.R(_clean(info.kind).replace("\n", " ")) if shown_kind else ""
        pack = self.pack_override or info.pack_version
        self._pack_text = self.R(_clean(pack).replace("\n", " ")) if pack else "n/a"
        self._source_tags = info.source_tags
        self._collection_lines = []
        for tag, kind, stamp, guid, group_variants, host in info.collections:
            shown_host = messages.EXPORT_UNKNOWN_VALUE
            if host:
                shown_host = self.R(_clean(host).replace("\n", " "), identifier=True)
            if kind == "ers_zip" and guid:
                names = sorted(group_variants, key=lambda v: (v != "enterprise", v))
                shown = "+".join(dict.fromkeys(_VARIANT_NAMES.get(v, "Other") for v in names))
                line = messages.EXPORT_COLLECTION_ERS.format(
                    tag=tag, ers_ts=_format_ers_ts(stamp), machine=shown_host,
                    guid=self.R(_clean(guid), identifier=True), variant=shown or "Enterprise",
                )  # fmt: skip
            else:
                line = messages.EXPORT_COLLECTION_PLAIN.format(
                    tag=tag, kind=self.R(_clean(kind).replace("\n", " ")), machine=shown_host
                )
            self._collection_lines.append(line)

    # -- sections

    def _summary_lines(
        self, counts: dict[str, int], start: int | None, end: int | None, events: Iterable[_Event]
    ) -> list[str]:
        lines = [
            messages.EXPORT_SECTION_SUMMARY,
            messages.EXPORT_SUMMARY_COUNTS.format(
                records=counts["records"],
                events=counts["events"],
                payloads=counts["payloads"],
                start=format_ts(start),
                end=format_ts(end),
            ),
        ]
        groups: dict[tuple[str, str], list[_Event]] = {}
        for event in events:
            if event.kind == "operation":
                groups.setdefault((event.family, event.name), []).append(event)
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for (family, name), members in ranked[:MAX_SUMMARY_GROUPS]:
            durations = sorted(e.duration_ms for e in members if e.duration_ms is not None)
            p90 = durations[max(0, -(-len(durations) * 90 // 100) - 1)] if durations else None
            lines.append(
                messages.EXPORT_SUMMARY_GROUP.format(
                    family=self.R(family) or "-",
                    name=self.R(name) or "-",
                    count=len(members),
                    fail=sum(1 for e in members if e.status in ("fail", "timeout")),
                    business=sum(1 for e in members if e.status == "business_error"),
                    p90="n/a" if p90 is None else f"{p90}ms",
                )
            )
        if len(ranked) > MAX_SUMMARY_GROUPS:
            lines.append(messages.EXPORT_SUMMARY_MORE.format(count=len(ranked) - MAX_SUMMARY_GROUPS))
        return lines

    def _event_lines(self, number: int, event: _Event, block: _Block | None) -> list[str]:
        end = event.ts_end if event.ts_end is not None else event.ts_start
        lines = [
            messages.EXPORT_EVENT_HEAD.format(
                n=number,
                start=format_ts(event.ts_start),
                end=format_ts(end),
                family=self.R(event.family) or "-",
                name=self.R(event.name or event.kind) or "-",
                status=self.R(event.status) or "-",
                duration="n/a" if event.duration_ms is None else f"{event.duration_ms}ms",
                key=self.R(event.key, identifier=True) if event.key else "-",
            )
        ]
        if event.process:
            kind = "sensitive" if event.sensitive else "normal"
            lines.append(
                EXCERPT_INDENT + messages.EXPORT_EVENT_STREAM.format(process=self.R(event.process), kind=kind)
            )
        codes = event.detail.get("error_codes")
        if isinstance(codes, list) and codes:
            lines.append(
                EXCERPT_INDENT + messages.EXPORT_EVENT_CODES.format(codes=self.R(", ".join(map(str, codes))))
            )
        exception = event.detail.get("exception")
        if isinstance(exception, str) and exception:
            lines.append(
                EXCERPT_INDENT + messages.EXPORT_EVENT_EXCEPTION.format(exception=_clean(self.R(exception)))
            )
        if block is None or not block.ids:
            lines.append(EXCERPT_INDENT + messages.EXPORT_EVENT_NO_RECORDS)
        else:
            lines.extend(self._block_lines(block))
        lines.append("")
        return lines

    def _record_lines(self, number: int, block: _Block, index: dict[int, _Event]) -> list[str]:
        anchors = [i for i in block.ids if i in block.selected and i in self.rows]
        if not anchors:
            return []
        first = self.rows[anchors[0]]
        event = index.get(first.event_id) if first.event_id is not None else None
        suffix = ""
        if event is not None:
            suffix = messages.EXPORT_RECORD_EVENT.format(
                name=self.R(event.name or event.kind), status=self.R(event.status) or "-"
            )
        head = messages.EXPORT_RECORD_HEAD.format(
            n=number,
            when=format_ts(first.ts),
            source=self._source_text(first),
            line=first.line_no,
            event=suffix,
        )
        if len(anchors) > 1:
            head += messages.EXPORT_RECORD_MORE.format(count=len(anchors) - 1)
        return [head, *self._block_lines(block), ""]

    def _block_lines(self, block: _Block) -> list[str]:
        present = [self.rows[i] for i in block.ids if i in self.rows]
        mark = len(present) > len(block.selected)
        out: list[str] = []
        for _source, group in groupby(present, key=lambda row: row.source_id):
            rows = list(group)
            last = rows[-1]
            out.append(
                EXCERPT_INDENT
                + messages.EXPORT_EXCERPT_HEAD.format(
                    file=self._source_text(rows[0]),
                    first=rows[0].line_no,
                    last=last.line_no + max(last.nlines, 1) - 1,
                )
            )
            for row in rows:
                prefix = MARK_SELECTED if mark and row.id in block.selected else EXCERPT_INDENT
                out.extend(self._row_lines(row, prefix))
                for note in self.notes.get(row.id, ()):
                    shown = self.R(_clean(note).replace("\n", " "))
                    out.append(EXCERPT_INDENT + messages.EXPORT_EVENT_NOTE.format(note=shown))
        return out

    def _row_lines(self, row: _Row, prefix: str) -> list[str]:
        if self.rows_shown >= MAX_ENTRIES or self.text_chars >= MAX_TOTAL_CHARS:
            self.text_limit_hit = self.text_limit_hit or self.text_chars >= MAX_TOTAL_CHARS
            return []
        self.rows_shown += 1
        text = row.msg + ("\n" + row.cont if row.cont else "")
        head = self._row_head(row)
        out: list[str] = []
        first_text = True
        # strict mode: the record's structure (its template) decides what is variable; the spans are
        # computed once on the whole record and handed to each piece around its payloads
        record_spans = (
            self.redactor.record_spans(text, len(row.msg), row.tpl) if self.redactor is not None else []
        )
        if row.cut and self.redactor is not None and self.redactor.strict and len(text) > len(row.msg):
            # a read cap cut this record: the state of the first line past the cut (or of the end of
            # the continuation) is unknown, so the continuation is one value
            record_spans.append((len(row.msg), len(text), "value"))
        known = len(self.payload_items)
        pieces = self._split_payloads(row, text)
        strict = self.redactor is not None and self.redactor.strict
        if strict and self.redactor is not None and not (len(pieces) == 1 and pieces[0][1] == text):
            # the record is cut around its payloads: each piece also gets EVERYTHING the whole record
            # replaces (patterns and labels included: a label whose value a payload cut off)
            record_spans = record_spans + self.redactor.replaced_spans(text, record_spans)
        clipped: list[list[tuple[int, int, str]]] = []
        if strict:
            # the whole record's spans cut to each text piece and to each payload body shown, in one
            # sorted sweep (linear: never pieces x spans); a payload body keeps them IN ADDITION to its
            # own default-deny redaction, so it is never less redacted than the whole record
            bodies = [item for item in self.payload_items[known:] if item.body and item.row_id == row.id]
            ranges = [(offset, offset + len(value)) for kind, value, offset in pieces if kind == "text"]
            ranges.extend((item.start, item.start + len(item.body)) for item in bodies)
            order = sorted(range(len(ranges)), key=lambda i: ranges[i][0])
            cut = _clip_spans(record_spans, [ranges[i] for i in order])
            clipped = [[] for _ in ranges]
            for rank, index in enumerate(order):
                clipped[index] = cut[rank]
            texts = sum(1 for kind, _v, _o in pieces if kind == "text")
            for item, spans in zip(bodies, clipped[texts:], strict=True):
                item.spans = spans
        piece = 0
        for kind, value, _offset in pieces:
            if kind == "note":
                out.append(EXCERPT_INDENT + value)
                continue
            piece_spans = clipped[piece] if strict else None
            piece += 1
            shown = _clean(self.R(value, spans=piece_spans))  # anonymize first, cut the anonymized text
            cut = ""
            if len(shown) > MAX_ENTRY_CHARS:
                cut = messages.EXPORT_EXCERPT_CUT.format(count=len(shown) - MAX_ENTRY_CHARS)
                shown = shown[:MAX_ENTRY_CHARS]
            if self.text_chars + len(shown) > MAX_TOTAL_CHARS:
                self.text_limit_hit = True
                break
            self.text_chars += len(shown)
            for line in shown.split("\n"):
                out.append(prefix + (head if first_text else "") + line)
                first_text = False
            if cut:
                out.append(EXCERPT_INDENT + cut)
        if row.omitted:
            out.append(EXCERPT_INDENT + messages.EXPORT_EXCERPT_CUT.format(count=row.omitted))
        return out

    @staticmethod
    def _row_head(row: _Row) -> str:
        if row.ts is None:
            return ""
        ids = f"[PID={row.pid}]" if row.pid is not None else ""
        if row.tid is not None:
            ids += f"[TID=0x{row.tid:x}]"
        head = format_ts(row.ts) + (" " + ids if ids else "")
        level = LEVEL_NAMES.get(row.level) if row.level is not None else None
        return f"{head} {level} - " if level else f"{head} "

    def _split_payloads(self, row: _Row, text: str) -> list[tuple[str, str, int]]:
        """The record text as ('text', piece, offset in ``text``) pieces and ('note', ...) placeholders
        for its payloads.

        Payload spans come from the ``payload`` table; without rows, the continuation lines from the
        first one that starts with '<' on are taken as the payload. A payload of a record cut by a read
        cap is OPAQUE: shown as one value, because its structural state is unknown.
        """
        spans: list[tuple[int, int, _Payload | None]] = [
            (p.start, min(p.end, len(text)), p)
            for p in self.table_payloads.get(row.id, ())
            if p.start < len(text)
        ]
        if not spans and row.cont:
            offset = len(row.msg) + 1
            for line in row.cont.split("\n"):
                if line.lstrip().startswith("<"):
                    spans.append((offset, len(text), None))
                    break
                offset += len(line) + 1
        if not spans:
            return [("text", text, 0)]
        out: list[tuple[str, str, int]] = []
        pos = 0
        for start, end, info in sorted(spans, key=lambda s: s[0]):
            if start < pos:
                continue
            if start > pos:
                out.append(("text", text[pos:start].rstrip("\n"), pos))
            number = self.payload_numbers.get((row.id, start))
            length = end - start
            if number is None:
                number = len(self.payload_numbers) + 1
                self.payload_numbers[(row.id, start)] = number
                body = ""
                if self.options.include_payloads:  # one budget for the whole report, payloads included
                    if self.text_chars + length <= MAX_TOTAL_CHARS:
                        body = text[start:end]
                        self.text_chars += length
                    else:
                        self.text_limit_hit = True
                self.payload_items.append(
                    _PayloadItem(
                        number, row.ts, row.file_name, row.line_no, info, body, length, row.cut, start, row.id
                    )
                )
            template = (
                messages.EXPORT_PAYLOAD_SEE
                if self.options.include_payloads
                else messages.EXPORT_PAYLOAD_OMITTED
            )
            role = self.R(info.role) if info is not None and info.role else "payload"
            out.append(("note", template.format(n=number, role=role, length=length), start))
            pos = end
        tail = text[pos:].lstrip("\n")
        if tail:
            out.append(("text", tail, len(text) - len(tail)))
        return out

    def _payload_lines(self) -> list[str]:
        lines: list[str] = []
        for item in self.payload_items:
            info = item.info
            lines.append(
                messages.EXPORT_PAYLOAD_HEAD.format(
                    n=item.number,
                    when=format_ts(item.ts),
                    file=self.R(item.file_name) or "-",
                    line=item.line_no,
                    role=self.R(info.role) if info and info.role else "payload",
                    direction=self.R(info.direction) if info and info.direction else "-",
                    peer=self.R(info.peer) if info and info.peer else "-",
                    operation=self.R(info.operation) if info and info.operation else "-",
                    fmt=self.R(info.fmt) if info and info.fmt else "text",
                    length=item.length,
                )
            )
            strict = self.redactor is not None and self.redactor.strict
            if item.body and item.opaque and strict:
                shown = _clean(self.R(item.body, identifier=True))  # its state is unknown: one value
            elif item.body:
                # strict: the union of the payload's own default-deny redaction and the whole record's
                # spans cut to the body (a character either replaces stays replaced)
                shown = _clean(self.R(item.body, payload=True, spans=item.spans if strict else None))
            else:
                shown = ""
            omitted = item.length if not item.body else max(0, len(shown) - MAX_PAYLOAD_CHARS)
            lines.extend(EXCERPT_INDENT + line for line in shown[:MAX_PAYLOAD_CHARS].split("\n") if shown)
            if omitted:
                lines.append(EXCERPT_INDENT + messages.EXPORT_PAYLOAD_CUT.format(count=omitted))
            lines.append("")
        return lines

    def _legend_lines(self) -> list[str]:
        """The legend section; only an anonymized report has one."""
        if self.redactor is None:
            return []
        lines = ["", messages.EXPORT_SECTION_LEGEND]
        lines.append(messages.EXPORT_LEGEND_NOTE)
        legend = self.redactor.legend()
        if not legend:
            lines.append(messages.EXPORT_LEGEND_NONE)
        for row in legend:
            text = (
                messages.REDACT_TERM_TEXT
                if row["category"] == "term"
                else messages.REDACT_CATEGORY_TEXT[row["category"]]
            )
            lines.append(
                messages.EXPORT_LEGEND_LINE.format(
                    text=text, label=row["label"], distinct=row["distinct"], replaced=row["replaced"]
                )
            )
        if self.redactor.overflow:
            lines.append(messages.EXPORT_LEGEND_OVERFLOW.format(count=self.redactor.overflow))
        lines.append(messages.EXPORT_LEGEND_NOTE_2)
        return lines

    # -- packing in parts

    def _title(self, part: int, total: int) -> str:
        if total > 1:
            return messages.EXPORT_TITLE_PART.format(label=self._label_text, part=part, total=total)
        return messages.EXPORT_TITLE.format(label=self._label_text)

    def _head(self, info: _CaseInfo, part: int, total: int) -> list[str]:
        if part > 1:
            return [self._title(part, total), messages.EXPORT_CONTINUES, ""]
        pack = self._pack_text
        if self._collection_lines:
            return [
                self._title(part, total),
                messages.EXPORT_GENERATED.format(
                    when=self.now.strftime("%Y-%m-%d %H:%M:%S"), version=self.version, pack=pack
                ),
                messages.EXPORT_SOURCE_MULTI.format(count=len(self._collection_lines)),
                *self._collection_lines,
                self._redaction_line(),
                messages.EXPORT_TIME_NOTE,
                messages.EXPORT_TIME_NOTE_MULTI,
                "",
            ]
        if info.kind == "ers_zip" or info.variants:
            names = sorted(info.variants, key=lambda v: (v != "enterprise", v))
            source = messages.EXPORT_SOURCE.format(
                ers_ts=_format_ers_ts(info.ers_ts),
                machine=self._machine_text,
                variant="+".join(dict.fromkeys(_VARIANT_NAMES.get(v, "Other") for v in names))
                or "Enterprise",
            )
        elif info.kind:
            source = messages.EXPORT_SOURCE_PLAIN.format(kind=self._kind_text, machine=self._machine_text)
        else:
            source = messages.EXPORT_SOURCE_UNKNOWN
        return [
            self._title(part, total),
            messages.EXPORT_GENERATED.format(
                when=self.now.strftime("%Y-%m-%d %H:%M:%S"), version=self.version, pack=pack
            ),
            source,
            self._redaction_line(),
            messages.EXPORT_TIME_NOTE,
            "",
        ]

    def _redaction_line(self) -> str:
        profile = self.options.profile or default_profile()
        mode = profile.mode if profile.mode in ("strict", "standard") else "strict"
        if self.options.anonymize:
            return messages.EXPORT_REDACTION_ON_MODE.format(profile=self._profile_text, mode=mode)
        return messages.EXPORT_REDACTION_OFF

    def _source_text(self, row: _Row) -> str:
        """File name of a record, with the tag of its collection group when the case has several (U21)."""
        name = self.R(row.file_name) or "-"
        tag = self._source_tags.get(row.source_id)
        return f"{tag} {name}" if tag else name

    def _pack(self, info: _CaseInfo, body: list[str], tail: list[str]) -> list[Part]:
        limit = self.options.max_bytes
        body = _squeeze([_clean(line) for line in body])
        while body and not body[-1]:
            body.pop()
        lines = [*body, *(_clean(line) for line in tail)]
        body_count = len(body)
        if not self.options.split:
            return [self._single_part(info, lines[:body_count], lines[body_count:], limit)]
        head_first = sum(_cost(line) for line in self._head(info, 1, 999))
        head_next = sum(_cost(line) for line in self._head(info, 2, 999))
        budget_min = limit - max(head_first, head_next)
        parts: list[list[str]] = [[]]
        used = head_first
        for line in lines:
            if _cost(line) > budget_min:
                line = _cut_to_bytes(line, budget_min - 2)
            if _cost(line) > limit - used and parts[-1]:
                parts.append([])
                used = head_next
            parts[-1].append(line)
            used += _cost(line)
        total = len(parts)
        stamp = self.now.strftime("%Y%m%d-%H%M%S")
        out = []
        for index, part_lines in enumerate(parts, 1):
            name = f"dmslog-report-{stamp}" + (f"-part{index}of{total}" if total > 1 else "") + ".txt"
            out.append(Part(name, "\r\n".join([*self._head(info, index, total), *part_lines]) + "\r\n"))
        return out

    def _single_part(self, info: _CaseInfo, body: list[str], tail: list[str], limit: int) -> Part:
        head = self._head(info, 1, 1)
        reserve = _cost(messages.EXPORT_TRUNCATED_NOTICE.format(count=99_999_999, limit=MAX_MAX_BYTES))
        capacity = limit - sum(_cost(x) for x in head) - sum(_cost(x) for x in tail) - reserve
        kept: list[str] = []
        used = 0
        for index, line in enumerate(body):
            if used + _cost(line) > capacity:
                kept.append(messages.EXPORT_TRUNCATED_NOTICE.format(count=len(body) - index, limit=limit))
                break
            kept.append(line)
            used += _cost(line)
        text = "\r\n".join([*head, *kept, *tail]) + "\r\n"
        return Part(f"dmslog-report-{self.now.strftime('%Y%m%d-%H%M%S')}.txt", text)


def _squeeze(lines: list[str]) -> list[str]:
    """Collapse runs of empty lines into one and drop the empty lines at the start."""
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return out


def _cost(line: str) -> int:
    """UTF-8 bytes of a line with its CRLF."""
    return _utf8_len(line) + 2


def build_report(
    conn: sqlite3.Connection,
    selection: Selection,
    options: ExportOptions,
    *,
    label: str | None = None,
    now: datetime | None = None,
    pack_version: str | None = None,
    version: str = __version__,
) -> ExportResult:
    """Build the report from a read-only case connection.

    Raises :class:`ExportError` when nothing matches and ``QueryInputError`` for an invalid filter.
    """
    builder = _Builder(conn, selection, options, label, now or datetime.now(), pack_version, version)
    return builder.build()
