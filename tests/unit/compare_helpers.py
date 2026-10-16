"""Synthetic cases for the comparison of views (store/viewcompare.py, ROADMAP 9.7). Values are fictitious.

A case is described logically (records by locator, templates by code, events by name and records), so
the same content can be written with different ids: another order of the sources, gaps in the ids,
other template, stream and event ids. That is what a complete, incremental or partial indexing changes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from dmslog.store.db import connect, create_case_db

CODES = ["CMP-001", "CMP-002", "CMP-003", "CMP-004", "CMP-005", "CMP-006"]
# stream key -> (collection path, ers_group, family, pid, member)
STREAMS = {
    "wfm1": ("c1.zip", 1, "AWFM", 100, "Log/WfmAdapter_100.log"),
    "wfm2": ("c1.zip", 1, "AWFM", 200, "Log/WfmAdapter_200.log"),
    "ont": ("c2.zip", 2, "AONT", 300, "Log/OntAdapter_300.log"),
}


@dataclass
class Record:
    stream: str
    line: int
    ts: int | None
    code: str | None  # None = unknown template (tpl_id 0), typed by its message signature
    msg: str


@dataclass
class Content:
    records: list[Record]
    events: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # name -> [(stream, line)]


@dataclass
class Built:
    path: Path
    entry: dict[tuple[str, int], int]  # (stream, line) -> entry id
    stream: dict[str, int]
    event: dict[str, int]


def random_content(seed: int, n: int = 60, ts_span: int = 40) -> Content:
    """Records of the three streams with repeated timestamps (ties across files), unknown lines and events."""
    rnd = random.Random(seed)
    records: list[Record] = []
    lines = dict.fromkeys(STREAMS, 0)
    for _ in range(n):
        stream = rnd.choice(list(STREAMS))
        lines[stream] += 1
        ts = None if rnd.random() < 0.05 else 1_000_000 + rnd.randint(0, ts_span)
        if rnd.random() < 0.15:
            code, msg = None, rnd.choice(["odd thing 12 happened", "odd thing 99 happened", "other 'x' line"])
        else:
            code, msg = rnd.choice(CODES), f"message {rnd.randint(0, 9)}"
        records.append(Record(stream, lines[stream], ts, code, msg))
    events: dict[str, list[tuple[str, int]]] = {}
    for name in ("op1", "op2"):
        stream = rnd.choice(list(STREAMS))
        mine = [r for r in records if r.stream == stream]
        if mine:
            k = rnd.randint(0, len(mine) - 1)
            events[name] = [(r.stream, r.line) for r in mine[k : k + rnd.randint(1, 12)]]
    return Content(records, events)


def build(path: Path, content: Content, permute: int | None = None) -> Built:
    """Write ``content`` as a case database. ``permute`` (a seed) changes every id but not the content:
    sources inserted in another order and interleaved, gaps in the ids, other template/stream/event ids.
    Inside one file the ids still follow the line order, as the writer guarantees."""
    rnd = random.Random(permute)
    stream_keys = list(STREAMS)
    codes = list(CODES)
    if permute is not None:
        rnd.shuffle(stream_keys)
        rnd.shuffle(codes)
    conn = create_case_db(path)
    built = Built(path, {}, {}, {})
    with conn:
        paths = sorted({v[0] for v in STREAMS.values()}, reverse=permute is not None)
        coll_id = {}
        for k, cpath in enumerate(paths, 1):
            group = next(v[1] for v in STREAMS.values() if v[0] == cpath)
            cid = k * (7 if permute is not None else 1)
            coll_id[cpath] = cid
            conn.execute(
                "INSERT INTO collection(id, kind, path, ers_group, added_at)"
                " VALUES (?, 'ers_zip', ?, ?, '2026')",
                (cid, cpath, group),
            )
        for k, key in enumerate(stream_keys, 1):
            cpath, group, family, pid, member = STREAMS[key]
            sid = k * (5 if permute is not None else 1) + (10 if permute is not None else 0)
            built.stream[key] = sid
            conn.execute(
                "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
                " VALUES (?,?,?,?,?,0)",
                (sid, group, family, family.title(), pid),
            )
            conn.execute(
                "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope)"
                " VALUES (?,?,?,?,?, 'adms')",
                (sid, coll_id[cpath], sid, member, member.rsplit("/", 1)[-1]),
            )
        tpl_id = {}
        for k, code in enumerate(codes, 1):
            tid = k * (3 if permute is not None else 1)
            tpl_id[code] = tid
            conn.execute(
                "INSERT INTO template(id, code, family, class, sev, title, pattern, fts, def_version)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (tid, code, "AWFM", "other", 4, f"Title of {code}", "x", 1, "v"),
            )
        # insertion order: per stream in line order; streams interleaved at random when permuted
        queues = {key: [r for r in content.records if r.stream == key] for key in stream_keys}
        for key in queues:
            queues[key].sort(key=lambda r: r.line)
        next_id = 1
        while any(queues.values()):
            live = [k for k in stream_keys if queues[k]]
            key = rnd.choice(live) if permute is not None else live[0]
            rec = queues[key].pop(0)
            next_id += rnd.randint(1, 4) if permute is not None else 1
            built.entry[(rec.stream, rec.line)] = next_id
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev,"
                " tpl_id, flags, fields, msg) VALUES (?,?,?,?,1,?,?,?,7,4,?,0,NULL,?)",
                (
                    next_id,
                    built.stream[rec.stream],
                    rec.line,
                    rec.line * 80,
                    rec.ts,
                    STREAMS[rec.stream][3],
                    16,
                    tpl_id[rec.code] if rec.code else 0,
                    rec.msg,
                ),
            )
        names = sorted(content.events, reverse=permute is not None)
        for k, name in enumerate(names, 1):
            members = content.events[name]
            eid = k * (11 if permute is not None else 1)
            built.event[name] = eid
            first = next(r for r in content.records if (r.stream, r.line) == members[0])
            conn.execute(
                "INSERT INTO event(id, stream_id, kind, name, ts_start, status, sev)"
                " VALUES (?,?,?,?,?, 'ok', 4)",
                (eid, built.stream[first.stream], "operation", name, first.ts or 0),
            )
        for name in sorted(content.events):  # a record belongs to the first event (by name) that lists it
            for loc in content.events[name]:
                conn.execute(
                    "UPDATE entry SET event_id = ? WHERE id = ? AND event_id IS NULL",
                    (built.event[name], built.entry[loc]),
                )
        conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
    conn.close()
    return built


def open_ro(path: Path):
    return connect(path, readonly=True)


def without_ids(result: dict) -> dict:
    """The comparison result with every id removed (rows, events and streams of the selectors)."""
    out = dict(result)
    for side in ("a", "b"):
        sel = dict(out[side])
        if "event" in sel:
            sel["event"] = {k: v for k, v in sel["event"].items() if k not in ("id", "stream_id")}
        if "stream" in sel:
            sel["stream"] = {k: v for k, v in sel["stream"].items() if k != "id"}
        out[side] = sel
    rows = []
    for row in out["rows"]:
        row = dict(row)
        for side in ("a", "b"):
            if row[side] is not None:
                row[side] = {k: v for k, v in row[side].items() if k not in ("id", "source_id", "tpl_id")}
        rows.append(row)
    out["rows"] = rows
    return out
