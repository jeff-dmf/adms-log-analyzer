"""kv_at continuation parser: allow-listed `@Name = value` items with closed types (nothing else leaves)."""

from __future__ import annotations

import re
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dmslog.parse.templates import SchemaError, load_catalog, load_texts

ALLOW = (
    'allow = { severity = "int:ev_severity", type = "token:ev_type", gidn = "gid:elem_gid", '
    'isZone = "bool:zone", note = "int:note_n" }'
)
TYPES = {"ev_severity", "ev_type", "elem_gid", "zone", "note_n"}
MSG = "Dump =>"


def _src(cont_expr: str, extra: str = "") -> str:
    return (
        'family = "X"\n[[template]]\ncode = "X-001"\nclass = "other"\n'
        f'text = "{MSG}"\nfts = false\ncont = {cont_expr}\n{extra}'
    )


def _catalog(cont_expr: str | None = None, extra: str = ""):
    return load_texts({"x.toml": _src(cont_expr or f'{{ parser = "kv_at", {ALLOW} }}', extra)})


@pytest.fixture(scope="module")
def tset():
    return _catalog().for_family("X", sensitive=False)


def run(tset, cont: str) -> dict:
    tm = tset.classify_text(MSG, cont)
    assert tm is not None
    return dict(tm.fields)


def test_extracts_typed_fields_and_counts_items(tset):
    f = run(
        tset, "@time = 2026-01-01 00:00:00  @type = Alarm  @message = Example @severity = 3 @isZone = False"
    )
    assert f == {"n_items": 5, "ev_type": "Alarm", "ev_severity": 3, "zone": False}


def test_values_are_never_stored(tset):
    f = run(tset, "@message = SECRET-WORD @severity = 3 @other = 12345678901")
    assert "SECRET-WORD" not in repr(f) and "12345678901" not in repr(f)
    assert set(f) == {"n_items", "ev_severity"}


def test_empty_list_and_no_item(tset):
    for cont in ("", "no items here, mail a@b = c", "x@severity = 9", "@", "@ =", "@=", "@1a = 2"):
        assert "n_items" not in run(tset, cont)


def test_empty_value_is_absent_not_rejected(tset):
    f = run(tset, "@severity =  @type = Alarm")
    assert f == {"n_items": 2, "ev_type": "Alarm"}


@pytest.mark.parametrize(
    "cont",
    [
        "@severity = abc",
        "@severity = 1234567890123456789",  # 19 digits: outside int
        "@severity = 3.5",
        "@severity = \u0663",  # non-ASCII digit
        "@severity = 1\x00",  # control byte inside the value
        "@severity = 1\x1b[31m",
        "@type = two words",
        "@type = 1abc",
        "@type = " + "a" * 33,
        "@type = Al\u00e9rt",
        "@isZone = yes",
        "@gidn = 0",
        "@gidn = 123456789012345678901",  # 21 digits
        "@gidn = 12 34",
    ],
)
def test_value_outside_type_is_rejected(tset, cont):
    f = run(tset, cont)
    assert f["kv_rejected"] == 1
    assert not (set(f) & TYPES)


def test_type_edges_accepted(tset):
    f = run(tset, "@severity = -7 @type = A_1 @gidn = 9223372036854775807 @isZone = TRUE")
    assert f["ev_severity"] == -7 and f["ev_type"] == "A_1" and f["zone"] is True
    assert f["elem_gid"] == 9223372036854775807
    assert run(tset, "@gidn = 9223372036854775808")["elem_gid"] == "9223372036854775808"


def test_duplicate_name_drops_both(tset):
    f = run(tset, "@severity = 3 @message = x @severity = 4")
    assert "ev_severity" not in f and f["kv_rejected"] == 2 and f["n_items"] == 3
    f = run(tset, "@severity = 1 @severity = 1 @severity = 1")
    assert "ev_severity" not in f and f["kv_rejected"] == 3


def test_injected_item_inside_free_text_erases_never_replaces(tset):
    f = run(tset, "@message = hello @severity = 1 world @severity = 3 @type = Alarm")
    assert "ev_severity" not in f and f["kv_rejected"] == 2 and f["ev_type"] == "Alarm"
    f = run(tset, "@type = Alarm @severity = 2 @message = text @isZone = true")
    assert f["ev_type"] == "Alarm" and f["ev_severity"] == 2 and f["zone"] is True


def test_value_runs_to_next_item_so_injection_in_a_typed_value_fails_the_type(tset):
    f = run(tset, "@type = Alarm Evil @severity = 3")
    assert f["kv_rejected"] == 1 and "ev_type" not in f and f["ev_severity"] == 3


def test_out_of_order_items(tset):
    f = run(tset, "@isZone = true @type = Alarm @severity = 5")
    assert f == {"n_items": 3, "zone": True, "ev_type": "Alarm", "ev_severity": 5}


def test_at_sign_without_leading_space_does_not_open_an_item(tset):
    assert run(tset, "@message = a@severity = 9 b") == {"n_items": 1}


def test_spacing_variants_and_newlines(tset):
    f = run(tset, "@severity=3\n@type =\tAlarm\n@isZone= true")
    assert f["ev_severity"] == 3 and f["ev_type"] == "Alarm" and f["zone"] is True


def test_long_name_is_not_an_item(tset):
    assert run(tset, "@" + "a" * 65 + " = 1 @severity = 2") == {"n_items": 1, "ev_severity": 2}


def test_huge_value_is_cut_by_the_window_last_item_dropped(tset):
    f = run(tset, "@severity = 3 @message = " + "x" * 9000 + " @type = Alarm")
    assert f == {"n_items": 1, "ev_severity": 3, "kv_cut": True}


def test_cut_never_reads_a_truncated_last_item(tset):
    f = run(tset, "@type = Alarm @message = " + "y" * 4060 + " @severity = 12345")
    assert f["kv_cut"] is True and f["ev_type"] == "Alarm" and "ev_severity" not in f
    f = run(tset, "@type = Alarm @message = " + "y" * 4060 + " @severity = 1")
    assert f["kv_cut"] is True and "ev_severity" not in f


def test_cut_item_name_still_counts_as_a_duplicate(tset):
    f = run(tset, "@severity = 1 @message = " + "z" * 4050 + " @severity = 2 and " + "w" * 100)
    assert f["kv_cut"] is True and "ev_severity" not in f and f["kv_rejected"] == 1


def test_no_item_in_a_cut_window_reports_the_cut(tset):
    assert run(tset, "x" * 5000) == {"n_items": 0, "kv_cut": True}


def test_window_option_range_and_effect():
    ts = _catalog(f'{{ parser = "kv_at", window = 256, {ALLOW} }}').for_family("X", sensitive=False)
    f = run(ts, "@severity = 3 @type = " + "A" * 300 + " @isZone = true")
    assert f["kv_cut"] is True and f["ev_severity"] == 3 and "zone" not in f
    for bad in (255, 4097, 0):
        with pytest.raises(SchemaError):
            _catalog(f'{{ parser = "kv_at", window = {bad}, {ALLOW} }}')


def test_big_continuation_is_linear_and_bounded(tset):
    cont = " @a = 1" * 40000 + " @severity = 3"  # about 280 KB, tens of thousands of '@'
    t0 = time.perf_counter()
    for _ in range(20):
        f = run(tset, cont)
    assert (time.perf_counter() - t0) / 20 < 0.05
    assert f["kv_cut"] is True and "ev_severity" not in f
    t0 = time.perf_counter()
    run(tset, "@" * 200000 + " =" * 50000)
    assert time.perf_counter() - t0 < 0.5


def test_parser_never_raises_on_odd_input(tset):
    for cont in ("@", "\x00\x00@severity = 1", "@severity = \ud800", "@" * 5000, "\n" * 5000, " @a =" * 3000):
        assert tset.classify_text(MSG, cont) is not None


# --------------------------------------------------------------------------- load errors


@pytest.mark.parametrize(
    "expr",
    [
        '{ parser = "kv_at" }',
        '{ parser = "kv_at", allow = {} }',
        '{ parser = "kv_at", allow = { a = "text:f" } }',
        '{ parser = "kv_at", allow = { a = "int" } }',
        '{ parser = "kv_at", allow = { a = "int:n_items" } }',
        '{ parser = "kv_at", allow = { a = "int:kv_cut" } }',
        '{ parser = "kv_at", allow = { a = "int:kv_rejected" } }',
        '{ parser = "kv_at", allow = { a = "int:f", b = "bool:f" } }',
        '{ parser = "kv_at", allow = { a = "int:F" } }',
        '{ parser = "kv_at", allow = { "1a" = "int:f" } }',
        '{ parser = "kv_at", allow = { a = 3 } }',
        '{ parser = "kv_at", allow = "x" }',
        '{ parser = "kv_at", allow = { a = "int:f" }, bogus = 1 }',
        '{ parser = "kv_at", allow = { a = "int:f" }, window = true }',
    ],
)
def test_load_errors(expr):
    with pytest.raises(SchemaError):
        _catalog(expr)


def test_too_many_allow_entries():
    entries = ", ".join(f'a{i} = "int:f{i}"' for i in range(33))
    with pytest.raises(SchemaError):
        _catalog(f'{{ parser = "kv_at", allow = {{ {entries} }} }}')


def test_fields_cannot_redeclare_an_allow_output():
    for name in ("ev_severity", "n_items"):
        with pytest.raises(SchemaError):
            _catalog(extra=f'fields = {{ {name} = "int" }}\n')


def test_allow_field_cannot_collide_with_a_regex_group():
    src = (
        'family = "X"\n[[template]]\ncode = "X-001"\nclass = "other"\n'
        "regex = '^Dump (?P<ev_severity>\\d+) =>$'\n"
        f'cont = {{ parser = "kv_at", {ALLOW} }}\n'
    )
    with pytest.raises(SchemaError):
        load_texts({"x.toml": src})


def test_outputs_are_the_fixed_ones_plus_allow_fields():
    t = _catalog().families["X"].templates[0]
    assert t.cont.outputs == {"n_items", "kv_rejected", "kv_cut"} | TYPES
    assert t.field_names >= TYPES


# --------------------------------------------------------------------------- the real template


def test_dom_s140_allow_list_and_examples():
    cat = load_catalog(check_examples=True)  # check_examples raises when an example fails
    t = next(x for x in cat.families["DMSOMS"].templates if x.code == "DOM-S140")
    assert t.cont is not None and t.cont.parser == "kv_at" and t.fts is False and not t.keys
    # only schema fields: no name, message, GID or other free-text member may be allow-listed
    assert set(t.cont.allow) == {
        "severity",
        "type",
        "alarmCategory",
        "alarmClass",
        "alarmSourceTypeId",
        "alarmInhibitTypeId",
        "operActivityTypeId",
        "isSTGZone",
    }
    assert {kind for kind, _ in t.cont.allow.values()} <= {"int", "bool", "token"}
    assert t.sev == 4  # the alarm severity does not change the record severity


# --------------------------------------------------------------------------- properties

_NAMES = st.sampled_from(["severity", "type", "gidn", "isZone", "note", "message", "other", "Severity"])
_VALUE = st.text(alphabet=list("abcXYZ019 -_@=.\n\t\x00\u00e9"), max_size=40) | st.sampled_from(
    ["@severity = 1", "@type = Evil", " @isZone = true", "9" * 30, "0", "true", "-5"]
)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.tuples(_NAMES, _VALUE), max_size=60), st.text(max_size=30), st.booleans())
def test_property_only_allowed_fields_with_valid_types(items, junk, big):
    ts = _catalog().for_family("X", sensitive=False)
    cont = junk + " ".join(f"@{n} = {v}" for n, v in items)
    if big:
        cont += " " * 5000 + "@severity = 7"
    tm = ts.classify_text(MSG, cont)
    assert tm is not None
    f = tm.fields
    assert set(f) <= {"n_items", "kv_rejected", "kv_cut"} | TYPES
    if "ev_severity" in f:
        assert isinstance(f["ev_severity"], int) and not isinstance(f["ev_severity"], bool)
        assert abs(f["ev_severity"]) < 10**18
    if "ev_type" in f:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", f["ev_type"])
    if "elem_gid" in f:
        g = f["elem_gid"]
        assert g != 0 and g != "0" and (isinstance(g, int) or re.fullmatch(r"[0-9]{19,20}", g))
    if "zone" in f:
        assert isinstance(f["zone"], bool)
    if "note_n" in f:
        assert isinstance(f["note_n"], int)
    for v in f.values():  # no free text ever lands in the fields
        if isinstance(v, str):
            assert re.fullmatch(r"[A-Za-z0-9_]{1,32}", v)
    # an allow-listed name written twice in the window never yields a field
    for log_name, field in (("severity", "ev_severity"), ("type", "ev_type"), ("isZone", "zone")):
        starts = re.findall(rf"(?:^|(?<=\s))@{log_name} ?= ?", cont[:4096])
        if len(starts) > 1:
            assert field not in f
