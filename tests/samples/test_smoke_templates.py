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


def _check(ok: object, label: str) -> None:
    """Assert with a FIXED message computed from booleans: no object holding sample output is asserted on."""
    assert ok, f"real-sample check failed: {label}"


@pytest.mark.skipif(not _find_samples(), reason="real extracted ERS sample folders are not available")
def test_template_smoke_runs() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=1800, cwd=ROOT, check=False
    )
    out = proc.stdout
    _check(proc.returncode == 0, "smoke_templates.py exit code 0")
    _check("COVERAGE" in out, "coverage section present")
    _check("WINDOW EFFECT" in out, "window section present")
    _check("source error" not in out, "no source error reported")
