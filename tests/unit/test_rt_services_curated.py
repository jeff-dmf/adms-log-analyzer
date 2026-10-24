"""Curated RT services (knowledge/curated/rt_services.toml) against families.toml and the template catalog."""

import re
import tomllib
from importlib import resources

import pytest

from dmslog.parse.templates import load_catalog

FAMILY_PREFIX = {
    "DMSFH": "DFH",
    "DMSJOB": "DJB",
    "DMSCORE": "DCR",
    "DMSMODEL": "DMD",
    "DMSOMS": "DOM",
    "PSENGINE": "PSE",
    "ARBIP": "ARB",
    "NMC": "NMC",
    "ERS": "ERS",
}
# Families whose rule in families.toml lists several services in one alternation.
MULTI_FAMILIES = ("DMSJOB", "DMSCORE", "DMSMODEL", "DMSOMS")
SINGLE_PROCESS = {"PSENGINE": "PSengine", "ARBIP": "arbIP", "NMC": "NMC", "ERS": "ERS"}
GROUPS = {"core", "model_db", "model_management", "calculation", "oms", "platform", "tool"}
KEYS = {"job_id", "jms_job", "fh_pid", "new_pid", "gid", "incident_gid", "inc", "exc_type"}
SERVICE_FIELDS = {"process", "family", "group", "codes", "keys", "related", "vault"}
VAULT_FIELDS = {"doc", "note", "section"}
RANGE = re.compile(r"^([A-Z]{3})-(S?)(\d{3})(?:\.\.S?(\d{3}))?$")
SECTION = re.compile(r"^\d+(\.\d+)*$")
NOTE = re.compile(r"^[A-Za-z0-9 .]+\.md$")


def _read(package: str, name: str) -> dict:
    return tomllib.loads(resources.files(package).joinpath(name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> dict:
    return _read("dmslog.knowledge.curated", "rt_services.toml")


@pytest.fixture(scope="module")
def services(doc) -> list[dict]:
    return doc["service"]


@pytest.fixture(scope="module")
def families() -> dict:
    return _read("dmslog", "families.toml")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _parse_range(text: str) -> tuple[str, str, int, int]:
    m = RANGE.match(text)
    assert m, f"bad range {text!r}"
    lo = int(m.group(3))
    hi = int(m.group(4)) if m.group(4) else lo
    assert lo <= hi, text
    return m.group(1), m.group(2), lo, hi


def _in_range(code: str, rng: str) -> bool:
    m = re.fullmatch(r"([A-Z]{3})-(S?)(\d+)", code)
    assert m, code
    pfx, s, lo, hi = _parse_range(rng)
    return (m.group(1), m.group(2)) == (pfx, s) and lo <= int(m.group(3)) <= hi


def _expected_processes(families: dict) -> dict[str, str]:
    """Process (pool name for pools) -> family, derived from the rules and pools of families.toml."""
    out: dict[str, str] = {}
    for rule in families["rule"]:
        fam = rule["family"]
        if fam in MULTI_FAMILIES:
            m = re.search(r"\(\?P<process>\(\?:([^)]+)\)\)", rule["pattern"])
            assert m, fam
            for name in m.group(1).split("|"):
                out[name] = fam
    for pool in families["pool"]:
        out[pool["name"]] = pool["family"]
    for fam, name in SINGLE_PROCESS.items():
        out[name] = fam
    return out


def test_header_version(doc):
    assert doc["version"] == 1
    assert set(doc) == {"version", "service"}


def test_fields_are_closed(services):
    for s in services:
        assert set(s) <= SERVICE_FIELDS, s["process"]
        assert {"process", "family", "group", "codes", "keys", "vault"} <= set(s), s["process"]
        for rel in s.get("related", []):
            assert set(rel) == {"process", "via"}, s["process"]
        v = s["vault"]
        if v != "absent":
            assert isinstance(v, dict) and set(v) == VAULT_FIELDS, s["process"]
            assert NOTE.match(v["note"]) and SECTION.match(v["section"]), s["process"]
            assert v["note"].startswith(v["doc"] + " "), s["process"]


def test_no_duplicates_and_all_processes_covered(services, families):
    names = [s["process"] for s in services]
    assert len(names) == len(set(names))
    expected = _expected_processes(families)
    assert set(names) == set(expected)
    assert len(names) == 33
    for s in services:
        assert s["family"] == expected[s["process"]], s["process"]


def test_vault_sections_unique(services):
    seen = [(s["vault"]["note"], s["vault"]["section"]) for s in services if s["vault"] != "absent"]
    assert len(seen) == len(set(seen))


def test_groups_and_keys_in_closed_lists(services):
    for s in services:
        assert s["group"] in GROUPS, s["process"]
        assert set(s["keys"]) <= KEYS, s["process"]
        assert len(set(s["keys"])) == len(s["keys"]), s["process"]


def test_related_exists_and_via_is_a_key(services):
    names = {s["process"] for s in services}
    for s in services:
        for rel in s.get("related", []):
            assert rel["process"] in names, (s["process"], rel)
            assert rel["process"] != s["process"]
            assert rel["via"] == "handoff" or rel["via"] in s["keys"], (s["process"], rel)


def test_process_map_is_disjoint(services):
    adapters = {p["name"] for p in _read("dmslog.knowledge.curated", "process_map.toml")["process"]}
    assert not adapters & {s["process"] for s in services}


def test_codes_match_catalog(services, catalog):
    by_family: dict[str, list[dict]] = {}
    for s in services:
        by_family.setdefault(s["family"], []).append(s)
    assert set(by_family) == set(FAMILY_PREFIX)
    for fam, group in by_family.items():
        assert fam in catalog.families, fam
        prefix = FAMILY_PREFIX[fam]
        templates = catalog.families[fam].templates
        owner: dict[str, str] = {}
        for s in group:
            assert s["codes"], s["process"]
            hit: list = []
            for rng in s["codes"]:
                pfx, _, _, _ = _parse_range(rng)
                assert pfx == prefix, (s["process"], rng)
                matched = [t for t in templates if _in_range(t.code, rng)]
                assert matched, f"{s['process']}: range {rng} has no template"
                hit += matched
            for t in hit:
                assert t.code not in owner, f"{t.code} in {owner[t.code]} and {s['process']}"
                owner[t.code] = s["process"]
            union = {k.type for t in hit for k in t.keys}
            assert set(s["keys"]) == union, f"{s['process']}: keys {sorted(s['keys'])} != {sorted(union)}"
        # Every template of the family sits in exactly one service range.
        assert {t.code for t in templates} == set(owner), fam
        if fam in MULTI_FAMILIES:
            assert len(group) > 1
