"""GET /api/events/missing[/summary] and POST /api/entries/repeats (ROADMAP 9.12) over a loopback server."""

from __future__ import annotations

import pytest
import test_repeats as tr
import test_sequences as ts
from test_api import client, mf, server  # noqa: F401  (fixtures)


@pytest.fixture
def seq_case(server):  # noqa: F811
    case_dir = server.api.store.root / "Seq"
    case_dir.mkdir(parents=True)
    ts._case(case_dir).close()
    return "Seq"


@pytest.fixture
def rep_case(server):  # noqa: F811
    case_dir = server.api.store.root / "Rep"
    case_dir.mkdir(parents=True)
    rows = [
        (3, 1000, 1, {"op": "A"}, "m", None, 7),  # 1
        (3, 1500, 1, {"op": "A"}, "m", None, 9),  # 2 repeats 1
        (3, 1600, 1, {"op": "B"}, "m", None, 7),  # 3
        (3, 4000, 1, {"op": "A"}, "m", None, 7),  # 4 too far
    ]
    tr._case(case_dir, rows).close()
    return "Rep"


def _bad(client, path, error="invalid_field"):  # noqa: F811
    status, body, _ = client.get(path)
    assert status == 400 and body["error"] == error, (path, status, body)


def test_missing_steps_pages_and_filters(client, seq_case):  # noqa: F811
    expected = [e[0] for e in ts.EVENTS if e[9] is not None]
    seen, after = [], ""
    while True:
        status, body, _ = client.get(f"/api/events/missing?case=seq&limit=4{after}")
        assert status == 200
        seen.extend(i["event"]["id"] for i in body["items"])
        if body["next"] is None:
            break
        after = f"&after={body['next']}"
    assert seen == expected
    status, body, _ = client.get("/api/events/missing?case=Seq&kind=startup&stream=2")
    assert [i["event"]["id"] for i in body["items"]] == [10, 19, 20]
    item = body["items"][0]
    assert item["missing"] == ["hot"] and item["cause"] == "end_of_log" and item["proven"] is False
    status, body, _ = client.get("/api/events/missing?case=Seq&kind=gap")
    assert status == 200 and body == {"items": [], "next": None}


def test_missing_steps_invalid_input(client, seq_case):  # noqa: F811
    _bad(client, "/api/events/missing?case=Seq&limit=0")
    _bad(client, "/api/events/missing?case=Seq&limit=1001")
    _bad(client, "/api/events/missing?case=Seq&limit=x")
    _bad(client, "/api/events/missing?case=Seq&after=nope")
    _bad(client, "/api/events/missing?case=Seq&kind=Bad-Kind")
    _bad(client, "/api/events/missing/summary?case=Seq&stream=-1")
    assert client.get("/api/events/missing?case=nope")[0] == 404
    assert client.get("/api/events/missing/summary?case=nope")[0] == 404


def test_missing_summary(client, seq_case):  # noqa: F811
    status, body, _ = client.get("/api/events/missing/summary?case=Seq")
    assert status == 200 and body["total"] == len([e for e in ts.EVENTS if e[9] is not None])
    groups = {(g["kind"], g["name"]): g for g in body["groups"]}
    assert groups[("operation", "SendWorks")]["causes"] == {"capture_cut": 1, "end_of_log": 2}
    status, body, _ = client.get("/api/events/missing/summary?case=Seq&kind=map_reload")
    assert [(g["kind"], g["n"]) for g in body["groups"]] == [("map_reload", 2)]


def test_the_event_id_route_is_not_shadowed_and_does_not_shadow(client, seq_case):  # noqa: F811
    status, body, _ = client.get("/api/events/1?case=Seq")
    assert status == 200 and body["event"]["id"] == 1
    status, body, _ = client.get("/api/events/missing?case=Seq")
    assert status == 200 and "items" in body  # not handled as the event id "missing"
    _bad(client, "/api/events/missingx?case=Seq")  # an invalid event id, not the missing route


def test_repeat_marks(client, rep_case):  # noqa: F811
    status, body, _ = client.post("/api/entries/repeats", {"case": "rep", "ids": [1, 2, 3, 4, 99]})
    assert status == 200 and body == {"marks": {"2": {"prev": 1, "dt_ms": 500, "earlier": 1}}}
    status, body, _ = client.post(
        "/api/entries/repeats", {"case": "Rep", "ids": [2, 4], "repeat": {"tid": True, "window_ms": 3000}}
    )
    assert body == {"marks": {"4": {"prev": 1, "dt_ms": 3000, "earlier": 1}}}
    status, body, _ = client.post("/api/entries/repeats", {"case": "Rep", "ids": []})
    assert status == 200 and body == {"marks": {}}


@pytest.mark.parametrize("ids", [None, "1", [1.5], [True], [-1], [2**63], ["1"], list(range(1001))], ids=repr)
def test_repeat_marks_invalid_ids(client, rep_case, ids):  # noqa: F811
    payload = {"case": "Rep"} if ids is None else {"case": "Rep", "ids": ids}
    status, body, _ = client.post("/api/entries/repeats", payload)
    assert status == 400 and body["error"] == "invalid_field"


def test_repeat_marks_invalid_spec_and_case(client, rep_case):  # noqa: F811
    for spec, error, path in (
        ({"window_ms": -1}, "bad_value", "$.repeat.window_ms"),
        ({"fields": []}, "empty_list", "$.repeat.fields"),
        ({"bogus": 1}, "unknown_operator", "$.repeat"),
        ([1], "bad_type", "$.repeat"),
    ):
        status, body, _ = client.post("/api/entries/repeats", {"case": "Rep", "ids": [1], "repeat": spec})
        assert (status, body["error"], body["path"]) == (400, error, path)
    status, _, _ = client.post("/api/entries/repeats", {"case": "nope", "ids": [1]})
    assert status == 404
    # the filter leaf goes through the grid routes like any other (no scope required)
    status, body, _ = client.post(
        "/api/entries/distinct", {"case": "Rep", "field": "tpl", "filter": {"repeat": {}}}
    )
    assert status == 200 and body["total"] == 1


@pytest.mark.parametrize(
    "after",
    ["9223372036854775808:1", "1000:9223372036854775808", "-9223372036854775809:1", "1:-9223372036854775809"],
)
def test_cursors_outside_int64_are_rejected(client, seq_case, after):  # noqa: F811
    _bad(client, f"/api/events/missing?case=Seq&after={after}")
    _bad(client, f"/api/events?case=Seq&after={after}")


def test_cursor_at_the_int64_limits_is_accepted(client, seq_case):  # noqa: F811
    for after in ("9223372036854775807:9223372036854775807", "-9223372036854775808:-9223372036854775808"):
        assert client.get(f"/api/events/missing?case=Seq&after={after}")[0] == 200
        assert client.get(f"/api/events?case=Seq&after={after}")[0] == 200
