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
