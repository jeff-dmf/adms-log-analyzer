"""Callback, retry, response and handoff links driven by ``[[link_rule]]`` tables (ARQUITETURA 8).

A rule says: records of the codes ``a`` and later records of the codes ``b`` that share the value of ``key``
(an ``entry_key`` type, or a field name when the type has no ``entry_key`` rows for that template) belong
together when ``0 <= dt <= max_dt_ms`` (default 60000). ``scope = "stream"`` (default) looks inside one
stream;
``scope = "group"`` looks across the normal and sensitive streams of the same process (same collection group,
family, process and pid). Rules of one family never link records of another family. Rows go to ``link``:

* ``callback`` and ``response``: each ``b`` is linked to the LATEST earlier ``a`` with the same key value
  (confidence 3). An ``a`` may receive several ``b`` (progress callbacks), up to ``MAX_B_PER_A``.
* ``retry``: records of the codes ``a`` + ``b`` with the same key value form a chain ordered by time; a chain
  starts at an ``a`` record and every following record gets a link from the previous one (``a -> b -> b``,
  confidence 3). A gap larger than ``max_dt_ms`` breaks the chain.
* ``window_only = true`` (no key): a ``b`` is linked to the one unconsumed ``a`` that precedes it within
  ``max_dt_ms``, only when that ``a`` is unambiguous (exactly one open ``a``); the link has confidence 0 and
  the ``a`` is consumed. With two or more open ``a`` nothing is linked (counted as ``ambiguous``). No rule
  of the catalog uses this plain form today (the Wfm one was measured wrong, docs/formato/wfm-ont.md,
  section 4); the Osr callback uses the stricter form with ``origin = "operation"`` below.
* ``origin = "operation"`` (``callback`` and ``response`` only, with ``key``): the ``a`` records only carry
  the key (Wfm: the GID that CRS/IMS returned, logged inside the request); the link goes from the START
  record of the operation (``event.kind = 'operation'``, through ``entry.event_id``) that holds them to each
  ``b`` with the same key value (confidence 2: identical key plus TID membership of the operation).
  Safe-match rule: every ``a`` record of the value in the partition must belong to one and the same
  operation, and the latest ``a`` at or before the ``b`` must be at most ``max_dt_ms`` older. Otherwise
  nothing is linked and the ``b`` is counted: ``unmatched`` (no ``a`` with that value, e.g. the INFO lines
  were not logged), ``ambiguous`` (``a`` records in more than one operation, or more than ``MAX_A_PER_KEY``
  of them), ``no_operation`` (``a`` outside any operation) or ``out_of_window`` (the ``b`` is older than
  every ``a``, or too far after them).
* ``window_only = true`` with ``origin = "operation"`` (``callback`` and ``response`` only, no key; Osr
  OSR-026 -> OSR-031): the ``a`` codes are START records of operations; the anchor of an ``a`` is the end
  (``event.ts_end``) of the operation it starts. A ``b`` is linked to an ``a`` (confidence 0, heuristic)
  only when the match is unique on BOTH sides: the ``a`` is the only one whose anchor lies in
  ``[b.ts - max_dt_ms, b.ts]`` and the ``b`` is the only one in ``[anchor, anchor + max_dt_ms]``.
  Otherwise nothing is linked and the ``b`` is counted: ``ambiguous`` (several ``a`` in its window, or its
  ``a`` has several ``b``), ``no_operation`` (the single ``a`` does not start a closed operation: open,
  truncated or missing; it is then anchored at its own ts), ``out_of_window`` (no ``a`` anchored in its
  window) or ``unmatched`` (the partition has no ``a`` record at all). The anchors are read in record
  order (no database sort) and reordered by a heap of the operations in flight; what the memory bounds
  make undecidable is counted as ``dropped`` and never linked.

* ``kind = "handoff"`` (always ``scope = "collection"``, keyed; optional ``families``, ``pid_field``): a job
  handed from one process to another, where the engine cannot trust any order of ids. One partition per
  collection group holds every stream (normal and sensitive, any process and pid) of the rule's family plus
  the families listed in ``families``. A ``b`` (ts ``t``, value ``v``) has as candidates the ``a`` records of
  value ``v`` with ts in ``[t - max_dt_ms, t]`` (an ``a`` with the same ts counts as earlier); with
  ``pid_field``, an ``a`` whose field and the ``b`` envelope PID are both known and different is not a
  candidate. Compatible means: same value, ``a.ts`` in the window of the ``b`` and no known PID
  mismatch. The link exists if and only if ``degree(a) = 1`` and ``degree(b) = 1`` (degrees count every
  compatible record, ambiguous ones included); a ``b`` with a compatible ``a`` that is not linked is
  ``ambiguous``. The link is ``a -> b`` with
  confidence 3 (PIDs compared and equal, or no ``pid_field``) or 2 (``pid_field`` but one side unknown). A
  ``b`` without any candidate is ``out_of_coverage`` when its ts lies outside the time span of the sources
  that hold ``a`` records (file rotation), else ``unmatched``; candidates all removed by the PID test count
  ``pid_mismatch``; an ``a`` with no compatible ``b`` counts ``unanswered`` (only inside the coverage of the
  ``b`` side). The outcome depends only on the set of ``(ts, value, pid)`` of the records, never on entry ids
  or on the order the files were indexed, so a resumed run equals a full one. The memory bound
  (``MAX_OPEN_KEYS`` undecided ``a``) is checked only when the ts advances; past it the partition stops and
  everything undecided counts ``dropped``.

Examples (the codes are placeholders)::

    # callback by GID: the waiting record and the callback live in different TIDs
    [[link_rule]]
    kind = "callback"
    a = "CRM-020"
    b = "CRM-021"
    key = "gid"
    scope = "group"
    max_dt_ms = 120000

    # retry chain by msgId, then the final response linked to the last attempt
    [[link_rule]]
    kind = "retry"
    a = "ONT-011"
    b = "ONT-011"
    key = "msgid"

    [[link_rule]]
    kind = "response"
    a = "ONT-011"
    b = "ONT-012"
    key = "msgid"

    # callback with no key on the start line: only by time window, heuristic
    [[link_rule]]
    kind = "callback"
    a = "XYZ-001"
    b = "XYZ-002"
    window_only = true
    max_dt_ms = 30000

    # callback linked to the request (operation) whose thread logged the key it carries
    [[link_rule]]
    kind = "callback"
    a = ["WFM-123", "WFM-124A", "WFM-124B"]
    b = "WFM-030"
    key = "gid"
    origin = "operation"

    # a job handed to another process (maybe of another family), unique both ways, PID checked
    [[link_rule]]
    kind = "handoff"
    a = "XYZ-010"
    b = "ABC-001"
    key = "job_id"
    scope = "collection"
    families = ["ABC"]
    pid_field = "host_pid"

    # callback with no key anywhere, linked to the operation that ended just before it, if unique both ways
    [[link_rule]]
    kind = "callback"
    a = "OSR-026"
    b = "OSR-031"
    window_only = true
    origin = "operation"
    max_dt_ms = 5000

Execution: ``link_pending`` works per collection group. A group is recomputed only when the signature in
``meta['correlate.links.<group>']`` (sources of its streams, rules, template ids and, when a rule uses
``origin``, the operation events of the streams and a hash of the operation of every ``a`` record, with
its end for the window form)
changed; an unchanged group writes nothing. The same meta
value keeps the counters of the last run per rule (``link_stats``). Recomputing deletes the links of this
module's kinds that start in the group, inserts the new ones and stores the signature, all inside one
SAVEPOINT. Candidates are read through ``ix_entry_tpl_ts`` by template id, one cursor per code, merged in
time order; memory is bounded (``MAX_OPEN_KEYS`` open keys, ``MAX_B_PER_A`` links per ``a``,
``MAX_A_PER_KEY``), and what is dropped by those bounds is counted in ``LinkSummary.dropped``. Odd data
(``NULL`` ts or key, a dangling ``event_id``) is skipped, never raised.

The ``normal_sensitive`` links of ``correlate.py`` and ``payloads.py`` are never touched here, and those
engines never delete this module's kinds: the origin links survive their recomputation.
"""

from __future__ import annotations

import bisect
import hashlib
import heapq
import json
import re
import sqlite3
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from dmslog.derive.engine import _undo, group_epoch

KINDS = ("callback", "retry", "response", "handoff")
DEFAULT_MAX_DT_MS = 60000
MAX_OPEN_KEYS = 200_000  # open keys (or open window records) kept per rule and partition
MAX_B_PER_A = 64  # callbacks linked to one ``a`` record
MAX_A_PER_KEY = 64  # ``origin`` rules: ``a`` records kept per key value; more makes the value ambiguous
BATCH = 5000
META_PREFIX = "correlate.links."
_SIGNATURE_VERSION = "4"
ORIGINS = ("record", "operation")
ORIGIN_CONFIDENCE = 2
_FIELD_NAME = re.compile(r"^\w+$")

# (ts, rank, entry_id, key value, is_a, is_b); rank puts a pure ``a`` before a ``b`` with the same ts
_Event = tuple[int, int, int, str, bool, bool]


@dataclass
class LinkSummary:
    links: int = 0  # rows inserted in ``link``
    groups: int = 0  # collection groups recomputed (unchanged ones are skipped)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (collection_group, message)
    dropped: int = 0  # candidates lost to the memory bounds (open keys, links per ``a``)
    ambiguous: int = 0  # ``b`` not linked: several open ``a`` (window_only) or several operations (origin)
    unmatched: int = 0  # origin rules: ``b`` whose key value no ``a`` record carries
    no_operation: int = 0  # origin rules: the ``a`` records of the value are outside any operation
    out_of_window: int = 0  # origin rules: no ``a`` of its value (or, window form, no anchor) in the window
    out_of_coverage: int = 0  # handoff: ``b`` without ``a`` whose ts is outside the span of the ``a`` sources
    pid_mismatch: int = 0  # handoff: every ``a`` candidate of the ``b`` has another PID
    unanswered: int = 0  # handoff: ``a`` no ``b`` considered, inside the coverage of the ``b`` sources


_COUNTERS = (
    "dropped",
    "ambiguous",
    "unmatched",
    "no_operation",
    "out_of_window",
    "out_of_coverage",
    "pid_mismatch",
    "unanswered",
)


@dataclass
class _Counters:
    linked: int = 0
    dropped: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    no_operation: int = 0
    out_of_window: int = 0
    out_of_coverage: int = 0
    pid_mismatch: int = 0
    unanswered: int = 0

    def add_to(self, other: Any) -> None:
        for name in _COUNTERS:
            setattr(other, name, getattr(other, name) + getattr(self, name))

    def as_dict(self) -> dict[str, int]:
        out = {"linked": self.linked}
        out.update({name: getattr(self, name) for name in _COUNTERS if getattr(self, name)})
        return out


# --------------------------------------------------------------------------- helpers


def _codes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [c for c in value if isinstance(c, str)]
    return []


def _max_dt(rule: Any) -> int:
    value = rule.get("max_dt_ms", DEFAULT_MAX_DT_MS)
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else DEFAULT_MAX_DT_MS
    )


def _handoff_problem(rule: Any) -> str | None:
    key = rule.get("key")
    if not (isinstance(key, str) and key):
        return "handoff rule without key"
    if rule.get("scope") != "collection":
        return "handoff needs scope = 'collection'"
    if "window_only" in rule or "origin" in rule:
        return "handoff takes neither window_only nor origin"
    if set(_codes(rule.get("a"))) & set(_codes(rule.get("b"))):
        return "handoff needs disjoint a and b codes"
    families = rule.get("families", [])
    if not isinstance(families, (list, tuple)) or not all(isinstance(f, str) for f in families):
        return "families must be a list of family names"
    pid_field = rule.get("pid_field")
    if pid_field is not None and not (isinstance(pid_field, str) and _FIELD_NAME.match(pid_field)):
        return "pid_field must be a field name"
    return None


def _rule_problem(rule: Any) -> str | None:
    """Why a rule cannot run (None when it can)."""
    if rule.get("kind") not in KINDS:
        return f"unknown link kind {rule.get('kind')!r}"
    if not _codes(rule.get("a")) or not _codes(rule.get("b")):
        return "rule without a/b codes"
    if rule["kind"] == "handoff":
        return _handoff_problem(rule)
    if "families" in rule or "pid_field" in rule:
        return "families and pid_field are only valid for handoff rules"
    window_only = bool(rule.get("window_only", False))
    if window_only and rule["kind"] == "retry":
        return "window_only is not valid for retry rules"
    key = rule.get("key")
    if not window_only and not (isinstance(key, str) and key):
        return "rule without key (and not window_only)"
    if rule.get("scope", "stream") not in ("stream", "group"):
        return f"unknown scope {rule.get('scope')!r}"
    origin = rule.get("origin", "record")
    if origin not in ORIGINS:
        return f"unknown origin {origin!r}"
    if origin == "operation":
        if rule["kind"] == "retry":
            return "origin = 'operation' needs a callback or response rule"
        if window_only and key is not None:
            return "origin = 'operation' with window_only takes no key (it would be ignored)"
        if set(_codes(rule.get("a"))) & set(_codes(rule.get("b"))):
            return "origin = 'operation' needs disjoint a and b codes"
    return None


def _origin(rule: Any) -> bool:
    return rule.get("origin", "record") == "operation"


def _window_origin(rule: Any) -> bool:
    """``origin = "operation"`` without a key: the unique-both-ways window anchored at the operation end."""
    return _origin(rule) and bool(rule.get("window_only", False))


def _rule_label(family: str, rule: Any) -> str:
    a, b = "+".join(_codes(rule.get("a"))), "+".join(_codes(rule.get("b")))
    suffix = "@operation-end" if _window_origin(rule) else "@operation" if _origin(rule) else ""
    return f"{family}/{rule.get('kind')}:{a}>{b}{suffix}"


def _digest(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


@dataclass
class _Stream:
    id: int
    family: str
    process: str | None
    pid: int | None
    sensitive: int


def _group_streams(conn: sqlite3.Connection) -> dict[int, list[_Stream]]:
    groups: dict[int, list[_Stream]] = {}
    rows = conn.execute(
        "SELECT collection_group, id, family, process, pid, sensitive FROM stream"
        " ORDER BY collection_group, id"
    ).fetchall()
    for group, sid, family, process, pid, sensitive in rows:
        groups.setdefault(group, []).append(_Stream(sid, family, process, pid, sensitive))
    return groups


def _family_rules(catalog: Any, family: str) -> list[Any]:
    try:
        return list(catalog.for_family(family, sensitive=False).link_rules())
    except Exception:  # noqa: BLE001 - a catalog problem must not abort the pipeline
        return []


def _template_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT code, id FROM template").fetchall())


def _signature(
    conn: sqlite3.Connection,
    streams: list[_Stream],
    rules: dict[str, list[Any]],
    ids: dict[str, int],
    group: int,
) -> str:
    parts: list[Any] = [_SIGNATURE_VERSION]
    for st in streams:
        rows = conn.execute(
            "SELECT id, rotation, status, size, mtime, zip_crc, indexed_bytes, n_entries,"
            " coalesce(tail_hash, ''), coalesce(head_hash, '') FROM source WHERE stream_id = ? ORDER BY id",
            (st.id,),
        ).fetchall()
        parts.append([st.id, st.family, st.process, st.pid, st.sensitive, rows])
    used: set[str] = set()
    origin_families: set[str] = set()
    for family in sorted(rules):
        for rule in rules[family]:
            used.update(_codes(rule.get("a")) + _codes(rule.get("b")))
            if _origin(rule):
                origin_families.add(family)
    for st in streams:
        if st.family in origin_families:
            # the origin links follow the operations: a re-derivation (new [[operation]] rule) recomputes them
            ops = conn.execute(
                "SELECT count(*), coalesce(max(id), 0), coalesce(sum(id), 0),"
                " coalesce(sum(start_entry_id), 0) FROM event WHERE stream_id = ? AND kind = 'operation'",
                (st.id,),
            ).fetchone()
            parts.append([st.id, list(ops)])
    for family in sorted(origin_families):
        for rule in rules[family]:
            if not _origin(rule) or _rule_problem(rule) is not None:
                continue
            digest = _anchor_digest if _window_origin(rule) else _origin_digest
            for stream_ids in _partitions(streams, rule.get("scope", "stream"), family):
                parts.append(digest(conn, rule, ids, stream_ids))
    parts.append({fam: [dict(r) for r in rules[fam]] for fam in sorted(rules)})
    defs = dict(conn.execute("SELECT code, def_version FROM template").fetchall())
    parts.append({c: [ids.get(c), defs.get(c)] for c in sorted(used)})
    row = conn.execute("SELECT value FROM meta WHERE key = 'entries_epoch'").fetchone()
    parts.append(group_epoch(conn, row[0] if row else None, group))
    return _digest(*parts)


def _origin_digest(conn: sqlite3.Connection, rule: Any, ids: dict[str, int], stream_ids: list[int]) -> str:
    """Streaming hash of the ``a`` records of an origin rule with the operation that holds each of them.

    The aggregates of the event table do not see an ``a`` record moved to another operation (same event ids,
    same count, same starts); this hash does: it covers ``(entry id, key value, operation id, operation
    start)`` of every ``a`` record, in O(number of ``a`` records) time and constant memory.
    """
    h = hashlib.sha1()
    for code in sorted(set(_codes(rule["a"]))):
        if code not in ids:
            continue
        h.update(code.encode("utf-8"))
        for row in _keyed_rows(conn, ids[code], stream_ids, rule["key"], with_operation=True):
            h.update(json.dumps(row[1:], default=str).encode("utf-8"))
    return h.hexdigest()


def _anchor_digest(conn: sqlite3.Connection, rule: Any, ids: dict[str, int], stream_ids: list[int]) -> str:
    """Streaming hash of the ``a`` records of a window origin rule with their operation and its end.

    Read in record order through ``ix_entry_tpl_ts`` (no sort): ``(ts, id, operation id, operation end)``.
    """
    h = hashlib.sha1()
    for code in sorted(set(_codes(rule["a"]))):
        if code not in ids:
            continue
        h.update(code.encode("utf-8"))
        for row in conn.execute(_anchor_sql(stream_ids), (ids[code],)):
            h.update(json.dumps(row, default=str).encode("utf-8"))
    return h.hexdigest()


# --------------------------------------------------------------------------- candidate reading


def _keyed_rows(
    conn: sqlite3.Connection,
    tpl_id: int,
    stream_ids: list[int],
    key: str | None,
    with_operation: bool = False,
) -> Iterator[tuple[Any, ...]]:
    """``(ts, id, value[, operation id, operation start])`` of one template in the streams, by (ts, id).

    ``with_operation`` adds the operation event that holds the record (``entry.event_id``), only when it is an
    operation of the same streams; otherwise both columns are NULL.
    """
    streams = ",".join(str(int(s)) for s in stream_ids)
    where = (
        "e.tpl_id = ? AND e.ts IS NOT NULL AND e.source_id IN"
        f" (SELECT id FROM source WHERE stream_id IN ({streams}))"
    )
    op_cols, op_join = "", ""
    if with_operation:
        op_cols = ", ev.id, ev.start_entry_id"
        op_join = (
            " LEFT JOIN event ev ON ev.id = e.event_id AND ev.kind = 'operation'"
            f" AND ev.stream_id IN ({streams})"
        )
    if key is None:
        yield from conn.execute(
            f"SELECT e.ts, e.id, ''{op_cols} FROM entry e{op_join} WHERE {where} ORDER BY e.ts, e.id",
            (tpl_id,),
        )
        return
    # template keys only (origin 0): payload keys serve the search, never the links; the index is named
    # because reading origin makes it non-covering and the planner would then scan the whole key type
    cur = conn.execute(
        f"SELECT e.ts, e.id, k.value{op_cols} FROM entry e JOIN entry_key k INDEXED BY ix_key_entry"
        f" ON k.entry_id = e.id AND k.key_type = ? AND k.origin = 0{op_join}"
        f" WHERE {where} ORDER BY e.ts, e.id",
        (key, tpl_id),
    )
    found = False
    for row in cur:
        found = True
        yield (row[0], row[1], str(row[2]), *row[3:])
    if found or not _FIELD_NAME.match(key):
        return
    # the key is a field name, not an entry_key type: read it from the stored fields
    cur = conn.execute(
        f"SELECT e.ts, e.id, json_extract(e.fields, ?){op_cols} FROM entry e{op_join}"
        f" WHERE {where} AND e.fields IS NOT NULL ORDER BY e.ts, e.id",
        (f'$."{key}"', tpl_id),
    )
    for row in cur:
        if row[2] is None or isinstance(row[2], (list, dict)):
            continue
        yield (row[0], row[1], str(row[2]), *row[3:])


def _anchor_sql(stream_ids: list[int]) -> str:
    """``(ts, id, operation id, operation end)`` of one template (``?``) in the streams, by (ts, id).

    The operation is the ``operation`` event of the same streams that the record STARTS (``entry.event_id``
    and ``start_entry_id``). Ordered like ``ix_entry_tpl_ts``, so SQLite never sorts (no temp B-tree).
    """
    streams = ",".join(str(int(s)) for s in stream_ids)
    return (
        "SELECT e.ts, e.id, ev.id, ev.ts_end FROM entry e LEFT JOIN event ev ON ev.id = e.event_id"
        f" AND ev.kind = 'operation' AND ev.start_entry_id = e.id AND ev.stream_id IN ({streams})"
        " WHERE e.tpl_id = ? AND e.ts IS NOT NULL AND e.source_id IN"
        f" (SELECT id FROM source WHERE stream_id IN ({streams})) ORDER BY e.ts, e.id"
    )


_OVERFLOW = -1  # entry id of the sentinel ``_anchors`` yields when the anchors in flight exceed the bound


def _anchors(
    conn: sqlite3.Connection, tpl_ids: list[int], stream_ids: list[int]
) -> Iterator[tuple[int, int, int, bool]]:
    """``(anchor, 0, id, closed)`` of the ``a`` records, by (anchor, id), without a database sort.

    The anchor is the end of the operation the record starts when that end is not before the record
    (``closed``); otherwise (no operation, open or truncated operation, odd end) the record's own ts. Records
    arrive by (ts, id) and every anchor is >= its ts, so an anchor is final once the read position passes it:
    a heap holds only the operations still in flight at the read position (concurrency, not history). More
    than ``MAX_OPEN_KEYS`` of them yields the sentinel ``(read ts, 0, _OVERFLOW, False)``, in order, and stops
    (beyond that point the order could no longer be guaranteed).
    """
    rows = heapq.merge(*(conn.execute(_anchor_sql(stream_ids), (t,)) for t in tpl_ids))
    pending: list[tuple[int, int, bool]] = []
    for ts, eid, _op, end in rows:
        while pending and pending[0][0] < ts:
            at, aid, closed = heapq.heappop(pending)
            yield (at, 0, aid, closed)
        closed = end is not None and end >= ts
        heapq.heappush(pending, (end if closed else ts, eid, closed))
        if len(pending) > MAX_OPEN_KEYS:
            yield (ts, 0, _OVERFLOW, False)  # every pending anchor is >= ts: the sentinel is in order
            return
    while pending:
        at, aid, closed = heapq.heappop(pending)
        yield (at, 0, aid, closed)


def _candidates(
    conn: sqlite3.Connection,
    tpl_id: int,
    stream_ids: list[int],
    key: str | None,
    rank: int,
    is_a: bool,
    is_b: bool,
) -> Iterator[_Event]:
    """Records of one template inside the streams, ordered by (ts, id), as events."""
    for ts, eid, value in _keyed_rows(conn, tpl_id, stream_ids, key):
        yield (ts, rank, eid, value, is_a, is_b)


def _events(
    conn: sqlite3.Connection, rule: Any, ids: dict[str, int], stream_ids: list[int], key: str | None
) -> Iterator[_Event]:
    a_codes, b_codes = set(_codes(rule["a"])), set(_codes(rule["b"]))
    gens = []
    for code in sorted(a_codes | b_codes):
        tpl = ids.get(code)
        if tpl is None:
            continue
        is_a, is_b = code in a_codes, code in b_codes
        gens.append(_candidates(conn, tpl, stream_ids, key, 0 if is_a and not is_b else 1, is_a, is_b))
    return heapq.merge(*gens)


# --------------------------------------------------------------------------- rule engines

_Link = tuple[int, int, str, int]


def _keyed_links(events: Iterator[_Event], kind: str, max_dt: int, counters: _Counters) -> Iterator[_Link]:
    """callback / response: each ``b`` to the latest earlier ``a`` with the same key value."""
    open_a: dict[str, list[int]] = {}  # value -> [entry_id, ts, links so far]
    for ts, _rank, eid, value, is_a, is_b in events:
        if is_b:
            cur = open_a.get(value)
            if cur is not None and cur[0] != eid and 0 <= ts - cur[1] <= max_dt:
                if cur[2] < MAX_B_PER_A:
                    cur[2] += 1
                    yield (cur[0], eid, kind, 3)
                else:
                    counters.dropped += 1
        if is_a:
            open_a.pop(value, None)
            open_a[value] = [eid, ts, 0]
            if len(open_a) > MAX_OPEN_KEYS:
                del open_a[next(iter(open_a))]
                counters.dropped += 1


def _retry_links(events: Iterator[_Event], max_dt: int, counters: _Counters) -> Iterator[_Link]:
    """retry: consecutive records of the same key value, the chain starting at an ``a`` record."""
    last: dict[str, tuple[int, int]] = {}  # value -> (entry_id, ts) of the last attempt
    for ts, _rank, eid, value, is_a, is_b in events:
        prev = last.get(value)
        linked = False
        if is_b and prev is not None and prev[0] != eid and 0 <= ts - prev[1] <= max_dt:
            yield (prev[0], eid, "retry", 3)
            linked = True
        if is_a or linked:
            last.pop(value, None)
            last[value] = (eid, ts)
            if len(last) > MAX_OPEN_KEYS:
                del last[next(iter(last))]
                counters.dropped += 1
        elif prev is not None:
            last.pop(value, None)  # the chain broke (gap beyond max_dt_ms): wait for a new ``a``


def _window_links(events: Iterator[_Event], kind: str, max_dt: int, counters: _Counters) -> Iterator[_Link]:
    """window_only: ``b`` to the single unconsumed ``a`` within the window; ambiguous cases link nothing."""
    open_a: deque[tuple[int, int]] = deque()
    for ts, _rank, eid, _value, is_a, is_b in events:
        if is_b:
            while open_a and ts - open_a[0][1] > max_dt:
                open_a.popleft()
            live = [a for a in open_a if a[0] != eid]
            if len(live) == 1:
                open_a.remove(live[0])
                yield (live[0][0], eid, kind, 0)
            elif len(live) > 1:
                counters.ambiguous += 1
        if is_a:
            open_a.append((eid, ts))
            if len(open_a) > MAX_OPEN_KEYS:
                open_a.popleft()
                counters.dropped += 1


class _OriginKey:
    """What the ``a`` records of one key value say about its origin operation."""

    __slots__ = ("op", "start", "ts", "mixed")

    def __init__(self, op: int | None, start: int | None, ts: int) -> None:
        self.op, self.start, self.ts, self.mixed = op, start, [ts], False

    def add(self, op: int | None, start: int | None, ts: int) -> None:
        if op != self.op or start != self.start or len(self.ts) >= MAX_A_PER_KEY:
            self.mixed = True
        else:
            self.ts.append(ts)  # rows arrive merged by (ts, id): the list stays sorted


def _origin_links(
    conn: sqlite3.Connection,
    rule: Any,
    ids: dict[str, int],
    stream_ids: list[int],
    max_dt: int,
    counters: _Counters,
) -> Iterator[_Link]:
    """origin = "operation": each ``b`` to the start record of the one operation holding its key's ``a``."""
    key = rule["key"]
    a_gens = [
        _keyed_rows(conn, ids[c], stream_ids, key, with_operation=True)
        for c in sorted(set(_codes(rule["a"])))
        if c in ids
    ]
    origins: dict[str, _OriginKey] = {}
    for ts, _eid, value, op, start in heapq.merge(*a_gens):
        cur = origins.get(value)
        if cur is not None:
            cur.add(op, start, ts)
        elif len(origins) < MAX_OPEN_KEYS:
            origins[value] = _OriginKey(op, start, ts)
        else:
            counters.dropped += 1  # the value stays unknown: its ``b`` count as unmatched, never guessed
    fanout: dict[int, int] = {}
    b_gens = [_keyed_rows(conn, ids[c], stream_ids, key) for c in sorted(set(_codes(rule["b"]))) if c in ids]
    for ts, eid, value in heapq.merge(*b_gens):
        cur = origins.get(value)
        if cur is None:
            counters.unmatched += 1
            continue
        if cur.mixed:
            counters.ambiguous += 1
            continue
        if cur.op is None or cur.start is None or cur.start == eid:
            counters.no_operation += 1
            continue
        i = bisect.bisect_right(cur.ts, ts)
        if i == 0 or ts - cur.ts[i - 1] > max_dt:
            counters.out_of_window += 1
            continue
        n = fanout.get(cur.start, 0)
        if n >= MAX_B_PER_A:
            counters.dropped += 1
            continue
        fanout[cur.start] = n + 1
        yield (cur.start, eid, rule["kind"], ORIGIN_CONFIDENCE)


class _Anchor:
    """An ``a`` record of a window origin rule while ``b`` records can still fall in its window."""

    __slots__ = ("at", "eid", "closed", "b_before", "pending", "pending_b")

    def __init__(self, at: int, eid: int, closed: bool, b_before: int) -> None:
        self.at, self.eid, self.closed, self.b_before = at, eid, closed, b_before
        self.pending = 0  # ``b`` records for which this was the only ``a`` in the window
        self.pending_b = 0


def _window_origin_links(
    conn: sqlite3.Connection,
    rule: Any,
    ids: dict[str, int],
    stream_ids: list[int],
    max_dt: int,
    counters: _Counters,
) -> Iterator[_Link]:
    """window_only + origin = "operation": ``b`` to the ``a`` whose operation ended just before it.

    The match must be unique both ways (one ``a`` in the window of the ``b``, one ``b`` in that of the ``a``).

    One sweep over the ``a`` anchors and the ``b`` records merged by time (an anchor before a ``b`` with the
    same ts); memory is the anchors of the last ``max_dt`` ms. How many ``b`` fall in the window of an anchor
    is the difference of the running ``b`` count, taken when the sweep passes ``anchor + max_dt``.

    Memory bounds never produce a link: an anchor evicted from the window (more than ``MAX_OPEN_KEYS``)
    leaves ``evicted_until`` behind, and every ``b`` whose window could reach it (``ts <= evicted_until``)
    is counted as ``dropped``, as are the ``b`` pending on it; an overflow of the anchors in flight
    (``_anchors``) stops the partition at that time and counts every ``b`` not yet decided as ``dropped``.
    """
    a_tpls = [ids[c] for c in sorted(set(_codes(rule["a"]))) if c in ids]
    a_gens = [_anchors(conn, a_tpls, stream_ids)] if a_tpls else []
    b_gens = [
        ((ts, 1, eid, False) for ts, eid, _v in _keyed_rows(conn, ids[c], stream_ids, None))
        for c in sorted(set(_codes(rule["b"])))
        if c in ids
    ]
    window: deque[_Anchor] = deque()
    n_a = n_b = no_anchor = 0
    evicted_until: int | None = None  # latest ``anchor + max_dt`` of an anchor dropped by the bound
    overflow = False

    def close(w: _Anchor) -> Iterator[_Link]:
        if not w.pending:
            return
        if w.pending == 1 and n_b - w.b_before == 1:
            if w.closed:
                yield (w.eid, w.pending_b, rule["kind"], 0)
            else:
                counters.no_operation += 1
        else:
            counters.ambiguous += w.pending

    events = heapq.merge(*a_gens, *b_gens)
    for t, is_b, eid, closed in events:
        while window and window[0].at + max_dt < t:
            yield from close(window.popleft())  # every anchor before t is known: these are final
        if eid == _OVERFLOW:
            # the later anchors cannot be ordered: decide nothing from here on in this partition
            overflow = True
            n_a += 1  # requests exist even if none was emitted yet: earlier lone ``b`` are out_of_window
            counters.dropped += sum(1 for ev in events if ev[1])
            break
        if not is_b:
            n_a += 1
            window.append(_Anchor(t, eid, closed, n_b))
            if len(window) > MAX_OPEN_KEYS:
                gone = window.popleft()
                counters.dropped += gone.pending  # its pending ``b`` are never linked
                evicted_until = gone.at + max_dt
            continue
        n_b += 1
        if evicted_until is not None and t <= evicted_until:
            counters.dropped += 1  # an evicted anchor is (or may be) in its window: never guess
            continue
        if not window:
            no_anchor += 1
        elif len(window) > 1:
            counters.ambiguous += 1
        else:
            window[0].pending += 1
            window[0].pending_b = eid
    if overflow:  # the ``b`` pending on anchors still open are not decided either
        counters.dropped += sum(w.pending for w in window)
        window.clear()
    while window:
        yield from close(window.popleft())
    # a ``b`` with an empty window saw every anchor up to its ts (the merge orders them before it)
    if n_a:
        counters.out_of_window += no_anchor
    else:
        counters.unmatched += no_anchor


class _HandoffA:
    """An ``a`` record of a handoff rule while a ``b`` can still fall in its window."""

    __slots__ = ("ts", "eid", "value", "pid", "state", "snap", "sole", "first")

    def __init__(self, ts: int, eid: int, value: str, pid: str | None, state: _ValueState) -> None:
        self.ts, self.eid, self.value, self.pid = ts, eid, value, pid
        self.state = state
        self.snap = state.b_seen(pid)  # ``b`` records compatible with this PID seen before it arrived
        self.sole = 0  # compatible ``b`` that have no other compatible ``a``
        self.first: tuple[int, int] | None = None  # (entry id, confidence) of the first such ``b``

    @property
    def degree(self) -> int:
        """Compatible ``b`` records (the ambiguous ones count), read from the counters of its key value."""
        return self.state.b_seen(self.pid) - self.snap


class _ValueState:
    """The open ``a`` of one key value, grouped by PID, and how many ``b`` of each PID class were seen.

    A ``b`` with an unknown PID is compatible with every ``a`` of the value; one with the PID ``p`` with the
    ``a`` of PID ``p`` and those of unknown PID. So the degree of a ``b`` and of an ``a`` come from counters,
    without visiting the ``a`` records one by one (a key repeated in the window stays linear).
    """

    __slots__ = ("alive", "by_pid", "n_all", "n_unknown", "n_pid")

    def __init__(self) -> None:
        self.alive: deque[_HandoffA] = deque()  # every open ``a`` of the value, oldest first
        self.by_pid: dict[str | None, deque[_HandoffA]] = {}  # the same, by PID (None: unknown)
        self.n_all = 0  # ``b`` records seen while the state lives
        self.n_unknown = 0  # ... with an unknown PID
        self.n_pid: dict[str, int] = {}  # ... with a known PID

    def b_seen(self, pid: str | None) -> int:
        if pid is None:
            return self.n_all
        return self.n_unknown + self.n_pid.get(pid, 0)

    def degree_of_b(self, pid: str | None) -> int:
        if pid is None:
            return len(self.alive)
        return len(self.by_pid.get(pid, ())) + len(self.by_pid.get(None, ()))

    def sole_candidate(self, pid: str | None) -> _HandoffA:
        """The only compatible ``a`` (call only when ``degree_of_b`` is 1)."""
        if pid is None:
            return self.alive[0]
        same = self.by_pid.get(pid)
        return same[0] if same else self.by_pid[None][0]

    def add_b(self, pid: str | None) -> None:
        self.n_all += 1
        if pid is None:
            self.n_unknown += 1
        elif pid in self.by_pid:  # only an open ``a`` of that PID reads this counter (by difference)
            self.n_pid[pid] = self.n_pid.get(pid, 0) + 1


def _pid_text(value: Any) -> str | None:
    """A PID as comparable text; None when unknown (absent, empty or not a plain number or word)."""
    if value is None or isinstance(value, (bool, list, dict, float)) or value == "":
        return None
    return str(value)


def _handoff_rows(
    conn: sqlite3.Connection, tpl_id: int, stream_ids: list[int], key: str, pid_field: str | None, is_a: bool
) -> Iterator[tuple[int, int, str, str | None]]:
    """``(ts, id, key value, pid)`` of one template in the streams, by (ts, id).

    ``pid`` is the envelope PID of a ``b`` record and, for an ``a`` record, the stored field ``pid_field``
    (None without ``pid_field``). Same key reading as ``_keyed_rows`` (template keys, else the stored field).
    """
    streams = ",".join(str(int(s)) for s in stream_ids)
    where = (
        "e.tpl_id = ? AND e.ts IS NOT NULL AND e.source_id IN"
        f" (SELECT id FROM source WHERE stream_id IN ({streams}))"
    )
    if not is_a:
        pid_sql, pid_args = "e.pid", []
    elif pid_field is not None:
        pid_sql, pid_args = "json_extract(e.fields, ?)", [f'$."{pid_field}"']
    else:
        pid_sql, pid_args = "NULL", []
    cur = conn.execute(
        f"SELECT e.ts, e.id, k.value, {pid_sql} FROM entry e JOIN entry_key k INDEXED BY ix_key_entry"
        f" ON k.entry_id = e.id AND k.key_type = ? AND k.origin = 0 WHERE {where} ORDER BY e.ts, e.id",
        (*pid_args, key, tpl_id),
    )
    found = False
    for ts, eid, value, pid in cur:
        found = True
        yield (ts, eid, str(value), _pid_text(pid))
    if found or not _FIELD_NAME.match(key):
        return
    cur = conn.execute(
        f"SELECT e.ts, e.id, json_extract(e.fields, ?), {pid_sql} FROM entry e"
        f" WHERE {where} AND e.fields IS NOT NULL ORDER BY e.ts, e.id",
        (f'$."{key}"', *pid_args, tpl_id),
    )
    for ts, eid, value, pid in cur:
        if value is None or isinstance(value, (list, dict)):
            continue
        yield (ts, eid, str(value), _pid_text(pid))


def _coverage(conn: sqlite3.Connection, tpl_ids: list[int], stream_ids: list[int]) -> tuple[int, int] | None:
    """Time span ``(first_ts, last_ts)`` of the sources that hold records of the templates (None: none)."""
    streams = ",".join(str(int(s)) for s in stream_ids)
    sources: set[int] = set()
    for tpl in tpl_ids:
        sources.update(
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT e.source_id FROM entry e WHERE e.tpl_id = ? AND e.source_id IN"
                f" (SELECT id FROM source WHERE stream_id IN ({streams}))",
                (tpl,),
            )
        )
    if not sources:
        return None
    ids = ",".join(str(int(i)) for i in sorted(sources))
    lo, hi = conn.execute(f"SELECT min(first_ts), max(last_ts) FROM source WHERE id IN ({ids})").fetchone()
    return None if lo is None or hi is None else (lo, hi)


def _covered(span: tuple[int, int] | None, ts: int) -> bool:
    return span is not None and span[0] <= ts <= span[1]


def _handoff_links(
    conn: sqlite3.Connection,
    rule: Any,
    ids: dict[str, int],
    stream_ids: list[int],
    max_dt: int,
    counters: _Counters,
) -> Iterator[_Link]:
    """handoff: ``b`` to the one ``a`` of the same key value within ``max_dt`` before it, unique both ways.

    The sweep merges the ``a`` and ``b`` records by ``(ts, rank, id)`` (an ``a`` before a ``b`` of the same
    ts) and keeps the ``a`` of the last ``max_dt`` ms. Every decision uses only the set of
    ``(ts, value, pid)`` of the records, never the ids or the order inside one ts: an ``a`` is decided when
    the sweep passes ``a.ts + max_dt`` (it links when it and its only ``b`` are each other's only match).
    ``MAX_OPEN_KEYS`` is checked only when the ts advances, over the ``a`` of the window: past it the
    partition stops and everything undecided is counted ``dropped`` (nothing is guessed).
    """
    key, pid_field = rule["key"], rule.get("pid_field")
    a_tpls = [ids[c] for c in sorted(set(_codes(rule["a"]))) if c in ids]
    b_tpls = [ids[c] for c in sorted(set(_codes(rule["b"]))) if c in ids]
    a_span = _coverage(conn, a_tpls, stream_ids)
    b_span = _coverage(conn, b_tpls, stream_ids)
    a_gens = (
        (
            (ts, 0, eid, value, pid)
            for ts, eid, value, pid in _handoff_rows(conn, t, stream_ids, key, pid_field, True)
        )
        for t in a_tpls
    )
    b_gens = (
        (
            (ts, 1, eid, value, pid)
            for ts, eid, value, pid in _handoff_rows(conn, t, stream_ids, key, None, False)
        )
        for t in b_tpls
    )
    events = heapq.merge(*a_gens, *b_gens)
    order: deque[_HandoffA] = deque()  # undecided ``a`` by (ts, id)
    by_value: dict[str, _ValueState] = {}

    def decide(a: _HandoffA) -> Iterator[_Link]:
        if a.degree == 1 and a.sole == 1 and a.first is not None:
            yield (a.eid, a.first[0], "handoff", a.first[1])
        elif a.sole:
            counters.ambiguous += a.sole  # their only ``a`` has other ``b`` too
        elif a.degree == 0 and _covered(b_span, a.ts):
            counters.unanswered += 1

    def expire(now: int) -> Iterator[_Link]:
        while order and order[0].ts + max_dt < now:
            a = order.popleft()
            state = by_value[a.value]
            yield from decide(a)  # reads the counters of its PID: forget them only afterwards
            state.alive.popleft()  # the oldest of its value (and of its PID) is the oldest overall
            pid_queue = state.by_pid[a.pid]
            pid_queue.popleft()
            if not pid_queue:
                del state.by_pid[a.pid]
                if a.pid is not None:
                    state.n_pid.pop(a.pid, None)  # no open ``a`` of this PID: a later one starts from 0
            if not state.alive:
                del by_value[a.value]

    def overflowed() -> bool:
        """The bound, checked at the end of every ts (the last one included), whatever comes after it."""
        return len(order) > MAX_OPEN_KEYS

    last_ts: int | None = None
    for ts, rank, eid, value, pid in events:
        if last_ts is not None and ts > last_ts and overflowed():
            counters.dropped += sum(a.sole for a in order)
            counters.dropped += (rank == 1) + sum(1 for ev in events if ev[1] == 1)
            return
        last_ts = ts
        yield from expire(ts)
        if rank == 0:
            state = by_value.get(value)
            if state is None:
                state = by_value[value] = _ValueState()
            a = _HandoffA(ts, eid, value, pid, state)
            order.append(a)
            state.alive.append(a)
            state.by_pid.setdefault(pid, deque()).append(a)
            continue
        state = by_value.get(value)
        known = pid_field is not None and pid is not None
        bpid = pid if known else None
        degree = 0 if state is None else state.degree_of_b(bpid)
        if state is None:
            if _covered(a_span, ts):
                counters.unmatched += 1
            else:
                counters.out_of_coverage += 1
            continue
        if degree == 0:
            counters.pid_mismatch += 1  # the value is open, but every ``a`` has another PID
        elif degree > 1:
            counters.ambiguous += 1
        elif degree == 1:
            a = state.sole_candidate(bpid)
            a.sole += 1
            if a.sole == 1:
                checked = pid_field is None or (known and a.pid is not None)
                a.first = (eid, 3 if checked else 2)
        state.add_b(bpid)
    if overflowed():  # the last ts is checked like the others: nothing is decided past the bound
        counters.dropped += sum(a.sole for a in order)
        return
    while order:
        yield from decide(order.popleft())


# --------------------------------------------------------------------------- a group


def _partitions(
    streams: list[_Stream], scope: str, family: str, families: tuple[str, ...] = ()
) -> list[list[int]]:
    """Stream id lists in which a rule of ``family`` looks for its records.

    ``scope = "collection"``: one list with every stream of the family and of ``families`` (none when the
    group has no stream of ``family`` itself).
    """
    mine = [s for s in streams if s.family == family]
    if scope == "collection":
        wanted = {family, *families}
        return [[s.id for s in streams if s.family in wanted]] if mine else []
    if scope == "stream":
        return [[s.id] for s in mine]
    by_process: dict[tuple[Any, Any], list[int]] = {}
    for s in mine:
        by_process.setdefault((s.process, s.pid), []).append(s.id)
    return list(by_process.values())


def _rule_links(
    conn: sqlite3.Connection,
    streams: list[_Stream],
    family: str,
    rule: Any,
    ids: dict[str, int],
    counters: _Counters,
) -> Iterator[_Link]:
    kind = rule["kind"]
    window_only = bool(rule.get("window_only", False))
    key = None if window_only else rule.get("key")
    max_dt = _max_dt(rule)
    families = tuple(rule.get("families", ()))
    for stream_ids in _partitions(streams, rule.get("scope", "stream"), family, families):
        if kind == "handoff":
            yield from _handoff_links(conn, rule, ids, stream_ids, max_dt, counters)
            continue
        if _window_origin(rule):
            yield from _window_origin_links(conn, rule, ids, stream_ids, max_dt, counters)
            continue
        if _origin(rule):
            yield from _origin_links(conn, rule, ids, stream_ids, max_dt, counters)
            continue
        events = _events(conn, rule, ids, stream_ids, key)
        if window_only:
            yield from _window_links(events, kind, max_dt, counters)
        elif kind == "retry":
            yield from _retry_links(events, max_dt, counters)
        else:
            yield from _keyed_links(events, kind, max_dt, counters)


def _compute(
    conn: sqlite3.Connection,
    streams: list[_Stream],
    rules: dict[str, list[Any]],
    ids: dict[str, int],
    stats: dict[str, _Counters],
    problems: list[str],
) -> Iterator[_Link]:
    for family in sorted(rules):
        for rule in rules[family]:
            problem = _rule_problem(rule)
            if problem is not None:
                problems.append(f"{family}: {problem}")
                continue
            counters = stats.setdefault(_rule_label(family, rule), _Counters())
            for link in _rule_links(conn, streams, family, rule, ids, counters):
                counters.linked += 1
                yield link


def _recompute_group(
    conn: sqlite3.Connection,
    group: int,
    streams: list[_Stream],
    rules: dict[str, list[Any]],
    ids: dict[str, int],
    signature: str,
    stats: dict[str, _Counters],
    problems: list[str],
) -> int:
    kinds = ",".join(f"'{k}'" for k in KINDS)
    conn.execute(
        f"DELETE FROM link WHERE kind IN ({kinds}) AND EXISTS"
        " (SELECT 1 FROM entry e WHERE e.id = link.a_entry_id"
        " AND e.source_id IN (SELECT s.id FROM source s JOIN stream st ON st.id = s.stream_id"
        " WHERE st.collection_group = ?))",
        (group,),
    )
    conn.execute(
        f"DELETE FROM link WHERE kind IN ({kinds}) AND NOT EXISTS"
        " (SELECT 1 FROM entry e WHERE e.id = link.a_entry_id)"
    )
    inserted = 0
    batch: list[_Link] = []
    sql = "INSERT OR IGNORE INTO link(a_entry_id, b_entry_id, kind, confidence) VALUES (?, ?, ?, ?)"
    for row in _compute(conn, streams, rules, ids, stats, problems):
        batch.append(row)
        if len(batch) >= BATCH:
            inserted += conn.executemany(sql, batch).rowcount
            batch.clear()
    if batch:
        inserted += conn.executemany(sql, batch).rowcount
    value = {"sig": signature, "rules": {label: c.as_dict() for label, c in sorted(stats.items())}}
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"{META_PREFIX}{group}", json.dumps(value, sort_keys=True)),
    )
    return inserted


def _stored(raw: Any) -> dict[str, Any]:
    """The stored meta value as a dict (``{}`` when absent or in an older format)."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def link_stats(conn: sqlite3.Connection, group: int) -> dict[str, dict[str, int]]:
    """Counters of the last run of the group, per rule label ``"<family>/<kind>:<a>><b>[@operation[-end]]"``.

    Each value has ``linked`` and, when not zero, ``dropped``, ``ambiguous``, ``unmatched``, ``no_operation``,
    ``out_of_window``, ``out_of_coverage``, ``pid_mismatch`` and ``unanswered``. Empty when never linked.
    """
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"{META_PREFIX}{group}",)).fetchone()
    rules = _stored(row[0] if row else None).get("rules")
    if not isinstance(rules, dict):
        return {}
    return {str(k): dict(v) for k, v in rules.items() if isinstance(v, dict)}


def link_pending(conn: sqlite3.Connection, catalog: Any, kb: Any = None) -> LinkSummary:
    """Create the callback/retry/response/handoff links of every collection group changed since the last run.

    A group that fails is rolled back and reported in ``errors``; the others still run. ``kb`` is accepted for
    the pipeline signature and unused.
    """
    summary = LinkSummary()
    try:
        groups = _group_streams(conn)
        ids = _template_ids(conn)
    except sqlite3.Error as exc:
        summary.errors.append((0, f"{type(exc).__name__}: {exc}"))
        return summary
    for group, streams in sorted(groups.items()):
        rules = {fam: r for fam in sorted({s.family for s in streams}) if (r := _family_rules(catalog, fam))}
        savepoint = f"correlate_links_{group}"
        own_tx = not conn.in_transaction
        try:
            signature = _signature(conn, streams, rules, ids, group)
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"{META_PREFIX}{group}",)).fetchone()
            if row is not None and _stored(row[0]).get("sig") == signature:
                continue
            if not rules and row is None:
                continue  # nothing to correlate and nothing stored: leave the database untouched
            stats: dict[str, _Counters] = {}
            problems: list[str] = []
            if own_tx:
                conn.execute("BEGIN")
            conn.execute(f"SAVEPOINT {savepoint}")
            inserted = _recompute_group(conn, group, streams, rules, ids, signature, stats, problems)
            if own_tx:
                conn.execute("COMMIT")  # ends the transaction with the savepoint still active
            else:
                conn.execute(f"RELEASE {savepoint}")
        except Exception as exc:  # correlation must never abort an ingestion
            _undo(conn, savepoint, own_tx)
            summary.errors.append((group, f"{type(exc).__name__}: {exc}"))
            continue
        summary.groups += 1
        summary.links += inserted
        for counters in stats.values():
            counters.add_to(summary)
        summary.errors.extend((group, p) for p in problems)
    return summary
