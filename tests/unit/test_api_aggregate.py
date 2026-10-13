"""POST /api/entries/aggregate against a real loopback server (synthetic cases only)."""

from __future__ import annotations

from query_helpers import build_case
from test_api import case, client, mf, server, zip_path  # noqa: F401  (fixtures)

from dmslog.knowledge import pack as pack_module
from dmslog.store import aggregate as A
from dmslog.store import query as Q


def post(client, body):  # noqa: F811
    return client.post("/api/entries/aggregate", body)


def test_aggregate_by_template_and_drill_down(client, case):  # noqa: F811
    status, body, _ = post(client, {"case": case, "group_by": ["tpl"], "limit": 5})
    assert status == 200
    assert set(body) == {
        "rows_kind",
        "columns",
        "rows",
        "groups",
        "truncated",
        "total",
        "exact",
        "method",
        "notes",
    }
    assert body["columns"] == [{"id": "tpl", "role": "group"}, {"id": "count", "role": "measure"}]
    count = client.post("/api/entries/count", {"case": case, "filter": None})[1]["count"]
    assert body["total"] == count and body["exact"] is True
    assert len(body["rows"]) <= 5 and body["truncated"] == (body["groups"] > len(body["rows"]))
    for row in body["rows"]:
        assert row["drill"] == {"tpl": row["values"][0]}
        drilled = client.post("/api/entries/count", {"case": case, "filter": row["drill"]})[1]["count"]
        assert drilled == row["values"][1]


def test_aggregate_events_percentiles_and_csv(client, server, monkeypatch):  # noqa: F811
    case_dir = server.api.store.root / "Agg"
    case_dir.mkdir(parents=True)
    build_case(case_dir / "case.sqlite")
    import sqlite3

    conn = sqlite3.connect(case_dir / "case.sqlite")
    with conn:
        conn.executemany(
            "INSERT INTO event(stream_id, kind, name, ts_start, duration_ms, status, sev)"
            " VALUES (1,'operation',?,?,?,?,4)",
            [
                ("SendWorks", 1_000_000 + k * 1000, d, "ok")
                for k, d in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
            ],
        )
    conn.close()
    req = {
        "case": "Agg", "rows": "events", "events": {"name": ["SendWorks"]}, "group_by": ["name"],
        "measures": ["count", "p50:duration", "p90:duration", "max:duration"],
    }  # fmt: skip
    status, body, _ = post(client, req)
    assert status == 200 and body["rows_kind"] == "events"
    assert body["rows"] == [
        {"values": ["SendWorks", 11, 50, 90, 100], "labels": [None], "drill": {"and": [{"event_name": ["SendWorks"]}, {"event_name": "SendWorks"}]}}
    ]
    status, _, headers = post(client, {**req, "format": "csv", "bom": True})
    assert status == 200 and headers["content-type"].startswith("text/csv")
    assert headers["x-dmslog-rows"] == "1" and headers["x-dmslog-truncated"] == "0"
    monkeypatch.setattr(pack_module, "current_pack", lambda: None)  # no knowledge pack installed
    status, body, _ = post(client, {"case": "Agg", "group_by": ["key.errcode"], "lookups": ["error.reason"]})
    assert status == 200 and body["notes"] == ["lookups_without_pack"]
    assert body["lookup"]["state"] == "not_installed" and body["lookup"]["columns"] == []
    assert body["lookup"]["message"] and [c["id"] for c in body["columns"]] == ["key.errcode", "count"]


def test_aggregate_csv_body(client, server):  # noqa: F811
    case_dir = server.api.store.root / "Csv"
    case_dir.mkdir(parents=True)
    build_case(case_dir / "case.sqlite")
    import http.client
    import json

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
    conn.request(
        "POST", "/api/entries/aggregate",
        body=json.dumps(
            {"case": "Csv", "group_by": ["sev"], "format": "csv", "order": {"by": "sev", "dir": "asc"}}
        ),
        headers={"Cookie": client.cookie, "Origin": f"http://127.0.0.1:{server.port}"},
    )  # fmt: skip
    text = conn.getresponse().read().decode("utf-8")
    conn.close()
    lines = text.split("\r\n")
    assert lines[0] == "Severity,Count"
    assert [line.split(",")[0] for line in lines[1:-1]] == ["critical", "error", "warning", "info", "noise"]


def test_aggregate_validation(client, case):  # noqa: F811
    cases = [
        ({"group_by": ["msg"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["tpl; DROP TABLE entry"]}, "bad_dimension", "$.group_by[0]"),
        ({"group_by": ["fields.not_in_catalog"]}, "bad_dimension", "$.group_by[0]"),
        ({"measures": ["median"]}, "bad_measure", "$.measures[0]"),
        ({"measures": ["p90:duration"]}, "needs_events", "$.measures[0]"),
        ({"limit": 0}, "bad_limit", "$.limit"),
        ({"rows": "lines"}, "bad_rows", "$.rows"),
        ({"format": "xlsx"}, "bad_format", "$.format"),
        ({"bom": 1}, "bad_bom", "$.bom"),
        ({"filter": [1]}, "bad_type", "$.filter"),
        ({"filter": {"bogus": 1}}, "unknown_operator", "$"),
    ]
    for extra, code, path in cases:
        status, body, _ = post(client, {"case": case, **extra})
        assert status == 400 and body["error"] == code and body["path"] == path, (extra, body)
        assert body["message"]
    status, body, _ = post(client, {"case": case, "filter": {"text": {"substr": "x"}}})
    assert status == 400 and body["error"] == "substr_needs_scope"
    assert post(client, {"case": "nope"})[0] == 404
    assert client.get(f"/api/entries/aggregate?case={case}")[0] == 400  # GET /api/entries/<id>: not an id


def test_aggregate_timeout_is_408_with_the_estimate(client, case, monkeypatch):  # noqa: F811
    def too_slow(*args):
        raise Q.QueryTimeout("simulated")

    monkeypatch.setattr(A, "_run", too_slow)
    status, body, _ = post(client, {"case": case, "group_by": ["level"]})
    assert status == 408 and body["error"] == "aggregate_timeout"
    count = client.post("/api/entries/count", {"case": case, "filter": None})[1]["count"]
    assert body["estimated_rows"] == count
    assert f"{count:,}" in body["message"] and "narrow the filter" in body["message"]
