"""GET /api/knowledge and /api/knowledge/error against a synthetic case with an ErrorConfiguration."""

import pytest
from test_api import client, mf, server  # noqa: F401  (fixtures)
from test_pipeline_knowledge import build

from dmslog.ingest.pipeline import run_ingest
from dmslog.store import create_case_db


@pytest.fixture
def demo(server, tmp_path):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, build(tmp_path / "coll", registry=True))
    conn.close()
    return "Demo"


def test_template_knowledge(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=WFM-080")
    assert status == 200 and body["template"] == "WFM-080" and body["family"] == "AWFM"
    assert "WaitForCRSCallback" in body["hint"]["text"]
    assert body["hint"]["params"] == ["WaitForCRSCallback"]
    param = body["params"][0]
    assert param["name"] == "WaitForCRSCallback" and param["default"] and param["description"]
    assert body["adapter"]["acronym"] == "AWFM"
    assert body["vault_note"] == body["hint"]["note"]


def test_template_without_hint_still_describes_the_adapter(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=WFM-011")
    assert status == 200 and body["hint"] is None and body["params"] == []
    assert body["vault_note"] is None or isinstance(body["vault_note"], str)


def test_error_defined_by_the_ers(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge/error?case=Demo&family=AWFM&code=ExampleCodeOne")
    assert status == 200
    assert body["source"] == "ers" and body["out_of_vault"] is True and body["severity"] == 1
    assert body["reason"] == "Example reason" and body["details"] == "Example details"


def test_error_unknown_and_validation(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge/error?case=Demo&family=AWFM&code=NoSuchCode")
    assert status == 404 and body["error"] == "error_not_found"
    assert client.get("/api/knowledge/error?case=Demo&code=x")[1]["error"] == "invalid_field"
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM&code=bad%20code")[0] == 400
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM")[0] == 400
    assert client.get("/api/knowledge/error?case=Demo&family=AWFM&code=X&operation=bad%20op")[0] == 400


def test_template_errors(client, demo):  # noqa: F811
    status, body, _ = client.get("/api/knowledge?case=Demo&template=NOPE-1")
    assert status == 404 and body["error"] == "template_not_found"
    assert client.get("/api/knowledge?case=Demo")[0] == 400
    assert client.get("/api/knowledge?case=Demo&template=bad%20code")[0] == 400
    assert client.get("/api/knowledge?template=WFM-080")[1]["error"] == "invalid_case_id"
    assert client.get("/api/knowledge?case=Nope&template=WFM-080")[1]["error"] == "case_not_found"
    assert client.request("POST", "/api/knowledge", {})[0] == 405


def test_reingest_after_editing_a_folder_xml_returns_the_new_value(client, server, tmp_path):  # noqa: F811
    case_dir = server.api.store.root / "Demo"
    case_dir.mkdir(parents=True)
    folder = build(tmp_path / "coll", registry=True)
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, folder)
    conn.close()
    url = "/api/knowledge/error?case=Demo&family=AWFM&code=ExampleCodeOne"
    assert client.get(url)[1]["reason"] == "Example reason"
    xml = next(folder.rglob("Example_Created_ErrorConfiguration.xml"))
    xml.write_text(
        xml.read_text(encoding="utf-8").replace("Example reason", "Edited reason"), encoding="utf-8"
    )
    root_mtime = folder.stat().st_mtime_ns
    conn = create_case_db(case_dir / "case.sqlite")
    run_ingest(conn, folder)
    conn.close()
    assert folder.stat().st_mtime_ns == root_mtime  # editing an inner file does not touch the root
    assert client.get(url)[1]["reason"] == "Edited reason"


def test_unread_request_body_is_drained_before_closing(client, monkeypatch):  # noqa: F811
    # A 405 (or 403) is answered without reading the body. Closing a socket that still holds unread data makes
    # the OS reset the connection (Windows: WinError 10053 on the client), so the server must drain it first.
    from dmslog.server.app import RequestHandler

    drained = []
    original = RequestHandler._discard

    def spy(self, length):
        drained.append(length)
        original(self, length)

    monkeypatch.setattr(RequestHandler, "_discard", spy)
    payload = b"x" * 5000
    assert client.request("POST", "/api/knowledge", raw=payload)[0] == 405
    assert client.request("POST", "/nope.html", raw=payload)[0] == 405
    assert drained == [5000, 5000]
    assert client.request("POST", "/api/cases", {"name": "ok"})[0] == 201
    assert drained == [5000, 5000]  # a body the handler reads is not drained again


def _raw_post(srv, declared, sent):
    import socket

    sock = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    head = (
        f"POST /api/knowledge HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nContent-Length: {declared}\r\n\r\n"
    ).encode()
    sock.sendall(head + b"x" * sent)
    return sock


def test_stalled_body_does_not_hold_the_handler_thread(server, monkeypatch):  # noqa: F811
    import threading
    import time

    from dmslog.server import app

    monkeypatch.setattr(app, "DRAIN_DEADLINE_SECONDS", 0.3)
    handlers = []
    original = app.RequestHandler.handle

    def tracked(self):
        handlers.append(threading.current_thread())
        original(self)

    monkeypatch.setattr(app.RequestHandler, "handle", tracked)
    sock = _raw_post(server, declared=5000, sent=10)  # declares 5000 bytes, sends 10, stays open
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end and not handlers:
            time.sleep(0.01)
        assert handlers
        handlers[0].join(timeout=3)  # guard: without the deadline this thread never ends
        assert not handlers[0].is_alive()
    finally:
        sock.close()


def test_stalled_body_still_gets_the_error_response(server, monkeypatch):  # noqa: F811
    from dmslog.server import app

    monkeypatch.setattr(app, "DRAIN_DEADLINE_SECONDS", 0.3)
    sock = _raw_post(server, declared=5000, sent=10)
    try:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            assert chunk
            data += chunk
        assert data.split(b"\r\n", 1)[0].split()[1] in (b"403", b"405")
    finally:
        sock.close()


def _track_handler(monkeypatch, app):
    """Wrap RequestHandler.handle: returns (started, finished) events set when a connection starts / ends."""
    import threading

    started, finished = threading.Event(), threading.Event()
    original = app.RequestHandler.handle

    def tracked(self):
        started.set()
        try:
            original(self)
        finally:
            finished.set()

    monkeypatch.setattr(app.RequestHandler, "handle", tracked)
    return started, finished


def test_trickling_body_cannot_extend_the_total_deadline(server, monkeypatch):  # noqa: F811
    # A client that keeps sending a byte now and then must not renew the timeout on every read: the
    # deadline is total. The handler has to finish while the client is still trickling.
    import socket
    import threading
    import time

    from dmslog.server import app

    monkeypatch.setattr(app, "DRAIN_DEADLINE_SECONDS", 0.3)
    started, finished = _track_handler(monkeypatch, app)
    stop = threading.Event()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)

    def trickle():
        try:
            while not stop.is_set():
                sock.sendall(b"x")
                stop.wait(0.1)
        except OSError:
            pass

    feeder = None
    try:
        head = (
            f"POST /api/knowledge HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nContent-Length: 100000\r\n\r\n"
        ).encode()
        sock.sendall(head)
        feeder = threading.Thread(target=trickle, daemon=True)
        feeder.start()
        began = time.monotonic()
        assert started.wait(3)
        assert finished.wait(1.5), "the drain deadline was renewed by the trickling client"
        assert time.monotonic() - began <= 1.5
        assert feeder.is_alive()  # the client was still sending when the handler gave up
    finally:
        stop.set()
        sock.close()
        if feeder is not None:
            feeder.join(timeout=3)


def test_client_disconnecting_mid_body_raises_nothing(server, monkeypatch, capsys):  # noqa: F811
    from dmslog.server import app

    monkeypatch.setattr(app, "DRAIN_DEADLINE_SECONDS", 0.3)
    errors = []
    monkeypatch.setattr(server, "handle_error", lambda request, client_address: errors.append(client_address))
    _, finished = _track_handler(monkeypatch, app)
    sock = _raw_post(server, declared=5000, sent=10)
    try:
        assert sock.recv(4096)  # the response was written; the handler is now draining the stalled body
    finally:
        sock.close()
    assert finished.wait(3), "the handler did not finish"
    assert errors == []
    assert "Traceback" not in capsys.readouterr().err
