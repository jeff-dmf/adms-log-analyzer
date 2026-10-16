"""Synthetic cases for the ad hoc extraction tests (ROADMAP 9.8): records given by locator, ids by order."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dmslog.store.db import connect, create_case_db

# collection: (path, variant); source: (collection index, member, rotation, family)
COLLECTIONS = [("C:/ers/A.zip", "enterprise"), ("C:/ers/A_s.zip", "sensitive"), ("D:/logs", None)]
SOURCES = [
    (0, "Log/X.log.1", 1, "AWFM"),
    (0, "Log/X.log.0", 0, "AWFM"),
    (0, "Log/X.log", None, "AWFM"),
    (1, "Log/X.log", None, "AWFM"),  # the same member in the other ERS half
    (2, "Y.log", None, "AONT"),
]


def build(
    path: Path,
    records: list[tuple],
    *,
    source_order: list[int] | None = None,
    collection_order: list[int] | None = None,
    tpl: dict[int, str] | None = None,
) -> sqlite3.Connection:
    """records: (source index, ts, msg, cont) in FILE order per source. Sources and collections are
    inserted in the given orders (other ids, same content); records of each source keep the file order,
    the sources take turns (another interleaving of the ids)."""
    conn = create_case_db(path)
    corder = collection_order or list(range(len(COLLECTIONS)))
    sorder = source_order or list(range(len(SOURCES)))
    cid = {c: k + 1 for k, c in enumerate(corder)}
    sid = {s: k + 1 for k, s in enumerate(sorder)}
    with conn:
        for c in corder:
            p, variant = COLLECTIONS[c]
            kind = "folder" if variant is None else "ers_zip"
            conn.execute(
                "INSERT INTO collection(id, kind, path, ers_variant, ers_group, added_at)"
                " VALUES (?,?,?,?,?,'x')",
                (cid[c], kind, p, variant, 1 if variant else 2),
            )
        families = sorted({s[3] for s in SOURCES})
        for k, fam in enumerate(families, 1):
            conn.execute(
                "INSERT INTO stream(id, collection_group, family, process, pid, sensitive)"
                " VALUES (?,1,?,?,?,0)",
                (k, fam, fam, k),
            )
        for s in sorder:
            c, member, rotation, fam = SOURCES[s]
            conn.execute(
                "INSERT INTO source(id, collection_id, stream_id, member, file_name, rotation, envelope)"
                " VALUES (?,?,?,?,?,?,'adms')",
                (sid[s], cid[c], families.index(fam) + 1, member, member.rsplit("/", 1)[-1], rotation),
            )
        for code_id, code in (tpl or {}).items():
            conn.execute(
                "INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
                " VALUES (?,?,'AWFM','other',4,'x',1,'v')",
                (code_id, code),
            )
        per_source: dict[int, list[tuple]] = {}
        for k, rec in enumerate(records):
            per_source.setdefault(rec[0], []).append((k, rec))
        queues = {s: list(per_source.get(s, [])) for s in sorder}
        line = {s: 0 for s in sorder}
        next_id = 1
        while any(queues.values()):
            for s in sorder:
                if not queues[s]:
                    continue
                k, rec = queues[s].pop(0)
                _, ts, msg, cont, *rest = rec
                tpl_id = rest[0] if rest else 0
                line[s] += 1
                conn.execute(
                    "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, sev, tpl_id, flags, msg)"
                    " VALUES (?,?,?,?,1,?,4,?,?,?)",
                    (next_id, sid[s], line[s], line[s] * 100, ts, tpl_id, 2 if cont is not None else 0, msg),
                )
                if cont is not None:
                    conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?,?)", (next_id, cont))
                next_id += 1
    conn.close()
    return connect(path, readonly=True)


def locator(conn: sqlite3.Connection, entry_id: int) -> tuple[str, str, int]:
    return conn.execute(
        "SELECT c.path, s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
        " JOIN collection c ON c.id = s.collection_id WHERE e.id = ?",
        (entry_id,),
    ).fetchone()
