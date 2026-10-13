"""`dmslog export`: original text by default (U18), explicit anonymization, selections, parts, files,
the local value map."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from dmslog.cli import STUB_COMMANDS, main

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "export_case_cli", ROOT / "tests" / "fixtures" / "export" / "build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_cli"] = case_module
_spec.loader.exec_module(case_module)

SENSITIVE_VALUES = [v for values in case_module.SENSITIVE.values() for v in values]


@pytest.fixture
def data_dir(tmp_path):
    case_module.build_export_case(tmp_path / "data" / "cases" / "demo" / "case.sqlite")
    return tmp_path / "data"


def run(data_dir, *args):
    return main(["--data-dir", str(data_dir), "export", "--case", "demo", *args])


def test_export_is_no_longer_a_stub():
    assert "export" not in STUB_COMMANDS


def test_the_default_export_is_the_original_text(data_dir, capsysbinary):
    # U18: no flag = no anonymization, no profile read, no value map
    (data_dir / "redaction.toml").write_text("[categories\n", encoding="utf-8")  # not even read
    selection = ["--event", "1", "--ids", "7", "--filter-json", '{"source": [1, 2]}', "--context", "2"]
    assert run(data_dir, *selection, "--include-payloads") == 0
    text = capsysbinary.readouterr().out.decode("utf-8")
    assert "Redaction: OFF (original text)" in text and "== Redaction legend ==" not in text
    assert "<GUID-" not in text and "<VALUE-" not in text
    missing = [v for v in SENSITIVE_VALUES if v not in text]
    assert missing == []
    assert not (data_dir / "redaction-maps").exists()


def _without_generated_line(raw: bytes) -> bytes:
    # The header carries "Generated: <local time>" with second resolution: two runs that straddle a
    # second boundary differ only there, which made this comparison flaky on slow CI runners.
    return b"\r\n".join(ln for ln in raw.split(b"\r\n") if not ln.lstrip().startswith(b"Generated:"))


def test_no_anonymize_is_still_accepted_and_equals_the_default(data_dir, capsysbinary):
    assert run(data_dir, "--ids", "3") == 0
    default = capsysbinary.readouterr().out
    assert b"Generated:" in default  # the masking below must really remove something
    assert run(data_dir, "--ids", "3", "--no-anonymize") == 0
    other = capsysbinary.readouterr().out
    assert _without_generated_line(other) == _without_generated_line(default)


def test_anonymize_and_no_anonymize_together_are_refused(data_dir, capsys):
    with pytest.raises(SystemExit) as info:
        run(data_dir, "--ids", "7", "--anonymize", "--no-anonymize")
    assert info.value.code == 2
    assert "anonymize" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [[], ["--no-anonymize"]])
def test_redaction_mode_without_anonymize_is_an_error(data_dir, flags, capsys):
    assert run(data_dir, "--ids", "3", "--redaction-mode", "strict", *flags) == 1
    captured = capsys.readouterr()
    assert "--redaction-mode only applies together with --anonymize" in captured.err
    assert captured.out == ""


def test_a_selection_is_required(data_dir, capsys):
    assert run(data_dir, "--anonymize") == 1
    assert "give --ids, --event or --filter-json" in capsys.readouterr().err


def test_anonymized_report_to_a_file(data_dir, tmp_path, capsys):
    out = tmp_path / "out" / "report.txt"
    code = run(data_dir, "--ids", "3", "--event", "1", "--anonymize", "--include-payloads", "--out", str(out))
    assert code == 0
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\r\n")
    assert raw.count(b"\n") == raw.count(b"\r\n")
    text = raw.decode("utf-8")
    assert "Redaction: ON" in text and "<GUID-1>" in text
    for value in SENSITIVE_VALUES:
        assert value not in text, value
    captured = capsys.readouterr()
    assert f"Wrote {out}" in captured.out
    maps = list((data_dir / "cases" / "demo" / "redaction-maps").glob("*.map.json"))
    assert len(maps) == 1 and "demo" not in maps[0].name
    assert json.loads(maps[0].read_text(encoding="utf-8"))["values"]["<GUID-1>"] == case_module.GUID_A
    assert "Value map saved to" in captured.err and case_module.GUID_A not in captured.err + captured.out


def test_profile_from_the_data_dir_is_used(data_dir, capsysbinary):
    (data_dir / "redaction.toml").write_text('[terms]\nword = ["ACME"]\n', encoding="utf-8")
    assert run(data_dir, "--ids", "5", "--anonymize") == 0
    text = capsysbinary.readouterr().out.decode("utf-8")
    assert "dmslog report - <TERM-1> <TERM-2>" not in text  # only ACME is a term here: SUBALPHA is not
    assert "dmslog report - <TERM-1> " in text


def test_stdout_gets_the_report_bytes(data_dir, capsysbinary):
    assert run(data_dir, "--ids", "3", "--no-anonymize") == 0
    raw = capsysbinary.readouterr().out
    assert raw.startswith(b"dmslog report - ACME SUBALPHA outage\r\n")
    assert b"Redaction: OFF" in raw and b"jsilva" in raw


def test_no_anonymize_keeps_values_and_writes_no_map(data_dir, tmp_path):
    out = tmp_path / "plain.txt"
    assert run(data_dir, "--event", "1", "--no-anonymize", "--out", str(out)) == 0
    assert case_module.GUID_A in out.read_text(encoding="utf-8")
    assert not (data_dir / "redaction-maps").exists()


def test_numbered_parts_in_files(data_dir, tmp_path, capsys):
    out = tmp_path / "report.txt"
    code = run(
        data_dir, "--event", "1", "--ids", "7", "--anonymize", "--include-payloads", "--context", "1",
        "--max-bytes", "2000", "--out", str(out),
    )  # fmt: skip
    assert code == 0
    files = sorted(tmp_path.glob("report-part*of*.txt"))
    assert len(files) >= 2 and not out.exists()
    assert files[0].name == f"report-part1of{len(files)}.txt"
    assert all(len(f.read_bytes()) <= 2000 for f in files)
    assert capsys.readouterr().out.count("Wrote ") == len(files)


def test_filter_json_inline_and_from_file(data_dir, tmp_path, capsysbinary):
    assert run(data_dir, "--filter-json", '{"sev": {"le": 2}}', "--no-anonymize") == 0
    assert b"1 selected records" in capsysbinary.readouterr().out
    spec = tmp_path / "filter.json"
    spec.write_text('{"source": [2]}', encoding="utf-8")
    assert run(data_dir, "--filter-json", f"@{spec}", "--no-anonymize") == 0
    assert b"1 selected records" in capsysbinary.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--ids", "a,b"],
        ["--ids", "0"],
        ["--event", ""],
        ["--filter-json", "{"],
        ["--filter-json", "[]"],
        ["--filter-json", "@/nonexistent/filter.json"],
        ["--ids", "999"],
        ["--ids", "1", "--context", "101"],
        ["--ids", "1", "--max-bytes", "10"],
    ],
)
def test_bad_input_exits_with_failure(data_dir, args, capsys):
    assert run(data_dir, *args, "--anonymize") == 1
    assert capsys.readouterr().err.startswith("dmslog export: ")


def test_unknown_or_invalid_case(data_dir, capsys):
    assert main(["--data-dir", str(data_dir), "export", "--case", "nope", "--ids", "1", "--anonymize"]) == 1
    assert "has no database" in capsys.readouterr().err
    assert main(["--data-dir", str(data_dir), "export", "--case", "../x", "--ids", "1", "--anonymize"]) == 1
    capsys.readouterr()


def test_an_invalid_profile_stops_an_anonymized_export_without_echoing_terms(data_dir, capsys):
    (data_dir / "redaction.toml").write_text('[terms]\nword = ["secret-customer", 5]\n', encoding="utf-8")
    assert run(data_dir, "--ids", "1", "--anonymize") == 1
    err = capsys.readouterr().err
    assert "Invalid redaction profile" in err and "secret-customer" not in err
    assert run(data_dir, "--ids", "1", "--no-anonymize") == 0  # explicit opt-out ignores the profile
    capsys.readouterr()


def test_redaction_mode_option_overrides_the_profile(data_dir, capsysbinary):
    (data_dir / "redaction.toml").write_text('mode = "standard"\n', encoding="utf-8")
    assert run(data_dir, "--ids", "5", "--anonymize") == 0
    assert 'profile "default", standard mode' in capsysbinary.readouterr().out.decode("utf-8")
    assert run(data_dir, "--ids", "5", "--anonymize", "--redaction-mode", "strict") == 0
    assert 'profile "default", strict mode' in capsysbinary.readouterr().out.decode("utf-8")


def test_redaction_mode_option_is_validated(data_dir, capsys):
    with pytest.raises(SystemExit) as info:
        run(data_dir, "--ids", "5", "--anonymize", "--redaction-mode", "lenient")
    assert info.value.code == 2
    assert "redaction-mode" in capsys.readouterr().err
