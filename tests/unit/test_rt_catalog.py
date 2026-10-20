"""Guards of the real-time (RT) template files (issue: RT template mining). The repository is public: the RT
templates are written from message shapes with markers and synthetic examples, never from real log lines."""

from __future__ import annotations

import ipaddress
import re
from importlib import resources

import pytest

from dmslog.parse.templates import load_catalog

RT_PREFIXES = ("DFH", "DJB", "DCR", "DMD", "DOM", "RTL")
RT_FILES = (
    "_dmsrt.toml",
    "dmsrt.toml",
    "dmsfh.toml",
    "dmsjob.toml",
    "dmscore.toml",
    "dmsmodel.toml",
    "dmsoms.toml",
)
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
SYNTHETIC_GUID_RE = re.compile(
    r"^(?:0{8}-0{4}-4000-8000-0{9}[0-9a-f]{3}|([0-9a-f])\1{7}-\1{4}-\1{4}-\1{4}-\1{12})$", re.I
)
IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
DOC_NETS = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "10.0.0.0/24")
]
LONG_DIGITS_RE = re.compile(r"(?<![0-9a-fA-Fx])\d{10,}(?![0-9a-fA-F])")


def rt_files() -> list[str]:
    root = resources.files("dmslog.adapters")
    return [n for n in RT_FILES if root.joinpath(n).is_file()]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(check_examples=True)


def rt_families(catalog):
    sources = {f"{n}" for n in rt_files()}
    return [f for f in catalog.families.values() if f.source.rsplit("/", 1)[-1] in sources]


def test_the_rt_files_are_ascii_and_free_of_real_looking_values():
    root = resources.files("dmslog.adapters")
    for name in rt_files():
        text = root.joinpath(name).read_text(encoding="utf-8")
        assert text.isascii(), f"{name}: only ASCII"
        for line_no, line in enumerate(text.splitlines(), 1):
            for guid in GUID_RE.findall(line):
                assert SYNTHETIC_GUID_RE.match(guid), f"{name}:{line_no}: a GUID that is not synthetic"
            for ip in IPV4_RE.findall(line):
                octets = [int(o) for o in ip.split(".")]
                if all(o <= 255 for o in octets):
                    assert any(ipaddress.ip_address(ip) in net for net in DOC_NETS), (
                        f"{name}:{line_no}: real-looking IP"
                    )
            assert not LONG_DIGITS_RE.search(line), f"{name}:{line_no}: a number of 10 or more digits"


def test_every_rt_template_has_the_conventional_fields(catalog):
    for family in rt_families(catalog):
        for t in family.templates:
            where = f"{family.family} {t.code}"
            assert t.code.startswith(RT_PREFIXES), f"{where}: code prefix"
            assert t.sev is not None, f"{where}: sev"
            assert t.title, f"{where}: title"
            assert t.examples, f"{where}: at least one example"
            assert t.counter_examples, f"{where}: at least one counter-example"


def test_the_rt_families_extend_the_rt_library_first(catalog):
    for family in rt_families(catalog):
        if family.family.startswith("_"):
            continue
        assert tuple(family.extends[:2]) == ("_dmsrt", "_common"), family.family
