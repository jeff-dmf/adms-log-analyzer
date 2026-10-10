"""Strict redaction mode (the structural safety net) and the review round 3 regressions.

The strict mode keeps what the catalog says is static text and replaces, by default, every variable
part it cannot prove harmless; the standard mode is the pattern layer alone. Both modes must hide the
round 3 leak variants; only the strict one hides values the patterns cannot recognize.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from dmslog.parse.templates import load_catalog
from dmslog.report import template_shape
from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.report.redact import (
    Profile,
    ProfileError,
    Redactor,
    dump_profile_toml,
    load_profile,
    profile_from_dict,
    save_profile,
)
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "export_case_strict", ROOT / "tests/fixtures/export/build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_strict"] = case_module
_spec.loader.exec_module(case_module)

MODES = ("strict", "standard")
NOW = datetime(2030, 10, 15, 11, 0, 0)
SECRET = "SecretSurnameQ"


def redactor(mode: str = "strict", **profile) -> Redactor:
    return Redactor(Profile(mode=mode, **profile))


# -- review round 3: the six findings, in both modes ---------------------------------------------

# (finding, text, secret, payload): the secret must not survive, in any mode
ROUND3 = [
    ("1 extension reaches a name", f"/a/b=user:Ana {SECRET}", SECRET, False),
    ("1 url extension", f"see https://h.example.com/x?user:Ana {SECRET} now", SECRET, False),
    ("3 quote closed after 4096", 'user: "Ana\n' + "x" * 4200 + f'{SECRET}"', SECRET, False),
    ("3 quote closed after 4096 (single)", "user: 'Ana\n" + "x" * 5000 + f"{SECRET}'", SECRET, False),
    ("3 quote never closed", 'user: "Ana\n' + "x" * 50 + f"\n{SECRET}", SECRET, False),
    ("4 blank run in a name", f"user: Ana  {SECRET}", SECRET, False),
    ("4 tab in a name", f"user: Ana\t{SECRET}", SECRET, False),
    ("4 particle in a name", f"user: Ana da {SECRET} ok", SECRET, False),
    ("4 quoted GID", 'GID: "123456 789012"', "789012", False),
    ("4 bracketed GID", "GID=[123456, 789012]", "789012", False),
    ("4 grouped GID", "GID: 123456 789012", "789012", False),
    ("4 quoted label in brackets", f'user: ["Ana", "{SECRET}"]', SECRET, False),
    ("5 XML in a JSON string", '{"body":"<User>SecretUserQ</User>"}', "SecretUserQ", True),
    ("5 JSON in XML text", '<Data>{"user":"SecretUserQ"}</Data>', "SecretUserQ", True),
    ("5 escaped JSON in a JSON string", '{"body":"{\\"user\\":\\"SecretUserQ\\"}"}', "SecretUserQ", True),
    ("5 escaped XML in XML text", "<Data>&lt;User&gt;SecretUserQ&lt;/User&gt;</Data>", "SecretUserQ", True),
    ("5 XML in JSON in XML", '<D>{"b":"<Name>SecretUserQ</Name>"}</D>', "SecretUserQ", True),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("finding,text,secret,payload", ROUND3, ids=[c[0] for c in ROUND3])
def test_round3_variants_do_not_leak(finding, text, secret, payload, mode):
    out = redactor(mode).redact(text, payload=payload)
    assert secret not in out, out[-200:]


@pytest.mark.parametrize("mode", MODES)
def test_round3_automatic_exception_never_hides_a_structured_field(mode):
    automatic = Redactor(Profile(mode=mode), exceptions=["SendWorks"])
    for text in ("<mRID>SendWorks</mRID>", '{"mRID": "SendWorks"}', "<x mRID='SendWorks'/>"):
        assert "SendWorks" not in automatic.redact(text, payload=True)
    # the same value outside a field is still protected (an operation name stays readable)
    assert automatic.redact("Start SendWorks now") == "Start SendWorks now"
    # an exception of the USER wins, inside a field too
    user = Redactor(Profile(mode=mode, exceptions=("SendWorks",)))
    assert "SendWorks" in user.redact("<mRID>SendWorks</mRID>", payload=True)


# -- timing: linearity is the criterion; the absolute cap only catches true blowups ------------------

SAFETY_CAP_S = 15.0  # seconds at the development machine's speed, scaled by _machine_factor()
NOISE_FLOOR_S = 0.3  # a small measurement below this proves nothing either way


def _machine_factor() -> float:
    """How much slower than the development machine this one is (>= 1): the MEDIAN of five runs of a
    fixed pure-Python loop that takes about 0.045 s there."""
    runs = []
    for _ in range(5):
        started = time.perf_counter()
        total = 0
        for i in range(600_000):
            total += i * i % 7
        runs.append(time.perf_counter() - started)
    return max(1.0, sorted(runs)[2] / 0.045)


def _best_of(measure, n: int, repeats: int = 3) -> float:
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        measure(n)
        best = min(best, time.perf_counter() - started)
    return best


def _assert_linear(measure, n: int, growth: int, label: object) -> None:
    """``measure(size)`` must grow linearly: at ``growth`` times the size it may cost at most twice the
    proportional time (noise, caches), never the quadratic ``growth ** 2``; plus a generous absolute cap
    that only a real blowup reaches. Best of three timings each."""
    small = _best_of(measure, n)
    big = _best_of(measure, n * growth)
    assert big < SAFETY_CAP_S * _machine_factor(), (label, small, big)
    assert big < max(2 * growth * small, NOISE_FLOOR_S), (label, small, big)


def _repeat(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


HOSTILE_425K = {
    "exception-heavy": "user:A%201.1.1.1\n",
    "labels": "user: Ana Quintanilha host: q7 GID: 123456 789012\n",
    "nested": '{"b":"<User a=\\"1\\">x</User>"} <D>{"user":"q"}</D> ',
    "quotes": "user: \"a\" mRID: 'b' [c] ",
    "round4-words": "customer Monitor password: !@#$ <B>Monitor 1 A</B> ",
    "round4-json": '{"note": "Monitor 1 A", "ok": true, "k": [1, "x"]} ',
    "round5-markup": '<!-- a --><![CDATA[b]]><?p c?><x a="1" b=\'2\'>t</x>{"k1": [true, 1, "s"]} <',
    "round6-names": "<Body " + "a" * 4000 + "!> <x v=12345Ready w=Monitor> ",
    "round9-regions": '<a/><Header></Header>{"k": 1} <!-- x --> ',
    "round10-regions": '<a/>[0,"<x/>"] <a></a>"<b/>" ',
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", HOSTILE_425K)
def test_round3_hostile_425k_inputs_are_fast_and_linear(name, mode):
    r = Redactor(Profile(mode=mode, exceptions=("A",)))

    def measure(size: int) -> None:
        text = _repeat(HOSTILE_425K[name], size)
        r.redact(text)
        r.redact(text, payload=True)

    _assert_linear(measure, 425_000 // 4, 4, (name, mode))  # the round 3 code took over 24 s here


# -- the profile switch ----------------------------------------------------------------------------


def test_mode_defaults_to_strict_and_is_validated(tmp_path):
    assert Profile().mode == "strict"
    assert profile_from_dict({}).mode == "strict"
    assert profile_from_dict({"mode": "standard"}).mode == "standard"
    for bad in ("lenient", "", 1, True, ["strict"]):
        with pytest.raises(ProfileError) as info:
            profile_from_dict({"mode": bad})
        assert info.value.problem == "bad_mode"
    save_profile(tmp_path, Profile(mode="standard"))
    assert 'mode = "standard"' in (tmp_path / "redaction.toml").read_text(encoding="utf-8")
    assert load_profile(tmp_path).mode == "standard"
    assert 'mode = "strict"' in dump_profile_toml(Profile())


def test_all_categories_off_turns_the_nets_off_too():
    off = Redactor(
        Profile(
            categories=dict.fromkeys(
                (
                    "guid",
                    "gid",
                    "ticket",
                    "mrid",
                    "plate",
                    "host",
                    "ip",
                    "user",
                    "url",
                    "path",
                    "email",
                    "phone",
                    "person",
                ),
                False,
            )
        )
    )
    text = "user: Ana Quintanilha"
    assert off.redact(text) is text and not off.strict


# -- free text: labels -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Login failed for user: ana quintanilha from host 10.1.2.3", "quintanilha"),  # lowercase name
        ("Login failed for user jsilvaq from host q7", "jsilvaq"),  # label word without ':'
        ("customerName=Quintanilha; ok", "Quintanilha"),  # camelCase label
        ("request_user_id: zq81k", "zq81k"),  # snake_case label
        ("crew GID: 123456 789012 moved", "789012"),
        ("token=K5W3N8Q", "K5W3N8Q"),
        ("user%3A%20Quintanilha", "Quintanilha"),  # the label written percent-encoded
        ("Address: Rua Zq Quintanilha 45", "Quintanilha"),
    ],
)
def test_strict_free_text_net_hides_labelled_values_the_patterns_miss(text, secret):
    assert secret not in redactor().redact(text)


def test_strict_free_text_keeps_only_proven_words():
    out = redactor().redact("Login failed for user: jsilvaq after 3 attempts in 120 ms (error 4711)")
    assert out.startswith("Login failed for user: <USER-1> after ")
    assert "attempts in 120 ms (error " in out
    assert " 3 " not in out and "4711" not in out  # a bare number is not proven harmless
    plain = (
        "Operation SendWorks completed in 120 ms at 2026-10-08 12:40:51.123 System.TimeoutException WFM-081"
    )
    assert redactor().redact(plain) == plain  # catalog words, a duration, a time, a type, a code
    assert "Quintanilha" not in redactor().redact("Operation SendWorks for Quintanilha")


def test_strict_values_keep_the_markers_of_the_categories_they_contain():
    out = redactor().redact("host: h1 3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c")
    assert "<GUID-1>" in out and "3f2a9c1e" not in out


def test_user_exceptions_win_over_the_strict_nets_but_automatic_ones_do_not():
    text = "user: Quintanilha"
    assert "Quintanilha" in Redactor(Profile(exceptions=("Quintanilha",))).redact(text)
    assert "Quintanilha" not in Redactor(Profile(), exceptions=["Quintanilha"]).redact(text)


def test_identifier_texts_are_replaced_whole_in_strict_mode():
    assert redactor().redact("R-77812", identifier=True) == "<VALUE-1>"
    assert redactor().redact("!@#$", identifier=True) == "<VALUE-1>"
    assert redactor("standard").redact("R-77812", identifier=True) == "R-77812"


# -- review round 4 ----------------------------------------------------------------------------------

# (finding, kind, text, secret, extra): kind "text" = free text, "payload", "record" (extra = template
# code), "auto" (extra = an automatic exception)
ROUND4 = [
    (
        "1 catalog words in a free field",
        "record",
        "CRS request is rejected by DMZ validation rules: Monitor 123456 A",
        ["Monitor", "123456", " A"],
        "WFM-130C",
    ),
    (
        "1 catalog words in a payload",
        "payload",
        "<Body>Monitor 123456 A</Body>",
        ["Monitor", "123456", ">A<"],
        None,
    ),
    (
        "1 catalog words in a JSON payload",
        "payload",
        '{"note": "Monitor 123456 A"}',
        ["Monitor", "123456"],
        None,
    ),
    (
        "2 camelCase in a result field",
        "record",
        'Result="QuintanilhaZorzo" received',
        ["QuintanilhaZorzo"],
        None,
    ),
    (
        "2 camelCase in the AMI-004 result field",
        "record",
        'External system returned "QuintanilhaZorzo" response for sent request. '
        "For more details please consult sensitive log.",
        ["QuintanilhaZorzo"],
        "AMI-004",
    ),
    (
        "2 camelCase in an op field",
        "record",
        "(ERROR) [Communication2] - Service operation QuintanilhaZorzo threw unexpected exception.",
        ["QuintanilhaZorzo"],
        "WFM-063",
    ),
    ("3 auto exception behind a label", "auto", "customerName=SendWorks", ["SendWorks"], "SendWorks"),
    ("3 auto exception behind user:", "auto", "user: SendWorks", ["SendWorks"], "SendWorks"),
    ("3 label word without punctuation", "text", "call from customer Monitor today", ["Monitor"], None),
    ("4 punctuation-only password", "text", "password: !@#$ ok", ["!@#$"], None),
    ("4 punctuation-only token", "text", "token=%%%% ok", ["%%%%"], None),
]


def _run4(kind, text, extra, mode="strict"):
    if kind == "auto":
        return Redactor(Profile(mode=mode), exceptions=[extra]).redact(text)
    r = redactor(mode)
    if kind == "payload":
        return r.redact(text, payload=True)
    if kind == "record":
        return r.redact(text, spans=r.record_spans(text, len(text), extra))
    return r.redact(text)


@pytest.mark.parametrize("finding,kind,text,secrets,extra", ROUND4, ids=[c[0] for c in ROUND4])
def test_round4_variants_do_not_leak(finding, kind, text, secrets, extra):
    out = _run4(kind, text, extra)
    for secret in secrets:
        assert secret not in out, out


def test_round4_automatic_exceptions_never_protect_labelled_values_in_standard_mode_either():
    r = Redactor(Profile(mode="standard"), exceptions=["SendWorks"])
    assert "SendWorks" not in r.redact("customerName=SendWorks")
    assert "SendWorks" not in r.redact("user: SendWorks")
    assert r.redact("Start SendWorks now") == "Start SendWorks now"  # unlabelled: still readable


# -- payloads ----------------------------------------------------------------------------------------


def test_strict_payload_keeps_only_the_skeleton():
    xml = (
        '<Req id="7"><Note>Poste caido na Rua Quintanilha 45</Note><Crew>SendWorks</Crew><Ok>true</Ok></Req>'
    )
    out = redactor(payload_names=()).redact(xml, payload=True)
    for gone in ("Quintanilha", "Poste", "45", "SendWorks", '"7"'):
        assert gone not in out
    # XML text "true" is data (round 5)
    assert out.startswith("<Req id=") and "<Note><VALUE-" in out and "<Ok><VALUE-" in out
    # standard mode: only the field rules and the patterns
    assert "Quintanilha" in redactor("standard", payload_names=()).redact(xml, payload=True)


def test_strict_payload_json_values_go_and_catalog_keys_stay():
    out = redactor(payload_names=()).redact(
        '{"Quintanilha": 1, "customerName": "x", "status": "OK", "ok": true, "error": null}', payload=True
    )
    assert "Quintanilha" not in out and '"customerName": "<PERSON-' in out and '"status": "<VALUE-' in out
    assert '"ok": true' in out and '"error": null' in out


def test_strict_payload_escapes_are_replaced():
    out = redactor(payload_names=()).redact(
        "<x>%51uintanilha &#81;uintanilha \\u0051uintanilha</x>", payload=True
    )
    assert "uintanilha" not in out


# -- records: the template catalog -----------------------------------------------------------------


def _examples():
    catalog = load_catalog()
    for tpl in catalog.templates():
        for number, example in enumerate(tpl.examples):
            yield pytest.param(tpl.code, example.msg, example.cont, id=f"{tpl.code}-{number}")


EXAMPLES = list(_examples())


def test_the_catalog_has_examples_to_check():
    assert len(EXAMPLES) > 300


@pytest.mark.parametrize("code,msg,cont", EXAMPLES)
def test_catalog_examples_keep_static_text_and_only_proven_fields(code, msg, cont):
    r = redactor()
    text = msg + ("\n" + cont if cont else "")
    out = r.redact(text, spans=r.record_spans(text, len(msg), code)).split("\n")[0]
    shape = template_shape.shape(code)
    lay = template_shape.layout(shape, msg)
    assert lay is not None  # the template still matches its own example
    static = "".join(msg[a:b] for a, b in lay.static)
    for start, end, name in lay.fields:
        value = msg[start:end].strip()
        if not r._field_kept(shape, name, msg[start:end]) and len(value) >= 3 and value not in static:
            assert value not in out, (name, out)
    for kind, literal in shape.items:
        if kind == "lit" and len(literal.strip()) >= 4:
            assert literal in out, (literal, out)


OAS_LINE = "(ERROR) [Communication2] - Service operation SendWorks threw unexpected exception."


@pytest.mark.parametrize(
    "code,msg,secrets,kept",
    [
        (
            "WFM-130C",
            "CRS request is rejected by DMZ validation rules: "
            "customer Marcelo Quintanilha-Zorzo at Rua Zq 45",
            ["Marcelo", "Quintanilha", "Zorzo", "45"],
            ["CRS request is rejected by DMZ validation rules: <VALUE-1>"],
        ),
        (
            "SVC-003",
            "The WfmAdapter service on DSPQ7X9K2N01 is now communicating with the partner on DSPQ7X9K2N02.",
            ["DSPQ7X9K2N01", "DSPQ7X9K2N02", "WfmAdapter"],
            ["service on", "is now communicating with the partner on"],
        ),
        ("WFM-063", OAS_LINE, [], [OAS_LINE]),  # an operation of the catalog is proven
        (
            "SVC-001",
            "The WfmAdapter service on DSPQ7 changed from Running to Stopped.",
            ["DSPQ7", "WfmAdapter"],
            ["changed from Running to Stopped."],  # closed set of service states (PROVEN_FIELDS)
        ),
        (
            "BLT-011",
            "[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution completed. "
            "Result: LocalAndDownstreamNodesReady status: True",
            [],
            ["status: True"],  # the regex allows only True or False
        ),
    ],
)
def test_template_fields_stay_only_when_proven(code, msg, secrets, kept):
    r = redactor()
    out = r.redact(msg, spans=r.record_spans(msg, len(msg), code))
    for secret in secrets:
        assert secret not in out, out
    for text in kept:
        assert text in out, out


def test_proven_fields_table_follows_its_maintenance_rule():
    from dmslog.report.redact import _FIELD_WORDS, PROVEN_FIELDS

    validators = {"operation", "level", "code", "bool", "number"}
    for (_code, name), validator in PROVEN_FIELDS.items():
        assert validator in validators or validator.startswith("enum:"), name
        assert not any(part in _FIELD_WORDS for part in name.split("_")), name  # never an identifier


def test_a_template_that_no_longer_matches_fails_closed():
    r = redactor()
    msg = "Totally different text from Quintanilha in 30 ms"
    for code in ("WFM-130C", "NOPE-404"):
        out = r.redact(msg, spans=r.record_spans(msg, len(msg), code))
        assert out == "<VALUE-1>"


def test_record_spans_are_empty_in_standard_mode():
    assert redactor("standard").record_spans("user: x", 7, None) == []


# -- through the export -----------------------------------------------------------------------------


def _edited(tmp_path, statements):
    db = case_module.build_export_case(tmp_path / f"strict-{uuid.uuid4().hex[:8]}.sqlite")
    rw = connect(db)
    with rw:
        for sql, params in statements:
            rw.execute(sql, params)
    rw.close()
    return connect(db, readonly=True)


def _export(conn, selection, profile, **options):
    result = build_report(conn, selection, ExportOptions(anonymize=True, profile=profile, **options), now=NOW)
    return "".join(p.text for p in result.parts)


def test_export_redacts_a_templated_record_by_its_structure(tmp_path):
    msg = "CRS request is rejected by DMZ validation rules: customer Marcelo Quintanilha-Zorzo called"
    conn = _edited(
        tmp_path,
        [
            ("UPDATE template SET code = 'WFM-130C' WHERE id = 1", ()),
            ("UPDATE entry SET tpl_id = 1, msg = ? WHERE id = 2", (msg,)),
        ],
    )
    try:
        strict = _export(conn, Selection(ids=(2,)), Profile())
        standard = _export(conn, Selection(ids=(2,)), Profile(mode="standard"))
    finally:
        conn.close()
    assert "Quintanilha" not in strict and "Marcelo" not in strict
    assert "CRS request is rejected by DMZ validation rules: <VALUE-" in strict
    assert 'Redaction: ON (profile "default", strict mode)' in strict
    assert "Quintanilha" in standard  # no label, no pattern: only the structure knows it is data


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "finding,text,secret,payload", [c for c in ROUND3 if not c[3]], ids=[c[0] for c in ROUND3 if not c[3]]
)
def test_round3_variants_do_not_leak_through_the_export(tmp_path, finding, text, secret, payload, mode):
    conn = _edited(tmp_path, [("UPDATE entry SET msg = ? WHERE id = 2", (text,))])
    try:
        out = _export(conn, Selection(ids=(2,)), Profile(mode=mode))
    finally:
        conn.close()
    assert secret not in out


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("body", [c[1] for c in ROUND3 if c[3]], ids=[c[0] for c in ROUND3 if c[3]])
def test_round3_nested_payloads_do_not_leak_through_the_export(tmp_path, body, mode):
    conn = _edited(
        tmp_path,
        [
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 7", (body,)),
            ("UPDATE payload SET char_len = ? WHERE entry_id = 7", (len(body),)),
        ],
    )
    try:
        out = _export(conn, Selection(ids=(7,)), Profile(mode=mode), include_payloads=True)
    finally:
        conn.close()
    assert "SecretUserQ" not in out


def test_round3_automatic_exception_in_an_exported_payload(tmp_path):
    body = "<WorkRequest><mRID>SendWorks</mRID></WorkRequest>"
    conn = _edited(
        tmp_path,
        [
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 7", (body,)),
            ("UPDATE payload SET char_len = ? WHERE entry_id = 7", (len(body),)),
        ],
    )
    try:
        for mode in MODES:  # SendWorks is the operation of event 1: an automatic exception
            out = _export(conn, Selection(events=(1,), ids=(7,)), Profile(mode=mode), include_payloads=True)
            payloads = out[out.index("== Payloads ==") :]
            assert "<mRID>SendWorks</mRID>" not in payloads and "<mRID><MRID-" in payloads
    finally:
        conn.close()


def test_event_keys_are_replaced_whole_in_strict_mode(tmp_path):
    conn = _edited(tmp_path, [("UPDATE event SET key = 'R-77812' WHERE id = 1", ())])
    try:
        strict = _export(conn, Selection(events=(1,)), Profile())
        standard = _export(conn, Selection(events=(1,)), Profile(mode="standard"))
    finally:
        conn.close()
    assert re.search(r"key=<VALUE-\d+>", strict) and "R-77812" not in strict
    assert "key=R-77812" in standard


def test_strict_legend_names_the_other_values():
    r = redactor()
    r.redact("user: Ana Quintanilha zq81k")
    rows = {row["category"]: row for row in r.legend()}
    assert rows["user"]["replaced"] >= 1
    assert re.search(r"Quintanilha|zq81k", repr(r.legend())) is None


def test_camel_and_compound_names_never_carry_long_digit_runs():
    r = redactor()
    msg = (
        "Health Monitor has received warning notification from HostQ_12345_1234567890 "
        "for the Wfm service on computer SRVQ7."
    )
    assert "1234567890" not in r.redact(msg, spans=r.record_spans(msg, len(msg), "SVC-007"))
    assert "20261008124051" not in r.redact("user: x log_20261008124051 end")


# -- review round 5: default-deny in payloads and structural spans -----------------------------------

# (finding, kind, text, secrets, extra) as ROUND4
ROUND5 = [
    ("1 XML comment", "payload", "<!-- <SecretQ> -->", ["SecretQ"], None),
    ("1 XML comment with JSON", "payload", '<!-- "120ms": true -->', ["120ms", "true"], None),
    ("1 CDATA with JSON", "payload", '<![CDATA["Monitor": true]]>', ["Monitor", "true"], None),
    ("1 processing instruction", "payload", "<?pi SecretQ?><x/>", ["SecretQ"], None),
    ("1 doctype", "payload", "<!DOCTYPE SecretQ><x/>", ["SecretQ"], None),
    ("2 punctuation after a labelled value", "text", "password: 12345!!!!!", ["12345", "!!!!!"], None),
    ("2 punctuation around an identifier", "identifier", "!@#SecretQ$%!", ["!@#", "$%!", "SecretQ"], None),
    ("2 punctuation in a payload value", "payload", "<B>12345!!!!!</B>", ["12345", "!"], None),
    (
        "2 punctuation in a template field",
        "record",
        "CRS request is rejected by DMZ validation rules: !!SecretQ!!",
        ["!!", "SecretQ"],
        "WFM-130C",
    ),
    ("3 JSON key with digits", "payload", '{"Monitor7":"x"}', ["Monitor7"], None),
    ("3 JSON key with a unit", "payload", '{"120ms": 1}', ["120ms"], None),
    ("3 JSON key in record text", "text", 'got {"Monitor7": "x"}', ["Monitor7"], None),
    ("4 labelled name with public words", "text", "customer monitor will", ["monitor", "will"], None),
    (
        "5 numeric regex is not a proof",
        "record",
        "arb_pubSubLookup: Received update on topic: q.t with status 654321",
        ["654321"],
        "GEN-PSA-02",
    ),
    ("6 boolean text in XML", "payload", "<Body>true</Body>", [">true<"], None),
]


def _run5(kind, text, extra):
    r = redactor(payload_names=())
    if kind == "identifier":
        return r.redact(text, identifier=True)
    if kind == "payload":
        return r.redact(text, payload=True)
    if kind == "record":
        return r.redact(text, spans=r.record_spans(text, len(text), extra))
    return r.redact(text)


@pytest.mark.parametrize("finding,kind,text,secrets,extra", ROUND5, ids=[c[0] for c in ROUND5])
def test_round5_variants_do_not_leak(finding, kind, text, secrets, extra):
    out = _run5(kind, text, extra)
    for secret in secrets:
        assert secret not in out, out


def test_round5_payload_keeps_only_syntax_allowed_names_and_json_literals():
    out = redactor(payload_names=()).redact(
        '<ns0:SendWorksRequest xmlns:ns0="urn:x"><Header><MessageID>abc</MessageID></Header>'
        '<Data>{"status": true, "count": null, "Quintanilha": false, "note": "x"}</Data>'
        "</ns0:SendWorksRequest>",
        payload=True,
    )
    assert out.startswith('<ns0:SendWorksRequest xmlns:ns0="<VALUE-')
    assert "<Header><MessageID><VALUE-" in out and '"status": true' in out and '"count": null' in out
    assert "Quintanilha" not in out and "urn:x" not in out and '"note": "<' in out


def test_round5_leftover_punctuation_joins_the_marker_of_its_value():
    out = redactor().redact("for user: jsilva (INC0045123) and again INC0045123")
    assert out == "for user: <USER-1> <TICKET-1> and again <TICKET-1>"


def test_round5_a_declared_numeric_type_still_proves_a_count():
    from types import SimpleNamespace

    r = redactor()
    shape = SimpleNamespace(code="X-1", proofs={}, field_types={"status": "int"})
    assert r._field_kept(shape, "status", "654321") and not r._field_kept(shape, "status", "1234567")
    assert not r._field_kept(SimpleNamespace(code="X-1", proofs={}, field_types={}), "status", "654321")


def test_product_error_codes_of_the_knowledge_pack_stay_in_free_text():
    codes = template_shape.product_codes()
    assert len(codes) > 100
    code = sorted(c for c in codes if c.isalpha() and len(c) > 12)[0]
    original = next(w for w in [code, code.upper(), code.title()] if w.casefold() == code)
    assert redactor().redact(f"error codes: {original}") == f"error codes: {original}"
    assert original not in redactor().redact(f"user: {original}")  # behind a label it is a value


# -- review round 6 ------------------------------------------------------------------------------

ROUND6 = [
    ("1 unquoted attribute value", "<Body value=Monitor>", ["Monitor"]),
    ("1 unquoted value glued to a word", "<Body value=12345Ready>", ["12345", "Ready"]),
    ("1 unquoted value after blanks", "<Body value =  Monitor/>", ["Monitor"]),
    ("1 unquoted value in a broken tag", "<Body a=Monitor b=Ready", ["Monitor", "Ready"]),
    ("2 wide JSON whitespace", '{         "status"         : "Monitor"}', ["Monitor"]),
    ("2 JSON newlines and tabs", '{\n\t"status"\n\n:\t\n"Monitor"\r\n}', ["Monitor"]),
    ("2 wide whitespace in a list", '[                    "Monitor", true]', ["Monitor"]),
]


@pytest.mark.parametrize("payload", [True, False])
@pytest.mark.parametrize("finding,text,secrets", ROUND6, ids=[c[0] for c in ROUND6])
def test_round6_variants_do_not_leak(finding, text, secrets, payload):
    r = redactor(payload_names=())
    outs = [r.redact(text, payload=payload), r.redact(text, spans=r.record_spans(text, len(text), None))]
    for out in outs:
        for secret in secrets:
            assert secret not in out, out


def test_round6_tag_names_are_scanned_in_linear_time():
    r = redactor(payload_names=())

    def measure(size: int) -> None:
        r.redact("<Body " + "a" * size + "!>", payload=True)

    _assert_linear(measure, 32_000, 4, "tag name")  # the round 5 code took about 6.5 s at 128k


# -- review round 7 ------------------------------------------------------------------------------

ROUND7 = [
    ("1 vertical tab around a key", '{\v"status"\v: "MonitorReady"}'),
    ("1 form feed", '{\f"status"\f:\f"MonitorReady"}'),
    ("1 NBSP and em space", '{\u00a0"status"\u2003:\u3000"MonitorReady"}'),
    ("1 line and paragraph separators", '{\u2028"status"\u2029:"MonitorReady"}'),
    ("1 zero-width and BOM", '{\u200b"status"\ufeff:"MonitorReady"}'),
    ("1 unicode blanks in a tag", "<Body\u00a0value\u2003=\u3000MonitorReady>"),
    ("2 stray double quotes in an unquoted value", '<Body value=x" MonitorReady ">'),
    ("2 stray single quotes in an unquoted value", "<Body value=x' MonitorReady '>"),
    ("2 entity quotes in an unquoted value", "<Body value=&quot; MonitorReady &quot;>"),
    ("2 orphan word in a tag", "<Body MonitorReady>x</Body>"),
    ("2 orphan word between attributes", '<Body a="1" MonitorReady b="2"/>'),
    ("2 unpaired tag in free text", "value <MonitorReady> found"),
    ("2 broken tag", "x <MonitorReady y"),
]


@pytest.mark.parametrize("finding,text", ROUND7, ids=[c[0] for c in ROUND7])
def test_round7_variants_do_not_leak(finding, text):
    r = redactor(payload_names=())
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
    ):
        assert "MonitorReady" not in out, out


def test_round7_well_formed_names_stay():
    out = redactor(payload_names=()).redact(
        '<Header id="1"><MessageID>abc</MessageID><Ok/></Header>', payload=True
    )
    assert out == '<Header id="<VALUE-1>"><MessageID><VALUE-2></MessageID><Ok/></Header>'


# -- review round 8 ------------------------------------------------------------------------------

ROUND8 = [
    ("1 Mongolian vowel separator", '{\u180e"status"\u180e:"MonitorReady"}'),
    ("1 separators everywhere", '{\u180e\u200c"status"\u2060:\u200d"MonitorReady"}'),
    ("2 closing tag in a comment", "value <MonitorReady> found <!-- </MonitorReady> -->"),
    ("2 closing tag in CDATA", "value <MonitorReady> found <![CDATA[</MonitorReady>]]>"),
    ("2 closing tag in an attribute", '<x a="</MonitorReady>"/> value <MonitorReady> x'),
    ("2 incomplete closing tag", "value <MonitorReady> found </MonitorReady"),
    ("2 closing tag in quoted text", 'value <MonitorReady> found "</MonitorReady>"'),
    ("2 closing tag in a processing instruction", "value <MonitorReady> found <?pi </MonitorReady> ?>"),
    ("2 closing tag in a JSON string", '{"a": "</MonitorReady>"} value <MonitorReady> x'),
    ("2 crossed tags", "<MonitorReady><Header></MonitorReady></Header>"),
    ("3 entity glued to a name", "<MonitorReady&#65;/>"),
    ("3 digits before an attribute name", '<Body 123MonitorReady="x"/>'),
    ("3 unicode letter glued to a name", "<MonitorReady\u00e9/>"),
    ("3 unicode letter glued to an attribute", '<Body MonitorReady\u00e9="x"/>'),
    ("3 punctuation inside an attribute name", '<Body Monitor!Ready="x"/>'),
]


@pytest.mark.parametrize("finding,text", ROUND8, ids=[c[0] for c in ROUND8])
def test_round8_variants_do_not_leak(finding, text):
    r = redactor(payload_names=())
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
    ):
        assert "MonitorReady" not in out and "Ready" not in out, out


def test_round8_one_separator_set_for_detection_and_scanning():
    from dmslog.report import redact as module

    disagree = [
        hex(code)
        for code in range(0x110000)
        if (module._RX["blank"].fullmatch(chr(code)) is not None) != module._is_blank(chr(code))
    ]
    assert disagree == []
    for code in range(0x110000):
        char = chr(code)
        if module._is_blank(char):
            assert module._RX["structured_hint"].search("{" + char + '"k"' + char + ":1}"), hex(code)


@pytest.mark.parametrize(
    "unit",
    ["<Body value=x&", '<Body a="', '<Body a=x"', "<!--", "<MonitorReady&#65;", "</x", '<x>"', "<a b"],
)
def test_round8_malformed_tags_are_scanned_in_linear_time(unit):
    r = redactor(payload_names=())

    def measure(size: int) -> None:  # the structure scanner alone: its cost is what can go quadratic
        r._structure_spans(_repeat(unit, size))

    _assert_linear(measure, 250_000, 8, unit)  # the round 7 suffix rescans grew about 22x here


def test_round8_unmatched_closing_tags_pair_in_linear_time():
    r = redactor(payload_names=())

    def measure(size: int) -> None:
        r.redact("<Header>" * size + "</Body>" * size, payload=True)

    _assert_linear(measure, 25_000, 4, "unmatched closing tags")


def test_round8_well_formed_payloads_keep_their_skeleton():
    out = redactor(payload_names=()).redact(
        '<Header><MessageID>abc</MessageID><Ok/><Data a="1">x</Data></Header>', payload=True
    )
    assert out.startswith("<Header><MessageID><VALUE-") and "</MessageID><Ok/><Data" in out
    assert out.endswith("</Data></Header>")


def test_round8_single_quoted_closing_tag_does_not_pair_but_apostrophes_in_words_are_text():
    r = redactor(payload_names=())
    assert "MonitorReady" not in r.redact("value <MonitorReady> found '</MonitorReady>'", payload=True)
    out = r.redact("<Header><Note>Can't connect</Note></Header>", payload=True)
    assert out.startswith("<Header><Note><VALUE-") and out.endswith("</Note></Header>")


# -- review round 9: the fail-closed gate ------------------------------------------------------------

# malformed regions of every round (and the three of round 9): each becomes ONE value
MALFORMED_REGIONS = [
    ("9 DOCTYPE ending inside quotes", '<!DOCTYPE x [<!ENTITY y "> </MonitorReady>">]>'),
    ("9 value that fabricates a closing tag", '<x a=foo"</MonitorReady>"/>'),
    ("9 slash between attributes", '<Body a/MonitorReady="x"/>'),
    ("9 slash inside a tag", "<MonitorReady/x/>"),
    ("8 entity glued to a name", "<MonitorReady&#65;/>"),
    ("8 digits before an attribute name", '<Body 123MonitorReady="x"/>'),
    ("8 crossed tags", "<MonitorReady><Header></MonitorReady></Header>"),
    ("7 stray quotes in an unquoted value", '<Body value=x" MonitorReady ">'),
    ("7 orphan word in a tag", "<Body MonitorReady>x</Body>"),
    ("7 unpaired tag", "<MonitorReady> found"),
    ("6 unquoted attribute value", "<Body value=MonitorReady></Body>"),
    ("5 DOCTYPE", "<!DOCTYPE MonitorReady><x/>"),
    ("5 truncated payload", "<Req><MonitorReady>1</MonitorReady>"),
    ("5 non-JSON blank in JSON", '{\u00a0"status": "MonitorReady"}'),
]


@pytest.mark.parametrize("finding,region", MALFORMED_REGIONS, ids=[c[0] for c in MALFORMED_REGIONS])
def test_round9_a_malformed_region_becomes_one_value(finding, region):
    r = redactor(payload_names=())
    assert re.fullmatch(r"<VALUE-\d+>", r.redact(region, payload=True)), finding
    out = r.redact("value " + region)  # free text before it keeps its allowlist
    assert re.fullmatch(r"value <VALUE-\d+>", out), out
    out = r.redact("value " + region, spans=r.record_spans("value " + region, len(region) + 6, None))
    assert re.fullmatch(r"value <VALUE-\d+>", out), out


def test_round9_well_formed_regions_keep_a_readable_skeleton():
    r = redactor(payload_names=())
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<ns0:SendWorksRequest xmlns:ns0="urn:x">'
        "<!-- note --><Header><MessageID>abc</MessageID></Header>"
        '<Data status="1"><![CDATA[x]]> &amp; y</Data><Ok/></ns0:SendWorksRequest>'
    )
    out = r.redact(xml, payload=True)
    kept_parts = [
        "<ns0:SendWorksRequest xmlns:ns0=",
        "<Header><MessageID><VALUE-",
        "</MessageID></Header>",
        "<!-- <VALUE-",
        "<Data status=",
        "<![CDATA[<VALUE-",
        "<Ok/></ns0:SendWorksRequest>",
    ]
    for kept in kept_parts:
        assert kept in out, (kept, out)
    record = 'Sending {"status": "OK", "ok": true} to <Req><MessageID>1</MessageID></Req> now'
    out = r.redact(record)
    assert out.startswith('Sending {"status": "<VALUE-') and '"ok": true}' in out
    assert "<Req><MessageID><VALUE-" in out and out.endswith("</MessageID></Req> now")


# -- review round 10: quote-aware cutting, stray delimiters, expat as the authority --------------------

ROUND10 = [
    ("1 markup in a JSON string of a numeric array", '[0,"<MonitorReady/>"]'),
    ("1 markup in a JSON string of a literal array", '[true, "<MonitorReady/>"]'),
    ("1 markup in a JSON string of a negative array", '[-1, "<MonitorReady/>"]'),
    ("1 markup in quoted text", 'value "<MonitorReady/>"'),
    ("2 stray '>' before a region", "value > <MonitorReady/>"),
    ("2 stray '<' before a region", "value < 3 <MonitorReady/>"),
    ("3 PI ended inside a quote", '<?xml version="?> <MonitorReady/>">'),
    ("3 duplicate attribute", '<MonitorReady status="a" status="b"/>'),
    ("3 undefined entity", "<MonitorReady>&undefined;</MonitorReady>"),
    ("3 invalid character reference", "<MonitorReady>&#0;</MonitorReady>"),
    ("3 comment ending in '-'", "<MonitorReady><!-- a ---></MonitorReady>"),
    ("3 unbound namespace prefix", "<p:MonitorReady/>"),
    ("3 xml declaration not at the start", 'x <?xml version="1.0"?><MonitorReady/>'),
]


@pytest.mark.parametrize("finding,text", ROUND10, ids=[c[0] for c in ROUND10])
def test_round10_variants_do_not_leak(finding, text):
    r = redactor(payload_names=())
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
    ):
        assert "MonitorReady" not in out, out


def test_round10_stray_delimiter_ends_the_scan():
    out = redactor(payload_names=()).redact("value > <Header><MessageID>1</MessageID></Header>")
    assert re.fullmatch(r"value <VALUE-\d+>", out), out


def test_round15_a_failed_json_candidate_ends_the_text():
    # since round 15 a failed candidate leaves a structure open: the rest of the text is one value
    out = redactor(payload_names=()).redact('[1/3] Sending {"status": [1, true]} done')
    assert re.fullmatch(r"<VALUE-\d+>", out), out
    # brackets that do not start JSON stay plain text
    assert (
        redactor(payload_names=()).redact("[ERROR] [Communication2] done") == "[ERROR] [Communication2] done"
    )


def test_round10_expat_is_the_authority_but_not_alone():
    from dmslog.report import redact as module

    for good in ("<a/>", "<a b='1'>x &amp; &#65;<!-- c --><![CDATA[d]]><?p e?></a>", '<n:a xmlns:n="u"/>'):
        assert module._expat_accepts(good, 0, len(good)), good
    for bad in ('<a b="1" b="2"/>', "<a>&nope;</a>", "<a>&#0;</a>", "<a><!-- x ---></a>", "<n:a/>"):
        assert not module._expat_accepts(bad, 0, len(bad)), bad
    # the module's own check still applies on top (expat would accept a DOCTYPE)
    assert module._xml_region_end("<!DOCTYPE a><a/>", 0) is None


# -- review round 11: complete strings when cutting, JSON inside XML text, linear JSON candidates -----

ROUND11 = [
    ("1 single-quoted text", "value '<MonitorReady/>'"),
    ("1 escaped quotes in a JSON string", '"value \\"<MonitorReady/>\\""'),
    ("1 escaped quote then markup", '"a\\" <MonitorReady/>'),
    ("1 unterminated double quote", 'value "open <MonitorReady/>'),
    ("1 unterminated single quote", "value 'open <MonitorReady/>"),
    ("1 single-quoted with an escaped quote", "value 'a\\' <MonitorReady/>'"),
    ("1 failing string inside a weak candidate", '[0, "abc\\q <MonitorReady/>"]'),
    ("1 control character inside a weak candidate", '[0, "abc\u0001 <MonitorReady/>"] x'),
    ("2 invalid JSON in an XML text node", '<Body>{"status":"MonitorReady":0}</Body>'),
    ("2 JSON split over two text nodes", '<A>{</A><B>"MonitorReady": 1</B>'),
    ("2 JSON with prose in a text node", '<Body>sent {"MonitorReady": 1} ok</Body>'),
    ("2 JSON in an attribute value", "<Body a='{\"MonitorReady\": 1}'/>"),
    ("2 JSON with a trailing comma", '<Body>{"MonitorReady": 1,}</Body>'),
]


@pytest.mark.parametrize("finding,text", ROUND11, ids=[c[0] for c in ROUND11])
def test_round11_variants_do_not_leak(finding, text):
    r = redactor(payload_names=())
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
    ):
        assert "MonitorReady" not in out, out


def test_round11_valid_json_in_an_xml_text_node_keeps_its_skeleton():
    out = redactor(payload_names=()).redact('<Body>{"status": "x", "ok": true}</Body>', payload=True)
    assert out.startswith('<Body>{"status": "<VALUE-') and out.endswith('"ok": true}</Body>')


def test_round11_json_scanner_agrees_with_the_json_module():
    import json as jsonmod
    import random

    from dmslog.report.redact import _json_scan

    rng = random.Random(11)
    alphabet = list('{}[],:"\\ 01-.eE+tfnrua') + [
        "true",
        "false",
        "null",
        '"a"',
        "\\u00e9",
        "12",
        "-0.5e3",
        "\x01",
    ]
    for _ in range(20_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
        if text[:1].isspace() or text[-1:].isspace():
            continue
        try:
            jsonmod.loads(text, strict=True, parse_constant=lambda c: 1 / 0)
            expected = True
        except (ValueError, ZeroDivisionError):
            expected = False
        end, ok = _json_scan(text, 0)
        assert (ok and end == len(text)) == expected, text


@pytest.mark.parametrize("unit", ["[0x] ", "{x} ", "[1, 2 ", '[0, "a', "[tru] ", "[-] "])
def test_round11_failed_json_candidates_cost_linear_time(unit):
    r = redactor(payload_names=())

    def measure(size: int) -> None:
        r._structure_spans(_repeat(unit, size), payload=False)

    _assert_linear(measure, 100_000, 8, unit)  # the round 10 code grew about 50x here (10.5 s)


# -- review round 12: what a failed JSON candidate consumed is one value -------------------------------

ROUND12 = [
    ("failed array holding an object", '[0,{"status":"MonitorReady"},x]'),
    ("truncated array holding an object", '[0,{"status":"MonitorReady"}'),
    ("invalid escape before an object", '[0,"a\\q",{"status":"MonitorReady"}]'),
    ("integer past the digit limit", '[0,{"status":"MonitorReady"},' + "1" * 5000 + "]"),
    ("failed array holding an array", '[1, ["MonitorReady"], x]'),
    ("failed object value after a number", '[-1, {"a": {"b": "MonitorReady"}} ;'),
]


@pytest.mark.parametrize("finding,text", ROUND12, ids=[c[0] for c in ROUND12])
def test_round12_failed_json_candidates_never_reach_the_free_text_allowlist(finding, text):
    r = redactor(payload_names=())
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
        r.redact("before " + text + " after"),
    ):
        assert "MonitorReady" not in out, out


def test_round12_json_scanner_and_json_module_agree_on_limits():
    import json as jsonmod

    from dmslog.report.redact import _json_scan

    for text in ("[" + "1" * 5000 + "]", "[" * 600 + "]" * 600, "1" * 4300, "-" + "9" * 4301, "1e5000"):
        try:
            jsonmod.loads(text)
            module = True
        except (ValueError, RecursionError):
            module = False
        end, ok = _json_scan(text, 0)
        scanner = ok and end == len(text)
        assert not scanner or module, text  # the scanner never accepts what the module refuses


# -- review round 13: the failing token itself is consumed before resuming -----------------------------

ROUND13 = [
    ("bad escape in a value", '[0,{"status":"MonitorReady \\q"}]'),
    ("bad escape in a key", '[0,{"MonitorReady \\q":1}]'),
    ("raw newline in a string", '[0,{"status":"MonitorReady\nx"}]'),
    ("raw control character in a string", '[0,{"status":"MonitorReady\u0001x"}]'),
    ("bad unicode escape", '[0,{"status":"MonitorReady \\u12G4"}]'),
    ("unterminated string", '[0,{"status":"MonitorReady'),
    ("bad number", "[0, 0xMonitorReady]"),
    ("bad literal", "[0, truMonitorReady]"),
    ("unexpected token", '[0, x, "MonitorReady"]'),
    ("unexpected string", '[0 "MonitorReady"]'),
    ("single-quoted string", "[0,'MonitorReady']"),
    ("missing colon", '[0,{"MonitorReady" 1}]'),
    ("truncated after a value", '[0,{"k":"MonitorReady"'),
    ("string closed against a word", '[0,{"k":"MonitorReady"x}]'),
]


@pytest.mark.parametrize("finding,text", ROUND13, ids=[c[0] for c in ROUND13])
def test_round13_the_failing_token_never_reaches_the_allowlist(finding, text):
    r = redactor(payload_names=())
    sentence = "before " + text + " after"
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
        r.redact(sentence),
        r.redact(sentence, spans=r.record_spans(sentence, len(sentence), None)),
    ):
        assert "MonitorReady" not in out, out


def test_round13_quoted_strings_between_regions_are_values():
    out = redactor(payload_names=()).redact('Sending "MonitorReady" {"status": "x"} done')
    assert out.startswith('Sending "<VALUE-') and '{"status": "<VALUE-' in out and out.endswith(" done")


# -- review round 14: one path to the end of the text, an allowlist guard, lone surrogates --------------

ROUND14 = [
    ("'>' starting the failed token", "[0,> <Data/> MonitorReady"),
    ("'<' starting the failed token", "[0,< <Data/> MonitorReady"),
    ("valid tag right after a failed token", "[0,<Data/> MonitorReady"),
    ("'>' inside the failed token", "[0, x>y <Data/> MonitorReady"),
    ("quoted '<' in a failed candidate", '[0, "<a>", x] MonitorReady'),
    ("brace left after a failed string", '[0,{"s":"\\q"}] MonitorReady'),
]


@pytest.mark.parametrize("finding,text", ROUND14, ids=[c[0] for c in ROUND14])
def test_round14_recovery_never_reopens_the_allowlist(finding, text):
    r = redactor(payload_names=())
    sentence = "before " + text
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
        r.redact(sentence, spans=r.record_spans(sentence, len(sentence), None)),
    ):
        assert "MonitorReady" not in out, out


LONE_SURROGATES = {
    "XML text": "<a>{s}</a>",
    "attribute value": '<a b="{s}"/>',
    "JSON string": '{{"k": "{s}"}}',
    "comment": "<!-- {s} -->",
    "CDATA": "<a><![CDATA[{s}]]></a>",
    "free text": "free {s} text",
    "JSON key": '{{"{s}": 1}}',
}


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("where", LONE_SURROGATES)
def test_round14_lone_surrogates_never_crash_and_never_reach_the_output(where, surrogate):
    r = redactor(payload_names=())
    text = LONE_SURROGATES[where].format(s=surrogate)
    for out in (
        r.redact(text),
        r.redact(text, payload=True),
        r.redact(text, spans=r.record_spans(text, len(text), None)),
    ):
        out.encode("utf-8")  # strict: raises if a lone surrogate survived


def test_round14_lone_surrogates_through_the_export_and_the_value_map(tmp_path, monkeypatch):
    # SQLite cannot even store a lone surrogate, so the rows are injected after reading
    from dmslog.report import export as export_module
    from dmslog.report.redact import write_value_map

    real_fetch = export_module._fetch_rows

    def fetch_with_surrogates(conn, ids):
        rows = real_fetch(conn, ids)
        for row in rows.values():
            row.msg = "user: Ana\udfff Quintanilha <a>\ud800</a> " + row.msg
        return rows

    monkeypatch.setattr(export_module, "_fetch_rows", fetch_with_surrogates)
    conn = _edited(tmp_path, [])
    try:
        result = build_report(
            conn, Selection(ids=(2, 7)), ExportOptions(anonymize=True, profile=Profile()), now=NOW
        )
        plain = build_report(conn, Selection(ids=(2,)), ExportOptions(anonymize=False), now=NOW)
    finally:
        conn.close()
    for part in [*result.parts, *plain.parts]:
        part.text.encode("utf-8")  # strict: no lone surrogate reaches any output
    assert all("Quintanilha" not in part.text for part in result.parts)
    path = write_value_map(tmp_path, "case", result.redactor)
    assert path is not None and path.read_bytes().decode("utf-8")


# -- review round 15: one structural state across the first line and the continuation -------------------

ROUND15_FIRST_LINES = {
    "stray '>'": "[0,> <Data/>",
    "stray '<'": "value < 3 <Data/>",
    "unterminated string in JSON": '{"k": "open',
    "failed JSON candidate": "[0, x",
    "open JSON object": '{"k":',
    "invalid XML region": "<Body a=",
    "open XML element": "<Body><Note>",
    "lone surrogate in markup": "x \ud800 <a>",
}


@pytest.mark.parametrize("state", ROUND15_FIRST_LINES)
def test_round15_record_spans_carry_the_state_into_the_continuation(state):
    r = redactor()
    msg = ROUND15_FIRST_LINES[state]
    text = msg + "\nMonitorReady and more"
    whole = r.redact(text)
    split = r.redact(text, spans=r.record_spans(text, len(msg)))
    assert "MonitorReady" not in whole and "MonitorReady" not in split, (whole, split)
    templated = "CRS request is rejected by DMZ validation rules: " + msg
    text = templated + "\nMonitorReady and more"
    out = r.redact(text, spans=r.record_spans(text, len(templated), "WFM-130C"))
    assert "MonitorReady" not in out, out


@pytest.mark.parametrize("state", ROUND15_FIRST_LINES)
def test_round15_the_export_carries_the_state_into_the_continuation(tmp_path, state, monkeypatch):
    # injected after reading (SQLite cannot store a lone surrogate)
    from dmslog.report import export as export_module

    real_fetch = export_module._fetch_rows
    msg = ROUND15_FIRST_LINES[state]

    def fetch(conn, ids):
        rows = real_fetch(conn, ids)
        row = rows[2]
        row.msg, row.cont = msg, "MonitorReady and more\nsecond line"
        return rows

    monkeypatch.setattr(export_module, "_fetch_rows", fetch)
    conn = _edited(tmp_path, [])
    try:
        result = build_report(
            conn, Selection(ids=(2,)), ExportOptions(anonymize=True, profile=Profile()), now=NOW
        )
    finally:
        conn.close()
    text = "".join(p.text for p in result.parts)
    text.encode("utf-8")
    assert "MonitorReady" not in text


# -- review round 16: the record state through continuation parsers, read cuts and payloads ------------


def _export_row(
    tmp_path,
    monkeypatch,
    msg,
    cont,
    *,
    tpl=None,
    payloads=None,
    cap=None,
    include_payloads=True,
    selection=None,
    context=0,
):
    from dmslog.report import export as export_module

    if cap is not None:
        monkeypatch.setattr(export_module, "ROW_MSG_CHARS", cap)
    conn = _edited(
        tmp_path,
        [
            ("UPDATE entry SET msg = ?, tpl_id = 0 WHERE id = 2", (msg,)),
            ("DELETE FROM entry_body WHERE entry_id = 2", ()),
            ("INSERT INTO entry_body(entry_id, cont) VALUES (2, ?)", (cont,)),
            ("DELETE FROM payload WHERE entry_id = 2", ()),
        ],
    )
    real_fetch = export_module._fetch_rows

    def fetch(c, ids):
        rows = real_fetch(c, ids)
        if tpl is not None:
            rows[2].tpl = tpl
        return rows

    monkeypatch.setattr(export_module, "_fetch_rows", fetch)
    if payloads is not None:
        monkeypatch.setattr(export_module, "_fetch_payloads", lambda c, ids: payloads)
    try:
        result = build_report(
            conn,
            selection or Selection(ids=(2,)),
            ExportOptions(
                anonymize=True, include_payloads=include_payloads, profile=Profile(), context=context
            ),
            now=NOW,
        )
    finally:
        conn.close()
    return "".join(p.text for p in result.parts)


def test_round16_continuation_parsers_respect_the_record_state(tmp_path, monkeypatch):
    out = _export_row(
        tmp_path, monkeypatch, "Detected errors: [0, x", "WFM-081 MonitorReady", tpl="GEN-ERR-03"
    )
    assert "WFM-081" not in out and "MonitorReady" not in out
    r = redactor()
    msg = "[Communication2] - By call count:"
    text = msg + '\n{"data":\n12345\n}'
    out = r.redact(text, spans=r.record_spans(text, len(msg), "GEN-STA-03"))
    assert "12345" not in out, out


def test_round16_a_read_cut_makes_the_state_unknown(tmp_path, monkeypatch):
    msg = "SecretQ " * 6250 + '{"k":"open'
    out = _export_row(tmp_path, monkeypatch, msg, "MonitorReady and more", cap=40_000)
    assert "MonitorReady" not in out


def test_round16_payloads_inherit_the_record_state(tmp_path, monkeypatch):
    from dmslog.report import export as export_module

    msg, cont = '{"k":"open', "<MonitorReady/>"
    out = _export_row(tmp_path, monkeypatch, msg, cont)  # auto-detected payload
    assert "MonitorReady" not in out
    start = len(msg) + 1
    table = {2: [export_module._Payload(2, "request", "in", "", "", "xml", start, start + len(cont))]}
    out = _export_row(tmp_path, monkeypatch, msg, cont, payloads=table)  # payload from the table
    assert "MonitorReady" not in out
    # a safe record keeps the payload skeleton
    out = _export_row(tmp_path, monkeypatch, "Content being sent to CRS", "<Header><Note>x</Note></Header>")
    assert "<Header><Note><VALUE-" in out


# -- review round 17: a payload that starts at a safe delimiter, and linear clipping ---------------------

R17_MSG = "Content being sent to CRS"
# (continuation, text the whole record hides, where the payload starts in the continuation)
ROUND17 = [
    ("<Header><![CDATA[<MonitorReady/>]]></Header>", "MonitorReady", "[CDATA["),  # the reported case
    ("<Header><!-- <MonitorReady/> --></Header>", "MonitorReady", "<MonitorReady"),
    ("<Header><!-- <MonitorReady/> --></Header>", "MonitorReady", " <Monitor"),
    ("<Header><?pi <MonitorReady/> ?></Header>", "MonitorReady", "<MonitorReady"),
    ('{"note": "<MonitorReady/>"}', "MonitorReady", "<MonitorReady"),
    ('{"note": "<MonitorReady/>"}', "MonitorReady", "MonitorReady"),
    ("<Header><![CDATA[<MonitorReady/>]]></Header>", "MonitorReady", "onitorReady"),  # mid-token
    ("<Header><![CDATA[<MonitorReady/>]]></Header>", "MonitorReady", "eader>"),  # mid-name
]


def _table(msg, cont, ranges):
    from dmslog.report import export as export_module

    base = len(msg) + 1
    return {
        2: [export_module._Payload(2, "request", "in", "", "", "xml", base + a, base + b) for a, b in ranges]
    }


@pytest.mark.parametrize(("cont", "hidden", "at"), ROUND17)
@pytest.mark.parametrize("where", ["ids", "event", "context"])
def test_round17_a_payload_never_shows_what_the_whole_record_hides(
    tmp_path, monkeypatch, cont, hidden, at, where
):
    whole = redactor().redact(
        R17_MSG + "\n" + cont, spans=redactor().record_spans(R17_MSG + "\n" + cont, len(R17_MSG))
    )
    assert hidden not in whole  # the premise: the whole record hides it
    start = cont.index(at)
    selection = {"ids": None, "event": Selection(events=(1,)), "context": Selection(ids=(3,))}[where]
    out = _export_row(
        tmp_path,
        monkeypatch,
        R17_MSG,
        cont,
        payloads=_table(R17_MSG, cont, [(start, len(cont))]),
        selection=selection,
        context=1 if where == "context" else 0,
    )
    assert "== Payloads ==" in out and hidden not in out, out


def test_round17_several_payloads_of_one_record(tmp_path, monkeypatch):
    cont = "<Header><![CDATA[<MonitorReady/>]]><!-- <MonitorReady/> --><?p <MonitorReady/> ?></Header>"
    starts = [cont.index("[CDATA["), cont.index("<MonitorReady", cont.index("<!--")), cont.index("<?p")]
    ranges = [(starts[0], starts[1] - 2), (starts[1], starts[2] - 1), (starts[2], len(cont))]
    out = _export_row(tmp_path, monkeypatch, R17_MSG, cont, payloads=_table(R17_MSG, cont, ranges))
    assert out.count("== Payloads ==") == 1 and "MonitorReady" not in out, out
    # a safe record keeps its payload skeleton (readability)
    safe = "<Header><Note>x</Note></Header>"
    out = _export_row(tmp_path, monkeypatch, R17_MSG, safe, payloads=_table(R17_MSG, safe, [(0, len(safe))]))
    assert "<Header><Note><VALUE-" in out


@pytest.mark.parametrize("include_payloads", [True, False])
def test_round17_many_payloads_cost_linear_time(tmp_path, monkeypatch, include_payloads):
    from dmslog.report import export as export_module

    unit = "<Id>x</Id>"
    conn = _edited(tmp_path, [("UPDATE entry SET tpl_id = 0 WHERE id = 2", ())])
    real_fetch = export_module._fetch_rows
    options = ExportOptions(anonymize=True, include_payloads=include_payloads, profile=Profile())

    def measure(count):
        cont = unit * count  # one payload per element: the record spans and the payloads both grow
        table = _table(R17_MSG, cont, [(i * len(unit), (i + 1) * len(unit)) for i in range(count)])

        def fetch(c, ids):
            rows = real_fetch(c, ids)
            rows[2].msg, rows[2].cont, rows[2].tpl = R17_MSG, cont, None
            return rows

        monkeypatch.setattr(export_module, "_fetch_rows", fetch)
        monkeypatch.setattr(export_module, "_fetch_payloads", lambda c, ids: table)
        start = time.perf_counter()
        build_report(conn, Selection(ids=(2,)), options, now=NOW)
        return time.perf_counter() - start

    try:
        _assert_linear(measure, 2000, 4, ("many payloads", include_payloads))
    finally:
        conn.close()
