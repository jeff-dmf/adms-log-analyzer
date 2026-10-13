"""Synthetic ERS captures of two servers, each with an Enterprise and a Sensitive zip (U21 tests).

Everything is invented (GUIDs with the synthetic prefix, zones, plates); built with the helpers of
``tools/make_fixture.py``. Both servers log the SAME process id, thread and times on purpose: a correlation
that crossed collections would pair records of different servers.

Per capture: ``AVLAdapter_5000.log`` (AVL-002 "details in the sensitive log") and its ``.sensitive.log``
(AVL-S002 with the plates, a ``vehicle`` key) at the same ts and TID, a level change (a ``level_window``
event) and a few records without a template.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if "make_fixture" in sys.modules:
    mf = sys.modules["make_fixture"]
else:
    _spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
    mf = importlib.util.module_from_spec(_spec)
    sys.modules["make_fixture"] = mf
    _spec.loader.exec_module(mf)

PID = 5000
TID = 0x10
PAIRS = 6  # AVL-002 / AVL-S002 pairs per capture
RECORDS = 3 * PAIRS + 1  # records of a capture: pairs, heartbeats and the level change
SERVERS = {
    # name: (guid, capture stamp, zone, first plate number)
    "A": (mf.synth_guid(11), "20300115103000", "zonea", 0),
    "B": (mf.synth_guid(12), "20300115113000", "zoneb", 3),  # plates 3..5 are shared with A
}
NORMAL = "Position is updated for vehicle(s). For more details check sensitive log."


def plate(n: int) -> str:
    return f"ZZZ0A{n:02d}"


def _member(zone: str, name: str) -> str:
    return f"{mf.LOG_ROOT}/dnaApp_{zone}/DMS_INTEGRATION/{name}"


def capture_files(server: str, kind: str) -> dict[str, bytes]:
    _guid, _stamp, zone, first = SERVERS[server]
    lines = []
    if kind == "enterprise":  # a level window: every capture derives at least one event
        lines.append(mf.header(500, "ALWAYS", "Debug level changed from DebugLog (3) to Info (5).", pid=PID))
    for i in range(PAIRS):
        ts = 1000 + i * 1000
        if kind == "enterprise":
            lines.append(mf.header(ts, "DEBUGLOG", NORMAL, pid=PID, tid=TID))
            lines.append(
                mf.header(ts + 300, "INFO", f"[Alpha] Heartbeat {server} {i} ok.", pid=PID, tid=TID + 1)
            )
        else:
            text = f"Position is updated for the following vehicle(s): {plate(first + i)}."
            lines.append(mf.header(ts, "DEBUGLOG", text, pid=PID, tid=TID))
    suffix = ".sensitive.log" if kind == "sensitive" else ".log"
    return {_member(zone, f"AVLAdapter_{PID}{suffix}"): mf.render(lines, eol="\r\n", bom=True)}


def build_capture(dest: Path, server: str, kind: str) -> Path:
    """One zip of a capture (``kind`` enterprise or sensitive) in ``dest``; returns its path."""
    guid, stamp, _zone, _first = SERVERS[server]
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / mf.ers_file_name(kind, guid, stamp, f"srv{server.lower()}")
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in sorted(capture_files(server, kind).items()):
            mf._zip_write(zf, name, data)
    return path


def build_all(dest: Path) -> dict[tuple[str, str], Path]:
    """``{(server, kind): path}`` for both servers and both halves."""
    return {
        (server, kind): build_capture(dest / f"{server}-{kind}", server, kind)
        for server in SERVERS
        for kind in ("enterprise", "sensitive")
    }


def group_of(conn, server: str) -> int:
    guid = SERVERS[server][0]
    return conn.execute("SELECT DISTINCT ers_group FROM collection WHERE ers_guid = ?", (guid,)).fetchone()[0]


def links_by_group(conn) -> list[tuple[int, int, str]]:
    """``(group of a, group of b, kind)`` of every link."""
    return conn.execute(
        "SELECT ca.ers_group, cb.ers_group, l.kind FROM link l"
        " JOIN entry ea ON ea.id = l.a_entry_id JOIN source sa ON sa.id = ea.source_id"
        " JOIN collection ca ON ca.id = sa.collection_id"
        " JOIN entry eb ON eb.id = l.b_entry_id JOIN source sb ON sb.id = eb.source_id"
        " JOIN collection cb ON cb.id = sb.collection_id ORDER BY 1, 2, 3"
    ).fetchall()


def group_snapshot(conn, group: int) -> dict:
    """Everything of one collection group, with its ids (what must not change while another group moves)."""
    in_group = "SELECT s.id FROM source s JOIN collection c ON c.id = s.collection_id WHERE c.ers_group = ?"
    streams = "SELECT id FROM stream WHERE collection_group = ?"
    meta = conn.execute(
        "SELECT key, value FROM meta WHERE key IN (?, ?, ?, ?) OR key >= 'derive.' AND key < 'derive/'",
        (f"correlate.{group}", f"correlate.links.{group}", f"payloads.{group}", f"entries_epoch.{group}"),
    ).fetchall()
    stream_ids = {r[0] for r in conn.execute(streams, (group,))}
    return {
        "entries": conn.execute(
            f"SELECT id, source_id, line_no, ts, tid, sev, tpl_id, event_id, flags, msg FROM entry"
            f" WHERE source_id IN ({in_group}) ORDER BY id",
            (group,),
        ).fetchall(),
        "keys": conn.execute(
            f"SELECT key_type, value, entry_id FROM entry_key WHERE entry_id IN"
            f" (SELECT id FROM entry WHERE source_id IN ({in_group})) ORDER BY 1, 2, 3",
            (group,),
        ).fetchall(),
        "events": conn.execute(
            f"SELECT id, stream_id, kind, name, ts_start, status FROM event WHERE stream_id IN ({streams})"
            " ORDER BY id",
            (group,),
        ).fetchall(),
        "links": conn.execute(
            f"SELECT a_entry_id, b_entry_id, kind, confidence FROM link WHERE a_entry_id IN"
            f" (SELECT id FROM entry WHERE source_id IN ({in_group})) ORDER BY 1, 2, 3",
            (group,),
        ).fetchall(),
        "meta": sorted(
            (k, v) for k, v in meta if not k.startswith("derive.") or int(k.split(".")[1]) in stream_ids
        ),
        "buckets": conn.execute(
            f"SELECT source_id, minute, sev, tpl_id, n FROM bucket WHERE source_id IN ({in_group})"
            " ORDER BY 1, 2, 3, 4",
            (group,),
        ).fetchall(),
    }


def assert_no_orphans(conn) -> None:
    """No row of any table points at a removed collection, source, stream, entry or event."""
    checks = {
        "source": "SELECT count(*) FROM source WHERE collection_id NOT IN (SELECT id FROM collection)",
        "stream": "SELECT count(*) FROM stream"
        " WHERE collection_group NOT IN (SELECT ers_group FROM collection)",
        "entry": "SELECT count(*) FROM entry WHERE source_id NOT IN (SELECT id FROM source)",
        "entry_body": "SELECT count(*) FROM entry_body WHERE entry_id NOT IN (SELECT id FROM entry)",
        "entry_key": "SELECT count(*) FROM entry_key WHERE entry_id NOT IN (SELECT id FROM entry)",
        "link_a": "SELECT count(*) FROM link WHERE a_entry_id NOT IN (SELECT id FROM entry)",
        "link_b": "SELECT count(*) FROM link WHERE b_entry_id NOT IN (SELECT id FROM entry)",
        "payload": "SELECT count(*) FROM payload WHERE entry_id NOT IN (SELECT id FROM entry)",
        "event": "SELECT count(*) FROM event WHERE stream_id NOT IN (SELECT id FROM stream)",
        "entry_event": "SELECT count(*) FROM entry WHERE event_id IS NOT NULL"
        " AND event_id NOT IN (SELECT id FROM event)",
        "stat_sample": "SELECT count(*) FROM stat_sample WHERE stream_id NOT IN (SELECT id FROM stream)",
        "config_observed": "SELECT count(*) FROM config_observed"
        " WHERE stream_id NOT IN (SELECT id FROM stream)",
        "config_param": "SELECT count(*) FROM config_param"
        " WHERE collection_group NOT IN (SELECT ers_group FROM collection)",
        "bucket": "SELECT count(*) FROM bucket WHERE source_id NOT IN (SELECT id FROM source)",
        "bucket_sev": "SELECT count(*) FROM bucket_sev WHERE source_id NOT IN (SELECT id FROM source)",
        "bookmark": "SELECT count(*) FROM bookmark WHERE collection_id NOT IN (SELECT id FROM collection)",
        "unknown_sig": "SELECT count(*) FROM unknown_sig WHERE first_entry_id NOT IN (SELECT id FROM entry)",
    }
    bad = {name: n for name, sql in checks.items() if (n := conn.execute(sql).fetchone()[0])}
    assert bad == {}
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
    n_entries = conn.execute("SELECT count(*) FROM entry").fetchone()[0]
    assert conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0] == n_entries
