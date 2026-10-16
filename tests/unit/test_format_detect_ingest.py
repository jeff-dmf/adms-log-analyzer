"""Ingestion of logs outside the 1.0 scope with generic envelopes (ROADMAP 9.14).

Synthetic data only. An unknown format must never stop the ingestion: it falls back to ``plain``.
"""

from pathlib import Path

import pytest

from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.store import create_case_db

DMY = "15.10.2026 13:04:{s:02d}.123 [TID=12] [{lv}] - step {s}"
TOOL = "Tool_1234.log"  # PROCESS family (optional scope, envelope hint "adms")


def dmy_log(n: int, start: int = 0) -> bytes:
    lines = []
    for s in range(start, start + n):
        lines.append(DMY.format(s=s, lv="ERROR" if s % 5 == 0 else "DEBUG"))
        if s % 2 == 0:
            lines.append("   at Some.Method()")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


FILES = {
    TOOL: dmy_log(10),
    "Host1_LastShutdownReason.log": b"2026-10-15 13:04:05.123: Service stopped\r\n",
    "ERS.log": b"2026.10.15 13:04:05.123          INFO: Collecting\n  a\n"
    + b"2026.10.15 13:04:06.000   ERROR: x\n",
    "setup.log": "15.10.2026 13:04:05.123 [TID=3] [INFO ] - installer\r\n".encode("utf-16"),
    "random.log": bytes(range(256)) * 8,
    "baddate.log": b"31.02.2026 13:04:05.123 [TID=12] [DEBUG] - not a date\n" * 3,
    "empty.log": b"",
    "notes.txt": b"just text\nmore text\n",
    "Svc_9.log": b"2026-01-02 03:04:05.000 [PID=9][TID=0x1] INFO - adms line\n"
    + b"15.10.2026 13:04:05.123 [TID=12] [DEBUG] - generic-looking line\n" * 5,
}


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def write_folder(root: Path, files: dict[str, bytes]) -> Path:
    for member, data in files.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def sources(conn) -> dict[str, tuple]:
    rows = conn.execute("SELECT member, status, envelope, n_entries FROM source").fetchall()
    return {member: (status, envelope, n) for member, status, envelope, n in rows}


def test_unknown_formats_never_fail_and_generic_ones_are_detected(tmp_path, db):
    root = write_folder(tmp_path / "Log", FILES)
    result = run_ingest(db, root, IngestOptions(scope="all"))
    assert result.sources_failed == 0
    got = sources(db)
    assert got[TOOL] == ("ready", "dmy_tid_level", 10)
    assert got["Host1_LastShutdownReason.log"] == ("ready", "ymd_colon", 1)
    assert got["ERS.log"] == ("ready", "ymd_level_colon", 2)
    assert got["setup.log"] == ("ready", "dmy_tid_level", 1)  # UTF-16, transcoded before detection
    assert got["random.log"][:2] == ("ready", "plain")
    assert got["baddate.log"] == ("ready", "plain", 3)  # impossible dates are not headers
    assert got["notes.txt"] == ("ready", "plain", 2)
    assert got["Svc_9.log"][:2] == ("ready", "adms")  # one built-in header beats five generic ones
    assert got["empty.log"][0] == "ready"
    # generic records carry time and level; continuation lines stay in the record
    tool_id = db.execute("SELECT id FROM source WHERE member=?", (TOOL,)).fetchone()[0]
    rows = db.execute(
        "SELECT ts IS NOT NULL, level, nlines FROM entry WHERE source_id=? ORDER BY byte_off", (tool_id,)
    ).fetchall()
    assert all(has_ts for has_ts, _, _ in rows)
    assert [lv for _, lv, _ in rows][:2] == [3, 5]  # ERROR, DEBUG -> DEBUGLOG
    assert sum(n for _, _, n in rows) == dmy_log(10).count(b"\n")


def test_generic_live_file_appends_like_a_fresh_ingest(tmp_path, db):
    root = write_folder(tmp_path / "Log", {TOOL: dmy_log(10)})
    run_ingest(db, root, IngestOptions(scope="all"))
    with (root / TOOL).open("ab") as fh:
        fh.write(dmy_log(6, start=10))
    result = run_ingest(db, root, IngestOptions(scope="all"))
    assert [d.action for d in result.details] == ["append"]
    fresh = create_case_db(tmp_path / "fresh.sqlite")
    try:
        run_ingest(fresh, root, IngestOptions(scope="all"))
        query = "SELECT line_no, byte_off, nlines, ts, tid, level, msg FROM entry ORDER BY byte_off"
        assert db.execute(query).fetchall() == fresh.execute(query).fetchall()
        assert sources(db)[TOOL] == sources(fresh)[TOOL] == ("ready", "dmy_tid_level", 16)
    finally:
        fresh.close()
    again = run_ingest(db, root, IngestOptions(scope="all"))
    assert [d.action for d in again.details] == ["unchanged"]


def test_single_file_with_a_generic_format(tmp_path, db):
    path = tmp_path / "anything.log"
    path.write_bytes(dmy_log(3))
    result = run_ingest(db, path)
    assert result.sources_failed == 0
    assert sources(db)["anything.log"] == ("ready", "dmy_tid_level", 3)


def test_removed_generic_envelope_reindexes_instead_of_failing(tmp_path, db, monkeypatch):
    from dmslog.ingest import pipeline

    root = write_folder(tmp_path / "Log", {TOOL: dmy_log(10)})
    run_ingest(db, root, IngestOptions(scope="all"))
    with (root / TOOL).open("ab") as fh:
        fh.write(dmy_log(6, start=10))
    # the [[envelope]] the source was indexed with no longer exists in families.toml
    monkeypatch.setattr(pipeline, "is_known_envelope", lambda name: name != "dmy_tid_level")
    result = run_ingest(db, root, IngestOptions(scope="all"))
    assert [d.action for d in result.details] == ["reindex"]


def timed_dmy_log(n: int) -> bytes:
    lines = [f"15.10.2026 13:{i // 60:02d}:{i % 60:02d}.000 [TID=1] [INFO] - item {i}" for i in range(n)]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def test_partial_state_with_a_removed_envelope_is_abandoned_and_indexed_again(tmp_path, db, monkeypatch):
    """Review of PR #196: the completion of a partial open resolved ``state.envelope`` blindly."""
    from dmslog.ingest.partial import FirstPass, load_state, pending_work
    from dmslog.ingest.pipeline import resume_pending
    from dmslog.parse.envelope import FALLBACK_ENVELOPES, parse_ts
    from dmslog.parse.formats import ensure_registered

    member = "dnaApp_zone1/SVC/WfmAdapter_1111.log"  # default scope
    root = write_folder(tmp_path / "Log", {member: timed_dmy_log(240)})
    first = FirstPass(
        ts_from=parse_ts(b"2026-10-15 13:02:00", b"000"),
        ts_to=parse_ts(b"2026-10-15 13:03:00", b"000"),
        defer_rest=True,
    )
    run_ingest(db, root, IngestOptions(first=first, chunk_size=700, batch_bytes=2000))
    [(sid, envelope)] = db.execute("SELECT id, envelope FROM source").fetchall()
    assert envelope == "dmy_tid_level"
    assert load_state(db, sid).envelope == "dmy_tid_level"
    ensure_registered()
    monkeypatch.delitem(FALLBACK_ENVELOPES, "dmy_tid_level")  # gone from families.toml
    resume_pending(db, IngestOptions(chunk_size=700, batch_bytes=2000))
    assert load_state(db, sid) is None
    assert pending_work(db) == []
    assert db.execute("SELECT status, envelope FROM source").fetchone() == ("ready", "plain")
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] == 240
