"""Pytest wrapper for smoke_local.py: runs as a skipped test when real ERS samples are absent."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "local-samples"
SMOKE_SCRIPT = ROOT / "tools" / "smoke_local.py"
ENTERPRISE = re.compile(r"^ERS_CL_.*_\d{14}_Enterprise_.*\.zip$")
SENSITIVE = re.compile(r"^ERS_CL_.*_\d{14}_Sensitive_Enterprise_.*\.zip$")
MIN_REAL_SIZE = 1_000_000


def _has_real_samples() -> bool:
    """Return True if both Enterprise and Sensitive ERS samples exist and are real."""
    files = {}
    for path in sorted(SAMPLES.glob("*.zip")):
        if (
            ENTERPRISE.match(path.name)
            and path.stat().st_size >= MIN_REAL_SIZE
            and zipfile.is_zipfile(path)
        ):
            files["enterprise"] = path
        elif (
            SENSITIVE.match(path.name)
            and path.stat().st_size >= MIN_REAL_SIZE
            and zipfile.is_zipfile(path)
        ):
            files["sensitive"] = path
    return len(files) == 2


@pytest.mark.skipif(
    not _has_real_samples(),
    reason="No real Enterprise+Sensitive ERS zip pair in local-samples/",
)
def test_smoke_local() -> None:
    """Run the smoke local test script and verify it passes."""
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )

    # The script prints the table and either "Overall: PASS" or "Overall: FAIL"
    output = result.stdout + result.stderr

    # Verify the script ran successfully
    assert result.returncode == 0, f"smoke_local.py failed with return code {result.returncode}\n{output}"

    # Verify all acceptance criteria passed
    assert "Overall: PASS" in output, f"Acceptance criteria not met\n{output}"
