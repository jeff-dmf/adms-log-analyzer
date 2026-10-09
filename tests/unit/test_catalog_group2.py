"""Template catalog of the Wfm (AWFM) and Ont (AONT) families (M2 task 2.3)."""

# ruff: noqa: E501 - log lines and regex-driven messages are kept on one line, as in test_templates.py

import re

import pytest

from dmslog.parse.envelope import get_envelope
from dmslog.parse.records import Record, parse_bytes
from dmslog.parse.templates import load_catalog

GUID_1 = "00000000-0000-4000-8000-000000000001"
GUID_2 = "00000000-0000-4000-8000-000000000002"
FAMILIES = ("AWFM", "AONT")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(check_examples=True)


def own_templates(catalog, family):
    return [t for t in catalog.families[family].templates]


def all_own(catalog):
    return [(fam, t) for fam in FAMILIES for t in own_templates(catalog, fam)]


def sensitive_of(tpl) -> bool:
    return tpl.stream == "sensitive"


def classify(catalog, family, *, sensitive, msg, cont=None):
    rec = Record(0, 1, 1 + (cont.count("\n") + 1 if cont else 0), 0, 1, 1, 5, msg, cont, False)
    return catalog.for_family(family, sensitive=sensitive).classify(rec)


def parse(data: str):
    return parse_bytes(data.encode("utf-8"), get_envelope("adms"))


# ------------------------------------------------------------------------------ load and shape


def test_both_families_load_and_extend_common(catalog):
    for fam in FAMILIES:
        assert fam in catalog.families
        assert catalog.families[fam].extends == ("_common",)
        assert len(own_templates(catalog, fam)) >= 25


def test_codes_follow_the_documented_prefixes(catalog):
    for fam, prefix in (("AWFM", "WFM-"), ("AONT", "ONT-")):
        for tpl in own_templates(catalog, fam):
            assert tpl.code.startswith(prefix), tpl.code
    # the generic IDs are owned by _common.toml and must not be redefined here
    for _fam, tpl in all_own(catalog):
        assert not tpl.code.startswith("GEN-")


def test_documented_ids_are_resolvable(catalog):
    for code in ("WFM-010", "WFM-012", "WFM-030", "WFM-081", "ONT-002", "ONT-011", "ONT-014", "ONT-020"):
        assert catalog.resolve(code) == code
    # old IDs that moved to the generic library keep resolving
    for old, new in (
        ("WFM-090", "GEN-STA-02"),
        ("ONT-100", "GEN-MSG-01"),
        ("WFM-050", "GEN-ERR-01"),
        ("WFM-099", "GEN-DBG-01"),
    ):
        assert catalog.resolve(old) == new


def test_every_template_has_positive_and_negative_examples(catalog):
    for _fam, tpl in all_own(catalog):
        assert tpl.examples, f"{tpl.code} has no examples"
        assert tpl.counter_examples, f"{tpl.code} has no counter-examples"


def test_examples_are_synthetic(catalog):
    guid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    for _fam, tpl in all_own(catalog):
        for ex in (*tpl.examples, *tpl.counter_examples):
            for found in guid.findall(ex.msg + (ex.cont or "")):
                assert found.lower().startswith("00000000-0000-4000-8000-"), (tpl.code, found)


def test_noise_and_dump_templates_stay_out_of_fts(catalog):
    assert catalog.template("WFM-001").fts is False
    assert catalog.template("WFM-001").sev == 5
    assert catalog.template("ONT-005").sev == 5
    for code in ("WFM-010", "WFM-081", "ONT-002", "ONT-014"):
        assert catalog.template(code).fts is True


# ------------------------------------------------------------------------------ examples


def test_each_example_is_classified_as_its_own_template(catalog):
    for fam, tpl in all_own(catalog):
        sens = sensitive_of(tpl)
        for ex in tpl.examples:
            match = classify(catalog, fam, sensitive=sens, msg=ex.msg, cont=ex.cont)
            assert match is not None and match.code == tpl.code, (tpl.code, ex.msg[:80])
            for name, expected in (ex.fields or {}).items():
                assert match.fields.get(name) == expected, (tpl.code, name)


def test_each_example_matches_only_its_own_template_in_the_family(catalog):
    for fam, tpl in all_own(catalog):
        sens = sensitive_of(tpl)
        for ex in tpl.examples:
            hits = [
                other.code
                for other in own_templates(catalog, fam)
                if other.stream != ("normal" if sens else "sensitive")
                and (
                    (other.regex is not None and other.regex.match(ex.msg.strip()))
                    or (other.text is not None and other.text == ex.msg.strip())
                )
            ]
            assert hits == [tpl.code], (tpl.code, hits, ex.msg[:80])


def test_counter_examples_do_not_match_their_template(catalog):
    for fam, tpl in all_own(catalog):
        for sens in (False, True):
            if tpl.stream == ("normal" if sens else "sensitive"):
                continue
            for ex in tpl.counter_examples:
                match = classify(catalog, fam, sensitive=sens, msg=ex.msg, cont=ex.cont)
                assert match is None or match.code != tpl.code, (tpl.code, ex.msg[:80])


def test_family_examples_do_not_leak_into_the_other_family(catalog):
    # Wfm and Ont lines must never be taken for each other's templates
    for fam, other in (("AWFM", "AONT"), ("AONT", "AWFM")):
        for tpl in own_templates(catalog, fam):
            for ex in tpl.examples:
                match = classify(catalog, other, sensitive=sensitive_of(tpl), msg=ex.msg, cont=ex.cont)
                assert match is None or match.code.startswith("GEN-"), (tpl.code, match and match.code)


def test_negatives_unrelated_lines_have_no_family_template(catalog):
    for fam in FAMILIES:
        for msg in ("", "hello", "Changed crews request received", "[SendIncidents] ", "Sending message"):
            match = classify(catalog, fam, sensitive=False, msg=msg)
            assert match is None or match.code.startswith("GEN-"), (fam, msg, match and match.code)


# ------------------------------------------------------------------------------ Wfm specifics


def test_wfm_request_lifecycle_keys_and_sensitive_reference(catalog):
    start = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg=f'Changed crews request received. Request with ID "{GUID_1.upper()}". Message content wirtten in sensitive log.',
    )
    assert start.code == "WFM-010" and start.refs_sensitive and start.cls == "start"
    assert start.fields["op"] == "Changed crews" and start.fields["request_id"] == GUID_1
    assert ("msgid", GUID_1) in start.keys
    end = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg=f'Changed crews request with ID "{GUID_1}" successfully processed.',
    )
    assert end.code == "WFM-012" and end.cls == "end" and ("msgid", GUID_1) in end.keys


def test_wfm_map_dump_is_cheap_and_wins_over_the_generic_marker(catalog):
    dump = "\n".join(f"\tK{i} - {i}" for i in range(50)) + "\n"
    match = classify(catalog, "AWFM", sensitive=False, msg='New "OMS_CREW_VEHICLE" map content:', cont=dump)
    assert match.code == "WFM-001" and match.fts is False and match.sev_hint == 5
    assert match.fields["map"] == "OMS_CREW_VEHICLE"
    assert match.fields["items"] == 50  # counted, pairs are not kept
    # the other markers of the same map stay with the generic library
    closed = classify(catalog, "AWFM", sensitive=False, msg='"OMS_CREW_VEHICLE" map closed.')
    assert closed.code == "GEN-MAP-01"
    # the Ont has no WFM-001: its dumps (if any) go to the generic template
    ont = classify(catalog, "AONT", sensitive=False, msg='New "X" map content:', cont="\tK - 1\n")
    assert ont.code == "GEN-MAP-01"


def test_wfm_callback_by_stream(catalog):
    normal = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg="Callback received:",
        cont="OMS_CREW_CHANGE_REQ GID: 288230376151711744, Errors count: 1. For Status value check sensitive log.",
    )
    assert normal.code == "WFM-030" and normal.refs_sensitive
    assert normal.fields["gid"] == 288230376151711744 and normal.fields["errors"] == 1
    sens = classify(
        catalog,
        "AWFM",
        sensitive=True,
        msg="Callback received:",
        cont="OMS_CREW_CHANGE_REQ GID: 288230376151711744, Errors count: 1 Status value: Accepted",
    )
    assert sens.code == "WFM-S030" and not sens.refs_sensitive
    assert sens.fields["status"] == "Accepted"
    ims = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg="Callback received:",
        cont="OMS_REQUEST GID: 5, Status value: Approved",
    )
    assert (
        ims.code == "WFM-030"
        and ims.fields["cb_type"] == "OMS_REQUEST"
        and ims.fields["status"] == "Approved"
    )


def test_wfm_detected_errors_split_by_stream_in_the_library(catalog):
    normal = classify(catalog, "AWFM", sensitive=False, msg="Detected errors:", cont="CodeOne\nCodeTwo")
    assert normal.code == "GEN-ERR-01" and normal.fields["codes"] == ["CodeOne", "CodeTwo"]
    sens = classify(catalog, "AWFM", sensitive=True, msg="Detected errors:", cont="Assignment: free text.")
    assert sens.code == "GEN-ERR-03"


def test_wfm_mrid_notice_keeps_spaces(catalog):
    match = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg="[Info] AlternateRadioID value will be ignored for vehicle with MRID 'AB 12.",
    )
    assert match.code == "WFM-040" and match.fields["mrid"] == "AB 12" and ("mrid", "AB 12") in match.keys


def test_wfm_failure_with_stack(catalog):
    msg = (
        'ERROR - Processing "ChangedCrewsEvent" message failed. ExceptionType: "System.TimeoutException" '
        "Reason: Delta has been forwarded to CRS, but callback is not received within 00:00:35 interval. "
        "StackTrace: at Example.Wfm.Updater.Apply(IExternal crews) in F:\\PD\\1234567\\1\\Example\\Updater.cs:line 120"
    )
    cont = (
        "   at Example.Wfm.Processor.Run(IRequest r) in F:\\PD\\1234567\\1\\Example\\Processor.cs:line 40\n"
    )
    match = classify(catalog, "AWFM", sensitive=False, msg=msg, cont=cont)
    assert match.code == "WFM-081" and match.sev_hint == 2 and match.cls == "error"
    assert match.fields["event_type"] == "ChangedCrewsEvent"
    assert ("exc_type", "System.TimeoutException") in match.keys


def test_wfm_dal_proxy_forms(catalog):
    a = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg="Creating new dal proxy. ServiceName = CRSBusinessService, Alias = DMSRT",
    )
    b = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg="New dal proxy created. ServiceName = CRSBusinessService, Alias = DMSRT",
    )
    assert (a.code, b.code) == ("WFM-062A", "WFM-062B")
    assert a.fields["service"] == b.fields["service"] == "CRSBusinessService"


def test_wfm_info_window_chain(catalog):
    gids = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg='Request IDs returned by CRS are: "288230376151711744, 288230376151711745".',
    )
    assert gids.code == "WFM-123" and gids.fields["gids"] == ["288230376151711744", "288230376151711745"]
    assert ("gid", "288230376151711745") in gids.keys
    cb = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg='CRS sent callback to the adapter for request with GID "288230376151711744" with result "Accepted" and errors: "0".',
    )
    assert cb.code == "WFM-126" and ("gid", "288230376151711744") in cb.keys
    for target, text in (("CRS", "Answer"), ("IMS", "Callback")):
        r = classify(
            catalog,
            "AWFM",
            sensitive=False,
            msg=f"[{target.title()}RequestManagerForProdRequests] {text} for request with GID 7 received.",
        )
        assert r.code == "WFM-125C"


# ------------------------------------------------------------------------------ Ont specifics


def test_ont_send_chain_shares_the_message_id(catalog):
    announce = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=(
            "[SendIncidents] - Incident resource with Mrid: 'INC 1001' and Cache operation 'Update' "
            f"will be processed as 'Changed' messages with MessageIds: '{GUID_1}'"
        ),
    )
    send = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=f'Sending "IncidentsEventMessageType" message: {GUID_1} with verb: Changed...',
    )
    done = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=f'Sending "IncidentsEventMessageType" message: {GUID_1} with verb: Changed finished successfully.',
    )
    reply = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=f"Request message with MessageId '{GUID_1}' successfully sent. Reply result 'OK' is returned in response message.",
    )
    assert [m.code for m in (announce, send, done, reply)] == ["ONT-002", "ONT-011", "ONT-012", "ONT-014"]
    for m in (announce, send, done, reply):
        assert ("msgid", GUID_1) in m.keys
    assert ("mrid", "INC 1001") in announce.keys
    assert (send.cls, done.cls, reply.cls) == ("req_resp", "req_resp", "end")


def test_ont_remove_variant_has_no_message_operation(catalog):
    match = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=(
            "[SendIncidents] - Incident resource with Mrid: 'INC 1' and Cache operation 'Remove' "
            f"will be processed as messages with MessageIds: '{GUID_2}'."
        ),
    )
    assert match.code == "ONT-003" and match.fields["cache_op"] == "Remove" and "msg_op" not in match.fields


def test_ont_failure_forms_by_stream(catalog):
    base = (
        'Error: Sending "IncidentsEventMessageType" message failed. "System.TimeoutException" has occurred.'
    )
    normal = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=base + " More details are possible in sensitive log on info level.",
    )
    assert normal.code == "ONT-020" and normal.refs_sensitive and normal.sev_hint == 2
    sens = classify(
        catalog,
        "AONT",
        sensitive=True,
        msg=base,
        cont="Stack trace:\n   at Example.Client.Send(Message m) in F:\\PD\\1234567\\1\\Example\\Client.cs:line 77\n",
    )
    assert sens.code == "ONT-S020" and ("exc_type", "System.TimeoutException") in sens.keys
    unsuccessful = classify(
        catalog,
        "AONT",
        sensitive=True,
        msg=f"Request message with MessageId '{GUID_1}' unsuccessfully sent. Reason: 'no endpoint'.",
    )
    assert unsuccessful.code == "ONT-022" and unsuccessful.fields["reason"] == "no endpoint"


def test_ont_smart_cache_queue_line(catalog):
    match = classify(
        catalog,
        "AONT",
        sensitive=False,
        msg=(
            "[CachedDataSynchronizer] - Updating 'IncidentsEventMessageType' message with GID: '288230376151711744', "
            f"Identifier: 'INC 7' and MessageId: '{GUID_1}' in smart cache succeeded. Status changed from NotSent to Sent"
        ),
    )
    assert match.code == "ONT-111A"
    assert match.fields["action"] == "Updating" and match.fields["message_id"] == GUID_1
    assert {("msgid", GUID_1), ("mrid", "INC 7"), ("gid", "288230376151711744")} <= set(match.keys)


# ------------------------------------------------------------------------------ payloads


def test_payload_defaults_per_family(catalog):
    wfm = catalog.for_family("AWFM", sensitive=True).payload_defaults
    ont = catalog.for_family("AONT", sensitive=True).payload_defaults
    assert wfm["request"] == {"direction": "in", "peer": "WFM"}
    assert wfm["response"]["direction"] == "out" and wfm["fault"]["direction"] == "out"
    assert ont["request"] == {"direction": "out", "peer": "SAP_PI"}
    assert ont["response"]["direction"] == "in" and ont["fault"]["direction"] == "in"


def test_payload_specs_of_the_family_templates(catalog):
    expected = {
        "WFM-S112": ("msg", "delta", "out", "CRS"),
        "WFM-120": ("first_cont_lt", "content", "out", "CRS"),
        "WFM-120W": ("first_cont_lt", "content", "out", "WMS"),
        "WFM-S121": ("first_cont_lt", "content", "out", "IMS"),
    }
    with_payload = {t.code for _f, t in all_own(catalog) if t.payload is not None}
    assert with_payload == set(expected)
    for code, (start, role, direction, peer) in expected.items():
        spec = catalog.template(code).payload
        assert (spec.fmt, spec.start, spec.role, spec.direction, spec.peer) == (
            "xml",
            start,
            role,
            direction,
            peer,
        )
        assert spec.strip_suffix is None


def test_payload_streams(catalog):
    assert catalog.template("WFM-120").stream == "normal"  # CRS and WMS content is in the NORMAL log
    assert catalog.template("WFM-S121").stream == "sensitive"
    assert catalog.template("WFM-S112").stream == "sensitive"
    assert catalog.template("WFM-121").refs_sensitive and catalog.template("WFM-112").refs_sensitive
    # an announce line is a normal-log template; it never applies to the sensitive stream
    assert classify(catalog, "AWFM", sensitive=True, msg="Content being sent to CRS:") is None


def test_end_to_end_wfm_announce_and_request_payload(catalog):
    normal = parse(
        "\ufeff2026-01-01 10:00:00.000 [PID=1111][TID=0x1a] DEBUGLOG - "
        f'Changed crews request received. Request with ID "{GUID_1}". Message content written in sensitive log.\r\n'
        f"2026-01-01 10:00:00.010 [PID=1111][TID=0x2b] DEBUGLOG - Message with MessageID: '{GUID_1}' will be logged in sensitive log.\r\n"
        f'2026-01-01 10:00:01.000 [PID=1111][TID=0x1a] DEBUGLOG - Changed crews request with ID "{GUID_1}" successfully processed.\r\n'
    )
    ts = catalog.for_family("AWFM", sensitive=False)
    codes = [ts.classify(r).code for r in normal]
    assert codes == ["WFM-010", "GEN-MSG-01", "WFM-012"]
    announce = ts.classify(normal[1])
    assert announce.refs_sensitive  # the payload is in the sensitive log, same TID, same ms
    body = (
        '<ChangedCrews xmlns="urn:example">\r\n  <Header>\r\n    <MessageID>' + GUID_1 + "</MessageID>\r\n"
        "  </Header>\r\n</ChangedCrews>.\r\n"
    )
    sens = parse(
        "2026-01-01 10:00:00.010 [PID=1111][TID=0x2b] INFO - Message content: "
        + body
        + "2026-01-01 10:00:00.020 [PID=1111][TID=0x2b] DEBUGLOG - "
        f'Changed crews request received. Request with ID "{GUID_1}" content:\r\n'
        "Identifier: CREW0001\r\nAvailability: Available\r\n"
    )
    tset = catalog.for_family("AWFM", sensitive=True)
    payload = tset.classify(sens[0])
    assert payload.code == "GEN-PLD-01" and payload.payload.role == "by_root"
    assert payload.payload.start == "match_end" and payload.payload.strip_suffix == "."
    assert sens[0].msg[payload.match_end :].startswith("<ChangedCrews")
    assert (sens[0].cont or "").rstrip().endswith("</ChangedCrews>.")
    summary = tset.classify(sens[1])
    assert summary.code == "WFM-S010" and ("msgid", GUID_1) in summary.keys
    assert summary.fields["kv"] == {"Identifier": "CREW0001", "Availability": "Available"}


def test_end_to_end_wfm_content_to_crs_in_the_normal_log(catalog):
    recs = parse(
        "2026-01-01 10:00:00.000 [PID=1111][TID=0x1a] INFO - Content being sent to CRS:\r\n"
        '<ArrayOfAssignmentStatusInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n'
        "  <AssignmentStatusInfo>\r\n    <CrewId>1</CrewId>\r\n  </AssignmentStatusInfo>\r\n"
        "</ArrayOfAssignmentStatusInfo>\r\n"
        "2026-01-01 10:00:00.100 [PID=1111][TID=0x1a] INFO - Content being sent to WMS:\r\n"
        '<ArrayOfDocumentCrewAssigment xmlns:xsd="http://www.w3.org/2001/XMLSchema" />\r\n'
    )
    ts = catalog.for_family("AWFM", sensitive=False)
    crs, wms = (ts.classify(r) for r in recs)
    assert (crs.code, wms.code) == ("WFM-120", "WFM-120W")
    for m, rec in ((crs, recs[0]), (wms, recs[1])):
        assert m.payload.start == "first_cont_lt"
        first = next(line for line in rec.cont.split("\n") if line.lstrip().startswith("<"))
        assert first.startswith("<Array")
    assert (crs.payload.peer, wms.payload.peer) == ("CRS", "WMS")


def test_end_to_end_wfm_delta_in_the_sensitive_log(catalog):
    recs = parse(
        '2026-01-01 10:00:00.000 [PID=1111][TID=0x1a] INFO - <delta id="42">\r\n'
        '  <operation type="insert">\r\n    <ResourceDescription />\r\n  </operation>\r\n</delta>\r\n'
    )
    match = catalog.for_family("AWFM", sensitive=True).classify(recs[0])
    assert match.code == "WFM-S112" and match.fields["delta_id"] == 42 and ("delta_id", "42") in match.keys
    assert match.payload.start == "msg" and match.payload.role == "delta"
    assert recs[0].msg.strip() == '<delta id="42">'


def test_end_to_end_ont_request_and_response_payloads(catalog):
    recs = parse(
        f"2026-01-01 10:00:00.000 [PID=2222][TID=0x9] DEBUGLOG - Message with MessageID: '{GUID_1}' will be logged in sensitive log.\r\n"
        "2026-01-01 10:00:00.001 [PID=2222][TID=0x9] DEBUGLOG - Message with MessageID: '' will be logged in sensitive log.\r\n"
    )
    ts = catalog.for_family("AONT", sensitive=False)
    first, second = (ts.classify(r) for r in recs)
    assert first.code == second.code == "GEN-MSG-01" and first.refs_sensitive
    assert ("msgid", GUID_1) in first.keys
    sens = parse(
        '2026-01-01 10:00:00.001 [PID=2222][TID=0x9] INFO - Message: <ChangedIncidents xmlns="urn:example">\r\n'
        f"  <Header>\r\n    <MessageID>{GUID_1}</MessageID>\r\n  </Header>\r\n</ChangedIncidents>.\r\n"
        '2026-01-01 10:00:00.002 [PID=2222][TID=0x9] INFO - Message: <ns0:IncidentsResponseMessage xmlns:ns0="urn:x">\r\n'
        "  <ns0:Reply>OK</ns0:Reply>\r\n</ns0:IncidentsResponseMessage>.\r\n"
    )
    tset = catalog.for_family("AONT", sensitive=True)
    request, response = (tset.classify(r) for r in sens)
    assert request.code == response.code == "GEN-PLD-01"
    assert request.payload.role == response.payload.role == "by_root"
    assert tset.payload_defaults["request"]["peer"] == "SAP_PI"
    assert tset.payload_defaults["response"]["direction"] == "in"


# ------------------------------------------------------------------------------ precedence


def test_family_templates_win_over_the_library(catalog):
    # the same text in a family of the other group falls to the library or to nothing
    msg = "Callback received:"
    cont = "OMS_CREW_CHANGE_REQ GID: 1, Errors count: 0. For Status value check sensitive log."
    assert classify(catalog, "AWFM", sensitive=False, msg=msg, cont=cont).code == "WFM-030"
    other = classify(catalog, "AONT", sensitive=False, msg=msg, cont=cont)
    assert other is None or other.code.startswith("GEN-")


def test_exact_text_beats_regex_and_library_templates_still_apply(catalog):
    assert classify(catalog, "AWFM", sensitive=False, msg="Generating Delta...").code == "WFM-110"
    assert (
        classify(catalog, "AWFM", sensitive=False, msg="Connecting to CRSBusinessService.").code
        == "GEN-CON-01"
    )
    stats = classify(catalog, "AONT", sensitive=False, msg="[Communication2] - Client-side statistics:")
    assert stats is not None and stats.code == "GEN-STA-01"
    assert (
        classify(
            catalog, "AWFM", sensitive=False, msg="Debug level changed from DebugLog (3) to Warning (4)."
        ).code
        == "GEN-DBG-01"
    )


def test_stream_scoped_templates_apply_to_one_stream_only(catalog):
    for code, stream in (
        ("WFM-S010", "sensitive"),
        ("WFM-S030", "sensitive"),
        ("ONT-S020", "sensitive"),
        ("ONT-025", "sensitive"),
        ("WFM-010", "normal"),
        ("ONT-020", "normal"),
    ):
        assert catalog.template(code).stream == stream
        tpl = catalog.template(code)
        ex = tpl.examples[0]
        wrong = classify(catalog, tpl.family, sensitive=(stream == "normal"), msg=ex.msg, cont=ex.cont)
        assert wrong is None or wrong.code != code


def test_info_variant_does_not_shadow_debug_variant(catalog):
    # WFM-010 (DEBUGLOG announce) and WFM-S010 (sensitive summary) share their beginning
    normal = classify(
        catalog,
        "AWFM",
        sensitive=False,
        msg=f'Changed vehicles request received. Request with ID "{GUID_1}". Message content written in sensitive log.',
    )
    sens = classify(
        catalog,
        "AWFM",
        sensitive=True,
        msg=f'Changed vehicles request received. Request with ID "{GUID_1}" content:',
    )
    assert (normal.code, sens.code) == ("WFM-010", "WFM-S010")
    assert (
        classify(
            catalog,
            "AWFM",
            sensitive=True,
            msg=f'Changed vehicles request received. Request with ID "{GUID_1}". Message content written in sensitive log.',
        )
        is None
    )
