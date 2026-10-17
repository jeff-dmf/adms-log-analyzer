"""Route tests of /api/preferences/export and /api/preferences/import (ROADMAP 9.10)."""

import json

import test_api

from dmslog.server import api as server_api

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


def test_sections_and_errors(client, monkeypatch):
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
    # a small limit keeps the oversized body small: a multi-megabyte body the server stops reading can
    # reset or stall the client socket on Windows
    monkeypatch.setitem(server_api._BODY_LIMITS, "/api/preferences/import", 4096)
    assert client.request("POST", "/api/preferences/import", raw=b"x" * 10_000)[0] == 413


def test_strict_body_over_http(client, server):
    good = b'{"package": {"schema": 1, "kind": "dmslog-preferences", "presets": []}}'
    status, rep, _ = client.request("POST", "/api/preferences/import", raw=good)
    assert status == 200 and rep["outcome"] == "unchanged"
    bad = [
        b'{"package": {"schema": 1, "schema": 1, "kind": "dmslog-preferences"}}',
        b'{"package": {"schema": NaN, "schema": 1, "kind": "dmslog-preferences"}}',
        b'{"package": {"schema": Infinity, "kind": "dmslog-preferences"}}',
        good.decode().encode("utf-16"),
        b"\xef\xbb\xbf" + good,
        b'{"package": ' + b"[" * 50_000 + b"}",
    ]
    for raw in bad:
        status, err, _ = client.request("POST", "/api/preferences/import", raw=raw)
        assert status == 400 and err["error"] in ("bad_json", "package_too_complex"), raw[:30]
    assert not (server.data_dir / "presets.toml").exists()


def test_package_as_text_over_http(client, server):
    import json

    codes = [f"GEN-EXC-{k:02d}" for k in range(30)]
    items = [
        {"name": f"{i:03d}" + "n" * 70, "description": "d" * 190, "filter": {"tpl": codes}}
        for i in range(200)
    ]
    package = {"schema": 1, "kind": "dmslog-preferences", "presets": items}
    text = json.dumps(package)
    assert len(text) > 128 * 1024
    status, rep, _ = client.post("/api/preferences/import", {"package": text, "dry_run": True})
    assert status == 200 and rep["summary"]["added"] == 200
    status, rep, _ = client.post("/api/preferences/import", {"package": package, "dry_run": True})
    assert status == 200 and rep["summary"]["added"] == 200
