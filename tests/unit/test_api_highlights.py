"""/api/highlights (rules of a case), /api/entries/highlight (batch marking) and .../highlight/next
against a real loopback server (synthetic ERS)."""

from __future__ import annotations

from test_api import case, client, mf, server, zip_path  # noqa: F401  (fixtures)
from test_api_keys import demo  # noqa: F401  (fixture: normal and sensitive records with one key)


def rules_of(client, case):  # noqa: F811
    status, body, _ = client.get(f"/api/highlights?case={case}")
    assert status == 200
    return body


def test_rules_lifecycle(client, case):  # noqa: F811
    empty = rules_of(client, case)
    assert (
        empty["rules"] == []
        and empty["palette"][0] == "yellow"
        and empty["limits"] == {"rules": 20, "ids": 500}
    )
    status, a, _ = client.post(
        "/api/highlights", {"case": case, "name": "errors", "color": "red", "cond": {"sev": {"le": 2}}}
    )
    assert status == 201 and a["pos"] == 0 and a["enabled"] is True
    status, b, _ = client.post(
        "/api/highlights",
        {"case": case, "name": "words", "color": "blue", "cond": {"text": {"fts": "incident"}}},
    )
    assert status == 201 and b["pos"] == 1
    status, changed, _ = client.request(
        "PATCH", f"/api/highlights/{b['id']}", {"case": case, "color": "green", "enabled": False}
    )
    assert status == 200 and changed["color"] == "green" and changed["enabled"] is False
    status, body, _ = client.request(
        "PUT", "/api/highlights/order", {"case": case, "ids": [b["id"], a["id"]]}
    )
    assert status == 200 and [r["id"] for r in body["rules"]] == [b["id"], a["id"]]
    assert client.request("DELETE", f"/api/highlights/{a['id']}?case={case}")[0] == 204
    assert [r["id"] for r in rules_of(client, case)["rules"]] == [b["id"]]
    status, body, _ = client.request("DELETE", f"/api/highlights/{a['id']}?case={case}")
    assert status == 404 and body["error"] == "not_found"


def test_rule_errors(client, case):  # noqa: F811
    def post(body):
        return client.post("/api/highlights", {"case": case, **body})

    status, body, _ = post({"name": "x", "color": "magenta", "cond": {"sev": 1}})
    assert status == 400 and body["error"] == "bad_color"
    status, body, _ = post({"name": "x", "color": "red", "cond": {"bogus": 1}})
    assert status == 400 and body["error"] == "unknown_operator" and body["path"] == "$"
    status, body, _ = post({"name": "x", "color": "red", "cond": {"text": {"regex": "a.*b.*c"}}})
    assert status == 400 and body["error"] == "bad_regex"
    status, body, _ = post({"name": "x", "color": "red", "cond": {"text": {"fts": "x" * 70_000}}})
    assert status == 413 and body["error"] == "cond_too_large"
    for k in range(20):
        assert post({"name": f"r{k}", "color": "red", "cond": {"sev": 1}})[0] == 201
    status, body, _ = post({"name": "x", "color": "red", "cond": {"sev": 1}})
    assert status == 409 and body["error"] == "too_many_rules"
    status, body, _ = client.request("PATCH", "/api/highlights/abc", {"case": case, "color": "red"})
    assert status == 400 and body["error"] == "invalid_highlight_id"
    status, body, _ = client.request("PATCH", "/api/highlights/order", {"case": case})
    assert status == 400 and body["error"] == "invalid_highlight_id"
    status, body, _ = client.request("PUT", "/api/highlights/order", {"case": case, "ids": [1]})
    assert status == 400 and body["error"] == "bad_order"
    assert client.request("GET", "/api/highlights/key")[0] == 405
    status, body, _ = client.get("/api/highlights?case=nope")
    assert status == 404 and body["error"] == "case_not_found"


def test_highlight_key_marks_normal_and_sensitive_records_and_navigates(client, demo):  # noqa: F811
    # demo (test_store_keys.seed): records 1..5 normal, 6..10 sensitive, 11..12 without time; all carry
    # msgid M-1.
    status, body, _ = client.post("/api/highlights/key", {"case": demo, "type": "msgid", "value": "M-1"})
    assert status == 201 and body["created"] is True and body["rule"]["color"] == "yellow"
    status, body, _ = client.post(
        "/api/highlights/key", {"case": demo, "type": "msgid", "value": "M-1", "color": "pink"}
    )
    assert status == 200 and body["created"] is False and body["rule"]["color"] == "pink"
    cond = body["rule"]["cond"]
    assert cond == {"key": {"type": "msgid", "eq": "M-1"}}
    errcode = {"key": {"type": "errcode"}}
    status, body, _ = client.post(
        "/api/entries/highlight", {"case": demo, "rules": [errcode, cond], "ids": list(range(1, 14))}
    )
    assert status == 200
    assert body["marks"] == {"1": 1, "2": 0, "3": 0, **{str(i): 1 for i in range(4, 13)}}
    sensitive = {"sensitive": True}
    status, body, _ = client.post(
        "/api/entries/highlight/next", {"case": demo, "filter": sensitive, "rules": [cond], "dir": "fwd"}
    )
    assert status == 200 and body == {"row": body["row"], "rule": 0} and body["row"]["id"] == 6
    walked, cursor = [], None
    while True:
        status, body, _ = client.post(
            "/api/entries/highlight/next", {"case": demo, "filter": None, "rules": [cond], "cursor": cursor}
        )
        assert status == 200
        if body["row"] is None:
            assert body["rule"] is None
            break
        walked.append(body["row"]["id"])
        row = body["row"]
        cursor = f"{'null' if row['ts'] is None else row['ts']}:{row['id']}"
    assert sorted(walked) == list(range(1, 13)) and walked[:2] == [11, 12]  # NULL ts first, as in the grid
    status, body, _ = client.post(
        "/api/entries/highlight/next", {"case": demo, "rules": [cond], "cursor": cursor, "dir": "back"}
    )
    assert status == 200 and body["row"]["id"] == walked[-2]


def test_next_validation(client, demo):  # noqa: F811
    cond = {"key": {"type": "msgid", "eq": "M-1"}}

    def nxt(**body):
        return client.post("/api/entries/highlight/next", {"case": demo, "rules": [cond], **body})

    status, body, _ = nxt(filter={"sev": 9})
    assert status == 400 and body["error"] == "bad_value"
    status, body, _ = nxt(filter={"text": {"substr": "x"}})
    assert status == 400 and body["error"] == "substr_needs_scope"
    status, body, _ = nxt(cursor="nope")
    assert status == 400 and body["error"] == "bad_cursor"
    status, body, _ = nxt(cursor=5)
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = nxt(dir="up")
    assert status == 400 and body["error"] == "bad_direction"
    status, body, _ = nxt(filter=[1])
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = client.post("/api/entries/highlight/next", {"case": demo, "rules": "x"})
    assert status == 400 and body["error"] == "bad_rules"


def test_mark_limits_over_http(client, case):  # noqa: F811
    status, body, _ = client.post(
        "/api/entries/highlight", {"case": case, "rules": [{"sev": 1}], "ids": list(range(501))}
    )
    assert status == 400 and body["error"] == "bad_ids"
    status, body, _ = client.post(
        "/api/entries/highlight", {"case": case, "rules": [{"sev": 1}] * 21, "ids": [1]}
    )
    assert status == 400 and body["error"] == "bad_rules"
    status, body, _ = client.post(
        "/api/entries/highlight",
        {"case": case, "rules": [{"text": {"fts": "'); DROP TABLE entry;--"}}], "ids": [1]},
    )
    assert status == 200 and body == {"marks": {}}
    assert client.post("/api/entries/count", {"case": case, "filter": None})[1]["count"] > 0


def test_mark_and_next_reject_an_oversized_condition_with_its_index(client, case):  # noqa: F811
    huge = {"text": {"fts": "x" * 70_000}}
    status, body, _ = client.post(
        "/api/entries/highlight", {"case": case, "rules": [{"sev": 1}, huge], "ids": [1]}
    )
    assert status == 400 and body["error"] == "too_large" and body["path"] == "$.rules[1]"
    status, body, _ = client.post(
        "/api/entries/highlight/next", {"case": case, "rules": [huge], "dir": "fwd"}
    )
    assert status == 400 and body["error"] == "too_large" and body["path"] == "$.rules[0]"
