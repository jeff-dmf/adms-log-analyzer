"""Route tests of /api/panels/* and the panel hook of the follow mode (watch cycles publish ``panels``)."""

import json

import test_api
import test_pipeline as tp
import test_watch as tw

from dmslog.server.api import Api
from dmslog.store import panelrun, panels

server, client, zip_path, case = (
    test_api.server,
    test_api.client,
    test_api.zip_path,
    test_api.case,
)  # fixtures

QUERY = {"group_by": ["sev"], "measures": ["count"]}
ERRORS = {"group_by": ["bucket:1m"], "measures": ["count"], "filter": {"sev": {"le": 2}}}
PANEL = {
    "name": "Overview",
    "widgets": [
        {"id": "sev", "title": "By severity", "query": QUERY, "chart": "bar"},
        {"id": "errors", "title": "Errors per minute", "query": ERRORS, "chart": "line"},
    ],
}


def test_crud(client):
    status, listing, _ = client.get("/api/panels")
    assert status == 200 and listing["panels"] == [] and listing["invalid"] is False
    status, made, _ = client.post("/api/panels", PANEL)
    assert status == 201 and made["rev"] == 1 and [w["id"] for w in made["widgets"]] == ["sev", "errors"]
    body = {"id": made["id"], "panel": {**PANEL, "name": "Renamed"}, "if_rev": 1}
    status, updated, _ = client.request("PUT", "/api/panels", body)
    assert status == 200 and updated["rev"] == 2 and updated["name"] == "Renamed"
    status, err, _ = client.request("PUT", "/api/panels", body)  # stale revision
    assert status == 409 and err["error"] == "rev_conflict"
    assert client.get("/api/panels")[1]["panels"] == [updated]
    assert client.request("DELETE", f"/api/panels?id={made['id']}")[0] == 204
    status, err, _ = client.request("DELETE", f"/api/panels?id={made['id']}")
    assert status == 404 and err["error"] == "not_found"


def test_errors(client):
    status, err, _ = client.post("/api/panels", {"name": "", "widgets": []})
    assert status == 400 and err["error"] == "bad_name"
    bad = {**PANEL, "widgets": [{**PANEL["widgets"][0], "query": {"group_by": ["nope<>"]}}]}
    status, err, _ = client.post("/api/panels", bad)
    assert status == 400 and err["error"] == "bad_query" and "nope<>" not in err["message"]
    case_bound = {
        **PANEL,
        "widgets": [{**PANEL["widgets"][0], "query": {**QUERY, "filter": {"source": [1]}}}],
    }
    assert client.post("/api/panels", case_bound)[1]["error"] == "case_specific"
    assert client.post("/api/panels", PANEL)[0] == 201
    assert client.post("/api/panels", PANEL)[0] == 409
    assert client.request("DELETE", "/api/panels")[1]["error"] == "bad_id"
    assert client.request("PUT", "/api/panels", {"id": "0" * 12})[1]["error"] == "bad_panel"
    assert client.request("PATCH", "/api/panels")[0] == 405


def test_run_equals_the_plain_aggregation_and_isolates_failures(client, case):
    made = client.post("/api/panels", PANEL)[1]
    broken = {
        "name": "Broken",
        "widgets": [
            {"id": "ok", "title": "", "query": QUERY, "chart": "table"},
            {"id": "gone", "title": "", "query": {"group_by": ["fields.no_such_field"]}, "chart": "table"},
        ],
    }
    other = client.post("/api/panels", broken)[1]
    status, ran, _ = client.post("/api/panels/run", {"case": case, "id": made["id"]})
    assert status == 200 and ran["complete"] is True and ran["panel"] == made["id"]
    for entry, query in zip(ran["results"], (QUERY, ERRORS), strict=True):
        assert entry["status"] == "ok"
        want = client.post("/api/entries/aggregate", {"case": case, **query})[1]
        assert entry["result"] == want
    status, ran, _ = client.post(
        "/api/panels/run", {"case": case, "id": other["id"], "widgets": ["gone", "ok"]}
    )
    assert status == 200
    assert [(r["id"], r["status"]) for r in ran["results"]] == [("ok", "ok"), ("gone", "error")]
    assert ran["results"][1]["error"]["code"] == "unknown_field"
    assert "no_such_field" not in json.dumps(ran)


def test_run_errors(client, case):
    made = client.post("/api/panels", PANEL)[1]
    assert client.post("/api/panels/run", {"case": "nope", "id": made["id"]})[0] == 404
    assert client.post("/api/panels/run", {"case": case, "id": "0" * 12})[1]["error"] == "not_found"
    assert client.post("/api/panels/run", {"case": case})[1]["error"] == "bad_id"
    body = {"case": case, "id": made["id"], "widgets": ["ghost"]}
    assert client.post("/api/panels/run", body)[1]["error"] == "unknown_widget"
    for bad in ("sev", [1], ["x"] * 17):
        status, err, _ = client.post("/api/panels/run", {**body, "widgets": bad})
        assert status == 400


def wait_event(hub, name, after=0):
    return tw.wait_for(hub, name, after)


def test_a_follow_cycle_publishes_a_notice_with_a_growing_seq(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    data = tmp_path / "data"
    panels.create(data, PANEL)
    api = Api(data)
    api.jobs.panel_watch = panelrun.PanelWatch(min_interval_s=0)
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        first, seq = wait_event(hub, "panels")
        assert first.data["throttled"] is False and first.data["to_id"] > 0 and first.data["cycle"] >= 1
        assert set(first.data) == {"cycle", "seq", "to_id", "throttled", "retry_after_ms", "min_interval_ms"}
        with open(root / tw.WFM, "ab") as handle:
            handle.write(tp.make_log(10, start=900_000))
        second, _ = wait_event(hub, "panels", seq)
        assert second.data["seq"] > first.data["seq"] and second.data["to_id"] > first.data["to_id"]
        assert "Overview" not in json.dumps(second.data)  # no panel names, no log text
    finally:
        api.shutdown()


def test_the_default_frequency_limit_marks_the_second_notice_throttled(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    data = tmp_path / "data"
    panels.create(data, PANEL)
    api = Api(data)
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        first, seq = wait_event(hub, "panels")
        assert first.data["throttled"] is False
        with open(root / tw.WFM, "ab") as handle:
            handle.write(tp.make_log(10, start=900_000))
        second, _ = wait_event(hub, "panels", seq)
        assert second.data["throttled"] is True and 0 < second.data["retry_after_ms"] <= 5000
        assert second.data["seq"] > first.data["seq"]
    finally:
        api.shutdown()


def test_a_failure_while_building_the_notice_never_stops_the_follow_mode(tmp_path, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    data = tmp_path / "data"
    panels.create(data, PANEL)
    api = Api(data)

    def boom(self, *args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(panelrun.PanelWatch, "notice", boom)
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        wait_event(hub, "watch")
        events, _ = hub.wait(0, 0.3)
        assert all(e.name != "panels" for e in events)
    finally:
        api.shutdown()


def test_no_panels_means_no_panels_events(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    api = Api(tmp_path / "data")
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        tw.wait_for(hub, "watch")
        events, _ = hub.wait(0, 0.3)
        assert all(e.name != "panels" for e in events)
    finally:
        api.shutdown()
