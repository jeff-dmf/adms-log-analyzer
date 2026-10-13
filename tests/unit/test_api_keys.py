"""GET /api/keys/*, /api/entries/<id>/links and /side-by-side against a real loopback server."""

from __future__ import annotations

from urllib.parse import quote

import pytest
import test_store_keys as tsk
from test_api import client, mf, server  # noqa: F401  (fixtures)

from dmslog.store import create_case_db


def build(server, name, cont_rows=()):  # noqa: F811
    case_dir = server.api.store.root / name
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    tsk.seed(conn)
    for entry_id, cont in cont_rows:
        conn.execute("INSERT INTO entry_body VALUES (?, ?)", (entry_id, cont))
    conn.commit()
    conn.close()


@pytest.fixture
def demo(server):  # noqa: F811
    build(server, "Demo", [(1, "\n".join(f"line {i}" for i in range(50))), (8, "sens cont")])
    return "Demo"


def invalid(client, path, error="invalid_field"):  # noqa: F811
    status, body, _ = client.get(path)
    assert status == 400 and body["error"] == error, (path, status, body)


def test_key_types(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/keys/types?case=demo")
    assert status == 200
    assert body["types"] == [
        {"type": "errcode", "count": 2, "values": 2},
        {"type": "msgid", "count": 13, "values": 2},
    ]


def test_key_records_pagination_over_http(client, demo):  # noqa: F811
    full = client.get("/api/keys/msgid/M-1?case=Demo&limit=1000")[1]
    assert full["next"] is None and len(full["records"]) == 12
    seen, after = [], ""
    while True:
        status, body, _ = client.get(f"/api/keys/msgid/M-1?case=Demo&limit=5&after={quote(after)}")
        assert status == 200
        seen += [r["id"] for r in body["records"]]
        if body["next"] is None:
            break
        after = body["next"]
    assert seen == [r["id"] for r in full["records"]] and len(set(seen)) == 12
    assert {"id", "source_id", "member", "family", "process", "sensitive", "ts", "tid", "level", "sev",
            "tpl_code", "msg", "collection"} == set(full["records"][0])  # fmt: skip


def test_key_value_with_odd_characters(client, demo):  # noqa: F811
    value = quote("50%/ \u00e9", safe="")
    status, body, _ = client.get(f"/api/keys/errcode/{value}?case=Demo")
    assert status == 200 and [r["id"] for r in body["records"]] == [2]
    status, body, _ = client.get(f"/api/keys/errcode/{quote(tsk.HOSTILE, safe='')}?case=Demo")
    assert status == 200 and [r["id"] for r in body["records"]] == [3]
    status, body, _ = client.get("/api/keys/errcode/nothing-here?case=Demo")
    assert status == 200 and body == {"records": [], "next": None}


def test_key_validation(client, demo):  # noqa: F811
    invalid(client, "/api/keys/msgid/%FF%FE?case=Demo")  # not UTF-8
    invalid(client, "/api/keys/msgid/%00?case=Demo")  # control character
    invalid(client, "/api/keys/msgid/" + "a" * 257 + "?case=Demo")
    invalid(client, "/api/keys/msgid/" + "%41" * 257 + "?case=Demo")
    invalid(client, "/api/keys/bad%20type/M-1?case=Demo")
    invalid(client, "/api/keys/msgid/M-1?case=Demo&limit=0")
    invalid(client, "/api/keys/msgid/M-1?case=Demo&limit=1001")
    invalid(client, "/api/keys/msgid/M-1?case=Demo&limit=x")
    invalid(client, "/api/keys/msgid/M-1?case=Demo&after=garbage")
    invalid(client, f"/api/keys/msgid/M-1?case=Demo&after={2**70}:1")
    status, body, _ = client.get("/api/keys/msgid/M-1?case=Nope")
    assert status == 404


def test_no_cross_case_leakage(client, server, demo):  # noqa: F811
    build(server, "Other")
    case_dir = server.api.store.root / "Other"
    import sqlite3

    conn = sqlite3.connect(case_dir / "case.sqlite")
    conn.execute("DELETE FROM entry_key WHERE key_type = 'msgid' AND entry_id > 2")
    conn.execute("DELETE FROM link")
    conn.commit()
    conn.close()
    assert len(client.get("/api/keys/msgid/M-1?case=Other")[1]["records"]) == 2
    assert len(client.get("/api/keys/msgid/M-1?case=Demo&limit=1000")[1]["records"]) == 12
    assert client.get("/api/entries/1/links?case=Other")[1]["links"] == []
    assert len(client.get("/api/entries/1/links?case=Demo")[1]["links"]) == 4


def test_entry_links_both_directions(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/1/links?case=Demo")
    assert status == 200
    kinds = {(x["entry_id"], x["direction"]) for x in body["links"]}
    assert kinds == {(6, "out"), (7, "out"), (8, "out"), (3, "in")}
    status, body, _ = client.get("/api/entries/8/links?case=demo")
    assert [(x["entry_id"], x["kind"], x["direction"]) for x in body["links"]] == [
        (1, "normal_sensitive", "in")
    ]
    assert body["links"][0]["source"] == {"member": "Log/a.log", "sensitive": False}
    assert client.get("/api/entries/12/links?case=Demo")[1] == {"links": []}


def test_entry_links_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/999/links?case=Demo")
    assert status == 404 and body["error"] == "entry_not_found"
    invalid(client, "/api/entries/abc/links?case=Demo", "invalid_entry_id")
    assert client.get("/api/entries/1/links")[0] == 400


def test_side_by_side_orders_normal_left(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/1/side-by-side?case=Demo")
    assert status == 200 and body["kind"] == "normal_sensitive" and body["confidence"] == 3
    assert (body["a"]["id"], body["a"]["sensitive"]) == (1, False)
    assert (body["b"]["id"], body["b"]["sensitive"]) == (8, True)
    assert body["a"]["text"].endswith("line 49") and body["b"]["text"].endswith("sens cont")
    # asked from the sensitive side: still normal on the left
    status, body, _ = client.get("/api/entries/8/side-by-side?case=Demo")
    assert status == 200 and (body["a"]["id"], body["b"]["id"]) == (1, 8)


def test_side_by_side_same_sensitivity_keeps_requested_first(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/10/side-by-side?case=Demo")
    assert status == 200 and body["kind"] == "callback"
    assert (body["a"]["id"], body["b"]["id"]) == (10, 9)


def test_side_by_side_errors_and_cont_lines(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/12/side-by-side?case=Demo")
    assert status == 404 and body["error"] == "no_counterpart"
    assert client.get("/api/entries/999/side-by-side?case=Demo")[1]["error"] == "entry_not_found"
    status, body, _ = client.get("/api/entries/1/side-by-side?case=Demo&cont_lines=3")
    assert body["a"]["cont_truncated"] is True and body["a"]["text"].endswith("line 2")
    invalid(client, "/api/entries/1/side-by-side?case=Demo&cont_lines=0")


def test_entry_endpoint_cont_lines(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/1?case=Demo")
    assert status == 200 and "cont_truncated" not in body and body["text"].endswith("line 49")
    status, body, _ = client.get("/api/entries/1?case=Demo&cont_lines=40")
    assert body["cont_truncated"] is True and body["text"].endswith("line 39")
    invalid(client, "/api/entries/1?case=Demo&cont_lines=abc")
