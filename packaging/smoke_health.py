"""Smoke test: start dmslog serve, check /api/health with and without the session.

Usage: python packaging/smoke_health.py <command> [args...]
The command is the launcher prefix (e.g. dmslog.bat, or python3 dist/dmslog.pyz); this script
appends `serve --no-browser --port <free port>`. Exits non-zero on any failure.
"""
from __future__ import annotations

import http.client
import json
import queue
import re
import socket
import subprocess
import sys
import threading
import urllib.parse

STARTUP_TIMEOUT = 30


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port: int, path: str, cookie: str | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Cookie": cookie} if cookie else {}
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    result = (response.status, {k.lower(): v for k, v in response.getheaders()}, body)
    conn.close()
    return result


def stop(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        # The .bat launcher runs python.exe as a child: kill the whole tree.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SMOKE FAILED: {message}")
    print(f"ok: {message}")


def main() -> None:
    command = sys.argv[1:]
    if not command:
        raise SystemExit(__doc__)
    port = free_port()
    proc = subprocess.Popen(
        [*command, "serve", "--no-browser", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"[server] {line.rstrip()}")
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()
    try:
        url = None
        while url is None:
            try:
                line = lines.get(timeout=STARTUP_TIMEOUT)
            except queue.Empty:
                raise SystemExit("SMOKE FAILED: server did not print its URL") from None
            match = re.search(r"http://\S+", line)
            url = match.group(0) if match else None
        parts = urllib.parse.urlsplit(url)
        check(parts.port == port, f"server listens on the requested port {port}")

        status, _, _ = request(port, "/api/health")
        check(status == 403, "GET /api/health without session -> 403")

        status, headers, _ = request(port, f"/{parts.query and '?' + parts.query}")
        check(status == 302 and "set-cookie" in headers, "start URL token is exchanged for a cookie")
        cookie = headers["set-cookie"].split(";", 1)[0]

        status, headers, body = request(port, "/api/health", cookie)
        check(status == 200, "GET /api/health with cookie -> 200")
        health = json.loads(body)
        print(f"health: {health}")
        check(health.get("fts5") is True, "fts5 is true")
        check("default-src 'self'" in headers.get("content-security-policy", ""), "CSP header present")
        status, _, _ = request(port, "/", cookie)
        check(status == 200, "GET / with cookie -> 200")
    finally:
        stop(proc)


if __name__ == "__main__":
    main()
