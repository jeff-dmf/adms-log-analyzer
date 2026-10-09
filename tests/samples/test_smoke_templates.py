"""Pytest wrapper for tools/smoke_templates.py: skipped automatically when the sample folders are absent."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "smoke_templates.py"


def _find_samples() -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location("smoke_templates", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_samples()


@pytest.mark.skipif(not _find_samples(), reason="real extracted ERS sample folders are not available")
def test_template_smoke_runs() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=1800, cwd=ROOT, check=False
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "COVERAGE" in proc.stdout
    assert "WINDOW EFFECT" in proc.stdout
    assert "source error" not in proc.stdout
