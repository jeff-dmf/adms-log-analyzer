"""GET /api/keys/<type>/<value>/chain against a real loopback server (written against the route contract).

The route is registered by the main session in ``server/api.py``; until then these tests are skipped.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
import test_store_chain as tsc
from test_api import client, server  # noqa: F401  (fixtures)

from dmslog.server import api as api_module

ROUTE_REGISTERED = "/chain" in Path(api_module.__file__).read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(
    not ROUTE_REGISTERED, reason="route GET /api/keys/<type>/<value>/chain not registered yet"
)


def build(server, name):  # noqa: F811
    case_dir = server.api.store.root / name
    case_dir.mkdir(parents=True)
    db = tsc.Db(case_dir / "case.sqlite")
    db.three_steps("100001", tsc.T0, group=1)
    db.three_steps("100001", tsc.T0 + 100_000, group=2)
    value = "a/b c"
    ev = db.event("Sched", "SchedulerJob", tsc.T0, value)
    db.entry("Sched", tsc.T0, value, ev)
    db.conn.commit()
    db.conn.close()


@pytest.fixture
def demo(server):  # noqa: F811
    build(server, "Demo")
    return "Demo"


def invalid(client, path, error="invalid_field"):  # noqa: F811
    status, body, _ = client.get(path)
    assert status == 400 and body["error"] == error, (path, status, body)


def test_chain_of_a_job(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/keys/job_id/100001/chain?case=Demo")
    assert status == 200
    assert set(body) == {"key", "operations", "handoffs", "scanned", "truncated"}
    assert body["key"] == {"type": "job_id", "value": "100001"}
    assert [o["name"] for o in body["operations"]] == ["SchedulerJob", "LauncherJob", "FunctionHostJob"] * 2
    assert len(body["handoffs"]) == 4 and body["truncated"] is False and body["scanned"] == 8
    assert {"kind", "confidence", "dt_ms", "from", "to"} == set(body["handoffs"][0])


def test_chain_filtered_by_collection(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/keys/job_id/100001/chain?case=Demo&collection=2")
    assert status == 200
    assert len(body["operations"]) == 3 and {o["collection"] for o in body["operations"]} == {2}
    assert len(body["handoffs"]) == 2


def test_chain_unknown_value_is_empty(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/keys/job_id/nothing/chain?case=Demo")
    assert status == 200 and body["operations"] == [] and body["handoffs"] == [] and body["scanned"] == 0


def test_chain_value_with_odd_characters(client, demo):  # noqa: F811
    status, body, _ = client.get(f"/api/keys/job_id/{quote('a/b c', safe='')}/chain?case=Demo")
    assert status == 200 and len(body["operations"]) == 1


def test_chain_validation(client, demo):  # noqa: F811
    invalid(client, "/api/keys/job_id/%FF%FE/chain?case=Demo")
    invalid(client, "/api/keys/job_id/%00/chain?case=Demo")
    invalid(client, "/api/keys/job_id/" + "a" * 257 + "/chain?case=Demo")
    invalid(client, "/api/keys/bad%20type/1/chain?case=Demo")
    invalid(client, "/api/keys/job_id/1/chain?case=Demo&collection=x")
    assert client.get("/api/keys/job_id/1/chain?case=Nope")[0] == 404
    assert client.get("/api/keys/job_id/1/chain")[0] == 400


def test_records_route_still_serves_a_literal_chain_value(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/keys/job_id/chain?case=Demo")
    assert status == 200 and body == {"records": [], "next": None}
