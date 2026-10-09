"""Catalog of group 1 (AVL, Osr, Smn, SiteSwitch): templates, examples, precedence, end to end."""

# ruff: noqa: E501 - synthetic log lines are kept on one line

from importlib import resources

import pytest

from dmslog.parse.envelope import get_envelope
from dmslog.parse.records import parse_bytes
from dmslog.parse.templates import Catalog, load_catalog, load_texts

FAMILIES = ("AAVL", "AOSR", "ASMN", "ASSS")
FILES = {"AAVL": "aavl.toml", "AOSR": "aosr.toml", "ASMN": "asmn.toml", "ASSS": "asss.toml"}

# Codes the documentation (docs/formato/avl-osr-smn-siteswitch.md, payloads.md) assigns to each family.
EXPECTED = {
    "AAVL": {f"AVL-{n:03d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)}
    | {"AVL-016", "AVL-S002", "AVL-S003"},
    "AOSR": {f"OSR-{n:03d}" for n in range(1, 33)} | {"OSR-S005", "OSR-S010"},
    "ASMN": {f"SMN-{n:03d}" for n in (*range(1, 20), 99, 110, 111, 112, 113)}
    | {"SMN-112A", "SMN-112B", "SMN-S016", "SMN-S019", "SMN-S020", "SMN-S021", "SMN-S110"},
    "ASSS": {"SSS-001", "SSS-002"},
}

# _common is a library owned by another task: an empty stand-in keeps these tests about group 1 only.
_STUB_COMMON = b'schema = 1\nfamily = "_common"\n'


def _read(name: str) -> bytes:
    return resources.files("dmslog.adapters").joinpath(name).read_bytes()


@pytest.fixture(scope="module")
def cat() -> Catalog:
    files = {FILES[f]: _read(FILES[f]) for f in FAMILIES}
    files["_common.toml"] = _STUB_COMMON
    return load_texts(files, check_examples=True)


def _own(cat: Catalog, fam: str):
    return [t for t in cat.families[fam].templates]


def test_all_templates_load_with_expected_codes(cat: Catalog) -> None:
    for fam in FAMILIES:
        assert {t.code for t in _own(cat, fam)} == EXPECTED[fam], fam


def test_no_generic_templates_defined_here(cat: Catalog) -> None:
    for fam in FAMILIES:
        assert not [t.code for t in _own(cat, fam) if t.code.startswith("GEN-")]
        assert cat.families[fam].extends == ("_common",)


def test_every_template_has_examples_and_counter_examples(cat: Catalog) -> None:
    for fam in FAMILIES:
        for t in _own(cat, fam):
            assert t.examples, t.code
            assert t.counter_examples, t.code


def test_examples_classify_as_their_own_template_only(cat: Catalog) -> None:
    for fam in FAMILIES:
        for t in _own(cat, fam):
            streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
            for sensitive in streams:
                tset = cat.for_family(fam, sensitive=sensitive)
                for ex in t.examples:
                    m = tset.classify_text(ex.msg, ex.cont)
                    assert m is not None and m.code == t.code, (t.code, ex.msg)
                    for k, v in (ex.fields or {}).items():
                        assert m.fields.get(k) == v, (t.code, k)
                    s = ex.msg.strip()
                    others = [
                        o.code
                        for o in _own(cat, fam)
                        if o is not t
                        and {t.stream, o.stream} != {"normal", "sensitive"}
                        and (o.text == s if o.text is not None else o.regex.match(s) is not None)
                    ]
                    assert not others, (t.code, ex.msg, others)


def test_counter_examples_never_match_their_template(cat: Catalog) -> None:
    for fam in FAMILIES:
        for t in _own(cat, fam):
            streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
            for sensitive in streams:
                tset = cat.for_family(fam, sensitive=sensitive)
                for ex in t.counter_examples:
                    m = tset.classify_text(ex.msg, ex.cont)
                    assert m is None or m.code != t.code, (t.code, ex.msg)


def test_hot_templates_are_not_indexed_for_search_where_the_docs_say(cat: Catalog) -> None:
    assert cat.template("SSS-001").fts is False
    assert cat.template("SSS-002").fts is False
    assert cat.template("OSR-004").fts is True


def test_shipped_catalog_loads_and_checks_examples() -> None:
    catalog = load_catalog(check_examples=True)
    for fam in FAMILIES:
        assert {t.code for t in catalog.families[fam].templates} == EXPECTED[fam]


# --------------------------------------------------------------------------- precedence


def _classify(cat: Catalog, fam: str, msg: str, cont: str | None = None, *, sensitive: bool = False):
    return cat.for_family(fam, sensitive=sensitive).classify_text(msg, cont)


def test_avl_known_validation_codes_win_over_the_generic_one(cat: Catalog) -> None:
    assert _classify(cat, "AAVL", "\t\t[Validation Info]: MessageRevisionMissing").code == "AVL-003"
    assert _classify(cat, "AAVL", "[Validation Info]: HeaderTimestampMissing").code == "AVL-004"
    assert _classify(cat, "AAVL", "[Validation Critical]: VehicleNotFound").code == "AVL-007"
    m = _classify(cat, "AAVL", "[Validation Warning]: SomeOtherCode")
    assert m.code == "AVL-016" and m.sev_hint == 3 and ("errcode", "SomeOtherCode") in m.keys
    assert _classify(cat, "AAVL", "[Validation Critical]: SomeOtherCode").sev_hint == 2


def test_avl_validation_in_the_sensitive_stream(cat: Catalog) -> None:
    m = _classify(cat, "AAVL", "\t\t[Validation Info]: Message revision is not provided.", sensitive=True)
    assert m.code == "AVL-S003"
    # The detail text of the sensitive log does not apply to the normal stream.
    assert _classify(cat, "AAVL", "[Validation Info]: Message revision is not provided.") is None


def test_osr_get_incidents_failed_is_distinct_from_other_failed_ends(cat: Catalog) -> None:
    gi = '[GetIncidents end] - Get incidents request message Message Id: "" Context: "archived" Search filter: "" failed. '
    assert _classify(cat, "AOSR", gi).code == "OSR-004"
    assert (
        _classify(cat, "AOSR", "[ChangeIncidents end] - Change incidents request message x failed.").code
        == "OSR-032"
    )
    assert (
        _classify(
            cat,
            "AOSR",
            '[GetIncidents end] - Get incidents request message Message Id: "" Context: "All" Search filter: "INC 1" processed.',
        ).code
        == "OSR-003"
    )


def test_osr_detected_errors_normal_vs_sensitive(cat: Catalog) -> None:
    n = _classify(cat, "AOSR", "Detected errors:", "CodeOne\nCodeTwo")
    assert n.code == "OSR-005" and n.fields["codes"] == ["CodeOne", "CodeTwo"]
    assert ("errcode", "CodeTwo") in n.keys
    s = _classify(cat, "AOSR", "Detected errors:", "Free text of the details.", sensitive=True)
    assert s.code == "OSR-S005" and "codes" not in s.fields


def test_smn_stream_specific_failures(cat: Catalog) -> None:
    normal = "[SendWorks] Sending Work Request finished unsuccessfully. Reason: Written in sensitive log. StackTrace: "
    assert _classify(cat, "ASMN", normal).code == "SMN-016"
    assert _classify(cat, "ASMN", normal).refs_sensitive is True
    real = "[SendWorks] Sending Work Request finished unsuccessfully. Reason: Example reason.. StackTrace: "
    assert _classify(cat, "ASMN", real, sensitive=True).code == "SMN-S016"
    assert _classify(cat, "ASMN", real) is None  # the real reason only exists in the sensitive log
    assert _classify(cat, "ASMN", normal, sensitive=True).code == "SMN-S016"


def test_smn_request_result_variants(cat: Catalog) -> None:
    g = "00000000-0000-4000-8000-0000000000aa"
    assert (
        _classify(
            cat,
            "ASMN",
            f"Request message with MessageId '{g}' successfully sent. Reply result 'OK' is returned in response message.",
        ).code
        == "SMN-010"
    )
    assert (
        _classify(
            cat, "ASMN", f"Request message with MessageId '{g}' unsuccessfully sent. Reason: 'Timeout'."
        ).code
        == "SMN-017"
    )
    assert (
        _classify(
            cat,
            "ASMN",
            f"Request message with MessageId '{g}' unsuccessfully sent. For more details check sensitive log.",
        ).code
        == "SMN-018"
    )


def test_smn_persister_variants_do_not_overlap(cat: Catalog) -> None:
    peek = "[UnsentMessagesPersister] - Peek message from internal queue..."
    assert _classify(cat, "ASMN", peek).code == "SMN-112A"
    save = "[UnsentMessagesPersister] - Saving message with GID: '12', Identifier: 'WR 5' to smart cache succeeded."
    assert _classify(cat, "ASMN", save).code == "SMN-112"
    mrid = "[CreatedEntityIdsPersister] - Deleting message with GID: '12', Mrid: 'ZZ' from smart cache."
    assert _classify(cat, "ASMN", mrid).code == "SMN-113"


# --------------------------------------------------------------------------- end to end


def _log(lines: list[tuple[str, str, str]]) -> bytes:
    """Build ADMS-envelope bytes from (tid, msg, continuation) triples."""
    out = []
    for i, (tid, msg, cont) in enumerate(lines):
        out.append(f"2026-01-01 10:00:{i:02d}.000 [PID=100][TID=0x{tid}] DEBUGLOG - {msg}\r\n")
        if cont:
            out.append(cont.replace("\n", "\r\n") + "\r\n")
    return "".join(out).encode("utf-8")


def _run(cat: Catalog, fam: str, data: bytes, *, sensitive: bool = False):
    tset = cat.for_family(fam, sensitive=sensitive)
    recs = parse_bytes(data, get_envelope("adms"))
    return recs, [tset.classify(r) for r in recs]


def test_avl_end_to_end(cat: Catalog) -> None:
    g = "00000000-0000-4000-8000-0000000000a1"
    data = _log(
        [
            ("1a", f'"ChangedVehiclesCoordinatesEvent": {g} message processing started...', ""),
            ("1a", "Position is updated for vehicle(s). For more details check sensitive log.", ""),
            ("1a", "\t\t[Validation Info]: MessageRevisionMissing", ""),
            ("1a", "\t\t[Validation Info]: HeaderTimestampMissing", ""),
            ("1a", "\t\t Details regarding validation errors can be found adapters sensistive log.", ""),
            ("1a", f'"ChangedVehiclesCoordinatesEvent": {g} message processing finished successfully.', ""),
            ("1b", "Checking quality of coordinates...", ""),
            ("1b", "Updating quality for 3 staled coordinate(s)...", ""),
            ("1b", "Updating quality for staled coordinate(s) successfully finished.", ""),
        ]
    )
    recs, ms = _run(cat, "AAVL", data)
    assert len(recs) == 9
    assert [m.code for m in ms] == [
        "AVL-001",
        "AVL-002",
        "AVL-003",
        "AVL-004",
        "AVL-005",
        "AVL-006",
        "AVL-009",
        "AVL-010",
        "AVL-011",
    ]
    assert ms[0].keys == [("msgid", g)] and ms[5].keys == [("msgid", g)]
    assert ms[7].fields["n"] == 3
    assert ms[1].refs_sensitive is True and ms[4].refs_sensitive is True and ms[0].refs_sensitive is False


def test_avl_sensitive_end_to_end(cat: Catalog) -> None:
    data = _log(
        [
            ("1a", "Position is updated for the following vehicle(s): ZZZ0A00.", ""),
            ("1a", "\t\t[Validation Info]: Message revision is not provided.", ""),
            ("1a", "\t\t[Validation Info]: TimeStamp is not provided.", ""),
        ]
    )
    _, ms = _run(cat, "AAVL", data, sensitive=True)
    assert [m.code for m in ms] == ["AVL-S002", "AVL-S003", "AVL-S003"]
    assert ms[0].keys == [("vehicle", "ZZZ0A00")]


def test_osr_end_to_end(cat: Catalog) -> None:
    data = _log(
        [
            (
                "2a",
                '[GetIncidents start] - Get incidents request message Message Id: "" Context: "active" Search filter: "INC 7" received from user . Processing message...',
                "",
            ),
            ("2a", "[GetIncidents] - Returning 0 incident(s) for provided search criteria.", ""),
            ("2a", "Detected errors:", "NoIncidentsFoundForProvidedValidSearchCriteria"),
            ("2a", "A detailed description of these errors can be found in the adapters sensitive log.", ""),
            (
                "2a",
                "[SystemEventSender] System event message: [AdapterOsr] [GetIncidents] Errors occurred while processing received message",
                "",
            ),
            (
                "2a",
                '[GetIncidents end] - Get incidents request message Message Id: "" Context: "active" Search filter: "INC 7" processed.',
                "",
            ),
            (
                "2b",
                "[ChangedIncidentHazards start] - Changed incident hazards message: Message ID: 00000000-0000-4000-8000-0000000000b1",
                "Example.Ns.PayloadType received from user WPA_X1. Processing message...",
            ),
            (
                "2b",
                '[Validation message] - Value "IsHazard" is not provided and will be set to default value "false".',
                "",
            ),
            (
                "2b",
                '[ChangedIncidentHazards end] -  Changed incident hazards message with ID "00000000-0000-4000-8000-0000000000b1" processed.',
                "",
            ),
            (
                "2c",
                '[GetIncidents end] - Get incidents request message Message Id: "" Context: "archived" Search filter: "" failed. ',
                "Reason: One or more errors occurred.\nStack trace: System.AggregateException: One or more errors occurred.\n   at Example.Reader.Read() in F:\\PD\\1234567\\1\\Example\\Reader.cs:line 83\n",
            ),
        ]
    )
    recs, ms = _run(cat, "AOSR", data)
    assert [m.code for m in ms] == [
        "OSR-001",
        "OSR-002",
        "OSR-005",
        "OSR-006",
        "OSR-007",
        "OSR-003",
        "OSR-018",
        "OSR-019",
        "OSR-021",
        "OSR-004",
    ]
    assert ms[2].fields["codes"] == ["NoIncidentsFoundForProvidedValidSearchCriteria"]
    assert (
        ms[6].fields["user"] == "WPA_X1" and ms[6].fields["msg_id"] == "00000000-0000-4000-8000-0000000000b1"
    )
    assert ms[8].keys == [("msgid", "00000000-0000-4000-8000-0000000000b1")]
    assert ms[9].sev_hint == 2 and ms[9].fields["exc_type"] == "System.AggregateException"
    assert ms[9].fields["build"] == "1234567"
    assert recs[9].cont is not None


def test_smn_end_to_end(cat: Catalog) -> None:
    g = "00000000-0000-4000-8000-0000000000c1"
    data = _log(
        [
            ("3a", "[BusinessLogicResolver] - Invoking Changed business logic for 2 entities.", ""),
            ("3a", "[SendSwitchingStepsMessageSerializer] New serialization in progress...", ""),
            ("3b", "[SendSwitchingSteps] Sending following Switching Step: SP 1000001 - ISS 2000001.", ""),
            ("3b", f'Sending "SwitchingStepsEventMessageType" message: {g} with verb: Changed...', ""),
            (
                "3b",
                'Error: Sending "SwitchingStepsEventMessageType" message failed. "EndpointNotFoundException" has occurred. Writing details in sensitive log.',
                "",
            ),
            ("3b", f'Sending "SwitchingStepsEventMessageType" message: {g} with verb: Changed...', ""),
            (
                "3b",
                f'Sending "SwitchingStepsEventMessageType" message: {g} with verb: Changed finished successfully.',
                "",
            ),
            ("3b", "[SendSwitchingSteps] Sending Switching Step finished successfully", ""),
            (
                "3b",
                f"Request message with MessageId '{g}' successfully sent. Reply result 'OK' is returned in response message.",
                "",
            ),
            (
                "3c",
                "[SendWorks] Sending Work Request finished unsuccessfully. Reason: Written in sensitive log. StackTrace: ",
                "Server stack trace: \n   at Example.Client.Send() in F:\\PD\\1234567\\1\\Example\\Client.cs:line 5\n",
            ),
        ]
    )
    _, ms = _run(cat, "ASMN", data)
    assert [m.code for m in ms] == [
        "SMN-001",
        "SMN-003",
        "SMN-004",
        "SMN-007",
        "SMN-019",
        "SMN-007",
        "SMN-008",
        "SMN-009",
        "SMN-010",
        "SMN-016",
    ]
    assert ms[2].keys == [("plan", "1000001"), ("step", "2000001")]
    assert ms[3].keys == ms[6].keys == ms[8].keys == [("msgid", g)]
    assert ms[4].sev_hint == 2 and ms[4].refs_sensitive
    assert ms[9].sev_hint == 2 and ms[9].fields["op"] == "SendWorks"


def test_smn_sensitive_end_to_end(cat: Catalog) -> None:
    data = _log(
        [
            (
                "3c",
                "[SendWorks] Sending Work Request finished unsuccessfully. Reason: Example reason.. StackTrace: ",
                "Server stack trace: \n   at Example.Client.Send() in F:\\PD\\1234567\\1\\Example\\Client.cs:line 5\n",
            ),
            (
                "3c",
                "[WmsSubscriber] - Data received from publication:",
                'DmsType: ExampleType\nOperation: Add\n<ResourceDescription xmlns="urn:example"/>',
            ),
            ("3d", "[ServiceUrl] : For SendWorks service url is https://example.invalid/pi", ""),
        ]
    )
    _, ms = _run(cat, "ASMN", data, sensitive=True)
    assert [m.code for m in ms] == ["SMN-S016", "SMN-S110", "SMN-S020"]
    assert ms[1].payload is not None and ms[1].payload.role == "publication"


def test_siteswitch_end_to_end(cat: Catalog) -> None:
    data = _log(
        [
            ("4a", " Adapter is on ACTIVE site. ", ""),
            ("4a", " Adapter responded with HTTP OK 200 response ", ""),
            ("4b", " Adapter is on ACTIVE site. ", ""),
            ("4b", " Adapter responded with HTTP OK 200 response ", ""),
        ]
    )
    recs, ms = _run(cat, "ASSS", data)
    assert len(recs) == 4
    assert [m.code for m in ms] == ["SSS-001", "SSS-002", "SSS-001", "SSS-002"]
    assert all(m.fts is False and m.cls == "req_resp" for m in ms)
