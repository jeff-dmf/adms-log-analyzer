"""GET /api/events*, /api/stats/* against a real loopback server and a synthetic case."""

from __future__ import annotations

import pytest
import test_store_events as tse
from test_api import client, mf, server  # noqa: F401  (fixtures)

from dmslog.store import create_case_db

T0 = tse.T0


@pytest.fixture
def demo(server):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    tse.seed(conn)
    tse.seed_stats(conn)
    op = conn.execute("SELECT id FROM event WHERE key = 'K003' AND kind = 'operation'").fetchone()[0]
    tse.seed_entries(conn, op, 3)
    conn.close()
    return "Demo"


def invalid(client, path):  # noqa: F811
    status, body, _ = client.get(path)
    assert status == 400 and body["error"] == "invalid_field", (path, status, body)


def test_events_page_all_and_canonical_case(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/events?case=demo&limit=1000")
    assert status == 200 and body["next"] is None and len(body["events"]) == 27
    assert {"id", "family", "detail", "duration_ms"} <= set(body["events"][0])


def test_events_keyset_pagination_over_http(client, demo):  # noqa: F811
    full = client.get("/api/events?case=Demo&limit=1000")[1]["events"]
    seen, after = [], ""
    while True:
        status, body, _ = client.get(f"/api/events?case=Demo&limit=4&after={after}")
        assert status == 200
        seen += [e["id"] for e in body["events"]]
        if body["next"] is None:
            break
        after = body["next"]
    assert seen == [e["id"] for e in full] and len(set(seen)) == len(seen)


def test_events_filters(client, demo):  # noqa: F811
    def get(qs):
        status, body, _ = client.get(f"/api/events?case=Demo&limit=1000&{qs}")
        assert status == 200
        return body["events"]

    assert len(get("kind=exception,retry_seq")) == 2
    assert len(get("kind=operation&name=GetB")) == 3
    assert len(get("name=GetB,Boom")) == 4
    assert {e["status"] for e in get("status=fail,open")} == {"fail", "open"}
    assert len(get("stream=2")) == 3
    assert len(get("q=K003")) == 3 and len(get("q=K00")) == 12
    assert len(get("from=2030-01-15%2010:02:00")) == 3
    assert len(get("to=2030-01-15T10:00:00.999&kind=operation")) == 3
    assert get("kind=operation&q=zzz") == []


def test_events_validation(client, demo):  # noqa: F811
    for qs in (
        "limit=0",
        "limit=1001",
        "limit=abc",
        "limit=-1",
        "after=zzz",
        "after=1",
        "from=yesterday",
        "to=2030-02-31",
        "stream=x",
        "source=-1",
        "kind=Operation",
        "kind=a;b",
        "status=ok,,fail",
        "q=" + "x" * 200,
        "name=" + ",".join(f"n{i}" for i in range(30)),
    ):
        invalid(client, f"/api/events?case=Demo&{qs}")


def test_events_unknown_case_and_missing_case(client, demo):  # noqa: F811
    assert client.get("/api/events?case=nope")[0] == 404
    status, body, _ = client.get("/api/events")
    assert status == 400 and body["error"] == "invalid_case_id"
    assert client.request("POST", "/api/events?case=Demo")[0] == 405


def test_sql_injection_text_is_inert(client, demo):  # noqa: F811
    status, body, _ = client.get(
        "/api/events?case=Demo&q=%27%3B%20DROP%20TABLE%20event%3B--&name=%27%20OR%201%3D1"
    )
    assert status == 200 and body["events"] == []
    assert len(client.get("/api/events?case=Demo&limit=1000")[1]["events"]) == 27


def test_get_event_detail(client, demo):  # noqa: F811
    op = next(e for e in client.get("/api/events?case=Demo&q=K003&kind=operation")[1]["events"])
    status, body, _ = client.get(f"/api/events/{op['id']}?case=Demo")
    assert status == 200
    assert body["event"]["id"] == op["id"] and body["entries_total"] == 3
    assert [e["id"] for e in body["entries"]] == [1, 2, 3]
    assert set(body["entries"][0]) == {"id", "ts", "tid", "level", "sev", "tpl_code", "msg"}
    assert body["entries"][0]["tpl_code"] == "TST-001" and len(body["entries"][0]["msg"]) == 300
    assert [e["kind"] for e in body["related"]] == ["exception", "retry_seq"]


def test_get_event_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/events/999999?case=Demo")
    assert status == 404 and body["error"] == "event_not_found"
    invalid(client, "/api/events/abc?case=Demo")
    assert client.get("/api/events/1?case=nope")[0] == 404


def test_events_stats(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/events/stats?case=Demo")
    assert status == 200 and body["kind"] == "operation"
    send = next(g for g in body["groups"] if g["name"] == "SendA")
    assert send["family"] == "AWFM" and (send["p50_ms"], send["p90_ms"], send["p99_ms"]) == (10, 18, 20)
    assert send["max_ms"] == 20 and send["avg_ms"] == 10.5 and send["open"] == 1 and send["fail"] == 2
    assert set(send) == {
        "family", "name", "n", "ok", "business_error", "fail", "timeout", "open", "truncated",
        "p50_ms", "p90_ms", "p99_ms", "max_ms", "avg_ms",
    }  # fmt: skip
    assert body["totals"]["n"] == 24
    only = client.get("/api/events/stats?case=Demo&stream=2&from=2030-01-15%2010:00")[1]
    assert [g["name"] for g in only["groups"]] == ["GetB"]
    invalid(client, "/api/events/stats?case=Demo&kind=BAD")
    invalid(client, "/api/events/stats?case=Demo&from=x")


def test_events_histogram(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/events/histogram?case=Demo&kind=operation&bucket=minute")
    assert status == 200 and body["bucket_ms"] == 60_000 and len(body["buckets"]) == 2
    assert body["buckets"][0]["counts"] == {"ok": 18, "fail": 2, "open": 1}
    hour = client.get("/api/events/histogram?case=Demo&bucket=hour&name=SendA")[1]
    assert hour["buckets"][0]["total"] == 21
    assert client.get("/api/events/histogram?case=Demo")[1]["bucket"] == "minute"
    invalid(client, "/api/events/histogram?case=Demo&bucket=day")


def test_stats_series_and_methods(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/stats/series?case=Demo")
    assert status == 200 and len(body["samples"]) == 5 and body["truncated"] is False
    assert [s["ts"] for s in body["samples"]] == sorted(s["ts"] for s in body["samples"])
    one = client.get("/api/stats/series?case=Demo&stream=1&side=client&method=%5BTOTAL%5D")[1]["samples"]
    assert [(s["calls"], s["d_calls"]) for s in one] == [(10, None), (15, 5)]
    assert (
        len(
            client.get("/api/stats/series?case=Demo&from=2030-01-15%2010:01:00&to=2030-01-15%2010:01:00")[1][
                "samples"
            ]
        )
        == 2
    )
    for qs in ("side=both", "stream=x", "from=nope", "method=" + "m" * 300):
        invalid(client, f"/api/stats/series?case=Demo&{qs}")
    methods = client.get("/api/stats/methods?case=Demo&stream=1")[1]["methods"]
    assert [(m["side"], m["method"], m["calls"]) for m in methods] == [
        ("client", "A.B", 4),
        ("client", "[TOTAL]", 15),
        ("service", "A.B", 9),
    ]
    assert len(client.get("/api/stats/methods?case=Demo")[1]["methods"]) == 4
    invalid(client, "/api/stats/methods?case=Demo&stream=-")
