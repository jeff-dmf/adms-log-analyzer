"""Route-level tests of the JSON API against a real loopback server and a fixture-built case."""

import http.client
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from dmslog.server.api import ApiError
from dmslog.server.app import COOKIE_NAME, DmslogServer

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)

OTHER_GUID = "00000000-0000-4000-8000-000000000002"


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

    def request(self, method, path, body=None, raw=None, headers=None):
        hdrs = {"Cookie": self.cookie, "Origin": f"http://127.0.0.1:{self.server.port}"}
        hdrs.update(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=15)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            response = conn.getresponse()
            payload = response.read()
            is_json = response.getheader("Content-Type", "").startswith("application/json")
            parsed = json.loads(payload) if payload and is_json else None
            return response.status, parsed, {k.lower(): v for k, v in response.getheaders()}
        finally:
            conn.close()

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)

    def wait_done(self, case, timeout=30):
        """Poll the case until the ingest finished (the SSE flow is covered elsewhere)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, data, _ = self.get("/api/cases")
            info = next(c for c in data["cases"] if c["id"] == case)
            if not info["ingesting"]:
                return info
            time.sleep(0.05)
        raise AssertionError("ingest did not finish")


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
def zip_path(tmp_path):
    return mf.build_ers_zip(tmp_path / "z")


@pytest.fixture
def case(client, zip_path):
    status, body, _ = client.post("/api/cases", {"name": "demo"})
    assert status == 201 and body == {"id": "demo", "name": "demo"}
    status, body, _ = client.post("/api/cases/demo/collections", {"path": str(zip_path), "scope": "default"})
    assert status == 202 and body["job_id"]
    client.wait_done("demo")
    return "demo"


def test_cases_lifecycle(client, tmp_path):
    assert client.get("/api/cases")[1] == {"cases": [], "watching": False}
    assert client.post("/api/cases", {"name": "one"})[0] == 201
    status, body, _ = client.get("/api/cases")
    assert status == 200
    (info,) = body["cases"]
    assert info["id"] == "one" and info["name"] == "one"
    assert info["sources"] == 0 and info["records"] == 0 and info["size_bytes"] > 0
    assert client.request("DELETE", "/api/cases/one")[0] == 204
    assert client.get("/api/cases")[1] == {"cases": [], "watching": False}
    assert not (tmp_path / "data" / "cases" / "one").exists()


def test_duplicate_case_is_conflict_even_with_other_letter_case(client):
    assert client.post("/api/cases", {"name": "Dup"})[0] == 201
    for name in ("Dup", "dup"):
        status, body, _ = client.post("/api/cases", {"name": name})
        assert status == 409 and body["error"] == "case_exists"


def test_delete_unknown_case_is_404(client):
    status, body, _ = client.request("DELETE", "/api/cases/nope")
    assert status == 404 and body["error"] == "case_not_found"


def test_case_listing_counts_sources_and_records(client, case):
    (info,) = client.get("/api/cases")[1]["cases"]
    assert info["sources"] == 11 and info["records"] == 31 and info["ingesting"] is False


def test_sources_listing(client, case):
    status, body, headers = client.get(f"/api/sources?case={case}")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    sources = body["sources"]
    wanted = {"id", "name", "family", "process", "pid", "sensitive", "rotation", "status", "records",
              "lines", "ts_min", "ts_max", "size"}  # fmt: skip
    assert wanted <= set(sources[0])
    ready = [s for s in sources if s["status"] == "ready"]
    assert len(ready) == 6 and sum(s["records"] for s in ready) == 31
    wfm = next(s for s in ready if s["name"] == "WfmAdapter_1111.log")
    assert wfm["sensitive"] is False and wfm["records"] == 10 and wfm["lines"] >= 10
    assert isinstance(wfm["ts_min"], int) and wfm["ts_min"] <= wfm["ts_max"]


def test_page_walks_all_rows_forward_and_back(client, case):
    seen, cursor = [], None
    while True:
        status, body, _ = client.post(
            "/api/entries/page", {"case": case, "filter": None, "cursor": cursor, "dir": "fwd", "limit": 10}
        )
        assert status == 200
        seen += body["rows"]
        cursor = body["next"]
        if cursor is None:
            break
    assert len(seen) == 31
    assert {"id", "source_id", "ts", "pid", "tid", "level", "sev", "msg", "nlines", "flags"} <= set(seen[0])
    keys = [(r["ts"], r["id"]) for r in seen]
    assert keys == sorted(keys, key=lambda k: (k[0] is not None, k[0] or 0, k[1]))
    status, back, _ = client.post(
        "/api/entries/page",
        {"case": case, "cursor": f"{keys[-1][0]}:{keys[-1][1]}", "dir": "back", "limit": 5},
    )
    assert status == 200 and [r["id"] for r in back["rows"]] == [r["id"] for r in seen[-6:-1]]
    assert back["prev"] is not None and back["next"] is not None


def test_page_limit_is_capped(client, case):
    for limit in (0, 501, "5", True):
        status, body, _ = client.post("/api/entries/page", {"case": case, "limit": limit})
        assert status == 400 and body["error"] == "bad_limit"
    assert client.post("/api/entries/page", {"case": case, "limit": 500})[0] == 200


def test_page_extra_columns(client, case):
    status, body, _ = client.post("/api/entries/page", {"case": case, "limit": 2, "columns": ["tpl"]})
    assert status == 200 and "tpl" in body["rows"][0]
    status, body, _ = client.post("/api/entries/page", {"case": case, "columns": ["nope; DROP"]})
    assert status == 400 and body["error"] == "unknown_column"


def test_count_with_filter(client, case):
    status, body, _ = client.post("/api/entries/count", {"case": case, "filter": None})
    assert status == 200 and body == {"count": 31, "exact": True}
    _, sources, _ = client.get(f"/api/sources?case={case}")
    wfm = next(s for s in sources["sources"] if s["name"] == "WfmAdapter_1111.log")
    status, body, _ = client.post("/api/entries/count", {"case": case, "filter": {"source": [wfm["id"]]}})
    assert body == {"count": 10, "exact": True}


def test_filter_errors_are_400_with_code_and_path(client, case):
    status, body, _ = client.post("/api/entries/page", {"case": case, "filter": {"bogus": 1}})
    assert status == 400
    assert body["error"] == "unknown_operator" and body["path"] == "$" and "bogus" in body["message"]
    status, body, _ = client.post("/api/entries/count", {"case": case, "filter": {"and": [{"sev": "x"}]}})
    assert status == 400 and body["path"].startswith("$.and[0]")
    status, body, _ = client.post("/api/entries/page", {"case": case, "filter": [1]})
    assert status == 400


def test_substr_requires_source_or_time_scope(client, case):
    substr = {"text": {"substr": "Processing"}}
    for route in ("page", "count"):
        status, body, _ = client.post(f"/api/entries/{route}", {"case": case, "filter": substr})
        assert status == 400 and body["error"] == "substr_needs_scope"
        status, body, _ = client.post(
            f"/api/entries/{route}", {"case": case, "filter": {"or": [{"source": [1]}, substr]}}
        )
        assert status == 400 and body["error"] == "substr_needs_scope"
        status, body, _ = client.post(
            f"/api/entries/{route}",
            {
                "case": case,
                "filter": {
                    "not": {"source": [1]},
                },
            },
        )
        assert status == 200
    scoped = {"and": [{"ts": {"ge": 0}}, substr]}
    status, body, _ = client.post("/api/entries/count", {"case": case, "filter": scoped})
    assert status == 200 and body["count"] > 0
    nested = {"and": [{"and": [{"source": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]}]}, substr]}
    assert client.post("/api/entries/count", {"case": case, "filter": nested})[0] == 200
    fts = {"text": {"fts": "Processing"}}
    assert client.post("/api/entries/count", {"case": case, "filter": fts})[1]["count"] > 0


def test_entry_detail_and_context(client, case):
    _, page, _ = client.post("/api/entries/page", {"case": case, "limit": 3})
    entry_id = page["rows"][1]["id"]
    status, entry, _ = client.get(f"/api/entries/{entry_id}?case={case}")
    assert status == 200
    assert entry["id"] == entry_id and entry["text"].startswith(entry["msg"])
    status, ctx, _ = client.get(f"/api/entries/{entry_id}/context?case={case}&before=1&after=1&same=source")
    assert status == 200 and ctx["same"] == "source"
    assert [r["id"] for r in ctx["rows"]][ctx["anchor"]] == entry_id and len(ctx["rows"]) == 3
    status, ctx, _ = client.get(f"/api/entries/{entry_id}/context?case={case}&same=merge")
    assert status == 200 and len(ctx["rows"]) <= 101


def test_entry_errors(client, case):
    assert client.get(f"/api/entries/99999999?case={case}")[0] == 404
    assert client.get(f"/api/entries/99999999/context?case={case}")[0] == 404
    status, body, _ = client.get(f"/api/entries/abc?case={case}")
    assert status == 400 and body["error"] == "invalid_entry_id"
    status, body, _ = client.get(f"/api/entries/1/context?case={case}&same=weird")
    assert status == 400 and body["error"] == "bad_same"
    status, body, _ = client.get(f"/api/entries/1/context?case={case}&before=x")
    assert status == 400 and body["error"] == "invalid_field"
    status, body, _ = client.get(f"/api/entries/1/context?case={case}&before=9999")
    assert status == 400 and body["error"] == "bad_context"
    assert client.get("/api/entries/1")[0] == 400  # no case


def test_other_captures_folders_and_files_join_the_case_and_copies_are_409(client, case, zip_path, tmp_path):
    for path in (
        mf.build_ers_zip(tmp_path / "g", guid=OTHER_GUID),  # another server
        mf.build_ers_zip(tmp_path / "t", stamp="20300116103000"),  # another capture of the same GUID
    ):
        assert client.post("/api/cases/demo/collections", {"path": str(path)})[0] == 202
        client.wait_done("demo")
    folder = tmp_path / "plain"
    folder.mkdir()
    assert client.post("/api/cases/demo/collections", {"path": str(folder)})[0] == 202
    client.wait_done("demo")
    _, body, _ = client.get("/api/cases/demo/collections")
    assert [g["kind"] for g in body["collections"]] == ["ers", "ers", "ers", "folder"]
    assert body["ordering"] == "approximate"
    copy = tmp_path / "copy" / zip_path.name  # the same ERS half from another file: refused
    copy.parent.mkdir()
    copy.write_bytes(zip_path.read_bytes())
    status, body, _ = client.post("/api/cases/demo/collections", {"path": str(copy)})
    assert status == 409 and body["error"] == "duplicate_collection"
    assert client.post("/api/cases/demo/collections", {"path": str(zip_path)})[0] == 202  # same file: resume
    client.wait_done("demo")


def test_other_half_of_the_pair_is_accepted(client, case, tmp_path):
    sensitive = mf.build_ers_zip(tmp_path / "s", kind="sensitive")
    status, body, _ = client.post("/api/cases/demo/collections", {"path": str(sensitive)})
    assert status == 202
    info = client.wait_done("demo")
    assert info["records"] > 31


def test_collection_input_validation(client, case, tmp_path):
    base = "/api/cases/demo/collections"
    assert client.post(base, {})[1]["error"] == "invalid_field"
    assert client.post(base, {"path": 5})[1]["error"] == "invalid_field"
    assert client.post(base, {"path": "relative/x.zip"})[1]["error"] == "path_not_absolute"
    assert client.post(base, {"path": str(tmp_path / "missing.zip")})[1]["error"] == "path_not_found"
    status, body, _ = client.post(base, {"path": str(tmp_path), "scope": "everything"})
    assert status == 400 and body["error"] == "bad_scope"
    assert client.post("/api/cases/nope/collections", {"path": str(tmp_path)})[0] == 404


@pytest.mark.parametrize(
    "case_id",
    ["..", ".", "a/b", "a\\b", "../x", "%2e%2e", "con", "NUL", "com1.txt", "aux.log", "a.", "-x", ".hidden",
     "x" * 81, "", "a b", "a\x00b", "c:"],
)  # fmt: skip
def test_unsafe_case_ids_are_rejected(client, server, case_id):
    status, body, _ = client.post("/api/cases", {"name": case_id})
    assert status == 400 and body["error"] == "invalid_case_id"
    assert client.post("/api/entries/count", {"case": case_id})[0] == 400
    assert client.get(f"/api/sources?case={quote(case_id)}")[0] in (400, 404)
    assert (
        not (server.data_dir / "cases").exists()
        or [p.name for p in (server.data_dir / "cases").iterdir()] == []
    )


@pytest.mark.parametrize(
    "path", ["/api/cases/..", "/api/cases/%2e%2e", "/api/cases/a%2fb", "/api/cases/..%5cx"]
)
def test_path_traversal_in_url_cannot_delete(client, server, path):
    (server.data_dir / "cases" / "keep").mkdir(parents=True)
    assert client.request("DELETE", path)[0] in (400, 404, 405)
    assert (server.data_dir / "cases" / "keep").exists()


def test_symlinked_case_folder_is_not_followed(client, server, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (server.data_dir / "cases").mkdir(parents=True)
    try:
        (server.data_dir / "cases" / "link").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not available")
    assert client.get("/api/sources?case=link")[0] == 404
    assert client.request("DELETE", "/api/cases/link")[0] == 404
    assert target.exists()


def test_body_limits_and_shapes(client):
    status, body, _ = client.request("POST", "/api/cases", raw=b"x" * (300 * 1024))
    assert status == 413 and body["error"] == "body_too_large"
    assert client.request("POST", "/api/cases", raw=b"{nope")[1]["error"] == "bad_json"
    assert client.request("POST", "/api/cases", raw=b"[1]")[1]["error"] == "bad_json"
    assert client.request("POST", "/api/cases", raw=b"")[1]["error"] == "bad_json"
    status, body, _ = client.request("POST", "/api/cases", headers={"Transfer-Encoding": "chunked"})
    assert status == 411


def test_unknown_route_and_wrong_method(client):
    status, body, _ = client.get("/api/nope")
    assert status == 404 and body["error"] == "not_found"
    status, body, _ = client.request("DELETE", "/api/sources?case=x")
    assert status == 405 and body["error"] == "method_not_allowed"


def test_api_requires_session_and_origin(server, case=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("GET", "/api/cases")
    assert conn.getresponse().status == 403
    conn.close()
    session = Client(server)
    status, _, _ = session.request("POST", "/api/cases", {"name": "x"}, headers={"Origin": "http://evil.com"})
    assert status == 403
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("POST", "/api/cases", body=b'{"name":"x"}', headers={"Cookie": session.cookie})
    assert conn.getresponse().status == 403
    conn.close()


def test_api_responses_keep_security_headers(client, case):
    _, _, headers = client.get(f"/api/sources?case={case}")
    assert headers["content-security-policy"].startswith("default-src 'self'")
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"
    _, _, headers = client.get("/api/nope")
    assert headers["cache-control"] == "no-store"


def test_script_literal_comes_back_intact(client, tmp_path):
    log = tmp_path / "Script_1234.log"
    log.write_bytes(mf.fx_script_literal())
    assert client.post("/api/cases", {"name": "xss"})[0] == 201
    assert client.post("/api/cases/xss/collections", {"path": str(log)})[0] == 202
    client.wait_done("xss")
    _, page, _ = client.post("/api/entries/page", {"case": "xss", "limit": 10})
    first = page["rows"][0]
    assert first["msg"] == "User text: <script>alert(1)</script> end."
    body_row = next(r for r in page["rows"] if r["nlines"] > 1)
    _, entry, headers = client.get(f"/api/entries/{body_row['id']}?case=xss")
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["x-content-type-options"] == "nosniff"
    assert entry["text"] == ('Payload follows:\n<a href="x" onclick="alert(1)"><script>alert(1)</script></a>')


def test_aliases_with_other_letter_case_resolve_to_the_real_folder_name(server, client):
    assert client.post("/api/cases", {"name": "Demo"})[0] == 201
    store = server.api.store
    for alias in ("Demo", "demo", "DEMO", "dEmO"):
        assert store.canonical_case_id(alias) == "Demo"
        assert store.require(alias) == "Demo"
    assert client.get("/api/sources?case=demo")[0] == 200
    with pytest.raises(ApiError) as err:
        store.canonical_case_id("missing")
    assert err.value.status == 404


def test_pre_existing_folders_differing_only_in_case_are_ambiguous(server, tmp_path):
    store = server.api.store
    for name in ("Twin", "twin"):
        folder = store.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "case.sqlite").write_bytes(b"")
    if len({p.name for p in store.root.iterdir()}) < 2:
        pytest.skip("case-insensitive filesystem cannot hold both folders")
    with pytest.raises(ApiError) as err:
        store.canonical_case_id("TWIN")
    assert err.value.status == 409 and err.value.code == "case_ambiguous"
