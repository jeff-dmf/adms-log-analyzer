"""Checks for the curated template hints (ROADMAP 3.8)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from dmslog.knowledge.resolve import Knowledge
from dmslog.parse.templates import load_catalog

PACK = Path(__file__).resolve().parents[2] / "src" / "dmslog" / "knowledge" / "pack"
HINTS = Path(__file__).resolve().parents[2] / "src" / "dmslog" / "knowledge" / "curated" / "hints.toml"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return tomllib.loads(HINTS.read_text(encoding="utf-8-sig"))["hint"]


def test_hints_not_empty(rows):
    assert len(rows) >= 40


def test_keys_are_known(rows):
    for row in rows:
        assert set(row) <= {"code", "text", "params", "note"}, row["code"]
        assert {"code", "text"} <= set(row)


def test_codes_exist_in_catalog(rows):
    catalog = load_catalog()
    for row in rows:
        assert catalog.resolve(row["code"]) is not None, row["code"]


def test_no_duplicates(rows):
    catalog = load_catalog()
    canonical = [catalog.resolve(r["code"]) for r in rows]
    assert len(canonical) == len(set(canonical))
    assert len({r["code"] for r in rows}) == len(rows)


def test_params_exist_in_pack(rows):
    data = json.loads((PACK / "parameters.json").read_text(encoding="utf-8"))
    names = {p["name"] for p in data["parameters"]}
    for row in rows:
        for param in row.get("params", []):
            assert param in names, (row["code"], param)


def test_notes_exist_in_pack(rows):
    adapters = json.loads((PACK / "adapters.json").read_text(encoding="utf-8"))["adapters"]
    notes = {a[k] for a in adapters for k in ("config_note", "index_note") if a.get(k)}
    for row in rows:
        if "note" in row:
            assert row["note"] in notes, row["code"]


def test_text_is_non_empty_and_short(rows):
    for row in rows:
        assert row["text"].strip(), row["code"]
        assert len(row["text"]) <= 300, (row["code"], len(row["text"]))


def test_knowledge_api_returns_hint():
    hint = Knowledge().hint("WFM-080")
    assert hint is not None
    assert "WaitForCRSCallback" in hint.params
