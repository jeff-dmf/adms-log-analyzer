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
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from .. import messages
from . import aggregate
from . import query as Q
from .filters import INT64_MAX, QueryInputError, compile_filter
from .panels import PanelError, _json_text, load

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
        share = min(remaining, 2 * remaining / (len(todo) - k))  # unused time flows to the later widgets
        kwargs: dict[str, Any] = {
            "timeout_s": min(aggregate.AGG_TIMEOUT_S, share / 2),
            "estimate_timeout_s": min(aggregate.ESTIMATE_TIMEOUT_S, share / 12),
            "bucket_timeout_s": min(aggregate.BUCKET_TIMEOUT_S, share / 12),
        }
        try:
            if knowledge is not None and widget["query"].get("lookups") and not know:
                know.append(knowledge())
            kwargs["knowledge"] = know[0] if know else None
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
            rows, _ = key = _widget_key(widget)
            if rows == "events" and events_changed is not False:
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


class PanelWatch:
    """Per-case frequency limit and event body of the ``panels`` SSE event."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_REFRESH_INTERVAL_S,
    ) -> None:
        self._clock = clock
        self._interval = min_interval_s
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._pending: dict[str, tuple[int, bool | None]] = {}  # case -> (earliest unreported id, events)

    def forget_case(self, case_id: str) -> None:
        with self._lock:
            self._last.pop(case_id, None)
            self._pending.pop(case_id, None)

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
            pending = self._pending.get(case_id)
            lo = baseline if baseline is not None else None
            if pending is not None:
                lo = pending[0] if lo is None else min(lo, pending[0])
                events_changed = (
                    None if events_changed is None or pending[1] is None else (events_changed or pending[1])
                )
            last = self._last.get(case_id)
            if last is not None and now - last < self._interval:
                # an unknown baseline is remembered as "everything since id 0", so no change is lost
                self._pending[case_id] = (0 if lo is None else lo, events_changed)
                wait_ms = max(1, math.ceil((self._interval - (now - last)) * 1000))
                return {
                    "from_id": lo, "to_id": until_id, "throttled": True, "retry_after_ms": wait_ms,
                    "min_interval_ms": round(self._interval * 1000), "full": lo is None, "refresh": [],
                }  # fmt: skip
            self._pending.pop(case_id, None)
            self._last[case_id] = now
            if len(self._last) > MAX_THROTTLE_CASES:  # cases come and go: keep the table bounded
                self._last.pop(next(iter(self._last)))
        if lo is None:
            refresh = [{"panel": p["id"], "widgets": [w["id"] for w in p["widgets"]]} for p in doc.panels]
            refresh = [r for r in refresh if r["widgets"]]
            probed = conservative = 0
        else:
            plan = refresh_plan(
                conn, doc.panels, since_id=lo, until_id=until_id, events_changed=events_changed
            )
            refresh, probed, conservative = plan["panels"], plan["probed"], plan["conservative"]
        return {
            "from_id": lo, "to_id": until_id, "throttled": False, "retry_after_ms": 0,
            "min_interval_ms": round(self._interval * 1000), "full": lo is None, "refresh": refresh,
            "probed": probed, "conservative": conservative,
        }  # fmt: skip


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
