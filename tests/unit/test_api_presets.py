"""Route tests of /api/presets (curated + user presets)."""

import json

import test_api

server, client, zip_path, case = (
    test_api.server,
    test_api.client,
    test_api.zip_path,
    test_api.case,
)  # fixtures

TREE = {"or": [{"sev": {"le": 2}}, {"tpl": ["GEN-EXC-01"]}]}


def test_list_has_curated_and_filters_by_case_families(client, case):
    _, everything, _ = client.get("/api/presets")
    status, applicable, _ = client.get(f"/api/presets?case={case}")
    assert status == 200 and applicable["invalid"] is False
    assert all(p["builtin"] for p in everything["presets"])
    assert len(applicable["presets"]) <= len(everything["presets"])
    _, sources, _ = client.get(f"/api/sources?case={case}")
    present = {s["family"] for s in sources["sources"] if s["family"]}
    assert {p["family"] for p in applicable["presets"]} <= present | {None}
    assert any(p["name"] == "Errors and exceptions" for p in applicable["presets"])


def test_lifecycle(client, case):
    body = {"name": " My errors ", "description": "x", "family": None, "filter": TREE}
    status, made, _ = client.post("/api/presets", body)
    assert status == 201 and made["name"] == "My errors" and made["builtin"] is False
    status, listing, _ = client.get(f"/api/presets?case={case}")
    assert any(p["name"] == "My errors" and not p["builtin"] for p in listing["presets"])
    status, updated, _ = client.request(
        "PUT", "/api/presets", {"match": {"name": "my errors"}, "preset": {**body, "name": "Renamed"}}
    )
    assert status == 200 and updated["name"] == "Renamed"
    status, _, _ = client.request("DELETE", "/api/presets?name=Renamed")
    assert status == 204
    status, err, _ = client.request("DELETE", "/api/presets?name=Renamed")
    assert status == 404 and err["error"] == "not_found"


def test_errors(client, case):
    status, err, _ = client.post("/api/presets", {"name": "", "filter": TREE})
    assert status == 400 and err["error"] == "bad_name"
    status, err, _ = client.post("/api/presets", {"name": "a", "filter": {"zzz": 1}})
    assert status == 400 and err["error"] == "filter_invalid"
    status, err, _ = client.post("/api/presets", {"name": "a", "filter": {"source": 1}})
    assert status == 400 and err["error"] == "case_specific"
    status, err, _ = client.post("/api/presets", {"name": "Errors and exceptions", "filter": TREE})
    assert status == 409 and err["error"] == "reserved_name"
    status, err, _ = client.request("DELETE", "/api/presets?name=Errors%20and%20exceptions")
    assert status == 403 and err["error"] == "builtin"
    assert client.request("DELETE", "/api/presets")[0] == 400
    assert client.request("PUT", "/api/presets", {"preset": {}})[0] == 400
    assert client.get("/api/presets?case=nope")[0] == 404
    assert client.request("PATCH", "/api/presets")[0] == 405
    assert client.request("POST", "/api/presets", raw=b"x" * (300 * 1024))[0] == 413


def test_export_and_import(client, case):
    client.post("/api/presets", {"name": "One", "family": "AWFM", "filter": TREE})
    for fmt in ("json", "toml"):
        status, out, _ = client.get(f"/api/presets/export?format={fmt}")
        assert status == 200 and out["format"] == fmt and out["count"] == 1
    assert client.get("/api/presets/export?format=yaml")[0] == 400
    _, out, _ = client.get("/api/presets/export?format=json")
    out["data"]["preset"].append({"name": "Broken", "filter": {"zzz": 1}})
    status, report, _ = client.post("/api/presets/import", {"format": "json", "data": out["data"]})
    assert status == 200 and report["updated"] == 1 and report["added"] == 0
    assert [i["name"] for i in report["ignored"]] == ["Broken"]
    status, report, _ = client.post("/api/presets/import", {"format": "toml", "data": "x = [oops"})
    assert status == 400 and report["error"] == "bad_import"
    big = json.dumps({"format": "json", "data": "x" * (2 * 1024 * 1024)}).encode()
    assert client.request("POST", "/api/presets/import", raw=big)[0] == 413
