"""Route tests of /api/views (saved filters per case)."""

import json

import test_api

server, client, zip_path, case = (
    test_api.server,
    test_api.client,
    test_api.zip_path,
    test_api.case,
)  # fixtures

FLT = {"and": [{"sev": {"le": 2}}]}


def test_views_lifecycle(client, case):
    status, body, _ = client.get(f"/api/views?case={case}")
    assert status == 200 and body == {"views": []}
    status, made, _ = client.post("/api/views", {"case": case, "name": " Errors ", "filter": FLT})
    assert status == 201 and made["name"] == "Errors" and made["filter"] == FLT and made["id"]
    status, body, _ = client.get(f"/api/views?case={case}")
    assert status == 200 and body["views"] == [made]
    assert client.request("DELETE", f"/api/views/{made['id']}?case={case}")[0] == 204
    assert client.get(f"/api/views?case={case}")[1] == {"views": []}


def test_case_id_is_canonical(client, case):
    assert client.post("/api/views", {"case": "DEMO", "name": "a", "filter": FLT})[0] == 201
    assert len(client.get("/api/views?case=Demo")[1]["views"]) == 1


def test_errors(client, case):
    assert client.get("/api/views")[0] == 400
    assert client.get("/api/views?case=nope")[0] == 404
    assert client.get("/api/views?case=../x")[0] == 400
    status, body, _ = client.post("/api/views", {"case": case, "name": "", "filter": FLT})
    assert status == 400 and body["error"] == "bad_name"
    status, body, _ = client.post("/api/views", {"case": case, "name": "a", "filter": {"zzz": 1}})
    assert status == 400 and body["error"] == "unknown_operator"
    status, body, _ = client.post("/api/views", {"case": case, "name": "a", "filter": []})
    assert status == 400 and body["error"] == "bad_filter"
    status, body, _ = client.post(
        "/api/views", {"case": case, "name": "a", "filter": {"text": {"substr": "x"}}}
    )
    assert status == 400 and body["error"] == "substr_needs_scope"
    assert client.request("DELETE", f"/api/views/abc?case={case}")[0] == 400
    status, body, _ = client.request("DELETE", f"/api/views/999?case={case}")
    assert status == 404 and body["error"] == "not_found"
    assert client.request("PUT", "/api/views")[0] == 405


def test_duplicate_and_limit(client, case):
    assert client.post("/api/views", {"case": case, "name": "A", "filter": FLT})[0] == 201
    status, body, _ = client.post("/api/views", {"case": case, "name": "a", "filter": FLT})
    assert status == 409 and body["error"] == "duplicate_name"


def test_too_large_body_and_filter(client, case):
    status, body, _ = client.request("POST", "/api/views", raw=b"x" * (300 * 1024))
    assert status == 413
    big = {"or": [{"text": {"fts": "w" * 500}}] * 200}  # under body cap, over filter size cap
    raw = json.dumps({"case": case, "name": "big", "filter": big})
    assert len(raw) < 256 * 1024
    status, body, _ = client.request("POST", "/api/views", raw=raw.encode())
    assert status in (400, 413)


def test_views_are_per_case(client, case):
    client.post("/api/cases", {"name": "other"})
    client.post("/api/views", {"case": case, "name": "a", "filter": FLT})
    assert client.get("/api/views?case=other")[1] == {"views": []}
