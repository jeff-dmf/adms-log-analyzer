"""Read-only queries over derived events and periodic statistics (ARQUITETURA 5, 6.5, 10).

HTTP contract (all GET, ``case`` is required and canonicalized like every other route; validation
errors are ``400 invalid_field``; an unknown event is ``404 event_not_found``). Times are log
wall-clock strings ``YYYY-MM-DD[ HH:MM[:SS[.mmm]]]`` (the same text the grid shows); lists are
comma separated. User text only ever reaches SQL as bound parameters.

``GET /api/events?case=&kind=&name=&status=&stream=&source=&q=&from=&to=&limit=&after=``
    ``{"events": [<event>], "next": "<ts_start>:<id>" | null}``. Ordered by ``(ts_start, id)``;
    keyset pagination (``after`` is the ``next`` of the previous page; never OFFSET). ``kind``,
    ``name`` and ``status`` accept comma lists; ``stream`` is a stream id; ``source`` keeps events
    whose first entry belongs to that source id; ``q`` matches ``event.key`` exactly or by prefix
    (case sensitive); ``from``/``to`` bound ``ts_start`` (inclusive). Default limit 200, max 1000.
    ``<event>`` = ``{id, stream_id, family, process, sensitive, kind, name, key, tid,
    start_entry_id, end_entry_id, ts_start, ts_end, duration_ms, status, sev, n_entries,
    detail}`` where ``detail`` is the parsed JSON column (``null`` when absent or malformed).

``GET /api/events/<id>?case=``
    ``{"event": <event>, "entries": [{id, ts, tid, level, sev, tpl_code, msg}], "entries_total": n,
    "related": [<event>]}``. Entries are those with ``entry.event_id = id``: the first 200 by id,
    ``msg`` cut at 300 characters. ``related`` is present only when the event has a key
    (``event.key`` or ``detail.op_key``): up to 20 events of kind ``retry_seq`` or ``exception``
    with the same key, ordered by ``(ts_start, id)``.

``GET /api/events/stats?case=&kind=operation&stream=&from=&to=``
    ``{"kind": ..., "groups": [{family, name, n, ok, business_error, fail, timeout, open,
    truncated, p50_ms, p90_ms, p99_ms, max_ms, avg_ms}], "totals": {same without family/name}}``.
    Percentiles are exact, nearest-rank, over events that have a duration (open events have
    none): with the durations of a group sorted ascending, ``cnt`` of them, the pXX value is the
    one at rank ``ceil(cnt * XX / 100)``; SQL window functions ``row_number() OVER (PARTITION BY
    ... ORDER BY duration_ms)`` and ``count(*) OVER`` pick those ranks, nothing is sampled.
    ``avg_ms`` is the mean of the same durations (one decimal). Without durations the four
    duration fields are ``null``. ``level_window`` summaries are not served here: use the generic
    ``/api/events?kind=level_window`` route.

``GET /api/events/histogram?case=&kind=&name=&bucket=minute|hour[&stream=&from=&to=]``
    ``{"bucket": "minute", "bucket_ms": 60000, "buckets": [{"t": <bucket start ms>, "total": n,
    "counts": {<status>: n}}], "truncated": bool}``: events counted by ``ts_start`` bucket and
    status (``unknown`` when status is NULL), ascending, at most 5000 buckets.

``GET /api/stats/series?case=&stream=&side=&method=&from=&to=``
    ``{"samples": [{stream_id, ts, side, method, calls, req_bytes, resp_bytes, d_calls,
    d_req_bytes, d_resp_bytes}], "truncated": bool}``, ordered by ``(ts, stream_id, side,
    method)``, at most 5000 samples.

``GET /api/stats/methods?case=&stream=``
    ``{"methods": [{stream_id, family, side, method, calls, last_ts}]}``: one row per
    ``(stream, side, method)`` with the last cumulative call count.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .query import PAGE_TIMEOUT_S, read_guard

if TYPE_CHECKING:
    import sqlite3

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
ENTRIES_LIMIT = 200
MSG_CHARS = 300
RELATED_LIMIT = 20
RELATED_KINDS = ("retry_seq", "exception")
SERIES_LIMIT = 5000
MAX_BUCKETS = 5000
BUCKET_MS = {"minute": 60_000, "hour": 3_600_000}
STATUSES = ("ok", "business_error", "fail", "timeout", "open", "truncated")
PERCENTILES = (("p50_ms", 50), ("p90_ms", 90), ("p99_ms", 99))

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_EPOCH = datetime(1970, 1, 1)
_TIME = re.compile(r"\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,3}))?)?)?\s*\Z")

_EVENT_COLUMNS = (
    "ev.id, ev.stream_id, st.family, st.process, st.sensitive, ev.kind, ev.name, ev.key, ev.tid,"
    " ev.start_entry_id, ev.end_entry_id, ev.ts_start, ev.ts_end, ev.duration_ms, ev.status, ev.sev,"
    " ev.n_entries, ev.detail"
)
_EVENT_FROM = "event ev LEFT JOIN stream st ON st.id = ev.stream_id"


def parse_log_time(text: str) -> int | None:
    """Parse a log wall-clock time into ``ts`` milliseconds (the log text read as UTC), or None."""
    m = _TIME.match(text)
    if m is None:
        return None
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour = int(m[4] or 0)
    minute = int(m[5] or 0)
    second = int(m[6] or 0)
    millis = int((m[7] or "0").ljust(3, "0"))
    try:
        moment = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    return (moment - _EPOCH) // _MS + millis


_MS = datetime(1970, 1, 1, 0, 0, 0, 1000) - _EPOCH  # one millisecond as a timedelta


@dataclass(frozen=True, slots=True)
class Cursor:
    ts: int
    id: int

    @classmethod
    def parse(cls, text: str) -> Cursor | None:
        """``"<ts_start>:<id>"`` or None when malformed or outside the int64 range of SQLite."""
        ts_text, sep, id_text = text.partition(":")
        if not sep or len(text) > 64:
            return None
        try:
            ts, id_ = int(ts_text), int(id_text)
        except ValueError:
            return None
        if not (_INT64_MIN <= ts <= _INT64_MAX and _INT64_MIN <= id_ <= _INT64_MAX):
            return None
        return cls(ts, id_)

    def __str__(self) -> str:
        return f"{self.ts}:{self.id}"


@dataclass(frozen=True, slots=True)
class EventFilter:
    kinds: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    stream_id: int | None = None
    source_id: int | None = None
    key_query: str | None = None
    ts_from: int | None = None
    ts_to: int | None = None
    # Optional entry condition (compiled grid filter over alias ``e``, constants and ``?`` only): keeps the
    # events whose start record satisfies it (used by the view export).
    entry_sql: str | None = None
    entry_params: tuple[Any, ...] = ()
    collection: int | None = None  # collection group (U21): events of that group only; None = every group


def _where(flt: EventFilter, after: Cursor | None = None) -> tuple[str, list[Any]]:
    """SQL condition over alias ``ev`` (constants and ``?`` only) and its parameters."""
    parts: list[str] = []
    params: list[Any] = []
    for column, values in (("ev.kind", flt.kinds), ("ev.name", flt.names), ("ev.status", flt.statuses)):
        if values:
            parts.append(f"{column} IN ({','.join('?' * len(values))})")
            params.extend(values)
    if flt.stream_id is not None:
        parts.append("ev.stream_id = ?")
        params.append(flt.stream_id)
    if flt.source_id is not None:
        parts.append("EXISTS (SELECT 1 FROM entry n WHERE n.id = ev.start_entry_id AND n.source_id = ?)")
        params.append(flt.source_id)
    if flt.collection is not None:
        parts.append("ev.stream_id IN (SELECT id FROM stream WHERE collection_group = ?)")
        params.append(flt.collection)
    if flt.entry_sql:
        parts.append(f"EXISTS (SELECT 1 FROM entry e WHERE e.id = ev.start_entry_id AND ({flt.entry_sql}))")
        params.extend(flt.entry_params)
    if flt.key_query:
        parts.append("substr(ev.key, 1, ?) = ?")
        params.extend((len(flt.key_query), flt.key_query))
    if flt.ts_from is not None:
        parts.append("ev.ts_start >= ?")
        params.append(flt.ts_from)
    if flt.ts_to is not None:
        parts.append("ev.ts_start <= ?")
        params.append(flt.ts_to)
    if after is not None:
        parts.append("(ev.ts_start > ? OR (ev.ts_start = ? AND ev.id > ?))")
        params.extend((after.ts, after.ts, after.id))
    return (" AND ".join(parts) if parts else "1"), params


def _detail(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _event_json(row: tuple[Any, ...]) -> dict[str, Any]:
    (id_, stream_id, family, process, sensitive, kind, name, key, tid, start_id, end_id, ts_start, ts_end,
     duration, status, sev, n_entries, detail) = row  # fmt: skip
    return {
        "id": id_, "stream_id": stream_id, "family": family, "process": process,
        "sensitive": None if sensitive is None else bool(sensitive), "kind": kind, "name": name,
        "key": key, "tid": tid, "start_entry_id": start_id, "end_entry_id": end_id,
        "ts_start": ts_start, "ts_end": ts_end, "duration_ms": duration, "status": status, "sev": sev,
        "n_entries": n_entries, "detail": _detail(detail),
    }  # fmt: skip


# --------------------------------------------------------------------------- events


def list_events(
    conn: sqlite3.Connection,
    flt: EventFilter,
    limit: int = DEFAULT_LIMIT,
    after: Cursor | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One keyset page of events ordered by ``(ts_start, id)`` and the cursor of the next page."""
    limit = max(1, min(limit, MAX_LIMIT))
    where, params = _where(flt, after)
    sql = (
        f"SELECT {_EVENT_COLUMNS} FROM {_EVENT_FROM} WHERE {where}"  # noqa: S608 (constants only)
        " ORDER BY ev.ts_start, ev.id LIMIT ?"
    )
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
    events = [_event_json(r) for r in rows[:limit]]
    nxt = str(Cursor(events[-1]["ts_start"], events[-1]["id"])) if len(rows) > limit else None
    return events, nxt


def get_event(conn: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    """The event, its first entries and the related retry/exception events (None if unknown)."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        row = conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM {_EVENT_FROM} WHERE ev.id = ?",  # noqa: S608
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        event = _event_json(row)
        entries = [
            {"id": r[0], "ts": r[1], "tid": r[2], "level": r[3], "sev": r[4], "tpl_code": r[5], "msg": r[6]}
            for r in conn.execute(
                "SELECT e.id, e.ts, e.tid, e.level, e.sev, t.code, substr(e.msg, 1, ?)"
                " FROM entry e LEFT JOIN template t ON t.id = e.tpl_id AND e.tpl_id <> 0"
                " WHERE e.event_id = ? ORDER BY e.id LIMIT ?",
                (MSG_CHARS, event_id, ENTRIES_LIMIT),
            )
        ]
        total = conn.execute("SELECT count(*) FROM entry WHERE event_id = ?", (event_id,)).fetchone()[0]
        result: dict[str, Any] = {"event": event, "entries": entries, "entries_total": total}
        keys = _keys_of(event)
        if keys:
            marks = ",".join("?" * len(keys))
            kinds = ",".join("?" * len(RELATED_KINDS))
            related = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM {_EVENT_FROM}"  # noqa: S608
                f" WHERE ev.key IN ({marks}) AND ev.kind IN ({kinds}) AND ev.id <> ?"
                " ORDER BY ev.ts_start, ev.id LIMIT ?",
                (*keys, *RELATED_KINDS, event_id, RELATED_LIMIT),
            ).fetchall()
            result["related"] = [_event_json(r) for r in related]
    return result


def _keys_of(event: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    detail = event["detail"]
    candidates = [event["key"], detail.get("op_key") if isinstance(detail, dict) else None]
    for value in candidates:
        if isinstance(value, str) and value and value not in keys:
            keys.append(value)
    return keys


# --------------------------------------------------------------------------- stats


def _empty_group() -> dict[str, Any]:
    return {"n": 0, **dict.fromkeys(STATUSES, 0), "p50_ms": None, "p90_ms": None, "p99_ms": None,
            "max_ms": None, "avg_ms": None}  # fmt: skip


def event_stats(conn: sqlite3.Connection, flt: EventFilter) -> dict[str, Any]:
    """Per ``(family, name)`` counts by status and exact duration percentiles, plus totals."""
    where, params = _where(flt)
    counts = ", ".join(f"coalesce(sum(ev.status = '{s}'), 0)" for s in STATUSES)  # constants only
    base = f"{_EVENT_FROM} WHERE {where}"
    with read_guard(conn, PAGE_TIMEOUT_S):
        groups: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        for row in conn.execute(
            f"SELECT st.family, ev.name, count(*), {counts}, max(ev.duration_ms), avg(ev.duration_ms)"  # noqa: S608
            f" FROM {base} GROUP BY st.family, ev.name ORDER BY st.family, ev.name",
            params,
        ):
            group = _empty_group()
            group["family"], group["name"] = row[0], row[1]
            group["n"] = row[2]
            group.update(zip(STATUSES, row[3 : 3 + len(STATUSES)], strict=True))
            group["max_ms"] = row[3 + len(STATUSES)]
            avg = row[4 + len(STATUSES)]
            group["avg_ms"] = None if avg is None else round(avg, 1)
            groups[(row[0], row[1])] = group
        for fam, name, cnt, rn, dur in conn.execute(
            "SELECT fam, nm, cnt, rn, dur FROM ("
            " SELECT st.family AS fam, ev.name AS nm, ev.duration_ms AS dur,"
            "  row_number() OVER (PARTITION BY st.family, ev.name ORDER BY ev.duration_ms, ev.id) AS rn,"
            "  count(*) OVER (PARTITION BY st.family, ev.name) AS cnt"
            f"  FROM {base} AND ev.duration_ms IS NOT NULL)"  # noqa: S608
            " WHERE rn IN ((cnt * 50 + 99) / 100, (cnt * 90 + 99) / 100, (cnt * 99 + 99) / 100)",
            params,
        ):
            group = groups[(fam, name)]
            for field_name, pct in PERCENTILES:
                if rn == (cnt * pct + 99) // 100:
                    group[field_name] = dur
        totals = _empty_group()
        row = conn.execute(
            f"SELECT count(*), {counts}, max(ev.duration_ms), avg(ev.duration_ms) FROM {base}",  # noqa: S608
            params,
        ).fetchone()
        totals["n"] = row[0]
        totals.update(zip(STATUSES, row[1 : 1 + len(STATUSES)], strict=True))
        totals["max_ms"] = row[1 + len(STATUSES)]
        avg = row[2 + len(STATUSES)]
        totals["avg_ms"] = None if avg is None else round(avg, 1)
        for cnt, rn, dur in conn.execute(
            "SELECT cnt, rn, dur FROM ("
            " SELECT ev.duration_ms AS dur, row_number() OVER (ORDER BY ev.duration_ms, ev.id) AS rn,"
            f"  count(*) OVER () AS cnt FROM {base} AND ev.duration_ms IS NOT NULL)"  # noqa: S608
            " WHERE rn IN ((cnt * 50 + 99) / 100, (cnt * 90 + 99) / 100, (cnt * 99 + 99) / 100)",
            params,
        ):
            for field_name, pct in PERCENTILES:
                if rn == (cnt * pct + 99) // 100:
                    totals[field_name] = dur
    return {"groups": list(groups.values()), "totals": totals}


def event_histogram(conn: sqlite3.Connection, flt: EventFilter, bucket: str) -> dict[str, Any]:
    """Events per ``ts_start`` bucket and status, ascending, capped at ``MAX_BUCKETS`` buckets."""
    size = BUCKET_MS[bucket]
    where, params = _where(flt)
    buckets: list[dict[str, Any]] = []
    truncated = False
    with read_guard(conn, PAGE_TIMEOUT_S):
        cur = conn.execute(
            f"SELECT (ev.ts_start / ?) * ? AS t, coalesce(ev.status, 'unknown'), count(*)"  # noqa: S608
            f" FROM event ev WHERE {where} GROUP BY t, 2 ORDER BY t, 2",
            (size, size, *params),
        )
        for t, status, n in cur:
            if not buckets or buckets[-1]["t"] != t:
                if len(buckets) >= MAX_BUCKETS:
                    truncated = True
                    break
                buckets.append({"t": t, "total": 0, "counts": {}})
            buckets[-1]["counts"][status] = n
            buckets[-1]["total"] += n
    return {"bucket": bucket, "bucket_ms": size, "buckets": buckets, "truncated": truncated}


# --------------------------------------------------------------------------- statistics samples


def stat_series(
    conn: sqlite3.Connection,
    *,
    stream_id: int | None = None,
    side: str | None = None,
    method: str | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    collection: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Statistics samples ordered by ``(ts, stream_id, side, method)``; second item: truncated.

    ``collection``: only the samples of the streams of that collection group (U21)."""
    parts: list[str] = []
    params: list[Any] = []
    for sql, value in (
        ("stream_id = ?", stream_id),
        ("side = ?", side),
        ("method = ?", method),
        ("ts >= ?", ts_from),
        ("ts <= ?", ts_to),
        ("stream_id IN (SELECT id FROM stream WHERE collection_group = ?)", collection),
    ):
        if value is not None:
            parts.append(sql)
            params.append(value)
    where = " AND ".join(parts) if parts else "1"
    names = ("stream_id", "ts", "side", "method", "calls", "req_bytes", "resp_bytes", "d_calls",
             "d_req_bytes", "d_resp_bytes")  # fmt: skip
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(
            f"SELECT {', '.join(names)} FROM stat_sample WHERE {where}"  # noqa: S608
            " ORDER BY ts, stream_id, side, method LIMIT ?",
            (*params, SERIES_LIMIT + 1),
        ).fetchall()
    return [dict(zip(names, r, strict=True)) for r in rows[:SERIES_LIMIT]], len(rows) > SERIES_LIMIT


def stat_methods(
    conn: sqlite3.Connection, stream_id: int | None = None, collection: int | None = None
) -> list[dict[str, Any]]:
    """One row per ``(stream, side, method)`` with the last cumulative call count.

    ``collection``: only the streams of that collection group (U21)."""
    parts: list[str] = []
    params: list[Any] = []
    for sql, value in (
        ("stream_id = ?", stream_id),
        ("stream_id IN (SELECT id FROM stream WHERE collection_group = ?)", collection),
    ):
        if value is not None:
            parts.append(sql)
            params.append(value)
    where = "WHERE " + " AND ".join(parts) if parts else ""
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(
            "SELECT s.stream_id, st.family, s.side, s.method, s.calls, s.ts FROM ("
            " SELECT stream_id, side, method, calls, ts,"
            "  row_number() OVER (PARTITION BY stream_id, side, method ORDER BY ts DESC) AS rn"
            f"  FROM stat_sample {where}) s"  # noqa: S608
            " LEFT JOIN stream st ON st.id = s.stream_id WHERE s.rn = 1"
            " ORDER BY s.stream_id, s.side, s.method",
            params,
        ).fetchall()
    return [
        {"stream_id": r[0], "family": r[1], "side": r[2], "method": r[3], "calls": r[4], "last_ts": r[5]}
        for r in rows
    ]
