"""Severity by error code at ingestion: the ERS ErrorConfiguration escalates a template severity."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from dmslog.ingest.pipeline import SeverityHook, run_ingest
from dmslog.store import create_case_db

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "knowledge" / "ers" / "SchneiderElectricData"
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("make_fixture", mf)
_spec.loader.exec_module(mf)

LOG_REL = "SchneiderElectricData/OASyS/Servers/Log/dnaApp_zone1/SVC/WfmAdapter_1111.log"


def build(root: Path, registry: bool) -> Path:
    if registry:
        shutil.copytree(FIXTURE, root / "SchneiderElectricData")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        mf.header(0, "DEBUGLOG", "Detected errors:") + "\nExampleCodeOne\nExampleCodeTwo",
        mf.header(10, "DEBUGLOG", "Something else happened."),
    ]
    log.write_bytes(mf.render(lines))
    return root


def severities(tmp_path: Path, registry: bool) -> dict[str, int]:
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, build(tmp_path / "coll", registry))
    rows = conn.execute("SELECT msg, sev FROM entry").fetchall()
    conn.close()
    return {msg: sev for msg, sev in rows}


def test_ers_error_code_escalates_template_severity(tmp_path):
    assert severities(tmp_path, registry=True)["Detected errors:"] == 1


def test_without_registry_the_template_severity_stands(tmp_path):
    assert severities(tmp_path, registry=False)["Detected errors:"] == 3


def test_resolutions_are_memoized_per_family_and_code():
    calls = []

    class Fake:
        def error(self, family, code):
            calls.append((family, code))
            return None

    hook = SeverityHook()
    hook._knowledge = Fake()
    hook.family = "AWFM"
    for _ in range(3):
        assert hook._resolve("X1") is None
    hook.family = "AONT"
    hook._resolve("X1")
    assert calls == [("AWFM", "X1"), ("AONT", "X1")]
