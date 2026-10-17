"""Running panels and the follow-mode notice (ROADMAP 9.11, ARQUITETURA 10.14).

:func:`run_panel` runs the widgets of one panel (``store/panels.py``) with :func:`aggregate.aggregate`
under one total time budget. A widget that fails or times out is reported on its own (static code and
text, never the query or any log text) and never stops the others; widgets the budget cannot reach are
``skipped``. Results come in panel order whatever order the caller asked for, and depend only on the
case's data, so they do not depend on the order in which it was indexed.

:class:`PanelWatch` is the follow-mode hook. It does NOT decide which widgets changed: after a cycle that
brought news it only says "something may have changed" (``panels`` SSE event with a strictly increasing
``seq``), at most once per ``min_interval_s`` per case without ``throttled``. The client re-runs every
widget of the open panel on any ``panels`` event and on (re)connecting, so a lost or repeated event is
harmless: the next event, or the reconnection, reloads everything.
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .. import messages
from . import aggregate
from . import query as Q
from .filters import QueryInputError
from .panels import PanelError, load

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


# --------------------------------------------------------------------------- follow-mode notice


class PanelWatch:
    """Builds the body of the ``panels`` SSE event and limits its frequency per case.

    ``seq`` comes from one counter of the whole watcher, seeded from the wall clock in milliseconds, so it
    is strictly increasing for every case across publications, evictions of the state table and server
    restarts. The only per-case state is the instant of the last non-throttled notice (table bounded to
    ``MAX_THROTTLE_CASES``, least recently used first out; forgetting a case just lets its next notice
    through).
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_REFRESH_INTERVAL_S,
    ) -> None:
        self._clock = clock
        self._interval = min_interval_s
        self._lock = threading.Lock()
        self._last: OrderedDict[str, float] = OrderedDict()
        self._seq = time.time_ns() // 1_000_000

    def forget_case(self, case_id: str) -> None:
        with self._lock:
            self._last.pop(case_id, None)

    def notice(self, data_dir: str | Path, case_id: str, to_id: int | None) -> dict[str, Any] | None:
        """Body of the ``panels`` event for a cycle that brought news, or None when the user has no panels
        (or the file is unusable): ``{seq, to_id, throttled, retry_after_ms, min_interval_ms}``."""
        doc = load(data_dir)
        if doc.invalid or not doc.panels:
            self.forget_case(case_id)
            return None
        now = self._clock()
        with self._lock:
            self._seq += 1
            last = self._last.get(case_id)
            wait_ms = 0
            if last is not None and now - last < self._interval:
                wait_ms = max(1, math.ceil((self._interval - (now - last)) * 1000))
            else:
                self._last[case_id] = now
            if case_id in self._last:
                self._last.move_to_end(case_id)
            while len(self._last) > MAX_THROTTLE_CASES:
                self._last.popitem(last=False)
            return {
                "seq": self._seq,
                "to_id": to_id,
                "throttled": wait_ms > 0,
                "retry_after_ms": wait_ms,
                "min_interval_ms": round(self._interval * 1000),
            }
