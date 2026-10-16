"""Comparison of two views of a case by their sequence of message types (ARQUITETURA 10.9, ROADMAP 9.7).

Two **sides**, A and B, are two sets of records of the same case. Each side is the common ``filter`` (the
view's final filter, chained views already resolved by the client) ANDed with the side's own selector,
by ``mode``:

* ``filter``: ``{"filter": tree}``, two filters;
* ``window``: ``{"from": ms, "to": ms}``, two time windows ``[from, to)`` of the log clock (U6);
* ``operation``: ``{"event": id}``, the records of two events (two runs of an operation);
* ``process``: ``{"stream": id, "from"?: ms, "to"?: ms}``, two processes (stream = family + PID), with an
  optional window.

Each side becomes the sequence of the message types of its records in a **stable order** (``ts``, then
the record's locator: collection path, member, line), the grid's order except that records of different
files at the same millisecond are ordered by locator instead of by id. Unknown records (``tpl_id = 0``)
are typed by their message signature (``parse.signature``), so two different unknown lines do not match.
The order, the tokens and therefore the result never depend on ids: a complete, incremental or partial
indexing of the same files gives the same comparison (only the ``id`` of the rows differs).

At most ``max_side`` records per side are compared (the first ones in that order; ``truncated``):
truncation keeps the sequence meaningful, a sample would not. The sequences are aligned by
:func:`store.align.align` (exact LCS: Myers, then bit-parallel; anchored and ``optimal: false`` only
beyond the budgets, which the request limits never reach).

The result is a list of **runs** (``same``, ``only_a``, ``only_b``, ``changed`` = a substitution block:
records of A replaced by records of B between two matches) and a page of **rows** (``offset``/``limit``
over the runs expanded to rows; ``only_diff`` hides the ``same`` rows), plus per message type counts and a
``digest`` of the compared input (equal digests = same comparison). The server keeps no state: each page
recomputes the (deterministic) alignment.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .. import messages
from ..parse.signature import MAX_INPUT_CHARS, signature
from . import align as AL
from . import query as Q
from .filters import INT64_MAX, INT64_MIN, CompiledFilter, FilterError, QueryInputError, compile_filter

MODES = ("filter", "window", "operation", "process")
_SIDE_KEYS = {
    "filter": ({"filter"}, set()),
    "window": ({"from", "to"}, set()),
    "operation": ({"event"}, set()),
    "process": ({"stream"}, {"from", "to"}),
}
DEFAULT_MAX_SIDE = 5_000
MAX_SIDE = 20_000
DEFAULT_LIMIT = 200
MAX_LIMIT = 1_000
MAX_OFFSET = 10 * MAX_SIDE  # rows never exceed 2 * MAX_SIDE
MAX_RUNS = 2_000
MAX_TEMPLATES = 200
COMPARE_TIMEOUT_S = Q.PAGE_TIMEOUT_S  # reads of both sides together; the page rows get their own
UNKNOWN_PREFIX = "?"  # token of an unknown record: "?" + signature (template codes never start with it)
_PARTIAL_PREFIX = "partial_scope."
CACHE_SIZE = 4  # alignments kept in memory, so that paging does not align again


class _AlignCache:
    """Small LRU of alignments by the hash of the two token sequences (the alignment is a pure function
    of them, so a hit returns exactly what a new computation would)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.items: OrderedDict[str, AL.Alignment] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, a: list[str], b: list[str]) -> AL.Alignment:
        h = hashlib.sha256(
            f"{AL.ALIGN_WORK}:{AL.BIT_WORK}:{AL.FALLBACK_WORK}:{AL.SMALL}".encode()
        )  # the budgets shape the result
        for seq in (a, b):
            h.update(len(seq).to_bytes(8, "little"))
            for token in seq:
                h.update(token.encode("utf-8", "surrogatepass") + b"\x00")
        key = h.hexdigest()
        with self.lock:
            hit = self.items.get(key)
            if hit is not None:
                self.items.move_to_end(key)
                return hit
        result = AL.align(a, b)
        with self.lock:
            self.items[key] = result
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return result


_CACHE = _AlignCache(CACHE_SIZE)


class CompareError(QueryInputError):
    messages = messages.COMPARE_ERRORS


@dataclass(frozen=True, slots=True)
class SideSpec:
    tree: Any  # the side's own selector as a filter tree (ANDed with the common filter)
    event: int | None = None
    stream: int | None = None
    window: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class CompareRequest:
    mode: str
    filter: Any
    a: SideSpec
    b: SideSpec
    max_side: int = DEFAULT_MAX_SIDE
    offset: int = 0
    limit: int = DEFAULT_LIMIT
    only_diff: bool = False

    def side_tree(self, side: SideSpec) -> Any:
        """Final filter tree of one side (common filter AND the side's selector)."""
        if not self.filter:
            return side.tree
        return {"and": [self.filter, side.tree]}


# --------------------------------------------------------------------------- request


def _int(value: Any, path: str, code: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise CompareError(code, path, lo=lo, hi=hi)
    return value


def _check_tree(tree: Any, path: str) -> None:
    """Validate a filter tree, reporting errors under ``path`` instead of ``$``."""
    if tree is not None and not isinstance(tree, dict):
        raise CompareError("bad_type", path, expected="a filter object")
    try:
        compile_filter(tree)
    except FilterError as exc:
        raise FilterError(exc.code, path + exc.path[1:], **exc.params) from None


def _window(side: Mapping[str, Any], path: str, required: bool) -> tuple[int, int] | None:
    if not required and "from" not in side and "to" not in side:
        return None
    lo = _int(side.get("from"), f"{path}.from", "bad_window", INT64_MIN, INT64_MAX)
    hi = _int(side.get("to"), f"{path}.to", "bad_window", INT64_MIN, INT64_MAX)
    if lo >= hi:
        raise CompareError("bad_window", f"{path}.to", lo=INT64_MIN, hi=INT64_MAX)
    return lo, hi


def _side(mode: str, side: Any, path: str) -> SideSpec:
    if not isinstance(side, dict):
        raise CompareError("bad_side", path, mode=mode)
    required, optional = _SIDE_KEYS[mode]
    for key in side:
        if key not in required and key not in optional:
            raise CompareError(
                "bad_side_key", f"{path}.{str(key)[:64]}", mode=mode, keys=sorted(required | optional)
            )
    for key in sorted(required):
        if key not in side:
            raise CompareError("missing_side_key", f"{path}.{key}", mode=mode, key=key)
    if mode == "filter":
        tree = side["filter"]
        _check_tree(tree, f"{path}.filter")
        return SideSpec(tree if tree else {"and": []})
    if mode == "window":
        window = _window(side, path, required=True)
        assert window is not None
        return SideSpec({"ts": {"ge": window[0], "lt": window[1]}}, window=window)
    if mode == "operation":
        event = _int(side["event"], f"{path}.event", "bad_id", 1, INT64_MAX)
        return SideSpec({"event": [event]}, event=event)
    stream = _int(side["stream"], f"{path}.stream", "bad_id", 1, INT64_MAX)
    window = _window(side, path, required=False)
    tree: Any = {"stream": [stream]}
    if window is not None:
        tree = {"and": [tree, {"ts": {"ge": window[0], "lt": window[1]}}]}
    return SideSpec(tree, stream=stream, window=window)


def parse_request(body: Mapping[str, Any]) -> CompareRequest:
    """Validate the JSON request (``case`` is the caller's business)."""
    mode = body.get("mode")
    if mode not in MODES:
        raise CompareError("bad_mode", "$.mode", modes=", ".join(MODES))
    flt = body.get("filter")
    _check_tree(flt, "$.filter")
    a = _side(mode, body.get("a"), "$.a")
    b = _side(mode, body.get("b"), "$.b")
    max_side = _int(body.get("max_side", DEFAULT_MAX_SIDE), "$.max_side", "bad_max_side", 1, MAX_SIDE)
    offset = _int(body.get("offset", 0), "$.offset", "bad_offset", 0, MAX_OFFSET)
    limit = _int(body.get("limit", DEFAULT_LIMIT), "$.limit", "bad_limit", 1, MAX_LIMIT)
    only_diff = body.get("only_diff", False)
    if not isinstance(only_diff, bool):
        raise CompareError("bad_only_diff", "$.only_diff")
    req = CompareRequest(mode, flt or None, a, b, max_side, offset, limit, only_diff)
    for name, side in (("a", a), ("b", b)):
        try:
            compile_filter(req.side_tree(side))  # the composition itself must fit the limits
        except FilterError as exc:
            raise FilterError(exc.code, f"$.{name}", **exc.params) from None
    return req


# --------------------------------------------------------------------------- sequences


@dataclass(slots=True)
class _Seq:
    ids: list[int]
    tokens: list[str]
    keys: list[tuple[Any, ...]]  # stable order key: (has ts, ts, path, member, line, id)
    sources: list[int]
    truncated: bool


def _locators(conn) -> dict[int, tuple[str, str, int | None]]:
    """source id -> (collection path, member, collection group)."""
    rows = conn.execute(
        "SELECT s.id, c.path, s.member, coalesce(c.ers_group, c.id) FROM source s"
        " JOIN collection c ON c.id = s.collection_id"
    )
    return {sid: (path, member, group) for sid, path, member, group in rows}


def _templates(conn) -> dict[int, tuple[str, str | None]]:
    return {tid: (code, title) for tid, code, title in conn.execute("SELECT id, code, title FROM template")}


_SEQ_SELECT = (
    "SELECT e.id, e.ts, e.source_id, e.line_no, e.tpl_id,"
    f" CASE WHEN e.tpl_id = 0 THEN substr(e.msg, 1, {MAX_INPUT_CHARS}) END FROM entry e"
)


def _sequence(
    conn,
    compiled: CompiledFilter,
    max_side: int,
    locs: Mapping[int, tuple[str, str, int | None]],
    templates: Mapping[int, tuple[str, str | None]],
    unknown: dict[str, str],
) -> _Seq:
    """The first ``max_side`` records of the side in the stable order, as tokens."""
    rows = conn.execute(
        f"{_SEQ_SELECT} WHERE {compiled.sql} ORDER BY e.ts, e.id LIMIT ?", (*compiled.params, max_side + 1)
    ).fetchall()
    truncated = len(rows) > max_side
    if truncated:
        # Ties of the boundary timestamp are ordered by id in that statement, which depends on the
        # indexing; take them again in locator order.
        boundary = rows[max_side][1]
        if boundary is None:
            rows = []
        else:
            rows = [r for r in rows if r[1] is not None and r[1] < boundary] + [
                r for r in rows if r[1] is None
            ]
        rows += conn.execute(
            f"{_SEQ_SELECT} JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id"
            f" WHERE ({compiled.sql}) AND e.ts IS ? ORDER BY c.path, s.member, e.line_no, e.id LIMIT ?",
            (*compiled.params, boundary, max_side - len(rows)),
        ).fetchall()
    seq = _Seq([], [], [], [], truncated)
    items = []
    for rid, ts, sid, line, tpl, msg in rows:
        path, member, _ = locs.get(sid, ("", "", None))
        key = (ts is not None, ts if ts is not None else 0, path, member, line, rid)
        if tpl == 0:
            text = msg or ""
            token = unknown.get(text)
            if token is None:
                token = unknown[text] = UNKNOWN_PREFIX + signature(text)
        else:
            known = templates.get(tpl)
            token = known[0] if known else f"#{tpl}"
        items.append((key, rid, token, sid))
    items.sort()
    for key, rid, token, sid in items:
        seq.keys.append(key)
        seq.ids.append(rid)
        seq.tokens.append(token)
        seq.sources.append(sid)
    return seq


# --------------------------------------------------------------------------- result


def _runs(pairs: tuple[tuple[int, int], ...], n: int, m: int) -> list[tuple[str, int, int, int, int]]:
    """(op, a_start, a_len, b_start, b_len) covering both sequences in order."""
    runs: list[tuple[str, int, int, int, int]] = []
    i = j = 0
    for pi, pj in (*pairs, (n, m)):
        da, db = pi - i, pj - j
        if da and db:
            runs.append(("changed", i, da, j, db))
        elif da:
            runs.append(("only_a", i, da, j, 0))
        elif db:
            runs.append(("only_b", i, 0, j, db))
        if pi < n:
            last = runs[-1] if runs else None
            if last and last[0] == "same" and last[1] + last[2] == pi:
                runs[-1] = ("same", last[1], last[2] + 1, last[3], last[4] + 1)
            else:
                runs.append(("same", pi, 1, pj, 1))
        i, j = pi + 1, pj + 1
    return runs


def _run_rows(run: tuple[str, int, int, int, int]) -> int:
    op, _, la, _, lb = run
    return la if op == "same" else max(la, lb)


def _page(
    runs: list[tuple[str, int, int, int, int]], offset: int, limit: int, only_diff: bool
) -> tuple[list[tuple[str, int | None, int | None]], int]:
    """Rows ``(op, a_pos, b_pos)`` of the page and the total number of rows."""
    out: list[tuple[str, int | None, int | None]] = []
    total = 0
    for run in runs:
        op, a0, la, b0, lb = run
        if only_diff and op == "same":
            continue
        size = _run_rows(run)
        lo, hi = max(offset - total, 0), min(offset + limit - total, size)
        for k in range(lo, hi):
            out.append((op, a0 + k if k < la else None, b0 + k if k < lb else None))
        total += size
    return out, total


def _row_details(conn, ids: list[int], templates: Mapping[int, tuple[str, str | None]]) -> dict[int, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    cur = conn.execute(f"SELECT {Q._BASE_SELECT} FROM entry e WHERE e.id IN ({marks})", ids)
    names = [d[0] for d in cur.description]
    out = {}
    for row in cur.fetchall():
        item = dict(zip(names, row, strict=True))
        code, title = templates.get(item["tpl_id"], (None, None))
        item["tpl"] = code
        item["label"] = title or code
        out[item["id"]] = item
    return out


def _selector(conn, req: CompareRequest, side: SideSpec, path: str) -> dict[str, Any]:
    if side.event is not None:
        row = conn.execute(
            "SELECT id, stream_id, kind, name, key, status, ts_start, ts_end, duration_ms FROM event"
            " WHERE id = ?",
            (side.event,),
        ).fetchone()
        if row is None:
            raise CompareError("event_not_found", f"{path}.event", id=side.event)
        keys = ("id", "stream_id", "kind", "name", "key", "status", "ts_start", "ts_end", "duration_ms")
        return {"event": dict(zip(keys, row, strict=True))}
    if side.stream is not None:
        row = conn.execute(
            "SELECT id, collection_group, family, process, pid, sensitive FROM stream WHERE id = ?",
            (side.stream,),
        ).fetchone()
        if row is None:
            raise CompareError("stream_not_found", f"{path}.stream", id=side.stream)
        keys = ("id", "collection", "family", "process", "pid", "sensitive")
        out: dict[str, Any] = {"stream": dict(zip(keys, row, strict=True))}
        if side.window:
            out["from"], out["to"] = side.window
        return out
    if side.window:
        return {"from": side.window[0], "to": side.window[1]}
    return {}


def _side_json(seq: _Seq, selector: dict[str, Any]) -> dict[str, Any]:
    stamps = [k[1] for k in seq.keys if k[0]]
    return {
        "count": len(seq.ids),
        "truncated": seq.truncated,
        "first_ts": min(stamps) if stamps else None,
        "last_ts": max(stamps) if stamps else None,
        **selector,
    }


def _digest(mode: str, a: _Seq, b: _Seq) -> str:
    """Hash of the compared input without ids: equal for any indexing of the same files."""
    h = hashlib.sha256(mode.encode())
    for seq in (a, b):
        h.update(b"\x00side")
        for key, token in zip(seq.keys, seq.tokens, strict=True):
            h.update(json.dumps([*key[:5], token], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:32]


def _token_label(token: str, templates_by_code: Mapping[str, str | None]) -> tuple[str | None, str]:
    if token.startswith(UNKNOWN_PREFIX):
        return None, token[len(UNKNOWN_PREFIX) :]
    return token, templates_by_code.get(token) or token


@contextmanager
def _snapshot(conn) -> Iterator[None]:
    """One read transaction around all the reads, so that the sequences and the rows of the page come from
    the same state of the case even while the indexer writes (WAL). A transaction the caller already
    opened is left alone."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def compare(conn, req: CompareRequest) -> dict[str, Any]:
    """Run the comparison: reads in one snapshot under ``read_guard``; the alignment is bounded by its
    work budgets."""
    with _snapshot(conn):
        with Q.read_guard(conn, COMPARE_TIMEOUT_S):
            sel_a = _selector(conn, req, req.a, "$.a")
            sel_b = _selector(conn, req, req.b, "$.b")
            locs = _locators(conn)
            templates = _templates(conn)
            unknown: dict[str, str] = {}
            seq_a = _sequence(
                conn, compile_filter(req.side_tree(req.a)), req.max_side, locs, templates, unknown
            )
            seq_b = _sequence(
                conn, compile_filter(req.side_tree(req.b)), req.max_side, locs, templates, unknown
            )
            incomplete = (
                conn.execute(
                    "SELECT 1 FROM meta WHERE key >= ? AND key < ? LIMIT 1",
                    (_PARTIAL_PREFIX, _PARTIAL_PREFIX[:-1] + "/"),
                ).fetchone()
                is not None
            )
        result = _CACHE.get(seq_a.tokens, seq_b.tokens)
        n, m = len(seq_a.tokens), len(seq_b.tokens)
        runs = _runs(result.pairs, n, m)
        page, total_rows = _page(runs, req.offset, req.limit, req.only_diff)
        ids = sorted(
            {seq_a.ids[p] for _, p, _ in page if p is not None}
            | {seq_b.ids[q] for _, _, q in page if q is not None}
        )
        with Q.read_guard(conn, COMPARE_TIMEOUT_S):
            details = _row_details(conn, ids, templates)

    stats = {"same": len(result.pairs), "only_a": 0, "only_b": 0, "changed_a": 0, "changed_b": 0}
    for op, _, la, _, lb in runs:
        if op == "only_a":
            stats["only_a"] += la
        elif op == "only_b":
            stats["only_b"] += lb
        elif op == "changed":
            stats["changed_a"] += la
            stats["changed_b"] += lb
    stats["similarity"] = round(2 * len(result.pairs) / (n + m), 4) if n + m else 1.0

    by_code = {code: title for code, title in templates.values()}
    count_a, count_b = Counter(seq_a.tokens), Counter(seq_b.tokens)
    tokens = sorted(
        set(count_a) | set(count_b),
        key=lambda t: (-abs(count_b[t] - count_a[t]), -(count_a[t] + count_b[t]), t),
    )
    tpl_rows = []
    for token in tokens[:MAX_TEMPLATES]:
        code, label = _token_label(token, by_code)
        tpl_rows.append(
            {"code": code, "label": label, "unknown": code is None, "a": count_a[token], "b": count_b[token]}
        )

    notes = []
    if seq_a.truncated:
        notes.append("truncated_a")
    if seq_b.truncated:
        notes.append("truncated_b")
    if not result.optimal:
        notes.append("approximate")
    if incomplete:
        notes.append("incomplete_index")
    groups = {locs.get(s, ("", "", None))[2] for seq in (seq_a, seq_b) for s in seq.sources}
    if len(groups) > 1:
        notes.append("mixed_clocks")

    def entry(seq: _Seq, pos: int | None) -> dict | None:
        return None if pos is None else details.get(seq.ids[pos])

    next_offset = req.offset + len(page)
    return {
        "mode": req.mode,
        "a": _side_json(seq_a, sel_a),
        "b": _side_json(seq_b, sel_b),
        "method": result.method,
        "optimal": result.optimal,
        "work": result.work,
        "stats": stats,
        "runs": [{"op": op, "a": [a0, la], "b": [b0, lb]} for op, a0, la, b0, lb in runs[:MAX_RUNS]],
        "runs_total": len(runs),
        "templates": tpl_rows,
        "templates_total": len(tokens),
        "rows": [
            {"op": op, "a_pos": p, "b_pos": q, "a": entry(seq_a, p), "b": entry(seq_b, q)}
            for op, p, q in page
        ],
        "offset": req.offset,
        "limit": req.limit,
        "total_rows": total_rows,
        "next_offset": next_offset if next_offset < total_rows else None,
        "digest": _digest(req.mode, seq_a, seq_b),
        "notes": notes,
    }
