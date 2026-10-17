"""Running panels and refreshing them as the follow mode brings records (ROADMAP 9.11, ARQUITETURA 10.14).

:func:`run_panel` runs the widgets of one panel (``store/panels.py``) with :func:`aggregate.aggregate`
under one total time budget. A widget that fails or times out is reported on its own (static code and
text, never the query or any log text) and never stops the others; widgets the budget cannot reach are
``skipped``. Results come in panel order whatever order the caller asked for, and depend only on the
case's data, so they do not depend on the order in which it was indexed.

:func:`refresh_plan` answers "which widgets must be run again because records ``since_id < id <=
until_id`` arrived": a widget needs a new run only when one of the new records matches its filter (a
probe with ``LIMIT 1`` on the primary key range, the same keyset the alerts use), or, for widgets over
events, when the cycle derived events. Anything that cannot be decided in time (probe timeout, error,
probe budget spent) is reported as needing a refresh: the plan may name too many widgets, never too few.

:class:`PanelWatch` is the follow-mode hook: after a cycle it builds the body of the ``panels`` SSE
event. It limits the frequency per case: inside ``min_interval_s`` of the last evaluation it only
remembers the earliest unreported id and answers ``throttled`` with ``retry_after_ms``; the client then
asks ``POST /api/panels/changes`` with ``since_id = from_id`` (or just waits for the next event, whose
``from_id`` covers it).
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import messages
from . import aggregate
from . import query as Q
from .filters import INT64_MAX, QueryInputError, compile_filter
from .panels import PanelError, _json_text, load
from .presets import _walk_leaves

PANEL_BUDGET_S = 15.0
MIN_WIDGET_S = 0.05  # below this a widget is not started
MIN_REFRESH_INTERVAL_S = 5.0
PROBE_BUDGET_S = 3.0
PROBE_TIMEOUT_S = 0.5
MAX_THROTTLE_CASES = 1000


# --------------------------------------------------------------------------- running


def _error(code: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": messages.PANEL_WIDGET_ERRORS[code], **extra}


def _widget_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, aggregate.AggregateTimeout):
        return _error("timeout", estimated_rows=exc.estimated_rows)
    if isinstance(exc, Q.QueryTimeout):
        return _error("timeout")
    if isinstance(exc, aggregate.AggregateError) and exc.code == "bad_dimension":
        return _error("unknown_field")  # a field/key of the panel that this case does not have
    if isinstance(exc, QueryInputError):
        return _error("invalid_query")
    return _error("failed")


def run_panel(
    conn: sqlite3.Connection,
    panel: dict[str, Any],
    *,
    widget_ids: Iterable[str] | None = None,
    knowledge: Callable[[], Any] | None = None,
    budget_s: float = PANEL_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
    run: Callable[..., aggregate.Aggregate] = aggregate.aggregate,
) -> dict[str, Any]:
    """Run ``panel``'s widgets (all, or those in ``widget_ids``) on a read-only ``conn``.

    ``knowledge`` is called at most once, and only if a widget has ``lookups``. The budget covers every
    phase of a widget: its share of the remaining time is split among the bucket, exact and estimate
    phases of :func:`aggregate.aggregate`.
    """
    wanted = None if widget_ids is None else set(widget_ids)
    known = {w["id"] for w in panel["widgets"]}
    if wanted is not None and not wanted <= known:
        raise PanelError("unknown_widget")
    todo = [w for w in panel["widgets"] if wanted is None or w["id"] in wanted]
    start = clock()
    deadline = start + budget_s
    know: list[Any] = []
    results: list[dict[str, Any]] = []
    for k, widget in enumerate(todo):
        now = clock()
        remaining = deadline - now
        entry: dict[str, Any] = {"id": widget["id"], "status": "ok", "result": None, "error": None}
        if remaining < MIN_WIDGET_S:
            entry.update(status="skipped", error=_error("budget"))
            results.append(entry)
            continue
        # one ABSOLUTE deadline for every phase (knowledge, field checks, exact/estimate, labels, lookups)
        ceiling = time.monotonic() + remaining
        with Q.deadline_ceiling(ceiling):
            try:
                if knowledge is not None and widget["query"].get("lookups") and not know:
                    know.append(knowledge())
                remaining = deadline - clock()  # loading the knowledge may have used the time
                if remaining < MIN_WIDGET_S:
                    entry.update(status="skipped", error=_error("budget"))
                else:
                    share = min(remaining, 2 * remaining / (len(todo) - k))  # unused time flows on
                    kwargs: dict[str, Any] = {
                        "timeout_s": min(aggregate.AGG_TIMEOUT_S, share / 2),
                        "estimate_timeout_s": min(aggregate.ESTIMATE_TIMEOUT_S, share / 12),
                        "bucket_timeout_s": min(aggregate.BUCKET_TIMEOUT_S, share / 12),
                        "knowledge": know[0] if know else None,
                    }
                    entry["result"] = aggregate.as_json(run(conn, widget["query"], **kwargs))
            except Exception as exc:  # noqa: BLE001 - one widget must not take the panel down
                entry.update(status="error", error=_widget_error(exc))
        results.append(entry)
    end = clock()
    return {
        "panel": panel["id"],
        "rev": panel["rev"],
        "results": results,
        "complete": all(r["status"] != "skipped" for r in results),
        "budget_ms": round(budget_s * 1000),
        "elapsed_ms": max(0, round((end - start) * 1000)),
    }


# --------------------------------------------------------------------------- refresh plan


# Filter leaves whose truth for an EXISTING record is derived later (an operation ends, a correlation links
# the record): the records of old cycles can start or stop matching without any new record matching.
DERIVED_LEAVES = frozenset({"event", "event_status", "event_name", "event_kind", "key"})


def depends_on_derived(query: dict[str, Any]) -> bool:
    """True when the widget's result can change although no new record matches its filter: it works on
    events, filters by derived leaves (event_status, event_name, event_kind, event, key) or groups/counts
    by correlation keys or event columns."""
    if query.get("rows", "entries") == "events":
        return True
    tree = query.get("filter")
    if tree is not None and any(leaf in DERIVED_LEAVES for leaf in _walk_leaves(tree)):
        return True
    names = [*query.get("group_by", ()), *query.get("measures", ())]
    return any(isinstance(n, str) and "key." in n for n in names)


def _widget_key(widget: dict[str, Any]) -> tuple[str, str]:
    return widget["query"].get("rows", "entries"), _json_text(widget["query"].get("filter"))


def refresh_plan(
    conn: sqlite3.Connection | None,
    panels: Sequence[dict[str, Any]],
    *,
    since_id: int,
    until_id: int,
    events_changed: bool | None,
    clock: Callable[[], float] = time.monotonic,
    budget_s: float = PROBE_BUDGET_S,
    probe_timeout_s: float = PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    """Widgets that must run again after records ``since_id < id <= until_id`` arrived.

    ``events_changed``: the cycle derived events (True), did not (False) or it is unknown (None; handled
    as True). ``conn`` None means the data cannot be probed: every widget is named. Returns
    ``{"panels": [{"panel", "widgets": [ids]}], "probed", "conservative"}`` in panel and widget order;
    panels with nothing to refresh are left out.
    """
    deadline = clock() + budget_s
    memo: dict[tuple[str, str], bool] = {}
    probed = conservative = 0
    out: list[dict[str, Any]] = []
    has_new = until_id > since_id
    for panel in panels:
        ids: list[str] = []
        for widget in panel["widgets"]:
            key = _widget_key(widget)
            if events_changed is not False and depends_on_derived(widget["query"]):
                need = True
            elif not has_new:
                need = False
            elif key in memo:
                need = memo[key]
            else:
                need = _probe(
                    conn,
                    widget["query"].get("filter"),
                    since_id,
                    until_id,
                    deadline - clock(),
                    probe_timeout_s,
                )
                if need is None:
                    conservative += 1
                    need = True
                else:
                    probed += 1
                memo[key] = need
            if need:
                ids.append(widget["id"])
        if ids:
            out.append({"panel": panel["id"], "widgets": ids})
    return {"panels": out, "probed": probed, "conservative": conservative}


def _probe(
    conn: sqlite3.Connection | None, tree: Any, since_id: int, until_id: int, left_s: float, timeout_s: float
) -> bool | None:
    """True/False: some new record matches the filter; None: could not be decided."""
    if conn is None or left_s < MIN_WIDGET_S:
        return None
    if tree is None:
        return True  # no filter: any new record counts
    try:
        cf = compile_filter(tree, rowwise=True)
        with Q.read_guard(conn, min(timeout_s, left_s)):
            row = conn.execute(
                f"SELECT 1 FROM entry e WHERE e.id > ? AND e.id <= ? AND ({cf.sql}) LIMIT 1",
                (since_id, until_id, *cf.params),
            ).fetchone()
        return row is not None
    except (Q.QueryTimeout, QueryInputError, sqlite3.Error, ValueError, RecursionError):
        return None


# --------------------------------------------------------------------------- follow-mode hook


@dataclass
class _CaseState:
    last: float | None = None  # clock of the last evaluation that was not throttled
    pending_from: int | None = None  # earliest id not yet reported (None: nothing pending)
    pending_events: bool | None = False  # events may have changed in the pending stretch (None: unknown)
    pending_full: bool = False  # the pending stretch could not be analysed: refresh everything
    prev_to: int | None = None  # ``to_id`` of the previous event published for the case


class PanelWatch:
    """Per-case frequency limit, pending ranges and event body of the ``panels`` SSE event.

    One table holds everything (bounded to ``MAX_THROTTLE_CASES`` cases, least recently used first out). A
    case without state (first cycle of the process, or evicted) is answered with a FULL refresh, so
    forgetting a case can only cost one extra refresh, never a lost change.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_REFRESH_INTERVAL_S,
    ) -> None:
        self._clock = clock
        self._interval = min_interval_s
        self._lock = threading.Lock()
        self._cases: OrderedDict[str, _CaseState] = OrderedDict()

    def forget_case(self, case_id: str) -> None:
        with self._lock:
            self._cases.pop(case_id, None)

    def _state(self, case_id: str) -> tuple[_CaseState, bool]:
        """(state, existed); marks the case most recently used and evicts beyond the bound."""
        state = self._cases.get(case_id)
        existed = state is not None
        if state is None:
            state = self._cases[case_id] = _CaseState()
        self._cases.move_to_end(case_id)
        while len(self._cases) > MAX_THROTTLE_CASES:
            self._cases.popitem(last=False)
        return state, existed

    def note_failure(self, case_id: str, baseline: int | None) -> dict[str, Any]:
        """A cycle could not be analysed: keep its range pending as a full refresh and return a ``failed``
        event body (the client refreshes everything it shows)."""
        with self._lock:
            state, _ = self._state(case_id)
            lo = 0 if baseline is None else baseline
            state.pending_from = lo if state.pending_from is None else min(state.pending_from, lo)
            state.pending_full = True
            body = self._body(state, None, None, throttled=False, full=True, refresh=[])
            body["failed"] = True
            state.prev_to = None
            return body

    def _body(
        self, state: _CaseState, lo: int | None, until: int | None, *, throttled: bool, full: bool,
        refresh: list[dict[str, Any]], wait_ms: int = 0,
    ) -> dict[str, Any]:  # fmt: skip
        return {
            "from_id": lo, "to_id": until, "prev_to_id": state.prev_to, "throttled": throttled,
            "retry_after_ms": wait_ms, "min_interval_ms": round(self._interval * 1000), "full": full,
            "refresh": refresh,
        }  # fmt: skip

    def process_cycle(
        self,
        conn: sqlite3.Connection | None,
        data_dir: str | Path,
        case_id: str,
        baseline: int | None,
        until_id: int,
        *,
        events_changed: bool | None,
    ) -> dict[str, Any] | None:
        """Body of the ``panels`` event for one follow cycle, or None when the user has no panels.

        ``baseline`` is the highest record id before the cycle (None: unknown, so everything refreshes).
        """
        doc = load(data_dir)
        if doc.invalid or not doc.panels:
            self.forget_case(case_id)
            return None
        now = self._clock()
        with self._lock:
            state, existed = self._state(case_id)
            lo = baseline
            if state.pending_from is not None:
                lo = state.pending_from if lo is None else min(lo, state.pending_from)
            full = baseline is None or state.pending_full or not existed
            if state.pending_events is None or events_changed is None:
                events_changed = None
            else:
                events_changed = events_changed or state.pending_events
            if state.last is not None and now - state.last < self._interval:
                # an unknown stretch is "everything since 0"
                state.pending_from = 0 if baseline is None or lo is None else lo
                state.pending_events = events_changed
                state.pending_full = full
                wait_ms = max(1, math.ceil((self._interval - (now - state.last)) * 1000))
                body = self._body(
                    state,
                    state.pending_from,
                    until_id,
                    throttled=True,
                    full=full,
                    refresh=[],
                    wait_ms=wait_ms,
                )
                state.prev_to = until_id
                return body
            state.last = now
        everything = [{"panel": p["id"], "widgets": [w["id"] for w in p["widgets"]]} for p in doc.panels]
        everything = [r for r in everything if r["widgets"]]
        probed = conservative = 0
        if full:
            refresh = everything
        else:
            try:
                plan = refresh_plan(
                    conn, doc.panels, since_id=lo or 0, until_id=until_id, events_changed=events_changed
                )
                refresh, probed, conservative = plan["panels"], plan["probed"], plan["conservative"]
            except Exception:  # noqa: BLE001 - never lose the range: refresh everything instead
                refresh, full = everything, True
        with self._lock:
            state, _ = self._state(case_id)
            state.pending_from, state.pending_events, state.pending_full = None, False, False
            body = self._body(state, None if baseline is None and lo is None else lo, until_id,
                              throttled=False, full=full, refresh=refresh)  # fmt: skip
            body.update(probed=probed, conservative=conservative)
            state.prev_to = until_id
        return body


def changes_since(conn: sqlite3.Connection, data_dir: str | Path, since_id: Any) -> dict[str, Any]:
    """The pull form of the event (``POST /api/panels/changes``): the plan for ids above ``since_id``."""
    if isinstance(since_id, bool) or not isinstance(since_id, int) or not 0 <= since_id <= INT64_MAX:
        raise PanelError("bad_since", limit=INT64_MAX)
    doc = load(data_dir)
    if doc.invalid:
        raise PanelError("file_invalid")
    until_id = conn.execute("SELECT coalesce(max(id), 0) FROM entry").fetchone()[0]
    plan = refresh_plan(conn, doc.panels, since_id=since_id, until_id=until_id, events_changed=None)
    return {"from_id": since_id, "to_id": until_id, "refresh": plan["panels"],
            "probed": plan["probed"], "conservative": plan["conservative"]}  # fmt: skip
