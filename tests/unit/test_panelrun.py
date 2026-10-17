"""Running panels, the refresh plan and the follow-mode hook (store/panelrun.py, ROADMAP 9.11)."""

from __future__ import annotations

import contextlib
import json
import sqlite3

import pytest
from query_helpers import build_case, open_ro, py_match
from test_aggregate import _fill

from dmslog.store import aggregate as A
from dmslog.store import panelrun as R
from dmslog.store import panels as P

BY_SEV = {"group_by": ["sev"], "measures": ["count"]}
BY_HOUR = {"group_by": ["bucket:1m"], "measures": ["count", "max:fields.n"]}
EVENTS = {"rows": "events", "group_by": ["name"], "measures": ["count", "p90:duration"]}
TOTAL = {"measures": ["count"]}


def widget(wid, query, chart="table", **extra):
    return {"id": wid, "title": wid, "query": query, "chart": chart, "options": {}, **extra}


def panel(pid, *widgets, name=None):
    return {"id": pid, "rev": 1, "name": name or pid, "widgets": list(widgets)}


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    base = tmp_path_factory.mktemp("panelrun")
    rows = build_case(base / "full.sqlite")
    _fill(base / "full.sqlite", rows)
    conn = open_ro(base / "full.sqlite")
    yield conn, rows
    conn.close()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# --- running -------------------------------------------------------------------------------------


def test_every_widget_equals_its_own_aggregation(case):
    conn, _ = case
    p = panel("a" * 12, widget("sev", BY_SEV), widget("hour", BY_HOUR, "line"), widget("ops", EVENTS, "bar"))
    out = R.run_panel(conn, p)
    assert out["panel"] == p["id"] and out["rev"] == 1 and out["complete"] is True
    assert [r["id"] for r in out["results"]] == ["sev", "hour", "ops"]
    for entry, w in zip(out["results"], p["widgets"], strict=True):
        assert entry["status"] == "ok" and entry["error"] is None
        assert entry["result"] == A.as_json(A.aggregate(conn, w["query"]))


def test_results_follow_the_panel_order_not_the_request_order(case):
    conn, _ = case
    p = panel("b" * 12, widget("one", BY_SEV), widget("two", TOTAL), widget("three", BY_HOUR))
    a = R.run_panel(conn, p, widget_ids=["three", "one"])
    b = R.run_panel(conn, p, widget_ids=iter(["one", "three", "one"]))
    assert [r["id"] for r in a["results"]] == ["one", "three"]
    assert [r["id"] for r in b["results"]] == ["one", "three"]
    strip = lambda o: [(r["id"], r["result"]) for r in o["results"]]  # noqa: E731
    assert strip(a) == strip(b)
    assert code_of(lambda: R.run_panel(conn, p, widget_ids=["one", "ghost"])) == "unknown_widget"


def code_of(call):
    with pytest.raises(P.PanelError) as info:
        call()
    return info.value.code


def test_a_failing_widget_does_not_take_the_panel_down(case):
    conn, _ = case
    bad_field = widget("nofield", {"group_by": ["fields.does_not_exist"], "measures": ["count"]})
    boom = widget("boom", dict(TOTAL))
    p = panel("c" * 12, widget("fine", BY_SEV), bad_field, boom, widget("also", TOTAL))

    def run(c, query, **kwargs):
        if query is boom["query"]:
            raise RuntimeError("secret internal text /home/user/x")
        return A.aggregate(c, query, **kwargs)

    out = R.run_panel(conn, p, run=run)
    status = {r["id"]: r["status"] for r in out["results"]}
    assert status == {"fine": "ok", "nofield": "error", "boom": "error", "also": "ok"}
    by = {r["id"]: r for r in out["results"]}
    assert by["nofield"]["error"]["code"] == "unknown_field" and by["nofield"]["result"] is None
    assert by["boom"]["error"]["code"] == "failed"
    assert "secret" not in json.dumps(out) and "does_not_exist" not in json.dumps(out)
    assert out["complete"] is True  # errors are answers; only a budget stop is incomplete


def test_timeouts_are_reported_with_their_row_bound(case):
    conn, _ = case

    def run(c, query, **kwargs):
        raise A.AggregateTimeout(1234)

    out = R.run_panel(conn, panel("d" * 12, widget("slow", BY_SEV)), run=run)
    assert (
        out["results"][0]["error"]["code"] == "timeout"
        and out["results"][0]["error"]["estimated_rows"] == 1234
    )


def test_the_total_budget_is_shared_and_covers_every_phase(case):
    conn, _ = case
    clock = Clock()
    seen = []

    def run(c, query, **kwargs):
        seen.append(kwargs)
        clock.t += 4.0  # each widget burns 4 s of a 10 s budget
        return A.aggregate(c, query, **{k: v for k, v in kwargs.items() if k != "knowledge"})

    p = panel("e" * 12, *[widget(f"w{k}", TOTAL) for k in range(5)])
    out = R.run_panel(conn, p, budget_s=10.0, clock=clock, run=run)
    assert [r["status"] for r in out["results"]] == ["ok", "ok", "ok", "skipped", "skipped"]
    assert out["complete"] is False and out["results"][3]["error"]["code"] == "budget"
    for kw in seen:  # no phase may be given more than the time that was left
        assert kw["timeout_s"] + 3 * kw["estimate_timeout_s"] + 3 * kw["bucket_timeout_s"] <= 10.0


def test_knowledge_is_built_once_and_only_for_lookups(case):
    conn, _ = case
    calls = []
    plain = panel("f" * 12, widget("a", BY_SEV), widget("b", TOTAL))
    R.run_panel(conn, plain, knowledge=lambda: calls.append(1))
    assert calls == []
    lookup = {
        "rows": "events",
        "group_by": ["name"],
        "measures": ["count"],
        "lookups": ["operation.direction"],
    }
    withl = panel("f" * 12, widget("a", lookup), widget("b", lookup))
    out = R.run_panel(conn, withl, knowledge=lambda: calls.append(1))
    assert calls == [1] and all(r["status"] == "ok" for r in out["results"])


# --- refresh plan --------------------------------------------------------------------------------


def oracle(conn, rows, widget_, since, until):
    """Independent: a widget needs a refresh iff a new record is in the filter set (no LIMIT, no rowwise)."""
    tree = widget_["query"].get("filter")
    return any(since < r["id"] <= until and py_match(tree, r) for r in rows)


FILTERS = [
    None,
    {"sev": {"le": 2}},
    {"sev": {"ge": 5}},
    {"tpl": [1]},
    {"and": [{"sev": {"le": 3}}, {"not": {"tpl": [0]}}]},
    {"or": [{"tid": 16}, {"level": {"ge": 6}}]},
    {"family": ["UNKNOWN"]},
]


@pytest.mark.parametrize("since", [0, 7, 100, 200, 250, 10_000])
def test_the_plan_names_exactly_the_widgets_a_new_record_matches(case, since):
    conn, rows = case
    until = max(r["id"] for r in rows)
    ws = [widget(f"w{k}", {**BY_SEV, **({"filter": f} if f else {})}) for k, f in enumerate(FILTERS)]
    plan = R.refresh_plan(conn, [panel("a" * 12, *ws)], since_id=since, until_id=until, events_changed=False)
    named = {w for p in plan["panels"] for w in p["widgets"]}
    assert named == {w["id"] for w in ws if oracle(conn, rows, w, since, until)}
    assert plan["conservative"] == 0
    flat = [x for p in plan["panels"] for x in p["widgets"]]
    assert flat == sorted(flat, key=lambda x: int(x[1:]))  # widget order of the panel


def test_event_widgets_follow_the_events_of_the_cycle(case):
    conn, rows = case
    until = max(r["id"] for r in rows)
    p = panel("a" * 12, widget("ops", EVENTS), widget("rows", BY_SEV))
    both = R.refresh_plan(conn, [p], since_id=0, until_id=until, events_changed=True)
    assert both["panels"] == [{"panel": p["id"], "widgets": ["ops", "rows"]}]
    unknown = R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=None)
    assert unknown["panels"] == [
        {"panel": p["id"], "widgets": ["ops"]}
    ]  # nothing new, but events may have changed
    none = R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=False)
    assert none["panels"] == []
    new_only_rows = R.refresh_plan(conn, [p], since_id=0, until_id=until, events_changed=False)
    assert new_only_rows["panels"][0]["widgets"] == ["ops", "rows"]  # new records may change the events


def test_what_cannot_be_decided_is_refreshed(case, monkeypatch):
    conn, rows = case
    until = max(r["id"] for r in rows)
    ws = [
        widget("a", {**BY_SEV, "filter": {"sev": {"le": 2}}}),
        widget("b", {**BY_SEV, "filter": {"tpl": [1]}}),
    ]
    p = panel("a" * 12, *ws)
    # no connection, probe budget already spent, and a database error all fall back to "refresh"
    plan = R.refresh_plan(conn, [p], since_id=0, until_id=until, events_changed=False, budget_s=0.0)
    assert plan["panels"] == [{"panel": p["id"], "widgets": ["a", "b"]}] and plan["conservative"] == 2

    @contextlib.contextmanager
    def expired(_conn, _timeout):
        raise R.Q.QueryTimeout("probe")
        yield

    monkeypatch.setattr(R.Q, "read_guard", expired)
    plan = R.refresh_plan(conn, [p], since_id=0, until_id=until, events_changed=False)
    assert plan["panels"][0]["widgets"] == ["a", "b"] and plan["conservative"] == 2
    monkeypatch.undo()
    plan = R.refresh_plan(None, [p], since_id=0, until_id=until, events_changed=False)
    assert plan["panels"][0]["widgets"] == ["a", "b"]
    broken = sqlite3.connect(":memory:")
    plan = R.refresh_plan(broken, [p], since_id=0, until_id=until, events_changed=False)
    assert plan["panels"][0]["widgets"] == ["a", "b"] and plan["conservative"] == 2
    broken.close()


def test_equal_filters_are_probed_once(case):
    conn, rows = case
    until = max(r["id"] for r in rows)
    ws = [widget(f"w{k}", {**BY_SEV, "filter": {"sev": {"le": 2}}}) for k in range(6)]
    plan = R.refresh_plan(conn, [panel("a" * 12, *ws)], since_id=0, until_id=until, events_changed=False)
    assert plan["probed"] == 1 and len(plan["panels"][0]["widgets"]) == 6


# --- the follow-mode hook ------------------------------------------------------------------------


def make_watch(tmp_path, interval=5.0):
    clock = Clock()
    P.create(tmp_path, {"name": "P", "widgets": [widget("w", {**BY_SEV, "filter": {"sev": {"le": 2}}})]})
    return R.PanelWatch(clock, interval), clock


def primed(conn, tmp_path, until, case_id="c1", interval=5.0):
    """A watch whose first (always full) cycle for ``case_id`` is over and whose interval has elapsed."""
    watch, clock = make_watch(tmp_path, interval)
    first = watch.process_cycle(conn, tmp_path, case_id, 0, until, events_changed=False)
    assert first["full"] is True and first["throttled"] is False and first["prev_to_id"] is None
    clock.t += interval + 1
    return watch, clock


def test_a_case_without_state_gets_a_full_refresh(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, _ = make_watch(tmp_path)
    body = watch.process_cycle(conn, tmp_path, "c1", until, until, events_changed=False)
    pid = P.list_panels(tmp_path)["panels"][0]["id"]
    assert body["full"] is True and body["refresh"] == [{"panel": pid, "widgets": ["w"]}]


def test_the_event_names_only_what_the_new_records_touch(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, _ = primed(conn, tmp_path, until)
    body = watch.process_cycle(conn, tmp_path, "c1", 0, until, events_changed=False)
    pid = P.list_panels(tmp_path)["panels"][0]["id"]
    assert body["refresh"] == [{"panel": pid, "widgets": ["w"]}] and body["throttled"] is False
    assert body["from_id"] == 0 and body["to_id"] == until and body["full"] is False
    assert body["prev_to_id"] == until  # continuity token: the previous event ended here
    watch.forget_case("c2")
    watch2, _ = primed(conn, tmp_path / "x", until, "c2")  # separate data dir, same shape
    quiet = watch2.process_cycle(conn, tmp_path / "x", "c2", until, until, events_changed=False)
    assert quiet["refresh"] == []  # nothing new above the baseline


def test_frequency_limit_coalesces_into_the_earliest_id(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, clock = primed(conn, tmp_path, until)
    first = watch.process_cycle(conn, tmp_path, "c1", 0, until, events_changed=False)
    assert first["throttled"] is False
    clock.t += 1.0
    second = watch.process_cycle(conn, tmp_path, "c1", 150, until, events_changed=False)
    assert second["throttled"] is True and second["refresh"] == [] and second["from_id"] == 150
    assert 3900 <= second["retry_after_ms"] <= 4000
    clock.t += 1.0
    third = watch.process_cycle(conn, tmp_path, "c1", 180, until, events_changed=True)
    assert third["throttled"] is True and third["from_id"] == 150  # the earliest unreported id survives
    clock.t += 10.0
    fourth = watch.process_cycle(conn, tmp_path, "c1", 190, until, events_changed=False)
    assert fourth["throttled"] is False and fourth["from_id"] == 150 and fourth["refresh"]
    clock.t += 10.0
    fifth = watch.process_cycle(conn, tmp_path, "c1", 195, until, events_changed=False)
    assert fifth["from_id"] == 195  # the pending id was consumed
    other = watch.process_cycle(conn, tmp_path, "c2", 0, until, events_changed=False)
    assert other["throttled"] is False and other["full"] is True  # cases are independent


def test_unknown_baseline_refreshes_everything(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, _ = primed(conn, tmp_path, until)
    body = watch.process_cycle(conn, tmp_path, "c1", None, 10, events_changed=None)
    assert body["full"] is True and body["refresh"][0]["widgets"] == ["w"]


def test_no_panels_or_unusable_file_means_no_event(case, tmp_path):
    conn, _ = case
    watch = R.PanelWatch(Clock())
    assert watch.process_cycle(conn, tmp_path, "c1", 0, 10, events_changed=True) is None
    P.panels_path(tmp_path).write_text("garbage")
    assert watch.process_cycle(conn, tmp_path, "c1", 0, 10, events_changed=True) is None


def test_forgetting_a_case_drops_its_state(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, _ = primed(conn, tmp_path, until)
    watch.forget_case("c1")
    body = watch.process_cycle(conn, tmp_path, "c1", until, until, events_changed=False)
    assert body["throttled"] is False and body["full"] is True


def test_the_state_table_is_bounded_and_eviction_costs_only_a_full_refresh(case, tmp_path, monkeypatch):
    conn, rows = case
    until = max(r["id"] for r in rows)
    monkeypatch.setattr(R, "MAX_THROTTLE_CASES", 10)
    watch, clock = make_watch(tmp_path)
    for k in range(50):  # every case gets a pending (throttled) stretch too
        watch.process_cycle(conn, tmp_path, f"c{k}", 0, until, events_changed=False)
        watch.process_cycle(conn, tmp_path, f"c{k}", 5, until, events_changed=False)
        assert len(watch._cases) <= 10
    clock.t += 100
    body = watch.process_cycle(conn, tmp_path, "c0", until, until, events_changed=False)
    assert body["full"] is True  # c0 was evicted with its pending stretch: everything refreshes


def test_a_failed_cycle_stays_pending_as_a_full_refresh(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, clock = primed(conn, tmp_path, until)
    failed = watch.note_failure("c1", 120)
    assert failed["failed"] is True and failed["full"] is True and failed["refresh"] == []
    clock.t += 10
    after = watch.process_cycle(conn, tmp_path, "c1", until, until, events_changed=False)
    assert after["full"] is True and after["from_id"] == 120 and after["refresh"]
    clock.t += 10
    again = watch.process_cycle(conn, tmp_path, "c1", until, until, events_changed=False)
    assert again["full"] is False  # the pending failure was consumed


def test_a_plan_that_raises_refreshes_everything(case, tmp_path, monkeypatch):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, clock = primed(conn, tmp_path, until)

    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(R, "refresh_plan", boom)
    body = watch.process_cycle(conn, tmp_path, "c1", 0, until, events_changed=False)
    assert body["full"] is True and body["refresh"][0]["widgets"] == ["w"]


def test_changes_since_validates_and_plans(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    P.create(tmp_path, {"name": "P", "widgets": [widget("w", BY_SEV)]})
    out = R.changes_since(conn, tmp_path, 0)
    assert out["to_id"] == until and out["from_id"] == 0 and out["refresh"][0]["widgets"] == ["w"]
    assert R.changes_since(conn, tmp_path, until)["refresh"] == []
    for bad in (-1, True, 1.5, "0", None, 2**63):
        assert code_of(lambda bad=bad: R.changes_since(conn, tmp_path, bad)) == "bad_since"


def test_a_throttled_unknown_baseline_is_not_lost(case, tmp_path):
    conn, rows = case
    until = max(r["id"] for r in rows)
    watch, clock = primed(conn, tmp_path, until)
    assert watch.process_cycle(conn, tmp_path, "c1", 0, until, events_changed=False)["throttled"] is False
    clock.t += 1.0
    held = watch.process_cycle(conn, tmp_path, "c1", None, until, events_changed=False)
    assert held["throttled"] is True and held["full"] is True
    clock.t += 10.0
    later = watch.process_cycle(conn, tmp_path, "c1", until, until, events_changed=False)
    assert later["from_id"] == 0 and later["full"] is True and later["refresh"]


# --- derived data (an operation ends: old records start or stop matching) -------------------------


DERIVED_TREES = [
    {"event_status": ["ok"]},
    {"and": [{"event_status": ["ok"]}, {"tpl": [1]}]},
    {"not": {"event_name": ["x"]}},
    {"or": [{"tpl": [1]}, {"event_kind": ["operation"]}]},
    {"event": [1]},
    {"key": {"type": "msgid"}},
]


@pytest.mark.parametrize("tree", DERIVED_TREES)
def test_widgets_filtering_by_derived_leaves_refresh_when_events_change(case, tree):
    conn, rows = case
    until = max(r["id"] for r in rows)
    w = widget("d", {**BY_SEV, "filter": tree})
    p = panel("a" * 12, w)
    # nothing new at all, but an operation ended in the cycle
    plan = R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=True)
    assert plan["panels"] == [{"panel": p["id"], "widgets": ["d"]}]
    plan = R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=None)
    assert plan["panels"] == [{"panel": p["id"], "widgets": ["d"]}]
    plan = R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=False)
    assert plan["panels"] == []


def test_plain_widgets_do_not_follow_events(case):
    conn, rows = case
    until = max(r["id"] for r in rows)
    p = panel("a" * 12, widget("plain", {**BY_SEV, "filter": {"and": [{"tpl": [1]}, {"sev": {"le": 3}}]}}))
    assert R.refresh_plan(conn, [p], since_id=until, until_id=until, events_changed=True)["panels"] == []


@pytest.mark.parametrize(
    "query",
    [
        {"group_by": ["key.msgid"]},
        {"measures": ["distinct:key.msgid"]},
        {"rows": "events", "measures": ["count"]},
    ],
)
def test_key_and_event_queries_depend_on_derived_data(query):
    assert R.depends_on_derived(query)
    assert not R.depends_on_derived({"group_by": ["sev", "fields.op"], "measures": ["count"]})


# --- the absolute deadline -------------------------------------------------------------------------


def test_the_ceiling_clamps_every_guard_and_nests(case):
    conn, _ = case
    import time

    from dmslog.store import query as Q

    with Q.deadline_ceiling(time.monotonic() - 1):
        with pytest.raises(Q.QueryTimeout), Q.read_guard(conn, 60.0):
            conn.execute(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c"
            )
    with Q.deadline_ceiling(time.monotonic() + 100), Q.deadline_ceiling(None):
        assert Q._CEILING.get() is not None  # a nested None never lifts an outer ceiling
    assert Q._CEILING.get() is None


def test_knowledge_that_eats_the_budget_stops_the_widgets(case):
    conn, _ = case
    clock = Clock()
    ran = []

    def slow_knowledge():
        clock.t += 20.0  # resolving the knowledge took longer than the whole budget
        return object()

    def run(c, query, **kwargs):
        ran.append(query)
        return A.aggregate(c, query)

    lookup = {
        "rows": "events",
        "group_by": ["name"],
        "measures": ["count"],
        "lookups": ["operation.direction"],
    }
    out = R.run_panel(
        conn, panel("a" * 12, widget("l", lookup), widget("p", TOTAL)), knowledge=slow_knowledge,
        budget_s=10.0, clock=clock, run=run,
    )  # fmt: skip
    assert ran == []
    assert [r["status"] for r in out["results"]] == ["skipped", "skipped"] and out["complete"] is False


def test_every_phase_of_a_widget_runs_under_the_panel_ceiling(case, monkeypatch):
    conn, _ = case
    import time

    from dmslog.store import query as Q

    seen = []

    def spy(c, query, **kwargs):
        seen.append(Q._CEILING.get())
        return A.aggregate(c, query)

    start = time.monotonic()
    R.run_panel(conn, panel("a" * 12, widget("a", TOTAL)), budget_s=0.5, run=spy)
    assert seen and start < seen[0] <= start + 0.6
