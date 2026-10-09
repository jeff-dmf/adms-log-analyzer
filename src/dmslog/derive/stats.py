"""Statistics deriver: ``[Communication<n>]`` blocks -> ``stat_sample`` rows and events (ARQUITETURA 6.5).

Block layout (one per side, written about every hour; counters are cumulative since the process start)::

    [Communication2] - Client-side statistics:                  (GEN-STA-01)
    [Communication2] - [TOTAL][Client] Call count: ...          (GEN-STA-02)
    [Communication2] - By call count:                           (GEN-STA-03, CSV in the continuation)
    [Communication2] - By Request Message Size Total:           (client only; empty sections are not written)
    [Communication2] - By Response Message Size Total:
    [Communication2] - Service-side statistics:                 (next block; same shape, usually one section)

The block of a side ends at the next header, at a non-statistics record of the same thread, when
the timestamps run too far from the header, or at ``flush``. A block needs its header and its totals
line to produce samples; a block cut by the file start or the rotation (orphan totals/sections, or a
header without totals) produces nothing and is counted in ``skipped`` (reported in the ``detail`` of
the next ``stats_block`` event). Parsing uses the ``msg`` and the ``cont`` of the entry, so it does
not depend on the template catalog (``rules`` is accepted for API symmetry and unused).

Re-derivation: ``low_watermark`` is the first entry of the oldest open block. The previous
cumulative values (needed for deltas) are not derivable from the tail alone, so they are exposed by
``export_state`` / ``import_state`` (plain JSON-able dict) for the engine to persist. The state holds
everything that influences the output: the previous cumulative values, the same-timestamp
de-duplication keys, the orphan channels and the pending partial-block counters.

Bounded memory: the retained state is capped (``MAX_STATE_CHANNELS`` previous-value maps, the same number
of orphan channels, ``MAX_DEDUP_KEYS`` de-duplication keys); the oldest channel is evicted first and every
drop is counted in ``state_dropped`` of the next ``stats_block`` event. ``samples`` is drained by the
engine through ``drain_samples()`` (it returns and clears the list).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from dmslog.derive.base import Event, StatSample, StoredEntry

TOTAL = "[TOTAL]"
MAX_METHODS = 4096  # per (chan, side): cap of methods kept for deltas and per block
MAX_OPEN_BLOCKS = 8  # distinct channels with an open block
MAX_BLOCK_ENTRY_IDS = 64
MAX_STATE_CHANNELS = 64  # (chan, side) maps of previous values kept, and orphan channels remembered
MAX_DEDUP_KEYS = 16_384  # (side, method) keys remembered for the last sample timestamp
STALE_MS = 120_000  # a block does not span more than this from its header

_HDR = re.compile(r"\[Communication(\d+)\] - (Client|Service)-side statistics:")
_TOT = re.compile(
    r"\[Communication(\d+)\] - \[TOTAL\]\[(Client|Service)\] Call count: (\d+), Request bytes: (\d+) "
    r"\(avg\. [\d.]+\), Response bytes: (\d+) \(avg\. [\d.]+\)\. Total operations: (\d+)"
)
_SEC = re.compile(
    r"\[Communication(\d+)\] - By (call count|Request Message Size Total|Response Message Size Total):"
)
_ROW = re.compile(r"([^,\s\[][^,]*),(\d{1,19}),(\d{1,19}),(\d{1,19})")

_Counters = tuple[int, int, int]  # calls, req_bytes, resp_bytes
_NAMES = ("calls", "req_bytes", "resp_bytes")


class _Block:
    __slots__ = (
        "chan",
        "side",
        "start_id",
        "ts",
        "tid",
        "last_id",
        "last_ts",
        "ids",
        "totals",
        "ops",
        "methods",
        "sections",
        "dropped",
    )

    def __init__(self, chan: int, side: str, entry: StoredEntry) -> None:
        self.chan = chan
        self.side = side
        self.start_id = entry.id
        self.ts = entry.ts
        self.tid = entry.tid
        self.last_id = entry.id
        self.last_ts = entry.ts
        self.ids: list[int] = [entry.id]
        self.totals: _Counters | None = None
        self.ops: int | None = None
        self.methods: dict[str, _Counters] = {}
        self.sections = 0
        self.dropped = 0  # methods over the cap


class StatsDeriver:
    kind = "stats_block"

    def __init__(self, family: str, rules: Any = None) -> None:
        self.family = family
        self.rules = rules
        self.samples: list[StatSample] = []
        self.skipped = 0  # partial blocks not yet reported in an event
        self.skipped_total = 0
        self._state_dropped = 0  # evicted history entries not yet reported in an event
        self._open: dict[int, _Block] = {}
        self._wm: int | None = None  # low watermark and tids of the open blocks, refreshed whenever
        self._tids: set[int | None] = set()  # ``_open`` changes (see ``_refresh``)
        self._orphan_chans: dict[int, None] = {}  # insertion-ordered set
        # (chan, side) -> {method: (calls, req, resp)}; TOTAL included under TOTAL
        self._prev: dict[tuple[int, str], dict[str, _Counters]] = {}
        self._last_ts_keys: set[tuple[int, str, str]] = set()
        self._last_sample_ts: int | None = None

    # ---------------------------------------------------------------- Deriver protocol

    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        if "[Communication" not in entry.msg:
            # Not a statistics record (every pattern needs the text): it only ends the open blocks of
            # its own thread, so look for one before doing any work.
            tid = entry.tid
            if tid is None:
                if not self._open:
                    self._orphan_chans.clear()
                    return ()
            elif tid not in self._tids and None not in self._tids:
                return ()
        out: list[Event] = []
        try:
            self._feed(entry, out)
        except Exception:  # never raise on odd input; the block is dropped and counted
            self._open.clear()
            self._refresh()
            self.skipped += 1
            self.skipped_total += 1
        return out

    def flush(self, *, eof: bool) -> Iterable[Event]:
        out: list[Event] = []
        if eof:
            for chan in sorted(self._open):
                self._close(chan, out)
            self._orphan_chans.clear()
        return out

    def low_watermark(self) -> int | None:
        return self._wm

    def drain_samples(self) -> list[StatSample]:
        """Return the samples produced so far and forget them (keeps ``samples`` from growing)."""
        out, self.samples = self.samples, []
        return out

    # ---------------------------------------------------------------- state for re-derivation

    def export_state(self) -> dict[str, Any]:
        return {
            "prev": {f"{c}:{s}": {m: list(v) for m, v in d.items()} for (c, s), d in self._prev.items()},
            "last_sample_ts": self._last_sample_ts,
            "last_ts_keys": sorted([side, method] for _, side, method in self._last_ts_keys),
            "orphan_chans": list(self._orphan_chans),
            "skipped": self.skipped,
            "skipped_total": self.skipped_total,
            "state_dropped": self._state_dropped,
        }

    def import_state(self, state: dict[str, Any]) -> None:
        self._prev = {}
        for k, d in (state.get("prev") or {}).items():
            chan, _, side = k.partition(":")
            self._prev[(int(chan), side)] = {m: (v[0], v[1], v[2]) for m, v in d.items()}
        ts = state.get("last_sample_ts")
        self._last_sample_ts = int(ts) if ts is not None else None
        self._last_ts_keys = (
            {(self._last_sample_ts, str(k[0]), str(k[1])) for k in state.get("last_ts_keys") or ()}
            if self._last_sample_ts is not None
            else set()
        )
        self._orphan_chans = dict.fromkeys(int(c) for c in state.get("orphan_chans") or ())
        self.skipped = int(state.get("skipped") or 0)
        self.skipped_total = int(state.get("skipped_total") or 0)
        self._state_dropped = int(state.get("state_dropped") or 0)

    # ---------------------------------------------------------------- internals

    def _feed(self, entry: StoredEntry, out: list[Event]) -> None:
        msg = (entry.msg or "").strip()
        h = _HDR.fullmatch(msg)
        t = _TOT.fullmatch(msg) if h is None else None
        s = _SEC.fullmatch(msg) if h is None and t is None else None
        if h is None and t is None and s is None:
            # Not a statistics record: it ends the blocks of its own thread.
            for chan in sorted(self._open):
                b = self._open[chan]
                if b.tid is None or entry.tid is None or b.tid == entry.tid:
                    self._close(chan, out)
            if entry.tid is None:
                self._orphan_chans.clear()
            return
        chan = int((h or t or s).group(1))  # type: ignore[union-attr]
        # Stale open blocks (cut block followed by an unrelated one much later).
        if entry.ts is not None:
            for c in sorted(self._open):
                b = self._open[c]
                if b.ts is not None and abs(entry.ts - b.ts) > STALE_MS:
                    self._close(c, out)
        if h is not None:
            self._orphan_chans.pop(chan, None)
            if chan in self._open:
                self._close(chan, out)
            if len(self._open) >= MAX_OPEN_BLOCKS:
                self.skipped += 1
                self.skipped_total += 1
                return
            self._open[chan] = _Block(chan, h.group(2).lower(), entry)
            self._refresh()
            return
        block = self._open.get(chan)
        if (
            t is not None
            and block is not None
            and (block.totals is not None or t.group(2).lower() != block.side)
        ):
            # Second totals line, or totals of the other side without its header: new (headerless) block.
            self._close(chan, out)
            block = None
        if block is None:
            if chan not in self._orphan_chans:
                self._orphan_chans[chan] = None
                if len(self._orphan_chans) > MAX_STATE_CHANNELS:
                    del self._orphan_chans[next(iter(self._orphan_chans))]
                    self._state_dropped += 1
                self.skipped += 1
                self.skipped_total += 1
            return
        block.last_id = entry.id
        block.last_ts = entry.ts if entry.ts is not None else block.last_ts
        if len(block.ids) < MAX_BLOCK_ENTRY_IDS:
            block.ids.append(entry.id)
        if t is not None:
            block.totals = (int(t.group(3)), int(t.group(4)), int(t.group(5)))
            block.ops = int(t.group(6))
        else:
            block.sections += 1
            self._read_rows(block, entry)

    @staticmethod
    def _read_rows(block: _Block, entry: StoredEntry) -> None:
        rows: list[Any] = []
        if entry.cont:
            for line in entry.cont.split("\n"):
                m = _ROW.fullmatch(line.strip())
                if m:
                    rows.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))
        else:
            rows = [
                r for r in (entry.fields.get("rows") or []) if isinstance(r, (list, tuple)) and len(r) == 4
            ]
        for name, calls, req, resp in rows:
            if name == TOTAL:
                continue
            if name not in block.methods and len(block.methods) >= MAX_METHODS:
                block.dropped += 1
                continue
            block.methods[name] = (int(calls), int(req), int(resp))  # later sections overwrite (same data)

    def _refresh(self) -> None:
        blocks = self._open.values()
        self._wm = min(b.start_id for b in blocks) if blocks else None
        self._tids = {b.tid for b in blocks}

    def _close(self, chan: int, out: list[Event]) -> None:
        block = self._open.pop(chan, None)
        if block is None:
            return
        self._refresh()
        if block.totals is None or block.ts is None:
            self.skipped += 1
            self.skipped_total += 1
            return
        ts = block.ts
        prev = self._prev.get((chan, block.side))
        # Same-ts duplicates (repeated block, other channel): the sample key (ts, side, method) is unique.
        if self._last_sample_ts != ts:
            self._last_sample_ts = ts
            self._last_ts_keys = set()
        dup_samples = 0

        drops: dict[str, list[int]] = {}
        if prev is not None and TOTAL in prev:
            for name, p, n in zip(_NAMES, prev[TOTAL], block.totals, strict=True):
                if n < p:
                    drops[name] = [p, n]
        restart = bool(drops)

        new_prev: dict[str, _Counters] = dict(prev) if (prev is not None and not restart) else {}
        rows: list[tuple[str, _Counters]] = [(TOTAL, block.totals), *sorted(block.methods.items())]
        for method, cur in rows:
            key = (ts, block.side, method)
            old = None if (prev is None or restart) else prev.get(method)
            if prev is None or restart:
                delta: _Counters | None = None
            elif old is None:
                delta = cur  # method first seen after the first sample: cumulative started at zero
            else:
                delta = (cur[0] - old[0], cur[1] - old[1], cur[2] - old[2])
                if min(delta) < 0:
                    delta = None  # per-method drop without a total drop
            if method != TOTAL and method not in new_prev and len(new_prev) >= MAX_METHODS + 1:
                block.dropped += 1
                continue
            new_prev[method] = cur
            if (ts, block.side, method) in self._last_ts_keys:
                dup_samples += 1
                continue
            if len(self._last_ts_keys) < MAX_DEDUP_KEYS:
                self._last_ts_keys.add(key)
            else:
                self._state_dropped += 1
            self.samples.append(
                StatSample(
                    ts=ts,
                    side=block.side,
                    method=method,
                    calls=cur[0],
                    req_bytes=cur[1],
                    resp_bytes=cur[2],
                    d_calls=delta[0] if delta else None,
                    d_req_bytes=delta[1] if delta else None,
                    d_resp_bytes=delta[2] if delta else None,
                )
            )
        self._prev.pop((chan, block.side), None)  # re-insert: the dict stays ordered by last update
        self._prev[(chan, block.side)] = new_prev
        while len(self._prev) > MAX_STATE_CHANNELS:
            del self._prev[next(iter(self._prev))]
            self._state_dropped += 1

        detail: dict[str, Any] = {
            "chan": chan,
            "calls": block.totals[0],
            "req_bytes": block.totals[1],
            "resp_bytes": block.totals[2],
            "ops": block.ops,
            "n_methods": len(block.methods),
            "n_sections": block.sections,
        }
        if prev is not None and not restart and TOTAL in prev:
            detail["d_calls"] = block.totals[0] - prev[TOTAL][0]
        if self.skipped:
            detail["skipped_partial_blocks"] = self.skipped
            self.skipped = 0
        if self._state_dropped:
            detail["state_dropped"] = self._state_dropped
            self._state_dropped = 0
        if dup_samples:
            detail["duplicate_samples"] = dup_samples
        if block.dropped:
            detail["methods_dropped"] = block.dropped
        if restart:
            detail["restart"] = True
        end_ts = block.last_ts if block.last_ts is not None else ts
        out.append(
            Event(
                kind="stats_block",
                ts_start=ts,
                name=block.side,
                tid=block.tid,
                start_entry_id=block.start_id,
                end_entry_id=block.last_id,
                ts_end=end_ts,
                duration_ms=max(0, end_ts - ts),
                status="ok",
                sev=5,
                n_entries=len(block.ids),
                detail=detail,
                entry_ids=list(block.ids),
            )
        )
        if restart:
            out.append(
                Event(
                    kind="restart",
                    ts_start=ts,
                    name=block.side,
                    tid=block.tid,
                    start_entry_id=block.start_id,
                    end_entry_id=block.start_id,
                    status="ok",
                    sev=3,
                    n_entries=1,
                    detail={"source": "stats", "chan": chan, "side": block.side, "drops": drops},
                    entry_ids=[block.start_id],
                )
            )
