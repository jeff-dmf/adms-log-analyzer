"""Tests for tools/bench.py (worst-case performance bench), at a tiny scale."""

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("bench", ROOT / "tools" / "bench.py")
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench"] = bench
_spec.loader.exec_module(bench)


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def tiny_tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench_tree") / "ers"
    sizes = bench.generate_folder(root, 400_000, 2, "verbose", 7)
    return root, sizes


def test_generated_size_is_close_to_target(tiny_tree):
    root, sizes = tiny_tree
    total = sum(sizes.values())
    assert total == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    assert 300_000 <= total <= 700_000


def test_generation_is_deterministic_per_seed(tiny_tree, tmp_path):
    root, _ = tiny_tree
    bench.generate_folder(tmp_path / "same", 400_000, 2, "verbose", 7)
    bench.generate_folder(tmp_path / "other", 400_000, 2, "verbose", 8)
    assert tree_digest(tmp_path / "same") == tree_digest(root)
    assert tree_digest(tmp_path / "other") != tree_digest(root)


def test_generated_formats(tiny_tree):
    root, sizes = tiny_tree
    names = {Path(m).name for m in sizes}
    assert "WfmAdapter_1000.log" in names and "WfmAdapter_1000.log.0" in names  # rotation
    assert "WfmAdapter_1000.sensitive.log" in names
    assert "BLTHost.DMS_INTEGRATION.sensitive.log" in names
    assert "oasErrLog.log" in names
    wfm = (root / bench.APP_DIR / "WfmAdapter_1000.log.0").read_bytes()
    assert wfm.startswith(b"\xef\xbb\xbf2030-")  # BOM + timestamp
    text = wfm.decode("utf-8")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    assert "[PID=1000][TID=0x" in text
    assert "VERBOSE - [Communication2]" in text  # verbose trace
    assert "<Payload>" in text  # multi-line XML payload (continuation lines)
    oas = (root / bench.LOG_DIR / "oasErrLog.log").read_text(encoding="utf-8-sig")
    assert " - (" in oas


def test_info_profile_has_no_verbose_trace(tmp_path):
    sizes = bench.generate_folder(tmp_path / "ers", 200_000, 1, "info", 3)
    assert sizes
    for path in (tmp_path / "ers").rglob("*.log*"):
        assert " VERBOSE - " not in path.read_text(encoding="utf-8-sig")


def test_zip_is_valid_and_deterministic(tiny_tree, tmp_path):
    root, _ = tiny_tree
    bench.zip_folder(root, tmp_path / "a.zip")
    bench.zip_folder(root, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "a.zip") as zf:
        assert zf.testzip() is None
        assert any(n.endswith("WfmAdapter_1000.log") for n in zf.namelist())


def test_refuses_out_inside_repository(capsys):
    inside = bench.ROOT / "bench-out-should-not-exist"
    assert bench.main(["--quick", "--out", str(inside)]) == 2
    assert "outside the repository" in capsys.readouterr().err
    assert not inside.exists()


def test_refuses_non_empty_out(tmp_path, capsys):
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    assert bench.main(["--quick", "--out", str(tmp_path)]) == 2
    assert "new or empty" in capsys.readouterr().err
    assert (tmp_path / "keep.txt").exists()


def test_adapters_range_is_validated(tmp_path, capsys):
    assert bench.main(["--adapters", "99", "--out", str(tmp_path / "o")]) == 2
    assert "--adapters" in capsys.readouterr().err


def test_help_is_in_english(capsys):
    with pytest.raises(SystemExit) as exc:
        bench.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: bench.py" in out and "--size-mb" in out and "--ers" in out
    assert "Worst-case performance bench" in out


def test_parse_ingest_output():
    text = (
        "Sources: 5 selected of 6 listed (5 indexed, 0 unchanged, 1 not selected, 0 failed)\n"
        "Records: 602  Lines: 873  Data: 0.1 MB\nTime: 0.3 s (0.3 MB/s)\n"
        "Derived events: 94 (derivation took 0.2 s)\nLinks: 20 (correlation took 0.1 s)\n"
        "Payloads: 23 (13 linked to an operation, linking took 0.4 s)\n"
    )
    got = bench.parse_ingest_output(text)
    assert got["records"] == 602 and got["lines"] == 873 and got["data_mb"] == 0.1
    assert got["derive_seconds"] == 0.2 and got["correlate_seconds"] == 0.1
    assert got["payload_seconds"] == 0.4 and got["sources_failed"] == 0
    assert bench.parse_ingest_output("nothing")["records"] is None


def test_target_status_fail_and_na():
    run = {
        "wall_seconds": 2.0, "peak_rss_mb": 2000.0, "rss_method": "x", "db_bytes": 1_000_000,
        "returncode": 0, "warnings": [],
        "parsed": {"records": 10, "data_mb": 1.0, "sources_failed": 0},
    }  # fmt: skip
    cfg = {"max_rss_mb": 1024.0, "max_seconds": 5.0, "max_db_mb": None}
    rep = bench.build_report("synthetic", {}, {"path": "p"}, run, cfg, None)
    assert rep["targets"]["max_rss_mb"]["status"] == "FAIL"
    assert rep["targets"]["max_seconds"]["status"] == "PASS"
    assert "max_db_mb" not in rep["targets"] and rep["status"] == "FAIL"
    run["peak_rss_mb"] = None
    rep = bench.build_report("synthetic", {}, {"path": "p"}, run, cfg, None)
    assert rep["targets"]["max_rss_mb"]["status"] == "N/A" and rep["status"] == "PASS"
    assert "MB/s" in bench.format_table(rep)


def test_quick_end_to_end_json_and_cleanup(tmp_path, capsys):
    out = tmp_path / "work"
    json_path = tmp_path / "result.json"
    code = bench.main(["--quick", "--out", str(out), "--json", str(json_path)])
    assert code == 0, capsys.readouterr().out
    assert not out.exists()  # everything created was removed
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for key in ("tool", "schema", "mode", "platform", "params", "input", "result", "targets", "status"):
        assert key in data
    assert data["mode"] == "synthetic" and data["status"] == "PASS"
    res = data["result"]
    for key in ("wall_seconds", "records", "events", "mb_per_s", "records_per_s", "peak_rss_mb", "db_mb"):
        assert key in res
    assert res["records"] > 1000 and res["sources_failed"] == 0 and res["db_mb"] > 0
    assert data["targets"]["max_rss_mb"]["limit"] == 1024.0
    assert data["input"]["path"].endswith(".zip")
    assert "Overall: PASS" in capsys.readouterr().out


def test_keep_leaves_the_work_directory(tmp_path):
    out = tmp_path / "kept"
    assert bench.main(["--size-mb", "0.2", "--adapters", "1", "--out", str(out), "--keep"]) == 0
    assert list(out.glob("data/cases/*/case.sqlite"))
    assert (out / "ers").is_dir()


def test_existing_ers_is_measured_without_modification(tmp_path):
    ers = tmp_path / "ers"
    bench.generate_folder(ers, 150_000, 1, "info", 1)
    before = tree_digest(ers)
    out = tmp_path / "work"
    json_path = tmp_path / "r.json"
    assert bench.main(["--ers", str(ers), "--out", str(out), "--json", str(json_path)]) == 0
    assert tree_digest(ers) == before
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["mode"] == "ers" and data["result"]["records"] > 0


# ----------------------------------------------------------------------------- review fixes
class FakeProc:
    """A process that reports 'running' for ``polls`` polls, then ends."""

    def __init__(self, polls: int) -> None:
        self.polls = polls

    def poll(self):
        self.polls -= 1
        return None if self.polls >= 0 else 0


class FakeSampler(bench.PeakRssSampler):
    def __init__(self, values) -> None:
        self.values = list(values)
        self.calls = 0

    def sample(self):
        self.calls += 1
        return self.values.pop(0) if self.values else None


def test_monitor_takes_a_final_reading_after_the_process_ends():
    # the peak only shows up in the reading taken after the end was detected
    sampler = FakeSampler([10, 20, 5_000_000])
    peak, warnings = bench.monitor_peak(FakeProc(2), sampler, interval=0)
    assert peak == 5_000_000 and warnings == [] and sampler.calls == 3


def test_monitor_failed_readings_give_none_and_a_warning_never_zero():
    peak, warnings = bench.monitor_peak(FakeProc(1), FakeSampler([None, 0]), interval=0)
    assert peak is None and warnings and "unavailable" in warnings[0]
    peak, warnings = bench.monitor_peak(FakeProc(0), FakeSampler([None]), interval=0)  # ended at once
    assert peak is None and warnings
    peak, warnings = bench.monitor_peak(FakeProc(2), FakeSampler([None, 7, None]), interval=0)
    assert peak == 7 and warnings == []


def test_win32_signatures_are_fully_declared():
    from unittest.mock import MagicMock

    kernel32, psapi = MagicMock(), MagicMock()
    bench._declare_win_api(kernel32, psapi)
    for func in (kernel32.OpenProcess, kernel32.CloseHandle, psapi.GetProcessMemoryInfo):
        assert isinstance(func.argtypes, list) and func.argtypes
        assert func.restype is not None
    assert len(kernel32.OpenProcess.argtypes) == 3 and len(psapi.GetProcessMemoryInfo.argtypes) == 3
    assert "WinDLL" not in Path(bench.__file__).read_text(encoding="utf-8").split("def _declare_win_api")[0]


def _report_with(rss=None, wall=1.0, db_bytes=1_000_000, cfg=None):
    run = {
        "wall_seconds": wall, "peak_rss_mb": rss, "rss_method": "x", "db_bytes": db_bytes,
        "returncode": 0, "warnings": [], "parsed": {"records": 10, "data_mb": 1.0, "sources_failed": 0},
    }  # fmt: skip
    cfg = cfg or {"max_rss_mb": 1024.0, "max_seconds": 1.0004, "max_db_mb": None}
    return bench.build_report("synthetic", {}, {"path": "p"}, run, cfg, None)


def test_targets_compare_raw_values_not_rounded_ones():
    rep = _report_with(rss=1023.99, wall=1.00049)
    assert rep["targets"]["max_rss_mb"]["status"] == "PASS"  # rounds to 1024.0 but is below the limit
    assert rep["targets"]["max_seconds"]["status"] == "FAIL"  # rounds to 1.0 but is above the limit
    assert rep["targets"]["max_rss_mb"]["value_raw"] == 1023.99 and rep["status"] == "FAIL"
    rep = _report_with(rss=1024.004)  # rounds to 1024.0, above the limit
    assert rep["targets"]["max_rss_mb"]["status"] == "FAIL"


def test_target_at_the_limit_passes():
    rep = _report_with(rss=1024.0, wall=1.0004)
    assert rep["targets"]["max_rss_mb"]["status"] == "PASS"
    assert rep["targets"]["max_seconds"]["status"] == "PASS" and rep["status"] == "PASS"


def test_db_target_uses_the_raw_size():
    cfg = {"max_rss_mb": None, "max_seconds": None, "max_db_mb": 1.0}
    assert _report_with(db_bytes=1_000_000, cfg=cfg)["targets"]["max_db_mb"]["status"] == "PASS"
    assert _report_with(db_bytes=1_000_001, cfg=cfg)["targets"]["max_db_mb"]["status"] == "FAIL"


def test_invalid_numbers_in_ingest_output_do_not_crash():
    got = bench.parse_ingest_output("Time: ... s\nRecords: 5  Lines: 6  Data: .. MB\n")
    assert got["ingest_seconds"] is None and got["records"] is None
    assert bench.parse_ingest_output("Time: 1.5 s\n")["ingest_seconds"] == 1.5


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")


@pytest.fixture
def ers_dir(tmp_path):
    ers = tmp_path / "ers"
    bench.generate_folder(ers, 100_000, 1, "info", 1)
    return ers


def _refused(capsys, *argv) -> str:
    assert bench.main(list(argv)) == 2
    return capsys.readouterr().err


def test_out_inside_ers_folder_is_refused(ers_dir, capsys):
    before = tree_digest(ers_dir)
    assert "overlap" in _refused(capsys, "--ers", str(ers_dir), "--out", str(ers_dir / "bench"))
    assert not (ers_dir / "bench").exists() and tree_digest(ers_dir) == before


def test_out_equal_or_parent_of_ers_is_refused(ers_dir, tmp_path, capsys):
    assert "overlap" in _refused(capsys, "--ers", str(ers_dir), "--out", str(ers_dir))
    assert "overlap" in _refused(capsys, "--ers", str(ers_dir), "--out", str(tmp_path))
    zip_path = tmp_path / "ers.zip"
    bench.zip_folder(ers_dir, zip_path)
    assert "overlap" in _refused(capsys, "--ers", str(zip_path), "--out", str(zip_path))


def test_json_over_the_ers_zip_is_refused(ers_dir, tmp_path, capsys):
    zip_path = tmp_path / "ers.zip"
    bench.zip_folder(ers_dir, zip_path)
    before = zip_path.read_bytes()
    err = _refused(capsys, "--ers", str(zip_path), "--out", str(tmp_path / "o"), "--json", str(zip_path))
    assert "--json" in err and zip_path.read_bytes() == before and not (tmp_path / "o").exists()


def test_json_inside_ers_folder_out_or_repository_is_refused(ers_dir, tmp_path, capsys):
    out = str(tmp_path / "o")
    assert "--json" in _refused(
        capsys, "--ers", str(ers_dir), "--out", out, "--json", str(ers_dir / "r.json")
    )
    assert "--json" in _refused(capsys, "--quick", "--out", out, "--json", str(tmp_path / "o" / "r.json"))
    inside = bench.ROOT / "bench-result-should-not-exist.json"
    assert "--json" in _refused(capsys, "--quick", "--out", out, "--json", str(inside))
    assert not inside.exists() and not Path(out).exists()
    assert "directory" in _refused(capsys, "--quick", "--out", out, "--json", str(tmp_path))


def test_symlinked_destinations_are_refused(ers_dir, tmp_path, capsys):
    link_out = tmp_path / "link_out"
    _symlink(link_out, tmp_path / "real_out")  # dangling link to a new directory
    assert "symbolic link" in _refused(capsys, "--quick", "--out", str(link_out))
    link_ers = tmp_path / "link_ers"
    _symlink(link_ers, ers_dir)
    assert "overlap" in _refused(capsys, "--ers", str(ers_dir), "--out", str(link_ers / "bench"))
    zip_path = tmp_path / "ers.zip"
    bench.zip_folder(ers_dir, zip_path)
    link_json = tmp_path / "link.json"
    _symlink(link_json, zip_path)
    assert "symbolic link" in _refused(
        capsys, "--ers", str(zip_path), "--out", str(tmp_path / "o"), "--json", str(link_json)
    )
    assert zipfile.is_zipfile(zip_path)
