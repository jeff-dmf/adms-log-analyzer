"""Regression tests of ``business_error`` in the [[operation]] rules of AWFM, AAVL, AOSR and ACRM.

Entries are built by hand with the template codes of the packaged catalog (all data synthetic). The
threshold behavior is the point: an operation whose "Detected errors" block carries only Info codes has no
system event, so it must stay ``ok``; the block plus the system event is a ``business_error``; a fail code
or a failing end beats it (docs/notas/auditoria-business-error.md).
"""

# ruff: noqa: E501 - table of cases

from __future__ import annotations

import pytest

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.operations import OperationsDeriver
from dmslog.parse import load_catalog

KEY = "00000000-0000-4000-8000-0000000000aa"

# family, rule name (as built by the deriver), start code, end code, name field, "Detected errors" code,
# system event code, fail code that must beat the business error (or None)
CASES = [
    ("AWFM", "Changed crews", "WFM-010", "WFM-012", {"op": "Changed crews"}, "GEN-ERR-01", "GEN-EVT-01", "WFM-081"),
    ("AWFM", "Created crew model", "WFM-011", "WFM-012", {}, "GEN-ERR-01", "GEN-EVT-01", "WFM-081"),
    ("AAVL", "Example vehicle event", "AVL-001", "AVL-006", {"event": "Example vehicle event"}, "AVL-007", "AVL-008", None),
    ("AOSR", "GetIncidents", "OSR-001", "OSR-003", {}, "OSR-005", "OSR-007", "OSR-004"),
    ("AOSR", "GetIncidentUsagePoints", "OSR-011", "OSR-013", {}, "OSR-005", "OSR-007", "OSR-032"),
    ("AOSR", "ChangeIncidents", "OSR-014", "OSR-017", {}, "OSR-005", "OSR-007", "OSR-032"),
    ("AOSR", "ChangedIncidentHazards", "OSR-018", "OSR-021", {}, "OSR-005", "OSR-007", "OSR-022"),
    ("AOSR", "CreatedIncidentHazards", "OSR-026", "OSR-030", {}, "OSR-005", "OSR-007", "OSR-032"),
    ("ACRM", "GetTroubleTickets", "CRM-001", "CRM-003", {"op": "GetTroubleTickets"}, "GEN-ERR-01", "GEN-EVT-01", "CRM-004"),
    ("ACRM", "CreatedTroubleTickets", "CRM-010", "CRM-015", {"op": "CreatedTroubleTickets"}, "GEN-ERR-01", "GEN-EVT-01", "CRM-014"),
]  # fmt: skip
IDS = [f"{c[0]}-{c[1].replace(' ', '_')}" for c in CASES]
_CATALOG = load_catalog()


def run(family, codes, name_fields, *, end_code, fail_codes=()):
    """Feed start, ``codes`` (middle), optional fail codes and the end on one thread; return the events."""
    entries = []

    def add(code, ts, fields=None, keys=()):
        entries.append(
            StoredEntry(
                id=len(entries) + 1,
                stream_id=1,
                ts=ts,
                tid=1,
                pid=1,
                level=4,
                sev=4,
                tpl_code=code,
                tpl_class=None,
                fields=fields or {},
                msg="m",
                keys=tuple(keys),
            )  # fmt: skip
        )

    start_code, *middle = codes
    add(start_code, 0, name_fields, [("msgid", KEY)])
    for i, code in enumerate(middle, start=1):
        add(code, i * 5)
    for i, code in enumerate(fail_codes, start=1):
        add(code, 50 + i)
    add(end_code, 100, name_fields, [("msgid", KEY)])
    d = OperationsDeriver(family, _CATALOG.for_family(family, sensitive=False))
    return run_in_memory([d], entries)


@pytest.mark.parametrize(("family", "name", "start", "end", "nf", "err", "evt", "fail"), CASES, ids=IDS)
def test_normal_operation_is_ok(family, name, start, end, nf, err, evt, fail):
    (ev,) = run(family, [start], nf, end_code=end)
    assert (ev.name, ev.status) == (name, "ok")


@pytest.mark.parametrize(("family", "name", "start", "end", "nf", "err", "evt", "fail"), CASES, ids=IDS)
def test_info_only_codes_without_system_event_stay_ok(family, name, start, end, nf, err, evt, fail):
    (ev,) = run(family, [start, err], nf, end_code=end)
    assert (ev.name, ev.status) == (name, "ok")


@pytest.mark.parametrize(("family", "name", "start", "end", "nf", "err", "evt", "fail"), CASES, ids=IDS)
def test_system_event_without_detected_errors_stays_ok(family, name, start, end, nf, err, evt, fail):
    (ev,) = run(family, [start, evt], nf, end_code=end)
    assert (ev.name, ev.status) == (name, "ok")


@pytest.mark.parametrize(("family", "name", "start", "end", "nf", "err", "evt", "fail"), CASES, ids=IDS)
def test_detected_errors_plus_system_event_is_business_error(family, name, start, end, nf, err, evt, fail):
    (ev,) = run(family, [start, err, evt], nf, end_code=end)
    assert (ev.name, ev.status) == (name, "business_error")
    assert ev.duration_ms == 100


@pytest.mark.parametrize(("family", "name", "start", "end", "nf", "err", "evt", "fail"), CASES, ids=IDS)
def test_failure_beats_business_error(family, name, start, end, nf, err, evt, fail):
    if fail is None:
        pytest.skip("the rule has no fail code (the end is always a success)")
    # A technical failure code seen inside the operation (for CRM-004/OSR-022 a fail code, for the
    # end_fail ones a failing end) wins over the business error.
    fail_in_middle = fail in {"CRM-004", "CRM-014", "OSR-022"}
    if fail_in_middle:
        (ev,) = run(family, [start, err, evt], nf, end_code=end, fail_codes=[fail])
    else:
        (ev,) = run(family, [start, err, evt], nf, end_code=fail)
    assert (ev.name, ev.status) == (name, "fail")


def test_catalog_declares_business_error_for_crm():
    rules = _CATALOG.for_family("ACRM", sensitive=False).operations()
    assert [r["business_error"] for r in rules] == [["GEN-ERR-01", "GEN-EVT-01"]]
