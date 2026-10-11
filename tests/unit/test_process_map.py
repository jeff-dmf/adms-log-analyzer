"""Checks for the curated process -> acronym map (ROADMAP 3.7)."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROCESS_MAP = ROOT / "src" / "dmslog" / "knowledge" / "curated" / "process_map.toml"
# The real pack never lives in the repository (ARQUITETURA D10): the cross-check below runs only where
# the maintainer points DMSLOG_TEST_REAL_PACK at a local copy.
REAL_PACK = os.environ.get("DMSLOG_TEST_REAL_PACK")
ADAPTERS = Path(REAL_PACK) / "adapters.json" if REAL_PACK else None
FAMILIES = ROOT / "src" / "dmslog" / "families.toml"


def _load(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _adapter_acronyms() -> set[str]:
    with ADAPTERS.open(encoding="utf-8-sig", newline="") as handle:
        return {row["acronym"] for row in json.load(handle)["adapters"]}


def test_process_map_is_valid_toml_with_known_sources():
    data = _load(PROCESS_MAP)
    assert data["process"], "no [[process]] entries"
    for entry in data["process"]:
        assert entry["source"] in {"vault", "curated"}, entry["name"]
        assert entry["note"].strip(), entry["name"]


def test_process_names_and_acronyms_are_unique():
    entries = _load(PROCESS_MAP)["process"]
    names = [e["name"] for e in entries]
    acronyms = [e["acronym"] for e in entries]
    assert len(names) == len(set(names))
    assert len(acronyms) == len(set(acronyms))


@pytest.mark.skipif(ADAPTERS is None, reason="DMSLOG_TEST_REAL_PACK not set (local check)")
def test_adapter_acronyms_exist_in_adapters_json():
    # BLTHost is a host log (empty adapter), not an adapter: exempt from this check.
    known = _adapter_acronyms()
    for entry in _load(PROCESS_MAP)["process"]:
        if entry["adapter"]:
            assert entry["acronym"] in known, entry["name"]


def test_in_scope_anchor_families_are_covered():
    """Every anchor (adapter) rule in families.toml is mapped or listed as unresolved."""
    data = _load(PROCESS_MAP)
    names = {e["name"] for e in data["process"]} | {u["name"] for u in data.get("unresolved", [])}
    rules = _load(FAMILIES)["rule"]
    for rule in rules:
        if rule.get("anchor"):
            assert rule["process"] in names, rule["process"]


def test_every_family_is_covered_or_out_of_scope():
    data = _load(PROCESS_MAP)
    covered = (
        {e.get("family", e["acronym"]) for e in data["process"]}
        | {u["family"] for u in data.get("unresolved", [])}
        | {o["family"] for o in data["out_of_scope"]}
    )
    families = {rule["family"] for rule in _load(FAMILIES)["rule"]}
    missing = sorted(families - covered)
    assert not missing, f"families without process map coverage: {missing}"


def test_out_of_scope_and_unresolved_do_not_overlap_mapped_acronyms():
    data = _load(PROCESS_MAP)
    acronyms = {e["acronym"] for e in data["process"]}
    for entry in data["out_of_scope"] + data.get("unresolved", []):
        assert entry["family"] not in acronyms, entry["family"]
