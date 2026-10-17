"""Synthetic cases with several collection groups and a brute-force model of the clock offsets (ROADMAP 9.15).

The model never uses ``store/clock.py`` nor SQL: it evaluates the filter tree in Python on the ADJUSTED time
of each record (``ts + offset of its group``) and orders by ``(no time first by id, then ts_adjusted, id)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dmslog.store.db import create_case_db

MINUTE = 60_000

# group -> its collections (the first is the head, id = group); each collection has two sources
GROUPS = {1: (1, 2), 3: (3,), 4: (4,)}
SOURCES = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4}  # source -> collection
GROUP_OF_COLLECTION = {c: g for g, cs in GROUPS.items() for c in cs}


def group_of_source(source: int) -> int:
    return GROUP_OF_COLLECTION[SOURCES[source]]


@dataclass(frozen=True)
class Row:
    id: int
    source: int
    ts: int | None
    sev: int


def build(path: Path, rows: list[tuple[int, int | None, int]], offsets: dict[int, int]) -> list[Row]:
    """Case at ``path`` with ``rows`` = ``[(source, ts, sev)]`` (ids in that order) and the group offsets
    written on every collection row of the group. Buckets per minute as the writer fills them."""
    conn = create_case_db(path)
    out = []
    with conn:
        for group, collections in GROUPS.items():
            for cid in collections:
                conn.execute(
                    "INSERT INTO collection(id, kind, path, ers_group, clock_offset_ms, added_at)"
                    " VALUES (?, 'folder', ?, ?, ?, '2030-01-01')",
                    (cid, f"/cases/c{cid}", group, offsets.get(group, 0)),
                )
        for sid, cid in SOURCES.items():
            conn.execute(
                "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
                " VALUES (?,?,?,?,?,0)",
                (sid, GROUP_OF_COLLECTION[cid], "AWFM", "WfmAdapter", 4000 + sid),
            )
            conn.execute(
                "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope, status)"
                " VALUES (?,?,?,?,?,'adms','ready')",
                (sid, cid, sid, f"Log/s{sid}.log", f"s{sid}.log"),
            )
        buckets: dict[tuple[int, int, int], int] = {}
        for i, (source, ts, sev) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, flags, msg)"
                " VALUES (?,?,?,?,1,?,?,0,0,?)",
                (i, source, i, i * 10, ts, sev, f"record {i}"),
            )
            out.append(Row(i, source, ts, sev))
            if ts is not None:
                key = (source, ts // MINUTE, sev)
                buckets[key] = buckets.get(key, 0) + 1
        conn.executemany(
            "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) VALUES (?,?,?,0,?)",
            [(*k, n) for k, n in buckets.items()],
        )
        conn.executemany(
            "INSERT INTO bucket_sev(source_id, minute, sev, n) VALUES (?,?,?,?)",
            [(*k, n) for k, n in buckets.items()],
        )
    conn.close()
    return out


# --------------------------------------------------------------------------- the model


def _cmp(op: str, a: int, b: int) -> bool:
    return {
        "eq": a == b,
        "ne": a != b,
        "lt": a < b,
        "le": a <= b,
        "gt": a > b,
        "ge": a >= b,
    }[op]


def matches(tree, row: Row, offsets: dict[int, int]) -> bool:
    """The filter tree on ``row``, times on adjusted time (subset: and/or/not/ts/sev/source/collection)."""
    if tree is None or tree == {}:
        return True
    ((op, arg),) = tree.items()
    if op == "and":
        return all(matches(c, row, offsets) for c in arg)
    if op == "or":
        return any(matches(c, row, offsets) for c in arg)
    if op == "not":
        return not matches(arg, row, offsets)
    if op == "ts":
        if row.ts is None:
            return False
        adj = row.ts + offsets.get(group_of_source(row.source), 0)
        if isinstance(arg, dict):
            return all(_cmp(o, adj, v) for o, v in arg.items())
        return adj in (arg if isinstance(arg, list) else [arg])
    if op == "sev":
        return row.sev in (arg if isinstance(arg, list) else [arg])
    if op == "source":
        return row.source in (arg if isinstance(arg, list) else [arg])
    if op == "collection":
        return group_of_source(row.source) in (arg if isinstance(arg, list) else [arg])
    raise AssertionError(op)


def adjusted(row: Row, offsets: dict[int, int]) -> int | None:
    return None if row.ts is None else row.ts + offsets.get(group_of_source(row.source), 0)


def expected(rows: list[Row], offsets: dict[int, int], tree=None) -> list[tuple[int, int | None]]:
    """``[(id, ts_adjusted)]`` of the records the filter keeps, in grid order."""
    kept = [r for r in rows if matches(tree, r, offsets)]

    def key(r: Row):
        adj = adjusted(r, offsets)
        return (0, 0, r.id) if adj is None else (1, adj, r.id)

    return [(r.id, adjusted(r, offsets)) for r in sorted(kept, key=key)]


def walk(page_fn, direction: str, limit: int) -> list[tuple[int, int | None]]:
    """Every row by following the cursors of ``page_fn(cursor, direction, limit)`` from one end."""
    out: list[tuple[int, int | None]] = []
    cursor = None
    for _ in range(500):
        page = page_fn(cursor, direction, limit)
        chunk = [(r["id"], r.get("ts_adjusted", r["ts"])) for r in page.rows]
        out = out + chunk if direction == "fwd" else chunk + out
        if not page.has_more or not page.rows:
            return out
        cursor = str(page.last if direction == "fwd" else page.first)
    raise AssertionError("no end")
