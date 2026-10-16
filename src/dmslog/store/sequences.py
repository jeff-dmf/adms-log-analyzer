"""Expected sequences and their missing steps (ROADMAP 9.12), read from the derived events.

The derivers already follow the life cycles step by step (ARQUITETURA 6.5); an event whose sequence
did not complete keeps that in its columns and ``detail``. This module names the expected steps of
each sequence and reports, for the events that miss one, which step is missing and why. It reads
``event`` only (no schema change, nothing stored): the answer is the same after a full, an
incremental or a partial indexing because the events are (ARQUITETURA 9.3, 9.3b).

==============  ======================  ========  ====================================================
event kind      steps                   missing   when, and ``cause``
==============  ======================  ========  ====================================================
``operation``   ``start``, ``end``      ``end``   ``end_entry_id`` is NULL. ``detail.closed_by``:
                                                  ``new_start`` -> ``superseded`` (a new start on the
                                                  same thread came first: proven); ``evicted`` ->
                                                  ``capacity`` (too many open operations); end of the
                                                  log -> ``capture_cut`` when the status is
                                                  ``truncated`` (the ERS cut the file), else
                                                  ``end_of_log``.
``startup``     ``start``, ``hot``      ``hot``   no ``detail.hot_ts``. ``closed_by = timeout`` ->
                                                  ``timeout`` (120 s without ``HOT``: proven); else
                                                  ``end_of_log``.
``map_reload``  ``closed``, ``open``    ``open``  status ``truncated`` -> ``superseded`` (a new
                                                  ``closed`` of the same map came first: proven);
                                                  ``open`` -> ``end_of_log``.
``retry_seq``   ``attempt``,            ``outcome``  status ``open`` (neither sent nor finally failed).
                ``outcome``                       ``detail.evicted`` -> ``capacity``; else
                                                  ``end_of_log``.
==============  ======================  ========  ====================================================

``proven`` is true when a later record shows that the step never came (``superseded``, ``timeout``);
otherwise the log ended before it (the step may still come in a live log, or lie after the capture).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import Any

from .events import (
    _EVENT_COLUMNS,
    _EVENT_FROM,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Cursor,
    EventFilter,
    _event_json,
    _where,
)
from .query import PAGE_TIMEOUT_S, read_guard

CAUSES = ("superseded", "timeout", "capacity", "capture_cut", "end_of_log")
PROVEN = frozenset({"superseded", "timeout"})


def _detail_value(name: str) -> str:
    """``detail.<name>``, NULL when the column is absent or not valid JSON (constant SQL)."""
    return f"(CASE WHEN json_valid(ev.detail) THEN json_extract(ev.detail, '$.{name}') END)"


@dataclass(frozen=True, slots=True)
class Sequence:
    kind: str
    steps: tuple[str, ...]
    missing: str
    condition: str  # SQL over alias ``ev``: the event misses the step (constants only)


SEQUENCES: dict[str, Sequence] = {
    s.kind: s
    for s in (
        Sequence("operation", ("start", "end"), "end", "ev.end_entry_id IS NULL"),
        Sequence("startup", ("start", "hot"), "hot", f"{_detail_value('hot_ts')} IS NULL"),
        Sequence("map_reload", ("closed", "open"), "open", "ev.status IN ('truncated', 'open')"),
        Sequence("retry_seq", ("attempt", "outcome"), "outcome", "ev.status = 'open'"),
    )
}


def cause_of(kind: str, status: str | None, closed_by: Any, evicted: Any) -> str:
    """The ``cause`` of a missing step (module docstring)."""
    if kind == "operation":
        if closed_by == "new_start":
            return "superseded"
        if closed_by == "evicted":
            return "capacity"
        return "capture_cut" if status == "truncated" else "end_of_log"
    if kind == "startup":
        return "timeout" if closed_by == "timeout" else "end_of_log"
    if kind == "map_reload":
        return "superseded" if status == "truncated" else "end_of_log"
    return "capacity" if evicted is True else "end_of_log"


def _scope(flt: EventFilter) -> tuple[list[Sequence], EventFilter]:
    """The sequences asked for (``flt.kinds`` restricts them) and the filter without the kinds."""
    kinds = [k for k in flt.kinds if k in SEQUENCES] if flt.kinds else list(SEQUENCES)
    return [SEQUENCES[k] for k in dict.fromkeys(kinds)], replace(flt, kinds=())


def _missing_where(flt: EventFilter, after: Cursor | None = None) -> tuple[str, list[Any]] | None:
    seqs, rest = _scope(flt)
    if not seqs:
        return None
    where, params = _where(rest, after)
    parts = [f"(ev.kind = ? AND {s.condition})" for s in seqs]
    return f"({' OR '.join(parts)}) AND {where}", [*(s.kind for s in seqs), *params]


def _item(event: dict[str, Any]) -> dict[str, Any]:
    seq = SEQUENCES[event["kind"]]
    detail = event["detail"] if isinstance(event["detail"], dict) else {}
    cause = cause_of(seq.kind, event["status"], detail.get("closed_by"), detail.get("evicted"))
    return {
        "event": event,
        "steps": list(seq.steps),
        "missing": [seq.missing],
        "cause": cause,
        "proven": cause in PROVEN,
    }


def list_missing(
    conn: sqlite3.Connection,
    flt: EventFilter,
    limit: int = DEFAULT_LIMIT,
    after: Cursor | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One keyset page, by ``(ts_start, id)``, of the events that miss a step, and the next cursor.

    ``flt`` is the filter of ``/api/events``; its ``kinds`` restrict the sequences (kinds without a
    sequence match nothing). Item: ``{event, steps, missing, cause, proven}``.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    built = _missing_where(flt, after)
    if built is None:
        return [], None
    where, params = built
    sql = (
        f"SELECT {_EVENT_COLUMNS} FROM {_EVENT_FROM} WHERE {where}"  # noqa: S608 (constants only)
        " ORDER BY ev.ts_start, ev.id LIMIT ?"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
    items = [_item(_event_json(r)) for r in rows[:limit]]
    last = items[-1]["event"] if items else None
    nxt = str(Cursor(last["ts_start"], last["id"])) if last is not None and len(rows) > limit else None
    return items, nxt


def missing_summary(conn: sqlite3.Connection, flt: EventFilter) -> dict[str, Any]:
    """Counts of the missing steps per ``(kind, family, name)`` and cause.

    ``{"groups": [{kind, family, name, missing, n, causes: {cause: n}}], "total": n}``, groups ordered
    by ``(kind, family, name)`` (NULL first); ``causes`` lists only the causes seen.
    """
    built = _missing_where(flt)
    if built is None:
        return {"groups": [], "total": 0}
    where, params = built
    sql = (
        f"SELECT ev.kind, st.family, ev.name, ev.status, {_detail_value('closed_by')},"  # noqa: S608
        f" {_detail_value('evicted')}, count(*) FROM {_EVENT_FROM} WHERE {where}"
        " GROUP BY 1, 2, 3, 4, 5, 6"
    )
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    total = 0
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, params).fetchall()
    for kind, family, name, status, closed_by, evicted, n in rows:
        group = groups.setdefault(
            (kind, family, name),
            {"kind": kind, "family": family, "name": name, "missing": SEQUENCES[kind].missing, "n": 0,
             "causes": {}},
        )  # fmt: skip
        # json_extract gives 1 for a JSON true
        cause = cause_of(kind, status, closed_by, True if evicted == 1 else evicted)
        group["n"] += n
        group["causes"][cause] = group["causes"].get(cause, 0) + n
        total += n
    ordered = sorted(groups.items(), key=lambda kv: tuple((v is not None, v or "") for v in kv[0]))
    for _, group in ordered:
        group["causes"] = {c: group["causes"][c] for c in CAUSES if c in group["causes"]}
    return {"groups": [g for _, g in ordered], "total": total}
