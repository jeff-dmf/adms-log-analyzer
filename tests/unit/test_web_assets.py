"""Static checks of the viewer assets, the Node unit tests, and serving from the built .pyz."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "dmslog" / "web"
JS_DIR = WEB / "js"
EXPECTED_MODULES = {
    "api.js",
    "clock-banner.js",
    "config-panel-model.js",
    "config-panel.js",
    "dashboard-model.js",
    "dashboard.js",
    "detail-model.js",
    "detail.js",
    "events-model.js",
    "events-panel.js",
    "filter-tree-model.js",
    "filter-tree.js",
    "filters.js",
    "grid-model.js",
    "grid.js",
    "knowledge-model.js",
    "knowledge.js",
    "main.js",
    "saved-views.js",
    "search.js",
    "sources.js",
    "strings.js",
    "theme.js",
    "time.js",
    "timeline-model.js",
    "timeline.js",
    "unknown-panel-model.js",
    "unknown-panel.js",
    "key-panel-model.js",
    "key-panel.js",
    "links-model.js",
    "links.js",
    "side-by-side.js",
    "payload-model.js",
    "payload.js",
    "payload-list-model.js",
    "payload-list.js",
    "export-model.js",
    "export-panel.js",
    "redaction-panel.js",
    "bookmarks-model.js",
    "bookmarks-panel.js",
    "compare-model.js",
    "compare.js",
    "saved-views-model.js",
    "presets.js",
    "presets-model.js",
    "follow-tail-model.js",
    "follow-tail.js",
    "copy-export-model.js",
    "copy-export.js",
    "column-resize-model.js",
    "column-resize.js",
    "source-filter-model.js",
    "source-filter.js",
    "distinct-model.js",
    "distinct-panel.js",
    "highlight-model.js",
    "highlight-panel.js",
    "case-files-model.js",
    "case-files.js",
    "folder-browser-model.js",
    "folder-browser.js",
}

FORBIDDEN_JS = [
    r"\binnerHTML\b",
    r"\bouterHTML\b",
    r"\binsertAdjacentHTML\b",
    r"\bdocument\.write\b",
    r"\beval\s*\(",
    r"\bnew\s+Function\b",
    r"\bsetTimeout\s*\(\s*[\"'`]",
    r"\bsetInterval\s*\(\s*[\"'`]",
    r"setAttribute\(\s*[\"']style[\"']",
    r"\bDOMParser\b",
    r"\bcreateContextualFragment\b",
    r"\bsrcdoc\b",
]


def js_files():
    return sorted(JS_DIR.glob("*.js"))


def web_files():
    return [WEB / "index.html", *sorted((WEB / "css").glob("*.css")), *js_files()]


def test_expected_modules_exist():
    assert {p.name for p in js_files()} == EXPECTED_MODULES


@pytest.mark.parametrize("pattern", FORBIDDEN_JS)
def test_no_unsafe_dom_or_code_execution_apis(pattern):
    for path in web_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(pattern, text), f"{path.name}: {pattern}"


def test_vendored_uplot_matches_its_recorded_checksums():
    folder = WEB / "vendor" / "uplot"
    notes = (folder / "VENDOR.md").read_text(encoding="utf-8")
    assert "MIT" in (folder / "LICENSE").read_text(encoding="utf-8")
    for name in ("uPlot.esm.js", "uPlot.min.css", "LICENSE"):
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        assert re.search(rf"\|\s*{re.escape(name)}\s*\|\s*{digest}\s*\|", notes), name


def test_app_code_does_not_load_anything_from_outside_vendor():
    # vendor/ is third-party code and is exempt from the scans above; app code is not.
    assert all("vendor" not in path.parts for path in web_files())
    for path in js_files():
        for target in re.findall(r"""from\s+["'](\.\./[^"']+)["']""", path.read_text(encoding="utf-8")):
            assert target.startswith("../vendor/") and (JS_DIR / target).resolve().is_file(), target


def test_no_absolute_urls_anywhere():
    for path in web_files():
        assert not re.search(r"https?://", path.read_text(encoding="utf-8")), path.name


class _HtmlScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems = []
        self.scripts = []
        self.stylesheets = []
        self.texts = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        self._stack.append(tag)
        for name, _value in attrs:
            if name.startswith("on"):
                self.problems.append(f"event handler attribute {name}")
            if name == "style":
                self.problems.append("inline style attribute")
        values = dict(attrs)
        if tag == "script":
            if values.get("src"):
                self.scripts.append(values["src"])
            else:
                self.problems.append("inline <script>")
        if tag == "style":
            self.problems.append("inline <style>")
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if data.strip() and (not self._stack or self._stack[-1] != "title"):
            self.texts.append(data.strip())


def scan_html():
    scan = _HtmlScan()
    scan.feed((WEB / "index.html").read_text(encoding="utf-8"))
    return scan


def test_html_has_no_inline_code_style_or_handlers():
    assert scan_html().problems == []


def test_html_references_existing_local_assets():
    scan = scan_html()
    assert scan.scripts and scan.stylesheets
    for ref in [*scan.scripts, *scan.stylesheets]:
        assert ref.startswith("/") and not ref.startswith("//"), ref
        assert (WEB / ref.lstrip("/")).is_file(), ref


def test_html_has_no_visible_text_of_its_own():
    # Labels come from strings.js through data-i18n attributes.
    assert scan_html().texts == []


def test_relative_imports_resolve_to_existing_modules():
    for path in js_files():
        for target in re.findall(r"""from\s+["'](\./[^"']+)["']""", path.read_text(encoding="utf-8")):
            assert (JS_DIR / target).is_file(), f"{path.name} imports {target}"


def test_every_i18n_key_used_in_html_exists_in_strings():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    strings = (JS_DIR / "strings.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n[\w-]*="(\w+)"', html))
    assert keys
    for key in keys:
        assert re.search(rf"^\s{{2}}{key}:", strings, re.MULTILINE), key


LITERAL_SINKS = [
    r"\.textContent\s*=\s*[\"'`][^\"'`]*[A-Za-z]",
    r"createTextNode\(\s*[\"'`][^\"'`]*[A-Za-z]",
    r"\.(placeholder|title|alt|ariaLabel)\s*=\s*[\"'`]",
    r"setAttribute\(\s*[\"'](aria-label|title|placeholder|alt)[\"']\s*,\s*[\"'`]",
    r"new\s+(Error|ApiError)\([^)]*[\"'`][A-Za-z]+ [A-Za-z ]+[\"'`]",
]


@pytest.mark.parametrize("pattern", LITERAL_SINKS)
def test_ui_text_comes_only_from_strings_js(pattern):
    for path in js_files():
        if path.name == "strings.js":
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(pattern, text), f"{path.name}: {pattern}"


# -- Node unit tests -------------------------------------------------------------------------


def _node_ok():
    node = shutil.which("node")
    if node is None:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    match = re.match(r"v(\d+)\.(\d+)", out)
    if not match or (int(match.group(1)), int(match.group(2))) < (22, 12):
        return None  # needs ES modules in plain .js files without a flag
    return node


@pytest.mark.parametrize("zone", ["UTC", "America/Sao_Paulo", "Asia/Kolkata"])
def test_node_unit_tests_pass_in_any_time_zone(zone):
    node = _node_ok()
    if node is None:
        pytest.skip("node >= 22.12 not available")
    env = {**os.environ, "TZ": zone}
    # Explicit files: older Node versions treat a directory argument as a module to load.
    test_files = sorted(str(path) for path in (ROOT / "tests" / "web").glob("*.test.mjs"))
    assert test_files, "no Node test files found"
    result = subprocess.run(
        [node, "--test", *test_files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_web_module_passes_node_syntax_check():
    """`node --check` on every shipped module: a merge slip in a DOM-only module (main.js) must not pass."""
    node = _node_ok()
    if node is None:
        pytest.skip("node >= 22.12 not available")
    failures = []
    for path in sorted(js_files()):
        result = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8", check=False
        )
        if result.returncode != 0:
            failures.append(f"{path.name}: {result.stderr.strip().splitlines()[:3]}")
    assert not failures, failures


# -- Served from the built .pyz ---------------------------------------------------------------


def test_pyz_serves_viewer_html_js_and_css(tmp_path, monkeypatch):
    packaging = ROOT / "packaging"
    monkeypatch.setenv("DMSLOG_COMMIT", "testcommit")
    spec = importlib.util.spec_from_file_location("build_pyz_web", packaging / "build_pyz.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_pyz_web"] = module
    sys.path.insert(0, str(packaging))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(packaging))
    monkeypatch.setattr(module, "DIST", tmp_path)
    monkeypatch.setattr(module, "TARGET", tmp_path / "dmslog.pyz")
    pyz = module.build()

    code = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import threading, http.client\n"
        "from dmslog.server.app import DmslogServer\n"
        "s = DmslogServer(0)\n"
        "threading.Thread(target=s.serve_forever, daemon=True).start()\n"
        "def get(path, cookie=None):\n"
        "    c = http.client.HTTPConnection('127.0.0.1', s.port)\n"
        "    c.request('GET', path, headers={'Cookie': cookie} if cookie else {})\n"
        "    r = c.getresponse(); body = r.read(); c.close()\n"
        "    return r, body\n"
        "r, _ = get('/?token=' + s.token)\n"
        "cookie = r.getheader('Set-Cookie').split(';')[0]\n"
        "out = {}\n"
        "for path in sys.argv[2:]:\n"
        "    r, body = get(path, cookie)\n"
        "    out[path] = [r.status, r.getheader('Content-Type'), len(body)]\n"
        "print(json.dumps(out))\n"
    )
    paths = [
        "/", "/css/app.css", "/css/timeline.css", "/css/unknown-panel.css",
        "/css/grid-extras.css", "/css/filter-tree.css", "/css/config-panel.css", "/css/knowledge.css",
        "/css/events.css", "/css/dashboard.css", "/css/links.css", "/css/key-panel.css",
        "/css/payload.css", "/css/payload-list.css",
        "/css/export.css",
        "/css/bookmarks.css",
        "/css/column-resize.css",
        "/css/bookmarks.css", "/css/presets.css",
        "/css/case-files.css",
        "/vendor/uplot/uPlot.esm.js", "/vendor/uplot/uPlot.min.css",
        *(f"/js/{name}" for name in sorted(EXPECTED_MODULES)),
    ]  # fmt: skip
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(pyz), *paths],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    served = json.loads(result.stdout.strip().splitlines()[-1])
    assert set(served) == set(paths)
    for path, (status, content_type, size) in served.items():
        assert status == 200, path
        assert size > 0, path
        if path.endswith(".js"):
            assert content_type.startswith("text/javascript"), path
    assert served["/"][1].startswith("text/html")
    assert served["/css/app.css"][1].startswith("text/css")
