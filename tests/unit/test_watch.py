"""ingest/watch.py: change detection and incremental ingestion by polling (cycles are driven by hand)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
import test_derive_engine as tde
import test_pipeline as tp
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dmslog import cli
from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.ingest.watch import MAX_BACKOFF_S, CycleReport, Watcher, backoff_delay
from dmslog.server.api import Api, watch_payload

WFM = tp.WFM
WFM_ROTATED = tp.WFM_ROTATED
SECOND = f"{tp.LOGDIR}/WfmAdapter_2222.log"


def options() -> IngestOptions:
    return IngestOptions(catalog=tde.catalog())


def full_snapshot(conn) -> dict:
    """Entries, buckets, sources, derived events, links and payloads (ids excluded)."""
    events, owner = tde.snapshot(conn)
    tables = ("link", "payload", "event")
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    return {"rows": tp.snapshot(conn), "events": events, "owner": owner, "counts": counts}


def clean(tmp_path: Path, root: Path, name: str) -> dict:
    conn = tp.fresh_db(tmp_path, name)
    try:
        run_ingest(conn, root, options())
        return full_snapshot(conn)
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    conn = tp.fresh_db(tmp_path, "watched")
    yield conn
    conn.close()


def make_watcher(conn, root, **kw) -> Watcher:
    return Watcher(conn, root, options(), **kw)


def chunks(data: bytes, cuts: list[int]) -> list[bytes]:
    points = sorted({min(max(c, 0), len(data)) for c in cuts} | {len(data)})
    out, last = [], 0
    for point in points:
        if point > last:
            out.append(data[last:point])
            last = point
    return out


# --------------------------------------------------------------------------- equality with one-shot


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(actions=tde.ACTIONS, cuts=st.lists(st.integers(0, 4000), max_size=12))
def test_a_file_that_grows_in_random_chunks_ends_equal_to_one_shot(tmp_path_factory, actions, cuts):
    data = tde.render(tde.build_lines(actions))
    base = tmp_path_factory.mktemp("watch")
    root = base / "Log"
    target = root / WFM
    target.parent.mkdir(parents=True)
    conn = tp.fresh_db(base, "w")
    try:
        watcher = make_watcher(conn, root)
        written = 0
        for piece in chunks(data, cuts):
            with open(target, "ab") as handle:
                handle.write(piece)
            written += len(piece)
            report = watcher.cycle()
            assert report.ran  # the size changed
        assert written == len(data)
        assert not watcher.cycle().ran  # nothing new
        assert full_snapshot(conn) == clean(base, root, "one-shot")
        tp.assert_consistent(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- rotation, truncation, new files


def open_handles_under(root: Path) -> list[str]:
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return []
    found = []
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if str(root) in target:
            found.append(target)
    return found


def test_rotation_keeps_every_record_and_leaves_no_handle(tmp_path, db):
    lines = tde.build_lines([(1, "begin")] * 6 + [(1, "end")] * 3)
    root = tp.write_folder(tmp_path / "Log", {WFM: tde.render(lines[:5])})
    watcher = make_watcher(db, root)
    assert watcher.cycle().ran
    assert open_handles_under(root) == []
    # Rotation on any OS: the rename must work right after a cycle (a kept handle would block it on Windows).
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tde.render(lines[5:]))
    report = watcher.cycle()
    assert report.ran
    assert open_handles_under(root) == []
    assert {d.member: d.action for d in report.news} == {WFM: "new"}
    assert full_snapshot(db) == clean(tmp_path, root, "one-shot")
    # A second rotation shifts the chain; removing the oldest file right after a cycle also works.
    (root / WFM_ROTATED).rename(root / f"{tp.LOGDIR}/WfmAdapter_1111.log.1")
    (root / WFM).rename(root / WFM_ROTATED)
    (root / WFM).write_bytes(tde.render(tde.build_lines([(2, "begin"), (2, "end")])))
    assert watcher.cycle().ran
    (root / f"{tp.LOGDIR}/WfmAdapter_1111.log.1").unlink()
    assert full_snapshot(db)["rows"]["entries"]  # records of the removed file stay in the case
    tp.assert_consistent(db)


def test_the_watcher_never_holds_a_log_open_between_cycles(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(30)})
    watcher = make_watcher(db, root)
    for step in range(3):
        watcher.cycle()
        moved = root / f"{WFM}.moved"
        (root / WFM).rename(moved)  # fails on Windows while a handle is open
        moved.rename(root / WFM)
        (root / WFM).unlink()
        (root / WFM).write_bytes(tp.make_log(30 + step, start=1000 * (step + 1)))
    assert open_handles_under(root) == []


def test_truncation_reindexes_the_file(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(150)})
    watcher = make_watcher(db, root)
    watcher.cycle()
    (root / WFM).write_bytes(tp.make_log(40, start=500_000))  # rewritten and shorter
    report = watcher.cycle()
    assert {d.member: d.action for d in report.news} == {WFM: "reindex"}
    assert full_snapshot(db) == clean(tmp_path, root, "one-shot")
    tp.assert_consistent(db)


def test_a_new_file_is_picked_up(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    watcher = make_watcher(db, root)
    watcher.cycle()
    assert not watcher.cycle().ran
    (root / SECOND).write_bytes(tp.make_log(25, pid=2222, start=7000))
    report = watcher.cycle()
    assert report.ran and {d.member: d.action for d in report.news} == {SECOND: "new"}
    assert report.result.records == 25
    assert full_snapshot(db) == clean(tmp_path, root, "one-shot")


def test_a_new_folder_with_a_new_service_is_picked_up(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    watcher = make_watcher(db, root, rescan_every=10_000)
    watcher.cycle()
    time.sleep(0.05)
    other = "dnaApp_zone1/OTHER/WfmAdapter_3333.log"
    tp.write_folder(root, {other: tp.make_log(12, pid=3333)})
    ran = [watcher.cycle().ran for _ in range(3)]  # the folder is listed again by its changed mtime
    assert any(ran)
    assert db.execute("SELECT count(*) FROM source WHERE member=?", (other,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- idle cycles


def test_a_cycle_without_changes_writes_nothing(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(30)})
    watcher = make_watcher(db, root, rescan_every=2)  # also covers the periodic listing
    watcher.cycle()
    time.sleep(3.2)  # recently touched folders are listed again until they settle
    watcher.cycle()
    before = db.total_changes
    for _ in range(6):
        report = watcher.cycle()
        assert not report.ran and not report.has_news
    assert db.total_changes == before


def test_a_failed_ingestion_stays_pending(tmp_path, db, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    watcher = make_watcher(db, root)
    import dmslog.ingest.watch as watch_module

    real = watch_module.run_ingest
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(watch_module, "run_ingest", flaky)
    with pytest.raises(OSError):
        watcher.cycle()
    assert watcher.cycle().ran  # retried: the state only advances after a successful ingestion
    assert full_snapshot(db) == clean(tmp_path, root, "one-shot")


# --------------------------------------------------------------------------- loop, backoff, CLI


def test_backoff_grows_and_is_capped():
    assert backoff_delay(2.0, 0) == 2.0
    assert backoff_delay(2.0, 1) == 4.0
    assert backoff_delay(2.0, 3) == 16.0
    assert backoff_delay(2.0, 20) == MAX_BACKOFF_S


def test_run_reports_news_and_retries_errors_with_a_backoff(tmp_path, db, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    watcher = make_watcher(db, root)
    stop = threading.Event()
    reports: list[CycleReport] = []
    errors: list[tuple[Exception, float]] = []
    real_cycle = watcher.cycle
    state = {"n": 0}

    def cycle(progress=None):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("gone")
        return real_cycle(progress)

    monkeypatch.setattr(watcher, "cycle", cycle)
    watcher.run(
        stop,
        0.1,
        on_report=lambda r: (reports.append(r), stop.set()),
        on_error=lambda exc, delay: errors.append((exc, delay)),
    )
    assert len(errors) == 1 and errors[0][1] == pytest.approx(0.2)
    assert len(reports) == 1 and reports[0].result.records > 0


def test_the_cycle_line_names_sources_and_derived_counts(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tde.render(tde.basic_lines())})
    report = make_watcher(db, root).cycle()
    line = cli.format_cycle_line(report, "12:00:02")
    assert line.startswith("12:00:02 +") and f"records in {WFM}" in line
    assert "events +3" in line and "links +" in line and "payloads +" in line


def test_watch_command_rejects_a_file_and_bad_intervals(tmp_path, capsys):
    log = tmp_path / "a.log"
    log.write_bytes(b"x\n")
    assert cli.main(["--data-dir", str(tmp_path / "d"), "watch", str(log), "--case", "c"]) == cli.EXIT_FAILED
    assert "folder" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["watch", str(tmp_path), "--case", "c", "--interval", "0"])
    with pytest.raises(SystemExit):
        cli.main(["watch", str(tmp_path)])  # --case is required


def test_watch_command_stops_cleanly_on_ctrl_c(tmp_path, capsys, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(15)})
    import dmslog.ingest.watch as watch_module

    def interrupted(self, stop, interval=2.0, **kw):
        report = self.cycle()
        kw["on_report"](report)
        raise KeyboardInterrupt

    monkeypatch.setattr(watch_module.Watcher, "run", interrupted)
    code = cli.main(
        ["--data-dir", str(tmp_path / "d"), "watch", str(root), "--case", "c1", "--interval", "0.5"]
    )
    out = capsys.readouterr().out
    assert code == 0 and "records in" in out and "Stopped watching." in out


# --------------------------------------------------------------------------- server side


def wait_for(hub, name: str, after: int = 0, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    seq = after
    while time.monotonic() < deadline:
        events, seq = hub.wait(seq, 0.2)
        for event in events:
            if event.name == name:
                return event, seq
    raise AssertionError(f"no {name!r} event")


def test_serve_watch_publishes_one_watch_event_per_change(tmp_path):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    api = Api(tmp_path / "data")
    try:
        case = api.jobs.start_watch("followed", root, "default", 0.05)
        hub = api.jobs.hub(case)
        first, seq = wait_for(hub, "watch")
        assert first.data["records"] > 0 and first.data["sources"][0]["source"] == WFM
        with open(root / WFM, "ab") as handle:
            handle.write(tp.make_log(5, start=900_000))
        second, _ = wait_for(hub, "watch", seq)
        assert second.data["cycle"] > first.data["cycle"] and second.data["records"] > 0
        assert watch_payload  # public helper used by the route layer
    finally:
        api.shutdown()
    with api.store.reader(case) as conn:
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] > 20


# --------------------------------------------------------------------------- review fixes


def _failing_once(monkeypatch):
    import dmslog.ingest.watch as watch_module

    real = watch_module.run_ingest
    calls = []

    def partial(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            result.sources_failed = 1  # run_ingest reports a failed source without raising
        return result

    monkeypatch.setattr(watch_module, "run_ingest", partial)
    return calls


def test_a_partial_failure_keeps_the_change_pending_even_after_a_relist(tmp_path, db, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    watcher = make_watcher(db, root, rescan_every=1)  # every poll lists the folder again
    _failing_once(monkeypatch)
    first = watcher.cycle()
    assert first.ran and first.failed == 1
    assert watcher.poll()  # still pending
    assert watcher.cycle().ran and not watcher.cycle().ran  # retried once, then settled


def test_run_applies_the_backoff_to_partial_failures(tmp_path, db, monkeypatch):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    watcher = make_watcher(db, root)
    _failing_once(monkeypatch)
    stop = threading.Event()
    errors = []
    reports = []
    real_cycle = watcher.cycle

    def cycle(progress=None):
        report = real_cycle(progress)
        reports.append(report.failed)
        if len(reports) == 2:  # the retry ran
            stop.set()
        return report

    monkeypatch.setattr(watcher, "cycle", cycle)
    watcher.run(stop, 0.1, on_error=lambda exc, delay: errors.append((type(exc).__name__, delay)))
    assert errors == [("PartialFailureError", pytest.approx(0.2))]
    assert reports == [1, 0]  # the failed cycle, then the retry that settled it


def test_a_replacement_with_the_same_size_and_mtime_is_noticed(tmp_path, db):
    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    target = root / WFM
    watcher = make_watcher(db, root, rescan_every=1)
    watcher.cycle()
    assert not watcher.cycle().ran
    stat = target.stat()
    other = target.with_name("replacement.tmp")
    other.write_bytes(b"x" * stat.st_size)  # same size, different content
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(other, target)  # a new file identity (inode / file id) under the same name
    assert target.stat().st_size == stat.st_size and target.stat().st_mtime_ns == stat.st_mtime_ns
    assert watcher.cycle().ran


def test_watch_event_survives_the_next_job_and_reaches_late_subscribers():
    from dmslog.server.sse import ProgressHub

    hub = ProgressHub()
    assert hub.try_begin("j1")
    hub.publish("watch", {"records": 3}, coalesce=True)
    hub.finish({"status": "ok"})
    assert hub.try_begin("j2")  # an idle follow cycle begins a job: the history is cleared
    hub.finish({"status": "ok"})
    events, _ = hub.wait(0, 0.01)
    assert [e.name for e in events] == ["watch", "done"]
    later, _ = hub.wait(events[0].seq, 0.01)
    assert [e.name for e in later] == ["done"]  # a subscriber that has seen it does not get it twice


def test_serve_watch_reports_a_partial_failure_and_retries(tmp_path, monkeypatch):
    import dmslog.ingest.watch as watch_module

    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(20)})
    real = watch_module.run_ingest
    calls = []

    def partial(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            result.sources_failed = 1
        return result

    monkeypatch.setattr(watch_module, "run_ingest", partial)
    api = Api(tmp_path / "data")
    try:
        case = api.jobs.start_watch("partial", root, "default", 0.05)
        hub = api.jobs.hub(case)
        first, seq = wait_for(hub, "watch")
        assert first.data["status"] == "partial" and first.data["failed"] == 1
        done, _ = wait_for(hub, "done")
        assert done.data["status"] == "error" and done.data["code"] == "partial_failure"
        assert len(calls) >= 2 or wait_for(hub, "done", seq)  # the change is retried
    finally:
        api.shutdown()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_stops_watch_cleanly(tmp_path):
    import signal
    import subprocess
    import sys

    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).parents[1])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "dmslog", "--data-dir", str(tmp_path / "d"), "watch", str(root),
         "--case", "c", "--interval", "0.2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    try:
        assert "Watching" in proc.stdout.readline()
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert "Stopped watching." in out


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_stops_serve_watch_cleanly(tmp_path):
    import signal
    import subprocess
    import sys

    root = tp.write_folder(tmp_path / "Log", {WFM: tp.make_log(10)})
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).parents[1])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "dmslog", "--data-dir", str(tmp_path / "d"), "serve", "--no-browser",
         "--watch", str(root), "--watch-case", "c", "--watch-interval", "0.2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    try:
        seen = ""
        while "is running at" not in seen:
            line = proc.stdout.readline()
            assert line, proc.stderr.read()
            seen += line
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    assert "dmslog stopped." in out
