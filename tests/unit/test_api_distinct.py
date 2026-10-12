"""POST /api/entries/distinct against a real loopback server (synthetic ERS)."""

from __future__ import annotations

from query_helpers import build_case
from test_api import case, client, mf, server, zip_path  # noqa: F401  (fixtures)


def post(client, body):  # noqa: F811
    return client.post("/api/entries/distinct", body)


def test_distinct_by_template_with_span(client, case):  # noqa: F811
    status, body, _ = post(client, {"case": case, "field": "tpl", "limit": 3, "span": True})
    assert status == 200
    assert set(body) == {"field", "values", "others", "total", "groups", "exact", "method"}
    assert body["field"] == "tpl" and body["exact"] is True
    count = client.post("/api/entries/count", {"case": case, "filter": None})[1]["count"]
    assert body["total"] == count
    shown = sum(v["count"] for v in body["values"])
    assert shown + body["others"]["count"] == body["total"]
    assert len(body["values"]) <= 3 and body["others"]["groups"] == body["groups"] - len(body["values"])
    for v in body["values"]:
        assert {"value", "count", "pct", "leaf", "label", "first_ts", "last_ts"} == set(v)
        assert v["leaf"] == {"tpl": v["value"]}
        drilled = client.post("/api/entries/count", {"case": case, "filter": v["leaf"]})[1]["count"]
        assert drilled == v["count"]


def test_distinct_inside_a_filter_and_default_limit(client, case):  # noqa: F811
    _, sources, _ = client.get(f"/api/sources?case={case}")
    wfm = next(s for s in sources["sources"] if s["name"] == "WfmAdapter_1111.log")
    flt = {"source": [wfm["id"]]}
    status, body, _ = post(client, {"case": case, "filter": flt, "field": "sev"})
    assert status == 200 and body["total"] == 10 and len(body["values"]) <= 20
    status, body, _ = post(client, {"case": case, "filter": flt, "field": "source"})
    assert [(v["value"], v["count"], v["label"]) for v in body["values"]] == [(wfm["id"], 10, wfm["name"])]


def test_distinct_validation(client, case):  # noqa: F811
    for field in (None, "msg", "fields.a-b", "key.a b", "tpl; DROP TABLE entry"):
        status, body, _ = post(client, {"case": case, "field": field})
        assert status == 400 and body["error"] == "bad_field" and body["path"] == "$.field", field
    for limit in (0, 501, "5", True):
        status, body, _ = post(client, {"case": case, "field": "tpl", "limit": limit})
        assert status == 400 and body["error"] == "bad_top"
    status, body, _ = post(client, {"case": case, "field": "tpl", "span": 1})
    assert status == 400 and body["error"] == "bad_span"
    status, body, _ = post(client, {"case": case, "field": "tpl", "filter": [1]})
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = post(client, {"case": case, "field": "tpl", "filter": {"bogus": 1}})
    assert status == 400 and body["error"] == "unknown_operator"
    status, body, _ = post(client, {"case": case, "field": "tpl", "filter": {"text": {"substr": "x"}}})
    assert status == 400 and body["error"] == "substr_needs_scope"
    status, body, _ = post(client, {"case": "nope", "field": "tpl"})
    assert status == 404
    assert client.get(f"/api/entries/distinct?case={case}")[0] == 400  # GET /api/entries/<id>: not an id


def test_distinct_of_parsed_fields_and_keys_with_drill_down(client, server):  # noqa: F811
    case_dir = server.api.store.root / "Fields"
    case_dir.mkdir(parents=True)
    build_case(case_dir / "case.sqlite")
    for field, leaf_of in (
        ("fields.op", lambda v: {"field": {"name": "op", "eq": v}}),
        ("key.msgid", lambda v: {"key": {"type": "msgid", "eq": v}}),
    ):
        status, body, _ = post(client, {"case": "Fields", "field": field, "limit": 3})
        assert status == 200 and body["exact"] is True and body["values"]
        for v in body["values"]:
            assert v["leaf"] == leaf_of(v["value"]) and "label" not in v
            drilled = client.post("/api/entries/count", {"case": "Fields", "filter": v["leaf"]})[1]["count"]
            assert drilled == v["count"]
