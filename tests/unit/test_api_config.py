"""GET /api/config against a real loopback server and a synthetic case with a Registry."""

import pytest
from test_api import client, mf, server  # noqa: F401  (fixtures)
from test_config_store import build_folder

from dmslog.ingest.pipeline import run_ingest
from dmslog.store import create_case_db


@pytest.fixture
def demo(server, tmp_path):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, build_folder(tmp_path / "coll"))
    conn.close()
    return "Demo"


def test_config_for_product(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/config?case=Demo&product=AdapterWfm")
    assert status == 200 and body["case"] == "Demo" and body["product"] == "AdapterWfm"
    keys = {k["name"]: k for k in body["keys"]}
    assert keys["LogLevel"]["effective"] == "DebugLog" and keys["LogLevel"]["diverges"] is True
    assert [x["status"] for x in keys["LogLevel"]["layers"]] == ["overridden", "wins"]
    assert body["log_level"] == {"value": "Info", "source": "observed"}
    ops = {o["operation"]: o["payload_expected"] for o in body["operations"]}
    assert ops == {"CreatedThing": True, "ChangedThing": False, "QuietThing": False, "OddThing": None}


def test_config_uses_the_canonical_case_id(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/config?case=demo&product=AdapterWfm")
    assert status == 200 and body["case"] == "Demo"


def test_config_without_product_lists_products(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/config?case=Demo")
    assert status == 200
    assert [p["product"] for p in body["products"]] == ["AdapterWfm"]


def test_config_group_filter(client, demo):  # noqa: F811
    group = client.get("/api/config?case=Demo")[1]["products"][0]["group"]
    assert client.get(f"/api/config?case=Demo&product=AdapterWfm&group={group}")[0] == 200
    assert client.get("/api/config?case=Demo&product=AdapterWfm&group=999")[0] == 404


def test_config_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/config?case=Demo&product=AdapterNope")
    assert status == 404 and body["error"] == "config_not_found"
    status, body, _ = client.get("/api/config?case=Demo&product=bad%20name")
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = client.get("/api/config?case=Demo&product=AdapterWfm&group=x")
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = client.get("/api/config?case=Nope&product=AdapterWfm")
    assert status == 404 and body["error"] == "case_not_found"
    status, body, _ = client.get("/api/config?product=AdapterWfm")
    assert status == 400 and body["error"] == "invalid_case_id"
    assert client.request("POST", "/api/config", {})[0] == 405
