"""Payload linking: announcement, operation and request/response pair (ARQUITETURA 6.6 and 8).

The ``payload`` rows are written at ingestion (``parse/payload.py``, ``store/writer.py``) with their text
span, role, root and header ids. ``link_payloads`` fills, per collection group and after ``operations`` and
the correlation, the columns that need other records: ``ref_entry_id`` (the normal record that announces the
payload), ``event_id`` + ``event_conf`` (the operation), ``pair_id`` (request <-> response) and ``operation``
(the event name instead of the root element). The linking rules, in confidence order:

1. Announcement. A sensitive payload is announced by a normal record of the announcing template (the
   ``normal`` side of a ``[[sensitive_pair]]`` rule whose ``sensitive`` is the payload template, plus the
   built-in ``DEFAULT_ANNOUNCERS``) with the same PID (same process), the SAME TID and ``|dt| <= 1 s``,
   checked by MessageID (an announcement without id fits any; an announced id must equal the payload's
   ``Header/MessageID``, empty = empty). One announcement per payload and the other way round, nearest in
   time first. Sets ``ref_entry_id`` and inserts a ``normal_sensitive`` link announcement -> payload record
   (confidence 3 with an id, 2 without). Existing links are kept (``INSERT OR IGNORE``).
2. Request -> operation: the payload ``msg_id`` equals an ``entry_key`` value (``msgid``/``request_id``) of
   a record that belongs to an ``operation`` event of the same process (``event_conf`` 3). Only template
   keys (``origin`` 0) count: the keys read from the payloads themselves never link a payload.
3. Response -> request: ``corr_id`` equals the ``msg_id`` of a request payload (Wfm): ``pair_id`` on both,
   the request's event, ``event_conf`` 3. Without such a request, the operation holding the id as a key.
4. Response without ids (Ont/Smn): operations of the process whose END is at most 16 ms from the payload and
   whose message type (``<Noun>EventMessageType`` in the operation's records) matches the root
   (``<Noun>ResponseMessage``) when both are known, not yet answered: a single candidate gives
   ``event_conf`` 1 (and the pair with the operation's single request payload); several are an orphan,
   reason ``ambiguous``.
5. Fault without ids (Wfm): a single ``fail`` operation of the process ending in the same millisecond:
   ``event_conf`` 1.
6. Payload in a normal-log record, or announced by a record that belongs to an operation (WFM-112/121 inside
   an open operation on the same TID), with a role other than request/response/fault: the operation of that
   record, ``event_conf`` 2.
7. The rest has no event; the reason (``no_message_id``, ``no_operation_for_key``,
   ``no_request_for_correlation_id``, ``no_operation_ending_nearby``, ``no_failed_operation_nearby``,
   ``ambiguous``, ``not_in_operation``, ``too_many_payloads``) is counted in ``PayloadSummary.orphans``
   and, when the ``payload`` table has a ``reason`` column, stored there.

Request and response are NEVER paired by TID or by order: the logging thread differs from the operation's.
The CRS/IMS callback by GID is a ``[[link_rule]]`` (``derive/links.py``), not a payload rule.

Execution (like ``derive/links.py``): one unit of work per collection group; the signature in
``meta['payloads.<group>']`` covers the sources of its streams (size, mtime, CRC, hashes, counts), the events
of its streams, the group's entries epoch (``engine.group_epoch``), the payload rows, the templates involved
and the announcement map.
An unchanged group writes nothing. A changed group is recomputed inside one SAVEPOINT. Memory is bounded:
at most ``MAX_PAYLOADS`` payloads per process (the rest is reported as ``too_many_payloads``), ``MAX_OPS``
operations and ``MAX_ANNOUNCERS`` announcements held per process, at most ``MAX_SCAN`` candidates examined
per payload, writes in batches of ``BATCH``.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmslog.derive.engine import _undo, group_epoch
from dmslog.parse.payload import local_name

KIND = "normal_sensitive"
META_PREFIX = "payloads."
EPOCH_KEY = "entries_epoch"
ANNOUNCE_DT_MS = 1000  # rule 1
RESPONSE_END_DT_MS = 16  # rule 4
FAULT_END_DT_MS = 0  # rule 5: the same millisecond
MAX_PAYLOADS = 300_000  # payloads held per process (stream pair)
MAX_ANNOUNCERS = 500_000
MAX_OPS = 1_000_000
MAX_SCAN = 256  # candidates looked at per payload
MAX_TYPE_SCAN = 64  # records of an operation read to find its message type
BATCH = 5000
KEY_TYPES = ("msgid", "request_id")
_SIGNATURE_VERSION = "1"

# Payload template -> announcing templates, for catalogs that do not declare the pair (ARQUITETURA 6.6).
DEFAULT_ANNOUNCERS: dict[str, tuple[str, ...]] = {
    "GEN-PLD-01": ("GEN-MSG-01",),
    "WFM-S112": ("WFM-112",),
    "WFM-S121": ("WFM-121",),
    "SMN-S110": ("SMN-110",),
}

R_NO_ID = "no_message_id"
R_NO_OPERATION = "no_operation_for_key"
R_NO_REQUEST = "no_request_for_correlation_id"
R_NO_END = "no_operation_ending_nearby"
R_NO_FAILURE = "no_failed_operation_nearby"
R_AMBIGUOUS = "ambiguous"
R_NOT_IN_OPERATION = "not_in_operation"
R_TOO_MANY = "too_many_payloads"
R_SCAN_LIMIT = "scan_limit"

_ANNOUNCED_ID = re.compile(r"MessageID: '([^']*)'")


@dataclass
class PayloadSummary:
    links: int = 0  # normal_sensitive links inserted (announcement -> payload record)
    groups: int = 0  # groups recomputed in this run (unchanged groups are not counted)
    payloads: int = 0  # payload rows examined in the recomputed groups
    announced: int = 0  # payloads with ref_entry_id
    linked: int = 0  # payloads with event_id
    paired: int = 0  # payloads with pair_id
    errors: list[tuple[int, str]] = field(default_factory=list)  # (collection_group, message)
    orphans: dict[str, int] = field(default_factory=dict)  # reason -> payloads without an event
    dropped: int = 0  # candidates left out by the memory bounds
    by_rule: dict[int, int] = field(default_factory=dict)  # linking rule (2..6) -> payloads linked
    scan_limited: int = 0  # payloads left unlinked because uniqueness could not be proven within MAX_SCAN


# --------------------------------------------------------------------------- helpers


def _digest(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


@dataclass
class _Stream:
    id: int
    family: str
    process: str | None
    pid: int | None
    sensitive: int


@dataclass
class _Process:
    """The streams of one process in a group: normal and sensitive."""

    family: str
    process: str | None
    pid: int | None
    streams: list[_Stream]

    @property
    def ids(self) -> list[int]:
        return [s.id for s in self.streams]

    @property
    def normal_ids(self) -> list[int]:
        return [s.id for s in self.streams if not s.sensitive]


def _group_processes(conn: sqlite3.Connection) -> dict[int, list[_Process]]:
    groups: dict[int, dict[tuple[Any, Any, Any], _Process]] = {}
    rows = conn.execute(
        "SELECT collection_group, id, family, process, pid, sensitive FROM stream"
        " ORDER BY collection_group, id"
    ).fetchall()
    for group, sid, family, process, pid, sensitive in rows:
        key = (family, process, pid)
        proc = groups.setdefault(group, {}).setdefault(key, _Process(family, process, pid, []))
        proc.streams.append(_Stream(sid, family, process, pid, sensitive))
    return {g: list(p.values()) for g, p in groups.items()}


def _in_list(ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def _announcers_of(catalog: Any, family: str) -> dict[str, tuple[str, ...]]:
    """payload template code -> announcing template codes, for one family."""
    out: dict[str, set[str]] = {code: set(codes) for code, codes in DEFAULT_ANNOUNCERS.items()}
    for sensitive in (False, True):
        try:
            pairs = catalog.for_family(family, sensitive=sensitive).sensitive_pairs()
        except Exception:  # noqa: BLE001 - a catalog problem must not abort the pipeline
            continue
        for rule in pairs:
            normal, sens = rule.get("normal"), rule.get("sensitive")
            if isinstance(normal, str) and isinstance(sens, str):
                out.setdefault(sens, set()).add(normal)
    return {code: tuple(sorted(codes)) for code, codes in out.items()}


def _signature(
    conn: sqlite3.Connection,
    procs: list[_Process],
    announcers: dict[str, dict[str, tuple[str, ...]]],
    templates: dict[str, tuple[int, str]],
    group: int,
) -> str:
    parts: list[Any] = [_SIGNATURE_VERSION]
    for proc in procs:
        for st in proc.streams:
            rows = conn.execute(
                "SELECT id, rotation, status, size, mtime, zip_crc, indexed_bytes, n_entries,"
                " coalesce(tail_hash, ''), coalesce(head_hash, '') FROM source WHERE stream_id = ?"
                " ORDER BY id",
                (st.id,),
            ).fetchall()
            events = conn.execute(
                "SELECT count(*), coalesce(max(id), 0), coalesce(sum(id), 0) FROM event WHERE stream_id = ?",
                (st.id,),
            ).fetchone()
            parts.append([st.id, st.family, st.process, st.pid, st.sensitive, rows, list(events)])
    ids = _in_list([st.id for proc in procs for st in proc.streams])
    payload = conn.execute(
        "SELECT count(*), coalesce(max(p.id), 0), coalesce(sum(p.entry_id), 0) FROM payload p"
        " JOIN entry e ON e.id = p.entry_id JOIN source s ON s.id = e.source_id"
        f" WHERE s.stream_id IN ({ids})"
    ).fetchone()
    parts.append(list(payload))
    parts.append({fam: announcers[fam] for fam in sorted(announcers)})
    used: set[str] = set()
    for per_family in announcers.values():
        for code, codes in per_family.items():
            used.add(code)
            used.update(codes)
    parts.append({c: templates.get(c) for c in sorted(used)})
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (EPOCH_KEY,)).fetchone()
    parts.append(group_epoch(conn, row[0] if row else None, group))
    return _digest(*parts)


# --------------------------------------------------------------------------- in-memory records


class _P:
    """A payload under examination."""

    __slots__ = (
        "conf", "corr", "entry", "ev", "ev_name", "id", "mid", "own_ev", "pair", "reason", "ref", "ref_ev",
        "role", "root", "rule", "sens", "tid", "tpl", "ts",
    )  # fmt: skip

    def __init__(self, row: tuple[Any, ...], sens: bool) -> None:
        (self.id, self.entry, self.ts, self.tid, self.tpl, self.own_ev, self.role, self.root, self.mid,
         self.corr) = row  # fmt: skip
        self.sens = sens
        self.ev: int | None = None  # operation event
        self.ev_name: str | None = None
        self.conf: int | None = None
        self.ref: int | None = None  # announcing normal record
        self.ref_ev: int | None = None  # event_id of that record
        self.pair: int | None = None  # payload id
        self.reason: str | None = None
        self.rule = 0


def _load_payloads(
    conn: sqlite3.Connection, proc: _Process, summary: PayloadSummary
) -> tuple[list[_P], int | None]:
    """The payloads of a process in ``payload.id`` order, at most ``MAX_PAYLOADS``.

    Returns ``(payloads sorted by time, id of the last payload read when there were more)``: the rest is not
    read at all (``_mark_overflow`` labels it in SQL).
    """
    sens_of = {s.id: bool(s.sensitive) for s in proc.streams}
    out: list[_P] = []
    cur = conn.execute(
        "SELECT p.id, p.entry_id, e.ts, e.tid, e.tpl_id, e.event_id, p.role, p.root, p.msg_id, p.corr_id,"
        " s.stream_id FROM payload p JOIN entry e ON e.id = p.entry_id JOIN source s ON s.id = e.source_id"
        f" WHERE s.stream_id IN ({_in_list(proc.ids)}) ORDER BY p.id LIMIT {MAX_PAYLOADS + 1}"
    )
    overflow_after: int | None = None
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for r in rows:
            if len(out) >= MAX_PAYLOADS:
                overflow_after = out[-1].id
                break
            out.append(_P(r[:10], sens_of.get(r[10], False)))
    out.sort(key=lambda p: (p.ts if p.ts is not None else -1, p.entry))
    return out, overflow_after


def _mark_overflow(
    conn: sqlite3.Connection, proc: _Process, after_id: int, has_reason: bool, summary: PayloadSummary
) -> None:
    """Label the payloads beyond ``MAX_PAYLOADS`` with ``too_many_payloads`` without loading them."""
    where = (
        " WHERE id > ? AND id IN (SELECT p.id FROM payload p JOIN entry e ON e.id = p.entry_id"
        f" JOIN source s ON s.id = e.source_id WHERE s.stream_id IN ({_in_list(proc.ids)}))"
    )
    if has_reason:
        n = conn.execute("UPDATE payload SET reason = ?" + where, (R_TOO_MANY, after_id)).rowcount
    else:
        n = conn.execute("SELECT count(*) FROM payload" + where, (after_id,)).fetchone()[0]
    summary.dropped += n
    summary.payloads += n
    summary.orphans[R_TOO_MANY] = summary.orphans.get(R_TOO_MANY, 0) + n


# --------------------------------------------------------------------------- rule 1: announcements


class _Pool:
    """Announcing records of one template group, per TID, ordered by time."""

    def __init__(self) -> None:
        self.by_tid: dict[int, tuple[list[int], list[tuple[int, int, str | None, int | None]]]] = {}

    def add(self, tid: int, ts: int, eid: int, ident: str | None, ev: int | None) -> None:
        keys, items = self.by_tid.setdefault(tid, ([], []))
        keys.append(ts)
        items.append((ts, eid, ident, ev))

    def finish(self) -> None:
        for tid, (_keys, items) in list(self.by_tid.items()):
            items.sort(key=lambda a: (a[0], a[1]))
            self.by_tid[tid] = ([a[0] for a in items], items)


def _load_pool(
    conn: sqlite3.Connection, tpl_ids: list[int], normal_ids: list[int], summary: PayloadSummary
) -> _Pool:
    pool = _Pool()
    if not tpl_ids or not normal_ids:
        return pool
    n = 0
    cur = conn.execute(
        "SELECT e.ts, e.id, e.tid, e.event_id, e.msg FROM entry e JOIN source s ON s.id = e.source_id"
        f" WHERE e.tpl_id IN ({_in_list(tpl_ids)}) AND s.stream_id IN ({_in_list(normal_ids)})"
        " AND e.ts IS NOT NULL AND e.tid IS NOT NULL"
    )
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for ts, eid, tid, ev, msg in rows:
            if n >= MAX_ANNOUNCERS:
                summary.dropped += 1
                continue
            n += 1
            m = _ANNOUNCED_ID.search(msg or "")
            pool.add(tid, ts, eid, m.group(1).strip().lower() if m else None, ev)
    pool.finish()
    return pool


def _announce(
    payloads: list[_P],
    pools: dict[int, _Pool],
    links: list[tuple[int, int, str, int]],
    summary: PayloadSummary,
) -> None:
    """Rule 1: nearest compatible unused announcement on the same TID within ANNOUNCE_DT_MS."""
    used: set[int] = set()
    for p in payloads:
        if not p.sens or p.tid is None or p.ts is None:
            continue
        pool = pools.get(p.tpl)
        entry = pool.by_tid.get(p.tid) if pool is not None else None
        if entry is None:
            continue
        keys, items = entry
        want = (p.mid or "").strip().lower()
        best: tuple[int, int, int, int] | None = None  # (|dt|, announcement id, conf, index)
        i = bisect.bisect_left(keys, p.ts - ANNOUNCE_DT_MS)
        scanned = 0
        while i < len(items) and keys[i] <= p.ts + ANNOUNCE_DT_MS and scanned < MAX_SCAN:
            ts, eid, ident, _ev = items[i]
            scanned += 1
            if eid not in used and (ident is None or ident == want):
                conf = 3 if ident else 2
                cand = (abs(ts - p.ts), eid, conf, i)
                if best is None or cand[:2] < best[:2]:
                    best = cand
            i += 1
        if best is None:
            continue
        _dt, eid, conf, idx = best
        used.add(eid)
        p.ref = eid
        p.ref_ev = items[idx][3]
        links.append((eid, p.entry, KIND, conf))


# --------------------------------------------------------------------------- operations


class _Ops:
    """Operation events of a process for the lookups of rules 2-6."""

    def __init__(self, conn: sqlite3.Connection, proc: _Process, summary: PayloadSummary) -> None:
        self.conn = conn
        self.streams = _in_list(proc.ids)
        self.summary = summary
        self._ends: list[tuple[int, int, str | None, str | None]] | None = None
        self._end_keys: list[int] = []
        self._fails: list[tuple[int, int, str | None, str | None]] | None = None
        self._fail_keys: list[int] = []
        self._types: dict[int, str | None] = {}
        self._kind: dict[int, tuple[int, str | None] | None] = {}

    def by_key(self, value: str, ts: int | None) -> tuple[int, str | None] | None:
        """The operation holding the key ``value`` (rules 2 and 3); the nearest in time when several."""
        raw = value.strip()
        if not raw:
            return None
        types = ",".join(f"'{t}'" for t in KEY_TYPES)
        rows = self.conn.execute(
            "SELECT DISTINCT ev.id, ev.name, ev.ts_start, ev.ts_end FROM entry_key k"
            " JOIN entry e ON e.id = k.entry_id JOIN event ev ON ev.id = e.event_id"
            f" WHERE k.key_type IN ({types}) AND k.value IN (?, ?) AND k.origin = 0 AND ev.kind = 'operation'"
            f" AND ev.stream_id IN ({self.streams}) LIMIT {MAX_SCAN}",
            (raw, raw.lower()),
        ).fetchall()
        if not rows:
            return None
        if len(rows) == 1 or ts is None:
            return rows[0][0], rows[0][1]

        def distance(r: tuple[Any, ...]) -> int:
            start, end = r[2], r[3] if r[3] is not None else r[2]
            return 0 if start <= ts <= end else min(abs(ts - start), abs(ts - end))

        best = min(rows, key=lambda r: (distance(r), r[0]))
        return best[0], best[1]

    def operation_of(self, event_id: int | None) -> tuple[int, str | None] | None:
        """``(id, name)`` when the event exists, is an operation and belongs to this process."""
        if event_id is None:
            return None
        if event_id not in self._kind:
            row = self.conn.execute(
                "SELECT id, name FROM event WHERE id = ? AND kind = 'operation'"
                f" AND stream_id IN ({self.streams})",
                (event_id,),
            ).fetchone()
            self._kind[event_id] = (row[0], row[1]) if row else None
        return self._kind[event_id]

    def _load(self, failed_only: bool) -> tuple[list[tuple[int, int, str | None, str | None]], list[int]]:
        items: list[tuple[int, int, str | None, str | None]] = []
        extra = " AND status = 'fail'" if failed_only else ""
        cur = self.conn.execute(
            "SELECT ts_end, id, name, status FROM event WHERE kind = 'operation' AND ts_end IS NOT NULL"
            f" AND stream_id IN ({self.streams}){extra} ORDER BY ts_end, id"
        )
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for r in rows:
                if len(items) >= MAX_OPS:
                    self.summary.dropped += 1
                    continue
                items.append(r)
        return items, [r[0] for r in items]

    def scan_near(
        self, ts: int, tol: int, failed_only: bool, accept: Callable[[int], bool] | None = None
    ) -> tuple[list[tuple[int, str | None]], bool]:
        """Operations ending within ``tol`` ms of ``ts`` that ``accept`` allows: ``(first two, truncated)``.

        Stops at the second one (that already means "several") and examines at most ``MAX_SCAN`` operations;
        ``truncated`` is True when operations were left unexamined, so a result with fewer than two cannot
        prove uniqueness.
        """
        if failed_only:
            if self._fails is None:
                self._fails, self._fail_keys = self._load(True)
            items, keys = self._fails, self._fail_keys
        else:
            if self._ends is None:
                self._ends, self._end_keys = self._load(False)
            items, keys = self._ends, self._end_keys
        lo = bisect.bisect_left(keys, ts - tol)
        hi = bisect.bisect_right(keys, ts + tol)
        found: list[tuple[int, str | None]] = []
        for examined, i in enumerate(range(lo, hi)):
            if examined >= MAX_SCAN:
                return found, True
            _end, eid, name, _status = items[i]
            if accept is not None and not accept(eid):
                continue
            found.append((eid, name))
            if len(found) == 2:
                return found, False
        return found, False

    def message_type(self, event_id: int) -> str | None:
        """The ``<Noun>`` of the ``<Noun>EventMessageType`` found in the records of the operation."""
        if event_id in self._types:
            return self._types[event_id]
        noun = None
        cur = self.conn.execute(
            "SELECT fields FROM entry WHERE event_id = ? AND fields IS NOT NULL LIMIT ?",
            (event_id, MAX_TYPE_SCAN),
        )
        for (raw,) in cur:
            try:
                value = json.loads(raw).get("msg_type")
            except (ValueError, AttributeError):
                continue
            if isinstance(value, str) and value:
                noun = value.removesuffix("EventMessageType").lower()
                break
        self._types[event_id] = noun
        return noun


def _response_noun(root: str | None) -> str | None:
    name = local_name(root)
    if not name:
        return None
    return name.removesuffix("ResponseMessage").lower() if name.endswith("ResponseMessage") else None


def _set_event(
    p: _P, found: tuple[int, str | None] | None, conf: int, rule: int, summary: PayloadSummary
) -> bool:
    if found is None:
        return False
    p.ev, p.ev_name = found
    p.conf = conf
    p.rule = rule
    summary.by_rule[rule] = summary.by_rule.get(rule, 0) + 1
    return True


def _unique_request(cands: Sequence[_P], ts: int | None) -> tuple[_P | None, int]:
    """The request a response with ``CorrelationID`` belongs to: ``(request, count up to 2)``.

    Requests at or before the response count; when none is, all of them do (clock skew between logs).
    Several is never resolved by order: the caller leaves the response unpaired.
    """
    first: _P | None = None
    n = 0
    for r in cands:
        if r.ts is not None and ts is not None and r.ts <= ts:
            n += 1
            first = first or r
            if n == 2:
                return first, 2
    if n:
        return first, n
    for r in cands:
        n += 1
        first = first or r
        if n == 2:
            break
    return first, n


def _link_operations(ops: _Ops, payloads: list[_P], summary: PayloadSummary) -> None:
    requests_by_id: dict[str, list[_P]] = {}
    requests_of_event: dict[int, list[_P]] = {}

    # rule 2: request -> operation by MessageID
    for p in payloads:
        if p.role != "request":
            continue
        if p.mid:
            requests_by_id.setdefault(p.mid.lower(), []).append(p)
            if _set_event(p, ops.by_key(p.mid, p.ts), 3, 2, summary):
                requests_of_event.setdefault(p.ev, []).append(p)  # type: ignore[arg-type]
            else:
                p.reason = R_NO_OPERATION
        else:
            p.reason = R_NO_ID

    answered: set[int] = set()  # operations that already have a response (rule 4)

    def pair(resp: _P, req: _P) -> None:
        resp.pair = req.id
        if req.pair is None:
            req.pair = resp.id

    for p in payloads:
        if p.role not in ("response", "fault"):
            continue
        if p.corr:
            # rule 3: response -> request by CorrelationID
            req, n_req = _unique_request(requests_by_id.get(p.corr.lower(), ()), p.ts)
            if n_req > 1:
                p.reason = R_AMBIGUOUS  # several requests with this id: no guess
            elif req is not None:
                pair(p, req)
                if req.ev is not None:
                    _set_event(p, (req.ev, req.ev_name), 3, 3, summary)
                else:
                    p.reason = R_NO_OPERATION
            elif not _set_event(p, ops.by_key(p.corr, p.ts), 3, 3, summary):
                p.reason = R_NO_REQUEST
            if p.ev is not None:
                answered.add(p.ev)
            continue
        if p.ts is None:
            p.reason = R_NO_END
            continue
        if p.role == "response":
            # rule 4: operation of the same type that ends within RESPONSE_END_DT_MS
            noun = _response_noun(p.root)

            def same_type(eid: int, noun: str | None = noun) -> bool:
                op_noun = ops.message_type(eid)
                return eid not in answered and not (
                    noun is not None and op_noun is not None and noun != op_noun
                )

            cands4, cut = ops.scan_near(p.ts, RESPONSE_END_DT_MS, False, same_type)
            if len(cands4) == 1 and not cut:
                _set_event(p, cands4[0], 1, 4, summary)
                answered.add(cands4[0][0])
                mine = requests_of_event.get(cands4[0][0], [])
                if len(mine) == 1:
                    pair(p, mine[0])
            elif len(cands4) > 1:
                p.reason = R_AMBIGUOUS
            elif cut:
                p.reason = R_SCAN_LIMIT
                summary.scan_limited += 1
            else:
                p.reason = R_NO_END
        else:
            # rule 5: fault -> failed operation ending in the same millisecond
            cands5, cut = ops.scan_near(p.ts, FAULT_END_DT_MS, True)
            if len(cands5) == 1 and not cut:
                _set_event(p, cands5[0], 1, 5, summary)
            elif len(cands5) > 1:
                p.reason = R_AMBIGUOUS
            elif cut:
                p.reason = R_SCAN_LIMIT
                summary.scan_limited += 1
            else:
                p.reason = R_NO_FAILURE

    # rule 6 (and 7): payloads of a normal record, or announced by a record inside an operation
    for p in payloads:
        if p.ev is not None:
            continue
        if p.sens and p.role in ("request", "response", "fault"):
            continue  # those had their own rules; keep the reason
        if _set_event(p, ops.operation_of(p.own_ev if not p.sens else p.ref_ev), 2, 6, summary):
            p.reason = None
        else:
            p.reason = R_NOT_IN_OPERATION


# --------------------------------------------------------------------------- a group


def _has_reason_column(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "reason" for row in conn.execute("PRAGMA table_info(payload)"))


def _recompute_process(
    conn: sqlite3.Connection,
    proc: _Process,
    pool_ids: dict[int, list[int]],
    has_reason: bool,
    summary: PayloadSummary,
) -> None:
    payloads, overflow_after = _load_payloads(conn, proc, summary)
    mine = (
        "(SELECT p.id FROM payload p JOIN entry e ON e.id = p.entry_id"
        f" JOIN source s ON s.id = e.source_id WHERE s.stream_id IN ({_in_list(proc.ids)}))"
    )
    # The announcement links of the previous run go first, so a payload whose announcement changed does not
    # keep the old one. Only the (announcement, payload) pairs this linker recorded in ref_entry_id are
    # removed; the links of the other rules (correlate.py) are other pairs and stay.
    stale = conn.execute(
        f"SELECT ref_entry_id, entry_id FROM payload WHERE ref_entry_id IS NOT NULL AND id IN {mine}"
    ).fetchall()
    for start in range(0, len(stale), BATCH):
        conn.executemany(
            f"DELETE FROM link WHERE a_entry_id = ? AND b_entry_id = ? AND kind = '{KIND}'",
            stale[start : start + BATCH],
        )
    conn.execute(
        "UPDATE payload SET event_id = NULL, event_conf = NULL, ref_entry_id = NULL, pair_id = NULL"
        + (", reason = NULL" if has_reason else "")
        + f" WHERE id IN {mine}"
    )
    if overflow_after is not None:
        _mark_overflow(conn, proc, overflow_after, has_reason, summary)
    if not payloads:
        return
    links: list[tuple[int, int, str, int]] = []
    pools = {tpl: _load_pool(conn, ids, proc.normal_ids, summary) for tpl, ids in pool_ids.items()}
    _announce(payloads, pools, links, summary)
    _link_operations(_Ops(conn, proc, summary), payloads, summary)

    sql = "UPDATE payload SET event_id=?, event_conf=?, ref_entry_id=?, pair_id=?, operation=?"
    sql += ", reason=? WHERE id=?" if has_reason else " WHERE id=?"
    batch: list[tuple[Any, ...]] = []
    for p in payloads:
        summary.payloads += 1
        summary.announced += p.ref is not None
        summary.paired += p.pair is not None
        if p.ev is not None:
            summary.linked += 1
        else:
            reason = p.reason or R_NOT_IN_OPERATION
            summary.orphans[reason] = summary.orphans.get(reason, 0) + 1
        operation = p.ev_name or local_name(p.root)
        row: tuple[Any, ...] = (p.ev, p.conf, p.ref, p.pair, operation)
        if has_reason:
            row += (None if p.ev is not None else (p.reason or R_NOT_IN_OPERATION),)
        batch.append((*row, p.id))
        if len(batch) >= BATCH:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    insert = "INSERT OR IGNORE INTO link(a_entry_id, b_entry_id, kind, confidence) VALUES (?, ?, ?, ?)"
    for start in range(0, len(links), BATCH):
        summary.links += conn.executemany(insert, links[start : start + BATCH]).rowcount


def _tpl_pool_ids(
    templates: dict[str, tuple[int, str]], announcers: dict[str, tuple[str, ...]]
) -> dict[int, list[int]]:
    """payload template id -> ids of its announcing templates."""
    out: dict[int, list[int]] = {}
    for code, ann in announcers.items():
        if code not in templates:
            continue
        ids = [templates[a][0] for a in ann if a in templates]
        if ids:
            out[templates[code][0]] = ids
    return out


def link_payloads(conn: sqlite3.Connection, catalog: Any, kb: Any = None) -> PayloadSummary:
    """Link the payloads of every collection group that changed since the last run.

    A group that fails is rolled back and reported in ``errors``; the others still run. ``kb`` is accepted for
    the pipeline signature and unused.
    """
    summary = PayloadSummary()
    try:
        if conn.execute("SELECT 1 FROM payload LIMIT 1").fetchone() is None:
            return summary
        groups = _group_processes(conn)
        templates = {
            code: (tid, ver) for code, tid, ver in conn.execute("SELECT code, id, def_version FROM template")
        }
        has_reason = _has_reason_column(conn)
    except sqlite3.Error as exc:
        summary.errors.append((0, f"{type(exc).__name__}: {exc}"))
        return summary
    for group, procs in sorted(groups.items()):
        savepoint = f"link_payloads_{group}"
        own_tx = not conn.in_transaction
        try:
            announcers = {fam: _announcers_of(catalog, fam) for fam in sorted({p.family for p in procs})}
            signature = _signature(conn, procs, announcers, templates, group)
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"{META_PREFIX}{group}",)).fetchone()
            if row is not None and row[0] == signature:
                continue
            local = PayloadSummary()
            if own_tx:
                conn.execute("BEGIN")
            conn.execute(f"SAVEPOINT {savepoint}")
            for proc in procs:
                _recompute_process(
                    conn, proc, _tpl_pool_ids(templates, announcers[proc.family]), has_reason, local
                )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"{META_PREFIX}{group}", signature),
            )
            if own_tx:
                conn.execute("COMMIT")
            else:
                conn.execute(f"RELEASE {savepoint}")
        except Exception as exc:  # payload linking must never abort an ingestion
            _undo(conn, savepoint, own_tx)
            summary.errors.append((group, f"{type(exc).__name__}: {exc}"))
            continue
        summary.groups += 1
        summary.links += local.links
        summary.payloads += local.payloads
        summary.announced += local.announced
        summary.linked += local.linked
        summary.paired += local.paired
        summary.dropped += local.dropped
        summary.scan_limited += local.scan_limited
        for reason, n in local.orphans.items():
            summary.orphans[reason] = summary.orphans.get(reason, 0) + n
        for rule, n in local.by_rule.items():
            summary.by_rule[rule] = summary.by_rule.get(rule, 0) + n
    return summary


__all__ = ["DEFAULT_ANNOUNCERS", "PayloadSummary", "link_payloads"]
