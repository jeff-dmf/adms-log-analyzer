"""Smoke test: ingest a synthetic ERS zip, then read a page and an entry through the served API.

Usage: python packaging/smoke_ingest.py <command> [args...]
The command is the launcher prefix (e.g. dmslog.bat, or python3 -I dist/dmslog.pyz); this script
appends `--data-dir <tmp> ingest ...` and `--data-dir <tmp> serve --no-browser --port <free port>`.
Exits non-zero on any failure.
"""

from __future__ import annotations

import http.client
import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_fixture import build_ers_zip  # noqa: E402

from smoke_health import STARTUP_TIMEOUT, check, free_port, request, stop  # noqa: E402

CASE = "smoke"
SUMMARY = re.compile(r"^Records:\s*(\d+)\b", re.MULTILINE)


def api(port: int, method: str, path: str, cookie: str, body: dict | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers = {"Cookie": cookie, "Origin": f"http://127.0.0.1:{port}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, (json.loads(raw) if raw else {})


def ingest(command: list[str], data_dir: Path, zip_path: Path) -> None:
    result = subprocess.run(
        [*command, "--data-dir", str(data_dir), "ingest", str(zip_path), "--case", CASE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    check(result.returncode == 0, "ingest exits with code 0")
    match = SUMMARY.search(result.stdout)
    check(match is not None and int(match.group(1)) > 0, "ingest summary reports records > 0")


def serve_and_query(command: list[str], data_dir: Path) -> None:
    port = free_port()
    proc = subprocess.Popen(
        [*command, "--data-dir", str(data_dir), "serve", "--no-browser", "--port", str(port)],
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
        status, headers, _ = request(port, f"/{parts.query and '?' + parts.query}")
        check(status == 302 and "set-cookie" in headers, "start URL token is exchanged for a cookie")
        cookie = headers["set-cookie"].split(";", 1)[0]

        status, data = api(port, "POST", "/api/entries/page", cookie, {"case": CASE, "limit": 5})
        check(status == 200, "POST /api/entries/page -> 200")
        rows = data.get("rows") or []
        check(len(rows) >= 1, f"page returns at least one row ({len(rows)})")
        row = rows[0]
        check(all(key in row for key in ("id", "ts", "msg")), "row carries id, ts and msg")

        status, entry = api(port, "GET", f"/api/entries/{row['id']}?case={CASE}", cookie)
        check(status == 200, "GET /api/entries/{id} -> 200")
        check(entry.get("id") == row["id"], "entry id matches the row")
        text = entry.get("text") or ""
        check(bool(text) and text.startswith(row["msg"][:40]), "entry text matches the row msg")
    finally:
        stop(proc)


def main() -> None:
    command = sys.argv[1:]
    if not command:
        raise SystemExit(__doc__)
    with tempfile.TemporaryDirectory(prefix="dmslog-smoke-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        zip_path = build_ers_zip(root / "ers")
        data_dir = root / "data"
        ingest(command, data_dir, zip_path)
        serve_and_query(command, data_dir)


if __name__ == "__main__":
    main()
