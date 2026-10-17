"""PUT/DELETE /api/cases/<case>/collections/<group>/clock and the reads on adjusted time (ROADMAP 9.15)."""

from __future__ import annotations

import json

import pytest
from test_api import client, server  # noqa: F401  (fixtures)
from test_api_collections import groups, two  # noqa: F401  (fixtures)

from dmslog.store import clock as C
from dmslog.store import query
from dmslog.store.db import connect


def put(client, group, body):  # noqa: F811
    return client.request("PUT", f"/api/cases/two/collections/{group}/clock", body)


def ids(client):  # noqa: F811
    a, b = (g["id"] for g in groups(client)["collections"])
    return a, b


def first_rows(client, group):  # noqa: F811
    _, page, _ = client.post("/api/entries/page", {"case": "two", "filter": {"collection": [group]}})
    return page["rows"]


def test_offset_zero_responses_are_unchanged(client, two):  # noqa: F811
    body = {"case": "two", "filter": {"ts": {"ge": 0}}, "limit": 7}
    _, page, _ = client.post("/api/entries/page", body)
    assert "clock" not in page and all("ts_adjusted" not in r for r in page["rows"])
    _, hist, _ = client.post("/api/histogram", {"case": "two"})
    assert "clock" not in hist
    entry_id = page["rows"][0]["id"]
    _, entry, _ = client.get(f"/api/entries/{entry_id}?case=two")
    assert "ts_adjusted" not in entry and "clock_offset_ms" not in entry
    listing = groups(client)
    assert listing["clocks"] == "raw" and all(g["clock_source"] is None for g in listing["collections"])


def test_a_pair_aligns_the_target_collection(client, two):  # noqa: F811
    a, b = ids(client)
    anchor = first_rows(client, a)[1]
    target = next(r for r in first_rows(client, b) if r["ts"] is not None and r["ts"] != anchor["ts"])
    status, out, _ = put(client, b, {"anchor": anchor["id"], "target": target["id"], "dry_run": True})
    assert status == 200 and not out["applied"]
    assert out["clock_offset_ms"] == anchor["ts"] - target["ts"]
    assert groups(client)["clocks"] == "raw"
    status, out, _ = put(client, b, {"anchor": anchor["id"], "target": target["id"]})
    assert status == 200 and out["applied"] and out["target"]["ts_adjusted"] == anchor["ts"]
    offset = out["clock_offset_ms"]
    listing = groups(client)
    assert listing["clocks"] == "adjusted" and listing["ordering"] == "approximate"
    shifted = next(g for g in listing["collections"] if g["id"] == b)
    assert shifted["clock_offset_ms"] == offset and shifted["clock_source"]["method"] == "pair"
    assert shifted["clock_source"]["target"]["line_no"] == target["line_no"]

    _, page, _ = client.post("/api/entries/page", {"case": "two", "limit": 500, "columns": ["collection"]})
    assert page["clock"] == "adjusted"
    rows = page["rows"]
    for row in rows:
        assert row["ts_adjusted"] == row["ts"] + (offset if row["collection"] == b else 0)
    keys = [(r["ts_adjusted"], r["id"]) for r in rows if r["ts"] is not None]
    assert keys == sorted(keys)
    # a time filter is on adjusted time, and so is the cursor
    t = anchor["ts"]
    _, page, _ = client.post(
        "/api/entries/page", {"case": "two", "filter": {"ts": t}, "columns": ["collection"]}
    )
    assert {r["id"] for r in page["rows"]} >= {anchor["id"], target["id"]}
    _, count, _ = client.post("/api/entries/count", {"case": "two", "filter": {"ts": t}})
    assert count["count"] == len(page["rows"])
    _, jump, _ = client.post("/api/entries/page", {"case": "two", "cursor": f"{t}:-1", "limit": 3})
    assert jump["rows"][0]["ts_adjusted"] == t
    _, hist, _ = client.post("/api/histogram", {"case": "two", "bucket_ms": 1000})
    assert hist["clock"] == "adjusted" and hist["total"] == sum(1 for r in rows if r["ts"] is not None)
    _, entry, _ = client.get(f"/api/entries/{target['id']}?case=two")
    assert (entry["ts"], entry["ts_adjusted"], entry["clock_offset_ms"]) == (target["ts"], t, offset)
    _, ctx, _ = client.get(f"/api/entries/{target['id']}/context?case=two&same=merge&before=2&after=2")
    assert all("ts_adjusted" in r for r in ctx["rows"]) and ctx["rows"][ctx["anchor"]]["id"] == target["id"]
    status, nxt, _ = client.post(
        "/api/entries/highlight/next", {"case": "two", "filter": None, "rules": [{"collection": [b]}]}
    )
    assert status == 200 and nxt["row"]["ts_adjusted"] == min(
        r["ts_adjusted"] for r in rows if r["collection"] == b
    )

    status, out, _ = client.request("DELETE", f"/api/cases/two/collections/{b}/clock")
    assert status == 200 and (out["clock_offset_ms"], out["previous_ms"]) == (0, offset)
    assert groups(client)["clocks"] == "raw"


def test_manual_offset(client, two):  # noqa: F811
    _a, b = ids(client)
    status, out, _ = put(client, b, {"offset_ms": -2500, "case": "ignored"})
    assert status == 200 and out["clock_offset_ms"] == -2500 and out["clock_source"]["method"] == "manual"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({}, 400, "bad_request"),
        ({"offset_ms": "5"}, 400, "bad_offset"),
        ({"offset_ms": C.MAX_OFFSET_MS + 1}, 400, "bad_offset"),
        ({"anchor": "x", "target": 1}, 400, "bad_entry"),
        ({"anchor": 999_999, "target": 1}, 404, "entry_not_found"),
    ],
)
def test_bad_bodies(client, two, body, status, code):  # noqa: F811
    _a, b = ids(client)
    got, out, _ = put(client, b, body)
    assert (got, out["error"]) == (status, code)


def test_pair_errors_and_unknown_collections(client, two):  # noqa: F811
    a, b = ids(client)
    ra, rb = first_rows(client, a)[0], first_rows(client, b)[0]
    assert put(client, b, {"anchor": rb["id"], "target": rb["id"]})[1]["error"] == "same_collection"
    assert put(client, b, {"anchor": rb["id"], "target": ra["id"]})[1]["error"] == "target_not_in_collection"
    status, out, _ = put(client, 9999, {"offset_ms": 1})
    assert (status, out["error"]) == (404, "collection_not_found")
    status, out, _ = client.request("DELETE", "/api/cases/two/collections/x/clock")
    assert (status, out["error"]) == (400, "invalid_field")
    status, out, _ = client.request("PUT", "/api/cases/nope/collections/1/clock", {"offset_ms": 1})
    assert status == 404


def test_reads_equal_the_store_functions(client, two, tmp_path):  # noqa: F811
    _a, b = ids(client)
    put(client, b, {"offset_ms": 1234})
    _, page, _ = client.post("/api/entries/page", {"case": "two", "limit": 50})
    db = next((tmp_path / "data").rglob("*.sqlite"))
    conn = connect(db, readonly=True)
    try:
        mine = C.page(conn, None, None, "fwd", 50, clock=C.load(conn))
        assert json.loads(json.dumps(mine.rows)) == page["rows"]
        assert query.page(conn, None, None, "fwd", 50).rows != mine.rows
    finally:
        conn.close()
