"""The "Reduced:" header line of the .txt export (ROADMAP R3d): present only with a reduced source."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

from dmslog.ingest.capped import CappedState, save_capped
from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.report.redact import Profile
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "export"
_spec = importlib.util.spec_from_file_location("export_case_reduced", FIXTURES / "build_case.py")
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_reduced"] = case_module
_spec.loader.exec_module(case_module)

NOW = datetime(2030, 10, 15, 11, 0, 0)
PROFILE = Profile(terms_word=tuple(case_module.USER_TERMS))
WHOLE = Selection(events=(1,), ids=(7,))
MB = 1024**2


def _state() -> CappedState:
    return CappedState(
        cap=256 * MB,
        head_bytes=MB,
        h=MB,
        h_line=10,
        lo=744 * MB,
        lo_line=900,
        skipped_bytes=743 * MB,
        skipped_lines=890,
        skipped_from_ts=1_900_000_000_000,
        skipped_to_ts=1_900_003_600_000,
        size_at_cap=1000 * MB,
    )


@pytest.fixture(scope="module")
def case_db(tmp_path_factory):
    return case_module.build_export_case(tmp_path_factory.mktemp("reduced") / "case.sqlite")


@pytest.fixture
def reduced_db(case_db, tmp_path):
    path = tmp_path / "case.sqlite"
    shutil.copy(case_db, path)
    return path


def _mark(path, source_id, *sql):
    conn = connect(path)
    with conn:
        for statement in sql:
            conn.execute(statement)
        save_capped(conn, source_id, _state())
    conn.close()


def _report(path, *, anonymize):
    conn = connect(path, readonly=True)
    try:
        options = ExportOptions(anonymize=anonymize, profile=PROFILE if anonymize else None, context=1)
        return build_report(conn, WHOLE, options, now=NOW, version="0.0.0-test").parts[0].text
    finally:
        conn.close()


def _reduced_lines(text):
    return [line for line in text.split("\r\n") if line.startswith("Reduced:")]


def test_no_reduced_source_keeps_the_golden(case_db):
    text = _report(case_db, anonymize=True)
    assert _reduced_lines(text) == []
    assert "Reduced" not in text  # the byte-exact golden is checked in test_export.py


def test_reduced_source_adds_one_line(reduced_db):
    _mark(reduced_db, 1)
    text = _report(reduced_db, anonymize=False)
    lines = _reduced_lines(text)
    assert len(lines) == 1
    line = lines[0]
    assert line.isascii() and "Log/" not in line
    assert "Reduced: WfmAdapter_4001.log - " in line
    assert "first 1 MB and the last 256 MB of 1000 MB" in line
    assert "skipped 743 MB (890 lines)" in line
    assert "1970-01-" not in line and " to " in line
    raw = text.encode("utf-8")
    assert raw.count(b"\n") == raw.count(b"\r\n")  # CRLF only
    head = text.split("\r\n")
    assert head[head.index(line) + 1].startswith("Redaction:")


def test_reduced_line_follows_anonymization_rules(reduced_db):
    _mark(reduced_db, 1, "UPDATE source SET file_name = 'Log/ACMEHOST01_zxqvaultn.log' WHERE id = 1")
    lines = _reduced_lines(_report(reduced_db, anonymize=True))
    assert len(lines) == 1
    assert "zxqvaultn" not in lines[0] and "ACMEHOST01" not in lines[0] and "Log/" not in lines[0]


def test_reduced_source_without_records_in_report_adds_nothing(reduced_db):
    _mark(reduced_db, 99)  # no such source in the report
    assert _reduced_lines(_report(reduced_db, anonymize=False)) == []
