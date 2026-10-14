import re
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED_SUFFIXES = {".py", ".js", ".html", ".css", ".toml"}
SKIPPED_DIRS = {"__pycache__", "fixtures", "samples", "vendor"}  # literal log text; third-party code

# Latin-1 accented letters, built from escapes so this file itself stays accent-free.
ACCENTED = re.compile("[\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff]")


def _files(*roots):
    for root in roots:
        for path in sorted((ROOT / root).rglob("*")):
            if path.is_file() and path.suffix in SCANNED_SUFFIXES and not SKIPPED_DIRS & set(path.parts):
                yield path


def test_source_and_tests_contain_no_accented_characters():
    offenders = []
    for path in _files("src", "tests"):
        text = path.read_text(encoding="utf-8-sig")
        for number, line in enumerate(text.splitlines(), 1):
            if ACCENTED.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, offenders


def test_web_assets_never_assign_inner_html_or_use_cdn():
    for path in _files("src/dmslog/web"):
        text = path.read_text(encoding="utf-8")
        assert "innerHTML" not in text, path
        assert "outerHTML" not in text, path
        assert not re.search(r"https?://", text), path


_JS_COMMENTS = re.compile(r"/\*.*?\*/|(?<![:\\\"'])//[^\n]*", re.S)
_STYLE_INJECTION = re.compile(
    r"createElement\(\s*[\"'`](?:link|style)[\"'`]\s*\)"  # any quoting, any spacing
    r"|setAttribute\(\s*[\"'`]rel[\"'`]"  # rel set through an attribute
    r"|\.rel\s*=|\brel\s*[:=]\s*[\"'`]stylesheet"  # rel assigned or given in an object literal
    r"|\blink\[rel|\bdocument\.(?:head|styleSheets)\b|\binsertRule\s*\(|\badoptedStyleSheets\b|ensureStylesheet"
)


def injects_styles(source: str) -> bool:
    """True when JS source (comments ignored) creates or inserts a stylesheet or style element."""
    return bool(_STYLE_INJECTION.search(_JS_COMMENTS.sub("", source)))


def test_web_modules_never_create_stylesheet_links():
    # Every stylesheet is listed in index.html (no late injection, no style recalculation after first paint).
    for path in sorted((ROOT / "src/dmslog/web/js").glob("*.js")):
        assert not injects_styles(path.read_text(encoding="utf-8")), path


def test_stylesheet_injection_detector_catches_real_patterns_and_ignores_comments():
    positives = [
        'const l = document.createElement("link"); l.rel = "stylesheet";',
        "document.createElement( `link` )",
        'link.setAttribute("rel", "stylesheet")',
        "document.head.append(link)",
        "document.head.appendChild(style)",
        'const s = document.createElement("style");',
        "sheet.insertRule('a{}')",
        "document.adoptedStyleSheets = [x]",
        'const o = { rel: "stylesheet" };',
        "ensureStylesheet(url)",
    ]
    for source in positives:
        assert injects_styles(source), source
    negatives = [
        "// ensureStylesheet is gone, see index.html",
        "/* document.createElement('link') used to live here */",
        "const url = 'https://x'; // no document.head here",
        'element.className = "link"; const rel = relation + 1;',
    ]
    for source in negatives:
        assert not injects_styles(source), source


def test_package_never_binds_outside_loopback(monkeypatch):
    from dmslog.server.app import DmslogServer

    bound = []
    original_bind = socket.socket.bind

    def recording_bind(self, address):
        bound.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", recording_bind)
    server = DmslogServer(0)
    server.server_close()
    assert bound
    assert all(address[0] == "127.0.0.1" for address in bound)


def test_package_source_has_no_wildcard_bind_address():
    for path in _files("src/dmslog"):
        text = path.read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, path


def _non_loopback(addresses):
    return [a for a in addresses if isinstance(a, tuple) and a[0] not in ("127.0.0.1", "::1")]


def test_loopback_guard_detects_an_external_address():
    assert _non_loopback([("127.0.0.1", 80), ("93.184.216.34", 80)]) == [("93.184.216.34", 80)]


def test_server_flow_never_connects_or_binds_outside_loopback(monkeypatch):
    import http.client
    import threading
    from urllib.parse import urlsplit

    from dmslog.server.app import DmslogServer

    seen = []
    for name in ("bind", "connect", "connect_ex"):
        original = getattr(socket.socket, name)

        def recorder(self, address, *args, _original=original, **kwargs):
            seen.append(address)
            return _original(self, address, *args, **kwargs)

        monkeypatch.setattr(socket.socket, name, recorder)

    server = DmslogServer(0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        start = urlsplit(server.start_url)
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        conn.request("GET", f"{start.path}?{start.query}")
        response = conn.getresponse()
        response.read()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        conn.request("GET", "/api/health", headers={"Cookie": cookie})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert seen
    assert not _non_loopback(seen), _non_loopback(seen)
