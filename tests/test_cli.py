import subprocess
import sys

from dmslog import __version__
from dmslog.cli import main


def test_main_sem_argumentos():
    assert main([]) == 0


def test_module_entry_point_reports_version():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "dmslog", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert result.stdout.strip() == __version__
