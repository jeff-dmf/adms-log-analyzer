"""GET /api/payloads*, /api/entries/<id>/payloads against a real loopback server and a synthetic case."""

from __future__ import annotations

import sys
import types

import pytest
import test_store_payloads as tsp
from test_api import client, mf, server  # noqa: F401  (fixtures)

from dmslog.store import create_case_db


def make_case(server, name, seeder=tsp.seed):  # noqa: F811
    case_dir = server.api.store.root / name
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    seeder(conn)
    conn.close()
    return name


@pytest.fixture
def demo(server):  # noqa: F811
    return make_case(server, "Demo")


def invalid(client, path):  # noqa: F811
    status, body, _ = client.get(path)
    assert status == 400 and body["error"] == "invalid_field", (path, status, body)


def stub_pretty(monkeypatch, func):
    module = types.ModuleType("dmslog.payload.pretty")
    module.format_payload = func
    monkeypatch.setitem(sys.modules, "dmslog.payload.pretty", module)


def test_page_all_and_canonical_case(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/payloads/page?case=demo&limit=1000")
    assert status == 200 and body["next"] is None and len(body["payloads"]) == 14
    assert {"id", "role", "reason", "member", "sensitive"} <= set(body["payloads"][0])


def test_page_keyset_pagination_over_http(client, demo):  # noqa: F811
    full = client.get("/api/payloads/page?case=Demo&limit=1000")[1]["payloads"]
    seen, after = [], ""
    while True:
        status, body, _ = client.get(f"/api/payloads/page?case=Demo&limit=3&after={after}")
        assert status == 200
        seen += [p["id"] for p in body["payloads"]]
        if body["next"] is None:
            break
        after = body["next"]
    assert seen == [p["id"] for p in full] and len(set(seen)) == len(seen) == 14


def test_page_filters(client, demo):  # noqa: F811
    def ids(qs):
        status, body, _ = client.get(f"/api/payloads/page?case=Demo&limit=1000&{qs}")
        assert status == 200
        return [p["id"] for p in body["payloads"]]

    assert ids("role=response") == [2]
    assert ids("role=fault,response") == [2, 3]
    assert ids("direction=out") == [2, 14]
    assert ids("peer=CRS,WMS") == [3, 5, 7, 9, 14]
    assert ids("operation=Other") == [8, 9, 10]
    assert ids("fmt=xml_seq") == [14]
    assert ids("event=1") == [1, 2, 13]
    assert ids("q=m-1") == [1, 2]
    assert ids("orphan=0") == [1, 2, 13]
    assert len(ids("orphan=1")) == 11
    assert ids("orphan=1&role=response") == []


def test_orphan_listing_reports_reasons(client, demo):  # noqa: F811
    rows = client.get("/api/payloads/page?case=Demo&orphan=1&limit=1000")[1]["payloads"]
    assert {p["reason"] for p in rows} == {"no_operation_for_key", "not_in_operation", "ambiguous", None}
    assert all(p["event_id"] is None for p in rows)


def test_page_validation(client, demo):  # noqa: F811
    for qs in (
        "limit=0",
        "limit=1001",
        "limit=abc",
        "after=zzz",
        "after=1",
        "orphan=2",
        "orphan=yes",
        "event=x",
        "event=-1",
        "role=a,,b",
        "peer=" + ",".join(f"p{i}" for i in range(30)),
        "operation=" + "x" * 200,
        "q=" + "x" * 200,
        "q=a%00b",
    ):
        invalid(client, f"/api/payloads/page?case=Demo&{qs}")


def test_injection_text_is_harmless_over_http(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/payloads/page?case=Demo&role=%27%20OR%201%3D1%20--&q=%27%3B--")
    assert status == 200 and body["payloads"] == []
    assert len(client.get("/api/payloads/page?case=Demo&limit=1000")[1]["payloads"]) == 14


def test_unknown_and_missing_case(client, demo):  # noqa: F811
    assert client.get("/api/payloads/page?case=nope")[0] == 404
    assert client.get("/api/payloads/1?case=nope")[0] == 404
    assert client.get("/api/entries/10/payloads?case=nope")[0] == 404
    assert client.get("/api/payloads/page")[0] in (400, 404)
    assert client.get("/api/payloads/1")[0] in (400, 404)


def test_get_payload_raw_with_pair_and_entry(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/payloads/1?case=Demo")
    assert status == 200
    assert body["text"] == "<SendWorksRequest><ID>1</ID></SendWorksRequest>"
    assert body["formatted"] is None and body["truncated_display"] is False
    assert body["payload"]["id"] == 1 and body["payload"]["well_formed"] is True
    assert body["pair"]["id"] == 2 and body["pair"]["role"] == "response"
    assert body["entry"] == {
        "id": 10, "ts": tsp.T0, "tid": 7, "member": "SVC/Wfm.sensitive.log", "sensitive": True,
    }  # fmt: skip
    assert client.get("/api/payloads/3?case=Demo")[1]["pair"] is None
    assert client.get("/api/payloads/1?case=Demo&format=raw")[1]["formatted"] is None


def test_get_payload_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/payloads/999?case=Demo")
    assert status == 404 and body["error"] == "payload_not_found"
    for bad in ("abc", "-1", "1.5", "99999999999999999999999"):
        status, body, _ = client.get(f"/api/payloads/{bad}?case=Demo")
        assert status == 400 and body["error"] == "invalid_field", bad
    invalid(client, "/api/payloads/1?case=Demo&format=html")
    invalid(client, "/api/payloads/1?case=Demo&format=")


def test_pretty_uses_the_formatter(client, demo, monkeypatch):  # noqa: F811
    calls = []

    def fake(text, fmt, *, max_chars=1_000_000):
        calls.append((text, fmt))
        return types.SimpleNamespace(
            text="PRETTY:" + text, ok=True, kind="xml", reason=None, truncated_display=False
        )

    stub_pretty(monkeypatch, fake)
    status, body, _ = client.get("/api/payloads/1?case=Demo&format=pretty")
    assert status == 200 and calls == [("<SendWorksRequest><ID>1</ID></SendWorksRequest>", "xml")]
    assert body["formatted"] == {
        "text": "PRETTY:" + body["text"], "ok": True, "kind": "xml", "reason": None,
        "truncated_display": False,
    }  # fmt: skip
    assert body["text"].startswith("<SendWorksRequest>")  # raw text still present


def test_pretty_failure_is_never_a_500(client, demo, monkeypatch):  # noqa: F811
    def boom(text, fmt, *, max_chars=1_000_000):
        raise RuntimeError("bug in the formatter")

    stub_pretty(monkeypatch, boom)
    status, body, _ = client.get("/api/payloads/1?case=Demo&format=pretty")
    assert status == 200
    assert body["formatted"]["ok"] is False and body["formatted"]["reason"] == "format_error"
    assert body["formatted"]["text"] == body["text"]


def test_pretty_when_the_formatter_module_is_missing(client, demo, monkeypatch):  # noqa: F811
    monkeypatch.setitem(sys.modules, "dmslog.payload.pretty", None)  # makes the import fail
    status, body, _ = client.get("/api/payloads/1?case=Demo&format=pretty")
    assert status == 200 and body["formatted"]["reason"] == "format_error"


def test_script_and_entities_come_back_literal_in_json(client, demo):  # noqa: F811
    status, body, headers = client.get("/api/payloads/12?case=Demo")
    assert status == 200 and body["text"] == tsp.SCRIPT_XML
    assert "application/json" in headers["content-type"]
    assert headers.get("x-content-type-options", "nosniff").lower() == "nosniff"


def test_large_payload_over_http(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/payloads/11?case=Demo")
    assert status == 200 and len(body["text"]) == body["payload"]["char_len"] > 170_000
    assert body["truncated_display"] is False and body["text"].endswith("</Table>")


def test_entry_payloads(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/entries/10/payloads?case=Demo")
    assert status == 200 and [p["id"] for p in body["payloads"]] == [1]
    assert [p["id"] for p in client.get("/api/entries/1/payloads?case=Demo")[1]["payloads"]] == [1]
    assert client.get("/api/entries/30/payloads?case=Demo")[1]["payloads"][0]["id"] == 14
    assert client.get("/api/entries/20/payloads?case=Demo")[1]["payloads"][0]["id"] == 11
    assert client.get("/api/entries/999/payloads?case=Demo")[0] == 404
    status, body, _ = client.get("/api/entries/abc/payloads?case=Demo")
    assert status == 400 and body["error"] == "invalid_entry_id"


def test_entry_detail_has_payload_count(client, demo):  # noqa: F811
    assert client.get("/api/entries/10?case=Demo")[1]["payload_count"] == 1
    assert client.get("/api/entries/1?case=Demo")[1]["payload_count"] == 1
    assert client.get("/api/entries/18?case=Demo")[1]["payload_count"] == 1
    assert client.get("/api/entries/19?case=Demo")[1]["payload_count"] == 1


def test_no_cross_case_leakage(server, client, demo):  # noqa: F811
    def other(conn):
        conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'ers_zip', 'y.zip', 'now')")
        conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, msg)"
            " VALUES (500, 1, 1, 0, 1, 5, 4, 0, 'Message content: <Other/>')"
        )
        tsp.add_payload(conn, 500, 500, "<Other/>", role="request", operation="OtherCaseOp")
        conn.commit()

    make_case(server, "Second", other)
    rows = client.get("/api/payloads/page?case=Second&limit=1000")[1]["payloads"]
    assert [p["id"] for p in rows] == [500]
    assert client.get("/api/payloads/500?case=Demo")[0] == 404
    assert client.get("/api/payloads/1?case=Second")[0] == 404
    assert client.get("/api/entries/500/payloads?case=Demo")[0] == 404
    assert client.get("/api/payloads/500?case=Second")[1]["text"] == "<Other/>"
    assert client.get("/api/payloads/page?case=Demo&operation=OtherCaseOp")[1]["payloads"] == []
