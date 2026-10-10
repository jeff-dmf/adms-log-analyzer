"""Ingestion of the real ERS samples in local-samples/ (local only, skipped when they are absent).

The samples hold sensitive data: this test only measures and compares counts, it never prints or
copies log text. Acceptance targets (docs/ROADMAP.md, M1): default scope in <= 40 s, RSS < 500 MB.
Run with ``-s`` to see the measurements.
"""

import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from dmslog import aliases as aliases_mod
from dmslog.ingest.ers import plan_collection, select_sources
from dmslog.store import connect

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "local-samples"
ENTERPRISE = re.compile(r"^ERS_CL_.*_\d{14}_Enterprise_.*\.zip$")
MIN_REAL_SIZE = 1_000_000  # smaller files are Git LFS pointers or placeholders

TIME_TARGET_S = 40.0
RSS_TARGET_MB = 500.0


def _usable(path: Path) -> bool:
    """A real zip of a plausible size; an unreadable or vanished file is skipped (no exception text)."""
    try:
        return path.stat().st_size >= MIN_REAL_SIZE and zipfile.is_zipfile(path)
    except OSError:
        return False


def _find_sample() -> Path | None:
    try:
        paths = sorted(SAMPLES.glob("*.zip"))
    except OSError:
        return None
    for path in paths:
        if ENTERPRISE.match(path.name) and _usable(path):
            return path
    return None


SAMPLE = _find_sample()
pytestmark = pytest.mark.skipif(SAMPLE is None, reason="no real Enterprise ERS zip in local-samples/")


def _peak_rss_mb_of_child(cmd: list[str]) -> tuple[subprocess.CompletedProcess[str], float | None]:
    try:
        import resource
    except ImportError:  # Windows has no resource module
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8"), None
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if peak == before:  # an earlier child was larger; the figure would not be ours
        return proc, None
    return proc, peak / 1024.0  # Linux reports KiB


def _check(ok: object, label: str) -> None:
    """Assert with a FIXED message: pytest must never echo sample names, stderr or log text."""
    assert ok, f"real-sample check failed: {label}"


def _physical_lines(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def test_default_scope_meets_the_acceptance_targets(tmp_path, capsys):
    _check(SAMPLE is not None, "sample present")
    alias_file = SAMPLES / "aliases.toml"  # optional, local only
    if alias_file.is_file():
        shutil.copyfile(alias_file, tmp_path / "aliases.toml")
    cmd = [sys.executable, "-I", "-m", "dmslog", "--data-dir", str(tmp_path), "ingest", str(SAMPLE)]
    started = time.perf_counter()
    proc, rss_mb = _peak_rss_mb_of_child(cmd)
    wall = time.perf_counter() - started
    _check(proc.returncode == 0, "ingest exit code 0")

    dbs = list((tmp_path / "cases").glob("*/case.sqlite"))
    _check(len(dbs) == 1, "one case database")
    conn = connect(dbs[0], readonly=True)
    try:
        selected = select_sources(plan_collection(SAMPLE, aliases_mod.get(tmp_path)).sources)
        statuses = dict(conn.execute("SELECT status, count(*) FROM source GROUP BY status").fetchall())
        _check(statuses.get("ready") == len(selected), "all selected sources ready")
        _check(statuses.get("error", 0) == 0, "no source in error")
        entries, db_lines = conn.execute("SELECT count(*), coalesce(sum(nlines), 0) FROM entry").fetchone()
        sources_without_records = conn.execute(
            "SELECT count(*) FROM source WHERE status='ready' AND n_entries = 0"
        ).fetchone()[0]

        # The sum of nlines must equal the physical lines of every selected member.
        expected_lines = 0
        with zipfile.ZipFile(SAMPLE) as zf:
            for info in selected:
                with zf.open(info.member) as fh:
                    head = fh.read(2)
                    if head in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16: lines are counted on the decoded text
                        text = (head + fh.read()).decode("utf-16", errors="replace")
                        expected_lines += _physical_lines(text.encode("utf-8"))
                    else:
                        expected_lines += _physical_lines(head + fh.read())
        _check(db_lines == expected_lines, "nlines sum equals physical lines")
        fts_rows = conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0]
        not_indexed = conn.execute(
            "SELECT count(*) FROM entry WHERE tpl_id IN (SELECT id FROM template WHERE fts = 0)"
        ).fetchone()[0]
        _check(
            fts_rows == entries - not_indexed, "FTS row count"
        )  # fts=0 templates (Verbose trace) stay out of the FTS
    finally:
        conn.close()

    total_mb = sum(s.size for s in selected) / 1e6
    with capsys.disabled():
        print(
            f"\n[real ERS default scope] {len(selected)} sources, {entries} records, {db_lines} lines, "
            f"{total_mb:.0f} MB in {wall:.1f} s wall ({total_mb / wall:.1f} MB/s), "
            f"peak RSS {'n/a' if rss_mb is None else f'{rss_mb:.0f} MB'}, "
            f"{sources_without_records} sources without records"
        )
    _check(wall <= TIME_TARGET_S, "time target")
    if rss_mb is not None:
        _check(rss_mb < RSS_TARGET_MB, "RSS target")

    # Re-ingesting the same zip changes nothing and is fast.
    started = time.perf_counter()
    again = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    _check(again.returncode == 0, "re-ingest exit code 0")
    _check("0 indexed" in again.stdout and "Records: 0" in again.stdout, "re-ingest is a no-op")
    _check(time.perf_counter() - started < 10, "re-ingest is fast")
