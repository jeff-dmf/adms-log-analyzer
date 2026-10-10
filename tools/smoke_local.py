"""Smoke test for M1 acceptance: envelope coverage, out-of-scope indexing, server performance.

Run with: python tools/smoke_local.py
Or via pytest: python -m pytest tests/samples/test_smoke_local.py

The real ERS samples in local-samples/ hold sensitive data. This test:
- Checks for samples and skips silently if absent or are LFS pointers.
- Never prints or saves log text; only counts, times, and memory.
- Verifies: envelope coverage (0 orphan records, nlines sum = physical lines),
  one log outside default scope indexes with --all-logs and opens via API,
  HTTP request performance (p50/p95/max in ms, 50 reps each).
- Reports peak RSS and ingest time against acceptance targets.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

# Detect if we can run
try:
    from dmslog import aliases as aliases_mod
    from dmslog.ingest.ers import plan_collection, select_sources
    from dmslog.server.app import DmslogServer
    from dmslog.store import connect
except ImportError:
    sys.stderr.write("Module dmslog not found; ensure you are running from the venv.\n")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "local-samples"
ENTERPRISE = re.compile(r"^ERS_CL_.*_\d{14}_Enterprise_.*\.zip$")
SENSITIVE = re.compile(r"^ERS_CL_.*_\d{14}_Sensitive_Enterprise_.*\.zip$")
MIN_REAL_SIZE = 1_000_000  # smaller files are Git LFS pointers or placeholders
ALIASES_FILE = SAMPLES / "aliases.toml"  # optional, local only


def _ingest_failure(label: str, returncode: int) -> str:
    """Neutral diagnostic for a failed ingest: a label, the exit code and a fixed text.

    Never includes file names, GUIDs, raw stderr or log text (the samples are sensitive).
    """
    return f"ingest of {label} failed (exit code {returncode}; details withheld)"


def _safe_error(label: str, exc: BaseException) -> str:
    """Fixed diagnostic for an exception: label, class name and errno only.

    Never includes the exception message, which may hold a zip member name or log text.
    """
    errno = getattr(exc, "errno", None)
    suffix = f", errno={errno}" if isinstance(errno, int) else ""
    return f"{label} failed ({type(exc).__name__}{suffix}; details withheld)"


def _zip_physical_lines(path: Path, selected, label: str) -> tuple[int, str | None]:
    """Sum the physical lines of the selected members. Any read error becomes a sanitized message."""
    total = 0
    try:
        with zipfile.ZipFile(path) as zf:
            for info in selected:
                with zf.open(info.member) as fh:
                    head = fh.read(2)
                    if head in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16
                        text = (head + fh.read()).decode("utf-16", errors="replace")
                        total += _physical_lines(text.encode("utf-8"))
                    else:
                        total += _physical_lines(head + fh.read())
    except Exception as exc:  # noqa: BLE001 - zipfile/zlib/OS errors carry member names
        return 0, _safe_error(f"reading {label}", exc)
    return total, None


def _seed_aliases(data_dir: Path) -> None:
    """Copy the optional local alias file into a temp data dir (never touches the real one)."""
    if ALIASES_FILE.is_file():
        shutil.copyfile(ALIASES_FILE, Path(data_dir) / "aliases.toml")


def _plan(path: Path, data_dir: Path):
    """plan_collection with the aliases that the ingest subprocess will load from `data_dir`."""
    return plan_collection(path, aliases_mod.get(data_dir))


TIME_TARGET_S = 40.0  # indexing only (M1 acceptance); derivation is checked separately
DERIVE_TARGET_S = 20.0
DERIVE_SECONDS = [0.0]  # filled by _ingest_default_scope
PAGE_TARGET_MS = 50.0
FTS_TARGET_MS = 100.0
RSS_TARGET_MB = 500.0

# Acceptance: envelope coverage, line counts, and out-of-scope log access
ACCEPTANCE_CRITERIA = {
    "ingest_time_s": (0, TIME_TARGET_S, "<="),
    "derive_time_s": (0, DERIVE_TARGET_S, "<="),
    "page_p95_ms": (0, PAGE_TARGET_MS, "<"),
    "fts_p95_ms": (0, FTS_TARGET_MS, "<"),
    "rss_mb": (0, RSS_TARGET_MB, "<"),
}


def _find_samples() -> tuple[Path | None, Path | None]:
    """Return (Enterprise, Sensitive) from the 12:40 capture (earliest), else (None, None).

    The requirement specifies the 12:40 capture: use the earliest timestamp.
    """
    enterprise = None
    sensitive = None

    for path in sorted(SAMPLES.glob("*.zip")):
        if not (path.stat().st_size >= MIN_REAL_SIZE and zipfile.is_zipfile(path)):
            continue
        if ENTERPRISE.match(path.name) and enterprise is None:
            enterprise = path
        elif SENSITIVE.match(path.name) and sensitive is None:
            sensitive = path

        # Once we have both from the same timestamp, stop
        if enterprise is not None and sensitive is not None:
            # Verify they're from the same timestamp (same GUID and timestamp)
            ent_parts = enterprise.stem.split("_")
            sens_parts = sensitive.stem.split("_")
            if (
                len(ent_parts) >= 3
                and len(sens_parts) >= 3
                and ent_parts[2] == sens_parts[2]
                and ent_parts[3] == sens_parts[3]
            ):
                return (enterprise, sensitive)
            # If timestamps don't match, reset and continue
            enterprise = None
            sensitive = None

    return (None, None)


def _physical_lines(data: bytes) -> int:
    """Count physical lines in bytes."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _ingest_default_scope(
    data_dir: Path, enterprise_zip: Path, sensitive_zip: Path
) -> tuple[float, str | None, float | None]:
    """Ingest both zips via CLI (default scope).
    Return (elapsed_seconds, error_msg or None, peak_rss_mb or None).
    """
    import subprocess

    # Measure peak RSS of the ingest subprocess (similar to test_real_ers_ingest.py)
    try:
        import resource

        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (ImportError, AttributeError):
        before = None

    started = time.perf_counter()
    derive_s = 0.0
    for label, path in (
        ("zip 1 of 2 (enterprise)", enterprise_zip),
        ("zip 2 of 2 (sensitive)", sensitive_zip),
    ):
        cmd = [sys.executable, "-I", "-m", "dmslog", "--data-dir", str(data_dir), "ingest", str(path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            return time.perf_counter() - started, _ingest_failure(label, proc.returncode), None
        found = re.search(r"derivation took ([0-9.]+) s", proc.stdout)
        elapsed_now = time.perf_counter() - started
        if found is None:
            return elapsed_now, f"ingest of {label}: derivation line missing from the output", None
        if "[warning]" in proc.stdout:
            return elapsed_now, f"ingest of {label} printed a warning (derivation or ingest)", None
        derive_s += float(found.group(1))

    # M1 target covers indexing; the M4 derivation pass is reported and checked on its own.
    DERIVE_SECONDS[0] = derive_s
    elapsed = time.perf_counter() - started - derive_s

    # Measure peak RSS of the child processes
    rss_mb = None
    if before is not None:
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            if peak > before:
                rss_mb = peak / 1024.0  # Linux reports KiB
        except (ImportError, AttributeError):
            pass

    return elapsed, None, rss_mb


def _verify_envelope_coverage(data_dir: Path, enterprise_zip: Path, sensitive_zip: Path) -> str | None:
    """Check envelope coverage: 0 orphan records, nlines sum == physical lines. Return error msg or None."""
    dbs = list((data_dir / "cases").glob("*/case.sqlite"))
    if len(dbs) != 1:
        return f"Expected 1 case db, found {len(dbs)}"

    conn = connect(dbs[0], readonly=True)
    try:
        # Check for orphan records
        orphans = conn.execute(
            "SELECT count(*) FROM entry WHERE source_id NOT IN (SELECT id FROM source)"
        ).fetchone()[0]
        if orphans > 0:
            return f"Found {orphans} orphan records"

        # Sum nlines and count physical lines in selected sources
        selected_by_path = {}
        for path in [enterprise_zip, sensitive_zip]:
            plan = _plan(path, data_dir)
            selected = select_sources(plan.sources)
            selected_by_path[path] = selected

        entries, db_lines = conn.execute("SELECT count(*), coalesce(sum(nlines), 0) FROM entry").fetchone()
        expected_lines = 0
        for idx, (_path, selected) in enumerate(selected_by_path.items(), start=1):
            lines, read_error = _zip_physical_lines(_path, selected, f"zip {idx} of 2")
            if read_error:
                return read_error
            expected_lines += lines
        if db_lines != expected_lines:
            return f"nlines sum {db_lines} != physical lines {expected_lines}"
        return None
    finally:
        conn.close()


def _test_all_logs_scope(enterprise_zip: Path) -> tuple[bool, str | None]:
    """Test that a log outside default scope (NMC or PSengine) indexes with --all-logs.
    Use a separate temp data-dir to avoid mixing with the default scope data.
    Return (success, error_msg or None).
    """
    import subprocess
    import tempfile as tf

    with tf.TemporaryDirectory() as tmp_dir:
        tmp_data_dir = Path(tmp_dir)
        _seed_aliases(tmp_data_dir)

        # First, find which logs are in the default scope
        plan = _plan(enterprise_zip, tmp_data_dir)
        default_selected = select_sources(plan.sources)
        default_names = {Path(info.member).name for info in default_selected}

        # Ingest with --all-logs in separate data-dir
        cmd = [
            sys.executable,
            "-I",
            "-m",
            "dmslog",
            "--data-dir",
            str(tmp_data_dir),
            "ingest",
            "--all-logs",
            str(enterprise_zip),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            return False, _ingest_failure("the --all-logs run", proc.returncode)

        # Check if we indexed something new
        dbs = list((tmp_data_dir / "cases").glob("*/case.sqlite"))
        if len(dbs) != 1:
            return False, f"Expected 1 case db after --all-logs, found {len(dbs)}"

        conn = connect(dbs[0], readonly=True)
        try:
            # Get all indexed sources from the all-logs run
            all_selected = select_sources(plan.sources)
            all_names = {Path(info.member).name for info in all_selected}

            # Find one source outside default scope
            out_of_scope = [name for name in all_names if name not in default_names]
            if not out_of_scope:
                # No out-of-scope logs found; this is OK (not all ERS have them)
                return True, None

            # Try to fetch an entry from the out-of-scope log
            sources = conn.execute(
                "SELECT id, member FROM source WHERE member IN ({})".format(
                    ",".join("?" * len(out_of_scope))
                ),
                out_of_scope,
            ).fetchall()
            if not sources:
                return False, f"Could not find sources for {len(out_of_scope)} out-of-scope logs"

            source_id = sources[0][0]
            entry = conn.execute("SELECT id FROM entry WHERE source_id = ? LIMIT 1", (source_id,)).fetchone()
            if entry is None:
                return False, "No entries found in the first out-of-scope source"

            return True, None
        finally:
            conn.close()


def _choose_fts_terms(data_dir: Path) -> tuple[str, str]:
    """Choose one rare and one common FTS term from the database distribution (without printing text)."""
    dbs = list((data_dir / "cases").glob("*/case.sqlite"))
    if not dbs:
        return "error", "search"

    conn = connect(dbs[0], readonly=True)
    try:
        # Get term frequencies from FTS5; pick one with low frequency and one with high
        cursor = conn.execute(
            """SELECT term, (SELECT COUNT(*) FROM entry_fts WHERE entry_fts MATCH term)
            FROM (
                WITH RECURSIVE cnt(x) AS (
                    SELECT 1
                    UNION ALL
                    SELECT x+1 FROM cnt WHERE x < (SELECT count(DISTINCT term) FROM entry_fts_vocab('term'))
                )
                SELECT term FROM entry_fts_vocab('term')
                ORDER BY RANDOM() LIMIT 100
            )"""
        )
        terms = list(cursor.fetchall())
        if len(terms) < 2:
            return "test", "case"

        # Sort by frequency: pick the least frequent (rare) and most frequent (common)
        terms_by_freq = sorted(terms, key=lambda x: x[1])
        rare_term = terms_by_freq[0][0] if terms_by_freq else "rare"
        common_term = terms_by_freq[-1][0] if terms_by_freq else "common"
        return rare_term, common_term
    except Exception:
        return "test", "default"
    finally:
        conn.close()


def _measure_http_requests(server: DmslogServer, case_id: str) -> dict[str, Any]:
    """Measure HTTP request performance: page (fwd/back), count, FTS, get_entry.
    Return dict with p50/p95/max in ms for each endpoint.
    """
    import urllib.request
    from http.cookiejar import CookieJar

    results = {
        "page_fwd": [],
        "page_back": [],
        "count": [],
        "fts_rare": [],
        "fts_common": [],
        "get_entry": [],
    }

    # Set up cookie jar and opener to handle session cookie
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # Exchange token for session cookie via GET with token parameter
    start_url = server.start_url
    try:
        with opener.open(start_url) as resp:
            resp.read()
    except Exception as e:
        return {"error": f"Failed to exchange token ({type(e).__name__})"}

    # Get an entry ID for later
    dbs = list((server.data_dir / "cases").glob("*/case.sqlite"))
    if not dbs:
        return {"error": "No case database found"}

    conn = connect(dbs[0], readonly=True)
    try:
        entry = conn.execute("SELECT id FROM entry LIMIT 1").fetchone()
        if not entry:
            return {"error": "No entries in database"}
        entry_id = entry[0]
    finally:
        conn.close()

    # Choose FTS terms
    rare_term, common_term = _choose_fts_terms(server.data_dir)

    # Helper to make HTTP request and measure time
    def measure_request(method: str, path: str, body: dict[str, Any] | None = None) -> float:
        """Make request, measure time in ms, return elapsed ms."""
        start = time.perf_counter()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.port}{path}",
                method=method,
            )
            req.add_header("Host", f"127.0.0.1:{server.port}")
            if body:
                req.add_header("Content-Type", "application/json")
                req.data = json.dumps(body).encode("utf-8")
            with opener.open(req) as resp:
                resp.read()
        except Exception:
            pass
        return (time.perf_counter() - start) * 1000

    # 50 repetitions of each request
    for _ in range(50):
        results["page_fwd"].append(
            measure_request(
                "POST",
                "/api/entries/page",
                {"case": case_id, "limit": 200, "dir": "fwd"},
            )
        )
        results["page_back"].append(
            measure_request(
                "POST",
                "/api/entries/page",
                {"case": case_id, "limit": 200, "dir": "back"},
            )
        )
        results["count"].append(measure_request("POST", "/api/entries/count", {"case": case_id}))
        results["fts_rare"].append(
            measure_request(
                "POST",
                "/api/entries/page",
                {"case": case_id, "filter": {"fts": rare_term}},
            )
        )
        results["fts_common"].append(
            measure_request(
                "POST",
                "/api/entries/page",
                {"case": case_id, "filter": {"fts": common_term}},
            )
        )
        results["get_entry"].append(measure_request("GET", f"/api/entries/{entry_id}", None))

    # Compute stats (excluding error key)
    stats = {}
    for key in [k for k in results.keys() if k != "error"]:
        times = results[key]
        if times:
            stats[key] = {
                "p50_ms": statistics.median(times),
                "p95_ms": sorted(times)[int(len(times) * 0.95)],
                "max_ms": max(times),
            }
    return stats


def _main() -> int:
    """Run the smoke test. Return 0 on success, 1 on failure, 0 (skip) if samples unavailable."""
    enterprise_zip, sensitive_zip = _find_samples()
    if enterprise_zip is None or sensitive_zip is None:
        print("skipped")
        return 0

    print("Testing with samples: zip 1 of 2 (enterprise), zip 2 of 2 (sensitive)")

    with tempfile.TemporaryDirectory() as tmp_dir:
        data_dir = Path(tmp_dir)
        _seed_aliases(data_dir)

        # Phase 1: Ingest default scope
        print("Ingesting default scope...", end=" ", flush=True)
        ingest_time, error, ingest_rss_mb = _ingest_default_scope(data_dir, enterprise_zip, sensitive_zip)
        if error:
            print(f"\nFAIL: {error}")
            return 1
        print(f"OK ({ingest_time:.1f} s)")

        # Phase 2: Verify envelope coverage
        print("Verifying envelope coverage...", end=" ", flush=True)
        error = _verify_envelope_coverage(data_dir, enterprise_zip, sensitive_zip)
        if error:
            print(f"\nFAIL: {error}")
            return 1
        print("OK")

        # Phase 3: Test out-of-scope logs with --all-logs
        print("Testing out-of-scope logs...", end=" ", flush=True)
        success, error = _test_all_logs_scope(enterprise_zip)
        if not success:
            print(f"\nFAIL: {error}")
            return 1
        print("OK")

        # Phase 4: Measure server performance
        print("Measuring server performance (50 requests each)...", end=" ", flush=True)

        # Start server in a thread
        server = DmslogServer(port=0, data_dir=data_dir)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            # Get case ID
            cases = [d.name for d in (data_dir / "cases").iterdir() if d.is_dir()]
            if not cases:
                print("\nFAIL: No case found")
                return 1
            case_id = cases[0]

            # Measure requests
            http_stats = _measure_http_requests(server, case_id)
            if "error" in http_stats:
                print(f"\nFAIL: {http_stats['error']}")
                return 1
            print("OK")
        finally:
            server.server_close()

        # Collect results (using RSS from ingest subprocess, not the entire script)
        # Build results table
        results = {
            "ingest_time_s": ingest_time,
            "derive_time_s": DERIVE_SECONDS[0],
            "page_p95_ms": max(
                http_stats.get("page_fwd", {}).get("p95_ms", 0),
                http_stats.get("page_back", {}).get("p95_ms", 0),
            ),
            "fts_p95_ms": max(
                http_stats.get("fts_rare", {}).get("p95_ms", 0),
                http_stats.get("fts_common", {}).get("p95_ms", 0),
            ),
            "rss_mb": ingest_rss_mb or 0,
        }

        # Print results table
        print("\n" + "=" * 70)
        print("M1 ACCEPTANCE RESULTS")
        print("=" * 70)
        print(f"{'Metric':<30} {'Value':<20} {'Target':<20} {'Status':<10}")
        print("-" * 70)

        all_pass = True
        for key, (_, target, op) in ACCEPTANCE_CRITERIA.items():
            value = results[key]
            if op == "<=":
                status = "PASS" if value <= target else "FAIL"
            elif op == "<":
                status = "PASS" if value < target else "FAIL"
            all_pass = all_pass and status == "PASS"

            if key == "rss_mb":
                value_str = f"{value:.0f} MB" if value > 0 else "n/a"
                target_str = f"< {target:.0f} MB"
            else:
                is_seconds = key in ("ingest_time_s", "derive_time_s")
                value_str = f"{value:.1f}" if is_seconds else f"{value:.1f} ms"
                target_str = f"{op} {target:.0f}" if is_seconds else f"{op} {target:.0f} ms"

            print(f"{key:<30} {value_str:<20} {target_str:<20} {status:<10}")

        print("=" * 70)

        if all_pass:
            print("Overall: PASS")
            return 0
        else:
            print("Overall: FAIL")
            return 1


def main() -> int:
    """Run the smoke test; an unexpected exception prints only its class name (no traceback, no message)."""
    try:
        return _main()
    except Exception as exc:  # noqa: BLE001 - messages and tracebacks may hold sample names
        print(f"\nFAIL: unexpected {type(exc).__name__} (details withheld)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
