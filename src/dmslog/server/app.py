"""Local HTTP server: loopback only, token-to-cookie sessions, Host/Origin checks."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import select
import socket
import threading
import time
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .. import __version__, messages
from ..buildinfo import get_commit
from ..paths import check_sqlite_fts5, resolve_data_dir
from . import sse
from .api import Api, ApiError, Download, Stream, body_limit

LOOPBACK_HOST = "127.0.0.1"
COOKIE_NAME = "dmslog_session"
TOKEN_PARAM = "token"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}

UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
DISCARD_LIMIT = 8 * 1024 * 1024
# Total time a handler may spend draining a request body it will not use (cuts off slow or stalled clients).
DRAIN_DEADLINE_SECONDS = 2.0


class DmslogServer(ThreadingHTTPServer):
    """Loopback-only ThreadingHTTPServer; a start token is exchanged for a session cookie.

    The start token stays valid while the process runs (each use issues a new cookie).
    """

    daemon_threads = True
    # On Windows SO_REUSEADDR allows another process to bind the same port.
    allow_reuse_address = os.name != "nt"

    def __init__(self, port: int = 0, data_dir: str | os.PathLike[str] | None = None) -> None:
        super().__init__((LOOPBACK_HOST, port), RequestHandler)
        self.port: int = self.server_address[1]
        self.data_dir: Path = resolve_data_dir(data_dir)
        self.api = Api(self.data_dir)
        self.token = secrets.token_urlsafe(32)
        self._sessions: set[str] = set()
        self._lock = threading.Lock()
        self.allowed_hosts = frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        self.allowed_origins = frozenset(f"http://{host}" for host in self.allowed_hosts)

    def server_close(self) -> None:
        """Stop background work (ingest jobs, SSE streams) before closing the socket."""
        self.api.shutdown()
        super().server_close()

    @property
    def start_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.port}/?{TOKEN_PARAM}={self.token}"

    def token_is_valid(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode("utf-8"), self.token.encode("utf-8"))

    def new_session(self) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(session_id)
        return session_id

    def session_is_valid(self, candidate: str) -> bool:
        with self._lock:
            known = list(self._sessions)
        encoded = candidate.encode("utf-8")
        return any(hmac.compare_digest(encoded, s.encode("utf-8")) for s in known)


class RequestHandler(BaseHTTPRequestHandler):
    server: DmslogServer
    server_version = "dmslog"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence per-request logging (the console shows only the start URL)."""

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if status != HTTPStatus.NO_CONTENT:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _forbid(self) -> None:
        self._send_text(HTTPStatus.FORBIDDEN, messages.HTTP_FORBIDDEN)

    # -- checks -----------------------------------------------------------

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip().lower()
        return origin in self.server.allowed_origins

    def _cookie_session_ok(self) -> bool:
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # malformed cookie header
            return False
        morsel = jar.get(COOKIE_NAME)
        return morsel is not None and self.server.session_is_valid(morsel.value)

    # -- dispatch ---------------------------------------------------------

    def _handle(self) -> None:
        self._body_consumed = False
        try:
            self._dispatch()
        finally:
            if self.command != "GET" and not self._body_consumed:
                self._drain_unread_body()

    def _drain_unread_body(self) -> None:
        """Read the body of a request answered without reading it (405, 403, ...).

        Closing a socket that still holds unread request data makes the OS send a reset (Windows reports
        it to the client as a connection abort), which can destroy the response already sent.
        """
        if self.headers.get("Transfer-Encoding"):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if length > 0:
            self._discard(length)

    def _dispatch(self) -> None:
        if self.command != "GET":
            # Request bodies are not kept alive across requests, so the connection is closed after the answer.
            self.close_connection = True
        if not self._host_ok():
            self._forbid()
            return
        split = urlsplit(self.path)
        query = parse_qsl(split.query, keep_blank_values=True)
        token = next((value for key, value in query if key == TOKEN_PARAM), None)

        if self.command == "GET" and token is not None:
            if not self.server.token_is_valid(token):
                self._forbid()
                return
            if "\\" in split.path:  # browsers treat "\\" like "/": would redirect off-origin
                self._forbid()
                return
            self._exchange_token(split.path, [(k, v) for k, v in query if k != TOKEN_PARAM])
            return

        if not self._cookie_session_ok():
            self._forbid()
            return
        if self.command in UNSAFE_METHODS and not self._origin_ok():
            self._forbid()
            return

        if split.path.startswith("/api/"):
            self._route_api(split.path, query)
        elif self.command == "GET":
            self._serve_static(split.path)
        else:
            self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, messages.HTTP_METHOD_NOT_ALLOWED)

    def _exchange_token(self, path: str, remaining_query: list[tuple[str, str]]) -> None:
        session_id = self.server.new_session()
        location = "/" + path.lstrip("/")  # collapse "//host" so it stays a same-origin path
        if remaining_query:
            location += "?" + urlencode(remaining_query)
        cookie = f"{COOKIE_NAME}={session_id}; HttpOnly; SameSite=Strict; Path=/"
        self._send(
            HTTPStatus.FOUND,
            b"",
            "text/plain; charset=utf-8",
            [("Location", location), ("Set-Cookie", cookie)],
        )

    def _route_api(self, path: str, query: list[tuple[str, str]]) -> None:
        if path == "/api/health":
            if self.command == "GET":
                self._send_json(HTTPStatus.OK, health_payload())
            else:
                self._send_json(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": "method_not_allowed", "message": messages.API_METHOD_NOT_ALLOWED},
                )
            return
        params: dict[str, str] = {}
        for key, value in query:
            params.setdefault(key, value)
        result = self.server.api.dispatch(self.command, path, params, self._read_json_body)
        if isinstance(result, Stream):
            self._stream_progress(result)
        elif isinstance(result, Download):
            self._stream_download(result)
        elif result.body is None:
            self._send(HTTPStatus(result.status), b"", "application/json; charset=utf-8")
        else:
            self._send_json(HTTPStatus(result.status), result.body)

    def _read_json_body(self) -> dict[str, Any]:
        """Read and decode the JSON object body; the size is capped before reading."""
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "length_required", messages.API_LENGTH_REQUIRED)
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(411, "length_required", messages.API_LENGTH_REQUIRED)
        try:
            length = int(raw_length)
        except ValueError:
            raise ApiError(400, "bad_content_length", messages.API_BAD_LENGTH) from None
        if length < 0:
            raise ApiError(400, "bad_content_length", messages.API_BAD_LENGTH)
        self._body_consumed = True
        limit = body_limit(urlsplit(self.path).path)
        if length > limit:
            self._discard(length)
            raise ApiError(413, "body_too_large", messages.API_BODY_TOO_LARGE.format(limit=limit))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise ApiError(400, "bad_json", messages.API_BAD_JSON) from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", messages.API_BODY_NOT_OBJECT)
        return data

    def _discard(self, length: int) -> None:
        """Drain an unused body (at most DISCARD_LIMIT bytes, within DRAIN_DEADLINE_SECONDS in total).

        Stops silently on deadline, EOF or any socket error: the answer is already decided, and a slow or
        stalled client must not hold the handler thread.
        """
        remaining = min(length, DISCARD_LIMIT)
        deadline = time.monotonic() + DRAIN_DEADLINE_SECONDS
        sock = self.connection
        try:
            previous = sock.gettimeout()
        except OSError:
            return
        try:
            while remaining > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                sock.settimeout(left)
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (OSError, ValueError):  # includes TimeoutError and ConnectionError
            pass
        finally:
            try:
                sock.settimeout(previous)
            except OSError:
                pass

    def _client_gone(self) -> bool:
        """True when the peer closed the connection (a readable socket that returns no data)."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _stream_progress(self, result: Stream) -> None:
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def write(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

        sse.stream(result.hub, write, self._client_gone, self.server.api.jobs.stopping)

    def _stream_download(self, result: Download) -> None:
        """Send a generated file with chunked transfer encoding (never held in memory). A failure while
        generating ends the connection without the closing chunk, so the client sees an aborted download
        instead of a file that looks complete."""
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in result.headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            if self.command != "HEAD":
                for chunk in result.chunks:
                    if chunk:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except Exception:  # noqa: BLE001  (client gone or a read error: abort the connection)
            pass
        finally:
            close = getattr(result.chunks, "close", None)
            if close is not None:
                close()

    def _serve_static(self, path: str) -> None:
        resource = find_web_resource(path)
        if resource is None:
            self._send_text(HTTPStatus.NOT_FOUND, messages.HTTP_NOT_FOUND)
            return
        suffix = "." + resource.name.rsplit(".", 1)[-1].lower() if "." in resource.name else ""
        content_type = CONTENT_TYPES.get(suffix)
        if content_type is None:
            self._send_text(HTTPStatus.NOT_FOUND, messages.HTTP_NOT_FOUND)
            return
        self._send(HTTPStatus.OK, resource.read_bytes(), content_type)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = _handle  # noqa: N815


def health_payload() -> dict[str, Any]:
    """Return the body of GET /api/health."""
    check = check_sqlite_fts5()
    return {
        "version": __version__,
        "pack_version": None,
        "sqlite": check["sqlite"],
        "fts5": check["fts5"],
        "commit": get_commit(),
    }


def find_web_resource(url_path: str) -> Traversable | None:
    """Map a URL path to a file under the web/ package, or None when not servable."""
    relative = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
    parts = relative.split("/")
    for part in parts:
        if part in ("", ".", "..") or "\\" in part or ":" in part or "\x00" in part:
            return None
    node: Traversable = resources.files("dmslog.web")
    for part in parts:
        node = node.joinpath(part)
    if not node.is_file() or node.name.startswith(("_", ".")):
        return None
    return node


def serve(
    port: int = 0,
    open_browser: bool = True,
    data_dir: str | os.PathLike[str] | None = None,
    watch: dict[str, Any] | None = None,
) -> int:
    """Run the server until Ctrl+C. Returns the process exit code.

    ``watch`` (``path``, ``case``, ``interval``, ``scope``) also follows a Log folder into a case.
    """
    try:
        server = DmslogServer(port, data_dir)
    except OSError as exc:
        print(messages.BIND_FAILED.format(port=port, error=exc), flush=True)
        return 1
    with server:
        if watch is not None:
            try:
                case = server.api.jobs.start_watch(
                    watch["case"], watch["path"], watch["scope"], watch["interval"]
                )
            except ApiError as exc:
                print(exc.message, flush=True)
                return 1
            print(
                messages.SERVE_WATCHING.format(path=watch["path"], case=case, interval=watch["interval"]),
                flush=True,
            )
        url = server.start_url
        print(messages.SERVING.format(url=url), flush=True)
        print(messages.SERVING_HINT, flush=True)
        if open_browser:
            webbrowser.open(url)
        from ..cli import _install_stop_handler

        def interrupt(_signum: int, _frame: Any) -> None:
            raise KeyboardInterrupt  # SIGTERM takes the same coordinated path as Ctrl+C

        restore_signals = _install_stop_handler(interrupt)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            restore_signals()
    print(messages.STOPPED, flush=True)
    return 0
