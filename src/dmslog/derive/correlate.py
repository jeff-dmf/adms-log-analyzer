"""Normal <-> sensitive pairing (ARQUITETURA 8, first row; ROADMAP 5.1).

Unit of work: a COLLECTION GROUP (``stream.collection_group``). Inside it, a normal stream and a sensitive
stream of the same family, process and pid form a pair of streams. For every ``[[sensitive_pair]]`` rule of
the family, each record of the rule's ``normal`` template is matched with the next unconsumed record of the
``sensitive`` template on the same TID (``match`` without ``"tid"`` ignores the TID) that is at most
``max_dt_ms`` later (``order = "after"``, the default) or up to ``ANY_ORDER_TOLERANCE_MS`` earlier or
``max_dt_ms`` later (``order = "any"``). Each sensitive record is consumed at most once, in order.

Confidence of a ``normal_sensitive`` link (``a`` = normal record, ``b`` = sensitive record):

- 3: the rule has a ``key`` field, both records expose it (in ``fields`` or as an ``entry_key`` of that
  type) and the values are equal. The key decides among the candidates in the time window (same
  ``max_dt_ms`` / order rules), and the TID only when ``match`` has it.
- 2: same timestamp (0 ms) and same TID.
- 1: any other time/order based match (same second in practice).

Only normal records inside the INTERSECTION of the covered periods (min/max ``ts``) of the two streams are
considered and counted; outside it the other file has no data, so the absence of a partner means nothing.

Idempotence: the signature of a group (sources of its streams, rules, templates) is stored in
``meta['correlate.<group>']`` together with the pairing statistics (``pair_stats``). A group whose signature
did not change writes nothing; a changed group deletes its ``normal_sensitive`` links, recomputes and inserts
them in one SAVEPOINT. Candidates are read through ``ix_entry_tpl_ts`` (by template), never by a full scan.

Memory and time: both candidate sets are read by cursor in (ts, id) order and merged incrementally
(``pair_stream``): only the sensitive records inside the matching window are buffered (capped by
``MAX_BUFFER``), each looked at O(1) times, and links are flushed to the database in batches of
``INSERT_BATCH``. ``MAX_CANDIDATES_PER_RULE`` caps the normal records read; what the caps drop is counted
in ``CorrelateSummary.dropped_by_rule``.

Staleness: the signature includes every source's size/mtime/CRC/hashes/counts, the rules, the templates the
rules use and the group's entries epoch (``meta['entries_epoch.<group>']``, bumped by the writer when it
deletes entries of the group, which also deletes the links incident to them; ``engine.group_epoch``).
A recompute also deletes this engine's orphan links.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from dmslog.derive.engine import group_epoch

KIND = "normal_sensitive"
META_PREFIX = "correlate."
FLAG_REF_SENSITIVE = 4
ANY_ORDER_TOLERANCE_MS = 2100
DEFAULT_MAX_DT_MS = 1000
DEFAULT_MATCH = ("pid", "tid")
INSERT_BATCH = 5000
KEY_CHUNK = 500
CHUNK = 5000  # rows per fetchmany
MAX_CANDIDATES_PER_RULE = 5_000_000  # normal records read per rule and stream pair; the rest is dropped
COMPACT_MIN = 1024  # consumed records tolerated in the buffer before it is compacted
MAX_BUFFER = 500_000  # sensitive records buffered at once; the oldest are dropped first
EPOCH_KEY = "entries_epoch"  # meta counter bumped by the writer whenever it deletes entries


@dataclass
class CorrelateSummary:
    links: int = 0  # links written in this run
    groups: int = 0  # groups recomputed in this run (unchanged groups are not counted)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (collection group, message)
    # normal template code -> normal records in the window without the refs_sensitive flag (sanity check)
    unflagged: dict[str, int] = field(default_factory=dict)
    # rule label -> {"dropped": n}: candidates left out by MAX_CANDIDATES_PER_RULE / MAX_BUFFER
    dropped_by_rule: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def dropped(self) -> int:
        return sum(c.get("dropped", 0) for c in self.dropped_by_rule.values())


# --------------------------------------------------------------------------- helpers


def _digest(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def _undo(conn: sqlite3.Connection, savepoint: str, own_tx: bool) -> None:
    """Undo the writes made since the savepoint; never raises (the original error must surface)."""
    if not conn.in_transaction:
        return
    try:
        if own_tx:
            conn.execute("ROLLBACK")
        else:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
    except sqlite3.Error:
        pass


def _rules_of(catalog: Any, family: str) -> tuple[dict[str, Any], ...]:
    """The ``[[sensitive_pair]]`` rules of a family (empty for unknown families or odd catalogs)."""
    for sensitive in (False, True):
        try:
            rules = catalog.for_family(family, sensitive=sensitive)
            pairs = rules.sensitive_pairs()
        except Exception:  # an unknown family never stops the correlation
            continue
        if pairs:
            return tuple(dict(p) for p in pairs)
    return ()


def _label(rule: dict[str, Any]) -> str:
    return f"{rule.get('normal')}>{rule.get('sensitive')}"


def _stream_pairs(conn: sqlite3.Connection, group: int) -> list[tuple[int, int, str, Any, Any]]:
    """``[(normal_stream, sensitive_stream, family, process, pid)]`` of the group."""
    rows = conn.execute(
        "SELECT id, family, process, pid, sensitive FROM stream WHERE collection_group = ? ORDER BY id",
        (group,),
    ).fetchall()
    normal: dict[tuple[Any, ...], int] = {}
    sens: dict[tuple[Any, ...], int] = {}
    for sid, family, process, pid, is_sens in rows:
        (sens if is_sens else normal)[(family, process, pid)] = sid
    return [(normal[k], sens[k], k[0], k[1], k[2]) for k in sorted(normal, key=repr) if k in sens]


def _source_ids(conn: sqlite3.Connection, stream_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM source WHERE stream_id = ? ORDER BY id", (stream_id,))]


def _stream_signature(conn: sqlite3.Connection, stream_id: int) -> list[Any]:
    """Everything that identifies the content of the stream's sources (reindexing changes at least one)."""
    return conn.execute(
        "SELECT id, rotation, status, size, mtime, zip_crc, indexed_bytes, n_entries,"
        " coalesce(head_hash, ''), coalesce(tail_hash, ''), file_name"
        " FROM source WHERE stream_id = ? ORDER BY id",
        (stream_id,),
    ).fetchall()


def _templates_sig(conn: sqlite3.Connection, codes: set[str]) -> str:
    """Id and definition version of the templates the rules use."""
    if not codes:
        return _digest([])
    marks = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT id, code, def_version FROM template WHERE code IN ({marks}) ORDER BY id", sorted(codes)
    ).fetchall()
    return _digest(rows)


def _entries_epoch(conn: sqlite3.Connection) -> str:
    """Counter the writer bumps whenever it deletes entries (tail removal, reindex)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (EPOCH_KEY,)).fetchone()
    return row[0] if row else ""


def _time_range(conn: sqlite3.Connection, sources: list[int]) -> tuple[int, int] | None:
    """(min ts, max ts) of the sources through ``ix_entry_src_ts``; None when they have no timestamps."""
    lo: int | None = None
    hi: int | None = None
    for sid in sources:
        a = conn.execute(
            "SELECT min(ts) FROM entry WHERE source_id = ? AND ts IS NOT NULL", (sid,)
        ).fetchone()[0]
        b = conn.execute(
            "SELECT max(ts) FROM entry WHERE source_id = ? AND ts IS NOT NULL", (sid,)
        ).fetchone()[0]
        if a is None or b is None:
            continue
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return None if lo is None or hi is None else (lo, hi)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iter_records(
    conn: sqlite3.Connection,
    tpl_id: int,
    sources: list[int],
    lo: int | None,
    hi: int | None,
    key: str | None,
) -> Iterator[list[Any]]:
    """Records ``[id, ts, tid, key, flags]`` of one template in the sources, by cursor, ordered by (ts, id).

    Read in chunks of ``CHUNK`` rows through ``ix_entry_tpl_ts``. ``lo``/``hi`` bound the timestamps
    (inclusive; None = open). The key comes from ``fields`` (JSON) and, when the field is missing, from
    ``entry_key`` of the same type.
    """
    marks = ",".join("?" * len(sources))
    sql = (
        f"SELECT id, ts, tid, flags, {'fields' if key else 'NULL'} FROM entry"
        f" WHERE tpl_id = ? AND source_id IN ({marks}) AND ts IS NOT NULL"
    )
    args: list[Any] = [tpl_id, *sources]
    if lo is not None:
        sql += " AND ts >= ?"
        args.append(lo)
    if hi is not None:
        sql += " AND ts <= ?"
        args.append(hi)
    sql += " ORDER BY ts, id"
    cursor = conn.execute(sql, args)
    while True:
        rows = cursor.fetchmany(CHUNK)
        if not rows:
            return
        chunk: list[list[Any]] = []
        missing: dict[int, list[Any]] = {}
        for eid, ts, tid, flags, fields in rows:
            value = None
            if key and fields:
                try:
                    parsed = json.loads(fields)
                    raw = parsed.get(key) if isinstance(parsed, dict) else None
                    value = None if raw is None else str(raw)
                except (ValueError, AttributeError):
                    value = None
            rec = [eid, ts, tid, value, flags or 0]
            chunk.append(rec)
            if key and value is None:
                missing[eid] = rec
        if missing:
            ids = list(missing)
            for i in range(0, len(ids), KEY_CHUNK):
                part = ids[i : i + KEY_CHUNK]
                q = ",".join("?" * len(part))
                for eid, value in conn.execute(
                    # template keys only: payload keys (origin 1) serve the search, never the pairing. The
                    # index is named: reading origin makes it non-covering and the planner would scan by type.
                    "SELECT entry_id, value FROM entry_key INDEXED BY ix_key_entry"
                    f" WHERE key_type = ? AND origin = 0 AND entry_id IN ({q})",
                    [key, *part],
                ):
                    missing[eid][3] = value
        yield from chunk


# --------------------------------------------------------------------------- pairing


def pair_stream(
    normals: Iterator[list[Any]],
    sens: Iterator[list[Any]],
    *,
    use_tid: bool,
    max_dt: int,
    order_any: bool,
    use_key: bool,
    max_buffer: int | None = None,
    counters: dict[str, int] | None = None,
    peak: list[int] | None = None,
) -> Iterator[tuple[int, int, int]]:
    """Merge two (ts, id)-ordered record streams and yield ``(normal_id, sens_id, confidence)``.

    Records are ``[id, ts, tid, key, flags]``. The result equals a two-pass matching:

    - pass 1: a normal record with a key takes the oldest unconsumed sensitive record with the same key (and
      TID when ``use_tid``) in its window (confidence 3); done as soon as the normal record is read, so keys
      always win;
    - pass 2: the other normal records (no key, or no keyed partner) take the oldest unconsumed sensitive
      record in the window; keyed normal records only the keyless ones, since two different keys never pair
      (confidence 2 at the same ms, else 1). Pass 2 of a normal record waits until every normal record that
      could still claim the same sensitive records by key has been read (``max_dt + tolerance`` later), so a
      keyless normal record never takes a sensitive record a keyed one in the window would claim.

    The window of a normal record at ``t`` is ``[t - tolerance, t + max_dt]`` (tolerance 0 unless
    ``order_any``). Records with a None TID never pair when ``use_tid`` is set. Every record is looked at
    O(1) times (per-TID queues, lazy deletion, compaction when consumed records outnumber the live ones).
    ``max_buffer`` bounds the sensitive records held (oldest dropped first, counted in
    ``counters['dropped']``) and the deferred normal records; ``peak[0]`` receives the largest physical size
    of the buffer (diagnostics).
    """
    max_buffer = MAX_BUFFER if max_buffer is None else max_buffer
    lo_tol = ANY_ORDER_TOLERANCE_MS if order_any else 0
    horizon = max_dt + lo_tol
    compact_min = min(COMPACT_MIN, max_buffer)
    # sensitive record: [id, ts, tid, key, flags, consumed]
    order: deque[list[Any]] = deque()  # every buffered record, oldest first
    order_k: deque[list[Any]] = deque()  # the keyed ones, expired by the pass 1 horizon
    unkeyed: dict[Any, deque[list[Any]]] = {}
    keyed: dict[Any, deque[list[Any]]] = {}
    bykey: dict[tuple[Any, str], deque[list[Any]]] = {}
    defer: deque[list[Any]] = deque()  # normal records waiting for pass 2
    live = 0
    dead = 0  # consumed records still physically in ``order``
    size = 0  # physical entries over every container below (order, order_k, queues, defer)
    cut1 = 0
    pending = next(sens, None)

    def tid_of(rec: list[Any]) -> Any:
        return rec[2] if use_tid else 0

    def head(table: dict[Any, deque[list[Any]]], k: Any, cut: int | None) -> list[Any] | None:
        nonlocal size
        dq = table.get(k)
        if dq is None:
            return None
        while dq and (dq[0][5] or (cut is not None and dq[0][1] < cut)):
            dq.popleft()
            size -= 1
        if not dq:
            del table[k]
            return None
        return dq[0]

    def clean(rec: list[Any]) -> None:
        nonlocal size
        while order_k and (order_k[0][5] or order_k[0][1] < cut1):
            order_k.popleft()
            size -= 1
        t = tid_of(rec)
        head(unkeyed, t, None)
        head(keyed, t, None)
        if rec[3] is not None and use_key:
            head(bykey, (t, rec[3]), cut1)

    def consume(rec: list[Any]) -> None:
        nonlocal live, dead
        rec[5] = True
        live -= 1
        dead += 1
        clean(rec)
        if dead > live and dead > compact_min:
            compact()

    def compact() -> None:
        nonlocal dead, size
        alive = [r for r in order if not r[5]]
        order.clear()
        order.extend(alive)
        dead = 0
        order_k.clear()
        unkeyed.clear()
        keyed.clear()
        bykey.clear()
        for r in alive:
            t = tid_of(r)
            if use_key and r[3] is not None:
                keyed.setdefault(t, deque()).append(r)
                if r[1] >= cut1:
                    order_k.append(r)
                    bykey.setdefault((t, r[3]), deque()).append(r)
            else:
                unkeyed.setdefault(t, deque()).append(r)
        size = (
            len(order)
            + len(order_k)
            + len(defer)
            + sum(len(dq) for table in (unkeyed, keyed, bykey) for dq in table.values())
        )

    def pop_oldest() -> bool:
        """Remove the oldest record from ``order``; True when it was still live (it is then consumed)."""
        nonlocal live, dead, size
        rec = order.popleft()
        size -= 1
        was_live = not rec[5]
        if was_live:
            rec[5] = True
            live -= 1
        else:
            dead -= 1
        clean(rec)
        return was_live

    def fill(limit: int) -> None:
        nonlocal pending, live, size
        while pending is not None and pending[1] <= limit:
            if not (use_tid and pending[2] is None):
                rec = [*pending, False]
                t = tid_of(rec)
                order.append(rec)
                if use_key and rec[3] is not None:
                    keyed.setdefault(t, deque()).append(rec)
                    bykey.setdefault((t, rec[3]), deque()).append(rec)
                    order_k.append(rec)
                    size += 4
                else:
                    unkeyed.setdefault(t, deque()).append(rec)
                    size += 2
                if peak is not None and size > peak[0]:
                    peak[0] = size  # before any drop or compaction: the true physical maximum
                live += 1
                while live > max_buffer:
                    if pop_oldest() and counters is not None:
                        counters["dropped"] = counters.get("dropped", 0) + 1
                if size > 8 * max(live, compact_min):
                    compact()
                if peak is not None and size > peak[0]:
                    peak[0] = size
            pending = next(sens, None)

    def pass2(d: list[Any]) -> tuple[int, int, int] | None:
        t = tid_of(d)
        limit, cut = d[1] + max_dt, d[1] - lo_tol
        a = head(unkeyed, t, cut)
        b = head(keyed, t, cut) if not (use_key and d[3] is not None) else None
        if a is not None and a[1] > limit:
            a = None
        if b is not None and b[1] > limit:
            b = None
        found = a if b is None else b if a is None else min(a, b, key=lambda r: (r[1], r[0]))
        if found is None:
            return None
        consume(found)
        return (d[0], found[0], 2 if found[1] == d[1] else 1)

    for n in normals:
        if use_tid and n[2] is None:
            continue
        n_ts = n[1]
        fill(n_ts + max_dt)
        cut1 = n_ts - lo_tol
        while order_k and (order_k[0][5] or order_k[0][1] < cut1):
            r = order_k.popleft()
            size -= 1
            head(bykey, (tid_of(r), r[3]), cut1)
        found = head(bykey, (tid_of(n), n[3]), cut1) if use_key and n[3] is not None else None
        if found is not None:
            consume(found)
            yield (n[0], found[0], 3)
        else:
            defer.append(n)
            size += 1
            if peak is not None and size > peak[0]:
                peak[0] = size
        while defer and (n_ts > defer[0][1] + horizon or len(defer) > max_buffer):
            size -= 1
            res = pass2(defer.popleft())
            if res is not None:
                yield res
        cut2 = (defer[0][1] if defer else n_ts) - lo_tol
        while order and (order[0][5] or order[0][1] < cut2):
            pop_oldest()
    while defer:
        d = defer.popleft()
        size -= 1
        fill(d[1] + max_dt)
        res = pass2(d)
        if res is not None:
            yield res


def pair_records(
    normals: list[list[Any]],
    sens: list[list[Any]],
    *,
    use_tid: bool,
    max_dt: int,
    order_any: bool,
    use_key: bool,
    max_buffer: int | None = None,
    counters: dict[str, int] | None = None,
    peak: list[int] | None = None,
) -> list[tuple[int, int, int]]:
    """List form of ``pair_stream`` for in-memory inputs (tests, tools)."""
    return list(
        pair_stream(
            iter(normals),
            iter(sens),
            use_tid=use_tid,
            max_dt=max_dt,
            order_any=order_any,
            use_key=use_key,
            max_buffer=max_buffer,
            counters=counters,
            peak=peak,
        )
    )


# --------------------------------------------------------------------------- group


def _capped(it: Iterator[list[Any]], cap: int, counters: dict[str, int]) -> Iterator[list[Any]]:
    """Yield at most ``cap`` records; the rest are drained and counted in ``counters['dropped']``."""
    for i, rec in enumerate(it):
        if i < cap:
            yield rec
        else:
            counters["dropped"] = counters.get("dropped", 0) + 1


def _flush(conn: sqlite3.Connection, batch: list[tuple[int, int, str, int]]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO link(a_entry_id, b_entry_id, kind, confidence) VALUES (?, ?, ?, ?)", batch
    )
    batch.clear()


def _correlate_group(
    conn: sqlite3.Connection,
    group: int,
    catalog: Any,
    pairs: list[tuple[int, int, str, Any, Any]],
    summary: CorrelateSummary,
) -> dict[str, Any]:
    """Compute the group's links and statistics, writing the links in batches of ``INSERT_BATCH``.

    The caller has already deleted the previous links. Returns the statistics per rule label.
    """
    stats: dict[str, Any] = {}
    batch: list[tuple[int, int, str, int]] = []
    tpl_ids = {code: tid for tid, code in conn.execute("SELECT id, code FROM template")}
    for n_stream, s_stream, family, _process, _pid in pairs:
        n_src, s_src = _source_ids(conn, n_stream), _source_ids(conn, s_stream)
        n_rng, s_rng = _time_range(conn, n_src), _time_range(conn, s_src)
        if not n_src or not s_src or n_rng is None or s_rng is None:
            continue
        w_lo, w_hi = max(n_rng[0], s_rng[0]), min(n_rng[1], s_rng[1])
        for rule in _rules_of(catalog, family):
            label = _label(rule)
            n_tpl, s_tpl = tpl_ids.get(rule.get("normal")), tpl_ids.get(rule.get("sensitive"))
            entry = stats.setdefault(
                label,
                {
                    "family": family,
                    "normal": rule.get("normal"),
                    "sensitive": rule.get("sensitive"),
                    "normal_in_window": 0,
                    "linked": 0,
                    "by_confidence": {"3": 0, "2": 0, "1": 0},
                },
            )
            if n_tpl is None or s_tpl is None or w_lo > w_hi:
                continue
            match = rule.get("match") or DEFAULT_MATCH
            max_dt = _int(rule.get("max_dt_ms"))
            max_dt = DEFAULT_MAX_DT_MS if max_dt is None or max_dt < 0 else max_dt
            key = rule.get("key") if isinstance(rule.get("key"), str) else None
            counters = summary.dropped_by_rule.setdefault(label, {})
            normals = _capped(
                _iter_records(conn, n_tpl, n_src, w_lo, w_hi, key), MAX_CANDIDATES_PER_RULE, counters
            )

            def counted(it: Iterator[list[Any]], entry=entry, code=rule["normal"]) -> Iterator[list[Any]]:
                for rec in it:
                    entry["normal_in_window"] += 1
                    if not rec[4] & FLAG_REF_SENSITIVE:
                        summary.unflagged[code] = summary.unflagged.get(code, 0) + 1
                    yield rec

            sens = _iter_records(conn, s_tpl, s_src, w_lo - ANY_ORDER_TOLERANCE_MS, None, key)
            for a, b, conf in pair_stream(
                counted(normals),
                sens,
                use_tid="tid" in match,
                max_dt=max_dt,
                order_any=rule.get("order") == "any",
                use_key=key is not None,
                counters=counters,
            ):
                batch.append((a, b, KIND, conf))
                entry["by_confidence"][str(conf)] += 1
                entry["linked"] += 1
                summary.links += 1
                if len(batch) >= INSERT_BATCH:
                    _flush(conn, batch)
            if not counters:
                del summary.dropped_by_rule[label]
    if batch:
        _flush(conn, batch)
    return stats


def _group_signature(
    conn: sqlite3.Connection, catalog: Any, pairs: list[tuple[int, int, str, Any, Any]], group: int
) -> str:
    parts = []
    codes: set[str] = set()
    for n_stream, s_stream, family, process, pid in pairs:
        rules = _rules_of(catalog, family)
        for r in rules:
            codes.update(c for c in (r.get("normal"), r.get("sensitive")) if isinstance(c, str))
        parts.append(
            (
                family,
                process,
                pid,
                _stream_signature(conn, n_stream),
                _stream_signature(conn, s_stream),
                [sorted((k, repr(v)) for k, v in r.items()) for r in rules],
            )
        )
    return _digest(parts, _templates_sig(conn, codes), group_epoch(conn, _entries_epoch(conn), group))


def _delete_links(conn: sqlite3.Connection, pairs: list[tuple[int, int, str, Any, Any]]) -> None:
    """Delete this engine's links of the group and the orphans (an endpoint that no longer exists)."""
    conn.execute(
        "DELETE FROM link WHERE kind = ? AND (NOT EXISTS (SELECT 1 FROM entry WHERE id = a_entry_id)"
        " OR NOT EXISTS (SELECT 1 FROM entry WHERE id = b_entry_id))",
        (KIND,),
    )
    for n_stream, _s, _f, _p, _pid in pairs:
        for src in _source_ids(conn, n_stream):
            conn.execute(
                "DELETE FROM link WHERE kind = ?"
                " AND a_entry_id IN (SELECT id FROM entry WHERE source_id = ?)",
                (KIND, src),
            )


def correlate_group(conn: sqlite3.Connection, group: int, catalog: Any, summary: CorrelateSummary) -> bool:
    """Recompute the normal<->sensitive links of one group if its signature changed; True when it did.

    One SAVEPOINT; on any error everything is rolled back and the exception propagates.
    """
    pairs = _stream_pairs(conn, group)
    if not pairs:
        return False
    sig = _group_signature(conn, catalog, pairs, group)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_PREFIX + str(group),)).fetchone()
    if row is not None:
        try:
            if json.loads(row[0]).get("sig") == sig:
                return False
        except (ValueError, AttributeError):
            pass
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN")
    savepoint = f"correlate_{group}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        _delete_links(conn, pairs)
        stats = _correlate_group(conn, group, catalog, pairs, summary)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (META_PREFIX + str(group), json.dumps({"sig": sig, "rules": stats}, sort_keys=True)),
        )
        summary.groups += 1
        if own_tx:
            conn.execute("COMMIT")  # ends the transaction with the savepoint still active
        else:
            conn.execute(f"RELEASE {savepoint}")
    except BaseException:
        _undo(conn, savepoint, own_tx)
        raise
    return True


def correlate_pending(conn: sqlite3.Connection, catalog: Any, kb: Any = None) -> CorrelateSummary:
    """Pair normal and sensitive records in every collection group whose streams changed.

    A group that fails is rolled back and reported in ``errors``; the others still run. ``kb`` is accepted
    for the pipeline signature and unused.
    """
    summary = CorrelateSummary()
    groups = [r[0] for r in conn.execute("SELECT DISTINCT collection_group FROM stream ORDER BY 1")]
    for group in groups:
        before = (
            summary.links,
            summary.groups,
            dict(summary.unflagged),
            {k: dict(v) for k, v in summary.dropped_by_rule.items()},
        )
        try:
            correlate_group(conn, group, catalog, summary)
        except Exception as exc:  # correlation must never abort an ingestion
            summary.links, summary.groups, summary.unflagged, summary.dropped_by_rule = before
            summary.errors.append((group, f"{type(exc).__name__}: {exc}"))
    return summary


def pair_stats(conn: sqlite3.Connection, group: int) -> dict[str, dict[str, Any]]:
    """Statistics stored by the last run of the group, per rule label ``"<normal>><sensitive>"``.

    Each value: ``family``, ``normal``, ``sensitive``, ``normal_in_window``, ``linked`` and
    ``by_confidence`` (``{3: n, 2: n, 1: n}``). Empty when the group was never correlated.
    """
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_PREFIX + str(group),)).fetchone()
    if row is None:
        return {}
    try:
        rules = json.loads(row[0]).get("rules") or {}
    except (ValueError, AttributeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for label, entry in rules.items():
        item = dict(entry)
        item["by_confidence"] = {int(k): v for k, v in entry.get("by_confidence", {}).items()}
        out[label] = item
    return out
