"""POST /api/entries/compare against a real loopback server (synthetic cases only)."""

from __future__ import annotations

from compare_helpers import Content, Record, build
from test_api import client, mf, server  # noqa: F401  (fixtures)


def post(client, body):  # noqa: F811
    return client.post("/api/entries/compare", body)


def _case(server, name="Cmp"):  # noqa: F811
    case_dir = server.api.store.root / name
    case_dir.mkdir(parents=True)
    recs = [
        Record("wfm1", k, 1_000_000 + k, f"CMP-00{c}", "m") for k, c in enumerate([1, 2, 3, 4, 1, 3, 4, 5], 1)
    ]
    recs.append(Record("wfm1", 9, 1_000_009, None, "odd 5 thing"))
    events = {"op1": [("wfm1", k) for k in range(1, 5)], "op2": [("wfm1", k) for k in range(5, 9)]}
    return build(case_dir / "case.sqlite", Content(recs, events))


def test_compare_two_operations(client, server):  # noqa: F811
    built = _case(server)
    status, body, _ = post(
        client,
        {
            "case": "Cmp",
            "mode": "operation",
            "a": {"event": built.event["op1"]},
            "b": {"event": built.event["op2"]},
            "limit": 2,
        },
    )
    assert status == 200
    assert set(body) == {
        "mode",
        "a",
        "b",
        "method",
        "optimal",
        "work",
        "stats",
        "runs",
        "runs_total",
        "templates",
        "templates_total",
        "rows",
        "offset",
        "limit",
        "total_rows",
        "next_offset",
        "digest",
        "notes",
    }
    assert body["stats"]["same"] == 3 and body["optimal"] is True and body["total_rows"] == 5
    assert len(body["rows"]) == 2 and body["next_offset"] == 2
    assert body["rows"][0]["a"]["msg"] == "m" and body["rows"][0]["a"]["tpl"] == "CMP-001"
    status, page, _ = post(
        client,
        {
            "case": "Cmp",
            "mode": "operation",
            "a": {"event": built.event["op1"]},
            "b": {"event": built.event["op2"]},
            "offset": 2,
            "limit": 10,
        },
    )
    assert status == 200 and page["digest"] == body["digest"] and len(page["rows"]) == 3


def test_compare_windows_and_errors(client, server):  # noqa: F811
    _case(server)
    ok = {
        "case": "Cmp",
        "mode": "window",
        "a": {"from": 1_000_001, "to": 1_000_005},
        "b": {"from": 1_000_005, "to": 1_000_010},
    }
    status, body, _ = post(client, ok)
    assert status == 200 and body["a"]["count"] == 4 and body["b"]["count"] == 5
    assert any(t["unknown"] for t in body["templates"])
    cases = [
        ({**ok, "mode": "nope"}, 400, "bad_mode", "$.mode"),
        ({**ok, "a": {"from": 3, "to": 3}}, 400, "bad_window", "$.a.to"),
        ({**ok, "max_side": 20_001}, 400, "bad_max_side", "$.max_side"),
        ({**ok, "filter": {"bogus": 1}}, 400, "unknown_operator", "$.filter"),
        (
            {**ok, "mode": "operation", "a": {"event": 1}, "b": {"event": 404}},
            404,
            "event_not_found",
            "$.b.event",
        ),
        (
            {**ok, "mode": "process", "a": {"stream": 77}, "b": {"stream": 1}},
            404,
            "stream_not_found",
            "$.a.stream",
        ),
    ]
    for req, code, error, path in cases:
        status, body, _ = post(client, req)
        assert (status, body["error"], body.get("path")) == (code, error, path), body
        assert body["message"]
    status, body, _ = post(client, {**ok, "case": "missing"})
    assert status == 404
    status, body, _ = post(
        client, {**ok, "mode": "filter", "a": {"filter": {"text": {"substr": "m"}}}, "b": {"filter": None}}
    )
    assert status == 400 and body["error"] == "substr_needs_scope"
