"""GET/PUT /api/redaction and POST /api/export against a real loopback server and the synthetic case."""

from __future__ import annotations

import http.client
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest
from test_api import client, server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "export_case_api", ROOT / "tests" / "fixtures" / "export" / "build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_api"] = case_module
_spec.loader.exec_module(case_module)

SENSITIVE = case_module.SENSITIVE
ALL_VALUES = [v for values in SENSITIVE.values() for v in values]


@pytest.fixture
def demo(server):  # noqa: F811
    folder = server.api.store.root / "Demo"
    folder.mkdir(parents=True)
    case_module.build_export_case(folder / "case.sqlite")
    return "Demo"


def body(case="Demo", **overrides):
    request = {
        "case": case,
        "selection": {"events": [1], "ids": [7]},
        "options": {"anonymize": True, "include_payloads": True, "context": 1},
    }
    request.update(overrides)
    return request


def put_profile(client, profile):  # noqa: F811
    return client.request("PUT", "/api/redaction", profile)


# -- profile -----------------------------------------------------------------------------------


def test_default_profile_when_nothing_is_stored(client, server):  # noqa: F811
    status, data, headers = client.get("/api/redaction")
    assert status == 200 and headers["cache-control"] == "no-store"
    assert data["invalid"] is False and data["profile"] == data["defaults"]
    assert all(data["profile"]["categories"].values())
    assert [c["id"] for c in data["categories"]][:3] == ["guid", "gid", "ticket"]
    assert not (server.api.data_dir / "redaction.toml").exists()


def test_profile_is_saved_in_the_data_dir_and_read_back(client, server):  # noqa: F811
    profile = {
        "name": "mine",
        "categories": {"ip": False},
        "terms": {"word": ["Acme", "Jos" + chr(0xE9)], "literal": ["SE-1"]},
        "exceptions": ["SendWorks"],
        "payload_names": ["Name"],
    }
    status, data, _ = put_profile(client, profile)
    assert status == 200 and data["profile"]["categories"]["ip"] is False
    stored = server.api.data_dir / "redaction.toml"
    assert stored.is_file() and "Acme" in stored.read_text(encoding="utf-8")
    again = client.get("/api/redaction")[1]["profile"]
    assert again["name"] == "mine" and again["terms"] == profile["terms"]
    assert again["exceptions"] == ["SendWorks"] and again["payload_names"] == ["Name"]
    assert again["categories"]["guid"] is True


def test_put_accepts_a_wrapper_and_rejects_bad_profiles_without_echoing_values(client, server):  # noqa: F811
    assert put_profile(client, {"profile": {"name": "wrapped"}})[0] == 200
    assert client.get("/api/redaction")[1]["profile"]["name"] == "wrapped"
    for bad in (
        {"categories": {"nope": True}},
        {"terms": {"word": ["x"]}},
        {"terms": {"word": "secret-customer"}},
        {"name": ""},
    ):
        status, data, _ = put_profile(client, bad)
        assert status == 400 and data["error"] == "invalid_profile"
        assert "secret-customer" not in json.dumps(data)
    assert client.get("/api/redaction")[1]["profile"]["name"] == "wrapped"  # a bad PUT changes nothing


def test_a_damaged_profile_file_fails_closed(client, server, demo):  # noqa: F811
    (server.api.data_dir / "redaction.toml").write_text("[categories\n", encoding="utf-8")
    status, data, _ = client.get("/api/redaction")
    assert status == 200 and data["invalid"] is True and data["profile"] == data["defaults"]
    assert "save a new one" in data["message"]
    status, data, _ = client.post("/api/export", body())
    assert status == 409 and data["error"] == "redaction_profile_invalid"
    status, data, _ = client.post("/api/export", body(options={"anonymize": False}))
    assert status == 200 and data["anonymized"] is False  # the original text does not need the profile
    status, data, _ = client.post("/api/export", body(options={}))
    assert status == 200 and data["anonymized"] is False  # neither does the default (U18)
    assert put_profile(client, {"name": "fixed"})[0] == 200
    assert client.post("/api/export", body())[0] == 200


# -- export ------------------------------------------------------------------------------------


def test_export_response_shape_and_headers(client, server, demo):  # noqa: F811
    assert put_profile(client, {"terms": {"word": case_module.USER_TERMS}})[0] == 200
    status, data, headers = client.post("/api/export", body())
    assert status == 200 and headers["cache-control"] == "no-store"
    assert set(data) == {"parts", "legend", "anonymized", "warnings", "counts", "map_saved"}
    assert data["anonymized"] is True and data["map_saved"] is True
    (part,) = data["parts"]
    assert set(part) == {"name", "text"} and part["name"].endswith(".txt")
    assert part["text"].startswith("dmslog report - <TERM-1> <TERM-2> <VALUE-1>\r\n")
    assert data["counts"] == {"records": 6, "events": 1, "payloads": 2}
    categories = {row["category"] for row in data["legend"]}
    assert {"guid", "host", "url", "term", "person"} <= categories
    blob = json.dumps(data)
    for value in ALL_VALUES + case_module.USER_TERMS:
        assert value.casefold() not in blob.casefold(), value


def test_export_uses_the_stored_profile(client, server, demo):  # noqa: F811
    put_profile(client, {"categories": {"guid": False, "person": False}, "mode": "standard"})
    text = client.post("/api/export", body())[1]["parts"][0]["text"]
    assert case_module.GUID_A in text and "Maria Souza" in text
    assert "<EMAIL-1>" in text and "standard mode" in text
    # the stored mode decides: in strict mode the nets still hide what the categories let through
    put_profile(client, {"categories": {"guid": False, "person": False}, "mode": "strict"})
    text = client.post("/api/export", body())[1]["parts"][0]["text"]
    assert case_module.GUID_A not in text and "Maria Souza" not in text and "strict mode" in text


def test_profile_mode_is_validated_and_round_trips(client, server):  # noqa: F811
    status, data, _ = put_profile(client, {"mode": "lenient"})
    assert status == 400 and data["error"] == "invalid_profile"
    status, data, _ = put_profile(client, {"mode": "standard"})
    assert status == 200 and data["profile"]["mode"] == "standard"
    assert client.get("/api/redaction")[1]["profile"]["mode"] == "standard"
    assert client.get("/api/redaction")[1]["defaults"]["mode"] == "strict"


def test_explicit_no_anonymize(client, server, demo):  # noqa: F811
    status, data, _ = client.post("/api/export", body(options={"anonymize": False, "include_payloads": True}))
    assert (
        status == 200 and data["anonymized"] is False and data["legend"] == [] and data["map_saved"] is False
    )
    text = data["parts"][0]["text"]
    assert "Redaction: OFF" in text and case_module.GUID_A in text
    assert not (server.api.data_dir / "redaction-maps").exists()


@pytest.mark.parametrize("options", [{}, {"include_payloads": True, "context": 1}, None])
def test_the_default_export_is_the_original_text(client, server, demo, options):  # noqa: F811
    # U18: without ``anonymize`` the report is the original text, the CLI's default too
    request = body(options=options)
    if options is None:
        del request["options"]
    status, data, _ = client.post("/api/export", request)
    assert status == 200 and data["anonymized"] is False
    assert data["legend"] == [] and data["map_saved"] is False
    text = data["parts"][0]["text"]
    assert "Redaction: OFF (original text)" in text and "== Redaction legend ==" not in text
    assert case_module.GUID_A in text and "<GUID-" not in text and "<VALUE-" not in text
    assert not (server.api.data_dir / "redaction-maps").exists()


def test_value_map_goes_inside_the_case_folder(client, server, demo):  # noqa: F811
    data = client.post("/api/export", body())[1]
    maps = list((server.api.store.root / "Demo" / "redaction-maps").glob("*.map.json"))
    assert len(maps) == 1 and data["map_saved"] is True
    assert "Demo" not in maps[0].name  # the file name does not carry the case id
    values = json.loads(maps[0].read_text(encoding="utf-8"))["values"]
    assert values["<GUID-1>"] == case_module.GUID_A
    assert case_module.GUID_A not in data["parts"][0]["text"]
    assert not (server.api.data_dir / "redaction-maps").exists()  # not in the shared folder any more


def test_parts_when_the_report_is_larger_than_the_limit(client, server, demo):  # noqa: F811
    options = {"anonymize": True, "include_payloads": True, "context": 1, "max_bytes": 2000}
    data = client.post("/api/export", body(options=options))[1]
    assert len(data["parts"]) >= 2
    assert all(len(p["text"].encode("utf-8")) <= 2000 for p in data["parts"])
    assert data["parts"][0]["name"].endswith(f"-part1of{len(data['parts'])}.txt")


def test_selection_by_filter_with_scope_check(client, server, demo):  # noqa: F811
    options = {"anonymize": True}
    status, data, _ = client.post(
        "/api/export", body(selection={"filter": {"sev": {"le": 2}}}, options=options)
    )
    assert status == 200 and data["counts"]["records"] == 1
    status, data, _ = client.post(
        "/api/export", body(selection={"filter": {"text": {"substr": "timeout"}}}, options=options)
    )
    assert status == 400 and data["error"] == "substr_needs_scope"
    status, data, _ = client.post(
        "/api/export",
        body(
            selection={"filter": {"and": [{"source": 1}, {"text": {"substr": "timeout"}}]}}, options=options
        ),
    )
    assert status == 200


@pytest.mark.parametrize(
    ("overrides", "status", "error"),
    [
        ({"options": {"anonymize": "true"}}, 400, "bad_option"),
        ({"options": {"anonymize": None}}, 400, "bad_option"),
        ({"options": []}, 400, "bad_options"),
        ({"options": {"anonymize": True, "context": 500}}, 400, "bad_option"),
        ({"selection": None}, 400, "bad_selection"),
        ({"selection": {"ids": []}}, 400, "bad_ids"),
        ({"selection": {"ids": ["x"]}}, 400, "bad_ids"),
        ({"selection": {"filter": {"nope": 1}}}, 400, "unknown_leaf"),
        ({"selection": {"ids": [999]}}, 404, "nothing"),
        ({"case": "missing"}, 404, "case_not_found"),
        ({"case": None}, 400, "invalid_case_id"),
    ],
)
def test_export_validation(client, server, demo, overrides, status, error):  # noqa: F811
    got, data, _ = client.post("/api/export", body(**overrides))
    assert got == status, data
    assert data["error"] == error or error == "unknown_leaf"


def test_route_methods_and_auth(client, server, demo):  # noqa: F811
    assert client.get("/api/export")[0] == 405
    assert client.request("DELETE", "/api/redaction")[0] == 405
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        conn.request("GET", "/api/redaction")
        response = conn.getresponse()
        response.read()
        assert response.status == 403  # no session cookie
    finally:
        conn.close()


def test_user_terms_are_never_logged(client, server, demo, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    put_profile(client, {"terms": {"word": ["top-secret-customer"]}})
    client.post("/api/export", body())
    client.get("/api/redaction")
    assert "top-secret-customer" not in caplog.text
