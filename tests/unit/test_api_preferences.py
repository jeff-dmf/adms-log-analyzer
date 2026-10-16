"""Route tests of /api/preferences/export and /api/preferences/import (ROADMAP 9.10)."""

import json

import test_api

server, client = test_api.server, test_api.client  # fixtures

TREE = {"or": [{"sev": {"le": 2}}, {"tpl": ["GEN-EXC-01"]}]}


def test_export_import_roundtrip_over_http(client, server):
    assert client.post("/api/presets", {"name": "Mine", "filter": TREE})[0] == 201
    status, out, _ = client.get("/api/preferences/export")
    assert status == 200 and out["package"]["presets"][0]["name"] == "Mine"
    assert json.loads(out["text"]) == out["package"]
    status, _, _ = client.request("DELETE", "/api/presets?name=Mine")
    assert status == 204
    status, dry, _ = client.post("/api/preferences/import", {"package": out["text"], "dry_run": True})
    assert status == 200 and dry["applied"] is False and dry["summary"]["added"] == 1
    assert client.get("/api/presets")[1]["presets"] == [
        p for p in client.get("/api/presets")[1]["presets"] if p["builtin"]
    ]
    status, rep, _ = client.post("/api/preferences/import", {"package": out["package"]})
    assert status == 200 and rep["outcome"] == "applied" and rep["applied"] is True
    status, again, _ = client.post("/api/preferences/import", {"package": out["package"]})
    assert again["outcome"] == "unchanged"
    assert client.get("/api/preferences/export")[1]["text"] == out["text"]


def test_sections_and_errors(client):
    assert client.get("/api/preferences/export?sections=presets")[1]["package"].keys() == {
        "schema", "kind", "presets",
    }  # fmt: skip
    status, err, _ = client.get("/api/preferences/export?sections=bogus")
    assert status == 400 and err["error"] == "bad_option"
    status, err, _ = client.post("/api/preferences/import", {"package": {}, "mode": "x"})
    assert status == 400 and err["error"] == "bad_option"
    status, rep, _ = client.post(
        "/api/preferences/import", {"package": {"schema": 9, "kind": "dmslog-preferences"}}
    )
    assert status == 200 and rep["outcome"] == "rejected" and rep["errors"][0]["code"] == "schema_too_new"
    assert client.request("PUT", "/api/preferences/import")[0] == 405
    assert client.request("POST", "/api/preferences/import", raw=b"x" * (9 * 1024 * 1024))[0] == 413
