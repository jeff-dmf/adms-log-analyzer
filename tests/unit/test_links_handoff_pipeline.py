"""Handoff links through the real ingest: a log folder that grows in steps equals one ingested at once."""

from __future__ import annotations

import test_pipeline as tp

from dmslog.derive.links import link_stats
from dmslog.ingest.pipeline import IngestOptions, run_ingest

mf = tp.mf
APP = "dnaApp_zone1/DMS_REALTIME"
SCHED = f"{APP}/DMS_JobScheduler_10.log"
LAUNCH = f"{APP}/DMS_JobLauncher_20.log"
FH = f"{APP}/DMS_FunctionHost_7.log"
SCHED_PID, LAUNCH_PID, FH_PID = 10, 20, 4242
LAUNCHER_ID = "00000000-0000-4000-8000-000000000001"


def sched_lines(jobs):
    return [
        mf.header(
            t,
            "INFO",
            f"Sending JobID: {j} Function: Calc to Job Launcher ID: {LAUNCHER_ID}. Channel index: 1",
            pid=SCHED_PID,
        )
        for t, j in jobs
    ]


def launch_lines(jobs):
    out = []
    for t, j in jobs:
        out.append(mf.header(t, "INFO", f"New job received. JobID: {j} Function: Calc", pid=LAUNCH_PID))
        out.append(
            mf.header(
                t + 5,
                "INFO",
                f"[Launcher] [ExecuteJob] Sending Execute command to FH ProcessID: {FH_PID} | SlotID: 1"
                f" | JobID: {j} | Function: Calc",
                pid=LAUNCH_PID,
            )
        )
    return out


def fh_lines(jobs):
    return [
        mf.header(
            t + 8,
            "INFO",
            f"New job received for execution. JobID: {j}. Size of the real-time sequence: 3",
            pid=FH_PID,
        )
        for t, j in jobs
    ]


JOBS = [(1000 + 100 * i, 5000 + i) for i in range(6)]


def files(n_sched, n_launch, n_fh):
    return {
        SCHED: mf.render(sched_lines(JOBS[:n_sched])),
        LAUNCH: mf.render(launch_lines(JOBS[:n_launch])),
        FH: mf.render(fh_lines(JOBS[:n_fh])),
    }


def snapshot(conn):
    rows = conn.execute(
        "SELECT sa.member, a.line_no, sb.member, b.line_no, l.confidence FROM link l"
        " JOIN entry a ON a.id = l.a_entry_id JOIN source sa ON sa.id = a.source_id"
        " JOIN entry b ON b.id = l.b_entry_id JOIN source sb ON sb.id = b.source_id"
        " WHERE l.kind = 'handoff' ORDER BY 1, 2, 3, 4"
    ).fetchall()
    group = conn.execute("SELECT min(collection_group) FROM stream").fetchone()[0]
    return rows, link_stats(conn, group)


def test_a_growing_folder_gives_the_links_of_an_ingest_at_once(tmp_path):
    whole = tp.fresh_db(tmp_path, "whole")
    root = tp.write_folder(tmp_path / "whole_log", files(6, 6, 6))
    run_ingest(whole, root, IngestOptions())
    want = snapshot(whole)
    rows, stats = want
    assert len(rows) == 12 and all(r[4] == 3 for r in rows)  # 6 Scheduler->Launcher, 6 Launcher->FH
    assert sum(c["linked"] for k, c in stats.items() if "handoff" in k) == 12

    grown = tp.fresh_db(tmp_path, "grown")
    root2 = tmp_path / "grown_log"
    for step in [(2, 1, 0), (2, 3, 1), (4, 3, 5), (6, 6, 6)]:  # the FH log runs ahead of the Launcher once
        tp.write_folder(root2, files(*step))
        run_ingest(grown, root2, IngestOptions())
    assert snapshot(grown) == want
    whole.close()
    grown.close()
