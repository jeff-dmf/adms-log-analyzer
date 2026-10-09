"""GET /api/templates and GET /api/unknown against a real loopback server and a fixture case."""

import pytest
from test_api import client, mf, server  # noqa: F401  (fixtures)

from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse.templates import load_texts
from dmslog.store import create_case_db

TOML = r"""
schema = 1
family = "AWFM"

[[template]]
code = "TST-001"
class = "end"
title = "Item done"
regex = '''^\[Alpha\] item (?P<num>\d+) done$'''
"""


def build_case(srv, tmp_path, name="Demo"):
    case_dir = srv.api.store.root / name
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    lines = [mf.header(i * 10, "INFO", f"[Alpha] item {i} done") for i in range(4)]
    lines += [mf.header(100 + i, "INFO", f"[Other] thing {i} happened") for i in range(3)]
    root = tmp_path / "Log"
    member = root / "dnaApp_zone1" / "SVC" / "WfmAdapter_1111.log"
    member.parent.mkdir(parents=True)
    member.write_bytes(mf.render(lines))
    run_ingest(conn, root, IngestOptions(catalog=load_texts({"awfm.toml": TOML})))
    conn.close()
    return name


@pytest.fixture
def demo(server, tmp_path):  # noqa: F811
    return build_case(server, tmp_path)


def test_templates_listing(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/templates?case=Demo")
    assert status == 200 and body["case"] == "Demo" and body["family"] is None
    (tpl,) = body["templates"]
    assert tpl["code"] == "TST-001" and tpl["family"] == "AWFM" and tpl["class"] == "end"
    assert tpl["n"] == 4 and tpl["fts"] is True and tpl["title"] == "Item done"
    assert tpl["first_ts"] < tpl["last_ts"] and set(tpl) >= {"id", "sev", "def_version"}


def test_templates_use_the_canonical_case_id(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/templates?case=demo")
    assert status == 200 and body["case"] == "Demo"


def test_templates_family_filter(client, demo):  # noqa: F811
    assert len(client.get("/api/templates?case=Demo&family=AWFM")[1]["templates"]) == 1
    status, body, _ = client.get("/api/templates?case=Demo&family=AONT")
    assert status == 200 and body["templates"] == [] and body["family"] == "AONT"


def test_unknown_listing_and_totals(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/unknown?case=Demo")
    assert status == 200 and body["case"] == "Demo"
    assert body["unknown"] == [
        {"family": "AWFM", "signature": "[Other] thing <n> happened", "n": 3, "first_entry_id": 5}
    ]
    assert body["signatures"] == 1 and body["records"] == 3
    assert client.get("/api/unknown?case=Demo&family=AONT")[1]["unknown"] == []


def test_unknown_limit(client, demo):  # noqa: F811
    assert client.get("/api/unknown?case=Demo&limit=1")[0] == 200
    for bad in ("0", "501", "abc", "-1"):
        status, body, _ = client.get(f"/api/unknown?case=Demo&limit={bad}")
        assert status == 400 and body["error"] in ("bad_limit", "invalid_field")


@pytest.mark.parametrize("route", ["templates", "unknown"])
def test_errors(client, demo, route):  # noqa: F811
    status, body, _ = client.get(f"/api/{route}?case=nope")
    assert status == 404 and body["error"] == "case_not_found"
    status, body, _ = client.get(f"/api/{route}")
    assert status == 400 and body["error"] == "invalid_case_id"
    status, body, _ = client.get(f"/api/{route}?case=..%2Fx")
    assert status in (400, 404)
    status, body, _ = client.get(f"/api/{route}?case=Demo&family=AW%20FM")
    assert status == 400 and body["error"] == "invalid_field"
    assert client.request("POST", f"/api/{route}?case=Demo", {})[0] == 405
