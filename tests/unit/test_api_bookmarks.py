"""/api/bookmarks/*, /api/compare and PUT /api/views/<id> against a real loopback server."""

from __future__ import annotations

import test_api_keys as tak
from test_api import client, mf, server  # noqa: F401  (fixtures)

demo = tak.demo  # fixture: case "Demo" with the synthetic keys case


def make(client, entry_id, **extra):  # noqa: F811
    return client.post("/api/bookmarks", {"case": "Demo", "entry_id": entry_id, **extra})


def test_lifecycle(client, demo):  # noqa: F811
    status, made, _ = make(client, 2, color="red", note="hello")
    assert status == 201 and made["color"] == "red" and made["note"] == "hello" and made["entry_id"] == 2
    status, body, _ = client.get("/api/bookmarks?case=demo")
    assert status == 200 and body == {"bookmarks": [made], "next": None}
    assert client.get(f"/api/bookmarks/{made['id']}?case=Demo")[1] == made
    assert client.get("/api/bookmarks/999?case=Demo")[0] == 404
    assert client.get("/api/bookmarks/abc?case=Demo")[0] == 400
    status, changed, _ = client.request(
        "PATCH", f"/api/bookmarks/{made['id']}", {"case": "Demo", "note": None}
    )
    assert status == 200 and changed["note"] is None and changed["color"] == "red"
    status, changed, _ = client.request(
        "PATCH", f"/api/bookmarks/{made['id']}", {"case": "Demo", "color": "blue"}
    )
    assert changed["color"] == "blue"
    assert client.request("DELETE", f"/api/bookmarks/{made['id']}?case=Demo")[0] == 204
    assert client.get("/api/bookmarks?case=Demo")[1]["bookmarks"] == []
    status, body, _ = client.request("DELETE", f"/api/bookmarks/{made['id']}?case=Demo")
    assert status == 404 and body["error"] == "not_found"


def test_errors(client, demo):  # noqa: F811
    status, first, _ = make(client, 3)
    assert status == 201
    status, body, _ = make(client, 3)
    assert status == 409 and body["error"] == "already_bookmarked" and body["bookmark_id"] == first["id"]
    assert make(client, 9999)[0] == 404
    assert make(client, 1, color="pink")[1]["error"] == "bad_color"
    assert make(client, 1, note="x" * 3000)[1]["error"] == "bad_note"
    assert client.post("/api/bookmarks", {"case": "Demo"})[0] == 400
    assert client.post("/api/bookmarks", {"case": "Demo", "entry_id": "1"})[0] == 400
    assert client.post("/api/bookmarks", {"case": "Demo", "entry_id": True})[0] == 400
    assert client.post("/api/bookmarks", {"entry_id": 1})[0] == 400
    assert client.get("/api/bookmarks?case=nope")[0] == 404
    assert client.get("/api/bookmarks?case=Demo&limit=0")[0] == 400
    assert client.get("/api/bookmarks?case=Demo&after=x")[0] == 400
    assert client.get("/api/bookmarks?case=Demo&color=pink")[0] == 400
    assert client.request("PATCH", "/api/bookmarks/abc", {"case": "Demo", "note": "x"})[0] == 400
    assert (
        client.request("PATCH", f"/api/bookmarks/{first['id']}", {"case": "Demo"})[1]["error"] == "no_changes"
    )
    assert client.request("PATCH", "/api/bookmarks/999", {"case": "Demo", "note": "x"})[0] == 404
    assert client.request("PUT", "/api/bookmarks", {"case": "Demo"})[0] == 405


def test_list_paging_and_filters(client, demo):  # noqa: F811
    for n in range(1, 6):
        make(client, n, color="red" if n % 2 else "green", note=f"n{n}")
    seen, after = [], ""
    while True:
        body = client.get(f"/api/bookmarks?case=Demo&limit=2&after={after}")[1]
        seen += [b["entry_id"] for b in body["bookmarks"]]
        if body["next"] is None:
            break
        after = body["next"]
    assert seen == [1, 2, 3, 4, 5]
    assert len(client.get("/api/bookmarks?case=Demo&color=green")[1]["bookmarks"]) == 2
    assert [b["entry_id"] for b in client.get("/api/bookmarks?case=Demo&q=N3")[1]["bookmarks"]] == [3]


def test_lookup(client, demo):  # noqa: F811
    made = make(client, 4, color="purple", note="x")[1]
    status, body, _ = client.post("/api/bookmarks/lookup", {"case": "Demo", "entry_ids": [3, 4, 5]})
    assert status == 200
    assert body == {"bookmarks": {"4": {"id": made["id"], "color": "purple", "has_note": True}}}
    for bad in ([], None, ["1"], list(range(501))):
        assert client.post("/api/bookmarks/lookup", {"case": "Demo", "entry_ids": bad})[0] == 400


def test_export_import(client, demo, server):  # noqa: F811
    make(client, 2, color="red", note="one")
    make(client, 8)
    status, data, _ = client.get("/api/bookmarks/export?case=Demo")
    assert status == 200 and data["format"] == "dmslog-bookmarks" and len(data["bookmarks"]) == 2
    tak.build(server, "Second")
    status, counts, _ = client.post("/api/bookmarks/import", {"case": "Second", "data": data})
    assert status == 200 and counts["imported"] == 2
    assert client.post("/api/bookmarks/import", {"case": "Second", "data": data})[1]["existing"] == 2
    assert [b["color"] for b in client.get("/api/bookmarks?case=Second")[1]["bookmarks"]] == ["red", "yellow"]
    status, body, _ = client.post("/api/bookmarks/import", {"case": "Second", "data": {"format": "x"}})
    assert status == 400 and body["error"] == "bad_import"
    assert client.post("/api/bookmarks/import", {"case": "Second"})[0] == 400


def test_compare(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/compare?case=Demo&a=1&b=8")
    assert status == 200
    assert body["a"]["id"] == 1 and body["b"]["id"] == 8 and body["a"]["cont_truncated"] is False
    assert body["diff"]["identical"] is False and body["diff"]["ops"]
    same = client.get("/api/compare?case=Demo&a=1&b=1")[1]["diff"]
    assert same["identical"] is True and same["stats"]["changed"] == 0
    assert client.get("/api/compare?case=Demo&a=1&b=9999")[0] == 404
    assert client.get("/api/compare?case=Demo&a=1")[0] == 400
    assert client.get("/api/compare?case=Demo&a=x&b=1")[1]["error"] == "invalid_entry_id"
    assert client.get("/api/compare?case=nope&a=1&b=2")[0] == 404


def test_update_saved_view(client, demo):  # noqa: F811
    flt = {"and": [{"sev": {"le": 2}}]}
    a = client.post("/api/views", {"case": "Demo", "name": "A", "filter": flt})[1]
    b = client.post("/api/views", {"case": "Demo", "name": "B", "filter": flt})[1]
    newer = {"and": [{"sev": {"le": 3}}]}
    status, body, _ = client.request(
        "PUT",
        f"/api/views/{a['id']}",
        {"case": "Demo", "name": " Renamed ", "filter": newer, "columns": ["ts"]},
    )
    assert (
        status == 200 and body["name"] == "Renamed" and body["filter"] == newer and body["columns"] == ["ts"]
    )
    same_name = client.request(
        "PUT", f"/api/views/{a['id']}", {"case": "Demo", "name": "renamed", "filter": flt}
    )
    assert same_name[0] == 200  # a view may keep its own name (case-insensitive)
    status, body, _ = client.request(
        "PUT", f"/api/views/{a['id']}", {"case": "Demo", "name": "b", "filter": flt}
    )
    assert status == 409 and body["error"] == "duplicate_name"
    assert client.request("PUT", "/api/views/999", {"case": "Demo", "name": "Z", "filter": flt})[0] == 404
    assert (
        client.request("PUT", f"/api/views/{b['id']}", {"case": "Demo", "name": "B", "filter": {"zzz": 1}})[0]
        == 400
    )
    assert (
        client.request("PUT", f"/api/views/{b['id']}", {"case": "Demo", "name": "", "filter": flt})[0] == 400
    )


def test_import_route_accepts_an_export_at_the_caps(client, demo, server):  # noqa: F811
    from dmslog.server import api as api_module
    from dmslog.store import bookmarks as bm

    assert api_module.body_limit("/api/bookmarks/import") >= bm.MAX_EXPORT_BYTES + 1024 * 1024
    assert api_module.body_limit("/api/views") == api_module.MAX_BODY_BYTES
    note = "\u00e9" * bm.MAX_NOTE_CHARS
    items = [
        {
            "member": "Log/a.log",
            "line_no": n,
            "color": "red",
            "note": note,
            "created_at": "2026-01-01T00:00:00Z",
        }
        for n in range(1, 301)
    ]
    data = {"format": bm.EXPORT_FORMAT, "version": 1, "bookmarks": items}
    import json

    assert len(json.dumps(data, ensure_ascii=False).encode()) > api_module.MAX_BODY_BYTES
    tak.build(server, "Big")
    status, counts, _ = client.post("/api/bookmarks/import", {"case": "Big", "data": data})
    assert status == 200 and counts["imported"] == 300
    status, exported, _ = client.get("/api/bookmarks/export?case=Big")
    assert status == 200 and exported["truncated"] is False and len(exported["bookmarks"]) == 300
    tak.build(server, "Again")
    assert client.post("/api/bookmarks/import", {"case": "Again", "data": exported})[1]["imported"] == 300
    # other routes keep the small limit
    big = {"case": "Big", "name": "x" * (api_module.MAX_BODY_BYTES + 10), "filter": {"sev": {"le": 1}}}
    assert client.post("/api/views", big)[0] == 413


def test_import_never_500s_on_hostile_items(client, demo):  # noqa: F811
    items = [
        {"member": "Log/a.log", "line_no": 2**63},
        {"member": "Log/a.log", "line_no": 1, "ers_ts": []},
        {"ers_guid": []},
    ]
    status, counts, _ = client.post(
        "/api/bookmarks/import",
        {"case": "Demo", "data": {"format": "dmslog-bookmarks", "version": 1, "bookmarks": items}},
    )
    assert status == 200 and counts["invalid"] == 3
    assert client.post("/api/bookmarks/lookup", {"case": "Demo", "entry_ids": [2**63]})[0] == 400
    assert client.post("/api/bookmarks", {"case": "Demo", "entry_id": 2**63})[0] == 400
