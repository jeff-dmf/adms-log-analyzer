"""Local alerts of the follow mode (``dmslog serve --watch``): rules, evaluation and alert history.

Rules live in ``<data dir>/alerts.toml`` (``schema = 1``), a user file like ``presets.toml``: alerts belong to
the user and apply to every case, so there is no per-case table (and no database migration). The file never
holds paths or data of a case: a filter that points at one case (``source``, ``stream``, ``event`` ids,
``ts`` ranges) is rejected, exactly as for presets. Reading is tolerant (a missing file is empty, an invalid
rule is skipped and reported, unknown keys are preserved); a file that is not valid TOML, or has a newer
schema, is never overwritten (``file_invalid``).

A rule is ``name`` + ``kind`` + ``severity`` + ``enabled`` + ``min_interval_s`` and the fields of its kind:

* ``match``: ``filter`` (the viewer's JSON tree, e.g. ``{"tpl": "WFM-081"}`` or the tree of a preset) and
  ``min_count`` (default 1): the cycle brought at least ``min_count`` new records that match.
* ``threshold``: ``filter``, ``threshold`` and ``window_s``: the cycle brought a match and the matches inside
  the ``window_s`` seconds that end at the newest one are more than ``threshold``.
* ``open_operation``: ``timeout_s`` or ``timeout_param`` (the name of a Registry parameter of the case, e.g.
  ``SendTimeout``) and optionally ``operation``: an operation of the case is still ``open`` (no end seen)
  although more than the timeout elapsed on the log's own clock (newest record of its stream).
  Without a resolvable timeout the rule is reported as unavailable instead of guessing.

Evaluation (:meth:`AlertState.process_cycle`) runs after each follow cycle and looks only at the records of
that cycle (``entry.id > baseline``, keyset on the primary key, parameterized, row-limited, under a query
timeout). One alert per rule per cycle; ``min_interval_s`` is the minimum delay between two alerts of a
rule. An alert carries only ids, counts, time bounds and the rule name; never log text.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import tomllib
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import messages
from . import query
from .config import _timespan, product_for_process
from .filters import FilterError, compile_filter
from .presets import (
    CASE_LEAVES,
    MAX_EXTRA_BYTES,
    MAX_FILE_BYTES,
    PresetError,
    _atomic_write,
    _small,
    _toml_key,
    _toml_string,
    _toml_value,
    _walk_leaves,
)
from .views import MAX_NAME_CHARS, ViewError, sanitize_name, validate_filter

SCHEMA = 1
FILENAME = "alerts.toml"
MAX_RULES = 100
KINDS = ("match", "threshold", "open_operation")
SEVERITIES = ("info", "warning", "critical")
FILTER_KINDS = frozenset({"match", "threshold"})
DEFAULT_MIN_INTERVAL_S = 30
MAX_MIN_INTERVAL_S = 86400
MAX_COUNT = 1_000_000
MAX_WINDOW_S = 86400
MAX_TIMEOUT_S = 7 * 86400
MAX_OPERATION_CHARS = 128
PARAM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
KNOWN_KEYS = frozenset(
    {"name", "kind", "enabled", "severity", "min_interval_s", "filter", "min_count", "threshold",
     "window_s", "timeout_s", "timeout_param", "operation"}
)  # fmt: skip

# Evaluation limits.
EVAL_TIMEOUT_S = 5.0
ROW_LIMIT = 100_000  # rows a rule looks at in one cycle
SAMPLE_IDS = 5
MAX_EVENT_IDS = 20
MAX_OPEN_EVENTS = 1000
HISTORY_LIMIT = 200
SEEN_LIMIT = 10_000


class AlertError(ValueError):
    """An alert operation failed; ``code`` is stable and ``params`` fill the message."""

    messages: Mapping[str, str] = messages.ALERT_ERRORS

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


# --------------------------------------------------------------------------- one rule


def _int_field(
    raw: Mapping[str, Any], key: str, lo: int, hi: int, code: str, default: int | None = None
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise AlertError(code, lo=lo, hi=hi)
    return value


def identity(rule: Mapping[str, Any]) -> str:
    return str(rule["name"]).casefold()


def validate_rule(raw: Any) -> dict[str, Any]:
    """Return the clean rule (unknown keys kept in ``extra``); raises AlertError."""
    if not isinstance(raw, dict):
        raise AlertError("bad_rule")
    try:
        name = sanitize_name(raw.get("name"))
    except ViewError:
        raise AlertError("bad_name", limit=MAX_NAME_CHARS) from None
    kind = raw.get("kind")
    if kind not in KINDS:
        raise AlertError("bad_kind")
    severity = raw.get("severity", "warning")
    if severity not in SEVERITIES:
        raise AlertError("bad_severity")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise AlertError("bad_enabled")
    clean: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "enabled": enabled,
        "severity": severity,
        "min_interval_s": _int_field(
            raw, "min_interval_s", 0, MAX_MIN_INTERVAL_S, "bad_interval", DEFAULT_MIN_INTERVAL_S
        ),
    }
    if kind in FILTER_KINDS:
        tree = raw.get("filter")
        try:
            validate_filter(tree)
        except FilterError as exc:
            raise AlertError("filter_invalid", detail=str(exc)) from None
        except ViewError as exc:
            if exc.code == "filter_too_large":
                raise AlertError("filter_too_large", **exc.params) from None
            raise AlertError("bad_filter") from None
        for leaf in _walk_leaves(tree):
            if leaf in CASE_LEAVES:
                raise AlertError("case_specific", leaf=leaf)
        clean["filter"] = tree
        if kind == "match":
            clean["min_count"] = _int_field(raw, "min_count", 1, MAX_COUNT, "bad_count", 1)
        else:
            clean["threshold"] = _int_field(raw, "threshold", 1, MAX_COUNT, "bad_count")
            clean["window_s"] = _int_field(raw, "window_s", 1, MAX_WINDOW_S, "bad_window")
    else:
        timeout_s, param = raw.get("timeout_s"), raw.get("timeout_param")
        if (timeout_s is None) == (param is None):
            raise AlertError("bad_timeout_source")
        if timeout_s is not None:
            clean["timeout_s"] = _int_field(raw, "timeout_s", 1, MAX_TIMEOUT_S, "bad_timeout")
        else:
            if not isinstance(param, str) or not PARAM_PATTERN.match(param):
                raise AlertError("bad_timeout_param")
            clean["timeout_param"] = param
        operation = raw.get("operation")
        if operation is not None:
            if (
                not isinstance(operation, str)
                or not operation.strip()
                or len(operation) > MAX_OPERATION_CHARS
            ):
                raise AlertError("bad_operation", limit=MAX_OPERATION_CHARS)
            clean["operation"] = operation.strip()
    extra = {k: v for k, v in raw.items() if k not in KNOWN_KEYS}
    if extra:
        if not _small(extra, MAX_EXTRA_BYTES):
            raise AlertError("extra_too_large")
        clean["extra"] = extra
    try:
        dump_toml([clean])  # a value TOML cannot hold (a null in the filter) is refused here
    except (PresetError, RecursionError, UnicodeEncodeError):
        raise AlertError("bad_rule") from None
    return clean


def public(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rule.items() if k != "extra"}


# --------------------------------------------------------------------------- TOML text


_FIELD_ORDER = (
    "kind", "enabled", "severity", "min_interval_s", "filter", "min_count", "threshold", "window_s",
    "timeout_s", "timeout_param", "operation",
)  # fmt: skip


def dump_toml(rules: list[dict[str, Any]], top_extra: Mapping[str, Any] | None = None) -> str:
    """The rules document as TOML text (LF, UTF-8)."""
    lines = [
        "# dmslog local alerts (user file). Local to this machine; filters are the JSON trees of the viewer.",
        f"schema = {SCHEMA}",
    ]
    for key, value in (top_extra or {}).items():
        if key not in ("schema", "rule"):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for rule in rules:
        lines += ["", "[[rule]]", f"name = {_toml_string(rule['name'])}"]
        for key in _FIELD_ORDER:
            if key in rule:
                lines.append(f"{key} = {_toml_value(rule[key])}")
        for key, value in (rule.get("extra") or {}).items():
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the file


@dataclass
class Document:
    """Result of reading the file: valid rules, skipped ones, preserved top-level keys."""

    rules: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)  # {"index", "name", "code", "message"}
    top_extra: dict[str, Any] = field(default_factory=dict)
    invalid: bool = False  # the file as a whole is unusable (not TOML, wrong schema)


def parse_document(data: Any) -> Document:
    doc = Document()
    if not isinstance(data, dict):
        doc.invalid = True
        return doc
    schema = data.get("schema", SCHEMA)
    if isinstance(schema, bool) or not isinstance(schema, int) or not 1 <= schema <= SCHEMA:
        doc.invalid = True
        return doc
    doc.top_extra = {k: v for k, v in data.items() if k not in ("schema", "rule")}
    if doc.top_extra and not _small(doc.top_extra, MAX_EXTRA_BYTES):
        doc.invalid = True
        return doc
    raw_list = data.get("rule", [])
    if not isinstance(raw_list, list):
        doc.invalid = True
        return doc
    seen: set[str] = set()
    for index, raw in enumerate(raw_list[: MAX_RULES * 2]):
        label = raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else ""
        try:
            clean = validate_rule(raw)
            if identity(clean) in seen:
                raise AlertError("duplicate_name", name=clean["name"])
            if len(doc.rules) >= MAX_RULES:
                raise AlertError("too_many_rules", limit=MAX_RULES)
        except AlertError as exc:
            doc.problems.append({"index": index, "name": label[:MAX_NAME_CHARS], "code": exc.code,
                                 "message": str(exc)})  # fmt: skip
            continue
        seen.add(identity(clean))
        doc.rules.append(clean)
    return doc


def alerts_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILENAME


_LOCK = threading.Lock()


def load(data_dir: str | Path) -> Document:
    """The user's rules; a missing file is empty, an unreadable or invalid file sets ``invalid``."""
    path = alerts_path(data_dir)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return Document(invalid=True)
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Document()
    except (OSError, UnicodeDecodeError):
        return Document(invalid=True)
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        return Document(invalid=True)
    return parse_document(data)


def _save(data_dir: str | Path, doc: Document) -> None:
    try:
        data = dump_toml(doc.rules, doc.top_extra).encode("utf-8")
    except (PresetError, UnicodeEncodeError, RecursionError):
        raise AlertError("save_failed") from None
    if len(data) > MAX_FILE_BYTES:
        raise AlertError("file_too_large", limit=MAX_FILE_BYTES)
    try:
        _atomic_write(alerts_path(data_dir), data)
    except OSError:
        raise AlertError("save_failed") from None


def _writable(data_dir: str | Path) -> Document:
    doc = load(data_dir)
    if doc.invalid:
        raise AlertError("file_invalid")
    return doc


def list_rules(data_dir: str | Path) -> dict[str, Any]:
    doc = load(data_dir)
    return {"rules": [public(r) for r in doc.rules], "invalid": doc.invalid, "problems": doc.problems}


def create(data_dir: str | Path, raw: Any) -> dict[str, Any]:
    clean = validate_rule(raw)
    with _LOCK:
        doc = _writable(data_dir)
        if any(identity(r) == identity(clean) for r in doc.rules):
            raise AlertError("duplicate_name", name=clean["name"])
        if len(doc.rules) >= MAX_RULES:
            raise AlertError("too_many_rules", limit=MAX_RULES)
        doc.rules.append(clean)
        _save(data_dir, doc)
    return public(clean)


def update(data_dir: str | Path, name: Any, raw: Any) -> dict[str, Any]:
    """Replace the rule ``name`` by ``raw`` (rename, other filter, switch on or off)."""
    old = str(name).casefold()
    clean = validate_rule(raw)
    with _LOCK:
        doc = _writable(data_dir)
        index = next((i for i, r in enumerate(doc.rules) if identity(r) == old), None)
        if index is None:
            raise AlertError("not_found", name=str(name))
        if any(i != index and identity(r) == identity(clean) for i, r in enumerate(doc.rules)):
            raise AlertError("duplicate_name", name=clean["name"])
        if "extra" not in clean and "extra" in doc.rules[index]:
            clean["extra"] = doc.rules[index]["extra"]
        doc.rules[index] = clean
        _save(data_dir, doc)
    return public(clean)


def delete(data_dir: str | Path, name: Any) -> None:
    key = str(name).casefold()
    with _LOCK:
        doc = _writable(data_dir)
        kept = [r for r in doc.rules if identity(r) != key]
        if len(kept) == len(doc.rules):
            raise AlertError("not_found", name=str(name))
        doc.rules = kept
        _save(data_dir, doc)


# --------------------------------------------------------------------------- timeouts of open operations

_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*\Z")
_UNITS = {
    "": 1.0, "ms": 0.001, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0, "h": 3600.0, "hour": 3600.0,
    "hours": 3600.0,
}  # fmt: skip


def parse_duration_s(text: str | None) -> float | None:
    """Seconds of a configuration value: a number (seconds), ``30 s``, ``1 min`` or a .NET TimeSpan."""
    if not text:
        return None
    match = _DURATION.match(text)
    if match is not None:
        unit = _UNITS.get(match.group(2).casefold())
        if unit is None:
            return None
        seconds = float(match.group(1)) * unit
    else:
        seconds = _timespan(text)
    return seconds if seconds is not None and 0 < seconds <= MAX_TIMEOUT_S else None


def _param_seconds(conn: sqlite3.Connection, group: int, product: str, name: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM config_param WHERE collection_group = ? AND product = ? AND scope = 'registry'"
        " AND key_path = '' AND name = ? AND effective = 1 LIMIT 1",
        (group, product, name),
    ).fetchone()
    return parse_duration_s(row[0]) if row else None


def rule_status(conn: sqlite3.Connection, rule: Mapping[str, Any]) -> dict[str, Any]:
    """``{"available": bool, "reason": code|None}``: can this rule work in the case?"""
    if rule["kind"] != "open_operation" or "timeout_param" not in rule:
        return {"available": True, "reason": None}
    try:
        rows = conn.execute(
            "SELECT value FROM config_param WHERE scope = 'registry' AND key_path = '' AND name = ?"
            " AND effective = 1 LIMIT 50",
            (rule["timeout_param"],),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if any(parse_duration_s(r[0]) is not None for r in rows):
        return {"available": True, "reason": None}
    return {"available": False, "reason": "timeout_unavailable"}


# --------------------------------------------------------------------------- evaluation


@dataclass(slots=True)
class Alert:
    rule: str
    kind: str
    severity: str
    count: int
    sample_ids: list[int] = field(default_factory=list)
    ts_from: int | None = None
    ts_to: int | None = None
    event_ids: list[int] = field(default_factory=list)

    def to_event(self) -> dict[str, Any]:
        """Body of the ``alert`` SSE event and the history: ids and numbers only, never log text."""
        return {
            "rule": self.rule, "kind": self.kind, "severity": self.severity, "count": self.count,
            "sample_ids": self.sample_ids, "ts_from": self.ts_from, "ts_to": self.ts_to,
            "event_ids": self.event_ids,
        }  # fmt: skip


def _eval_match(conn: sqlite3.Connection, rule: Mapping[str, Any], lo: int, hi: int) -> Alert | None:
    cf = compile_filter(rule["filter"], rowwise=True)  # the rows are known: per-row lookups beat set scans
    with query.read_guard(conn, EVAL_TIMEOUT_S):
        n, ts_from, ts_to = conn.execute(
            "SELECT count(*), min(ts), max(ts) FROM"
            f" (SELECT e.ts FROM entry e WHERE e.id > ? AND e.id <= ? AND ({cf.sql}) LIMIT ?)",
            (lo, hi, *cf.params, ROW_LIMIT),
        ).fetchone()
        if n < rule.get("min_count", 1) or n == 0:
            return None
        ids = [
            r[0]
            for r in conn.execute(
                f"SELECT e.id FROM entry e WHERE e.id > ? AND e.id <= ? AND ({cf.sql}) ORDER BY e.id LIMIT ?",
                (lo, hi, *cf.params, SAMPLE_IDS),
            )
        ]
    return Alert(rule["name"], rule["kind"], rule["severity"], n, ids, ts_from, ts_to)


def _eval_threshold(conn: sqlite3.Connection, rule: Mapping[str, Any], lo: int, hi: int) -> Alert | None:
    fresh = _eval_match(conn, {**rule, "min_count": 1}, lo, hi)
    if fresh is None or fresh.ts_to is None:
        return None
    cf = compile_filter(rule["filter"])
    start = fresh.ts_to - rule["window_s"] * 1000
    with query.read_guard(conn, EVAL_TIMEOUT_S):
        (n,) = conn.execute(
            "SELECT count(*) FROM"
            f" (SELECT 1 FROM entry e WHERE e.ts >= ? AND e.ts <= ? AND ({cf.sql}) LIMIT ?)",
            (start, fresh.ts_to, *cf.params, ROW_LIMIT),
        ).fetchone()
    if n <= rule["threshold"]:
        return None
    return Alert(rule["name"], rule["kind"], rule["severity"], n, fresh.sample_ids, start, fresh.ts_to)


def _eval_open(
    conn: sqlite3.Connection, rule: Mapping[str, Any], seen: set[tuple[int, int, str]]
) -> tuple[Alert | None, list[tuple[int, int, str]]]:
    """Operations still open after the rule's timeout; ``seen`` holds the ones already alerted."""
    sql = (
        "SELECT ev.id, ev.stream_id, ev.name, ev.ts_start, ev.start_entry_id, st.collection_group, st.process"
        " FROM event ev JOIN stream st ON st.id = ev.stream_id"
        " WHERE ev.kind = 'operation' AND ev.status = 'open'"
    )
    params: list[Any] = []
    if "operation" in rule:
        sql += " AND ev.name = ?"
        params.append(rule["operation"])
    sql += " ORDER BY ev.ts_start, ev.id LIMIT ?"
    params.append(MAX_OPEN_EVENTS)
    products: dict[int, set[str]] = {}
    timeouts: dict[tuple[int, str | None], float | None] = {}
    clocks: dict[int, int | None] = {}
    found: list[tuple[int, int, int, str, int | None]] = []
    with query.read_guard(conn, EVAL_TIMEOUT_S):
        for ev_id, stream_id, name, ts_start, start_entry, group, process in conn.execute(
            sql, params
        ).fetchall():
            key = (stream_id, ts_start, name or "")
            if key in seen:
                continue
            seconds = rule.get("timeout_s")
            if seconds is None:
                if (group, process) not in timeouts:
                    if group not in products:
                        products[group] = {
                            r[0] for r in conn.execute(
                                "SELECT DISTINCT product FROM config_param WHERE collection_group = ?",
                                (group,),
                            )
                        }  # fmt: skip
                    product = product_for_process(process, products[group])
                    timeouts[(group, process)] = (
                        _param_seconds(conn, group, product, rule["timeout_param"]) if product else None
                    )
                seconds = timeouts[(group, process)]
            if seconds is None:
                continue  # unresolved for this stream: the rule stays unavailable here
            if stream_id not in clocks:
                clocks[stream_id] = conn.execute(
                    "SELECT max(last_ts) FROM source WHERE stream_id = ?", (stream_id,)
                ).fetchone()[0]
            now = clocks[stream_id]
            if now is not None and now - ts_start >= seconds * 1000:
                found.append((ev_id, stream_id, ts_start, name or "", start_entry))
    if not found:
        return None, []
    alert = Alert(
        rule["name"], rule["kind"], rule["severity"], len(found),
        [f[4] for f in found if f[4] is not None][:SAMPLE_IDS],
        min(f[2] for f in found), max(f[2] for f in found), [f[0] for f in found][:MAX_EVENT_IDS],
    )  # fmt: skip
    return alert, [(f[1], f[2], f[3]) for f in found]


def evaluate_rule(
    conn: sqlite3.Connection,
    rule: Mapping[str, Any],
    lo: int,
    hi: int,
    seen: set[tuple[int, int, str]] | None = None,
) -> tuple[Alert | None, list[tuple[int, int, str]]]:
    """Evaluate one rule over the records ``lo < id <= hi``; returns the alert and the new ``seen`` keys."""
    if rule["kind"] == "match":
        return _eval_match(conn, rule, lo, hi), []
    if rule["kind"] == "threshold":
        return _eval_threshold(conn, rule, lo, hi), []
    return _eval_open(conn, rule, seen if seen is not None else set())


def max_entry_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT coalesce(max(id), 0) FROM entry").fetchone()[0]


class AlertState:
    """In-memory state of the alerts: cooldowns, alerted operations and the recent history, per case."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], float] = {}
        self._seen: dict[tuple[str, str], dict[tuple[int, int, str], None]] = {}
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._next_id = 0

    def history(self, case_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(case_id, ()))

    def clear_history(self, case_id: str) -> None:
        with self._lock:
            self._history.pop(case_id, None)

    def forget_case(self, case_id: str) -> None:
        with self._lock:
            self._history.pop(case_id, None)
            for table in (self._last, self._seen):
                for key in [k for k in table if k[0] == case_id]:
                    del table[key]

    def _cooling(self, case_id: str, rule: Mapping[str, Any]) -> bool:
        last = self._last.get((case_id, identity(rule)))
        return last is not None and self._clock() - last < rule.get("min_interval_s", 0)

    def process_cycle(
        self, conn: sqlite3.Connection, data_dir: str | Path, case_id: str, baseline: int
    ) -> list[dict[str, Any]]:
        """Evaluate the enabled rules over the records added after ``baseline``; returns the new alerts.

        A rule that fails (timeout, bad filter for this case) is skipped; the others still run.
        """
        rules = [r for r in load(data_dir).rules if r["enabled"]]
        if not rules:
            return []
        hi = max_entry_id(conn)
        if hi <= baseline:
            return []
        out: list[dict[str, Any]] = []
        for rule in rules:
            ident = (case_id, identity(rule))
            with self._lock:
                if self._cooling(case_id, rule):
                    continue
                seen = set(self._seen.get(ident, ()))
            try:
                alert, keys = evaluate_rule(conn, rule, baseline, hi, seen)
            except (query.QueryTimeout, FilterError, sqlite3.Error):
                continue
            if alert is None:
                continue
            body = alert.to_event()
            with self._lock:
                self._last[ident] = self._clock()
                if keys:
                    store = self._seen.setdefault(ident, {})
                    for key in keys:
                        store[key] = None
                    while len(store) > SEEN_LIMIT:
                        del store[next(iter(store))]
                self._next_id += 1
                body = {**body, "id": self._next_id, "case": case_id,
                        "at": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
                self._history.setdefault(case_id, deque(maxlen=HISTORY_LIMIT)).append(body)
            out.append(body)
        return out
