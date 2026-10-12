import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"


@pytest.fixture
def pyz(tmp_path, monkeypatch):
    sys.path.insert(0, str(PACKAGING))
    monkeypatch.setenv("DMSLOG_COMMIT", "testcommit")
    try:
        import build_pyz

        monkeypatch.setattr(build_pyz, "DIST", tmp_path)
        monkeypatch.setattr(build_pyz, "TARGET", tmp_path / "dmslog.pyz")
        yield build_pyz.build()
    finally:
        sys.path.remove(str(PACKAGING))


def test_smoke_ingest_runs_against_the_built_pyz(pyz):
    result = subprocess.run(
        [sys.executable, str(PACKAGING / "smoke_ingest.py"), sys.executable, "-I", str(pyz)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "page returns at least one row" in result.stdout
