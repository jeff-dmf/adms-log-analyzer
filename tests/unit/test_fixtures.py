"""Fixture determinism, leakage scan and ERS zip builder checks.

Synthetic GUID rule: any GUID inside a fixture must start with ``00000000-0000-4000-8000-``
(``make_fixture.synth_guid``). Anything else is treated as potentially real data.
"""

import importlib.util
import ipaddress
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "logs"

_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)

GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")
LONG_DIGITS_RE = re.compile(r"\d{9,}")
ALLOWED_NETS = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8")
]


def _all_texts():
    for rel, data in mf.build_all().items():
        yield rel, data.decode("utf-8")


def test_fixtures_match_generator_bit_for_bit():
    assert mf.check_all(FIXTURE_DIR) == []


def test_generator_is_deterministic():
    assert mf.build_all() == mf.build_all()
    assert mf.generate_perf_log(50_000) == mf.generate_perf_log(50_000)


def test_no_unexpected_files_in_fixture_dir():
    expected = set(mf.build_all()) | {".gitattributes"}
    found = {p.relative_to(FIXTURE_DIR).as_posix() for p in FIXTURE_DIR.rglob("*") if p.is_file()}
    assert found == expected


@pytest.mark.parametrize("rel", sorted(mf.build_all()))
def test_no_real_data_patterns(rel):
    text = mf.build_all()[rel].decode("utf-8")
    for guid in GUID_RE.findall(text):
        assert guid.startswith(mf.GUID_PREFIX), f"non synthetic GUID {guid}"
    assert not EMAIL_RE.findall(text), "e-mail address found"
    assert not LONG_DIGITS_RE.findall(text), "long digit sequence found"
    for match in IPV4_RE.finditer(text):
        octets = [int(g) for g in match.groups()]
        if all(o <= 255 for o in octets):
            addr = ipaddress.ip_address(".".join(map(str, octets)))
            assert any(addr in net for net in ALLOWED_NETS), f"non reserved IP {addr}"


def test_scan_detects_leaks():
    assert GUID_RE.findall("id 11111111-2222-4333-8444-555555555555")
    assert EMAIL_RE.findall("mail ana@corp.com")
    assert LONG_DIGITS_RE.findall("id 1234567890")


def test_edge_case_bytes():
    fx = mf.build_all()
    assert fx["bom_crlf.log"].startswith(mf.BOM) and b"\r\n" in fx["bom_crlf.log"]
    assert b"\r" not in fx["lf_only.log"] and not fx["lf_only.log"].startswith(mf.BOM)
    assert b"[TID=" not in b"".join(
        line for line in fx["no_tid.log"].splitlines(True) if b"PSAttach" in line
    )
    assert b" - \tManaged BLT" in fx["leading_tab.log"]
    assert fx["big_dump.log"].count(b"\n") >= 1600 and len(fx["big_dump.log"]) < 32_000
    assert not fx["truncated.log"].endswith(b"\n")
    assert not fx["orphans.log"].startswith(b"20")
    assert b"<script>alert(1)</script>" in fx["script_literal.log"]
    assert b"\r" in fx["utf8_cr.log"].replace(b"\r\n", b"") and "caf\u00e9".encode() in fx["utf8_cr.log"]


def test_perf_log_size_and_shape():
    data = mf.generate_perf_log()
    assert 2_000_000 <= len(data) < 2_100_000
    assert data.startswith(mf.BOM)


def test_ers_zip_enterprise(tmp_path):
    path = mf.build_ers_zip(tmp_path, adapters=("WfmAdapter", "OntAdapter"))
    assert path.name == f"ERS_CL_{mf.SYNTH_GUID.upper()}_20300115103000_Enterprise_synthetic.zip"
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
    assert f"{mf.ZIP_ROOT}/WfmAdapter_1111.log" in names
    assert f"{mf.ZIP_ROOT}/WfmAdapter_1111.log.0" in names
    assert f"{mf.ZIP_ROOT}/OntAdapter_2222.log" in names
    assert f"{mf.LOG_ROOT}/NMC.log" in names and f"{mf.LOG_ROOT}/oasErrLog.log" in names
    assert any(n.endswith(".evtx") for n in names)
    assert any("/ADMS/configuration/DMSIntegration/" in n and n.endswith(".xml") for n in names)
    assert not any(".sensitive." in n for n in names)


def test_ers_zip_sensitive(tmp_path):
    path = mf.build_ers_zip(tmp_path, kind="sensitive", adapters=("WfmAdapter",))
    assert path.name.startswith("ERS_CL_") and "_Sensitive_Enterprise_" in path.name
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert f"{mf.ZIP_ROOT}/WfmAdapter_1111.sensitive.log" in names
    assert all(".sensitive." in n for n in names)


def test_ers_zip_is_deterministic_and_rejects_bad_kind(tmp_path):
    a = mf.build_ers_zip(tmp_path / "a").read_bytes()
    b = mf.build_ers_zip(tmp_path / "b").read_bytes()
    assert a == b
    with pytest.raises(ValueError):
        mf.build_ers_zip(tmp_path, kind="other")
