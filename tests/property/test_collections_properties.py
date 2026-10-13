"""Property: removing a collection group gives the case that never had it (U21, ARQUITETURA 9.3).

Random cases of two or three installation folders (each a collection group with a normal and a sensitive AVL
log of the same process, random numbers of paired records, unknown records and shared plates) are indexed
into one case; removing one group must leave exactly the content (records, keys, links, events, buckets,
unknown signatures, configuration) of a case built from the other folders only, compared without ids, and no
row of any table may point at something removed.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.ingest.pipeline import run_ingest  # noqa: E402
from dmslog.store import create_case_db  # noqa: E402
from dmslog.store.writer import IndexWriter  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
if "make_fixture" in sys.modules:
    mf = sys.modules["make_fixture"]
else:
    _spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
    mf = importlib.util.module_from_spec(_spec)
    sys.modules["make_fixture"] = mf
    _spec.loader.exec_module(mf)

NORMAL = "Position is updated for vehicle(s). For more details check sensitive log."

folder_spec = st.tuples(
    st.integers(0, 4),  # paired records
    st.integers(0, 3),  # unknown records
    st.integers(0, 3),  # first plate (plates overlap between folders)
)


def write_folder(root: Path, index: int, spec: tuple[int, int, int]) -> Path:
    pairs, unknown, first = spec
    normal = [mf.header(500, "ALWAYS", "Debug level changed from DebugLog (3) to Info (5).", pid=5000)]
    sensitive = []
    for i in range(pairs):
        ts = 1000 + i * 1000
        normal.append(mf.header(ts, "DEBUGLOG", NORMAL, pid=5000, tid=16))
        text = f"Position is updated for the following vehicle(s): ZZZ0A{first + i:02d}."
        sensitive.append(mf.header(ts, "DEBUGLOG", text, pid=5000, tid=16))
    for j in range(unknown):
        normal.append(mf.header(9000 + j, "INFO", f"[Alpha] Heartbeat folder {index} item {j} ok.", pid=5000))
    base = root / f"Log{index}" / "dnaApp_zone1" / "DMS_INTEGRATION"
    base.mkdir(parents=True)
    (base / "AVLAdapter_5000.log").write_bytes(mf.render(normal))
    if sensitive:
        (base / "AVLAdapter_5000.sensitive.log").write_bytes(mf.render(sensitive))
    return root / f"Log{index}"


def content(conn: sqlite3.Connection) -> dict:
    """Everything of the case by locator (collection path, member, line), never by id."""
    loc = (
        "(SELECT c.path || '|' || s.member || '|' || e.line_no FROM entry e"
        " JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id WHERE e.id = {})"
    )
    return {
        "entries": conn.execute(
            "SELECT c.path, s.member, e.line_no, e.ts, e.sev, e.flags, e.msg, t.code FROM entry e"
            " JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id"
            " LEFT JOIN template t ON t.id = e.tpl_id ORDER BY 1, 2, 3"
        ).fetchall(),
        "keys": sorted(
            conn.execute(
                f"SELECT k.key_type, k.value, {loc.format('k.entry_id')} FROM entry_key k"
            ).fetchall()
        ),
        "links": sorted(
            conn.execute(
                f"SELECT l.kind, l.confidence, {loc.format('l.a_entry_id')}, {loc.format('l.b_entry_id')}"
                " FROM link l"
            ).fetchall()
        ),
        "events": sorted(
            conn.execute(
                f"SELECT ev.kind, ev.name, ev.status, ev.ts_start, {loc.format('ev.start_entry_id')}"
                " FROM event ev"
            ).fetchall()
        ),
        "buckets": sorted(
            conn.execute(
                "SELECT c.path, s.member, b.minute, b.sev, b.n FROM bucket_sev b"
                " JOIN source s ON s.id = b.source_id JOIN collection c ON c.id = s.collection_id"
            ).fetchall()
        ),
        "unknown": sorted(conn.execute("SELECT family, signature, n FROM unknown_sig").fetchall()),
        "streams": sorted(conn.execute("SELECT family, process, pid, sensitive FROM stream").fetchall()),
        "fts": conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0],
    }


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(specs=st.lists(folder_spec, min_size=2, max_size=3), victim=st.integers(0, 2))
def test_removing_a_group_equals_never_adding_it(specs, victim):
    victim %= len(specs)
    work = Path(tempfile.mkdtemp(prefix="dmslog-groups-"))
    try:
        folders = [write_folder(work / "in", i, spec) for i, spec in enumerate(specs)]
        full = create_case_db(work / "full" / "case.sqlite")
        reference = create_case_db(work / "ref" / "case.sqlite")
        try:
            for i, folder in enumerate(folders):
                run_ingest(full, folder)
                if i != victim:
                    run_ingest(reference, folder)
            path = str(folders[victim].resolve())
            group = full.execute("SELECT ers_group FROM collection WHERE path = ?", (path,)).fetchone()[0]
            assert IndexWriter(full).remove_group(group) is not None
            assert content(full) == content(reference)
            full.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
            dangling = full.execute(
                "SELECT (SELECT count(*) FROM link WHERE a_entry_id NOT IN (SELECT id FROM entry)"
                " OR b_entry_id NOT IN (SELECT id FROM entry))"
                " + (SELECT count(*) FROM entry_key WHERE entry_id NOT IN (SELECT id FROM entry))"
                " + (SELECT count(*) FROM event WHERE stream_id NOT IN (SELECT id FROM stream))"
                " + (SELECT count(*) FROM unknown_sig WHERE first_entry_id NOT IN (SELECT id FROM entry))"
                " + (SELECT count(*) FROM meta WHERE key IN (?, ?))",
                (f"correlate.{group}", f"entries_epoch.{group}"),
            ).fetchone()[0]
            assert dangling == 0
            # the remaining groups are settled: indexing them again writes nothing
            changes = full.total_changes
            for i, folder in enumerate(folders):
                if i != victim:
                    run_ingest(full, folder)
            assert full.total_changes == changes
        finally:
            full.close()
            reference.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
