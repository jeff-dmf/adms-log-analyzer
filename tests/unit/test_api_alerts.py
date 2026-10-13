"""Route tests of /api/alerts/* and the alert hook of the follow mode (watch cycles publish alerts)."""

import time

import test_api
import test_pipeline as tp
import test_watch as tw

from dmslog.server.api import Api
from dmslog.store import alerts

server, client, zip_path, case = (
    test_api.server,
    test_api.client,
    test_api.zip_path,
    test_api.case,
)  # fixtures

ERRORS = {"sev": {"le": 2}}
RULE = {"name": "Errors", "kind": "match", "filter": ERRORS, "severity": "critical", "min_interval_s": 0}


def test_rules_lifecycle(client, case):
    status, made, _ = client.post("/api/alerts/rules", RULE)
    assert status == 201 and made["name"] == "Errors" and made["enabled"] is True
    status, listing, _ = client.get(f"/api/alerts/rules?case={case}")
    assert status == 200 and listing["rules"][0]["available"] is True and listing["invalid"] is False
    off = {**RULE, "enabled": False}
    status, updated, _ = client.request(
        "PUT", "/api/alerts/rules", {"match": {"name": "errors"}, "rule": off}
    )
    assert status == 200 and updated["enabled"] is False
    status, listing, _ = client.get("/api/alerts/rules")
    assert status == 200 and "available" not in listing["rules"][0]
    assert client.request("DELETE", "/api/alerts/rules?name=Errors")[0] == 204
    status, err, _ = client.request("DELETE", "/api/alerts/rules?name=Errors")
    assert status == 404 and err["error"] == "not_found"


def test_rule_errors(client, case):
    status, err, _ = client.post("/api/alerts/rules", {"name": "", "kind": "match"})
    assert status == 400 and err["error"] == "bad_name"
    status, err, _ = client.post("/api/alerts/rules", {**RULE, "filter": {"source": 1}})
    assert status == 400 and err["error"] == "case_specific"
    assert client.post("/api/alerts/rules", RULE)[0] == 201
    status, err, _ = client.post("/api/alerts/rules", RULE)
    assert status == 409 and err["error"] == "duplicate_name"
    assert client.request("DELETE", "/api/alerts/rules")[0] == 400
    assert client.request("PUT", "/api/alerts/rules", {"rule": {}})[0] == 400
    assert client.get("/api/alerts/rules?case=nope")[0] == 404
    assert client.get("/api/alerts/history")[0] in (400, 404)
    assert client.request("PATCH", "/api/alerts/rules")[0] == 405


def test_an_unavailable_timeout_is_reported_per_case(client, case):
    body = {"name": "Slow", "kind": "open_operation", "timeout_param": "SendTimeout"}
    assert client.post("/api/alerts/rules", body)[0] == 201
    _, listing, _ = client.get(f"/api/alerts/rules?case={case}")
    (rule,) = listing["rules"]
    assert rule["available"] is False and "timeout" in rule["unavailable_message"]


def test_history_endpoints(client, case):
    status, body, _ = client.get(f"/api/alerts/history?case={case}")
    assert status == 200 and body == {"alerts": []}
    assert client.request("DELETE", f"/api/alerts/history?case={case}")[0] == 204


def wait_alert(hub, after=0, timeout=20):
    deadline = time.monotonic() + timeout
    seq = after
    while time.monotonic() < deadline:
        events, seq = hub.wait(seq, 0.2)
        for event in events:
            if event.name == "alert":
                return event, seq
    raise AssertionError("no alert event")


def test_a_follow_cycle_publishes_an_alert_for_the_new_records(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    data = tmp_path / "data"
    alerts.create(data, RULE)
    api = Api(data)
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        first, seq = wait_alert(hub)
        assert first.data["rule"] == "Errors" and first.data["count"] > 0 and first.data["case"] == case
        assert set(first.data["sample_ids"]) and "msg" not in first.data
        old_max = max(first.data["sample_ids"])
        with open(root / tw.WFM, "ab") as handle:
            handle.write(tp.make_log(10, start=900_000))
        second, _ = wait_alert(hub, seq)
        assert min(second.data["sample_ids"]) > old_max  # only the records of the new cycle
        assert len(api.jobs.alerts.history(case)) >= 2
    finally:
        api.shutdown()


def test_no_rules_means_no_alert_events(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    api = Api(tmp_path / "data")
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        tw.wait_for(hub, "watch")
        events, _ = hub.wait(0, 0.3)
        assert all(e.name != "alert" for e in events)
        assert api.jobs.alerts.history(case) == []
    finally:
        api.shutdown()


def test_the_case_stays_busy_until_the_alerts_of_a_cycle_are_out(tmp_path, monkeypatch):
    """The job is released only after evaluation and publication: a delete cannot overlap the evaluation."""
    import threading

    root = tp.write_folder(tmp_path / "Log", {tw.WFM: tp.make_log(20)})
    data = tmp_path / "data"
    alerts.create(data, RULE)
    api = Api(data)
    inside, release = threading.Event(), threading.Event()
    original = alerts.AlertState.process_cycle
    seen = {}

    def slow(self, conn, data_dir, case_id, baseline):
        seen["running"] = api.jobs.hub(case_id).running
        inside.set()
        release.wait(10)
        return original(self, conn, data_dir, case_id, baseline)

    monkeypatch.setattr(alerts.AlertState, "process_cycle", slow)
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        assert inside.wait(20)
        assert seen["running"] is True and api.jobs.is_running(case)  # delete_case answers 409 here
        release.set()
        wait_alert(api.jobs.hub(case))
        deadline = time.monotonic() + 10
        while api.jobs.is_running(case) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not api.jobs.is_running(case)
    finally:
        release.set()
        api.shutdown()
