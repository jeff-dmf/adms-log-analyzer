"""The .txt export: format, parts, selections, payloads, privacy scan of the synthetic case.

Every sensitive value in the fixture is fictitious (tests/fixtures/export/build_case.py).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from dmslog.report import export
from dmslog.report.export import (
    ExportError,
    ExportOptions,
    Selection,
    build_report,
    parse_options,
    parse_selection,
)
from dmslog.report.redact import CATEGORIES, Profile
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "export"
_spec = importlib.util.spec_from_file_location("export_case", FIXTURES / "build_case.py")
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case"] = case_module
_spec.loader.exec_module(case_module)

SENSITIVE = case_module.SENSITIVE
USER_TERMS = case_module.USER_TERMS
NOW = datetime(2030, 10, 15, 11, 0, 0)
PROFILE = Profile(terms_word=tuple(USER_TERMS))
STANDARD = Profile(terms_word=tuple(USER_TERMS), mode="standard")  # size mechanics: no strict collapsing


@pytest.fixture(scope="module")
def case_db(tmp_path_factory):
    return case_module.build_export_case(tmp_path_factory.mktemp("export") / "case.sqlite")


@pytest.fixture
def conn(case_db):
    connection = connect(case_db, readonly=True)
    yield connection
    connection.close()


def run(conn, selection, *, profile=PROFILE, **options):
    options.setdefault("anonymize", True)
    return build_report(
        conn,
        selection,
        ExportOptions(profile=profile, **options),
        now=NOW,
        version="0.0.0-test",
    )


def full_text(result):
    return "".join(part.text for part in result.parts)


WHOLE = Selection(events=(1,), ids=(7,))


# -- format -----------------------------------------------------------------------------------


def test_golden_anonymized_report_with_payloads_is_byte_exact(conn):
    result = run(conn, WHOLE, include_payloads=True, context=1)
    assert len(result.parts) == 1
    expected = (FIXTURES / "golden_anonymized.txt").read_bytes()
    assert result.parts[0].text.encode("utf-8") == expected


def test_format_rules(conn):
    text = run(conn, WHOLE, include_payloads=True, context=1).parts[0].text
    raw = text.encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    assert raw.endswith(b"\r\n") and raw.count(b"\n") == raw.count(b"\r\n")  # CRLF only
    lines = text.split("\r\n")
    assert lines[0].startswith("dmslog report - ") and lines[4] == (
        "Time shown as logged (local server time, no conversion)."
    )
    frame = [line for line in lines if line.startswith("==")]
    assert frame == [
        "== Summary ==",
        "== Events ==",
        "== Records ==",
        "== Payloads ==",
        "== Redaction legend ==",
    ]
    assert all(line.isascii() for line in frame)
    assert "\x00" not in text and "\t" not in text


def test_header_reports_redaction_state_and_ers(conn):
    on = run(conn, WHOLE).parts[0].text
    assert 'Redaction: ON (profile "default", strict mode)' in on
    assert "Source: ERS 2030-10-15 09:59:00 (<HOST-1>) - Enterprise+Sensitive" in on
    assert "dmslog 0.0.0-test" in on and "test-pack" not in on  # the pack name comes from the case
    off = run(conn, WHOLE, anonymize=False).parts[0].text
    assert "pack test-pack" in off
    assert "Redaction: OFF (explicit; this report is NOT anonymized)" in off
    assert "(ACMEHOST01)" in off
    assert "Anonymization was turned off for this export: nothing was replaced." in off


@pytest.mark.parametrize("mode", ["strict", "standard"])
def test_every_header_text_from_the_profile_or_the_case_is_anonymized(tmp_path, mode):
    # review round 4: the profile name reached the header literally
    ro = edited_case(
        tmp_path,
        [
            ("UPDATE meta SET value = 'CUSTOMERQ-pack' WHERE key = 'pack_version'", ()),
            ("UPDATE collection SET ers_ts = 'CUSTOMERQ'", ()),
        ],
    )
    profile = Profile(name="CUSTOMERQ", terms_word=("CUSTOMERQ",), mode=mode)
    try:
        result = run(ro, WHOLE, profile=profile, max_bytes=2000)
    finally:
        ro.close()
    for part in result.parts:
        assert "CUSTOMERQ" not in part.text and "CUSTOMERQ" not in part.name
    assert "customerq" not in repr(result.legend).casefold() and not result.warnings


def test_part_names_carry_no_case_text(conn):
    names = [p.name for p in run(conn, WHOLE, max_bytes=2000, include_payloads=True).parts]
    assert all(re.fullmatch(r"dmslog-report-20301015-110000(-part\dof\d)?\.txt", n) for n in names)


def test_summary_and_event_lines(conn):
    text = run(conn, Selection(events=(1,)), anonymize=False).parts[0].text
    assert (
        "5 selected records, 1 events, 0 payloads, window 2030-10-15 10:00:00.000 .. 2030-10-15 10:00:00.090"
        in text
    )
    assert "AWFM SendWorks  count=1  fail=1  business_error=0  p90=90ms" in text
    assert "status=fail  duration=90ms  key=3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c" in text
    assert "error codes: WFM-081" in text and "exception: System.TimeoutException" in text
    assert "note: ACME called about INC0045123" in text
    assert "--- log excerpt (WfmAdapter_4001.log, lines 10-16) ---" in text


# -- selections -------------------------------------------------------------------------------


def test_selection_by_ids_with_context_marks_the_selected_record(conn):
    text = run(conn, Selection(ids=(3,)), anonymize=False, context=1).parts[0].text
    assert "[R1] 2030-10-15 10:00:00.040  WfmAdapter_4001.log  line 12" in text
    assert "  > 2030-10-15 10:00:00.040" in text
    assert "    2030-10-15 10:00:00.020" in text and "    2030-10-15 10:00:00.060" in text
    assert "10:00:00.000" not in text.split("== Records ==")[1]  # context 1 stops at the neighbours


def test_overlapping_context_is_merged_into_one_excerpt(conn):
    text = run(conn, Selection(ids=(2, 3, 4)), anonymize=False, context=1).parts[0].text
    assert text.count("--- log excerpt") == 1
    assert "(+2 more selected records in this excerpt)" in text


def test_selection_by_filter(conn):
    result = run(conn, Selection(filter={"sev": {"le": 2}}), anonymize=False)
    text = result.parts[0].text
    assert "1 selected records" in text and "SendWorks failed with code WFM-081" in text
    assert "event: SendWorks status=fail" in text  # the record knows its event


def test_filter_and_ids_and_events_combine(conn):
    result = run(conn, Selection(filter={"sev": {"le": 2}}, ids=(7,), events=(1,)))
    assert result.counts["events"] == 1 and result.counts["records"] == 7


def test_missing_ids_and_events_are_warned_and_nothing_is_an_error(conn):
    result = run(conn, Selection(ids=(7, 999), events=(1, 55)))
    assert len(result.warnings) == 2
    with pytest.raises(ExportError) as info:
        run(conn, Selection(ids=(999,)))
    assert info.value.code == "nothing"


def test_invalid_filter_is_reported_as_a_query_error(conn):
    from dmslog.store.filters import QueryInputError

    with pytest.raises(QueryInputError):
        run(conn, Selection(filter={"nope": 1}))


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"ids": []},
        {"ids": [0]},
        {"ids": [True]},
        {"ids": "1"},
        {"events": [1, "x"]},
        {"filter": []},
    ],
)
def test_bad_selection(raw):
    with pytest.raises(ExportError):
        parse_selection(raw)


def test_selection_accepts_event_or_events_and_dedupes():
    assert parse_selection({"event": 4}).events == (4,)
    assert parse_selection({"events": [3, 3, 5]}).events == (3, 5)
    assert parse_selection({"filter": {"sev": 2}}).filter == {"sev": 2}


# -- options ----------------------------------------------------------------------------------


def test_anonymize_has_no_default():
    for raw in ({}, {"anonymize": None}, {"anonymize": "yes"}, {"anonymize": 1}, {"include_payloads": True}):
        with pytest.raises(ExportError) as info:
            parse_options(raw)
        assert info.value.code == "anonymize_required"
    with pytest.raises(ExportError):
        parse_options(None)
    assert parse_options({"anonymize": False}).anonymize is False


@pytest.mark.parametrize(
    "extra",
    [
        {"context": -1},
        {"context": 101},
        {"context": True},
        {"context": "2"},
        {"max_bytes": 1999},
        {"max_bytes": 2_000_001},
        {"include_payloads": "yes"},
        {"split": 0},
    ],
)
def test_bad_options(extra):
    with pytest.raises(ExportError) as info:
        parse_options({"anonymize": True, **extra})
    assert info.value.code == "bad_option"


# -- payloads ---------------------------------------------------------------------------------


def test_payloads_stay_out_unless_asked(conn):
    text = run(conn, WHOLE, anonymize=False, context=1).parts[0].text
    assert "<SendWorksRequest>" not in text and "Maria Souza" not in text
    assert "[payload P1: payload, 250 characters - omitted (payloads not included)]" in text
    assert "[payload P2: request, 250 characters - omitted (payloads not included)]" in text
    assert "== Payloads ==" not in text


def test_included_payloads_are_redacted_per_element(conn):
    text = run(conn, WHOLE, include_payloads=True, context=1).parts[0].text
    assert "== Payloads ==" in text
    assert "<Name><PERSON-1></Name>" in text and "<Address><PERSON-2></Address>" in text
    assert "Maria" not in text and "Acacias" not in text
    assert (
        "[P2] 2030-10-15 10:00:00.125  WfmAdapter_4001.sensitive.log line 3  "
        "request in WFM SendWorks  (xml, 250" in text
    )
    plain = run(conn, WHOLE, include_payloads=True, anonymize=False, context=1).parts[0].text
    assert "<Name>Maria Souza</Name>" in plain


def test_the_person_category_controls_the_element_rules(conn):
    profile = Profile(categories={"person": False}, mode="standard")
    text = run(conn, WHOLE, include_payloads=True, profile=profile).parts[0].text
    assert "<Name>Maria Souza</Name>" in text
    assert "(21) 98765-4321" not in text  # the phone category still works on the payload text
    # strict mode: without the element rule the payload token rule still hides the name
    strict = run(conn, WHOLE, include_payloads=True, profile=Profile(categories={"person": False}))
    assert "Maria" not in strict.parts[0].text and "Souza" not in strict.parts[0].text


# -- parts and size ---------------------------------------------------------------------------


def test_numbered_parts_respect_the_limit_and_repeat_the_title(conn):
    single = run(conn, WHOLE, include_payloads=True, context=1)
    parts = run(conn, WHOLE, include_payloads=True, context=1, max_bytes=2000).parts
    assert len(parts) >= 3
    total = len(parts)
    for index, part in enumerate(parts, 1):
        assert len(part.text.encode("utf-8")) <= 2000
        title = single.parts[0].text.split("\r\n")[0]
        assert part.text.startswith(f"{title} - part {index}/{total}\r\n")
        assert part.name.endswith(f"-part{index}of{total}.txt")
        if index > 1:
            assert part.text.split("\r\n")[1] == "(continued from the previous part)"

    # the same body, in order, with the same markers
    def body(text_parts, skip_first):
        lines = []
        for k, part in enumerate(text_parts):
            lines.extend(part.text.split("\r\n")[skip_first if k else 6 :])
        return [line for line in lines if line]

    assert body(parts, 3) == body(single.parts, 3)


def test_unsplit_report_is_cut_with_a_notice(conn):
    result = run(conn, WHOLE, include_payloads=True, context=1, max_bytes=2500, split=False)
    (part,) = result.parts
    text = part.text
    assert len(text.encode("utf-8")) <= 2500
    assert "more lines not shown: the report exceeds 2500 bytes; export in numbered parts]" in text
    assert text.rstrip().endswith("review the text before sharing it.")  # the legend always survives


def test_long_lines_never_break_the_limit(tmp_path):
    db = case_module.build_export_case(tmp_path / "long.sqlite")
    rw = connect(db)
    with rw:
        rw.execute("UPDATE entry SET msg = ? WHERE id = 2", ("w" * 45_000,))
    rw.close()
    ro = connect(db, readonly=True)
    try:
        result = run(ro, Selection(ids=(2,)), max_bytes=3000, profile=STANDARD)
    finally:
        ro.close()
    assert all(len(p.text.encode("utf-8")) <= 3000 for p in result.parts)
    text = full_text(result)
    assert "...[line cut]" in text
    assert "more characters of this record omitted" in text  # 45,000 characters > MAX_ENTRY_CHARS


def test_bytes_not_characters_are_counted(tmp_path):
    db = case_module.build_export_case(tmp_path / "utf8.sqlite")
    rw = connect(db)
    with rw:
        rw.execute("UPDATE entry SET msg = ? WHERE id = 2", ((chr(0xE7) + chr(0xE3) + "o ") * 400,))
    rw.close()
    ro = connect(db, readonly=True)
    try:
        result = run(ro, Selection(ids=(2,)), max_bytes=2500)
    finally:
        ro.close()
    assert all(len(p.text.encode("utf-8")) <= 2500 for p in result.parts)


def test_record_and_text_caps_add_warnings(conn, monkeypatch):
    monkeypatch.setattr(export, "MAX_ENTRIES", 3)
    result = run(conn, Selection(ids=(1, 2, 3, 4, 5, 6, 7)), context=1)
    assert any("log records" in w for w in result.warnings)
    monkeypatch.setattr(export, "MAX_ENTRIES", 2000)
    monkeypatch.setattr(export, "MAX_TOTAL_CHARS", 100)
    result = run(conn, Selection(events=(1,)))
    assert any("characters of log text" in w for w in result.warnings)


# -- legend -----------------------------------------------------------------------------------


def test_legend_lists_categories_and_counts_but_no_values(conn):
    result = run(conn, WHOLE, include_payloads=True)
    legend = {row["category"]: row for row in result.legend}
    assert legend["guid"]["distinct"] == 2 and legend["term"]["replaced"] >= 5
    text = full_text(result)
    section = text.split("== Redaction legend ==")[1]
    assert "GUIDs: 2 distinct values" in section
    for values in SENSITIVE.values():
        assert not any(v in section for v in values)
    assert result.redactor is not None and result.redactor.value_map()["<GUID-1>"] == case_module.GUID_A


# -- 7.6-lite: nothing sensitive survives, and switching a category off lets it through ---------

SELECTIONS = {
    "events": Selection(events=(1,)),
    "ids": Selection(ids=(1, 2, 3, 4, 5, 6, 7)),
    "filter": Selection(filter={"source": [1, 2]}),
    "all": Selection(events=(1,), ids=(7,), filter={"source": [1, 2]}),
}


@pytest.mark.parametrize("payloads", [False, True], ids=["no-payload", "payload"])
@pytest.mark.parametrize("name", SELECTIONS)
@pytest.mark.parametrize("context", [0, 2])
def test_no_sensitive_value_survives_an_anonymized_export(conn, name, payloads, context):
    result = run(conn, SELECTIONS[name], include_payloads=payloads, context=context)
    text = full_text(result)
    leaked = [v for values in SENSITIVE.values() for v in values if v.casefold() in text.casefold()]
    leaked += [t for t in USER_TERMS if t.casefold() in text.casefold()]
    if not payloads:
        leaked += [v for v in SENSITIVE["person"] if v in text]
    assert leaked == []
    assert "<SendWorksRequest>" not in text or payloads


def test_the_scan_has_teeth_when_anonymization_is_off(conn):
    text = full_text(run(conn, SELECTIONS["all"], include_payloads=True, anonymize=False, context=2))
    missing = [v for values in SENSITIVE.values() for v in values if v not in text]
    assert missing == []  # every planted value is in the unredacted export, so the scan above is meaningful


RELATED_OFF = {"url": ("host",)}  # the host inside a URL is a host too


@pytest.mark.parametrize("category", CATEGORIES)
def test_turning_a_category_off_lets_its_values_appear(conn, category):
    flags = {c: c != category and c not in RELATED_OFF.get(category, ()) for c in CATEGORIES}
    profile = Profile(categories=flags, mode="standard")  # strict mode keeps a net under every category
    result = run(conn, SELECTIONS["all"], include_payloads=True, context=2, profile=profile)
    text = full_text(result)
    assert any(v in text for v in SENSITIVE[category]), category
    others = [v for c, values in SENSITIVE.items() if c != category for v in values]
    # values of the categories that stay on are still hidden (a few are also caught by a related rule)
    still_on = [
        v
        for c, values in SENSITIVE.items()
        if c not in (category, *RELATED_OFF.get(category, ()))
        for v in values
        if v in text
    ]
    assert still_on == [], (category, still_on, others[:1])


def test_exceptions_in_the_profile_survive_the_export(conn):
    profile = Profile(exceptions=("jsilva",), terms_word=tuple(USER_TERMS))
    text = full_text(run(conn, SELECTIONS["events"], profile=profile))
    assert "user: jsilva" in text and "dnaApp_acmeprod" not in text


def test_event_codes_and_operation_names_are_never_replaced(conn):
    profile = Profile(terms_word=("SendWorks", "WFM-081", "AWFM"))
    text = full_text(run(conn, SELECTIONS["events"], profile=profile))
    for kept in ("SendWorks", "WFM-081", "AWFM"):
        assert kept in text


def test_export_never_writes_to_the_case(case_db):
    ro = connect(case_db, readonly=True)
    before = ro.execute("SELECT count(*), sum(length(msg)) FROM entry").fetchone()
    run(ro, SELECTIONS["all"], include_payloads=True, context=3)
    assert ro.execute("SELECT count(*), sum(length(msg)) FROM entry").fetchone() == before
    ro.close()


# -- review round -------------------------------------------------------------------------------


def edited_case(tmp_path, statements):
    """A copy of the fixture case with ``statements`` (sql, params) applied; a read-only connection."""
    db = case_module.build_export_case(tmp_path / f"edited-{uuid.uuid4().hex[:8]}.sqlite")
    rw = connect(db)
    with rw:
        for sql, params in statements:
            rw.execute(sql, params)
    rw.close()
    return connect(db, readonly=True)


def test_the_exception_message_of_an_event_is_anonymized(conn):
    text = full_text(run(conn, Selection(events=(1,))))
    line = next(x for x in text.split("\r\n") if "exception:" in x)
    assert line.strip().startswith("exception: System.TimeoutException: timeout calling <URL-")
    assert "esb.acme" not in line and "jsilva" not in line and "INC0045123" not in line
    off = full_text(run(conn, Selection(events=(1,)), anonymize=False))
    assert "for user: jsilva (INC0045123)" in off


def test_free_text_never_becomes_an_exception(conn):
    # a profile term equal to the message would be shielded if the message were an exception
    message = (
        "System.TimeoutException: timeout calling https://esb.acme-grid.example.com:8443/Services/Wfm.svc"
    )
    profile = Profile(terms_word=("timeout",))
    text = full_text(run(conn, Selection(events=(1,)), profile=profile))
    assert "esb.acme-grid" not in text
    assert message not in text


def test_error_codes_outside_the_catalog_are_not_protected(tmp_path):
    ro = edited_case(
        tmp_path,
        [
            (
                "UPDATE event SET detail = ? WHERE id = 1",
                ('{"error_codes": ["WFM-081", "QRS4T56", "jsilva@mail.example.com"]}',),
            )
        ],
    )
    try:
        text = full_text(run(ro, Selection(events=(1,))))
    finally:
        ro.close()
    line = next(x for x in text.split("\r\n") if "error codes:" in x)
    assert "WFM-081" in line and "QRS4T56" not in line and "jsilva@" not in line


def test_names_with_ids_inside_are_not_protected(tmp_path):
    ro = edited_case(tmp_path, [("UPDATE event SET name = ? WHERE id = 1", (case_module.GUID_A,))])
    try:
        text = full_text(run(ro, Selection(events=(1,))))
    finally:
        ro.close()
    assert case_module.GUID_A not in text


def test_a_value_crossing_the_entry_cut_leaves_no_fragment(tmp_path):
    guid = case_module.GUID_B
    pad = "x " * ((export.MAX_ENTRY_CHARS - 18) // 2)
    pad = pad[: export.MAX_ENTRY_CHARS - 18]
    ro = edited_case(
        tmp_path, [("UPDATE entry SET msg = ? WHERE id = 2", (pad + guid + " tail" + " z" * 3000,))]
    )
    try:
        text = full_text(run(ro, Selection(ids=(2,)), profile=STANDARD))
        strict = full_text(run(ro, Selection(ids=(2,))))
    finally:
        ro.close()
    assert guid[:18] not in text and guid[:8] not in text
    assert "more characters of this record omitted" in text
    assert guid[:8] not in strict


def test_a_value_crossing_the_read_cap_leaves_no_fragment(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "ROW_MSG_CHARS", 100)
    guid = case_module.GUID_B
    ro = edited_case(
        tmp_path, [("UPDATE entry SET msg = ? WHERE id = 2", ("y " * 40 + "id " + guid + " tail",))]
    )
    try:
        text = full_text(run(ro, Selection(ids=(2,))))
    finally:
        ro.close()
    assert guid[:8] not in text and "a1b2c3d4" not in text.casefold()
    assert "characters of this record omitted" in text


def test_long_values_across_the_cut_are_replaced_whole(tmp_path):
    url = "https://esb.acme-grid.example.com/" + "p" * 30_000 + "SECRETTAIL"
    ro = edited_case(tmp_path, [("UPDATE entry SET msg = ? WHERE id = 2", ("go " + url,))])
    try:
        text = full_text(run(ro, Selection(ids=(2,))))
    finally:
        ro.close()
    assert "SECRETTAIL" not in text and "pppp" not in text and "<URL-1>" in text


def test_payload_budget_is_global(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "MAX_TOTAL_CHARS", 3000)
    big = "<Req><Name>Maria Souza</Name>" + "<Item>filler value</Item>" * 100 + "</Req>"
    ro = edited_case(
        tmp_path,
        [
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 6", (big,)),
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 7", (big,)),
            ("UPDATE payload SET char_len = ? WHERE entry_id = 7", (len(big),)),
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 3", ("   at x\n" * 10,)),
        ],
    )
    try:
        result = run(ro, Selection(events=(1,), ids=(7,)), include_payloads=True, context=1)
    finally:
        ro.close()
    text = full_text(result)
    assert any("characters of log text" in w for w in result.warnings)
    assert text.count("filler value") <= 100  # at most one payload fits in the 3000 character budget
    assert "Maria Souza" not in text


def test_payload_text_over_the_global_budget_is_not_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "MAX_TOTAL_CHARS", 10)
    ro = edited_case(tmp_path, [])
    try:
        result = run(ro, Selection(ids=(7,)), include_payloads=True)
    finally:
        ro.close()
    text = full_text(result)
    assert "<SendWorksRequest>" not in text
    assert any("characters of log text" in w for w in result.warnings)


EVASIONS = {
    "percent": [
        ("guid", "id %7B" + case_module.GUID_A + "%7D", case_module.GUID_A),
        ("email", "to maria.souza%40acme-grid.example.com", "maria.souza"),
        ("url", "go https%3A%2F%2Fesb.acme-grid.example.com%2FServices", "esb.acme-grid"),
        ("ip", "ip 10%2E20%2E30%2E40", "10%2E20"),
        ("user", "user%3A%20jsilva", "jsilva"),
    ],
    "unicode": [
        ("email", "to maria.souza\\u0040acme-grid.example.com", "acme-grid"),
        ("url", "go h\\u0074tps://esb.acme-grid.example.com/x", "esb.acme-grid"),
        ("ticket", "inc \\u0030045123", "045123"),
        ("entity", "to maria.souza&#64;acme-grid.example.com", "acme-grid"),
    ],
    "case": [
        ("ticket", "opened inc0045123 and prob0077", "0045123"),
        ("plate", "plate qrs4t56 and abc-1234", "4t56"),
        ("url", "go HTTPS://ESB.Acme-Grid.example.com/x", "acme-grid"),
        ("user", "USER: JSILVA", "JSILVA"),
    ],
}


@pytest.mark.parametrize("family", EVASIONS)
@pytest.mark.parametrize("payloads", [False, True], ids=["no-payload", "payload"])
def test_evasion_variants_do_not_survive(tmp_path, family, payloads):
    for index, (_category, text, secret) in enumerate(EVASIONS[family]):
        ro = edited_case(tmp_path, [("UPDATE entry SET msg = ? WHERE id = 2", (text,))])
        try:
            out = full_text(run(ro, Selection(ids=(2,)), include_payloads=payloads, context=1))
        finally:
            ro.close()
        assert secret.casefold() not in out.casefold(), (family, index)


def test_personal_xml_attributes_and_elements_in_payloads(tmp_path):
    xml = (
        '<Req><Customer Name="Maria Souza" Phone="(21) 98765-4321" Address="Rua das Acacias 45 > bloco B">'
        "<Contact><FirstName>Maria</FirstName></Contact></Customer>"
        '<Address Street="Rua das Acacias 45"/><Name><Part>Maria Souza</Part></Name></Req>'
    )
    ro = edited_case(
        tmp_path,
        [
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 7", (xml,)),
            ("UPDATE payload SET char_len = ? WHERE entry_id = 7", (len(xml),)),
        ],
    )
    try:
        out = full_text(run(ro, Selection(ids=(7,)), include_payloads=True))
    finally:
        ro.close()
    for secret in ("Maria", "Souza", "Acacias", "98765", "bloco"):
        assert secret not in out, secret


def test_title_is_anonymized_before_it_is_cut(tmp_path):
    guid = case_module.GUID_A
    label = "t" * (export.MAX_LABEL_CHARS - 12) + " " + guid
    ro = edited_case(tmp_path, [("UPDATE meta SET value = ? WHERE key = 'name'", (label,))])
    try:
        text = full_text(run(ro, Selection(ids=(2,))))
    finally:
        ro.close()
    assert guid[:8] not in text


def test_payload_metadata_is_anonymized(tmp_path):
    ro = edited_case(
        tmp_path,
        [
            ("UPDATE payload SET operation = ?, peer = ? WHERE entry_id = 7", ("inc0045123", "user: jsilva")),
        ],
    )
    try:
        text = full_text(run(ro, Selection(ids=(7,)), include_payloads=True))
    finally:
        ro.close()
    assert "0045123" not in text and "jsilva" not in text


def test_automatic_exceptions_never_protect_personal_data(tmp_path):
    ro = edited_case(
        tmp_path,
        [
            ("UPDATE event SET name = ? WHERE id = 1", ("inc0045123",)),
            ("UPDATE entry_body SET cont = ? WHERE entry_id = 7", ("<Name>inc0045123</Name>",)),
            ("UPDATE payload SET char_len = ? WHERE entry_id = 7", (len("<Name>inc0045123</Name>"),)),
        ],
    )
    try:
        text = full_text(run(ro, Selection(events=(1,), ids=(7,)), include_payloads=True))
    finally:
        ro.close()
    assert "0045123" not in text
