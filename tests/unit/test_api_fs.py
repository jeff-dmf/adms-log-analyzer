"""Routes of the file picker and folder browser (/api/fs/*, ARQUITETURA 10.4) on a real loopback server."""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from dmslog.server import filepick
from dmslog.server.app import COOKIE_NAME, DmslogServer

ACCENTED_ZIP = "A\u00e7\u00e3o.ZIP"


class Client:
    def __init__(self, server):
        self.server = server
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
        start = urlsplit(server.start_url)
        conn.request("GET", f"{start.path}?{start.query}")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        assert self.cookie.startswith(COOKIE_NAME)

    def request(self, method, path, body=None, raw=None, headers=None, cookie=True, origin=True):
        hdrs = {}
        if cookie:
            hdrs["Cookie"] = self.cookie
        if origin:
            hdrs["Origin"] = f"http://127.0.0.1:{self.server.port}"
        hdrs.update(headers or {})
        data = json.dumps(body).encode("utf-8") if body is not None else raw
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=15)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            response = conn.getresponse()
            payload = response.read()
            is_json = response.getheader("Content-Type", "").startswith("application/json")
            return response.status, (json.loads(payload) if payload and is_json else None)
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
def client(server):
    return Client(server)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "ERS files"
    root.mkdir()
    (root / "Sub").mkdir()
    (root / "a.zip").write_bytes(b"PK")
    (root / "A\u00e7\u00e3o.ZIP").write_bytes(b"PK!")
    (root / "notes.txt").write_text("x", encoding="utf-8")
    return root


class StubPicker:
    """Stands in for filepick.Picker: records calls, answers what the test sets."""

    def __init__(self, available=True, answer=None, error=None):
        self.available = available
        self.answer = answer or {
            "paths": [],
            "cancelled": True,
            "timed_out": False,
            "errors": [],
            "truncated": False,
        }
        self.error = error
        self.calls = []
        self.cancelled = 0

    def capabilities(self):
        kind = "zenity" if self.available else None
        return {"native": {"available": self.available, "kind": kind}, "browse": True, "platform": "linux",
                "kinds": ["zip", "folder"]}  # fmt: skip

    def pick(self, kind, multiple):
        self.calls.append((kind, multiple))
        if self.error:
            raise self.error
        return self.answer

    def cancel(self):
        self.cancelled += 1
        return False

    def close(self):
        pass


def q(path) -> str:
    return quote(str(path), safe="")


# --------------------------------------------------------------------------- session and origin


@pytest.mark.parametrize("path", ["/api/fs/capabilities", "/api/fs/list"])
def test_get_routes_need_the_session(client, path):
    assert client.request("GET", path, cookie=False)[0] == 403


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_pick_routes_need_session_and_origin(client, method):
    client.server.api.picker = stub = StubPicker()
    body = {"kind": "zip"} if method == "POST" else None
    assert client.request(method, "/api/fs/pick", body, cookie=False)[0] == 403
    assert client.request(method, "/api/fs/pick", body, origin=False)[0] == 403
    assert client.request(method, "/api/fs/pick", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert stub.calls == [] and stub.cancelled == 0


def test_pick_body_size_is_limited(client):
    client.server.api.picker = stub = StubPicker()
    raw = json.dumps({"kind": "zip", "pad": "x" * (300 * 1024)}).encode()
    assert client.request("POST", "/api/fs/pick", raw=raw)[0] == 413
    assert stub.calls == []


# --------------------------------------------------------------------------- capabilities


def test_capabilities_default_picker(client):
    status, body = client.request("GET", "/api/fs/capabilities")
    assert status == 200
    assert body["browse"] is True and set(body["native"]) == {"available", "kind"}
    assert body["native"]["kind"] in (None, "windows-comdlg", "zenity", "kdialog")


def test_capabilities_without_native_dialog(client):
    client.server.api.picker = filepick.Picker(lambda: None)
    status, body = client.request("GET", "/api/fs/capabilities")
    assert status == 200 and body["native"] == {"available": False, "kind": None}


# --------------------------------------------------------------------------- list


def test_list_folder(client, tree):
    status, body = client.request("GET", f"/api/fs/list?path={q(tree)}")
    assert status == 200
    assert [(e["name"], e["type"]) for e in body["entries"]] == [
        ("Sub", "dir"),
        ("a.zip", "zip"),
        ("A\u00e7\u00e3o.ZIP", "zip"),
    ]
    assert body["path"] == str(tree) and body["parent"] == str(tree.parent)
    assert body["other_files"] == 1 and body["truncated"] is False
    assert isinstance(body["shortcuts"], list) and body["shortcuts"][0]["kind"] == "home"


def test_list_defaults_to_home(client, tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree))
    monkeypatch.setenv("USERPROFILE", str(tree))
    status, body = client.request("GET", "/api/fs/list")
    assert status == 200 and Path(body["path"]) == tree


@pytest.mark.parametrize(("make", "status", "code"), [
    (lambda t: t / "missing", 404, "path_not_found"),
    (lambda t: t / "a.zip", 400, "not_a_directory"),
    (lambda t: "relative/dir", 400, "path_not_absolute"),
    (lambda t: f"{t}\x00x", 400, "invalid_path"),
    (lambda t: "/" + "a" * 5000, 400, "invalid_path"),
])  # fmt: skip
def test_list_errors(client, tree, make, status, code):
    got, body = client.request("GET", f"/api/fs/list?path={q(make(tree))}")
    assert (got, body["error"]) == (status, code)
    assert body["message"]


@pytest.mark.skipif(os.name != "nt", reason="Windows path rules")
@pytest.mark.parametrize(("raw", "code"), [
    ("\\\\server\\share", "unc_path"),
    ("\\\\?\\C:\\Windows", "device_path"),
    ("C:\\Windows\\CON", "reserved_name"),
])  # fmt: skip
def test_list_refuses_windows_special_paths(client, raw, code):
    got, body = client.request("GET", f"/api/fs/list?path={q(raw)}")
    assert (got, body["error"]) == (400, code)


def test_list_does_not_change_anything(client, tree):
    before = sorted(p.name for p in tree.iterdir())
    for _ in range(2):
        client.request("GET", f"/api/fs/list?path={q(tree)}")
    assert sorted(p.name for p in tree.iterdir()) == before


# --------------------------------------------------------------------------- pick


def test_pick_answer(client, tree):
    answer = {"paths": [str(tree / "a.zip")], "cancelled": False, "timed_out": False,
              "errors": [{"path": str(tree / "notes.txt"), "error": "not_a_zip", "message": "x"}],
              "truncated": False}  # fmt: skip
    client.server.api.picker = stub = StubPicker(answer=answer)
    status, body = client.request("POST", "/api/fs/pick", {"kind": "zip", "multiple": True})
    assert status == 200 and body == answer and stub.calls == [("zip", True)]
    assert client.request("POST", "/api/fs/pick", {})[0] == 200
    assert stub.calls[-1] == ("zip", True)  # defaults
    client.request("POST", "/api/fs/pick", {"kind": "folder", "multiple": False})
    assert stub.calls[-1] == ("folder", False)


@pytest.mark.parametrize(("body", "code"), [
    ({"kind": "exe"}, "bad_kind"),
    ({"kind": None}, "bad_kind"),
    ({"multiple": "yes"}, "bad_multiple"),
    ({"multiple": 1}, "bad_multiple"),
    ({"kind": "zip", "title": "x"}, "invalid_field"),
    ({"kind": "zip", "start": "/etc"}, "invalid_field"),
    ({"kind": "zip", "args": ["--help"]}, "invalid_field"),
])  # fmt: skip
def test_pick_accepts_only_kind_and_multiple(client, body, code):
    client.server.api.picker = stub = StubPicker()
    status, answer = client.request("POST", "/api/fs/pick", body)
    assert (status, answer["error"]) == (400, code) and stub.calls == []


def test_pick_without_native_dialog(client):
    client.server.api.picker = filepick.Picker(lambda: None)
    status, body = client.request("POST", "/api/fs/pick", {"kind": "zip"})
    assert (status, body["error"]) == (409, "picker_unavailable")


def test_pick_busy_and_failed(client):
    client.server.api.picker = StubPicker(error=filepick.PickerBusyError())
    assert client.request("POST", "/api/fs/pick", {"kind": "zip"})[1]["error"] == "picker_busy"
    client.server.api.picker = StubPicker(error=filepick.PickerError("boom"))
    status, body = client.request("POST", "/api/fs/pick", {"kind": "zip"})
    assert (status, body["error"]) == (500, "picker_failed") and "boom" not in body["message"]


def test_pick_cancel_route(client):
    client.server.api.picker = stub = StubPicker()
    assert client.request("DELETE", "/api/fs/pick")[0] == 204
    assert stub.cancelled == 1


def test_get_on_pick_is_not_allowed(client):
    assert client.request("GET", "/api/fs/pick")[0] == 405


@pytest.mark.skipif(os.name != "posix", reason="fake zenity is a POSIX script")
def test_pick_end_to_end_with_a_fake_dialog(client, tree, tmp_path):
    script = tmp_path / "zenity"
    answer = tmp_path / "answer.bin"
    answer.write_bytes(f"{tree / ACCENTED_ZIP}\n{tree / 'Sub'}\n".encode())
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(open({str(answer)!r}, 'rb').read())\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    client.server.api.picker = filepick.Picker(lambda: filepick.NativeTool("zenity", str(script)))
    status, body = client.request("POST", "/api/fs/pick", {"kind": "zip", "multiple": True})
    assert status == 200
    assert body["paths"] == [str(tree / "A\u00e7\u00e3o.ZIP")] and body["cancelled"] is False
    assert [(e["path"], e["error"]) for e in body["errors"]] == [(str(tree / "Sub"), "not_a_zip")]
