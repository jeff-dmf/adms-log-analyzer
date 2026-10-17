"""User-built panels: named lists of declarative aggregations (ROADMAP 9.11, ARQUITETURA 10.14 draft).

A panel is ``{id, rev, name, widgets: [{id, title, query, chart, options}]}``. A widget's ``query`` is
exactly a request of the declarative aggregation (``store/aggregate.py``, 9.6): this module validates it
with :func:`aggregate.parse_request` and the filter compiler, so a panel can never hold SQL or anything
the aggregation would refuse. ``chart`` is ``bar``, ``line``, ``table`` or ``stat`` and ``options`` pick
which columns of the result feed the chart.

Panels live in ``<data dir>/panels.json`` (``schema = 1``), a user file like ``alerts.toml``: a panel
belongs to the user and works in every case, so there is no per-case table and no database migration.
For that reason a widget filter may not point at one case (``source``, ``stream``, ``event`` ids,
``ts``, ``collection``: the same leaves the presets and alerts refuse), and the text and regex
conditions, which need such a scope, are refused too.

The file is versioned: :data:`MIGRATIONS` maps ``n`` to a pure function that turns a schema ``n``
document into a schema ``n + 1`` one (:func:`migrate` chains them). A file of a newer schema, or one
that is not JSON, is never overwritten (``file_invalid``). Reading is tolerant: a panel that fails
validation is reported in ``problems`` (code and index only, never its text) and written back
untouched, so no data is lost silently. Writes are atomic (temporary file + replace) and happen only
after the whole new document validated and fits its size limit.

Everything read from outside (HTTP body, file) is first pre-scanned in linear time for node count,
depth, string length, integer range and finite floats, before any recursive validator or allocation.
"""

from __future__ import annotations

import copy
import json
import math
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import messages
from . import aggregate
from .filters import INT64_MAX, INT64_MIN, QueryInputError, compile_filter
from .presets import CASE_LEAVES, _atomic_write, _walk_leaves
from .views import MAX_NAME_CHARS, ViewError, sanitize_name

SCHEMA = 1
FILENAME = "panels.json"
MAX_PANELS = 20
MAX_WIDGETS = 16
MAX_TITLE_CHARS = 80
MAX_PANEL_ROWS = 4000  # sum of the widgets' ``limit``: bounds the size of one panel run
MAX_TOP = 50
MAX_PANEL_BYTES = 256 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
# Pre-scan limits (before any validator runs).
SCAN_MAX_NODES = 60_000
SCAN_MAX_QUERY_NODES = 5_000
SCAN_MAX_DEPTH = 48  # a filter nests 16 operators, each an object plus a list
SCAN_MAX_STR = 1024
SCAN_MAX_KEY = 128

CHARTS = ("bar", "line", "table", "stat")
QUERY_KEYS = frozenset(
    {"rows", "filter", "events", "group_by", "measures", "order", "limit", "lookups", "exact"}
)
PANEL_KEYS = frozenset({"name", "widgets"})
STORED_KEYS = PANEL_KEYS | {"id", "rev"}
WIDGET_KEYS = frozenset({"id", "title", "query", "chart", "options"})
CHART_OPTIONS: dict[str, frozenset[str]] = {
    "table": frozenset(),
    "stat": frozenset({"y"}),
    "bar": frozenset({"x", "y", "series", "stacked", "top"}),
    "line": frozenset({"x", "y", "series", "stacked", "top"}),
}
_WIDGET_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
_PANEL_ID = re.compile(r"[0-9a-f]{12}\Z")
_REASON = re.compile(r"[a-z_]{1,40}\Z")

LIMITS = {
    "panels": MAX_PANELS,
    "widgets": MAX_WIDGETS,
    "rows": MAX_PANEL_ROWS,
    "title_chars": MAX_TITLE_CHARS,
    "name_chars": MAX_NAME_CHARS,
}


class PanelError(ValueError):
    """A panel operation failed; ``code`` is stable and the message is static per code."""

    messages: Mapping[str, str] = messages.PANEL_ERRORS

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


# --------------------------------------------------------------------------- pre-scan


def bounded(value: Any, max_nodes: int = SCAN_MAX_NODES, max_depth: int = SCAN_MAX_DEPTH) -> bool:
    """True when ``value`` is plain JSON data within the limits (iterative, linear, no recursion).

    Lists and objects are length-checked before their children are queued, so a huge container never
    allocates a work list. Only ``None``, bool, int64, finite floats, short strings and containers pass.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            return False
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if not INT64_MIN <= item <= INT64_MAX:
                return False
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
        elif isinstance(item, str):
            if len(item) > SCAN_MAX_STR:
                return False
        elif isinstance(item, list):
            if nodes + len(item) + len(stack) > max_nodes:
                return False
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if nodes + len(item) + len(stack) > max_nodes:
                return False
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > SCAN_MAX_KEY:
                    return False
                stack.append((child, depth + 1))
        else:
            return False
    return True


def _json_text(value: Any) -> str:
    """Compact ASCII JSON (lone surrogates and non-BMP text survive any encoding)."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _size(value: Any) -> int:
    try:
        return len(_json_text(value))
    except (ValueError, TypeError, RecursionError):
        return MAX_FILE_BYTES + 1


# --------------------------------------------------------------------------- one widget


def _reason(exc: QueryInputError) -> str:
    code = getattr(exc, "code", "")
    return code if isinstance(code, str) and _REASON.match(code) else "invalid"


def validate_query(raw: Any) -> tuple[dict[str, Any], aggregate.AggRequest]:
    """The clean query (an aggregation request) and its parsed form; raises PanelError."""
    if not isinstance(raw, dict):
        raise PanelError("bad_query", reason="bad_type")
    if not bounded(raw, SCAN_MAX_QUERY_NODES):
        raise PanelError("query_too_large")
    if not set(raw) <= QUERY_KEYS:
        raise PanelError("unknown_key")
    query = dict(raw)
    if query.get("filter") == {}:
        del query["filter"]  # an empty tree means "no filter": one spelling only
    try:
        req = aggregate.parse_request(query)
        if req.tree is not None:
            compiled = compile_filter(req.tree)  # also the filter tree's own limits
            if compiled.uses_substr or getattr(compiled, "uses_regex", False):
                raise PanelError("scan_needs_scope")
            if any(leaf in CASE_LEAVES for leaf in _walk_leaves(req.tree)):
                raise PanelError("case_specific")
    except PanelError:
        raise
    except QueryInputError as exc:  # AggregateError and FilterError
        raise PanelError("bad_query", reason=_reason(exc)) from None
    except (RecursionError, ValueError, TypeError):
        raise PanelError("bad_query", reason="invalid") from None
    if _size(query) > MAX_PANEL_BYTES:
        raise PanelError("query_too_large")
    return query, req


def _title(raw: Any) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PanelError("bad_title", limit=MAX_TITLE_CHARS)
    if not raw.strip():
        return ""
    try:
        text = sanitize_name(raw)
    except ViewError:
        raise PanelError("bad_title", limit=MAX_TITLE_CHARS) from None
    if len(text) > MAX_TITLE_CHARS:
        raise PanelError("bad_title", limit=MAX_TITLE_CHARS)
    return text


def _options(chart: str, raw: Any, req: aggregate.AggRequest) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or not set(raw) <= CHART_OPTIONS[chart]:
        raise PanelError("bad_options")
    dims = [d.id for d in req.dims]
    measures = [m.id for m in req.measures]
    buckets = [d.id for d in req.dims if d.kind == "bucket"]
    if chart == "stat" and dims:
        raise PanelError("chart_needs")
    if chart == "bar" and not dims:
        raise PanelError("chart_needs")
    if chart == "line" and not buckets:
        raise PanelError("chart_needs")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ("x", "series", "y"):
            pool = measures if key == "y" else dims
            if key == "x" and chart == "line":
                pool = buckets
            if not isinstance(value, str) or value not in pool:
                raise PanelError("bad_option")
        elif key == "stacked":
            if not isinstance(value, bool):
                raise PanelError("bad_option")
        elif key == "top" and (
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TOP
        ):
            raise PanelError("bad_option")
        out[key] = value
    default_x = buckets[0] if chart == "line" else (dims[0] if dims else None)
    if "series" in out and out["series"] == out.get("x", default_x):
        raise PanelError("bad_option")  # the series must be another dimension than the x axis
    return {k: out[k] for k in sorted(out)}


def validate_widget(raw: Any) -> tuple[dict[str, Any], int]:
    """(clean widget, rows its query may return); raises PanelError."""
    if not isinstance(raw, dict) or not {"id", "query", "chart"} <= set(raw):
        raise PanelError("bad_widget")
    if not set(raw) <= WIDGET_KEYS:
        raise PanelError("unknown_key")
    wid = raw["id"]
    if not isinstance(wid, str) or not _WIDGET_ID.match(wid):
        raise PanelError("bad_widget_id")
    chart = raw["chart"]
    if not isinstance(chart, str) or chart not in CHARTS:
        raise PanelError("bad_chart")
    query, req = validate_query(raw["query"])
    widget = {
        "id": wid,
        "title": _title(raw.get("title")),
        "query": query,
        "chart": chart,
        "options": _options(chart, raw.get("options"), req),
    }
    return widget, req.limit


# --------------------------------------------------------------------------- one panel


def identity(panel: Mapping[str, Any]) -> str:
    return str(panel["name"]).casefold()


def validate_panel(raw: Any, *, stored: bool = False) -> dict[str, Any]:
    """The clean panel; ``stored`` also requires and checks ``id`` and ``rev``. Raises PanelError."""
    if not isinstance(raw, dict):
        raise PanelError("bad_panel")
    if not bounded(raw):
        raise PanelError("bad_data")
    if not set(raw) <= (STORED_KEYS if stored else PANEL_KEYS):
        raise PanelError("unknown_key")
    try:
        name = sanitize_name(raw.get("name"))
    except ViewError:
        raise PanelError("bad_name", limit=MAX_NAME_CHARS) from None
    widgets_raw = raw.get("widgets", [])
    if not isinstance(widgets_raw, list) or len(widgets_raw) > MAX_WIDGETS:
        raise PanelError("bad_widgets", limit=MAX_WIDGETS)
    widgets: list[dict[str, Any]] = []
    ids: set[str] = set()
    rows = 0
    for item in widgets_raw:
        widget, limit = validate_widget(item)
        if widget["id"] in ids:
            raise PanelError("duplicate_widget")
        ids.add(widget["id"])
        rows += limit
        widgets.append(widget)
    if rows > MAX_PANEL_ROWS:
        raise PanelError("too_many_rows", limit=MAX_PANEL_ROWS)
    clean: dict[str, Any] = {"name": name, "widgets": widgets}
    if stored:
        pid, rev = raw.get("id"), raw.get("rev")
        if not isinstance(pid, str) or not _PANEL_ID.match(pid):
            raise PanelError("bad_id")
        if isinstance(rev, bool) or not isinstance(rev, int) or not 1 <= rev <= INT64_MAX:
            raise PanelError("bad_panel")
        clean = {"id": pid, "rev": rev, **clean}
    if _size(clean) > MAX_PANEL_BYTES:
        raise PanelError("panel_too_large", limit=MAX_PANEL_BYTES)
    return clean


# --------------------------------------------------------------------------- versioning

Migration = Callable[[dict[str, Any]], dict[str, Any]]
# n -> pure function: document of schema n in, document of schema n + 1 out (no I/O, no clock). Empty while
# SCHEMA == 1; the first schema change adds ``1: _v1_to_v2`` here and raises SCHEMA.
MIGRATIONS: dict[int, Migration] = {}


class FileInvalid(Exception):
    """The document is unusable as a whole (not an object, bad or newer schema, failing migration)."""


def migrate(
    data: Any, *, migrations: Mapping[int, Migration] | None = None, target: int | None = None
) -> dict[str, Any]:
    """Chain the migrations from the document's schema up to ``target``; the input is not modified."""
    if not isinstance(data, dict):
        raise FileInvalid
    steps = MIGRATIONS if migrations is None else migrations
    target = SCHEMA if target is None else target
    schema = data.get("schema", 1)
    if isinstance(schema, bool) or not isinstance(schema, int) or not 1 <= schema <= target:
        raise FileInvalid  # also a newer schema: this build must not rewrite it
    doc = copy.deepcopy(data)
    while schema < target:
        step = steps.get(schema)
        if step is None:
            raise FileInvalid
        try:
            doc = step(doc)
        except Exception:  # noqa: BLE001 - a broken migration leaves the file untouched
            raise FileInvalid from None
        if not isinstance(doc, dict) or doc.get("schema") != schema + 1:
            raise FileInvalid
        schema += 1
    doc["schema"] = target
    return doc


# --------------------------------------------------------------------------- the file


@dataclass
class Document:
    """Result of reading the file: valid panels, skipped ones (kept verbatim), preserved top-level keys."""

    panels: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[Any] = field(default_factory=list)  # raw entries that failed validation
    problems: list[dict[str, Any]] = field(default_factory=list)  # {"index", "code"}
    top_extra: dict[str, Any] = field(default_factory=dict)
    invalid: bool = False


def parse_document(data: Any, *, migrations: Mapping[int, Migration] | None = None) -> Document:
    doc = Document()
    try:
        data = migrate(data, migrations=migrations)
    except FileInvalid:
        doc.invalid = True
        return doc
    doc.top_extra = {k: v for k, v in data.items() if k not in ("schema", "panels")}
    raw_list = data.get("panels", [])
    if not isinstance(raw_list, list) or len(raw_list) > MAX_PANELS * 2 or not bounded(doc.top_extra, 1000):
        doc.invalid = True
        return doc
    ids: set[str] = set()
    names: set[str] = set()
    for index, raw in enumerate(raw_list):
        try:
            clean = validate_panel(raw, stored=True)
            if clean["id"] in ids or identity(clean) in names:
                raise PanelError("duplicate_name")
            if len(doc.panels) >= MAX_PANELS:
                raise PanelError("too_many_panels", limit=MAX_PANELS)
        except PanelError as exc:
            doc.problems.append({"index": index, "code": exc.code})
            doc.skipped.append(raw)
            continue
        ids.add(clean["id"])
        names.add(identity(clean))
        doc.panels.append(clean)
    return doc


def panels_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILENAME


_LOCK = threading.Lock()  # one writer at a time (read-modify-write) within the server process


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def load(data_dir: str | Path) -> Document:
    """The user's panels; a missing file is empty, an unreadable or invalid one sets ``invalid``."""
    try:
        with panels_path(data_dir).open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return Document()
    except OSError:
        return Document(invalid=True)
    if len(raw) > MAX_FILE_BYTES:
        return Document(invalid=True)
    try:
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Document(invalid=True)
    if not bounded(data, SCAN_MAX_NODES * (MAX_PANELS * 2 + 1), SCAN_MAX_DEPTH + 8):
        return Document(invalid=True)
    return parse_document(data)


def _save(data_dir: str | Path, doc: Document) -> None:
    body = {"schema": SCHEMA, **doc.top_extra, "panels": [*doc.panels, *doc.skipped]}
    try:
        data = (json.dumps(body, ensure_ascii=True, allow_nan=False, indent=1) + "\n").encode("ascii")
    except (ValueError, TypeError, RecursionError):
        raise PanelError("save_failed") from None
    if len(data) > MAX_FILE_BYTES:  # the next read would reject it: keep the old file
        raise PanelError("file_too_large", limit=MAX_FILE_BYTES)
    try:
        _atomic_write(panels_path(data_dir), data)
    except OSError:
        raise PanelError("save_failed") from None


def _writable(data_dir: str | Path) -> Document:
    doc = load(data_dir)
    if doc.invalid:
        raise PanelError("file_invalid")
    return doc


def list_panels(data_dir: str | Path) -> dict[str, Any]:
    doc = load(data_dir)
    return {"panels": doc.panels, "invalid": doc.invalid, "problems": doc.problems, "limits": LIMITS}


def get_panel(data_dir: str | Path, panel_id: Any) -> dict[str, Any]:
    if not isinstance(panel_id, str) or not _PANEL_ID.match(panel_id):
        raise PanelError("bad_id")
    doc = load(data_dir)
    if doc.invalid:
        raise PanelError("file_invalid")
    found = next((p for p in doc.panels if p["id"] == panel_id), None)
    if found is None:
        raise PanelError("not_found")
    return found


def create(data_dir: str | Path, raw: Any) -> dict[str, Any]:
    clean = validate_panel(raw)
    with _LOCK:
        doc = _writable(data_dir)
        if any(identity(p) == identity(clean) for p in doc.panels):
            raise PanelError("duplicate_name")
        if len(doc.panels) >= MAX_PANELS:
            raise PanelError("too_many_panels", limit=MAX_PANELS)
        taken = {p["id"] for p in doc.panels}
        pid = secrets.token_hex(6)
        while pid in taken:
            pid = secrets.token_hex(6)
        panel = {"id": pid, "rev": 1, **clean}
        doc.panels.append(panel)
        _save(data_dir, doc)  # nothing is committed in memory: ``doc`` is local
        return panel


def update(data_dir: str | Path, panel_id: Any, raw: Any, if_rev: Any = None) -> dict[str, Any]:
    """Replace a panel's name and widgets; ``if_rev`` (the revision the caller saw) guards lost updates."""
    if not isinstance(panel_id, str) or not _PANEL_ID.match(panel_id):
        raise PanelError("bad_id")
    if if_rev is not None and (isinstance(if_rev, bool) or not isinstance(if_rev, int)):
        raise PanelError("rev_conflict")
    clean = validate_panel(raw)
    with _LOCK:
        doc = _writable(data_dir)
        index = next((k for k, p in enumerate(doc.panels) if p["id"] == panel_id), None)
        if index is None:
            raise PanelError("not_found")
        current = doc.panels[index]
        if if_rev is not None and if_rev != current["rev"]:
            raise PanelError("rev_conflict")
        if any(identity(p) == identity(clean) for k, p in enumerate(doc.panels) if k != index):
            raise PanelError("duplicate_name")
        panel = {"id": panel_id, "rev": current["rev"] + 1, **clean}
        doc.panels[index] = panel
        _save(data_dir, doc)
        return panel


def delete(data_dir: str | Path, panel_id: Any) -> None:
    if not isinstance(panel_id, str) or not _PANEL_ID.match(panel_id):
        raise PanelError("bad_id")
    with _LOCK:
        doc = _writable(data_dir)
        kept = [p for p in doc.panels if p["id"] != panel_id]
        if len(kept) == len(doc.panels):
            raise PanelError("not_found")
        doc.panels = kept
        _save(data_dir, doc)
