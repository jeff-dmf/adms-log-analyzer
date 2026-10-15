"""Partial open through the API (ROADMAP 9.9, ARQUITETURA 9.3b): plan, first pass, progress and resume."""

from __future__ import annotations

import pytest
from test_api import client, server  # noqa: F401  (fixtures)
from test_partial_open import LOGDIR, WFM, make_log, ms, write_folder

ONT = f"{LOGDIR}/OntAdapter_2222.log"


@pytest.fixture
def folder(tmp_path):
    return write_folder(tmp_path / "Log", {WFM: make_log(200), ONT: make_log(50, pid=2222)})


def test_plan_lists_the_sources_before_indexing(client, folder):  # noqa: F811
    assert client.post("/api/cases", {"name": "p"})[0] == 201
    status, body, _ = client.post("/api/cases/p/collections/plan", {"path": str(folder)})
    assert status == 200
    rows = {r["member"]: r for r in body["sources"]}
    assert set(rows) == {WFM, ONT}
    assert set(rows[WFM]) == {
        "member", "name", "family", "process", "pid", "sensitive", "rotation", "size", "selected",
        "indexable", "first_ts", "last_ts",
    }  # fmt: skip
    assert (rows[WFM]["first_ts"], rows[WFM]["last_ts"]) == (ms(0), ms(199_000))
    _, cases, _ = client.get("/api/cases")
    assert next(c for c in cases["cases"] if c["id"] == "p")["ingesting"] is False
    assert (
        client.post("/api/cases/p/collections/plan", {"path": "relative"})[1]["error"] == "path_not_absolute"
    )
    assert client.post("/api/cases/p/collections/plan", {"path": str(folder), "scope": "x"})[1]["error"] == (
        "bad_scope"
    )


@pytest.mark.parametrize(
    "first",
    [{}, [], {"from": "1"}, {"from": True}, {"from": 5, "to": 5}, {"sources": []}, {"sources": [1]},
     {"sources": ["a"] * 501}, {"x": 1}, {"from": 2**63}],
)  # fmt: skip
def test_bad_first_is_refused(client, folder, first):  # noqa: F811
    assert client.post("/api/cases", {"name": "p"})[0] == 201
    status, body, _ = client.post("/api/cases/p/collections", {"path": str(folder), "first": first})
    assert (status, body["error"]) == (400, "bad_first")


def test_first_pass_reports_first_ready_then_done(client, server, folder):  # noqa: F811
    assert client.post("/api/cases", {"name": "p"})[0] == 201
    first = {"from": ms(100_000), "to": ms(120_000), "sources": [WFM, "nope.log"]}
    status, body, _ = client.post("/api/cases/p/collections", {"path": str(folder), "first": first})
    assert status == 202
    client.wait_done("p")
    events, _ = server.api.jobs.hub("p").wait(0, 0)
    statuses = [(e.name, e.data.get("status"), e.data.get("stage")) for e in events]
    assert ("progress", "first_ready", "first") in statuses
    assert statuses.index(("progress", "first_ready", "first")) < len(statuses) - 1
    done = events[-1].data
    assert done["status"] == "ok" and done["partial_left"] == 0 and done["first_unmatched"] == ["nope.log"]
    _, page, _ = client.post("/api/entries/count", {"case": "p", "filter": {}})
    assert page["count"] == 250
    status, body, _ = client.get("/api/cases/p/pending")
    assert (status, body["pending"], body["running"]) == (200, [], False)
    assert client.post("/api/cases/p/resume", {})[:2] == (200, {"job_id": None})


def test_resume_completes_an_interrupted_partial_open(client, server, folder):  # noqa: F811
    from dmslog.ingest.partial import FirstPass
    from dmslog.ingest.pipeline import IngestOptions, run_ingest
    from dmslog.store import create_case_db

    assert client.post("/api/cases", {"name": "p"})[0] == 201
    conn = create_case_db(server.api.store.db_path("p"))
    try:  # what a server stopped after the first pass leaves behind
        run_ingest(
            conn,
            folder,
            IngestOptions(first=FirstPass(ts_from=ms(10_000), ts_to=ms(20_000), defer_rest=True)),
        )
    finally:
        conn.close()
    status, body, _ = client.get("/api/cases/p/pending")
    assert status == 200 and [(p["partial"], p["pending"], p["scope"]) for p in body["pending"]] == [
        (2, 0, "default")
    ]
    status, body, _ = client.post("/api/cases/p/resume", {})
    assert status == 202 and body["job_id"]
    client.wait_done("p")
    _, page, _ = client.post("/api/entries/count", {"case": "p", "filter": {}})
    assert page["count"] == 250
    assert client.get("/api/cases/p/pending")[1]["pending"] == []
