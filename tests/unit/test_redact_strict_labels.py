"""Strict mode: identifier words glued to digits, and values behind a label whose category is off."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.report.redact import CATEGORIES, Profile, Redactor
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "export_case_strict_labels", ROOT / "tests/fixtures/export/build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_strict_labels"] = case_module
_spec.loader.exec_module(case_module)

NOW = datetime(2030, 10, 15, 11, 0, 0)

# category -> (text, value that must not appear when the category is off in strict mode)
CASES = {
    "guid": ("message 3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c done", "3f2a9c1e"),
    "gid": ("crew GID: 1234567890123456 moved", "1234567890123456"),
    "ticket": ("opened INC0045123 today", "INC0045123"),
    "mrid": ("Crew mRID: CREW7788 assigned", "CREW7788"),
    "plate": ("vehicle QRS4T56 left", "QRS4T56"),
    "host": ("connect hostname=dmsprod01 now", "dmsprod01"),
    "ip": ("peer 10.20.30.40 answered", "10.20.30.40"),
    "user": ("request by user: jsilva ok", "jsilva"),
    "url": ("call https://esb.example.com:8443/Svc/Wfm.svc now", "esb.example.com"),
    "path": ("file C:\\Data\\acme\\x.log opened", "acme"),
    "email": ("mail maria.souza@example.com sent", "maria.souza"),
    "phone": ("call +55 21 99876-5432 back", "99876-5432"),
    "person": ("Address: Rua das Acacias 45", "Acacias"),
}
RELATED = {"url": ("host",)}  # the host inside a URL is a host too

# identifier words that are also catalog words, glued to a short number
LABEL_WORDS = ["Crew", "Server", "Node", "Meter", "Feeder", "User", "Token", "Inc", "Prob", "Substation"]
DIGITS = ["12", "123", "1234"]


def strict(**flags: bool) -> Redactor:
    return Redactor(Profile(categories=flags, mode="strict"))


def test_every_category_has_a_case():
    assert set(CASES) == set(CATEGORIES)


@pytest.mark.parametrize("digits", DIGITS)
@pytest.mark.parametrize("word", LABEL_WORDS)
def test_identifier_word_glued_to_digits_is_replaced(word, digits):
    token = f"{word.upper()}{digits}"
    out = strict().redact(f"seen {token} here")
    assert token not in out and digits not in out, out


@pytest.mark.parametrize(("word", "category"), [("Server", "host"), ("Crew", "mrid"), ("User", "user")])
def test_identifier_word_glued_to_digits_is_replaced_with_its_category_off(word, category):
    token = f"{word}42"
    out = strict(**{category: False}).redact(f"seen {token} here")
    assert token not in out and "<VALUE-" in out, out


def test_catalog_compounds_with_digits_still_pass():
    out = strict().redact("Communication2 and WFM-081 are fine")
    assert "Communication2" in out and "WFM-081" in out


@pytest.mark.parametrize("category", CATEGORIES)
def test_strict_category_off_still_replaces_the_value(category):
    text, value = CASES[category]
    flags = {c: c != category and c not in RELATED.get(category, ()) for c in CATEGORIES}
    out = strict(**flags).redact(text)
    assert value not in out, out
    assert "<VALUE-" in out, out


@pytest.mark.parametrize(
    ("category", "text"),
    [("mrid", "mRID: ZZ-77-ab"), ("user", "user: jsilva"), ("gid", "GID: 12345")],
)
def test_labelled_value_with_its_category_off_becomes_value(category, text):
    out = strict(**{category: False}).redact(text)
    assert "<VALUE-1>" in out, out
    assert text.split(": ")[1] not in out


def test_standard_mode_still_lets_a_labelled_value_through_when_off():
    profile = Profile(categories={"mrid": False}, mode="standard")
    out = Redactor(profile).redact("Crew mRID: CREW7788 assigned")
    assert "CREW7788" in out


@pytest.mark.parametrize("category", CATEGORIES)
def test_strict_export_with_a_category_off_leaks_no_fixture_value(tmp_path, category):
    db = case_module.build_export_case(tmp_path / f"s-{uuid.uuid4().hex[:8]}.sqlite")
    conn = connect(db, readonly=True)
    flags = {c: c != category for c in CATEGORIES}
    try:
        result = build_report(
            conn,
            Selection(ids=(4, 7), events=(1,)),
            ExportOptions(
                anonymize=True, include_payloads=True, profile=Profile(categories=flags, mode="strict")
            ),
            now=NOW,
        )
    finally:
        conn.close()
    text = "".join(p.text for p in result.parts)
    for values in case_module.SENSITIVE.values():
        for value in values:
            assert value not in text, (category, value)
