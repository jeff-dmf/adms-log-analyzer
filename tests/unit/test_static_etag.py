import http.client
import threading
from urllib.parse import urlsplit

import pytest

from dmslog.server.app import DmslogServer, etag_matches


@pytest.fixture
def server():
    srv = DmslogServer(0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def call(srv, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        conn.close()


@pytest.fixture
def auth(server):
    start = urlsplit(server.start_url)
    _, headers, _ = call(server, "GET", f"{start.path}?{start.query}")
    return {"Cookie": headers["set-cookie"].split(";", 1)[0]}


def test_static_200_has_strong_etag_and_no_cache(server, auth):
    status, headers, body = call(server, "GET", "/", auth)
    assert status == 200 and body
    assert headers["etag"].startswith('"') and not headers["etag"].startswith("W/")
    assert headers["cache-control"] == "no-cache"


@pytest.fixture
def etag(server, auth):
    return call(server, "GET", "/", auth)[1]["etag"]


@pytest.mark.parametrize(
    "build",
    [
        lambda e: e,
        lambda e: "W/" + e,
        lambda e: f'"other", {e}',
        lambda e: "*",
    ],
)
def test_if_none_match_hit_returns_304_without_body(server, auth, etag, build):
    status, headers, body = call(server, "GET", "/", {**auth, "If-None-Match": build(etag)})
    assert status == 304
    assert body == b""
    assert headers["etag"] == etag
    assert headers["cache-control"] == "no-cache"
    assert "content-length" not in headers


@pytest.mark.parametrize("value", ['"different"', '"a", "b"', ""])
def test_if_none_match_miss_returns_200(server, auth, value):
    status, _, body = call(server, "GET", "/", {**auth, "If-None-Match": value})
    assert status == 200 and body


def test_head_is_coherent_with_get(server, auth, etag):
    status, headers, body = call(server, "HEAD", "/", auth)
    assert status == 200 and body == b""
    assert headers["etag"] == etag
    assert int(headers["content-length"]) > 0
    status, _, body = call(server, "HEAD", "/", {**auth, "If-None-Match": etag})
    assert status == 304 and body == b""


def test_etag_is_stable_and_differs_per_file(server, auth, etag):
    assert call(server, "GET", "/", auth)[1]["etag"] == etag
    assert call(server, "GET", "/js/main.js", auth)[1]["etag"] != etag


def test_api_stays_no_store(server, auth):
    status, headers, _ = call(server, "GET", "/api/health", auth)
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert "etag" not in headers
    status, headers, _ = call(server, "GET", "/api/health", {**auth, "If-None-Match": "*"})
    assert status == 200 and headers["cache-control"] == "no-store"


def test_missing_file_is_404_no_store(server, auth):
    status, headers, _ = call(server, "GET", "/js/nope.js", auth)
    assert status == 404
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/js/../../x.py", "/%2e%2e/x", "/js/%5c..%5cx"])
def test_path_traversal_stays_blocked(server, auth, path):
    status, _, _ = call(server, "GET", path, auth)
    assert status == 404


def test_etag_matches_helper():
    assert etag_matches('W/"a"', '"a"')
    assert etag_matches('"b" , "a"', '"a"')
    assert etag_matches("*", '"a"')
    assert not etag_matches(None, '"a"')
    assert not etag_matches('"b"', '"a"')


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "build",
    [
        lambda e: f"garbage, {e}",
        lambda e: "garbage, *",
        lambda e: '*, "x"',
        lambda e: '"x", *',
        lambda e: f'"unbalanced, {e}',
        lambda e: f"{e} garbage",
        lambda e: f"{e},",
        lambda e: "",
        lambda e: f"w/{e}",
    ],
)
def test_invalid_if_none_match_is_ignored(server, auth, etag, method, build):
    status, _, _ = call(server, method, "/", {**auth, "If-None-Match": build(etag)})
    assert status == 200


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_valid_forms_match_for_get_and_head(server, auth, etag, method):
    for value in (f'"a,b", {etag}', f"W/{etag}", f" {etag} ", f'W/"x" ,{etag}', "*"):
        status, _, body = call(server, method, "/", {**auth, "If-None-Match": value})
        assert status == 304 and body == b"", value


def test_comma_inside_tag_is_not_a_separator():
    assert not etag_matches('"a,"b"', '"b"')
    assert etag_matches('"a,b"', '"a,b"')
    assert not etag_matches('"a,b"', '"b"')


def test_concurrent_requests_fill_each_resource_once(server, monkeypatch):
    from pathlib import Path

    from dmslog.server.app import find_web_resource

    reads: list[str] = []
    original = Path.read_bytes

    def slow_read(self):
        reads.append(str(self))
        threading.Event().wait(0.05)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", slow_read)
    resources = [find_web_resource("/"), find_web_resource("/js/main.js")]
    assert all(isinstance(r, Path) for r in resources)
    results = []
    threads = [
        threading.Thread(target=lambda r=r: results.append(server.static_entry(r)))
        for r in resources
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 16
    assert sorted(reads) == sorted(str(r) for r in resources)
