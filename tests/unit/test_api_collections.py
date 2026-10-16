"""GET/DELETE /api/cases/<case>/collections and the ``collection`` parameters of the read routes (U21)."""

from __future__ import annotations

import multi_helpers as mh
import pytest
from test_api import client, server  # noqa: F401  (fixtures)


@pytest.fixture
def two(client, tmp_path):  # noqa: F811
    """Case ``two``: servers A and B, each with its Enterprise and Sensitive zip, added through the API."""
    zips = mh.build_all(tmp_path / "zips")
    assert client.post("/api/cases", {"name": "two"})[0] == 201
    for key in (("A", "enterprise"), ("B", "sensitive"), ("A", "sensitive"), ("B", "enterprise")):
        assert client.post("/api/cases/two/collections", {"path": str(zips[key])})[0] == 202
        client.wait_done("two")
    return zips


def groups(client):  # noqa: F811
    status, body, _ = client.get("/api/cases/two/collections")
    assert status == 200
    return body


def test_listing_contract(client, two):  # noqa: F811
    body = groups(client)
    assert body["case"] == "two" and body["ordering"] == "approximate"
    first, second = body["collections"]
    assert set(first) == {
        "id", "group", "number", "tag", "kind", "guid", "capture", "server", "machine", "system", "label",
        "variants", "missing", "clock_offset_ms", "clock_source", "status", "sources", "listed", "records",
        "first_ts", "last_ts", "added_at", "members",
    }  # fmt: skip
    assert (first["tag"], first["guid"], first["capture"], first["server"]) == (
        "C1",
        mh.SERVERS["A"][0],
        "20300115103000",
        "zonea",
    )
    assert (second["tag"], second["variants"], second["clock_offset_ms"]) == (
        "C2",
        ["enterprise", "sensitive"],
        0,
    )
    assert [m["variant"] for m in second["members"]] == ["sensitive", "enterprise"]  # order of arrival
    assert all("/" not in m["name"] and "\\" not in m["name"] for m in first["members"])  # names, not paths
    assert first["records"] == second["records"] == mh.RECORDS


def test_read_routes_take_a_collection(client, two):  # noqa: F811
    a, b = (g["id"] for g in groups(client)["collections"])
    _, sources, _ = client.get("/api/sources?case=two")
    assert {s["collection"] for s in sources["sources"]} == {a, b}
    _, page, _ = client.post(
        "/api/entries/page", {"case": "two", "filter": {"collection": [b]}, "columns": ["collection"]}
    )
    assert page["rows"] and {r["collection"] for r in page["rows"]} == {b}
    _, keys, _ = client.get(f"/api/keys/vehicle/{mh.plate(4)}?case=two")
    assert sorted(r["collection"] for r in keys["records"]) == [a, b]
    _, keys, _ = client.get(f"/api/keys/vehicle/{mh.plate(4)}?case=two&collection={a}")
    assert [r["collection"] for r in keys["records"]] == [a]
    assert client.get("/api/events?case=two&collection=x")[0] == 400
    assert client.get(f"/api/events?case=two&collection={b}")[0] == 200


def test_remove_a_collection(client, server, two):  # noqa: F811
    a, b = (g["id"] for g in groups(client)["collections"])
    status, body, _ = client.request("DELETE", f"/api/cases/two/collections/{b}")
    assert status == 200
    assert body == {"group": b, "collections": 2, "sources": 2, "records": mh.RECORDS, "bookmarks": 0}
    after = groups(client)
    assert [g["id"] for g in after["collections"]] == [a] and after["ordering"] == "exact"
    hub = server.api.jobs.hub("two")
    events, _ = hub.wait(0, 0)
    assert events[-1].name == "done" and events[-1].data["removed"]["group"] == b  # open viewers refresh
    status, body, _ = client.request("DELETE", f"/api/cases/two/collections/{b}")
    assert status == 404 and body["error"] == "collection_not_found"
    status, body, _ = client.request("DELETE", "/api/cases/two/collections/x1")
    assert status == 400 and body["error"] == "invalid_field"
    assert client.request("DELETE", f"/api/cases/nope/collections/{a}")[0] == 404


def test_remove_waits_for_jobs_and_never_drops_the_followed_folder(client, server, two):  # noqa: F811
    a = groups(client)["collections"][0]["id"]
    hub = server.api.jobs.hub("two")
    assert hub.try_begin("busy")
    try:
        status, body, _ = client.request("DELETE", f"/api/cases/two/collections/{a}")
        assert status == 409 and body["error"] == "job_running"
    finally:
        hub.finish({"job_id": "busy", "status": "ok"})
    path = str(two[("A", "enterprise")].resolve())
    with server.api.jobs._lock:
        server.api.jobs._watched["two"] = path
    status, body, _ = client.request("DELETE", f"/api/cases/two/collections/{a}")
    assert status == 409 and body["error"] == "collection_followed"
    assert len(groups(client)["collections"]) == 2
