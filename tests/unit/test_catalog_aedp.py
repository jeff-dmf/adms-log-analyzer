"""Catalog of family AEDP (CustomAdapterChangeIncidentDevice): examples, precedence, keys, operation."""

# ruff: noqa: E501 - synthetic log lines are kept on one line

from importlib import resources

import pytest

from dmslog.parse.templates import Catalog, load_catalog, load_texts

CODES = {"AEDP-001", "AEDP-002", "AEDP-003"}
MID = "00000000-0000-4000-8000-0000000000aa"
PREFIX = "[ReceiveIncidentDeviceService] - Processing message with Id"


@pytest.fixture(scope="module")
def cat() -> Catalog:
    files = {n: resources.files("dmslog.adapters").joinpath(n).read_bytes() for n in ("aedp.toml", "_common.toml")}
    return load_texts(files, check_examples=True)


def test_codes_and_examples(cat: Catalog) -> None:
    fam = cat.families["AEDP"]
    assert {t.code for t in fam.templates} == CODES
    assert fam.extends == ("_common",)
    assert cat.check_examples() == []
    for t in fam.templates:
        assert t.examples and t.counter_examples, t.code
        for ex in t.examples:
            got = cat.for_family("AEDP", sensitive=False).classify_text(ex.msg, ex.cont)
            assert got is not None and got.code == t.code, t.code


@pytest.mark.parametrize(
    ("tail", "code", "cls"),
    [("started.", "AEDP-001", "start"), ("successfully finished.", "AEDP-002", "end"), ("failed. ", "AEDP-003", "error")],
)
def test_lifecycle_markers_key_on_message_id(cat: Catalog, tail: str, code: str, cls: str) -> None:
    got = cat.for_family("AEDP", sensitive=False).classify_text(f"{PREFIX} '{MID}' {tail}", None)
    assert got is not None and got.code == code and got.cls == cls
    assert got.keys == [("msgid", MID)]


def test_failed_continuation_is_read_as_stack(cat: Catalog) -> None:
    cont = "Reason: 'There is no device with ID: 7 in the ADMS model.'. \nFull exception: 'System.Exception: x\n   at Example.A.B()"
    got = cat.for_family("AEDP", sensitive=False).classify_text(f"{PREFIX} '{MID}' failed. ", cont)
    assert got is not None and got.code == "AEDP-003"
    assert got.fields.get("frame") == "Example.A.B"


def test_other_shapes_fall_to_common(cat: Catalog) -> None:
    got = cat.for_family("AEDP", sensitive=False).classify_text("[Communication2] - Client-side statistics:", None)
    assert got is not None and got.code.startswith("GEN-")


def test_operation_rule_in_default_catalog() -> None:
    fam = load_catalog().families["AEDP"]
    ops = [o for o in fam.operations if o.get("name") == "ReceiveIncidentDevice"]
    assert len(ops) == 1
    assert (ops[0]["start"], ops[0]["end"], ops[0]["fail"]) == (["AEDP-001"], ["AEDP-002"], ["AEDP-003"])
