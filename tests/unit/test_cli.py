import subprocess
import sys

import pytest

from dmslog import __version__
from dmslog.cli import STUB_COMMANDS, main

COMMANDS = ["serve", "info", "ingest", "export", "templates", "kb"]


def test_main_without_arguments_prints_help_and_returns_zero(capsys):
    assert main([]) == 0
    assert "usage: dmslog" in capsys.readouterr().out


def test_module_entry_point_reports_version():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "dmslog", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert result.stdout.strip() == __version__


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize("command", COMMANDS)
def test_subcommand_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert f"usage: dmslog {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(STUB_COMMANDS))
def test_stub_commands_exit_with_code_two(command, capsys):
    assert main([command]) == 2
    err = capsys.readouterr().err
    assert "not implemented yet (milestone M" in err
    assert command in err


def test_info_reports_environment(capsys, tmp_path):
    assert main(["--data-dir", str(tmp_path), "info"]) == 0
    out = capsys.readouterr().out
    assert f"dmslog: {__version__}" in out
    assert "fts5: yes" in out
    assert str(tmp_path) in out


def test_data_dir_is_accepted_after_the_subcommand(capsys, tmp_path):
    assert main(["info", "--data-dir", str(tmp_path)]) == 0
    assert str(tmp_path) in capsys.readouterr().out


@pytest.mark.parametrize("value", ["-1", "65536", "abc"])
def test_serve_rejects_invalid_port(value, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--port", value])
    assert exc.value.code == 2
    assert "invalid port" in capsys.readouterr().err


def _pack_line(out):
    return next(line for line in out.splitlines() if line.startswith("knowledge pack: "))


def test_info_without_pack_says_not_installed_and_where_it_looked(capsys, tmp_path):
    assert main(["info", "--data-dir", str(tmp_path)]) == 0
    line = _pack_line(capsys.readouterr().out)
    assert line.startswith("knowledge pack: not installed (searched: data_dir: ")
    assert str(tmp_path) in line


def test_info_with_pack_dir_reports_source_and_version(capsys, tmp_path, mini_pack_dir):
    assert main(["info", "--data-dir", str(tmp_path), "--pack-dir", str(mini_pack_dir)]) == 0
    line = _pack_line(capsys.readouterr().out)
    assert line.startswith("knowledge pack: installed (option: ")
    assert "schema 1" in line and "vault synthetic-test-fixture" in line


def test_info_with_pack_in_the_data_dir(capsys, tmp_path, mini_pack_dir):
    import shutil

    shutil.copytree(mini_pack_dir, tmp_path / "knowledge" / "pack")
    assert main(["--pack-dir", "", "--data-dir", str(tmp_path), "info"]) == 0
    assert _pack_line(capsys.readouterr().out).startswith("knowledge pack: installed (data_dir: ")


def test_info_with_invalid_pack(capsys, tmp_path):
    (tmp_path / "pack").mkdir()
    (tmp_path / "pack" / "manifest.json").write_text("{}", encoding="utf-8")
    assert main(["info", "--data-dir", str(tmp_path), "--pack-dir", str(tmp_path / "pack")]) == 0
    line = _pack_line(capsys.readouterr().out)
    assert line.startswith("knowledge pack: invalid, ignored (option): ")
