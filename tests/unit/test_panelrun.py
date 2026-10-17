"""Running panels, the refresh plan and the follow-mode hook (store/panelrun.py, ROADMAP 9.11)."""

from __future__ import annotations

import json

import pytest
from query_helpers import build_case, open_ro
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


# --- the follow-mode notice ---------------------------------------------------------------------


def make_watch(tmp_path, interval=5.0):
    clock = Clock()
    P.create(tmp_path, {"name": "P", "widgets": [widget("w", BY_SEV)]})
    return R.PanelWatch(clock, interval), clock


def test_the_notice_only_says_that_something_may_have_changed(tmp_path):
    watch, _ = make_watch(tmp_path)
    body = watch.notice(tmp_path, "c1", 245)
    assert set(body) == {"seq", "to_id", "throttled", "retry_after_ms", "min_interval_ms"}
    assert body["to_id"] == 245 and body["throttled"] is False and body["retry_after_ms"] == 0
    assert body["min_interval_ms"] == 5000


def test_seq_always_advances_even_when_nothing_else_changed(tmp_path):
    watch, clock = make_watch(tmp_path)
    seqs = []
    for k in range(6):
        clock.t += 0.0 if k % 2 else 10.0
        seqs.append(watch.notice(tmp_path, "c1", 7)["seq"])  # same to_id every time
    assert seqs == sorted(set(seqs))


def test_frequency_limit_throttles_and_reports_the_wait(tmp_path):
    watch, clock = make_watch(tmp_path)
    assert watch.notice(tmp_path, "c1", 1)["throttled"] is False
    clock.t += 1.0
    held = watch.notice(tmp_path, "c1", 2)
    assert held["throttled"] is True and 3900 <= held["retry_after_ms"] <= 4000
    assert watch.notice(tmp_path, "c2", 2)["throttled"] is False  # cases are independent
    clock.t += 4.0
    assert watch.notice(tmp_path, "c1", 3)["throttled"] is False  # a throttled notice did not restart it


def test_no_panels_or_unusable_file_means_no_notice(tmp_path):
    watch = R.PanelWatch(Clock())
    assert watch.notice(tmp_path, "c1", 1) is None
    P.panels_path(tmp_path).write_text("garbage")
    assert watch.notice(tmp_path, "c1", 1) is None


def test_forgetting_a_case_lets_its_next_notice_through(tmp_path):
    watch, _ = make_watch(tmp_path)
    watch.notice(tmp_path, "c1", 1)
    assert watch.notice(tmp_path, "c1", 1)["throttled"] is True
    watch.forget_case("c1")
    assert watch.notice(tmp_path, "c1", 1)["throttled"] is False


def test_the_state_table_is_bounded_and_seq_survives_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "MAX_THROTTLE_CASES", 10)
    watch, _ = make_watch(tmp_path)
    last = {}
    for k in range(60):
        case = f"c{k % 25}"
        seq = watch.notice(tmp_path, case, k)["seq"]
        assert seq > last.get(case, 0)
        last[case] = seq
        assert len(watch._last) <= 10


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
