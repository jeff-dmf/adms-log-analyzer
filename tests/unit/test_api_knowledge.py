"""GET /api/knowledge and /api/knowledge/error against a synthetic case with an ErrorConfiguration."""

import pytest
from test_api import client, mf, server  # noqa: F401  (fixtures)
from test_pipeline_knowledge import build

from dmslog.ingest.pipeline import run_ingest
from dmslog.store import create_case_db


@pytest.fixture
def demo(server, tmp_path):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, build(tmp_path / "coll", registry=True))
    conn.close()
    return "Demo"


def test_template_knowledge(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=WFM-080")
    assert status == 200 and body["template"] == "WFM-080" and body["family"] == "AWFM"
    assert "WaitForCRSCallback" in body["hint"]["text"]
    assert body["hint"]["params"] == ["WaitForCRSCallback"]
    param = body["params"][0]
    assert param["name"] == "WaitForCRSCallback" and param["default"] and param["description"]
    assert body["adapter"]["acronym"] == "AWFM"
    assert body["vault_note"] == body["hint"]["note"]


def test_template_without_hint_still_describes_the_adapter(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=WFM-011")
    assert status == 200 and body["hint"] is None and body["params"] == []
    assert body["vault_note"] is None or isinstance(body["vault_note"], str)


def test_error_defined_by_the_ers(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge/error?case=Demo&family=AWFM&code=ExampleCodeOne")
    assert status == 200
    assert body["source"] == "ers" and body["out_of_vault"] is True and body["severity"] == 1
    assert body["reason"] == "Example reason" and body["details"] == "Example details"


def test_error_unknown_and_validation(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge/error?case=Demo&family=AWFM&code=NoSuchCode")
    assert status == 404 and body["error"] == "error_not_found"
    assert client.get("/api/knowledge/error?case=Demo&code=x")[1]["error"] == "invalid_field"
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM&code=bad%20code")[0] == 400
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM")[0] == 400
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM&code=X&operation=bad%20op")[0] == 400


def test_template_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=NOPE-1")
    assert status == 404 and body["error"] == "template_not_found"
    assert client.get("/api/knowledge?case=Demo")[0] == 400
    assert client.get("/api/knowledge?case=Demo&template=bad%20code")[0] == 400
    assert client.get("/api/knowledge?template=WFM-080")[1]["error"] == "invalid_case_id"
    assert client.get("/api/knowledge?case=Nope&template=WFM-080")[1]["error"] == "case_not_found"
    assert client.request("POST", "/api/knowledge", {})[0] == 405


def test_reingest_after_editing_a_folder_xml_returns_the_new_value(client, server, tmp_path):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    folder = build(tmp_path / "coll", registry=True)
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, folder)
    conn.close()
    url = "/api/knowledge/error?case=Demo&family=AWFM&code=ExampleCodeOne"
    assert client.get(url)[1]["reason"] == "Example reason"
    xml = next(folder.rglob("Example_Created_ErrorConfiguration.xml"))
    xml.write_text(
        xml.read_text(encoding="utf-8").replace("Example reason", "Edited reason"), encoding="utf-8"
    )
    root_mtime = folder.stat().st_mtime_ns
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, folder)
    conn.close()
    assert folder.stat().st_mtime_ns == root_mtime  # editing an inner file does not touch the root
    assert client.get(url)[1]["reason"] == "Edited reason"
