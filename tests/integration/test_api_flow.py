"""End-to-end flow of the API: create case, ingest in the background, follow SSE, query."""

import http.client
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dmslog.server import sse
from dmslog.server.app import DmslogServer

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)


class Session:
    def __init__(self, server):
        self.server = server
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
        start = urlsplit(server.start_url)
        conn.request("GET", f"{start.path}?{start.query}")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]

    def call(self, method, path, body=None):
        headers = {"Cookie": self.cookie, "Origin": f"http://127.0.0.1:{self.server.port}"}
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        try:
            conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = conn.getresponse()
            data = response.read()
            return response.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def open_sse(self, case):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        conn.request("GET", f"/api/cases/{case}/progress", headers={"Cookie": self.cookie})
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type").startswith("text/event-stream")
        assert response.getheader("Cache-Control") == "no-store"
        return conn, response

    def events_until_done(self, case):
        conn, response = self.open_sse(case)
        events = []
        try:
            name = None
            while True:
                line = response.fp.readline().decode().rstrip("\n")
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    events.append((name, json.loads(line[6:])))
                    if name == "done":
                        return events
                elif line == "" and response.fp.closed:
                    raise AssertionError("stream closed early")
        finally:
            conn.close()


@pytest.fixture
def server(tmp_path):
    srv = DmslogServer(0, data_dir=tmp_path / "data")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


@pytest.fixture
def session(server):
    return Session(server)


def handler_threads():
    return {t for t in threading.enumerate() if "process_request_thread" in t.name}


def big_log(tmp_path, mb=12):
    path = tmp_path / "Big_4242.log"
    path.write_bytes(mf.generate_perf_log(mb * 1_000_000))
    return path


def test_full_flow_with_sse_progress(session, tmp_path):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert session.call("POST", "/api/cases", {"name": "flow"}) == (201, {"id": "flow", "name": "flow"})
    # Subscribe first: the hub replays the history of the job, so a late subscriber also sees it all.
    status, body = session.call(
        "POST", "/api/cases/flow/collections", {"path": str(zpath), "scope": "default"}
    )
    assert status == 202
    events = session.events_until_done("flow")
    names = [n for n, _ in events]
    assert names[-1] == "done" and "progress" in names
    done = events[-1][1]
    assert done["job_id"] == body["job_id"] and done["status"] == "ok"
    progress = [d for n, d in events if n == "progress"]
    assert all(
        set(d) == {"job_id", "source", "bytes_done", "bytes_total", "records", "status", "stage"}
        and d["stage"] is None
        for d in progress
    )
    assert progress[-1]["records"] == 31 and progress[-1]["bytes_done"] == progress[-1]["bytes_total"]

    status, sources = session.call("GET", "/api/sources?case=flow")
    ready = [s for s in sources["sources"] if s["status"] == "ready"]
    assert status == 200 and sum(s["records"] for s in ready) == 31
    status, page = session.call("POST", "/api/entries/page", {"case": "flow", "limit": 500})
    assert len(page["rows"]) == 31 and page["next"] is None
    assert session.call("POST", "/api/entries/count", {"case": "flow", "filter": None})[1]["count"] == 31
    mid = page["rows"][15]["id"]
    status, entry = session.call("GET", f"/api/entries/{mid}?case=flow")
    assert status == 200 and entry["id"] == mid
    status, ctx = session.call("GET", f"/api/entries/{mid}/context?case=flow&before=2&after=2&same=source")
    assert status == 200 and len(ctx["rows"]) >= 1


def test_sse_replays_done_to_a_late_subscriber(session, tmp_path):
    zpath = mf.build_ers_zip(tmp_path / "z")
    session.call("POST", "/api/cases", {"name": "late"})
    session.call("POST", "/api/cases/late/collections", {"path": str(zpath)})
    first = session.events_until_done("late")
    second = session.events_until_done("late")
    assert first[-1] == second[-1]


def test_reingest_same_zip_is_accepted(session, tmp_path):
    zpath = mf.build_ers_zip(tmp_path / "z")
    session.call("POST", "/api/cases", {"name": "again"})
    for _ in range(2):
        assert session.call("POST", "/api/cases/again/collections", {"path": str(zpath)})[0] == 202
        assert session.events_until_done("again")[-1][1]["status"] == "ok"
    assert session.call("POST", "/api/entries/count", {"case": "again"})[1]["count"] == 31


def test_conflict_reported_before_any_job_starts(session, tmp_path):
    session.call("POST", "/api/cases", {"name": "c409"})
    first = mf.build_ers_zip(tmp_path / "a")
    session.call("POST", "/api/cases/c409/collections", {"path": str(first)})
    session.events_until_done("c409")
    copy = tmp_path / "b" / first.name  # a second copy of the same ERS half (U21)
    copy.parent.mkdir()
    copy.write_bytes(first.read_bytes())
    status, body = session.call("POST", "/api/cases/c409/collections", {"path": str(copy)})
    assert status == 409 and body["error"] == "duplicate_collection"


def test_failed_ingest_ends_with_error_done(session, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    session.call("POST", "/api/cases", {"name": "bad"})
    status, _ = session.call("POST", "/api/cases/bad/collections", {"path": str(bad)})
    if status == 202:
        done = session.events_until_done("bad")[-1][1]
        assert done["status"] == "error" or done["message"]
    else:
        assert status == 400


def test_one_job_per_case_and_parallel_reads_during_ingest(session, tmp_path):
    big = big_log(tmp_path)
    session.call("POST", "/api/cases", {"name": "busy"})
    other = mf.build_ers_zip(tmp_path / "z")
    session.call("POST", "/api/cases", {"name": "quiet"})
    session.call("POST", "/api/cases/quiet/collections", {"path": str(other)})
    session.events_until_done("quiet")

    assert session.call("POST", "/api/cases/busy/collections", {"path": str(big)})[0] == 202
    status, body = session.call("POST", "/api/cases/busy/collections", {"path": str(big)})
    assert status == 409 and body["error"] == "job_running"
    assert session.call("DELETE", "/api/cases/busy")[0] == 409

    errors, counts = [], []

    def reader(i):
        try:
            sess = Session(session.server)
            for _ in range(5):
                status, body = sess.call("POST", "/api/entries/count", {"case": "quiet"})
                assert status == 200
                counts.append(body["count"])
                assert sess.call("GET", "/api/sources?case=busy")[0] == 200
                assert sess.call("POST", "/api/entries/page", {"case": "busy", "limit": 50})[0] == 200
                assert sess.call("GET", "/api/cases")[0] == 200
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert set(counts) == {31}
    done = session.events_until_done("busy")[-1][1]
    assert done["status"] == "ok"
    total = session.call("POST", "/api/entries/count", {"case": "busy"})[1]["count"]
    assert total == done["records"] and total > 1000
    assert session.call("DELETE", "/api/cases/busy")[0] == 204


def test_sse_client_disconnect_does_not_leak_handler_threads(session, tmp_path, monkeypatch):
    monkeypatch.setattr(sse, "POLL_S", 0.05)
    session.call("POST", "/api/cases", {"name": "drop"})
    before = handler_threads()
    conns = []
    for _ in range(5):
        conns.append(session.open_sse("drop"))  # keep the responses alive: dropping one closes it
    time.sleep(0.3)
    mine = {t for t in handler_threads() - before if t.is_alive()}
    assert len(mine) == 5
    for conn, response in conns:
        response.close()
        conn.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(t.is_alive() for t in mine):
        time.sleep(0.05)
    assert not any(t.is_alive() for t in mine)


def test_server_close_cancels_a_running_ingest_and_ends_streams(tmp_path):
    srv = DmslogServer(0, data_dir=tmp_path / "data")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    sess = Session(srv)
    big = big_log(tmp_path, mb=40)
    sess.call("POST", "/api/cases", {"name": "stop"})
    conn, _response = sess.open_sse("stop")
    assert sess.call("POST", "/api/cases/stop/collections", {"path": str(big)})[0] == 202
    time.sleep(0.3)
    started = time.monotonic()
    srv.shutdown()
    srv.server_close()
    thread.join(5)
    conn.close()
    assert time.monotonic() - started < 10
    assert not any(t.name == "ingest-stop" and t.is_alive() for t in threading.enumerate())


def test_aliases_with_other_letter_case_share_the_job_lock_and_hub(session, server, tmp_path):
    big = big_log(tmp_path)
    session.call("POST", "/api/cases", {"name": "Demo"})
    assert session.call("POST", "/api/cases/Demo/collections", {"path": str(big)})[0] == 202
    for alias in ("demo", "DEMO"):
        status, body = session.call("POST", f"/api/cases/{alias}/collections", {"path": str(big)})
        assert status == 409 and body["error"] == "job_running"
        assert session.call("DELETE", f"/api/cases/{alias}")[0] == 409
    jobs = server.api.jobs
    assert jobs.hub("demo") is jobs.hub("DEMO") is jobs.hub("Demo")
    assert jobs.is_running("DEMO")
    events = session.events_until_done("dEmO")
    assert events[-1][0] == "done"
