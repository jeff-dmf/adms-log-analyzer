"""Strict mode with every redaction category switched off keeps the default-deny net.

Switching every category off in the profile is not a way to export without anonymization: in strict
mode the free-text allowlist and the structural nets still replace what they cannot prove harmless.
The only way to get the text untouched is the explicit option not to anonymize (``anonymize=False``,
``--no-anonymize``). The standard mode is the pattern layer alone, so with every category off it
leaves the text untouched. Every value below is fictitious.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.report.redact import CATEGORIES, Profile, Redactor
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "export_case_all_off", ROOT / "tests/fixtures/export/build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_all_off"] = case_module
_spec.loader.exec_module(case_module)

NOW = datetime(2030, 10, 15, 11, 0, 0)
ALL_OFF = dict.fromkeys(CATEGORIES, False)
SENSITIVE_VALUES = [v for values in case_module.SENSITIVE.values() for v in values]
SECRET = "Crew1234"

# (case, text, payload): the synthetic secret must not survive strict mode with every category off
CASES = [
    ("labelled free text", f"Login failed for user: {SECRET} from the console", False),
    ("key=value field", f"operator={SECRET}; retry=3", False),
    ("XML payload", f"<Request><Operator>{SECRET}</Operator></Request>", True),
    ("JSON payload", f'{{"operator": "{SECRET}", "retry": 3}}', True),
]


def all_off(mode: str) -> Redactor:
    return Redactor(Profile(categories=dict(ALL_OFF), mode=mode))


@pytest.mark.parametrize("case,text,payload", CASES, ids=[c[0] for c in CASES])
def test_strict_with_every_category_off_still_hides_values(case, text, payload):
    redactor = all_off("strict")
    assert redactor.strict and redactor.active
    out = redactor.redact(text, payload=payload)
    assert SECRET not in out, out
    assert "<VALUE-" in out, out


@pytest.mark.parametrize("case,text,payload", CASES, ids=[c[0] for c in CASES])
def test_standard_with_every_category_off_leaves_the_text_untouched(case, text, payload):
    redactor = all_off("standard")
    assert not redactor.strict and not redactor.active
    assert redactor.redact(text, payload=payload) == text


@pytest.fixture(scope="module")
def case_db(tmp_path_factory):
    return case_module.build_export_case(tmp_path_factory.mktemp("all-off") / "case.sqlite")


def _export(case_db, *, anonymize, profile=None):
    conn = connect(case_db, readonly=True)
    try:
        result = build_report(
            conn,
            Selection(events=(1,), ids=(7,), filter={"source": [1, 2]}),
            ExportOptions(anonymize=anonymize, profile=profile, include_payloads=True, context=2),
            now=NOW,
            version="0.0.0-test",
        )
    finally:
        conn.close()
    return "".join(part.text for part in result.parts)


def test_strict_export_with_every_category_off_leaks_no_planted_value(case_db):
    text = _export(case_db, anonymize=True, profile=Profile(categories=dict(ALL_OFF)))
    leaked = [v for v in SENSITIVE_VALUES if v.casefold() in text.casefold()]
    assert leaked == []


def test_only_the_explicit_option_exports_the_text_untouched(case_db):
    text = _export(case_db, anonymize=False)
    missing = [v for v in SENSITIVE_VALUES if v not in text]
    assert missing == []


# -- automatic exceptions with every category off ------------------------------------------------


def _case_with_catalog_code(tmp_path: Path, code: str) -> Path:
    """The fixture case with ``code`` defined in its catalog, listed in the event and in a message."""
    db = case_module.build_export_case(tmp_path / "catalog-code.sqlite")
    conn = connect(db)
    with conn:
        conn.execute(
            "INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
            " VALUES (2, ?, 'AWFM', 'error', 2, 'x', 1, 'v')",
            (code,),
        )
        conn.execute("UPDATE event SET detail = ? WHERE id = 1", (json.dumps({"error_codes": [code]}),))
        conn.execute("UPDATE entry SET msg = ? WHERE id = 1", (f"Start SendWorks with code {code}",))
    conn.close()
    return db


@pytest.mark.parametrize("mode", ["standard", "strict"])
def test_catalog_code_listed_as_a_term_with_every_category_off(tmp_path, mode):
    # standard is unchanged: with every category off the probe redacts nothing, so every catalog code is
    # an automatic exception and wins over a user term; strict keeps only what its nets prove harmless
    db = _case_with_catalog_code(tmp_path, SECRET)
    profile = Profile(categories=dict(ALL_OFF), terms_word=(SECRET,), mode=mode)
    text = _export(db, anonymize=True, profile=profile)
    if mode == "standard":
        assert SECRET in text and "<TERM-" not in text
    else:
        assert SECRET not in text


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_alone_turns_the_probe_nets_on(category):
    # the export's probe relies on it: before strict kept its nets with every category off, any category
    # on meant a pattern rule, hence the nets; one category on must still mean an active probe
    only = Profile(categories={c: c == category for c in CATEGORIES}, payload_names=())
    redactor = Redactor(only)
    # strict is always active, so also pin the premise itself: the category contributes a finder
    assert redactor._finders, f"category {category!r} has no finder"
    assert redactor.active
