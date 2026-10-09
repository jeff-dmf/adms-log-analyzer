"""Statistics deriver (ARQUITETURA 6.5 'stats'): synthetic [Communication<n>] blocks."""

from __future__ import annotations

import json

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.stats import MAX_STATE_CHANNELS, TOTAL, StatsDeriver

HOUR = 3_600_000
T0 = 1_700_000_000_000


class Feed:
    """Builds consecutive StoredEntry ids for hand-made blocks."""

    def __init__(self) -> None:
        self.n = 0
        self.entries: list[StoredEntry] = []

    def add(self, ts: int | None, msg: str, cont: str = "", tid: int | None = 7) -> None:
        self.n += 1
        self.entries.append(
            StoredEntry(
                id=self.n,
                stream_id=1,
                ts=ts,
                tid=tid,
                pid=1,
                level=None,
                sev=5,
                tpl_code=None,
                tpl_class=None,
                fields={},
                msg=msg,
                cont=cont,
            )
        )

    def header(self, ts: int | None, side: str, chan: int = 2) -> None:
        self.add(ts, f"[Communication{chan}] - {side}-side statistics:")

    def total(self, ts: int | None, side: str, calls: int, req: int, resp: int, chan: int = 2) -> None:
        self.add(
            ts,
            f"[Communication{chan}] - [TOTAL][{side}] Call count: {calls}, Request bytes: {req} (avg. 1), "
            f"Response bytes: {resp} (avg. 2.5). Total operations: {calls}",
        )

    def section(
        self, ts: int | None, order: str, rows: list[tuple[str, int, int, int]], chan: int = 2
    ) -> None:
        body = "[method name, call count, request total size, response total size]\n"
        body += "".join(f"{m},{c},{q},{r}\n" for m, c, q, r in rows)
        self.add(ts, f"[Communication{chan}] - By {order}: ", cont=body)

    def other(self, ts: int | None, tid: int | None = 7) -> None:
        self.add(ts, "Something else happened", tid=tid)

    def block(self, ts: int, scale: int = 1, other_after: bool = True) -> None:
        """Client (3 sections) + Service (1 section); counters grow with ``scale``."""
        a = ("Example.Contract.IQuery.Get", 2 * scale, 20 * scale, 200 * scale)
        b = ("Example.Contract.IQuery.List", 1 * scale, 10 * scale, 100 * scale)
        self.header(ts, "Client")
        self.total(ts, "Client", 3 * scale, 30 * scale, 300 * scale)
        for order in ("call count", "Request Message Size Total", "Response Message Size Total"):
            self.section(ts, order, [a, b])
        self.header(ts, "Service")
        self.total(ts, "Service", 1 * scale, 5 * scale, 50 * scale)
        self.section(ts, "call count", [("Example.Contract.IService.Do", scale, 5 * scale, 50 * scale)])
        if other_after:
            self.other(ts + 5)


def derive(entries: list[StoredEntry], eof: bool = True) -> tuple[StatsDeriver, list]:
    d = StatsDeriver("example", None)
    return d, run_in_memory([d], entries, eof=eof)


def by(samples, side: str, method: str):
    return [s for s in samples if s.side == side and s.method == method]


def test_two_blocks_cumulative_and_delta() -> None:
    f = Feed()
    f.block(T0, 1)
    f.block(T0 + HOUR, 3)
    d, events = derive(f.entries)
    blocks = [e for e in events if e.kind == "stats_block"]
    assert [(e.name, e.ts_start) for e in blocks] == [
        ("client", T0),
        ("service", T0),
        ("client", T0 + HOUR),
        ("service", T0 + HOUR),
    ]
    assert not [e for e in events if e.kind == "restart"]
    assert (
        blocks[0].detail["calls"] == 3
        and blocks[0].detail["n_methods"] == 2
        and blocks[0].detail["n_sections"] == 3
    )
    first, second = by(d.samples, "client", TOTAL)
    assert (first.calls, first.d_calls) == (3, None)
    assert (second.calls, second.d_calls, second.d_req_bytes, second.d_resp_bytes) == (9, 6, 60, 600)
    m1, m2 = by(d.samples, "client", "Example.Contract.IQuery.Get")
    assert (m1.d_calls, m2.calls, m2.d_calls) == (None, 6, 4)
    svc = by(d.samples, "service", "Example.Contract.IService.Do")
    assert [s.d_calls for s in svc] == [None, 2]
    assert blocks[0].entry_ids[0] == blocks[0].start_entry_id


def test_restart_when_a_total_drops() -> None:
    f = Feed()
    f.block(T0, 5)
    f.block(T0 + HOUR, 1)
    f.block(T0 + 2 * HOUR, 2)
    d, events = derive(f.entries)
    restarts = [e for e in events if e.kind == "restart"]
    assert [(e.name, e.ts_start) for e in restarts] == [("client", T0 + HOUR), ("service", T0 + HOUR)]
    assert restarts[0].detail["drops"] == {
        "calls": [15, 3],
        "req_bytes": [150, 30],
        "resp_bytes": [1500, 300],
    }
    after = by(d.samples, "client", TOTAL)
    assert [s.d_calls for s in after] == [None, None, 3]
    assert all(s.d_calls is None for s in d.samples if s.ts == T0 + HOUR)
    # only one total dropping is enough
    g = Feed()
    g.header(T0, "Service")
    g.total(T0, "Service", 10, 100, 1000)
    g.other(T0 + 1)
    g.header(T0 + HOUR, "Service")
    g.total(T0 + HOUR, "Service", 11, 100, 999)
    _, ev = derive(g.entries)
    assert [e.detail["drops"] for e in ev if e.kind == "restart"] == [{"resp_bytes": [1000, 999]}]


def test_partial_block_at_file_start_is_skipped_and_counted() -> None:
    f = Feed()
    f.section(T0 - 1, "Response Message Size Total", [("Example.Contract.IQuery.Get", 1, 2, 3)])
    f.section(T0 - 1, "call count", [("Example.Contract.IQuery.Get", 1, 2, 3)])
    f.header(T0 - 1, "Service")  # header without totals (cut by rotation)
    f.other(T0 - 1)
    f.block(T0, 1)
    d, events = derive(f.entries)
    assert d.skipped_total == 2
    blocks = [e for e in events if e.kind == "stats_block"]
    assert len(blocks) == 2 and blocks[0].detail["skipped_partial_blocks"] == 2
    assert "skipped_partial_blocks" not in blocks[1].detail
    assert {s.ts for s in d.samples} == {T0}
    assert all(s.d_calls is None for s in d.samples)


def test_totals_without_header_is_skipped() -> None:
    f = Feed()
    f.total(T0, "Client", 3, 30, 300)
    f.section(T0, "call count", [("Example.Contract.IQuery.Get", 3, 30, 300)])
    _, events = derive(f.entries)
    assert events == []


def test_equal_ts_repeated_block_does_not_duplicate_samples() -> None:
    f = Feed()
    f.block(T0, 1, other_after=False)
    f.block(T0, 1)
    d, events = derive(f.entries)
    keys = [(s.ts, s.side, s.method) for s in d.samples]
    assert len(keys) == len(set(keys))
    blocks = [e for e in events if e.kind == "stats_block"]
    assert len(blocks) == 4
    assert blocks[2].detail["duplicate_samples"] == 3  # TOTAL + 2 methods of the client block
    assert not [e for e in events if e.kind == "restart"]


def test_odd_input_never_raises() -> None:
    f = Feed()
    f.header(None, "Client")
    f.total(None, "Client", 1, 1, 1)
    f.other(None, tid=None)
    f.header(T0, "Client")
    f.total(T0, "Client", 5, 5, 5)
    f.add(T0, "[Communication2] - By call count: ", cont="garbage\n\n,,,\nMethod,x,y,z\n")
    f.block(T0 - 10 * HOUR, 1)  # out of order
    d, events = derive(f.entries)
    assert any(s.ts == T0 and s.method == TOTAL for s in d.samples)
    assert d.low_watermark() is None


def test_block_open_at_eof_false_keeps_watermark_and_eof_closes() -> None:
    f = Feed()
    f.header(T0, "Client")
    f.total(T0, "Client", 4, 40, 400)
    d = StatsDeriver("example")
    for e in f.entries:
        assert list(d.feed(e)) == []
    assert list(d.flush(eof=False)) == []
    assert d.low_watermark() == 1
    events = list(d.flush(eof=True))
    assert [e.kind for e in events] == ["stats_block"] and d.low_watermark() is None
    assert [s.method for s in d.samples] == [TOTAL]


def test_rederivation_in_two_halves_gives_same_samples() -> None:
    f = Feed()
    for i, scale in enumerate((1, 2, 3, 1, 2)):
        f.block(T0 + i * HOUR, scale)
    full, full_events = derive(f.entries)

    # First run stops in the middle of the 3rd client block; the second restarts at the watermark.
    cut = next(i for i, e in enumerate(f.entries) if e.ts == T0 + 2 * HOUR) + 3
    first = StatsDeriver("example")
    ev1 = []
    for e in f.entries[:cut]:
        ev1.extend(first.feed(e))
    ev1.extend(first.flush(eof=False))
    wm = first.low_watermark()
    assert wm == f.entries[cut - 3].id  # header of the open block
    kept = [s for s in first.samples if s.ts < T0 + 2 * HOUR]
    kept_events = [e for e in ev1 if e.ts_start < T0 + 2 * HOUR]

    second = StatsDeriver("example")
    second.import_state({"prev": _prev_before(first, T0 + 2 * HOUR, f.entries)})
    ev2 = []
    for e in f.entries:
        if e.id >= wm:
            ev2.extend(second.feed(e))
    ev2.extend(second.flush(eof=True))
    assert kept + second.samples == full.samples
    assert [(e.kind, e.name, e.ts_start, e.detail) for e in kept_events + ev2] == [
        (e.kind, e.name, e.ts_start, e.detail) for e in full_events
    ]


def _prev_before(first: StatsDeriver, ts: int, entries: list[StoredEntry]) -> dict:
    """The engine persists ``export_state`` at the checkpoint; here: state after blocks before ``ts``."""
    ref = StatsDeriver("example")
    for e in entries:
        if e.ts is not None and e.ts >= ts:
            break
        list(ref.feed(e))
    list(ref.flush(eof=True))
    return ref.export_state()["prev"]


def test_uses_fields_rows_when_cont_is_empty_and_other_channel() -> None:
    f = Feed()
    f.header(T0, "Client", chan=3)
    f.total(T0, "Client", 1, 2, 3, chan=3)
    f.entries.append(
        StoredEntry(
            id=99,
            stream_id=1,
            ts=T0,
            tid=7,
            pid=1,
            level=None,
            sev=5,
            tpl_code=None,
            tpl_class=None,
            fields={"rows": [["Example.Contract.IQuery.Get", 1, 2, 3]]},
            msg="[Communication3] - By call count:",
        )
    )
    d, events = derive(f.entries)
    assert events[0].detail["chan"] == 3 and events[0].detail["n_methods"] == 1
    assert [s.method for s in d.samples] == [TOTAL, "Example.Contract.IQuery.Get"]


def test_incremental_state_keeps_dedup_and_pending_counters() -> None:
    """Two channels with the same ts: the second block's samples are dropped in both runs."""
    f = Feed()
    f.add(
        T0,
        "[Communication9] - [TOTAL][Client] Call count: 1, Request bytes: 1 (avg. 1.0), "
        "Response bytes: 1 (avg. 1.0). Total operations: 1",
    )  # orphan on channel 9 (skipped)
    f.header(T0 + 10, "Client", chan=2)
    f.total(T0 + 10, "Client", 5, 50, 500, chan=2)
    f.section(T0 + 10, "call count", [("Example.Contract.IQuery.Get", 5, 50, 500)], chan=2)
    f.other(T0 + 10)
    f.header(T0 + 10, "Client", chan=3)
    f.total(T0 + 10, "Client", 10, 100, 1000, chan=3)
    f.section(T0 + 10, "call count", [("Example.Contract.IQuery.Get", 10, 100, 1000)], chan=3)
    f.other(T0 + 20)
    f.add(
        T0 + 30,
        "[Communication4] - [TOTAL][Client] Call count: 1, Request bytes: 1 (avg. 1.0), "
        "Response bytes: 1 (avg. 1.0). Total operations: 1",
    )  # second orphan, pending
    full, full_events = derive(f.entries)
    assert [s.calls for s in full.samples if s.method == TOTAL] == [5]

    # Run 1 stops after the channel-2 block closed; run 2 resumes with the exported state.
    cut = next(i for i, e in enumerate(f.entries) if "Communication3" in e.msg and "statistics" in e.msg)
    first = StatsDeriver("example")
    ev1 = []
    for e in f.entries[:cut]:
        ev1.extend(first.feed(e))
    ev1.extend(first.flush(eof=False))
    state = json.loads(json.dumps(first.export_state()))  # must survive a JSON round trip
    second = StatsDeriver("example")
    second.import_state(state)
    ev2 = []
    for e in f.entries[cut:]:
        ev2.extend(second.feed(e))
    ev2.extend(second.flush(eof=True))
    assert first.samples + second.samples == full.samples
    assert [(e.kind, e.detail) for e in ev1 + ev2] == [(e.kind, e.detail) for e in full_events]
    assert second.skipped_total == full.skipped_total


def test_state_is_bounded_and_drops_are_counted() -> None:
    f = Feed()
    n = MAX_STATE_CHANNELS + 36
    for i in range(n):
        f.header(T0 + i * 1000, "Client", chan=100 + i)
        f.total(T0 + i * 1000, "Client", 1, 1, 1, chan=100 + i)
        f.other(T0 + i * 1000 + 1)
    for i in range(n):  # orphans (totals without header) of other channels
        f.total(T0 + n * 1000 + i, "Client", 1, 1, 1, chan=1000 + i)
    d, events = derive(f.entries)
    state = d.export_state()
    assert len(state["prev"]) == MAX_STATE_CHANNELS
    assert len(state["orphan_chans"]) <= MAX_STATE_CHANNELS
    assert sum(e.detail.get("state_dropped", 0) for e in events if e.kind == "stats_block") == 36


def test_drain_samples_returns_and_clears() -> None:
    f = Feed()
    f.block(T0)
    d, _ = derive(f.entries)
    first = list(d.samples)
    assert first
    assert d.drain_samples() == first
    assert d.samples == [] and d.drain_samples() == []
