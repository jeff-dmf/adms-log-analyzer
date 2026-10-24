"""Unit tests of the life-cycle derivers (all entries are synthetic)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from dmslog.derive.base import STATUS_OK, STATUS_OPEN, STATUS_TRUNCATED, Event, StoredEntry, run_in_memory
from dmslog.derive.lifecycle import (
    DEFAULT_GAP_HEARTBEAT_S,
    MAX_MAP_ENTRY_IDS,
    LifecycleDeriver,
    possible_clock_jumps,
    resolve_heartbeat_ms,
)

T0 = 1_700_000_000_000
MIN = 60_000
HOUR = 3_600_000


class Maker:
    """Builds StoredEntry rows with increasing ids."""

    def __init__(self, start_id: int = 1) -> None:
        self._ids = itertools.count(start_id)

    def __call__(
        self,
        ts: int | None,
        msg: str = "x",
        *,
        code: str | None = None,
        fields: dict[str, Any] | None = None,
        cont: str = "",
        sev: int = 4,
        tid: int | None = 7,
    ) -> StoredEntry:
        return StoredEntry(
            id=next(self._ids),
            stream_id=1,
            ts=ts,
            tid=tid,
            pid=1,
            level=None,
            sev=sev,
            tpl_code=code,
            tpl_class=None,
            fields=fields or {},
            msg=msg,
            cont=cont,
        )


def level_change(mk: Maker, ts: int, frm: tuple[str, int], to: tuple[str, int]) -> StoredEntry:
    return mk(
        ts,
        f"Debug level changed from {frm[0]} ({frm[1]}) to {to[0]} ({to[1]}).",
        code="GEN-DBG-01",
        fields={"from_name": frm[0], "from_n": frm[1], "to_name": to[0], "to_n": to[1]},
    )


def hot(mk: Maker, ts: int) -> StoredEntry:
    return mk(
        ts,
        "[SystemStatusPublisher] - Server state changed. New state: HOT",
        code="GEN-STU-01",
        fields={"state": "HOT"},
    )


def cfg(mk: Maker, ts: int) -> StoredEntry:
    return mk(
        ts,
        "[Configuration] [LogLevel]: [DebugLog]. Type: [ConfigurationRegistry]",
        code="GEN-CFG-01",
        fields={"param": "LogLevel", "value": "DebugLog"},
    )


def other(mk: Maker, ts: int | None, msg: str = "Something happened.") -> StoredEntry:
    return mk(ts, msg)


def of_kind(events: list[Event], kind: str) -> list[Event]:
    return [e for e in events if e.kind == kind]


def derive(entries: list[StoredEntry], family: str = "AWFM", **kw: Any) -> list[Event]:
    return run_in_memory([LifecycleDeriver(family, **kw)], entries)


# --------------------------------------------------------------------------- startup
def test_startup_closes_5s_after_hot() -> None:
    mk = Maker()
    entries = [
        cfg(mk, T0),
        mk(
            T0 + 100,
            "[ServiceUrl] : For X service url is http://example.invalid/x",
            code="GEN-CFG-04",
            fields={"url": "http://example.invalid/x"},
        ),
        mk(
            T0 + 200,
            "<AdapterExample> Initialize startup manager on port 9000.",
            code="GEN-STU-04",
            fields={"port": 9000},
        ),
        hot(mk, T0 + 10_000),
        other(mk, T0 + 14_999),
        other(mk, T0 + 15_000),  # exactly 5 s after HOT: still inside
        other(mk, T0 + 15_001),  # outside
    ]
    (ev,) = of_kind(derive(entries), "startup")
    assert ev.start_entry_id == entries[0].id
    assert ev.end_entry_id == entries[5].id
    assert ev.entry_ids == [e.id for e in entries[:6]]
    assert ev.n_entries == 6
    assert ev.ts_start == T0 and ev.ts_end == T0 + 15_000
    assert ev.detail["closed_by"] == "hot"
    assert ev.detail["hot_ts"] == T0 + 10_000
    assert ev.detail["port"] == 9000
    assert ev.detail["service_urls"] == ["http://example.invalid/x"]
    assert ev.status == STATUS_OK


def test_startup_closes_after_120s_without_hot() -> None:
    mk = Maker()
    entries = [cfg(mk, T0), other(mk, T0 + 120_000), other(mk, T0 + 120_001)]
    (ev,) = of_kind(derive(entries), "startup")
    assert ev.entry_ids == [entries[0].id, entries[1].id]
    assert ev.detail["closed_by"] == "timeout"
    assert "hot_ts" not in ev.detail


def test_startup_eof_without_hot_is_open_and_triggers_on_banner() -> None:
    mk = Maker()
    entries = [
        mk(T0, "=============================== {E:\\Builds\\1\\Example\\Main.cs:150}"),
        other(mk, T0 + 5_000),
    ]
    (ev,) = of_kind(derive(entries), "startup")
    assert ev.status == STATUS_OPEN and ev.detail["closed_by"] == "eof"


def test_no_startup_without_trigger_and_none_ts_inside_block() -> None:
    mk = Maker()
    assert of_kind(derive([other(mk, T0), other(mk, T0 + 1)]), "startup") == []
    mk = Maker()
    entries = [cfg(mk, T0), other(mk, None), hot(mk, T0 + 1_000), other(mk, T0 + 7_000)]
    (ev,) = of_kind(derive(entries), "startup")
    assert ev.entry_ids == [entries[0].id, entries[1].id, entries[2].id]


# --------------------------------------------------------------------------- level
def test_level_windows_and_unknown_first_level() -> None:
    mk = Maker()
    entries = [
        other(mk, T0),
        other(mk, T0 + 1_000),
        level_change(mk, T0 + 2_000, ("DebugLog", 3), ("Info", 5)),
        other(mk, T0 + 3_000),
        level_change(mk, T0 + 9_000, ("Info", 5), ("Verbose", 6)),
        other(mk, T0 + 10_000),
    ]
    events = of_kind(derive(entries), "level_window")
    first, second, third = sorted(events, key=lambda e: e.ts_start)
    assert first.ts_start == T0 and first.ts_end == T0 + 2_000
    assert first.detail["from"] is None and first.detail["to"] == "DebugLog"
    assert first.detail["level_name"] == "DebugLog" and first.detail["level_num"] == 3
    assert first.start_entry_id == entries[0].id and first.end_entry_id == entries[1].id
    assert second.ts_start == T0 + 2_000 and second.ts_end == T0 + 9_000
    assert (second.detail["from"], second.detail["to"]) == ("DebugLog", "Info")
    assert second.detail["level_name"] == "Info" and second.detail["level_num"] == 5
    assert second.name == "Info" and second.status == STATUS_OK
    assert third.status == STATUS_OPEN and third.ts_end == T0 + 10_000
    assert third.detail["level_name"] == "Verbose"


def test_level_without_any_change_yields_nothing() -> None:
    mk = Maker()
    assert of_kind(derive([other(mk, T0), other(mk, T0 + 1)]), "level_window") == []


def test_level_fallback_on_msg_without_template() -> None:
    mk = Maker()
    entries = [
        other(mk, T0),
        mk(T0 + 1_000, "Debug level changed from Info (5) to Warning (4)."),
    ]
    events = of_kind(derive(entries), "level_window")
    assert [e.detail["level_name"] for e in events] == ["Info", "Warning"]


# --------------------------------------------------------------------------- gaps
def test_gap_boundaries_hourly_family() -> None:
    assert resolve_heartbeat_ms("AAVL") == 61 * MIN
    for delta, expected in [(60 * MIN + 54_000, 0), (61 * MIN, 0), (61 * MIN + 1, 1), (3 * HOUR, 1)]:
        mk = Maker()
        entries = [other(mk, T0), other(mk, T0 + delta)]
        gaps = of_kind(derive(entries, "AAVL"), "gap")
        assert len(gaps) == expected, delta
        if gaps:
            (g,) = gaps
            assert g.ts_start == T0 and g.ts_end == T0 + delta and g.duration_ms == delta
            assert g.start_entry_id == entries[0].id and g.end_entry_id == entries[1].id
            assert g.detail["heartbeat_ms"] == 61 * MIN


def test_gap_siteswitch_5s_and_sub_minute_boundary() -> None:
    for delta, expected in [(5_000, 0), (5_001, 1), (60_900, 1)]:
        mk = Maker()
        gaps = of_kind(derive([other(mk, T0), other(mk, T0 + delta)], "ASSS"), "gap")
        assert len(gaps) == expected, delta


def test_gap_only_between_entries_and_skips_none_ts() -> None:
    mk = Maker()
    entries = [other(mk, T0), other(mk, None), other(mk, T0 + 2 * HOUR)]
    (g,) = of_kind(derive(entries, "AAVL"), "gap")
    assert g.start_entry_id == entries[0].id and g.end_entry_id == entries[2].id
    # a single entry or an unknown family never yields gaps
    mk = Maker()
    assert of_kind(derive([other(mk, T0)], "AAVL"), "gap") == []
    mk = Maker()
    assert of_kind(derive([other(mk, T0), other(mk, T0 + 9 * HOUR)], "AWFM"), "gap") == []


def test_heartbeat_resolution_order() -> None:
    class Rules:
        gap_heartbeat = 10

    assert resolve_heartbeat_ms("AWFM", Rules()) == 10_000
    assert resolve_heartbeat_ms("ASSS", Rules()) == 10_000
    assert resolve_heartbeat_ms("ASSS", Rules(), 2) == 2_000
    assert resolve_heartbeat_ms("ASSS") == DEFAULT_GAP_HEARTBEAT_S["ASSS"] * 1000
    assert resolve_heartbeat_ms("AWFM") is None
    assert resolve_heartbeat_ms("AWFM", object(), 0) is None
    mk = Maker()
    assert len(of_kind(derive([other(mk, T0), other(mk, T0 + 3_000)], "AWFM", gap_heartbeat_s=2), "gap")) == 1


# --------------------------------------------------------------------------- clock
def test_clock_jump_dst_back_step_and_no_ts_alteration() -> None:
    mk = Maker()
    entries = [
        other(mk, T0),
        other(mk, T0 + 10_000),
        other(mk, T0 + 10_000 - HOUR),
        other(mk, T0 + 20_000 - HOUR),
    ]
    before = [(e.id, e.ts) for e in entries]
    (ev,) = of_kind(derive(entries), "clock_jump")
    assert ev.detail["back_ms"] == HOUR
    assert ev.start_entry_id == entries[1].id and ev.end_entry_id == entries[2].id
    assert ev.ts_start == T0 + 10_000 - HOUR
    assert [(e.id, e.ts) for e in entries] == before


def test_clock_jump_threshold_and_out_of_order_jitter() -> None:
    mk = Maker()
    entries = [other(mk, T0), other(mk, T0 - 30 * MIN), other(mk, T0 - 30 * MIN - 5)]
    assert of_kind(derive(entries), "clock_jump") == []  # exactly 30 min and jitter: not a jump
    mk = Maker()
    assert len(of_kind(derive([other(mk, T0), other(mk, T0 - 30 * MIN - 1)]), "clock_jump")) == 1


# --------------------------------------------------------------------------- maps
def map_entry(mk: Maker, ts: int | None, name: str, state: str, cont: str = "") -> StoredEntry:
    text = {
        "closed": f'"{name}" map closed.',
        "content": f'New "{name}" map content:',
        "open": f'"{name}" map is now open.',
    }[state]
    return mk(
        ts,
        text,
        code="GEN-MAP-01",
        fields={
            "map": name,
            "state": {"closed": "closed.", "content": "content:", "open": "is now open."}[state],
        },
        cont=cont,
    )


def test_map_reload_with_items() -> None:
    mk = Maker()
    entries = [
        other(mk, T0),
        map_entry(mk, T0 + 1_000, "EXAMPLE_MAP", "closed"),
        map_entry(mk, T0 + 1_100, "EXAMPLE_MAP", "content", "\t1 - A\n\t2 - B\n\t3 - C"),
        map_entry(mk, T0 + 1_200, "EXAMPLE_MAP", "open"),
    ]
    (ev,) = of_kind(derive(entries), "map_reload")
    assert ev.name == "EXAMPLE_MAP" and ev.detail == {"map": "EXAMPLE_MAP", "items": 3}
    assert ev.ts_start == T0 + 1_000 and ev.ts_end == T0 + 1_200 and ev.duration_ms == 200
    assert ev.entry_ids == [e.id for e in entries[1:]]
    assert ev.status == STATUS_OK


def test_map_without_closed_is_not_a_reload_and_interleaved_maps() -> None:
    mk = Maker()
    startup = [map_entry(mk, T0, "A", "content", "\t1 - x"), map_entry(mk, T0 + 1, "A", "open")]
    assert of_kind(derive(startup), "map_reload") == []
    mk = Maker()
    entries = [
        map_entry(mk, T0, "A", "closed"),
        map_entry(mk, T0 + 1, "B", "closed"),
        map_entry(mk, T0 + 2, "B", "open"),
        map_entry(mk, T0 + 3, "A", "open"),
    ]
    events = of_kind(derive(entries), "map_reload")
    assert [(e.name, e.detail["items"]) for e in events] == [("B", 0), ("A", 0)]


def test_map_open_at_eof_and_restarted_reload() -> None:
    mk = Maker()
    entries = [map_entry(mk, T0, "A", "closed"), map_entry(mk, T0 + 60_000, "A", "closed")]
    events = of_kind(derive(entries), "map_reload")
    assert [e.status for e in events] == [STATUS_TRUNCATED, STATUS_OPEN]


def test_map_fallback_on_msg_without_template() -> None:
    mk = Maker()
    entries = [mk(T0, '"X" map closed.'), mk(T0 + 1, '"X" map is now open.')]
    (ev,) = of_kind(derive(entries), "map_reload")
    assert ev.name == "X"


# --------------------------------------------------------------------------- service
def test_service_events() -> None:
    mk = Maker()
    entries = [
        mk(
            T0,
            "The X service on H1 changed from STANDBY to HOT.",
            code="SVC-001",
            fields={"service": "X", "host": "H1", "from_state": "STANDBY", "to_state": "HOT"},
        ),
        mk(
            T0 + 1,
            "Non-Critical X process 'Example' dead on H1. Restarting process.",
            code="SVC-005",
            fields={"crit": "Non-Critical", "service": "X", "process": "Example", "host": "H1"},
        ),
        mk(
            T0 + 2,
            "RestartProcess: Process Example (pid 11) ...",
            code="SVC-006",
            fields={"process": "Example", "proc_pid": 11, "service": "X"},
        ),
        mk(T0 + 3, "Starting service X", code="SVC-010", fields={"service": "X"}),
        mk(T0 + 4, "Server Starting", code="SVC-010"),
        mk(T0 + 5, "Neither X service is hot, H1 will assert", code="SVC-004", fields={"assert_host": "H1"}),
        mk(None, "Shutting down service X.", code="SVC-012", fields={"service": "X"}),
        mk(T0 + 6, "Health Monitor has received warning ...", code="SVC-007"),
    ]
    events = derive(entries, "SERVICE")
    state, dead, restart, start, bare_start, arb = (
        events[0],
        events[1],
        events[2],
        events[3],
        events[4],
        events[5],
    )
    assert len(events) == 6  # the None-ts entry and SVC-007 are not events
    assert (state.kind, state.name) == ("service_state", "X")
    assert state.detail["from_state"] == "STANDBY" and state.detail["to_state"] == "HOT"
    assert (dead.kind, dead.name, dead.detail["critical"]) == ("restart", "Example", False)
    assert (restart.kind, restart.detail["pid"], restart.detail["change"]) == (
        "restart",
        11,
        "process_restarting",
    )
    assert start.detail["change"] == "start" and bare_start.name is None
    assert arb.detail["change"] == "assert" and arb.detail["host"] == "H1"
    assert all(e.start_entry_id == e.end_entry_id and e.duration_ms == 0 for e in events)


# --------------------------------------------------------------------------- robustness
def test_never_raises_on_odd_input() -> None:
    mk = Maker()
    entries = [
        other(mk, None),
        other(mk, T0 + 5 * HOUR),
        other(mk, T0),
        mk(None, "", code="GEN-DBG-01"),
        mk(T0, "", code="GEN-MAP-01", fields={"map": "A"}),
        mk(T0, "", code="SVC-001"),
        level_change(mk, T0, ("A", 1), ("B", 2)),
        mk(None, '"X" map closed.'),
    ]
    derive(entries, "AAVL")


def test_deterministic() -> None:
    mk = Maker()
    entries = [
        cfg(mk, T0),
        hot(mk, T0 + 1),
        level_change(mk, T0 + 2, ("Info", 5), ("Verbose", 6)),
        other(mk, T0 + 3 * HOUR),
    ]
    assert derive(entries, "AAVL") == derive(entries, "AAVL")


# --------------------------------------------------------------------------- re-derivation contract
def sample_stream() -> list[StoredEntry]:
    mk = Maker()
    return [
        cfg(mk, T0),
        other(mk, T0 + 1_000),
        hot(mk, T0 + 2_000),
        other(mk, T0 + 3_000),
        level_change(mk, T0 + 4_000, ("DebugLog", 3), ("Info", 5)),
        other(mk, T0 + 6 * MIN),
        map_entry(mk, T0 + 7 * MIN, "A", "closed"),
        map_entry(mk, T0 + 7 * MIN + 10, "A", "content", "\t1 - x\n\t2 - y"),
        map_entry(mk, T0 + 7 * MIN + 20, "A", "open"),
        other(mk, T0 + 9 * MIN),
        other(mk, T0 + 9 * MIN + HOUR),  # gap
        other(mk, T0 + 9 * MIN + 100),  # clock back
        level_change(mk, T0 + 9 * MIN + 200, ("Info", 5), ("Verbose", 6)),
        other(mk, T0 + 9 * MIN + 300),
        map_entry(mk, T0 + 9 * MIN + 400, "B", "closed"),
        other(mk, T0 + 9 * MIN + 500),
    ]


def key(e: Event) -> tuple[Any, ...]:
    return (
        e.kind,
        e.name,
        e.ts_start,
        e.ts_end,
        e.start_entry_id,
        e.end_entry_id,
        e.status,
        e.n_entries,
        e.detail,
        e.entry_ids,
    )


@pytest.mark.parametrize("family", ["AAVL", "ASSS", "AWFM"])
def test_rederivation_from_low_watermark_matches_whole_stream(family: str) -> None:
    entries = sample_stream()
    whole = run_in_memory([LifecycleDeriver(family)], entries)
    assert whole, "the sample stream must produce events"
    for k in range(1, len(entries) + 1):
        first = LifecycleDeriver(family)
        events: list[Event] = []
        for e in entries[:k]:
            events.extend(first.feed(e))
        events.extend(first.flush(eof=False))  # emits nothing
        wm = first.low_watermark()
        assert wm is not None and wm <= entries[k - 1].id
        kept = [ev for ev in events if (ev.start_entry_id or 0) < wm]
        second = LifecycleDeriver(family, stream_start=wm == entries[0].id)
        tail = [e for e in entries if e.id >= wm]
        redone = run_in_memory([second], tail)
        # events of the tail pass that start before the watermark were kept from the first pass
        redone = [ev for ev in redone if (ev.start_entry_id or 0) >= wm]
        assert sorted(map(repr, map(key, kept + redone))) == sorted(map(repr, map(key, whole))), (family, k)


def _head_streams() -> dict[str, list[StoredEntry]]:
    out: dict[str, list[StoredEntry]] = {}
    mk = Maker()
    out["orphans_work_change"] = [
        other(mk, None, "orphan"),
        other(mk, None, "orphan 2"),
        other(mk, T0),
        other(mk, T0 + 1_000),
        level_change(mk, T0 + 2_000, ("Info", 5), ("DebugLog", 3)),
        other(mk, None),
        other(mk, T0 + 3_000),
    ]
    mk = Maker()
    out["orphan_then_change"] = [
        other(mk, None, "orphan"),
        level_change(mk, T0, ("Info", 5), ("DebugLog", 3)),
        other(mk, T0 + 1_000),
        level_change(mk, T0 + 2_000, ("DebugLog", 3), ("Info", 5)),
        other(mk, T0 + 3_000),
    ]
    mk = Maker()
    out["change_first"] = [
        level_change(mk, T0, ("Info", 5), ("DebugLog", 3)),
        other(mk, T0 + 1_000),
        level_change(mk, T0 + 2_000, ("DebugLog", 3), ("Info", 5)),
    ]
    mk = Maker()
    out["two_changes_in_a_row"] = [
        other(mk, None, "orphan"),
        other(mk, None, "orphan 2"),
        level_change(mk, T0, ("Info", 5), ("DebugLog", 3)),
        level_change(mk, T0, ("DebugLog", 3), ("Info", 5)),
        other(mk, T0 + 1_000),
    ]
    return out


@pytest.mark.parametrize("family", ["AWFM", "AAVL"])
@pytest.mark.parametrize("name", sorted(_head_streams()))
def test_untimed_head_does_not_hide_the_first_level_window(family: str, name: str) -> None:
    entries = _head_streams()[name]
    whole = run_in_memory([LifecycleDeriver(family)], entries)
    assert any(ev.kind == "level_window" and ev.detail.get("from") is None for ev in whole)
    for k in range(1, len(entries) + 1):
        first = LifecycleDeriver(family)
        events = [ev for e in entries[:k] for ev in first.feed(e)]
        wm = first.low_watermark()
        assert wm is not None and wm <= entries[k - 1].id
        kept = [ev for ev in events if (ev.start_entry_id or 0) < wm]
        second = LifecycleDeriver(family, stream_start=wm == entries[0].id)
        redone = run_in_memory([second], [e for e in entries if e.id >= wm])
        redone = [ev for ev in redone if (ev.start_entry_id or 0) >= wm]
        assert sorted(map(repr, map(key, kept + redone))) == sorted(map(repr, map(key, whole))), (
            family,
            name,
            k,
        )


def test_flush_without_eof_emits_nothing_and_watermark_tracks_open_state() -> None:
    mk = Maker()
    d = LifecycleDeriver("AWFM")
    entries = [cfg(mk, T0), other(mk, T0 + 1)]
    for e in entries:
        list(d.feed(e))
    assert list(d.flush(eof=False)) == []
    assert d.low_watermark() == entries[0].id
    assert LifecycleDeriver("AWFM").low_watermark() is None


# --------------------------------------------------------------------------- possible_clock_jumps
def gap(start: int, dur: int, sid: int = 1) -> Event:
    return Event(
        kind="gap",
        ts_start=start,
        ts_end=start + dur,
        duration_ms=dur,
        start_entry_id=sid * 100,
        end_entry_id=sid * 100 + 1,
    )


def test_possible_clock_jump_requires_every_stream() -> None:
    g1, g2, g3 = gap(T0, HOUR + 5_000), gap(T0 + 20_000, HOUR - 30_000, 2), gap(T0 - 10_000, HOUR + MIN, 3)
    inputs = {1: [g1], 2: [g2], 3: [g3]}
    result = possible_clock_jumps(inputs)
    assert [(e.detail["stream_id"], e.start_entry_id) for e in result] == [(3, 300), (1, 100), (2, 200)]
    assert all(e.detail["possible_clock_jump"] is True and e.detail["streams"] == 3 for e in result)
    assert "possible_clock_jump" not in g1.detail  # inputs are not modified
    assert possible_clock_jumps({1: [g1], 2: [g2], 3: []}) == []


def test_possible_clock_jump_tolerances() -> None:
    assert possible_clock_jumps(
        {1: [gap(T0, HOUR + MIN)], 2: [gap(T0 + MIN, HOUR - MIN, 2)]}
    )  # 60 min +/- 1 min
    assert not possible_clock_jumps({1: [gap(T0, HOUR + MIN + 1)], 2: [gap(T0, HOUR, 2)]})  # too long
    assert not possible_clock_jumps(
        {1: [gap(T0, HOUR)], 2: [gap(T0 + 2 * HOUR, HOUR, 2)]}
    )  # not simultaneous
    assert not possible_clock_jumps({1: [gap(T0, HOUR)]})  # a single stream proves nothing
    assert not possible_clock_jumps({})
    assert possible_clock_jumps({1: [gap(T0, HOUR)], 2: [gap(T0 + 90_000, HOUR, 2)]}, tolerance_ms=2 * MIN)


# ---------------------------------------------------------------- map: repeated closed
def marker(mk: Maker, ts: int, name: str, state: str) -> StoredEntry:
    text = {
        "cleared": "cleared.",
        "integrity": "integrity update started. Map will be cleared and populated with fresh data.",
    }
    return mk(
        ts, f'"{name}" map {text[state]}', code="GEN-MAP-01", fields={"map": name, "state": text[state]}
    )


def test_map_closed_logged_twice_is_one_reload() -> None:
    mk = Maker()
    entries = [
        map_entry(mk, T0, "A", "closed"),
        marker(mk, T0 + 1, "A", "integrity"),
        marker(mk, T0 + 2, "A", "cleared"),
        map_entry(mk, T0 + 10, "A", "closed"),  # repeat a few ms later
        map_entry(mk, T0 + 20, "A", "open"),
        map_entry(mk, T0 + 5_000, "A", "closed"),
        map_entry(mk, T0 + 5_010, "A", "closed"),
        map_entry(mk, T0 + 5_020, "A", "open"),
    ]
    events = of_kind(derive(entries), "map_reload")
    assert [e.status for e in events] == [STATUS_OK, STATUS_OK]
    first = events[0]
    assert (
        first.detail["closed_repeats"] == 1 and first.detail["cleared"] and first.detail["integrity_update"]
    )
    assert first.ts_start == T0 and first.start_entry_id == entries[0].id
    assert first.entry_ids == [e.id for e in entries[:5]]
    assert "cleared" not in events[1].detail


def test_map_closed_far_apart_still_truncates() -> None:
    mk = Maker()
    entries = [
        map_entry(mk, T0, "A", "closed"),
        map_entry(mk, T0 + 60_000, "A", "closed"),
        map_entry(mk, T0 + 60_010, "A", "open"),
    ]
    assert [e.status for e in of_kind(derive(entries), "map_reload")] == [STATUS_TRUNCATED, STATUS_OK]


def test_map_reload_entry_ids_are_capped_and_counted() -> None:
    mk = Maker()
    entries = [map_entry(mk, T0, "A", "closed")]
    entries += [map_entry(mk, T0 + 1 + i % 5, "A", "closed") for i in range(10_000)]
    entries.append(map_entry(mk, T0 + 20, "A", "open"))
    (ev,) = of_kind(derive(entries), "map_reload")
    assert ev.detail["closed_repeats"] == 10_000  # exact
    assert len(ev.entry_ids) == MAX_MAP_ENTRY_IDS
    assert ev.n_entries == 10_002
    assert ev.detail["entry_ids_dropped"] == 10_002 - MAX_MAP_ENTRY_IDS
