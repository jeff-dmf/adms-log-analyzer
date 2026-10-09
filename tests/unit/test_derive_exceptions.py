"""ExceptionDeriver: one event per occurrence, normalized signature (ARQUITETURA 6.5)."""

# ruff: noqa: E501 - synthetic log lines are kept on one line

import json
from pathlib import Path

import pytest

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.exceptions import MAX_FRAMES, UNKNOWN_TYPE, ExceptionDeriver

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "derive_exceptions"


def make(i=1, *, msg="x", cont="", tpl_code=None, tpl_class=None, fields=None, ts=1000, tid=7, sev=4):
    return StoredEntry(
        id=i, stream_id=1, ts=ts, tid=tid, pid=1, level=None, sev=sev, tpl_code=tpl_code,
        tpl_class=tpl_class, fields=fields or {}, msg=msg, cont=cont,
    )  # fmt: skip


def derive(*entries):
    return run_in_memory([ExceptionDeriver("smn")], entries)


def stack(exc_line, *frames):
    return (
        exc_line + "\n" + "".join(f"   at Example.M{i}() in F:\\PD\\123\\1\\E.cs:line {i}\n" for i in frames)
    )


def load_cases():
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    return [pytest.param(c, id=c["name"]) for c in cases]


@pytest.mark.parametrize("case", load_cases())
def test_fixture_cases(case):
    e = case["entry"]
    entry = make(msg=e["msg"], cont=e.get("cont", ""), tpl_code=e["tpl_code"], tpl_class=e["tpl_class"],
                 fields=e["fields"], sev=e["sev"])  # fmt: skip
    (ev,) = derive(entry)
    assert ev.kind == "exception"
    assert ev.key == ev.detail["signature"] == case["expect"]["signature"]
    for k, v in case["expect"].items():
        assert ev.detail.get(k) == v, k
    assert ev.sev == e["sev"]
    assert ev.start_entry_id == ev.end_entry_id == entry.id
    assert ev.name == case["expect"]["type"]


def test_same_exception_different_volatile_parts_same_signature():
    a = make(
        1,
        tpl_class="error",
        cont=stack(
            "System.InvalidOperationException: Message 11111111-2222-4333-8444-555555555555 failed (code 17) 'abc'",
            1,
            2,
        ),
    )
    b = make(
        2,
        tpl_class="error",
        cont=stack(
            "System.InvalidOperationException: Message 99999999-2222-4333-8444-555555555555 failed (code 99) 'zzz'",
            3,
        ),
    )
    ea, eb = derive(a, b)
    assert ea.key == eb.key
    assert ea.key == "System.InvalidOperationException: Message <guid> failed (code <n>) <s>"
    assert ea.detail["n_frames"] == 2 and eb.detail["n_frames"] == 1


def test_frames_are_not_part_of_the_signature():
    a = make(1, tpl_class="error", cont=stack("System.IO.IOException: disk", 1, 2, 3))
    b = make(2, tpl_class="error", cont=stack("System.IO.IOException: disk", 9))
    assert derive(a, b)[0].key == derive(a, b)[1].key


def test_different_type_different_signature():
    a = make(1, tpl_class="error", cont=stack("System.IO.IOException: same text", 1))
    b = make(2, tpl_class="error", cont=stack("System.TimeoutException: same text", 1))
    ea, eb = derive(a, b)
    assert ea.key != eb.key


def test_different_message_different_signature():
    a = make(1, tpl_class="error", cont=stack("System.IO.IOException: disk full", 1))
    b = make(2, tpl_class="error", cont=stack("System.IO.IOException: access denied", 1))
    ea, eb = derive(a, b)
    assert ea.key != eb.key


def test_frames_capped_at_eight():
    e = make(tpl_class="error", cont=stack("System.IO.IOException: x", *range(20)))
    (ev,) = derive(e)
    assert len(ev.detail["frames"]) == MAX_FRAMES == 8
    assert ev.detail["n_frames"] == 20
    assert ev.detail["frames"][0].startswith("at Example.M0()")


def test_parser_fields_are_used():
    e = make(
        tpl_class="error",
        msg="m",
        fields={
            "exc_type": "Example.CustomException",
            "exc_msg": "Id 42 broke",
            "build": "555",
            "inner_type": "System.X.InnerException",
        },
    )
    (ev,) = derive(e)
    assert ev.key == "Example.CustomException: Id <n> broke"
    assert ev.detail["build"] == "555" and ev.detail["inner_type"] == "System.X.InnerException"


def test_message_from_cont_when_only_type_known():
    e = make(
        tpl_class="error",
        fields={"exc_type": "System.TimeoutException"},
        cont="Reason: The request channel timed out after 00:01:00.\nStack trace:\n   at A.B() in F:\\PD\\9\\1\\x.cs:line 1\n",
    )
    (ev,) = derive(e)
    assert ev.key == "System.TimeoutException: The request channel timed out after <n>:<n>:<n>."


def test_wfm_exception_type_attribute():
    e = make(
        tpl_class="error",
        msg='ERROR - Processing "ChangedCrewsEvent" message failed. ExceptionType: "System.NullReferenceException" Reason: Object reference not set. StackTrace: at X.Y()',
        cont="   at A.B() in F:\\PD\\8\\1\\a.cs:line 2\n",
    )
    (ev,) = derive(e)
    assert ev.detail["type"] == "System.NullReferenceException"
    assert ev.key == "System.NullReferenceException: Object reference not set"


def test_stack_without_type_is_unknown():
    e = make(tpl_class="other", msg="something odd", cont="   at A.B() in F:\\PD\\8\\1\\a.cs:line 2\n")
    (ev,) = derive(e)
    assert ev.detail["type"] == UNKNOWN_TYPE


def test_non_candidates_are_ignored():
    assert derive(make(tpl_class="error", msg="Plain error without exception")) == []
    assert derive(make(tpl_class="start", fields={"exc_type": "System.XException"})) == []
    assert derive(make(msg="Reading System.IO.IOException from a doc", tpl_class="other")) == []


def test_odd_input_never_raises():
    e = StoredEntry(id=1, stream_id=1, ts=None, tid=None, pid=None, level=None, sev=4, tpl_code="X", tpl_class="error",
                    fields={"exc_type": 5, "exc_msg": None, "build": None}, msg="", cont="\n\n\x00 at \n")  # fmt: skip
    events = derive(e)
    assert all(ev.ts_start == 0 for ev in events)
    # out-of-order ts is fine: stateless
    derive(
        make(1, ts=10, tpl_class="error", fields={"exc_type": "A.BException"}),
        make(2, ts=5, tpl_class="error", fields={"exc_type": "A.BException"}),
    )


def test_no_state_and_deterministic():
    d = ExceptionDeriver("smn", rules=None)
    assert d.low_watermark() is None
    entries = [make(i, tpl_class="error", cont=stack(f"A.XException: n {i}", 1)) for i in range(1, 6)]
    full = run_in_memory([ExceptionDeriver("smn")], entries)
    assert full == run_in_memory([ExceptionDeriver("smn")], entries)
    # re-derivation contract: from any checkpoint the events of the tail are the same
    tail = run_in_memory([ExceptionDeriver("smn")], entries[3:])
    assert tail == [e for e in full if e.start_entry_id >= 4]
