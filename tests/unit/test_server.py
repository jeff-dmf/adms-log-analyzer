import http.client
import json
import threading
from urllib.parse import urlsplit

import pytest

from dmslog import __version__
from dmslog.server.app import CONTENT_SECURITY_POLICY, COOKIE_NAME, DmslogServer

EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)


@pytest.fixture
def server():
    srv = DmslogServer(0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def call(srv, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        conn.close()


@pytest.fixture
def cookie(server):
    start = urlsplit(server.start_url)
    status, headers, _ = call(server, "GET", f"{start.path}?{start.query}")
    assert status == 302
    return headers["set-cookie"].split(";", 1)[0]


def test_csp_constant_is_exact():
    assert CONTENT_SECURITY_POLICY == EXPECTED_CSP


def test_binds_only_to_loopback(server):
    assert server.server_address[0] == "127.0.0.1"


def test_request_without_session_is_forbidden(server):
    status, _, _ = call(server, "GET", "/api/health")
    assert status == 403


def test_wrong_token_is_forbidden(server):
    status, headers, _ = call(server, "GET", "/?token=wrong")
    assert status == 403
    assert "set-cookie" not in headers


def test_wrong_cookie_is_forbidden(server):
    status, _, _ = call(server, "GET", "/api/health", {"Cookie": f"{COOKIE_NAME}=forged"})
    assert status == 403


@pytest.mark.parametrize("path", ["//evil.example/x", "///evil.example", "/a/b"])
def test_token_redirect_stays_on_the_same_origin(server, path):
    status, headers, _ = call(server, "GET", f"{path}?token={server.token}")
    assert status == 302
    location = headers["location"]
    assert location.startswith("/") and not location.startswith("//")


def test_token_is_exchanged_for_http_only_strict_cookie(server):
    status, headers, _ = call(server, "GET", f"/?token={server.token}")
    assert status == 302
    assert headers["location"] == "/"
    cookie_header = headers["set-cookie"]
    assert cookie_header.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie_header
    assert "SameSite=Strict" in cookie_header
    assert server.token not in cookie_header


def test_health_with_cookie(server, cookie):
    status, headers, body = call(server, "GET", "/api/health", {"Cookie": cookie})
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    health = json.loads(body)
    assert set(health) == {"version", "pack_version", "sqlite", "fts5", "commit"}
    assert health["version"] == __version__
    assert health["pack_version"] is None
    assert health["fts5"] is True


def test_forged_host_is_forbidden_even_with_cookie(server, cookie):
    status, _, _ = call(server, "GET", "/api/health", {"Cookie": cookie, "Host": "evil.com"})
    assert status == 403


def test_forged_host_is_forbidden_with_valid_token(server):
    status, _, _ = call(server, "GET", f"/?token={server.token}", {"Host": f"evil.com:{server.port}"})
    assert status == 403


def test_localhost_host_is_allowed(server, cookie):
    status, _, _ = call(server, "GET", "/api/health", {"Cookie": cookie, "Host": f"localhost:{server.port}"})
    assert status == 200


def test_security_headers_on_api_responses(server, cookie):
    _, headers, _ = call(server, "GET", "/api/health", {"Cookie": cookie})
    assert headers["content-security-policy"] == EXPECTED_CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"


def test_security_headers_on_forbidden_responses(server):
    _, headers, _ = call(server, "GET", "/api/health")
    assert headers["content-security-policy"] == EXPECTED_CSP


def test_post_with_foreign_origin_is_forbidden(server, cookie):
    headers = {"Cookie": cookie, "Origin": "http://evil.com"}
    status, _, _ = call(server, "POST", "/api/health", headers, body=b"{}")
    assert status == 403


def test_post_without_origin_is_forbidden(server, cookie):
    status, _, _ = call(server, "POST", "/api/health", {"Cookie": cookie}, body=b"{}")
    assert status == 403


def test_delete_with_foreign_origin_is_forbidden(server, cookie):
    status, _, _ = call(server, "DELETE", "/api/health", {"Cookie": cookie, "Origin": "http://evil.com"})
    assert status == 403


def test_post_with_same_origin_reaches_the_router(server, cookie):
    headers = {"Cookie": cookie, "Origin": f"http://127.0.0.1:{server.port}"}
    status, _, _ = call(server, "POST", "/api/health", headers, body=b"{}")
    assert status == 405


def test_index_and_assets_are_served(server, cookie):
    status, headers, body = call(server, "GET", "/", {"Cookie": cookie})
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b"innerHTML" not in body
    assert b"<script>" not in body  # no inline scripts: CSP forbids them
    for path, content_type in (("/js/main.js", "text/javascript"), ("/css/app.css", "text/css"),
                               ("/js/strings.js", "text/javascript")):
        status, headers, _ = call(server, "GET", path, {"Cookie": cookie})
        assert status == 200
        assert headers["content-type"].startswith(content_type)


@pytest.mark.parametrize("path", ["/__init__.py", "/../cli.py", "/js/../../cli.py", "/%2e%2e/cli.py",
                                  "/js\\main.js", "/nope.html", "/js/"])
def test_static_paths_cannot_escape_or_leak(server, cookie, path):
    status, _, body = call(server, "GET", path, {"Cookie": cookie})
    assert status == 404
    assert b"import" not in body


def test_unknown_api_route_is_json_404(server, cookie):
    status, headers, _ = call(server, "GET", "/api/nope", {"Cookie": cookie})
    assert status == 404
    assert headers["content-type"].startswith("application/json")
