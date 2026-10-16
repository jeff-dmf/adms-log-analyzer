"""POST /api/extract/check and /api/entries/extract[/values|/promote] (ROADMAP 9.8) over a loopback server."""

from __future__ import annotations

import pytest
import test_extract as te
from extract_helpers import build
from test_api import client, mf, server  # noqa: F401  (fixtures)

from dmslog.store import extract as X


@pytest.fixture
def ext_case(server):  # noqa: F811
    case_dir = server.api.store.root / "Ext"
    case_dir.mkdir(parents=True)
    build(case_dir / "case.sqlite", te.RECORDS).close()
    return "Ext"


def test_check_describes_the_regex_or_points_at_the_problem(client):  # noqa: F811
    status, body, _ = client.post("/api/extract/check", {"regex": te.GID, "types": {"gid": "int"}})
    assert status == 200
    assert body["columns"] == [{"name": "gid", "type": "int"}] and body["pattern"] == "GID: (?P<gid>[0-9]+)"
    assert body["target"] == "msg" and body["anchored"] is False and 128 <= body["window"] <= 4096
    status, body, _ = client.post("/api/extract/check", {"regex": "ab(?P<a>.*)x"})
    assert status == 400 and body["error"] == "bad_regex" and body["path"] == "$.regex"
    assert "offset 8" in body["message"]
    status, body, _ = client.post("/api/extract/check", {"regex": te.GID, "target": "all"})
    assert status == 400 and body["error"] == "bad_target"


def test_extract_over_a_filter(client, ext_case):  # noqa: F811
    status, body, _ = client.post(
        "/api/entries/extract",
        {"case": ext_case, "regex": te.GID, "types": {"gid": "int"}, "limit": 5, "top": 3},
    )
    assert status == 200
    assert {"columns", "rows", "scanned", "matched", "cut", "truncated", "window", "pattern"} <= set(body)
    assert body["scanned"] == 5 and body["truncated"] is True
    assert [r["values"] for r in body["rows"]] == [[99], [5], [11], [9], [5]]
    value = body["columns"][0]["values"][0]
    scoped = {"and": [value["leaf"], {"source": [1, 2, 3, 4, 5]}]}  # a regex scan needs a scope
    status, counted, _ = client.post("/api/entries/count", {"case": ext_case, "filter": scoped})
    assert status == 200 and counted["count"] == value["count"]
    status, body, _ = client.post(
        "/api/entries/extract", {"case": ext_case, "regex": te.GID, "filter": {"source": [3]}}
    )
    assert status == 200 and body["matched"] == 3


def test_extract_errors(client, ext_case):  # noqa: F811
    status, body, _ = client.post("/api/entries/extract", {"case": ext_case, "regex": te.GID, "limit": 0})
    assert status == 400 and body["error"] == "bad_limit" and body["path"] == "$.limit"
    status, body, _ = client.post("/api/entries/extract", {"case": "nope", "regex": te.GID})
    assert status == 404
    # a regex scan inside the filter needs a source or time scope, as everywhere
    leaf = {"extract": {"regex": te.GID, "group": "gid", "eq": "5"}}
    status, body, _ = client.post("/api/entries/extract", {"case": ext_case, "regex": te.GID, "filter": leaf})
    assert status == 400 and body["error"] == "substr_needs_scope"
    scoped = {"and": [leaf, {"source": [3]}]}
    status, body, _ = client.post(
        "/api/entries/extract", {"case": ext_case, "regex": te.GID, "filter": scoped}
    )
    assert status == 200 and body["matched"] == 2
    status, body, _ = client.post("/api/entries/count", {"case": ext_case, "filter": leaf})
    assert status == 400 and body["error"] == "substr_needs_scope"


def test_extract_timeout_is_408(client, ext_case, monkeypatch):  # noqa: F811
    real = X.extract

    def slow(conn, ex, flt, **kw):
        return real(conn, ex, flt, **{**kw, "timeout_s": 0})

    monkeypatch.setattr(X, "extract", slow)
    status, body, _ = client.post("/api/entries/extract", {"case": ext_case, "regex": te.GID})
    assert status == 408 and body["error"] == "extract_timeout"


def _id(server, msg):  # noqa: F811
    with server.api.store.reader("Ext") as conn:
        return conn.execute("SELECT id FROM entry WHERE msg = ?", (msg,)).fetchone()[0]


def test_values_of_a_page(client, server, ext_case):  # noqa: F811
    ids = [_id(server, te.RECORDS[k][2]) for k in (0, 6, 7, 8)]
    status, body, _ = client.post(
        "/api/entries/extract/values", {"case": ext_case, "regex": te.GID, "ids": [*ids, 10**6]}
    )
    assert status == 200 and set(body["values"]) == {str(ids[0]), str(ids[1])}
    assert body["values"][str(ids[0])] == {"values": ["17"], "cut": False}
    for ids in (None, [True], [-1], list(range(1001)), ["1"]):
        status, body, _ = client.post(
            "/api/entries/extract/values", {"case": ext_case, "regex": te.GID, "ids": ids}
        )
        assert status == 400 and body["error"] == "bad_ids", ids


def test_promote_route(client, server, ext_case):  # noqa: F811
    regex = r"GID: (?P<gid>\d+), Errors count: (?P<errors>\d+)"
    status, body, _ = client.post(
        "/api/entries/extract/promote", {"case": ext_case, "ids": list(range(1, 10)), "regex": regex}
    )
    assert status == 200 and body["check"]["loads"] is True and body["toml"].startswith("[[template]]")
    unrelated = _id(server, "unrelated")
    status, body, _ = client.post(
        "/api/entries/extract/promote", {"case": ext_case, "ids": [unrelated], "regex": regex}
    )
    assert status == 400 and body["error"] == "promote_no_match"
    mixed = [_id(server, te.RECORDS[k][2]) for k in (0, 6)]
    status, body, _ = client.post(
        "/api/entries/extract/promote", {"case": ext_case, "ids": mixed, "regex": te.GID}
    )
    assert status == 400 and body["error"] == "mixed_families"
