"""[[operation]] rules of AAVL, AOSR, ASMN and ASSS, through OperationsDeriver built from the real catalog.

Scenarios live in ``tests/fixtures/derive_rules/avl_osr_smn_sss/*.json`` (100% synthetic lines
``[ts_ms, tid, msg]``); each line is classified by the family's real templates before it is fed to the
deriver, so the codes, keys and fields are the ones the ingest produces.
"""

# ruff: noqa: E501 - fixture-driven test

import json
from pathlib import Path

import pytest

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.operations import OperationsDeriver
from dmslog.parse.templates import load_catalog

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "derive_rules" / "avl_osr_smn_sss"
FAMILIES = {"AAVL": 1, "AOSR": 5, "ASMN": 3, "ASSS": 1}  # family -> number of [[operation]] rules

_CATALOG = load_catalog()


def load_scenarios():
    params = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for sc in data["scenarios"]:
            params.append(pytest.param(data["family"], sc, id=f"{path.stem}-{sc['name']}"))
    return params


def entries_of(family, lines):
    tset = _CATALOG.for_family(family, sensitive=False)
    out = []
    for i, (ts, tid, msg) in enumerate(lines, start=1):
        m = tset.classify_text(msg)
        assert m is not None, f"line not classified: {msg[:60]}"
        out.append(
            StoredEntry(
                id=i,
                stream_id=1,
                ts=ts,
                tid=tid,
                pid=1,
                level=None,
                sev=4,
                tpl_code=m.code,
                tpl_class=m.cls,
                fields=dict(m.fields),
                msg=msg,
                keys=tuple(m.keys),
            )  # fmt: skip
        )
    return out


def derive(family, lines, **kw):
    deriver = OperationsDeriver(family, _CATALOG.for_family(family, sensitive=False), **kw)
    return deriver, run_in_memory([deriver], entries_of(family, lines))


@pytest.mark.parametrize(("family", "scenario"), load_scenarios())
def test_scenarios(family, scenario):
    deriver, events = derive(family, scenario["lines"])
    expected = scenario["expect"]
    assert len(events) == len(expected), [(e.name, e.status) for e in events]
    for ev, exp in zip(events, expected, strict=True):
        assert ev.kind == "operation"
        for field, value in exp.items():
            actual = ev.detail.get(field) if field == "fail_how" else getattr(ev, field)
            assert actual == value, (field, ev.name, ev.status)
    assert deriver.errors == 0


@pytest.mark.parametrize("family", FAMILIES)
def test_each_family_declares_its_operations(family):
    rules = _CATALOG.for_family(family, sensitive=False).operations()
    assert len(rules) == FAMILIES[family]
    for rule in rules:
        assert rule.get("name") or rule.get("name_field")
        assert rule.get("start")
        assert rule.get("end") or rule.get("end_ok") or rule.get("end_fail")


def test_truncated_stream_closes_open_operation_as_truncated():
    lines = [[1000, 3, "[SendWorks] Sending following Work Request: WR 90."]]
    _, events = derive("ASMN", lines, truncated=True)
    assert [(e.name, e.status) for e in events] == [("SendWorks", "truncated")]


def test_new_start_on_same_thread_truncates_the_older_operation():
    lines = [
        [1000, 3, "[SendWorks] Sending following Work Request: WR 90."],
        [1100, 3, "[SendWorks] Sending following Work Request: WR 91."],
        [1150, 3, "[SendWorks] Sending Work Request finished successfully."],
    ]
    deriver, events = derive("ASMN", lines)
    assert [(e.status, e.key) for e in events] == [("truncated", "90"), ("ok", "91")]
    assert deriver.overlaps == 1
