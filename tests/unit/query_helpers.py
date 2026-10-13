"""Small synthetic case database for filter and query tests (values are fictitious)."""

from __future__ import annotations

import json
import random
from pathlib import Path

from dmslog.store.db import connect, create_case_db

INJECTION_MSG = "user Robert'); DROP TABLE entry;-- said ' OR 1=1 -- and NEAR(x y) *"


def entry_rows(seed: int = 7, n_per_source: int = 120) -> list[dict]:
    """Rows in insertion (file) order: source 3 (plain, no ts), then sources 1 and 2.

    Timestamps repeat and sometimes go back (flag 32), as in real logs.
    """
    rnd = random.Random(seed)
    rows: list[dict] = []
    for k in range(5):
        rows.append(
            dict(
                source_id=3,
                ts=None,
                tid=None,
                pid=None,
                level=None,
                sev=5,
                tpl_id=0,
                flags=0,
                msg=f"plain line {k}",
                cont=None,
                fields=None,
            )
        )
    for source_id, start in ((1, 1_000_000), (2, 1_000_050)):
        ts = start
        for k in range(n_per_source):
            r = rnd.random()
            flags = 0
            if r < 0.25:
                pass  # same ts as the previous record
            elif r < 0.33:
                ts -= rnd.randint(1, 40)  # ts goes back
                flags |= 32
            else:
                ts += rnd.randint(1, 30)
            tid = rnd.choice([None, 0x10, 0x20, 0x30])
            sev = rnd.randint(1, 5)
            cont = None
            if rnd.random() < 0.2:
                cont = f"   at Stack.Frame{k}()\n   at Other.Frame()"
                flags |= 2
            if rnd.random() < 0.1:
                flags |= 1
            word = rnd.choice(["timeout", "connected", "GetIncidents", "SendWorks", "retry", "crew"])
            rows.append(
                dict(
                    source_id=source_id,
                    ts=ts,
                    tid=tid,
                    pid=4000 + source_id,
                    level=rnd.randint(1, 8),
                    sev=sev,
                    tpl_id=rnd.choice([0, 1, 2]),
                    flags=flags,
                    msg=f"{word} message {source_id}-{k} value={rnd.randint(0, 99)}",
                    cont=cont,
                    fields=json.dumps({"op": word, "n": k}),
                )
            )
    rows[20]["msg"] = INJECTION_MSG
    return rows


def build_case(path: Path, rows: list[dict] | None = None) -> list[dict]:
    """Create a case database at ``path`` and return the rows with their ids."""
    rows = entry_rows() if rows is None else rows
    conn = create_case_db(path)
    with conn:
        conn.execute(
            "INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'ers_zip', 'case.zip', '2026-01-01')"
        )
        conn.executemany(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES (?,?,?,?,?,?)",
            [
                (1, 1, "AWFM", "WfmAdapter", 4001, 0),
                (2, 1, "AWFM", "WfmAdapter", 4001, 1),
                (3, 1, "UNKNOWN", None, None, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope)"
            " VALUES (?,1,?,?,?,?)",
            [
                (1, 1, "Log/a.log", "a.log", "adms"),
                (2, 2, "Log/Sensitive/a.log", "a.log", "adms"),
                (3, 3, "Log/x.txt", "x.txt", "plain"),
            ],
        )
        conn.executemany(
            "INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, "TST-001", "AWFM", "other", 4, "x", 1, "v"),
                (2, "TST-002", "AWFM", "other", 5, "y", 0, "v"),
            ],
        )
        for i, row in enumerate(rows, 1):
            row["id"] = i
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev,"
                " tpl_id, flags, fields, msg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    row["source_id"],
                    i,
                    i * 100,
                    1 if row["cont"] is None else 3,
                    row["ts"],
                    row["pid"],
                    row["tid"],
                    row["level"],
                    row["sev"],
                    row["tpl_id"],
                    row["flags"],
                    row["fields"],
                    row["msg"],
                ),
            )
            if row["cont"] is not None:
                conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?,?)", (i, row["cont"]))
        conn.execute(
            "INSERT INTO event(id, stream_id, kind, name, ts_start, status, sev) VALUES "
            "(1, 1, 'operation', 'GetIncidents', 1000000, 'fail', 2), "
            "(2, 1, 'operation', 'SendWorks', 1000100, 'ok', 4)"
        )
        conn.execute("UPDATE entry SET event_id = 1 WHERE id BETWEEN 10 AND 14")
        conn.execute("UPDATE entry SET event_id = 2 WHERE id BETWEEN 15 AND 16")
        for row in rows:
            row["event_id"] = 1 if 10 <= row["id"] <= 14 else 2 if 15 <= row["id"] <= 16 else None
        conn.executemany(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)",
            [("msgid", "m-1", 30), ("msgid", "m-1", 140), ("gid", "g-9", 31)],
        )
        conn.execute(
            "INSERT INTO payload(entry_id, role, direction, peer, operation, fmt, char_off, char_len) "
            "VALUES (40, 'response', 'in', 'WFM', 'SendWorks', 'xml', 0, 10)"
        )
        conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
    conn.close()
    return rows


def open_ro(path: Path):
    return connect(path, readonly=True)


# --- reference evaluator (Python) for the filter semantics --------------------------------------

SOURCE_STREAM = {1: 1, 2: 2, 3: 3}
STREAM_FAMILY = {1: "AWFM", 2: "AWFM", 3: "UNKNOWN"}
STREAM_SENSITIVE = {1: 0, 2: 1, 3: 0}
TPL_CODES = {"TST-001": 1, "TST-002": 2}
KEYS = {30: {("msgid", "m-1")}, 140: {("msgid", "m-1")}, 31: {("gid", "g-9")}}
EVENT_STATUS = {1: "fail", 2: "ok"}
PAYLOADS = {
    40: {
        "role": "response",
        "direction": "in",
        "peer": "WFM",
        "operation": "SendWorks",
        "fmt": "xml",
        "msg_id": None,
        "corr_id": None,
        "root": None,
    }
}
SEV = {"critical": 1, "error": 2, "warning": 3, "info": 4, "noise": 5}
LEVEL = {"fatal": 1, "exit": 2, "error": 3, "always": 4, "debuglog": 5, "warning": 6, "info": 7, "verbose": 8}
FLAGS = {
    "partial": 1,
    "has_cont": 2,
    "ref_sensitive": 4,
    "startup": 8,
    "elevated_level": 16,
    "ts_out_of_order": 32,
    "has_payload": 64,
}
_CMP = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}


def _lst(v):
    return v if isinstance(v, list) else [v]


def _ordered(value, arg, names):
    def conv(v):
        return names[v.lower()] if isinstance(v, str) else v

    if value is None:
        return False
    if isinstance(arg, dict):
        return all(_CMP[op](value, conv(v)) for op, v in arg.items())
    return value in [conv(v) for v in _lst(arg)]


def py_match(tree, row) -> bool:
    """Reference semantics of a filter tree on one row (no FTS)."""
    if tree is None or tree == {}:
        return True
    ((op, arg),) = tree.items()
    stream = SOURCE_STREAM[row["source_id"]]
    if op == "and":
        return all(py_match(t, row) for t in arg)
    if op == "or":
        return any(py_match(t, row) for t in arg)
    if op == "not":
        return not py_match(arg, row)
    if op in ("source", "tid", "pid", "event"):
        col = {"source": "source_id", "event": "event_id"}.get(op, op)
        return row[col] is not None and row[col] in _lst(arg)
    if op == "stream":
        return stream in _lst(arg)
    if op == "family":
        return STREAM_FAMILY[stream] in _lst(arg)
    if op == "sensitive":
        return STREAM_SENSITIVE[stream] == int(arg)
    if op == "sev":
        return _ordered(row["sev"], arg, SEV)
    if op == "level":
        return _ordered(row["level"], arg, LEVEL)
    if op == "ts":
        return _ordered(row["ts"], arg, {})
    if op == "tpl":
        ids = {TPL_CODES.get(v, -1) if isinstance(v, str) else v for v in _lst(arg)}
        return row["tpl_id"] in ids
    if op == "text":
        ((kind, value),) = arg.items()
        if kind == "substr":
            return value in row["msg"] or (row["cont"] is not None and value in row["cont"])
        raise ValueError("fts has no reference evaluator")
    if op == "key":
        keys = KEYS.get(row["id"], set())
        if "eq" in arg:
            return (arg["type"], arg["eq"]) in keys
        if "in" in arg:
            return any((arg["type"], v) in keys for v in arg["in"])
        return any(t == arg["type"] for t, _ in keys)
    if op == "event_status":
        return row["event_id"] is not None and EVENT_STATUS.get(row["event_id"]) in _lst(arg)
    if op == "flags":
        mask = 0
        for name in _lst(arg):
            mask |= FLAGS[name]
        return row["flags"] & mask == mask
    if op == "field":
        fields = json.loads(row["fields"]) if row["fields"] else {}
        value = fields.get(arg["name"])
        want = arg["eq"]
        if value is None or want is None:
            return value is None and want is None
        if isinstance(value, str) != isinstance(want, str):
            return False  # json_extract keeps the JSON type: "5" is not 5
        return value == want
    if op == "payload":
        p = PAYLOADS.get(row["id"])
        return p is not None and all(p[k] in _lst(v) for k, v in arg.items())
    raise ValueError(op)


def order_key(row):
    return (row["ts"] is not None, row["ts"] or 0, row["id"])
