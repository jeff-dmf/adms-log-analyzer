"""The message-queue routes, the job hook and the CLI flag (issue #74, part B). Everything is synthetic."""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import multi_helpers as mh
from test_api import client, server  # noqa: F401  (fixtures)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import lps_fixtures as fx  # noqa: E402

from dmslog.cli import main  # noqa: E402

CASE = "q"


def ers_with_queue(tmp_path: Path, *, deleted: tuple[int, ...] = (2,)) -> Path:
    """A synthetic capture (enterprise half) with one queue database whose WAL deletes ``deleted``."""
    path = mh.build_capture(tmp_path / "zip", "A", "enterprise")
    bank = fx.make_bank(
        tmp_path / "bank",
        [fx.SynthItem(1), fx.SynthItem(2), fx.SynthItem(3, status="Sent")],
        wal_deletes=deleted,
    )
    with zipfile.ZipFile(path, "a") as zf:
        zf.write(bank.db, f"{fx.STORE_DIR}/{fx.CACHE_NAME}")
        if bank.wal is not None:
            zf.write(bank.wal, f"{fx.STORE_DIR}/{fx.CACHE_NAME}-wal")
    return path


def add(client, path, **extra):  # noqa: F811
    assert client.post("/api/cases", {"name": CASE})[0] == 201
    status, body, _ = client.post(f"/api/cases/{CASE}/collections", {"path": str(path), **extra})
    return status, body


def group_of_case(client) -> int:  # noqa: F811
    _, body, _ = client.get(f"/api/cases/{CASE}/collections")
    return body["collections"][0]["group"]


def summary(client):  # noqa: F811
    status, body, _ = client.get(f"/api/queue/summary?case={CASE}")
    assert status == 200
    return body


def test_the_queue_is_off_by_default(client, tmp_path):  # noqa: F811
    assert add(client, ers_with_queue(tmp_path))[0] == 202
    client.wait_done(CASE)
    body = summary(client)
    assert body["enabled"] is False and body["dbs"] == [] and body["totals"]["items"] == 0
    assert client.get(f"/api/queue/items?case={CASE}")[1]["items"] == []


def test_a_bad_option_is_refused(client, tmp_path):  # noqa: F811
    assert client.post("/api/cases", {"name": CASE})[0] == 201
    path = ers_with_queue(tmp_path)
    status, body, _ = client.post(
        f"/api/cases/{CASE}/collections", {"path": str(path), "message_queue": "yes"}
    )
    assert status == 400 and body["error"] == "bad_option"


def test_reading_with_the_option_applies_the_wal_and_lists_the_items(client, tmp_path):  # noqa: F811
    status, body = add(client, ers_with_queue(tmp_path), message_queue=True)
    assert status == 202
    client.wait_done(CASE)
    body = summary(client)
    assert body["enabled"] is True
    (bank,) = body["dbs"]
    assert (bank["status"], bank["snapshot"], bank["items"]) == (
        "ready",
        "wal_applied",
        2,
    )  # item 2 left in the WAL
    assert "/" not in bank["name"] and "\\" not in bank["name"]  # a name, never a path
    _, page, _ = client.get(f"/api/queue/items?case={CASE}")
    assert sorted(i["item_id"] for i in page["items"]) == ["1", "3"]
    sent = {i["item_id"]: i["state"] for i in page["items"]}
    assert sent == {"1": "pending", "3": "sent"}
    _, only_pending, _ = client.get(f"/api/queue/items?case={CASE}&state=pending")
    assert [i["item_id"] for i in only_pending["items"]] == ["1"]
    dumped = json.dumps([body, page])
    assert fx.USERNAME_PREFIX not in dumped  # the account column is never read


def test_the_preview_is_a_labeled_reconstruction(client, tmp_path):  # noqa: F811
    add(client, ers_with_queue(tmp_path), message_queue=True)
    client.wait_done(CASE)
    _, page, _ = client.get(f"/api/queue/items?case={CASE}")
    row = page["items"][0]
    assert row["preview"] == "available"
    status, body, _ = client.get(f"/api/queue/items/{row['id']}/preview?case={CASE}")
    assert status == 200 and body["decode"]["ok"] is True and body["available"] is True
    assert body["formatted"]["ok"] is True and "<" in body["formatted"]["text"]
    assert body["reconstruction"]["basis"] in {"catalog", "exemplar", "none"}
    stored = client.get(f"/api/queue/items/{row['id']}/preview?case={CASE}&form=stored")
    assert stored[0] == 200 and fx.USERNAME_PREFIX not in json.dumps(stored[1])
    assert client.get(f"/api/queue/items/{row['id']}/preview?case={CASE}&form=zzz")[0] == 400
    assert client.get(f"/api/queue/items/999999/preview?case={CASE}")[0] == 404


def test_reading_a_collection_added_without_the_option(client, tmp_path):  # noqa: F811
    add(client, ers_with_queue(tmp_path))
    client.wait_done(CASE)
    group = group_of_case(client)
    status, body, _ = client.post(f"/api/cases/{CASE}/queue/read", {"group": group})
    assert status == 202 and body["job_id"]
    client.wait_done(CASE)
    assert summary(client)["enabled"] is True and summary(client)["totals"]["items"] == 2
    assert client.post(f"/api/cases/{CASE}/queue/read", {"group": "x"})[0] == 400
    assert client.post(f"/api/cases/{CASE}/queue/read", {"group": 999})[0] == 404


def test_deleting_the_queue_data_keeps_the_option(client, tmp_path):  # noqa: F811
    add(client, ers_with_queue(tmp_path), message_queue=True)
    client.wait_done(CASE)
    group = group_of_case(client)
    assert client.request("DELETE", f"/api/cases/{CASE}/queue?group=999")[0] == 404
    assert client.request("DELETE", f"/api/cases/{CASE}/queue?group=x")[0] == 400
    status, body, _ = client.request("DELETE", f"/api/cases/{CASE}/queue?group={group}")
    assert status == 200 and (body["dbs"], body["items"]) == (1, 2)
    after = summary(client)
    assert after["enabled"] is True and after["dbs"] == [] and after["totals"]["items"] == 0


def test_the_item_filters_validate_their_input(client, tmp_path):  # noqa: F811
    add(client, ers_with_queue(tmp_path), message_queue=True)
    client.wait_done(CASE)
    for query in ("limit=0", "state=zzz", "limit=x"):
        status, body, _ = client.get(f"/api/queue/items?case={CASE}&{query}")
        assert status == 400 and body["error"].startswith("bad_"), query


def test_the_cli_reads_the_queue_only_with_the_flag(tmp_path, capsys):
    data = tmp_path / "data"
    path = ers_with_queue(tmp_path)
    assert main(["--data-dir", str(data), "ingest", "--message-queue", "--case", "withq", str(path)]) == 0
    assert "Message queue: 1 databases, 2 items (1 pending)" in capsys.readouterr().out
    assert main(["--data-dir", str(data), "ingest", "--case", "plain", str(path)]) == 0
    assert "Message queue" not in capsys.readouterr().out
    for case, expected in (("withq", 2), ("plain", 0)):
        db_file = next((data / "cases").glob(f"{case}*/case.sqlite"))
        with sqlite3.connect(f"file:{db_file}?mode=ro", uri=True) as conn:
            assert conn.execute("SELECT count(*) FROM queue_item").fetchone()[0] == expected
