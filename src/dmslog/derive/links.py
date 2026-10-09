"""Callback, retry and response links driven by ``[[link_rule]]`` tables (ARQUITETURA 8).

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
* ``window_only = true`` (no key; Wfm "Callback received"): a ``b`` is linked to the one unconsumed ``a`` that
  precedes it within ``max_dt_ms``, only when that ``a`` is unambiguous (exactly one open ``a``); the link has
  confidence 0 and the ``a`` is consumed. With two or more open ``a`` nothing is linked (counted as
  ``ambiguous``).

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
    a = "WFM-123"
    b = "WFM-126"
    window_only = true
    max_dt_ms = 30000

Execution: ``link_pending`` works per collection group. A group is recomputed only when the signature in
``meta['correlate.links.<group>']`` (sources of its streams, rules, template ids) changed; an unchanged group
writes nothing. Recomputing deletes the links of this module's kinds that start in the group, inserts the new
ones and stores the signature, all inside one SAVEPOINT. Candidates are read through ``ix_entry_tpl_ts`` by
template id, one cursor per code, merged in time order; memory is bounded (``MAX_OPEN_KEYS`` open keys,
``MAX_B_PER_A`` links per ``a``), and what is dropped by those bounds is counted in ``LinkSummary.dropped``.
Odd data (``NULL`` ts or key) is skipped, never raised.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import sqlite3
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from dmslog.derive.engine import _undo

KINDS = ("callback", "retry", "response")
DEFAULT_MAX_DT_MS = 60000
MAX_OPEN_KEYS = 200_000  # open keys (or open window records) kept per rule and partition
MAX_B_PER_A = 64  # callbacks linked to one ``a`` record
BATCH = 5000
META_PREFIX = "correlate.links."
_SIGNATURE_VERSION = "1"
_FIELD_NAME = re.compile(r"^\w+$")

# (ts, rank, entry_id, key value, is_a, is_b); rank puts a pure ``a`` before a ``b`` with the same ts
_Event = tuple[int, int, int, str, bool, bool]


@dataclass
class LinkSummary:
    links: int = 0  # rows inserted in ``link``
    groups: int = 0  # collection groups recomputed (unchanged ones are skipped)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (collection_group, message)
    dropped: int = 0  # candidates lost to the memory bounds (open keys, links per ``a``)
    ambiguous: int = 0  # window_only ``b`` records not linked because several ``a`` were open


@dataclass
class _Counters:
    dropped: int = 0
    ambiguous: int = 0


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


def _rule_problem(rule: Any) -> str | None:
    """Why a rule cannot run (None when it can)."""
    if rule.get("kind") not in KINDS:
        return f"unknown link kind {rule.get('kind')!r}"
    if not _codes(rule.get("a")) or not _codes(rule.get("b")):
        return "rule without a/b codes"
    window_only = bool(rule.get("window_only", False))
    if window_only and rule["kind"] == "retry":
        return "window_only is not valid for retry rules"
    key = rule.get("key")
    if not window_only and not (isinstance(key, str) and key):
        return "rule without key (and not window_only)"
    if rule.get("scope", "stream") not in ("stream", "group"):
        return f"unknown scope {rule.get('scope')!r}"
    return None


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
    conn: sqlite3.Connection, streams: list[_Stream], rules: dict[str, list[Any]], ids: dict[str, int]
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
    for family in sorted(rules):
        for rule in rules[family]:
            used.update(_codes(rule.get("a")) + _codes(rule.get("b")))
    parts.append({fam: [dict(r) for r in rules[fam]] for fam in sorted(rules)})
    defs = dict(conn.execute("SELECT code, def_version FROM template").fetchall())
    parts.append({c: [ids.get(c), defs.get(c)] for c in sorted(used)})
    row = conn.execute("SELECT value FROM meta WHERE key = 'entries_epoch'").fetchone()
    parts.append(row[0] if row else None)
    return _digest(*parts)


# --------------------------------------------------------------------------- candidate reading


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
    streams = ",".join(str(int(s)) for s in stream_ids)
    where = (
        "e.tpl_id = ? AND e.ts IS NOT NULL AND e.source_id IN"
        f" (SELECT id FROM source WHERE stream_id IN ({streams}))"
    )
    if key is None:
        cur = conn.execute(f"SELECT e.ts, e.id, '' FROM entry e WHERE {where} ORDER BY e.ts, e.id", (tpl_id,))
        for ts, eid, value in cur:
            yield (ts, rank, eid, value, is_a, is_b)
        return
    cur = conn.execute(
        f"SELECT e.ts, e.id, k.value FROM entry e JOIN entry_key k ON k.entry_id = e.id AND k.key_type = ?"
        f" WHERE {where} ORDER BY e.ts, e.id",
        (key, tpl_id),
    )
    found = False
    for ts, eid, value in cur:
        found = True
        yield (ts, rank, eid, str(value), is_a, is_b)
    if found or not _FIELD_NAME.match(key):
        return
    # the key is a field name, not an entry_key type: read it from the stored fields
    cur = conn.execute(
        f"SELECT e.ts, e.id, json_extract(e.fields, ?) FROM entry e WHERE {where} AND e.fields IS NOT NULL"
        f" ORDER BY e.ts, e.id",
        (f'$."{key}"', tpl_id),
    )
    for ts, eid, value in cur:
        if value is None or isinstance(value, (list, dict)):
            continue
        yield (ts, rank, eid, str(value), is_a, is_b)


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


# --------------------------------------------------------------------------- a group


def _partitions(streams: list[_Stream], scope: str, family: str) -> list[list[int]]:
    """Stream id lists in which a rule of ``family`` looks for its records."""
    mine = [s for s in streams if s.family == family]
    if scope == "stream":
        return [[s.id] for s in mine]
    by_process: dict[tuple[Any, Any], list[int]] = {}
    for s in mine:
        by_process.setdefault((s.process, s.pid), []).append(s.id)
    return list(by_process.values())


def _compute(
    conn: sqlite3.Connection,
    streams: list[_Stream],
    rules: dict[str, list[Any]],
    ids: dict[str, int],
    counters: _Counters,
    problems: list[str],
) -> Iterator[_Link]:
    for family in sorted(rules):
        for rule in rules[family]:
            problem = _rule_problem(rule)
            if problem is not None:
                problems.append(f"{family}: {problem}")
                continue
            kind = rule["kind"]
            window_only = bool(rule.get("window_only", False))
            key = None if window_only else rule["key"]
            max_dt = _max_dt(rule)
            for stream_ids in _partitions(streams, rule.get("scope", "stream"), family):
                events = _events(conn, rule, ids, stream_ids, key)
                if window_only:
                    yield from _window_links(events, kind, max_dt, counters)
                elif kind == "retry":
                    yield from _retry_links(events, max_dt, counters)
                else:
                    yield from _keyed_links(events, kind, max_dt, counters)


def _recompute_group(
    conn: sqlite3.Connection,
    group: int,
    streams: list[_Stream],
    rules: dict[str, list[Any]],
    ids: dict[str, int],
    signature: str,
    counters: _Counters,
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
    for row in _compute(conn, streams, rules, ids, counters, problems):
        batch.append(row)
        if len(batch) >= BATCH:
            inserted += conn.executemany(sql, batch).rowcount
            batch.clear()
    if batch:
        inserted += conn.executemany(sql, batch).rowcount
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"{META_PREFIX}{group}", signature),
    )
    return inserted


def link_pending(conn: sqlite3.Connection, catalog: Any, kb: Any = None) -> LinkSummary:
    """Create the callback/retry/response links of every collection group that changed since the last run.

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
            signature = _signature(conn, streams, rules, ids)
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"{META_PREFIX}{group}",)).fetchone()
            if row is not None and row[0] == signature:
                continue
            if not rules and row is None:
                continue  # nothing to correlate and nothing stored: leave the database untouched
            counters = _Counters()
            problems: list[str] = []
            if own_tx:
                conn.execute("BEGIN")
            conn.execute(f"SAVEPOINT {savepoint}")
            inserted = _recompute_group(conn, group, streams, rules, ids, signature, counters, problems)
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
        summary.dropped += counters.dropped
        summary.ambiguous += counters.ambiguous
        summary.errors.extend((group, p) for p in problems)
    return summary
