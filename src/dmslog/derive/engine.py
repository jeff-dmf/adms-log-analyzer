"""Derivation engine: runs the derivers over a stored stream and stores the events (ARQUITETURA 6.5).

Chains: one stream can hold unrelated files (BLTHost.Common.log, BLTHost.Arbitration.log, ...). The unit of
derivation is the ROTATION CHAIN: the sources of the stream whose ``file_name`` is the same once a trailing
``.N`` is removed (``X.log``, ``X.log.0`` ... one chain). Every chain gets fresh derivers, its own checkpoint,
signature and stateful-deriver state (``meta['derive.<stream_id>.<chain>']``); events keep the stream id and
get ``detail.chain`` only when the stream has more than one chain. ``stream.derive_checkpoint`` is the
minimum over the chains (the ingestion lowers it when it deletes entries, which the engine reads as a reset).

Order inside a chain: sources oldest first (``.log.N`` with the highest ``N``, ..., ``.log.0``, then the
current ``.log``), inside a source by ``entry.id``. The checkpoint is an ``entry.id``, which only means
something when ids grow along that order. The ingestion adds the sources of a chain oldest first so that this
holds; the engine verifies it (min/max id per source) and, if a chain violates it, derives that chain whole
every time it changes (checkpoint 0). Ids across different chains do not matter.

Incremental derivation (per chain): events of the chain with ``start_entry_id >= checkpoint`` (and the
``entry.event_id`` that point to them) are deleted, the entries from the checkpoint on are fed to the derivers
in order, and the new checkpoint is the smallest ``low_watermark`` (or last entry id + 1 when nothing is
open). Everything happens in one transaction. Derivers that expose ``export_state()`` / ``import_state()``
(stats: cumulative counters for deltas) get their state saved in the chain record at a snapshot that is not
later than the checkpoint, so the checkpoint can be moved back to that snapshot. ``stat_sample`` rows after
the
first reprocessed ``ts`` are deleted too, except in streams with several chains (the table has no chain
column): there the rows are only replaced.

Nothing is written when nothing changed: the chain record keeps a signature of the chain's sources (sizes,
hashes, counts) plus the rules, the derivers and the template definitions. A different set of rules or
derivers, or a change in the number of chains of the stream, re-derives the chain from scratch.

``derive_pending`` ends with the clock-jump post-pass (``lifecycle.possible_clock_jumps``): a one hour gap
found at the same time in every chain of a collection marks those gaps ``possible_clock_jump``.

Performance: the derivers run once per entry (1.6 M entries in a large ERS), so the hot path is built to do as
little as possible. ``_entries`` hands the derivers ``RowEntry`` objects (a tuple built from the query row;
``fields`` is parsed only when a deriver needs it, ``field(name)`` and ``fields_raw`` avoid even that),
the template, continuation and key lookups are done outside SQL, a deriver returns at once for entries it
cannot care about, and the ``entry.event_id`` links are written as ranges of consecutive entry ids.

``kb`` (the knowledge base) is accepted for the pipeline signature; the current derivers do not use it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import groupby, repeat
from operator import add, itemgetter
from typing import Any

from dmslog.derive import build_derivers
from dmslog.derive.base import Event, RowEntry, StatSample

CHUNK = 5000  # entries read per query
WRITE_EVERY = 4000  # events buffered before a batch write
MAX_SNAPSHOTS = 64
CONT_CHARS = 65536  # continuation text handed to the derivers (same limit as the FTS)

_FIRST = itemgetter(0)
_LAST = itemgetter(-1)
META_PREFIX = "derive."
_ROTATION_SUFFIX = re.compile(r"\.\d+$")


@dataclass
class StreamDerivation:
    stream_id: int
    skipped: bool = False  # nothing changed since the last derivation: nothing was written
    events: int = 0
    samples: int = 0
    entries: int = 0  # entries fed to the derivers
    checkpoint: int = 0  # new value of stream.derive_checkpoint
    full: bool = False  # derived from the beginning of the stream


@dataclass
class DeriveSummary:
    streams: int = 0
    derived: int = 0
    events: int = 0
    samples: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)  # (stream_id, message)


# --------------------------------------------------------------------------- stream layout


def _sources(conn: sqlite3.Connection, stream_id: int) -> list[tuple[Any, ...]]:
    """Sources of the stream, oldest first inside each chain: ``.log.N`` (N descending), then ``.log``."""
    return conn.execute(
        "SELECT id, rotation, status, indexed_bytes, n_entries, coalesce(tail_hash, ''),"
        " coalesce(head_hash, ''), file_name"
        " FROM source WHERE stream_id = ? ORDER BY (rotation IS NULL), rotation DESC, id",
        (stream_id,),
    ).fetchall()


def _id_ranges(
    conn: sqlite3.Connection, sources: list[tuple[Any, ...]]
) -> tuple[list[tuple[int, int, int]], bool]:
    """``[(source_id, min_id, max_id)]`` of the non-empty sources in order, and whether ids grow along it."""
    ranges: list[tuple[int, int, int]] = []
    monotone = True
    last = -1
    for src in sources:
        lo, hi = conn.execute("SELECT min(id), max(id) FROM entry WHERE source_id = ?", (src[0],)).fetchone()
        if lo is None:
            continue
        if lo <= last:
            monotone = False
        last = max(last, hi)
        ranges.append((src[0], lo, hi))
    return ranges, monotone


def _digest(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def _templates_sig(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id, code, def_version FROM template ORDER BY id").fetchall()
    return _digest(rows)


def chain_of(file_name: str) -> str:
    """Rotation chain of a log file: its name without a trailing ``.N`` (``X.log.0`` -> ``X.log``)."""
    return _ROTATION_SUFFIX.sub("", file_name)


def _chains(sources: list[tuple[Any, ...]]) -> dict[str, list[tuple[Any, ...]]]:
    out: dict[str, list[tuple[Any, ...]]] = {}
    for src in sources:
        out.setdefault(chain_of(src[7]), []).append(src)
    return dict(sorted(out.items()))


def _record_key(stream_id: int, chain: str) -> str:
    return f"{META_PREFIX}{stream_id}.{chain}"


def _load_record(conn: sqlite3.Connection, stream_id: int, chain: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_record_key(stream_id, chain),)).fetchone()
    if not row or not row[0]:
        return None
    try:
        rec = json.loads(row[0])
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


# --------------------------------------------------------------------------- reading


def _entries(conn: sqlite3.Connection, ranges: list[tuple[int, int, int]], start: int) -> Iterator[RowEntry]:
    """RowEntry rows with ``id >= start`` of the sources, in order (keyset pages, no open cursor)."""
    tpl_code: dict[int, str] = {}
    tpl_class: dict[int, str | None] = {}
    for tpl_id, code, cls in conn.execute("SELECT id, code, class FROM template"):
        tpl_code[tpl_id], tpl_class[tpl_id] = code, cls
    for source_id, lo, hi in ranges:
        if hi < start:
            continue
        last = max(start, lo) - 1
        while True:
            rows = conn.execute(
                "SELECT id, ts, tid, sev, coalesce(fields, ''), msg, tpl_id FROM entry NOT INDEXED"
                " WHERE id > ? AND id <= ? AND source_id = ? ORDER BY id LIMIT ?",
                (last, hi, source_id, CHUNK),
            ).fetchall()
            if not rows:
                break
            first_id, last_id = rows[0][0], rows[-1][0]
            conts = dict(
                conn.execute(
                    "SELECT entry_id, substr(cont, 1, ?) FROM entry_body WHERE entry_id BETWEEN ? AND ?",
                    (CONT_CHARS, first_id, last_id),
                )
            )
            keys: dict[int, tuple[tuple[str, str], ...]] = {}
            cursor = conn.execute(
                "SELECT entry_id, key_type, value FROM entry_key WHERE entry_id BETWEEN ? AND ?"
                " ORDER BY entry_id, key_type, value",
                (first_id, last_id),
            )
            for entry_id, group in groupby(cursor, _FIRST):
                keys[entry_id] = tuple([(g[1], g[2]) for g in group])
            # row + (cont, keys, tpl_code, tpl_class), built with C-level iterators (once per entry)
            ids = list(map(_FIRST, rows))
            tpls = list(map(_LAST, rows))
            cells = zip(
                map(conts.get, ids, repeat("")),
                map(keys.get, ids, repeat(())),
                map(tpl_code.get, tpls),
                map(tpl_class.get, tpls),
                strict=True,
            )
            yield from map(RowEntry, map(add, rows, cells))
            last = last_id
            if len(rows) < CHUNK:
                break


# --------------------------------------------------------------------------- the engine


class _Snapshots:
    """States of the derivers that expose ``export_state`` / ``import_state``, usable as resume points.

    A resume point is an entry id ``b`` such that the stateful derivers are quiescent (``low_watermark()`` is
    None) before ``b``. The state does not change while they stay quiescent and produce nothing, so one
    saved state covers a range of boundaries ``[lo, hi]`` (``hi`` grows entry by entry at no cost).
    """

    def __init__(self, derivers: list[Any], start: int) -> None:
        self.derivers = derivers
        self.items: list[list[Any]] = []  # [lo, hi, state]
        self.open = False  # the last item still covers the current position
        self.changed = True
        self._watermarks = [d.low_watermark for d in derivers]
        self._with_samples = [d for d in derivers if hasattr(d, "samples")]
        self._counts = [len(d.samples) for d in self._with_samples]
        self.observe(start)

    def state(self) -> dict[str, Any]:
        return {type(d).__qualname__: d.export_state() for d in self.derivers}

    def observe(self, boundary: int) -> None:
        """Called before feeding the entry ``boundary`` (and once at the end with last id + 1)."""
        for watermark in self._watermarks:
            if watermark() is not None:
                self.open = False
                self.changed = True
                return
        if self.open and not self.changed:
            self.items[-1][1] = boundary
            return
        self._observe_new(boundary)

    def _observe_new(self, boundary: int) -> None:
        """Quiescent and (not open or changed): a new resume point starts here."""
        self.items.append([boundary, boundary, self.state()])
        self.open, self.changed = True, False
        if len(self.items) > MAX_SNAPSHOTS:  # thin out, keeping the oldest and the newest
            self.items[:] = self.items[::2] + ([self.items[-1]] if len(self.items) % 2 == 0 else [])

    def step(self, produced_by_stateful: bool, boundary: int) -> None:
        """``fed(produced)`` for the entry just fed, then ``observe(boundary)`` for the next one.

        Same effect as calling both; one call per entry because this runs for every entry.
        """
        counts = self._counts
        for i, d in enumerate(self._with_samples):  # ``samples`` may be replaced by ``drain_samples``
            n = len(d.samples)
            if n != counts[i]:
                counts[i] = n
                produced_by_stateful = True
        for watermark in self._watermarks:
            if watermark() is not None:
                self.open = False
                self.changed = True
                return
        if self.open and not (self.changed or produced_by_stateful):
            self.items[-1][1] = boundary
            return
        self.changed = self.changed or produced_by_stateful
        self._observe_new(boundary)

    def fed(self, produced_by_stateful: bool) -> None:
        """Called after an entry was fed: did a stateful deriver produce output?"""
        counts = self._counts
        for i, d in enumerate(self._with_samples):  # ``samples`` may be replaced by ``drain_samples``
            n = len(d.samples)
            if n != counts[i]:
                counts[i] = n
                produced_by_stateful = True
        if produced_by_stateful:
            self.changed = True

    def prune(self, oldest_open: int | None) -> None:
        """Drop the items that cannot be the answer any more (before the latest not after ``oldest_open``)."""
        if oldest_open is None:
            del self.items[:-1]
            return
        keep = max((i for i, it in enumerate(self.items) if it[0] <= oldest_open), default=0)
        del self.items[:keep]

    def best(self, cp: int) -> tuple[int, dict[str, Any]]:
        """The latest resume point not after ``cp``: (boundary, states)."""
        usable = [it for it in self.items if it[0] <= cp]
        lo, hi, state = usable[-1] if usable else self.items[0]
        return min(hi, cp) if usable else lo, state


def _runs(event_id: int, entry_ids: list[int], out: list[tuple[int, int, int]]) -> None:
    """Append ``(event_id, first, last)`` for each run of consecutive ascending entry ids (order kept)."""
    first = last = None
    for entry_id in entry_ids:
        if last is not None and entry_id == last + 1:
            last = entry_id
            continue
        if first is not None:
            out.append((event_id, first, last))  # type: ignore[arg-type]
        first = last = entry_id
    if first is not None:
        out.append((event_id, first, last))  # type: ignore[arg-type]


class _Writer:
    """Batches event, entry.event_id and stat_sample writes (explicit event ids, no per-row round trips)."""

    def __init__(self, conn: sqlite3.Connection, stream_id: int) -> None:
        self.conn = conn
        self.stream_id = stream_id
        self.next_id = conn.execute("SELECT coalesce(max(id), 0) FROM event").fetchone()[0] + 1
        self.events = 0
        self.samples = 0

    def events_batch(self, events: list[Event]) -> None:
        if not events:
            return
        events.sort(key=lambda e: (e.start_entry_id is None, e.start_entry_id or 0, e.kind, e.name or ""))
        rows = []
        links = []
        for ev in events:
            eid = self.next_id
            self.next_id += 1
            n = ev.n_entries if ev.n_entries is not None else (len(ev.entry_ids) or None)
            rows.append(
                (eid, self.stream_id, ev.kind, ev.name, ev.key, ev.tid, ev.start_entry_id, ev.end_entry_id,
                 ev.ts_start if ev.ts_start is not None else 0, ev.ts_end, ev.duration_ms, ev.status,
                 ev.sev, n,
                 ev.detail_json())
            )  # fmt: skip
            _runs(eid, ev.entry_ids, links)
        self.conn.executemany(
            "INSERT INTO event(id, stream_id, kind, name, key, tid, start_entry_id, end_entry_id, ts_start,"
            " ts_end, duration_ms, status, sev, n_entries, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        if links:
            self.conn.executemany("UPDATE entry SET event_id = ? WHERE id BETWEEN ? AND ?", links)
        self.events += len(rows)

    def samples_batch(self, samples: list[StatSample]) -> None:
        if not samples:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO stat_sample(stream_id, ts, side, method, calls, req_bytes, resp_bytes,"
            " d_calls, d_req_bytes, d_resp_bytes) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (self.stream_id, s.ts, s.side, s.method, s.calls, s.req_bytes, s.resp_bytes, s.d_calls,
                 s.d_req_bytes, s.d_resp_bytes)
                for s in samples
            ],
        )  # fmt: skip
        self.samples += len(samples)


def _take_samples(deriver: Any, seen: dict[int, int]) -> list[StatSample]:
    """New samples of a deriver: ``drain_samples()`` when it has one, else a cursor over ``samples``."""
    drain = getattr(deriver, "drain_samples", None)
    if drain is not None:
        return list(drain())
    samples = getattr(deriver, "samples", None)
    if samples is None:
        return []
    start = seen.get(id(deriver), 0)
    try:
        new = list(samples[start:])
        seen[id(deriver)] = len(samples)
    except TypeError:  # not a sequence
        new = list(samples)[start:]
        seen[id(deriver)] = start + len(new)
    return new


def _reset(
    conn: sqlite3.Connection,
    stream_id: int,
    chain: str,
    cp: int,
    ts_cut: int | None,
    multi: bool,
) -> None:
    """Drop what the derivers will produce again for one chain.

    Events of the chain with ``start_entry_id >= cp`` or whose start entry no longer exists (and their
    ``entry.event_id``), events without a start
    after ``ts_cut`` and, in a single-chain stream, the stat samples after ``ts_cut``.
    """
    # Events of the chain are found by ``detail.chain`` (multi-chain streams) and not through the entries,
    # which may be gone after a re-index: start at or after the checkpoint, or a start entry that is gone.
    where = (
        "stream_id = ? AND start_entry_id IS NOT NULL AND (start_entry_id >= ?"
        " OR NOT EXISTS (SELECT 1 FROM entry WHERE entry.id = event.start_entry_id))"
    )
    params: list[Any] = [stream_id, cp]
    if multi:
        # a full pass also drops events stored before the stream had several chains (no chain tag)
        where += " AND (json_extract(detail, '$.chain') = ?" + (
            " OR json_extract(detail, '$.chain') IS NULL)" if cp <= 0 else ")"
        )
        params.append(chain)
    conn.execute(
        f"UPDATE entry SET event_id = NULL WHERE event_id IN (SELECT id FROM event WHERE {where})", params
    )  # noqa: S608
    conn.execute(f"DELETE FROM event WHERE {where}", params)  # noqa: S608
    null_where, null_params = "stream_id = ? AND start_entry_id IS NULL", [stream_id]
    if multi:
        null_where += " AND json_extract(detail, '$.chain') = ?"
        null_params.append(chain)
    full = cp <= 0 or ts_cut is None
    if not full:
        null_where += " AND ts_start >= ?"
        null_params.append(ts_cut)
    conn.execute(f"DELETE FROM event WHERE {null_where}", null_params)  # noqa: S608
    if not multi:
        if full:
            conn.execute("DELETE FROM stat_sample WHERE stream_id = ?", (stream_id,))
        else:
            conn.execute("DELETE FROM stat_sample WHERE stream_id = ? AND ts > ?", (stream_id, ts_cut))


def _undo(conn: sqlite3.Connection, savepoint: str, own_tx: bool) -> None:
    """Undo the writes made since the savepoint; never raises (the original error must surface).

    Own transaction: a plain ROLLBACK. Caller's transaction: ROLLBACK TO + RELEASE, so only this unit of
    work is undone and the caller's transaction stays open.
    """
    if not conn.in_transaction:
        return
    try:
        if own_tx:
            conn.execute("ROLLBACK")
        else:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
    except sqlite3.Error:
        pass  # e.g. the savepoint was never created or the transaction is already gone


def derive_stream(
    conn: sqlite3.Connection,
    stream_id: int,
    catalog: Any,
    kb: Any = None,
    *,
    truncated: bool = False,
    force: bool = False,
) -> StreamDerivation:
    """Derive the events of one stream, chain by chain, from the checkpoints (see the module docstring).

    ``catalog``: a ``Catalog`` (the family's ``TemplateSet`` comes from ``for_family``). ``truncated``: the
    stream was cut by the ERS (operations without an end become ``truncated``). ``force``: derive even if
    nothing changed. Raises ``KeyError`` for an unknown stream; on any error the transaction is rolled back.
    """
    row = conn.execute(
        "SELECT family, sensitive, derive_checkpoint FROM stream WHERE id = ?", (stream_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown stream {stream_id}")
    family, sensitive, stream_cp = row[0], bool(row[1]), int(row[2] or 0)
    rules = catalog.for_family(family, sensitive=sensitive)
    chains = _chains(_sources(conn, stream_id))
    multi = len(chains) > 1
    ops = getattr(rules, "operations", None)
    kinds = [type(d).__qualname__ for d in build_derivers(family, rules, truncated=truncated)]
    rules_sig = _digest(repr(ops() if callable(ops) else None), kinds, truncated, multi, _templates_sig(conn))
    records = {name: _load_record(conn, stream_id, name) for name in chains}
    recorded = [r["cp"] for r in records.values() if r and isinstance(r.get("cp"), int)]
    # The ingestion lowers stream.derive_checkpoint when it deletes entries: below every recorded chain
    # checkpoint means a reset happened, and no chain may resume after it.
    floor = stream_cp if recorded and len(recorded) == len(chains) and stream_cp < min(recorded) else None

    todo = []
    for name, sources in chains.items():
        sig = _digest([tuple(s) for s in sources])  # source id, rotation, status, sizes, counts, hashes, name
        rec = records[name]
        if not force and rec and rec.get("sig") == sig and rec.get("rules") == rules_sig and floor is None:
            continue
        todo.append((name, sources, sig, rec))
    if not todo:
        return StreamDerivation(stream_id, skipped=True, checkpoint=stream_cp)

    out = StreamDerivation(stream_id)
    end_cps = {name: r["cp"] for name, r in records.items() if r and isinstance(r.get("cp"), int)}
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN")
    savepoint = f"derive_stream_{stream_id}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        writer = _Writer(conn, stream_id)
        for name, sources, sig, rec in todo:
            end_cps[name] = _derive_chain(
                conn, writer, family, rules, name, sources, sig, rec, rules_sig, floor, multi, truncated, out
            )
        out.checkpoint = min(end_cps.values()) if end_cps else 0
        conn.execute("UPDATE stream SET derive_checkpoint = ? WHERE id = ?", (out.checkpoint, stream_id))
        if own_tx:
            conn.execute("COMMIT")  # ends the transaction with the savepoint still active
        else:
            conn.execute(f"RELEASE {savepoint}")
    except BaseException:
        _undo(conn, savepoint, own_tx)
        raise
    out.events, out.samples = writer.events, writer.samples
    return out


def _derive_chain(
    conn: sqlite3.Connection,
    writer: _Writer,
    family: str,
    rules: Any,
    chain: str,
    sources: list[tuple[Any, ...]],
    sig: str,
    record: dict[str, Any] | None,
    rules_sig: str,
    floor: int | None,
    multi: bool,
    truncated: bool,
    out: StreamDerivation,
) -> int:
    """Derive one rotation chain and store its record; returns the chain's new checkpoint."""
    stream_id = writer.stream_id
    derivers = build_derivers(family, rules, truncated=truncated)
    ranges, monotone = _id_ranges(conn, sources)
    stateful = [d for d in derivers if hasattr(d, "export_state") and hasattr(d, "import_state")]
    cp = 0
    if (
        record is not None
        and record.get("rules") == rules_sig
        and monotone
        and isinstance(record.get("cp"), int)
    ):
        cp = record["cp"]
        if floor is not None:
            cp = min(cp, floor)
        if stateful and (cp != record["cp"] or not isinstance(record.get("states"), dict)):
            cp = 0  # the saved state does not belong to this checkpoint (it was reset by a re-index)
    if cp > 0:
        # resuming mid-chain (entries of the chain exist before the checkpoint): the derivers must not
        # behave as at the start of the stream
        marks = ",".join("?" for _ in ranges) or "NULL"
        earlier = conn.execute(
            f"SELECT 1 FROM entry WHERE source_id IN ({marks}) AND id < ? LIMIT 1",  # noqa: S608
            [*(r[0] for r in ranges), cp],
        ).fetchone()
        if earlier:
            derivers = build_derivers(family, rules, truncated=truncated, stream_start=False)
            stateful = [d for d in derivers if hasattr(d, "export_state") and hasattr(d, "import_state")]
        states = record["states"] if record else {}
        for d in stateful:
            d.import_state(states.get(type(d).__qualname__) or {})

    source_ids = [r[0] for r in ranges]
    ts_cut = None
    if cp > 0 and source_ids:
        marks = ",".join("?" for _ in source_ids)
        ts_cut = conn.execute(
            f"SELECT min(ts) FROM entry WHERE source_id IN ({marks}) AND id >= ?",  # noqa: S608
            [*source_ids, cp],
        ).fetchone()[0]
    _reset(conn, stream_id, chain, cp, ts_cut, multi)
    seen: dict[int, int] = {}
    pending: list[Event] = []
    pending_samples: list[StatSample] = []
    snaps = _Snapshots(stateful, cp) if stateful else None
    fed = 0
    last_id: int | None = None

    tail = [-1, []]  # [highest entry id any event touches, start ids of the events touching it]

    def track(events: list[Event]) -> None:
        for ev in events:
            top = max(ev.end_entry_id or 0, max(ev.entry_ids, default=0))
            if top < tail[0] or ev.start_entry_id is None:
                continue
            if top > tail[0]:
                tail[0], tail[1] = top, []
            tail[1].append(ev.start_entry_id)

    def write() -> None:
        nonlocal pending, pending_samples
        track(pending)
        for d in derivers:
            pending_samples.extend(_take_samples(d, seen))
        if multi:
            for ev in pending:
                ev.detail["chain"] = chain
        writer.events_batch(pending)
        writer.samples_batch(pending_samples)
        pending, pending_samples = [], []

    def watermark() -> int | None:
        marks = [w for w in (d.low_watermark() for d in derivers) if w is not None]
        return min(marks) if marks else None

    feeds = [(d.feed, d in stateful) for d in derivers]
    step = snaps.step if snaps is not None else None
    produced_by_stateful = False
    for entry in _entries(conn, ranges, cp):
        if step is not None and fed:
            step(produced_by_stateful, entry.id)  # also: fed() of the previous entry
            if not fed & 63:
                snaps.prune(watermark())  # type: ignore[union-attr]
        produced_by_stateful = False
        for feed, is_stateful in feeds:
            produced = feed(entry)
            if produced:
                pending.extend(produced)
                produced_by_stateful = produced_by_stateful or is_stateful
        fed += 1
        last_id = entry.id
        if len(pending) >= WRITE_EVERY:
            write()

    end_mark = watermark()
    end_cp = end_mark if end_mark is not None else (last_id + 1 if last_id is not None else cp)
    # The ingestion deletes and re-adds the tail record: an event that already touches the last entry is
    # lost with it and the deriver state that produced it is gone, so the next pass must derive it again.
    track(pending)
    if last_id is not None and tail[0] == last_id:
        end_cp = min(end_cp, *tail[1])
    states: dict[str, Any] = {}
    if snaps is not None:
        if last_id is not None:
            snaps.fed(produced_by_stateful)
            snaps.observe(last_id + 1)
        end_cp, states = snaps.best(end_cp)
    for d in derivers:
        pending.extend(d.flush(eof=True))
    write()
    if not monotone:
        end_cp = 0
    rec = {"sig": sig, "rules": rules_sig, "cp": end_cp, "states": states}
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_record_key(stream_id, chain), json.dumps(rec, sort_keys=True, separators=(",", ":"))),
    )
    out.entries += fed
    out.full = out.full or cp == 0
    return end_cp


def _clock_jump_pass(conn: sqlite3.Connection, group: int) -> None:
    """Mark the gaps of a collection that are a one hour hole in every chain at the same time."""
    from dmslog.derive.lifecycle import KIND_GAP, possible_clock_jumps

    streams = conn.execute(
        "SELECT id FROM stream WHERE collection_group = ? ORDER BY id", (group,)
    ).fetchall()
    units: dict[tuple[int, str], int] = {}  # (stream id, chain) -> unit key
    for (sid,) in streams:
        for name in _chains(_sources(conn, sid)):
            units[(sid, name)] = len(units)
    if len(units) < 2:
        return
    multi = {sid: sum(1 for k in units if k[0] == sid) > 1 for (sid,) in streams}
    by_unit: dict[int, list[Event]] = {u: [] for u in units.values()}
    stored: dict[
        tuple[int, int | None], tuple[int, dict[str, Any]]
    ] = {}  # (stream, start) -> (event id, detail)
    for eid, sid, start, ts0, ts1, dur, detail in conn.execute(
        "SELECT e.id, e.stream_id, e.start_entry_id, e.ts_start, e.ts_end, e.duration_ms, e.detail"
        " FROM event e"
        " JOIN stream s ON s.id = e.stream_id WHERE s.collection_group = ? AND e.kind = ?",
        (group, KIND_GAP),
    ):
        data = json.loads(detail) if detail else {}
        chain = data.get("chain") if multi.get(sid) else next(k[1] for k in units if k[0] == sid)
        unit = units.get((sid, chain))
        if unit is None:
            continue
        stored[(sid, start)] = (eid, data)
        by_unit[unit].append(
            Event(kind=KIND_GAP, ts_start=ts0, ts_end=ts1, duration_ms=dur, start_entry_id=start,
                  detail=data, tid=sid)
        )  # fmt: skip
    flagged = {(ev.tid, ev.start_entry_id) for ev in possible_clock_jumps(by_unit)}
    for key, (eid, data) in stored.items():
        want = key in flagged
        has = bool(data.get("possible_clock_jump"))
        if want == has:
            continue
        data = dict(data)
        if want:
            data.update(possible_clock_jump=True, streams=len(units))
        else:
            for k in ("possible_clock_jump", "streams"):
                data.pop(k, None)
        conn.execute(
            "UPDATE event SET detail = ? WHERE id = ?",
            (json.dumps(data, sort_keys=True, separators=(",", ":")), eid),
        )


def derive_pending(conn: sqlite3.Connection, catalog: Any, kb: Any = None) -> DeriveSummary:
    """Derive every stream whose chains changed since their last derivation (cheap check per chain).

    A stream that fails is rolled back and reported in ``errors``; the others are still derived. Afterwards
    the clock-jump pass runs for the collections that had a stream derived.
    """
    summary = DeriveSummary()
    changed_groups: set[int] = set()
    for stream_id, group in conn.execute("SELECT id, collection_group FROM stream ORDER BY id").fetchall():
        summary.streams += 1
        try:
            result = derive_stream(conn, stream_id, catalog, kb)
        except Exception as exc:  # derivation must never abort an ingestion
            summary.errors.append((stream_id, f"{type(exc).__name__}: {exc}"))
            continue
        if not result.skipped:
            summary.derived += 1
            summary.events += result.events
            summary.samples += result.samples
            changed_groups.add(group)
    for group in sorted(changed_groups):
        own_tx = not conn.in_transaction
        savepoint = f"clock_jump_{group}"
        try:
            if own_tx:
                conn.execute("BEGIN")
            conn.execute(f"SAVEPOINT {savepoint}")
            _clock_jump_pass(conn, group)
            if own_tx:
                conn.execute("COMMIT")
            else:
                conn.execute(f"RELEASE {savepoint}")
        except Exception as exc:
            _undo(conn, savepoint, own_tx)
            summary.errors.append((0, f"clock-jump pass failed: {type(exc).__name__}: {exc}"))
    return summary
