"""Generic templates (_common.toml): examples, aliases, UNKNOWN fallback, precedence (task 2.2)."""

# ruff: noqa: E501 - synthetic log lines are kept on one line

from importlib import resources

import pytest

from dmslog.parse.templates import Catalog, TemplateDef, load_catalog, load_texts

CLASSES = {"start", "end", "req_resp", "validation", "error", "statistics", "startup", "lifecycle", "other"}

# Old per-document IDs (docs/formato/*.md) and the generic template that now owns them.
ALIASES = {
    "COM-001": "GEN-STA-01",
    "COM-006": "GEN-STA-01",
    "COM-002": "GEN-STA-02",
    "COM-007": "GEN-STA-02",
    "WFM-090": "GEN-STA-02",
    "ONT-030": "GEN-STA-02",
    "COM-003": "GEN-STA-03",
    "COM-004": "GEN-STA-03",
    "COM-005": "GEN-STA-03",
    "WFM-020": "GEN-CON-01",
    "WFM-021": "GEN-CON-02",
    "WFM-022": "GEN-CON-03",
    "WFM-050": "GEN-ERR-01",
    "WFM-051": "GEN-ERR-02",
    "WFM-052": "GEN-EVT-01",
    "WFM-099": "GEN-DBG-01",
    "WFM-100": "GEN-MSG-01",
    "ONT-100": "GEN-MSG-01",
}

# Every GEN-* ID that appears in docs/formato/*.md must resolve.
DOCUMENTED_GEN = (
    "GEN-STA-01 GEN-STA-02 GEN-STA-03 GEN-CON-01 GEN-CON-02 GEN-CON-03 GEN-NET-01 GEN-ERR-01 GEN-ERR-02 "
    "GEN-EVT-01 GEN-EVT-02 GEN-EVT-03 GEN-EXC-01 GEN-AUD-01 GEN-AUD-02 GEN-SUB-01 GEN-SUB-02 GEN-SUB-03 "
    "GEN-SUB-04 GEN-MAP-01 GEN-MAP-02 GEN-C2T-01 GEN-C2T-02 GEN-C2T-03 GEN-C2M-01 GEN-C2O-01 GEN-C2O-02 "
    "GEN-C2O-03 GEN-C2L-01 GEN-C2L-02 GEN-MOD-01 GEN-MOD-02 GEN-PSA-01 GEN-PSA-02 GEN-PRX-01 GEN-PRX-02 "
    "GEN-PRX-S01 GEN-FLT-01 GEN-FLT-02 GEN-POOL-01 GEN-XSD-01 GEN-XSD-02 GEN-CCH-01 GEN-MSG-01 GEN-PLD-01"
).split()

# Tracing and infrastructure noise: out of the full-text index, severity 5.
NOISE_PREFIXES = ("GEN-C2", "GEN-MOD-", "GEN-PSA-", "GEN-STA-")


def _read(name: str) -> bytes:
    return resources.files("dmslog.adapters").joinpath(name).read_bytes()


@pytest.fixture(scope="module")
def common_only() -> Catalog:
    return load_texts({"_common.toml": _read("_common.toml")}, check_examples=True)


@pytest.fixture(scope="module")
def full() -> Catalog:
    return load_catalog(check_examples=True)


def _templates(cat: Catalog) -> list[TemplateDef]:
    return list(cat.families["_common"].templates)


def _hits(t: TemplateDef, msg: str) -> bool:
    s = msg.strip()
    return t.text == s if t.text is not None else t.regex.match(s) is not None  # type: ignore[union-attr]


def test_library_is_not_a_stream_family(common_only: Catalog) -> None:
    fam = common_only.families["_common"]
    assert fam.is_library
    assert fam.extends == ()


def test_all_generic_templates_load_with_valid_attributes(common_only: Catalog) -> None:
    ts = _templates(common_only)
    assert len(ts) >= 60
    assert len({t.code for t in ts}) == len(ts)
    for t in ts:
        assert t.code.startswith("GEN-"), t.code
        assert t.cls in CLASSES, t.code
        assert t.sev in (1, 2, 3, 4, 5) or t.sev_map, t.code
        assert t.title, t.code


def test_every_template_has_positive_and_negative_examples(common_only: Catalog) -> None:
    for t in _templates(common_only):
        assert t.examples, t.code
        assert t.counter_examples, t.code


def test_examples_are_synthetic() -> None:
    text = _read("_common.toml").decode("utf-8")
    for needle in ("DSPMPDMZ", "dnaApp_", "mpdmz", "edpbr", "AECS_"):
        assert needle not in text, needle


def test_check_examples_reports_nothing(common_only: Catalog) -> None:
    assert common_only.check_examples() == []


def test_each_example_matches_only_its_own_template(common_only: Catalog) -> None:
    ts = _templates(common_only)
    for t in ts:
        for ex in t.examples:
            owners = [o.code for o in ts if o.stream in ("any", t.stream) and _hits(o, ex.msg)]
            assert owners == [t.code], (t.code, ex.msg, owners)


def test_negative_examples_are_not_classified_as_their_template(common_only: Catalog) -> None:
    for t in _templates(common_only):
        streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
        for sensitive in streams:
            tset = common_only.for_family("UNKNOWN", sensitive=sensitive)
            for ex in t.counter_examples:
                tm = tset.classify_text(ex.msg, ex.cont)
                assert tm is None or tm.code != t.code, (t.code, ex.msg)


@pytest.mark.parametrize(("old", "new"), sorted(ALIASES.items()))
def test_old_ids_resolve_to_the_generic_template(full: Catalog, old: str, new: str) -> None:
    assert full.resolve(old) == new
    assert full.template(old).code == new


@pytest.mark.parametrize("code", DOCUMENTED_GEN)
def test_every_documented_generic_id_resolves(full: Catalog, code: str) -> None:
    assert full.resolve(code) is not None


def test_aliases_are_not_codes_of_other_templates(full: Catalog) -> None:
    codes = {t.code for fam in full.families.values() for t in fam.templates}
    for t in _templates(full):
        assert not set(t.aliases) & codes


def test_unknown_family_uses_the_generic_set(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    sens = common_only.for_family("UNKNOWN", sensitive=True)
    assert normal.classify_text("Connecting to ExampleBusinessService.").code == "GEN-CON-01"
    assert normal.classify_text("[Communication2] - Client-side statistics:").code == "GEN-STA-01"
    assert normal.classify_text("Detected errors:").code == "GEN-ERR-01"
    assert sens.classify_text("Detected errors:").code == "GEN-ERR-03"
    assert normal.classify_text("Something nobody described") is None
    # Stream filter: sensitive-only templates are absent from the normal stream and vice versa.
    assert normal.classify_text("Message content: <ChangedExamples>") is None
    assert sens.classify_text("Message content: <ChangedExamples>").code == "GEN-PLD-01"
    assert (
        sens.classify_text("Creating client proxy object for service URL: net.tcp://example.invalid:1/S").code
        == "GEN-PRX-S01"
    )
    assert (
        normal.classify_text("Creating client proxy object for service URL: net.tcp://example.invalid:1/S")
        is None
    )


def test_statistics_fields_and_continuation(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    tm = normal.classify_text(
        "[Communication2] - By call count: ",
        "[methodname, call count, request total size, response total size]\nExample.IQuery.Get,2,10,20\nExample.IQuery.Put,1,5,6\n\n",
    )
    assert tm is not None and tm.code == "GEN-STA-03"
    assert tm.fields["n_rows"] == 2
    tm = normal.classify_text(
        "[Communication2] - [TOTAL][Service] Call count: 3, Request bytes: 294 (avg. 98), Response bytes: 28553 (avg. 9517.667). Total operations: 3"
    )
    assert tm is not None and tm.code == "GEN-STA-02"
    assert tm.fields["side"] == "Service" and tm.fields["ops"] == 3 and tm.fields["resp_avg"] == 9517.667
    assert tm.sev_hint == 5


def test_error_codes_become_keys_in_the_normal_stream_only(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    tm = normal.classify_text("Detected errors:", "ExampleCodeOne\nExampleCodeTwo\n")
    assert tm is not None
    assert tm.fields["codes"] == ["ExampleCodeOne", "ExampleCodeTwo"]
    assert ("errcode", "ExampleCodeOne") in tm.keys and ("errcode", "ExampleCodeTwo") in tm.keys
    sens = common_only.for_family("UNKNOWN", sensitive=True)
    tm = sens.classify_text(
        "Detected errors:", "No trouble tickets found for provided search criteria: INC 1.\n"
    )
    assert tm is not None and tm.code == "GEN-ERR-03" and tm.keys == []


def test_system_event_severity_comes_from_the_text(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    prefix = "[SystemEventSender] System event message: [AdapterExample] "
    assert (
        normal.classify_text(
            prefix + "[CreateExample] Errors occurred while processing received message"
        ).sev_hint
        == 3
    )
    assert (
        normal.classify_text(prefix + "ERROR: Processing message from external system failed.").sev_hint == 2
    )
    assert normal.classify_text(prefix + "WARNING: something").sev_hint == 3
    assert normal.classify_text(prefix + "Uploading file to remote location succeeded.").sev_hint == 4


def test_exception_header_and_stack_fields(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    tm = normal.classify_text(
        "Non Fatal Exception System.IO.IOException: The process cannot access the file.",
        "\tManaged Code Stack Trace:\n   at Example.Reader.Read() in F:\\PD\\7654321\\1\\Example\\Reader.cs:line 12\n",
    )
    assert tm is not None and tm.code == "GEN-EXC-01" and tm.sev_hint == 2
    assert tm.fields["exc_type"] == "System.IO.IOException"
    assert tm.fields["frame"] == "Example.Reader.Read"
    assert ("exc_type", "System.IO.IOException") in tm.keys


def test_map_dump_counts_items_and_never_keeps_pairs(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    tm = normal.classify_text('New "EXAMPLE_MAP" map content:', "\t1 - A\n\t2 - B\n\t3 - C")
    assert tm is not None and tm.code == "GEN-MAP-01"
    assert tm.fields["map"] == "EXAMPLE_MAP" and tm.fields["items"] == 3
    assert set(tm.fields) == {"map", "state", "items"}


def test_payload_announcement_and_payload_spec(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    tm = normal.classify_text(
        "Message with MessageID: '00000000-0000-4000-8000-000000000001' will be logged in sensitive log."
    )
    assert tm is not None and tm.code == "GEN-MSG-01" and tm.refs_sensitive
    assert tm.keys == [("msgid", "00000000-0000-4000-8000-000000000001")]
    empty = normal.classify_text("Message with MessageID: '' will be logged in sensitive log.")
    assert empty is not None and empty.keys == []
    sens = common_only.for_family("UNKNOWN", sensitive=True)
    for prefix in ("Message content: ", "Message: "):
        pld = sens.classify_text(
            prefix + '<ChangedExamples xmlns="urn:example">', "  <A/>\n</ChangedExamples>."
        )
        assert pld is not None and pld.code == "GEN-PLD-01"
        assert pld.payload is not None and pld.payload.fmt == "xml" and pld.payload.strip_suffix == "."


def test_noise_templates_are_hidden_and_outside_the_full_text_index(common_only: Catalog) -> None:
    for t in _templates(common_only):
        if t.code.startswith(("GEN-C2", "GEN-MOD-", "GEN-PSA-")):
            assert t.sev == 5, t.code
            assert t.fts is False, t.code
        if t.code.startswith("GEN-STA-"):
            assert t.sev == 5, t.code


def test_startup_marker_and_configuration_fields(common_only: Catalog) -> None:
    normal = common_only.for_family("UNKNOWN", sensitive=False)
    hot = normal.classify_text("[SystemStatusPublisher] - Server state changed. New state: HOT")
    assert (
        hot is not None
        and hot.code == "GEN-STU-01"
        and hot.cls == "lifecycle"
        and hot.fields["state"] == "HOT"
    )
    cfg = normal.classify_text("[Configuration] [LogLevel]: [DebugLog]. Type: [ConfigurationRegistry]")
    assert cfg is not None and cfg.code == "GEN-CFG-01" and cfg.cls == "startup"
    assert cfg.fields["param"] == "LogLevel" and cfg.fields["value"] == "DebugLog"
    default = normal.classify_text("[Configuration] [Default] [ExampleParam]: [5].")
    assert default is not None and default.code == "GEN-CFG-03" and default.fields["value"] == "5"
    missing = normal.classify_text(
        "[Configuration][LoadingError] [ExampleParam]. Type: [ConfigurationRegistry]. Exception: x"
    )
    assert missing is not None and missing.code == "GEN-CFG-02" and missing.fields["param"] == "ExampleParam"


def test_configuration_alias_table_is_documented_for_m3() -> None:
    text = _read("_common.toml").decode("utf-8")
    assert "PathToWebServiceConfiguration" in text
    assert "ErrorConfigurationPath" in text


def test_family_templates_win_over_generic_ones(full: Catalog) -> None:
    text = "Non Fatal Exception Example.CredentialNotFoundException: Credential with with source application 'AdapterExample' and destination application 'SFTP' not found. Reason: x"
    assert full.for_family("AFMO", sensitive=False).classify_text(text).code == "FMO-001"
    assert full.for_family("UNKNOWN", sensitive=False).classify_text(text).code == "GEN-EXC-01"
    cache = "(REPLICATION_INFO) Cache[AdapterFUStorageSmartCache] Replicate elements finished"
    assert full.for_family("AFUP", sensitive=False).classify_text(cache).code == "FUP-009"
    assert full.for_family("UNKNOWN", sensitive=False).classify_text(cache).code == "GEN-CCH-01"
    hosted = "[ReceiveExampleData] Starting web service on : https://localhost:9999/Example"
    assert full.for_family("AWDI", sensitive=True).classify_text(hosted).code == "WDI-001"
    assert full.for_family("UNKNOWN", sensitive=True).classify_text(hosted).code == "GEN-STU-09"


def test_generic_examples_never_become_another_generic_template(full: Catalog) -> None:
    """In every family chain a generic example is either its own template or taken by a more
    specific template of the family; it never lands on a different GEN-* template. For the
    families of group 3 the family templates that may take a generic example are listed."""
    group3_overrides = {
        ("AFUP", "GEN-CCH-01"): "FUP-009",  # AdapterFUStorageSmartCache lines
        ("AWDI", "GEN-STU-09"): "WDI-001",  # "Starting web service on" / "is hosted on" lines
        ("BLTHOST", "GEN-STU-10"): "BLT-003",  # DistribuSysAPI lines of the first BLT
    }
    generic = _templates(full)
    for fam, fdef in full.families.items():
        if fdef.is_library or "_common" not in fdef.extends:
            continue
        for t in generic:
            streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
            for sensitive in streams:
                tset = full.for_family(fam, sensitive=sensitive)
                for ex in t.examples:
                    got = tset.classify_text(ex.msg, ex.cont)
                    assert got is not None, (fam, t.code, ex.msg)
                    if got.code == t.code:
                        continue
                    assert not got.code.startswith("GEN-"), (fam, t.code, got.code, ex.msg)
                    if (fam, t.code) in group3_overrides:
                        assert got.code == group3_overrides[(fam, t.code)]
