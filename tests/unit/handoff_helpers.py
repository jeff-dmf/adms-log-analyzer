"""Synthetic case databases and a brute-force oracle for the ``handoff`` link rules (no real data).

The world has four log files: a Scheduler (code ``TST-001``) and a Launcher (``TST-002`` and ``TST-003``) of
family ``DMSJOB`` and two FunctionHost files (``TST-101``) of family ``DMSFH``. Two rules::

    TST-001 -> TST-002   handoff inside DMSJOB (Scheduler -> Launcher)
    TST-003 -> TST-101   handoff to DMSFH, with ``pid_field = "fh_pid"``
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from dmslog.derive.links import link_pending, link_stats
from dmslog.parse.templates import Catalog, load_texts
from dmslog.store.db import create_case_db

MAX_DT = 50

TOML_JOB = f"""
schema = 1
family = "DMSJOB"

[[template]]
code = "TST-001"
class = "req_resp"
regex = '''^sent (?P<job_id>\\d+)$'''
fields = {{ job_id = "int" }}
keys = ["job_id"]

[[template]]
code = "TST-002"
class = "start"
regex = '''^received (?P<job_id>\\d+)$'''
fields = {{ job_id = "int" }}
keys = ["job_id"]

[[template]]
code = "TST-003"
class = "req_resp"
regex = '''^execute (?P<job_id>\\d+) on (?P<fh_pid>\\d+)$'''
fields = {{ job_id = "int", fh_pid = "int" }}
keys = ["job_id"]

[[link_rule]]
kind = "handoff"
a = "TST-001"
b = "TST-002"
key = "job_id"
scope = "collection"
max_dt_ms = {MAX_DT}

[[link_rule]]
kind = "handoff"
a = "TST-003"
b = "TST-101"
key = "job_id"
scope = "collection"
families = ["DMSFH"]
pid_field = "fh_pid"
max_dt_ms = {MAX_DT}
"""

TOML_FH = """
schema = 1
family = "DMSFH"

[[template]]
code = "TST-101"
class = "start"
regex = '''^job (?P<job_id>\\d+) running$'''
fields = { job_id = "int" }
keys = ["job_id"]
"""

RULE1 = "DMSJOB/handoff:TST-001>TST-002"
RULE2 = "DMSJOB/handoff:TST-003>TST-101"

# file -> (family, process, envelope pid, codes it may hold)
FILES: dict[str, tuple[str, str, int | None, tuple[str, ...]]] = {
    "sched.log": ("DMSJOB", "Sched", 10, ("TST-001",)),
    "launch.log": ("DMSJOB", "Launch", 20, ("TST-002", "TST-003")),
    "fh1.log": ("DMSFH", "FH_1", 31, ("TST-101",)),
    "fh2.log": ("DMSFH", "FH_2", 32, ("TST-101",)),
}
# one record: (ts, code, job id, fh_pid or None)
Record = tuple[int, str, int, int | None]
World = Mapping[str, list[Record]]


def catalog() -> Catalog:
    return load_texts({"dmsjob.toml": TOML_JOB, "dmsfh.toml": TOML_FH})


class HandoffDb:
    """A case database filled by hand, one source per file, ids in insertion order."""

    def __init__(self, path: Path, cat: Catalog | None = None, group: int = 1) -> None:
        self.cat = cat or catalog()
        self.group = group
        self.conn = create_case_db(path)
        self.conn.isolation_level = None
        self.conn.execute(
            "INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', '2026-01-01')"
        )
        for t in self.cat.templates():
            self.conn.execute(
                "INSERT INTO template(id, code, family, class, pattern, def_version) VALUES (?,?,?,?,?,'v1')",
                (self.cat.tpl_id(t.code), t.code, t.family, t.cls, t.pattern),
            )
        self.sources: dict[str, int] = {}
        self.next_id = 1

    def source(self, name: str, group: int | None = None) -> int:
        """The source of a file (created with its stream on first use)."""
        key = name if group is None else f"{name}#{group}"
        if key not in self.sources:
            family, process, pid, _codes = FILES[name]
            grp = self.group if group is None else group
            sid = self.conn.execute(
                "INSERT INTO stream(collection_group, family, process, pid, sensitive) VALUES (?,?,?,?,0)",
                (grp, family, process, pid),
            ).lastrowid
            self.sources[key] = self.conn.execute(
                "INSERT INTO source(collection_id, stream_id, member, file_name, envelope, status)"
                " VALUES (1, ?, ?, ?, 'adms', 'ready')",
                (sid, key, name),
            ).lastrowid
        return self.sources[key]

    def add(self, name: str, line_no: int, rec: Record, group: int | None = None, pid: Any = ...) -> int:
        ts, code, job, fh_pid = rec
        src = self.source(name, group)
        eid = self.next_id
        self.next_id += 1
        env_pid = FILES[name][2] if pid is ... else pid
        fields = {"job_id": job}
        if fh_pid is not None:
            fields["fh_pid"] = fh_pid
        self.conn.execute(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, sev, tpl_id, flags,"
            " fields, msg) VALUES (?,?,?,?,1,?,?,1,4,?,0,?,?)",
            (eid, src, line_no, line_no, ts, env_pid, self.cat.tpl_id(code), json.dumps(fields), code),
        )
        self.conn.execute(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES ('job_id', ?, ?)", (str(job), eid)
        )
        return eid

    def sync(self) -> None:
        """Update the sources the way the indexer does after a batch (totals and the growth markers)."""
        self.conn.execute(
            "UPDATE source SET"
            " n_entries = (SELECT count(*) FROM entry e WHERE e.source_id = source.id),"
            " indexed_bytes = (SELECT coalesce(max(byte_off), 0) FROM entry e WHERE e.source_id = source.id),"
            " first_ts = (SELECT min(ts) FROM entry e WHERE e.source_id = source.id),"
            " last_ts = (SELECT max(ts) FROM entry e WHERE e.source_id = source.id)"
        )

    def run(self):
        self.sync()
        return link_pending(self.conn, self.cat)

    def delete_source(self, name: str, group: int | None = None) -> None:
        """Remove the entries of a source the way a reindex does, bumping the group epoch."""
        src = self.source(name, group)
        self.conn.execute(
            "DELETE FROM entry_key WHERE entry_id IN (SELECT id FROM entry WHERE source_id = ?)", (src,)
        )
        self.conn.execute("DELETE FROM entry WHERE source_id = ?", (src,))
        grp = self.group if group is None else group
        key = f"entries_epoch.{grp}"
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(int(row[0]) + 1 if row else 1)),
        )

    def where(self, eid: int) -> tuple[str, int]:
        return self.conn.execute(
            "SELECT s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id WHERE e.id = ?",
            (eid,),
        ).fetchone()

    def links(self, kind: str = "handoff") -> set[tuple[tuple[str, int], tuple[str, int], int]]:
        """The links as ``((member, line_no) of a, (member, line_no) of b, confidence)``: no entry ids."""
        rows = self.conn.execute(
            "SELECT a_entry_id, b_entry_id, confidence FROM link WHERE kind = ?", (kind,)
        ).fetchall()
        return {(self.where(a), self.where(b), conf) for a, b, conf in rows}

    def stats(self, group: int | None = None) -> dict[str, dict[str, int]]:
        return link_stats(self.conn, self.group if group is None else group)

    def close(self) -> None:
        self.conn.close()


def load_world(db: HandoffDb, world: World, prefixes: Mapping[str, int] | None = None, order=None) -> None:
    """Insert the records of every file (the first ``prefixes[file]`` lines only when given), file by file."""
    for name in order or list(world):
        records = world.get(name, [])
        n = len(records) if prefixes is None else prefixes.get(name, len(records))
        for i in range(n):
            db.add(name, i + 1, records[i])


def append_rest(db: HandoffDb, world: World, prefixes: Mapping[str, int], order=None) -> None:
    """Insert the lines after the prefix of every file (the file grew)."""
    for name in order or list(world):
        records = world.get(name, [])
        for i in range(prefixes.get(name, len(records)), len(records)):
            db.add(name, i + 1, records[i])


# --------------------------------------------------------------------------- oracle


def _span(world: World, codes: Iterable[str]) -> tuple[int, int] | None:
    lo = hi = None
    for records in world.values():
        if any(r[1] in codes for r in records):
            for r in records:
                lo = r[0] if lo is None else min(lo, r[0])
                hi = r[0] if hi is None else max(hi, r[0])
    return None if lo is None else (lo, hi)  # type: ignore[return-value]


def _in(span, ts: int) -> bool:
    return span is not None and span[0] <= ts <= span[1]


def oracle(world: World, a_code: str, b_code: str, pid_field: bool, max_dt: int = MAX_DT):
    """Brute force over every pair of records: ``(links, counters)`` with links as in ``HandoffDb.links``.

    A link ``a -> b`` exists if and only if degree(a) = 1 and degree(b) = 1, the degree counting every
    compatible record (same value, a.ts in [b.ts - max_dt, b.ts], no known PID mismatch).
    """
    recs = []  # (member, line_no, ts, code, job, fh_pid, envelope pid)
    for name, records in world.items():
        for i, (ts, code, job, fh) in enumerate(records):
            recs.append((name, i + 1, ts, code, job, fh, FILES[name][2]))
    a_span, b_span = _span(world, {a_code}), _span(world, {b_code})
    a_recs = [r for r in recs if r[3] == a_code]
    b_recs = [r for r in recs if r[3] == b_code]
    counters: dict[str, int] = defaultdict(int)
    compat: dict[tuple, list] = {}  # b -> compatible a records
    for b in b_recs:
        raw = [a for a in a_recs if a[4] == b[4] and b[2] - max_dt <= a[2] <= b[2]]
        cands = [a for a in raw if not (pid_field and a[5] is not None and b[6] is not None and a[5] != b[6])]
        compat[(b[0], b[1])] = cands
        if not raw:
            counters["unmatched" if _in(a_span, b[2]) else "out_of_coverage"] += 1
        elif not cands:
            counters["pid_mismatch"] += 1
    deg_a: dict[tuple, int] = defaultdict(int)
    for cands in compat.values():
        for a in cands:
            deg_a[(a[0], a[1])] += 1
    links = set()
    for b in b_recs:
        cands = compat[(b[0], b[1])]
        if not cands:
            continue
        if len(cands) == 1 and deg_a[(cands[0][0], cands[0][1])] == 1:
            a = cands[0]
            checked = (not pid_field) or (a[5] is not None and b[6] is not None)
            links.add(((a[0], a[1]), (b[0], b[1]), 3 if checked else 2))
        else:
            counters["ambiguous"] += 1
    for a in a_recs:
        if deg_a[(a[0], a[1])] == 0 and _in(b_span, a[2]):
            counters["unanswered"] += 1
    counters["linked"] = len(links)
    return links, {k: v for k, v in counters.items() if v}
