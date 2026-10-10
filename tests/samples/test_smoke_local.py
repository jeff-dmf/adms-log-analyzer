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
        if ENTERPRISE.match(path.name) and path.stat().st_size >= MIN_REAL_SIZE and zipfile.is_zipfile(path):
            files["enterprise"] = path
        elif SENSITIVE.match(path.name) and path.stat().st_size >= MIN_REAL_SIZE and zipfile.is_zipfile(path):
            files["sensitive"] = path
    return len(files) == 2


def _check(ok: object, label: str) -> None:
    """Assert with a FIXED message computed from booleans: no object holding sample output is asserted on."""
    assert ok, f"real-sample check failed: {label}"


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

    exit_ok = result.returncode == 0
    passed = "Overall: PASS" in output
    _check(exit_ok, "smoke_local.py exit code 0")
    _check(passed, "all acceptance criteria passed")


def _load_smoke_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("smoke_local_under_test", SMOKE_SCRIPT)
    _check(spec is not None and spec.loader is not None, "module spec")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ingest_failure_message_never_echoes_names() -> None:
    """Error formatting uses neutral labels: a synthetic zip name and stderr text are never echoed."""
    mod = _load_smoke_module()
    fake_name = "ERS_CL_AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE_20200101000000_Enterprise_secretdesc.zip"
    message = mod._ingest_failure("zip 1 of 2 (enterprise)", 3)
    _check(fake_name not in message and "secretdesc" not in message, "name not echoed")
    _check("exit code 3" in message and "zip 1 of 2 (enterprise)" in message, "neutral label and code")


def test_corrupt_zip_member_name_never_appears_in_diagnostics(tmp_path, capsys, monkeypatch) -> None:
    """A CRC error inside a zip must not leak the member name, through the helper or the top-level wrapper."""
    import zipfile
    from types import SimpleNamespace

    mod = _load_smoke_module()
    member = "dnaApp_distinctivezone/DISTINCTIVE_member_name.log"
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(member, b"line one\nline two\n")
    raw = bytearray(path.read_bytes())
    at = raw.index(b"line one")
    raw[at] ^= 0xFF  # corrupt the stored data: reading raises BadZipFile with the member name
    path.write_bytes(bytes(raw))

    lines, error = mod._zip_physical_lines(path, [SimpleNamespace(member=member)], "zip 1 of 2")
    _check(error is not None and lines == 0, "read error reported")
    _check("DISTINCTIVE" not in error and "distinctivezone" not in error, "member name not echoed")
    _check("BadZipFile" in error and "zip 1 of 2" in error, "class name and neutral label")

    def boom():
        raise ValueError(member)

    monkeypatch.setattr(mod, "_main", boom)
    code = mod.main()
    out = capsys.readouterr()
    _check(code == 1, "unexpected exception exits non-zero")
    _check("DISTINCTIVE" not in out.out + out.err and "ValueError" in out.out, "wrapper prints class only")
