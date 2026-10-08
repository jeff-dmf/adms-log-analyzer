"""Tests for `dmslog ingest` and the case listing of `dmslog info`."""

import importlib.util
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

from dmslog.cli import main

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)

CASE = "00000000-20300115103000"


def case_db(data_dir: Path, name: str = CASE) -> Path:
    return data_dir / "cases" / name / "case.sqlite"


def count(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


def run(data_dir: Path, *args: str) -> int:
    return main(["--data-dir", str(data_dir), "ingest", *args])


def test_ingest_zip_prints_progress_and_summary(tmp_path, data_dir, capsys):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert run(data_dir, str(zpath)) == 0
    out = capsys.readouterr().out
    assert f"into case {CASE} (default scope)" in out
    assert "[new]" in out and "WfmAdapter_1111.log" in out
    assert "Sources: 6 selected of 11 listed (6 indexed, 0 unchanged, 5 not selected, 0 failed)" in out
    assert "Records: 31  Lines: 32" in out
    assert "MB/s" in out
    assert str(case_db(data_dir)) in out
    assert count(case_db(data_dir), "entry") == 31


def test_second_run_reports_everything_unchanged(tmp_path, data_dir, capsys):
    zpath = mf.build_ers_zip(tmp_path / "z")
    run(data_dir, str(zpath))
    capsys.readouterr()
    assert run(data_dir, str(zpath)) == 0
    out = capsys.readouterr().out
    assert "(0 indexed, 6 unchanged, 5 not selected, 0 failed)" in out
    assert "Records: 0" in out
    assert "[new]" not in out
    assert count(case_db(data_dir), "entry") == 31


def test_all_logs_flag_indexes_the_rest(tmp_path, data_dir, capsys):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert run(data_dir, str(zpath), "--all-logs") == 0
    out = capsys.readouterr().out
    assert "(all logs scope)" in out
    assert "7 selected of 11 listed" in out  # the 4 non-text files stay listed but are never indexed


def test_explicit_case_name_and_folder_input(tmp_path, data_dir, capsys):
    root = tmp_path / "Log" / "dnaApp_zone1" / "SVC"
    root.mkdir(parents=True)
    (root / "WfmAdapter_1111.log").write_bytes(mf.render([mf.header(0, "INFO", "hello")]))
    assert run(data_dir, str(tmp_path / "Log"), "--case", "my-case") == 0
    assert count(case_db(data_dir, "my-case"), "entry") == 1
    assert "into case my-case" in capsys.readouterr().out


def test_second_ers_in_the_same_case_exits_with_a_conflict(tmp_path, data_dir, capsys):
    run(data_dir, str(mf.build_ers_zip(tmp_path / "a")), "--case", "c")
    capsys.readouterr()
    other = mf.build_ers_zip(tmp_path / "b", guid=mf.synth_guid(2))
    assert run(data_dir, str(other), "--case", "c") == 3
    err = capsys.readouterr().err
    assert "cannot add this collection to case c" in err and "open a new case" in err
    assert count(case_db(data_dir, "c"), "collection") == 1


def test_other_half_of_the_pair_joins_the_same_default_case(tmp_path, data_dir, capsys):
    assert run(data_dir, str(mf.build_ers_zip(tmp_path / "e", kind="enterprise"))) == 0
    assert run(data_dir, str(mf.build_ers_zip(tmp_path / "s", kind="sensitive"))) == 0
    db = case_db(data_dir)
    assert count(db, "collection") == 2
    assert count(db, "stream") == 8  # 5 enterprise processes + 3 sensitive counterparts
    assert [p.name for p in (data_dir / "cases").iterdir()] == [CASE]


def test_missing_path_fails_cleanly(tmp_path, data_dir, capsys):
    assert run(data_dir, str(tmp_path / "nope.zip")) == 1
    assert "cannot ingest" in capsys.readouterr().err
    assert not (data_dir / "cases").exists()


@pytest.mark.parametrize("name", ["../evil", "a/b", ""])
def test_unsafe_case_name_is_rejected(tmp_path, data_dir, capsys, name):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert run(data_dir, str(zpath), "--case", name) == 1
    assert "invalid case name" in capsys.readouterr().err


def test_zip_slip_member_is_refused(tmp_path, data_dir, capsys):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.log", b"x\n")
    assert run(data_dir, str(bad)) == 1
    assert "unsafe or invalid archive" in capsys.readouterr().err


def test_info_lists_cases(tmp_path, data_dir, capsys):
    main(["--data-dir", str(data_dir), "info"])
    assert "cases: none" in capsys.readouterr().out
    run(data_dir, str(mf.build_ers_zip(tmp_path / "z")))
    capsys.readouterr()
    main(["--data-dir", str(data_dir), "info"])
    out = capsys.readouterr().out
    assert f"{CASE}: 6 sources, 31 records" in out


def test_ingest_help_is_in_english(capsys):
    with pytest.raises(SystemExit):
        main(["ingest", "--help"])
    out = capsys.readouterr().out
    assert "--all-logs" in out and "--case" in out and "--data-dir" in out
