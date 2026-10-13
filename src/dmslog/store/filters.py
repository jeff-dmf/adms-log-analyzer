"""Filter tree (JSON) -> parameterized SQL over ``entry e`` (ARQUITETURA 10).

The viewer sends a filter as a JSON tree. :func:`compile_filter` validates it and
returns a SQL boolean expression over the alias ``e`` (table ``entry``) plus its
parameters. **Nothing that comes from the user is ever written into the SQL text**:
every value is bound as a ``?`` parameter, and the SQL fragments themselves are
constants of this module.

Grammar (each node is an object with exactly one key)::

    node      := {"and": [node, ...]} | {"or": [node, ...]} | {"not": node} | leaf
    leaf      := {"source": ids} | {"stream": ids} | {"family": strs} | {"sensitive": bool}
               | {"sev": ordered} | {"level": ordered} | {"ts": ordered}
               | {"tid": ids} | {"pid": ids} | {"event": ids}
               | {"tpl": tpls} | {"text": {"fts": str} | {"substr": str}}
               | {"key": {"type": str, "eq": str} | {"type": str, "in": [str, ...]} | {"type": str}}
               | {"event_status": strs} | {"flags": flag | [flag, ...]}
               | {"payload": {"role"|"roles"|"direction"|"peer"|"operation"|"fmt"|"msg_id"|"corr_id"
                              |"root": strs, "orphan": bool}}
               | {"collection": ids}        (collection GROUP ids: one ERS capture, folder or file; U21)
    ids       := int | [int, ...]
    strs      := str | [str, ...]
    tpls      := (str code | int id) | [...]
    ordered   := value | [value, ...] | {"eq"|"ne"|"lt"|"le"|"gt"|"ge": value, ...}   (ops are ANDed)
                 value = int, or a name for sev (critical..noise) and level (fatal..verbose)

``{"and": []}`` is true and ``{"or": []}`` is false; ``None`` or ``{}`` means "no filter".

Two-valued logic: every leaf evaluates to 0 or 1, never NULL (nullable columns are
guarded with ``IS NOT NULL``), so ``{"not": x}`` is always the exact complement of ``x``.

Full-text search (``text.fts``) accepts a deliberately restricted syntax, not the FTS5
query language: the text is split into terms at whitespace; ``"..."`` groups a phrase;
a trailing ``*`` on a bare term makes it a prefix (at least 2 letters or digits before
it); all terms must match (implicit AND). Every term is emitted as a quoted FTS5 string,
so ``OR``, ``NOT``, ``NEAR``, ``column:``, ``^``, ``+`` and parentheses are plain words.
Terms without any letter or digit are dropped (the tokenizer ignores them anyway).

``text.substr`` is a case-sensitive substring test on ``msg`` and the continuation
lines (``instr``); it scans rows, so the caller should pair it with a source or time
filter (ARQUITETURA 9.4) and run it under the query timeout (``query.py``).

Filters over tables that are still empty in M1 (template, event, payload, entry_key)
compile normally and simply match nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..parse.envelope import LEVELS

# Limits (validation happens before any SQL is built).
MAX_JSON_BYTES = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 200
MAX_LIST = 1000
MAX_STR = 1024
MAX_PARAMS = 4000
MAX_FTS_CHARS = 512
MAX_FTS_TERMS = 16
MIN_PREFIX_CHARS = 2
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

SEV_NAMES = {"critical": 1, "error": 2, "warning": 3, "info": 4, "noise": 5}
LEVEL_NAMES = {name.decode("ascii").lower(): value for name, value in LEVELS.items()}

# entry.flags bits (ARQUITETURA 5).
FLAG_BITS = {
    "partial": 1,
    "has_cont": 2,
    "ref_sensitive": 4,
    "startup": 8,
    "elevated_level": 16,
    "ts_out_of_order": 32,
    "has_payload": 64,
}

_COMPARE_OPS = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
_PAYLOAD_ALIASES = {"roles": "role"}
_PAYLOAD_COLUMNS = ("role", "direction", "peer", "operation", "fmt", "msg_id", "corr_id", "root")
_ID_COLUMNS = {"source": "source_id", "tid": "tid", "pid": "pid", "event": "event_id"}
_NULLABLE = {"e.ts", "e.tid", "e.pid", "e.level", "e.event_id"}
_ORDERED = {
    "sev": ("sev", SEV_NAMES, 1, 5),
    "level": ("level", LEVEL_NAMES, 1, 8),
    "ts": ("ts", None, INT64_MIN, INT64_MAX),
}

# English message templates for FilterError codes (to be moved to messages.py).
ERROR_MESSAGES = {
    "invalid_json": "filter is not valid JSON",
    "too_large": "filter is larger than {limit} bytes",
    "too_deep": "filter is nested deeper than {limit} levels",
    "too_many_nodes": "filter has more than {limit} nodes",
    "too_many_params": "filter has more than {limit} values",
    "bad_node": "a filter node must be an object with exactly one key",
    "unknown_operator": "unknown filter operator {name!r}",
    "bad_type": "expected {expected}",
    "bad_value": "invalid value: {detail}",
    "empty_list": "the list must not be empty",
    "list_too_long": "the list has more than {limit} items",
    "string_too_long": "the text is longer than {limit} characters",
    "fts_empty": "the search text has no searchable word",
    "fts_too_many_terms": "the search text has more than {limit} terms",
    "fts_prefix_too_short": "a prefix search needs at least {limit} letters or digits before '*'",
}


class QueryInputError(ValueError):
    """Invalid input from the client (filter, cursor, columns). Safe to report back.

    ``code`` is a stable identifier, ``path`` points at the offending node (JSON-path-like)
    and ``params`` fill the English message template.
    """

    messages: Mapping[str, str] = ERROR_MESSAGES

    def __init__(self, code: str, path: str = "$", **params: Any) -> None:
        self.code = code
        self.path = path
        self.params = params
        template = self.messages.get(code, code)
        try:
            text = template.format(**params)
        except (KeyError, IndexError, ValueError):
            text = template
        super().__init__(f"{path}: {text}")


class FilterError(QueryInputError):
    """The filter tree is invalid."""


@dataclass(frozen=True, slots=True)
class CompiledFilter:
    sql: str  # boolean SQL expression over alias "e"; constants and "?" only
    params: tuple[Any, ...]
    uses_fts: bool = False
    uses_substr: bool = False


TRUE = CompiledFilter("1", ())


def parse_filter(text: str | bytes | None) -> Any:
    """Decode a JSON filter received as text, with size and nesting limits."""
    if text is None:
        return None
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_JSON_BYTES:
        raise FilterError("too_large", limit=MAX_JSON_BYTES)
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise FilterError("invalid_json") from exc


def compile_bucket_filter(tree: Any) -> CompiledFilter | None:
    """Compile ``tree`` over ``bucket b`` for count estimates, or None when it cannot be.

    Only source/stream/family/sensitive/sev/tpl/ts and and/or/not are supported; ``ts``
    is rounded to whole minutes (superset) and is not allowed under ``not``. Invalid
    trees raise FilterError exactly like :func:`compile_filter`.
    """
    if tree is None or (isinstance(tree, dict) and not tree):
        return TRUE
    compile_filter(tree)  # full validation first: errors must not depend on the mode
    compiler = _Compiler(bucket=True)
    try:
        sql = compiler.node(tree, "$", 1)
    except _Incompatible:
        return None
    return CompiledFilter(sql, tuple(compiler.params))


def compile_filter(tree: Any) -> CompiledFilter:
    """Validate ``tree`` and compile it into a parameterized SQL expression."""
    if tree is None or (isinstance(tree, dict) and not tree):
        return TRUE
    compiler = _Compiler()
    sql = compiler.node(tree, "$", 1)
    if len(compiler.params) > MAX_PARAMS:
        raise FilterError("too_many_params", limit=MAX_PARAMS)
    return CompiledFilter(sql, tuple(compiler.params), compiler.uses_fts, compiler.uses_substr)


# --- validation helpers -----------------------------------------------------------


def _int(value: Any, path: str, lo: int = INT64_MIN, hi: int = INT64_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FilterError("bad_type", path, expected="an integer")
    if not lo <= value <= hi:
        raise FilterError("bad_value", path, detail=f"out of range {lo}..{hi}")
    return value


def _str(value: Any, path: str, limit: int = MAX_STR) -> str:
    if not isinstance(value, str):
        raise FilterError("bad_type", path, expected="a string")
    if len(value) > limit:
        raise FilterError("string_too_long", path, limit=limit)
    return value


def _as_list(value: Any, path: str) -> list[Any]:
    items = value if isinstance(value, list) else [value]
    if not items:
        raise FilterError("empty_list", path)
    if len(items) > MAX_LIST:
        raise FilterError("list_too_long", path, limit=MAX_LIST)
    return items


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def _guard(column: str, predicate: str) -> str:
    """Make a predicate on a nullable column two-valued (false instead of NULL)."""
    if column in _NULLABLE:
        return f"({column} IS NOT NULL AND {predicate})"
    return predicate


# --- FTS --------------------------------------------------------------------------


def fts_query(text: Any, path: str = "$") -> str:
    """Translate the restricted search syntax (module docstring) into a safe FTS5 query."""
    text = _str(text, path, MAX_FTS_CHARS).replace("\x00", " ")
    pieces: list[tuple[str, bool]] = []  # (text, is_prefix)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = text.find('"', i + 1)
            if end < 0:
                end = n
            pieces.append((text[i + 1 : end], False))
            i = end + 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        word = text[i:j]
        stripped = word.rstrip("*")
        pieces.append((stripped, len(stripped) < len(word)))
        i = j
    terms: list[str] = []
    for piece, prefix in pieces:
        alnum = sum(1 for ch in piece if ch.isalnum())
        if not alnum:
            continue
        if prefix and alnum < MIN_PREFIX_CHARS:
            raise FilterError("fts_prefix_too_short", path, limit=MIN_PREFIX_CHARS)
        terms.append('"' + piece.replace('"', '""') + '"' + ("*" if prefix else ""))
    if not terms:
        raise FilterError("fts_empty", path)
    if len(terms) > MAX_FTS_TERMS:
        raise FilterError("fts_too_many_terms", path, limit=MAX_FTS_TERMS)
    return " ".join(terms)


# --- compiler ---------------------------------------------------------------------


class _Incompatible(Exception):
    """The filter cannot be evaluated on the bucket table (bucket mode only)."""


# Bucket table (per-minute counts): minute = ts // BUCKET_MS (ARQUITETURA 5, 9.4).
BUCKET_MS = 60_000
_BUCKET_OPS = frozenset(
    {"and", "or", "not", "source", "stream", "family", "sensitive", "sev", "tpl", "ts", "collection"}
)
_BUCKET_COLUMNS = {"source_id": "b.source_id", "sev": "b.sev", "tpl_id": "b.tpl_id"}


class _Compiler:
    def __init__(self, *, bucket: bool = False) -> None:
        self.params: list[Any] = []
        self.nodes = 0
        self.uses_fts = False
        self.uses_substr = False
        self.bucket = bucket  # compile over "bucket b" instead of "entry e" (approximate on ts)
        self.negated = 0

    def col(self, name: str) -> str:
        if self.bucket:
            return _BUCKET_COLUMNS[name]
        return "e." + name

    def node(self, tree: Any, path: str, depth: int) -> str:
        if depth > MAX_DEPTH:
            raise FilterError("too_deep", path, limit=MAX_DEPTH)
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FilterError("too_many_nodes", path, limit=MAX_NODES)
        if not isinstance(tree, dict) or len(tree) != 1:
            raise FilterError("bad_node", path)
        ((op, arg),) = tree.items()
        sub = f"{path}.{op}" if isinstance(op, str) and op.isidentifier() else path
        if self.bucket and op not in _BUCKET_OPS:
            raise _Incompatible
        if op in ("and", "or"):
            if not isinstance(arg, list):
                raise FilterError("bad_type", sub, expected="a list of filter nodes")
            if len(arg) > MAX_NODES:
                raise FilterError("too_many_nodes", sub, limit=MAX_NODES)
            if not arg:
                return "1" if op == "and" else "0"
            parts = [self.node(child, f"{sub}[{k}]", depth + 1) for k, child in enumerate(arg)]
            return "(" + f" {op.upper()} ".join(parts) + ")"
        if op == "not":
            self.negated += 1
            inner = self.node(arg, sub, depth + 1)
            self.negated -= 1
            return f"(NOT {inner})"
        handler = _LEAVES.get(op) if isinstance(op, str) else None
        if handler is None:
            raise FilterError("unknown_operator", path, name=str(op)[:64])
        return handler(self, arg, sub)

    def bind(self, *values: Any) -> None:
        self.params.extend(values)

    # leaves -----------------------------------------------------------------------

    def ids(self, name: str, arg: Any, path: str) -> str:
        column = self.col(name)
        values = [_int(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return _guard(column, f"{column} IN ({_placeholders(len(values))})")

    def stream(self, arg: Any, path: str) -> str:
        values = [_int(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        column = self.col("source_id")
        return f"{column} IN (SELECT id FROM source WHERE stream_id IN ({_placeholders(len(values))}))"

    def collection(self, arg: Any, path: str) -> str:
        """Records of the given collection groups (``collection.ers_group``; ARQUITETURA 10)."""
        values = [_int(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return (
            f"{self.col('source_id')} IN (SELECT s.id FROM source s JOIN collection c"
            f" ON c.id = s.collection_id WHERE c.ers_group IN ({_placeholders(len(values))}))"
        )

    def family(self, arg: Any, path: str) -> str:
        values = [_str(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return (
            f"{self.col('source_id')} IN (SELECT s.id FROM source s JOIN stream st ON st.id = s.stream_id "
            f"WHERE st.family IN ({_placeholders(len(values))}))"
        )

    def sensitive(self, arg: Any, path: str) -> str:
        if not isinstance(arg, bool):
            raise FilterError("bad_type", path, expected="true or false")
        self.bind(1 if arg else 0)
        return (
            f"{self.col('source_id')} IN (SELECT s.id FROM source s JOIN stream st ON st.id = s.stream_id "
            "WHERE st.sensitive = ?)"
        )

    def tpl(self, arg: Any, path: str) -> str:
        codes: list[str] = []
        ids: list[int] = []
        for k, v in enumerate(_as_list(arg, path)):
            if isinstance(v, str):
                codes.append(_str(v, f"{path}[{k}]"))
            else:
                ids.append(_int(v, f"{path}[{k}]", 0))
        column = self.col("tpl_id")
        parts = []
        if ids:
            self.bind(*ids)
            parts.append(f"{column} IN ({_placeholders(len(ids))})")
        if codes:
            self.bind(*codes)
            parts.append(f"{column} IN (SELECT id FROM template WHERE code IN ({_placeholders(len(codes))}))")
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def text(self, arg: Any, path: str) -> str:
        if not isinstance(arg, dict) or len(arg) != 1:
            raise FilterError("bad_type", path, expected='{"fts": text} or {"substr": text}')
        ((kind, value),) = arg.items()
        if kind == "fts":
            self.uses_fts = True
            self.bind(fts_query(value, f"{path}.fts"))
            return "e.id IN (SELECT rowid FROM entry_fts WHERE entry_fts MATCH ?)"
        if kind == "substr":
            value = _str(value, f"{path}.substr")
            if not value:
                raise FilterError("bad_value", f"{path}.substr", detail="empty text")
            self.uses_substr = True
            self.bind(value, value)
            return (
                "(instr(e.msg, ?) > 0 OR EXISTS (SELECT 1 FROM entry_body b "
                "WHERE b.entry_id = e.id AND instr(b.cont, ?) > 0))"
            )
        raise FilterError("unknown_operator", path, name=str(kind)[:64])

    def key(self, arg: Any, path: str) -> str:
        if not isinstance(arg, dict) or "type" not in arg or not set(arg) <= {"type", "eq", "in"}:
            raise FilterError("bad_type", path, expected='{"type": ..., "eq" | "in": ...}')
        if "eq" in arg and "in" in arg:
            raise FilterError("bad_value", path, detail="use either 'eq' or 'in'")
        self.bind(_str(arg["type"], f"{path}.type"))
        if "eq" in arg:
            self.bind(_str(arg["eq"], f"{path}.eq"))
            return "e.id IN (SELECT entry_id FROM entry_key WHERE key_type = ? AND value = ?)"
        if "in" in arg:
            if not isinstance(arg["in"], list):
                raise FilterError("bad_type", f"{path}.in", expected="a list of strings")
            values = [_str(v, f"{path}.in[{k}]") for k, v in enumerate(_as_list(arg["in"], f"{path}.in"))]
            self.bind(*values)
            return (
                "e.id IN (SELECT entry_id FROM entry_key WHERE key_type = ? "
                f"AND value IN ({_placeholders(len(values))}))"
            )
        return "e.id IN (SELECT entry_id FROM entry_key WHERE key_type = ?)"

    def event_status(self, arg: Any, path: str) -> str:
        values = [_str(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return _guard(
            "e.event_id",
            f"e.event_id IN (SELECT id FROM event WHERE status IN ({_placeholders(len(values))}))",
        )

    def flags(self, arg: Any, path: str) -> str:
        mask = 0
        for k, v in enumerate(_as_list(arg, path)):
            name = _str(v, f"{path}[{k}]", 64)
            bit = FLAG_BITS.get(name)
            if bit is None:
                raise FilterError("bad_value", f"{path}[{k}]", detail=f"unknown flag {name!r}")
            mask |= bit
        self.bind(mask, mask)
        return "(e.flags & ?) = ?"

    def payload(self, arg: Any, path: str) -> str:
        if not isinstance(arg, dict):
            raise FilterError("bad_type", path, expected="an object of payload attributes")
        parts = []
        for name, value in arg.items():
            sub = f"{path}.{name}"
            if name == "orphan":
                if not isinstance(value, bool):
                    raise FilterError("bad_type", sub, expected="a boolean")
                parts.append("event_id IS NULL" if value else "event_id IS NOT NULL")
                continue
            column = _PAYLOAD_ALIASES.get(name, name)
            if column not in _PAYLOAD_COLUMNS:
                raise FilterError("unknown_operator", path, name=str(name)[:64])
            values = [_str(v, f"{sub}[{k}]") for k, v in enumerate(_as_list(value, sub))]
            self.bind(*values)
            # column is one of the constant column names above, never user text.
            parts.append(f"{column} IN ({_placeholders(len(values))})")
        where = (" WHERE " + " AND ".join(parts)) if parts else ""
        return f"e.id IN (SELECT entry_id FROM payload{where})"


def _ids_leaf(op: str):
    name = _ID_COLUMNS[op]
    return lambda compiler, arg, path: compiler.ids(name, arg, path)


def _bucket_ts(self: _Compiler, arg: Any, path: str) -> str:
    """ts on the bucket table: whole minutes, so the result is a superset (estimate only)."""
    if self.negated:
        raise _Incompatible
    if not isinstance(arg, dict):
        values = [_int(v, f"{path}[{k}]") // BUCKET_MS for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return f"b.minute IN ({_placeholders(len(values))})"
    parts = []
    for cmp, v in arg.items():
        t = _int(v, f"{path}.{cmp}")
        if cmp in ("ge", "gt"):
            parts.append("b.minute >= ?")
            self.bind(t // BUCKET_MS)
        elif cmp == "le":
            parts.append("b.minute <= ?")
            self.bind(t // BUCKET_MS)
        elif cmp == "lt":
            parts.append("b.minute <= ?")
            self.bind((t - 1) // BUCKET_MS)
        elif cmp == "eq":
            parts.append("b.minute = ?")
            self.bind(t // BUCKET_MS)
        else:
            raise _Incompatible
    if not parts:
        raise _Incompatible
    return "(" + " AND ".join(parts) + ")"


def _ordered_leaf(op: str):
    name, names, lo, hi = _ORDERED[op]

    def value(v: Any, path: str) -> int:
        if names is not None and isinstance(v, str):
            try:
                return names[v.lower()]
            except KeyError:
                raise FilterError("bad_value", path, detail=f"unknown name {v[:64]!r}") from None
        return _int(v, path, lo, hi)

    def compile_(self: _Compiler, arg: Any, path: str) -> str:
        if self.bucket and name == "ts":
            return _bucket_ts(self, arg, path)
        column = self.col(name)
        if isinstance(arg, dict):
            if not arg:
                raise FilterError("bad_value", path, detail="no comparison given")
            parts = []
            for cmp, v in arg.items():
                sql_op = _COMPARE_OPS.get(cmp)
                if sql_op is None:
                    raise FilterError("unknown_operator", path, name=str(cmp)[:64])
                self.bind(value(v, f"{path}.{cmp}"))
                parts.append(f"{column} {sql_op} ?")
            return _guard(column, "(" + " AND ".join(parts) + ")")
        values = [value(v, f"{path}[{k}]") for k, v in enumerate(_as_list(arg, path))]
        self.bind(*values)
        return _guard(column, f"{column} IN ({_placeholders(len(values))})")

    return compile_


_LEAVES = {
    "source": _ids_leaf("source"),
    "tid": _ids_leaf("tid"),
    "pid": _ids_leaf("pid"),
    "event": _ids_leaf("event"),
    "stream": _Compiler.stream,
    "family": _Compiler.family,
    "sensitive": _Compiler.sensitive,
    "sev": _ordered_leaf("sev"),
    "level": _ordered_leaf("level"),
    "ts": _ordered_leaf("ts"),
    "tpl": _Compiler.tpl,
    "text": _Compiler.text,
    "key": _Compiler.key,
    "event_status": _Compiler.event_status,
    "flags": _Compiler.flags,
    "payload": _Compiler.payload,
    "collection": _Compiler.collection,
}
LEAF_OPERATORS = frozenset(_LEAVES)
