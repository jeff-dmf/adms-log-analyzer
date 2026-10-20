"""Catalog of group 3 (Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, BLTHost, oasErrLog,
service logs): templates, examples, precedence over _common, synthetic flows (task 2.3)."""

# ruff: noqa: E501 - synthetic log lines are kept on one line

import tomllib
from importlib import resources

import pytest

from dmslog.parse.templates import Catalog, TemplateDef, load_texts

FILES = {
    "ACRM": "acrm.toml",
    "AAMI": "aami.toml",
    "ASMR": "asmr.toml",
    "AWDI": "awdi.toml",
    "ACLC": "aclc.toml",
    "AFUP": "afup.toml",
    "AFMO": "afmo.toml",
    "BLTHOST": "blthost.toml",
    "OASERR": "oaserr.toml",
    "SERVICE": "service.toml",
}

# Codes that the format document (docs/formato/crm-e-demais.md) assigns to each family.
EXPECTED = {
    "ACRM": {
        "CRM-001",
        "CRM-002",
        "CRM-003",
        "CRM-004",
        "CRM-010",
        "CRM-011",
        "CRM-012",
        "CRM-013",
        "CRM-014",
        "CRM-015",
        "CRM-S01",
    },
    "AAMI": {f"AMI-{n:03d}" for n in range(1, 9)} | {"AMI-S01", "AMI-S02"},
    "ASMR": {f"SMR-{n:03d}" for n in range(1, 8)},
    "AWDI": {"WDI-001"},
    "ACLC": {f"CLC-{n:03d}" for n in range(1, 12)} | {"CLC-S01", "CLC-S07"},
    "AFUP": {f"FUP-{n:03d}" for n in range(1, 10)} | {"FUP-S01", "FUP-S02"},
    "AFMO": {"FMO-001", "FMO-002", "FMO-003"},
    "BLTHOST": {
        "BLT-001",
        "BLT-002",
        "BLT-003",
        "BLT-004",
        "BLT-005",
        "BLT-010",
        "BLT-011",
        "BLT-012",
        "BLT-020",
        "BLT-021",
        "BLT-022",
        "BLT-023",
        "BLT-030",
        "BLT-031",
        "BLT-032",
    },
    "OASERR": {f"OAS-{n:03d}" for n in range(1, 23)},
    "SERVICE": {f"SVC-{n:03d}" for n in range(1, 26)},
}


def _read(name: str) -> bytes:
    return resources.files("dmslog.adapters").joinpath(name).read_bytes()


@pytest.fixture(scope="module")
def cat() -> Catalog:
    files = {name: _read(name) for name in (*FILES.values(), "_common.toml")}
    return load_texts(files, check_examples=True)


def _own(cat: Catalog, fam: str) -> tuple[TemplateDef, ...]:
    return cat.families[fam].templates


def _hits(t: TemplateDef, msg: str) -> bool:
    s = msg.strip()
    return t.text == s if t.text is not None else t.regex.match(s) is not None  # type: ignore[union-attr]


def _cls(cat: Catalog, fam: str, msg: str, cont: str | None = None, *, sensitive: bool = False):
    return cat.for_family(fam, sensitive=sensitive).classify_text(msg, cont)


def test_family_codes_exist_in_families_toml() -> None:
    rules = tomllib.loads(resources.files("dmslog").joinpath("families.toml").read_text(encoding="utf-8"))
    declared = {r["family"] for r in rules["rule"]}
    assert set(FILES) <= declared


def test_all_templates_load_with_expected_codes(cat: Catalog) -> None:
    for fam in FILES:
        assert {t.code for t in _own(cat, fam)} == EXPECTED[fam], fam


def test_no_generic_templates_and_common_is_extended(cat: Catalog) -> None:
    for fam in FILES:
        assert not [t.code for t in _own(cat, fam) if t.code.startswith("GEN-")]
        assert cat.families[fam].extends == ("_common",)


def test_every_template_has_positive_and_negative_examples(cat: Catalog) -> None:
    for fam in FILES:
        for t in _own(cat, fam):
            assert t.examples, t.code
            assert t.counter_examples, t.code


def test_check_examples_reports_nothing(cat: Catalog) -> None:
    assert cat.check_examples() == []


def test_each_example_is_classified_as_its_own_template(cat: Catalog) -> None:
    for fam in FILES:
        for t in _own(cat, fam):
            streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
            for sensitive in streams:
                for ex in t.examples:
                    got = _cls(cat, fam, ex.msg, ex.cont, sensitive=sensitive)
                    assert got is not None and got.code == t.code, (fam, t.code, ex.msg, got and got.code)


def test_each_example_matches_only_one_template_of_its_file(cat: Catalog) -> None:
    for fam in FILES:
        ts = _own(cat, fam)
        for t in ts:
            for ex in t.examples:
                owners = [o.code for o in ts if o.stream in ("any", t.stream) and _hits(o, ex.msg)]
                assert owners == [t.code], (t.code, ex.msg, owners)


def test_negative_examples_are_not_classified_as_their_template(cat: Catalog) -> None:
    for fam in FILES:
        for t in _own(cat, fam):
            streams = (False, True) if t.stream == "any" else (t.stream == "sensitive",)
            for sensitive in streams:
                for ex in t.counter_examples:
                    got = _cls(cat, fam, ex.msg, ex.cont, sensitive=sensitive)
                    assert got is None or got.code != t.code, (t.code, ex.msg)


def test_examples_are_synthetic() -> None:
    for name in FILES.values():
        text = _read(name).decode("utf-8")
        for needle in (
            "DSPMPDMZ",
            "dnaApp_",
            "mpdmz",
            "bpdmz",
            "mpcore",
            "bpcore",
            "OASyS\\\\",
            "Rascunho",
            "Submetido",
        ):
            assert needle not in text, (name, needle)


def test_family_templates_win_over_generic_ones(cat: Catalog) -> None:
    exc = "Non Fatal Exception Example.CredentialNotFoundException: Credential with with source application 'AdapterExample' and destination application 'SFTP' not found. Reason: x"
    assert _cls(cat, "AFMO", exc).code == "FMO-001"
    assert _cls(cat, "ACLC", exc).code == "GEN-EXC-01"
    assert cat.for_family("UNKNOWN", sensitive=False).classify_text(exc).code == "GEN-EXC-01"
    cache = "(REPLICATION_INFO) Cache[AdapterFUStorageSmartCache] Replicate elements finished"
    assert _cls(cat, "AFUP", cache).code == "FUP-009"
    assert _cls(cat, "AAMI", cache).code == "GEN-CCH-01"
    assert _cls(cat, "BLTHOST", "DistribuSysAPI: Initialized.").code == "BLT-003"
    assert _cls(cat, "ACRM", "DistribuSysAPI: Initialized.").code == "GEN-STU-10"
    # Same text, different family: the validation prefix of ClaimCheck has a code only there.
    assert _cls(cat, "ACLC", "\t\t[Validation Info]: HashMissing").code == "CLC-007"
    assert _cls(cat, "ACRM", "\t\t[Validation Info]: HashMissing") is None


def test_generic_lines_are_available_in_every_family(cat: Catalog) -> None:
    for fam in FILES:
        got = _cls(cat, fam, "[Communication2] - Client-side statistics:")
        assert got is not None and got.code == "GEN-STA-01", fam
        got = _cls(cat, fam, "Detected errors:", "ExampleCode\n")
        assert got is not None and got.code == "GEN-ERR-01", fam
        got = _cls(cat, fam, "Detected errors:", "details", sensitive=True)
        assert got is not None and got.code == "GEN-ERR-03", fam


# ----------------------------------------------------------------------------- synthetic flows


def test_crm_get_trouble_tickets_flow(cat: Catalog) -> None:
    lines = [
        (
            '[GetTroubleTickets started] - Processing message: Message Id: "" Context: "All" Search filter: "INC 1001"',
            "CRM-001",
        ),
        ("Detected errors:", "GEN-ERR-01"),
        ("A detailed description of these errors can be found in the adapters sensitive log.", "GEN-ERR-02"),
        (
            "[SystemEventSender] System event message: [AdapterCrm] [GetTroubleTickets] Errors occurred while processing received message",
            "GEN-EVT-01",
        ),
        (
            '[GetTroubleTickets] - Returning 0 trouble ticket(s) for context "All" and search filter: INC 1001',
            "CRM-002",
        ),
        ("[GetTroubleTickets finished] - Message with message ID  processed.", "CRM-003"),
    ]
    for msg, code in lines:
        assert (
            _cls(cat, "ACRM", msg, "NoCallsForIncident" if msg == "Detected errors:" else None).code == code
        ), msg
    got = _cls(cat, "ACRM", "[GetTroubleTickets finished] - Message with message ID  processed.")
    assert got.fields["msg_id"] == "" and got.keys == []
    got = _cls(cat, "ACRM", "Detected errors:", "NoCallsForIncident\n")
    assert got.keys == [("errcode", "NoCallsForIncident")]


def test_crm_created_trouble_tickets_flow_pairs_by_gid(cat: Catalog) -> None:
    start = _cls(
        cat,
        "ACRM",
        '[CreatedTroubleTickets started] - Processing message with Message ID "EXAMPLE_1". Writing message data to sensitive log.',
    )
    assert start.code == "CRM-010" and start.refs_sensitive and start.keys == [("msgid", "EXAMPLE_1")]
    wait = _cls(
        cat,
        "ACRM",
        "[OMSBusinessManagerCallbackHandler] IMS created OMS_REQUEST entities: 288230376151711744. Now adapter will wait (00:00:15) for IMS callback.",
    )
    callback = _cls(cat, "ACRM", "IMS callback received for OMS_REQUEST GID: 288230376151711744 - Approved.")
    ok = _cls(
        cat, "ACRM", "SUCCESS: OMS_REQUEST entities (288230376151711744) are successfully processed by IMS."
    )
    assert [x.code for x in (wait, callback, ok)] == ["CRM-011", "CRM-012", "CRM-013"]
    assert wait.keys == callback.keys == ok.keys == [("gid", "288230376151711744")]
    timeout = _cls(
        cat,
        "ACRM",
        "[OMSBusinessManagerCallbackHandler] WARNING - Call creation has been forwarded to IMS, but callback is not received within 00:00:15 interval",
    )
    assert timeout.code == "CRM-014" and timeout.cls == "error" and timeout.sev_hint == 3
    end = _cls(
        cat, "ACRM", '[CreatedTroubleTickets finished] - Message with message ID "EXAMPLE_1" processed.'
    )
    assert end.code == "CRM-015" and end.cls == "end"
    sens = _cls(
        cat,
        "ACRM",
        "[CreatedTroubleTickets started] - Processing message: Message ID: EXAMPLE_1",
        "Customer trouble ticket: T-0001",
        sensitive=True,
    )
    assert sens.code == "CRM-S01" and sens.fields["ticket"] == "T-0001"


def test_crm_failure_keeps_the_stack_header(cat: Catalog) -> None:
    got = _cls(
        cat,
        "ACRM",
        '[GetTroubleTickets failed] - Processing message with messageId: "EXAMPLE_1" failed. ',
        "Reason: Execution Timeout Expired.\nStack trace:    at Example.Dao.Query() in F:\\PD\\7654321\\1\\Example\\Dao.cs:line 12\n",
    )
    assert got.code == "CRM-004" and got.cls == "error" and got.sev_hint == 2


def test_ami_flow(cat: Catalog) -> None:
    codes = [
        _cls(cat, "AAMI", m).code
        for m in (
            "[GetMeterReadingsBusinessLogicExt] - Processing MeterReadingRequest from received publication.",
            "[ GetMeterReadingsRequest] Sending request message",
            'Sending "GetMeterReadingsRequest" message: 1234567890-0.',
            'Sending "GetMeterReadingsRequest" message: 1234567890-0 finished successfully.',
            "[GetMeterReadingsRequest] Sending request finished successfully.",
            'External system returned "OK" response for sent request. For more details please consult sensitive log.',
            "[GetMeterReadingsBusinessLogicExt] - Processing MeterReadingRequest finished.",
        )
    ]
    assert codes == ["AMI-001", "AMI-003", "AMI-003", "AMI-003", "AMI-003", "AMI-004", "AMI-002"]
    sent = _cls(cat, "AAMI", 'Sending "GetMeterReadingsRequest" message: 1234567890-0.')
    assert sent.keys == [("msgid", "1234567890-0")]
    answer = _cls(
        cat,
        "AAMI",
        "[CreatedMeterReadings] Processing a message with a message ID: 1234567890-0. Writing message data to sensitive log.",
    )
    done = _cls(cat, "AAMI", "[CreatedMeterReadings] Message with message ID; 1234567890-0 is processed.")
    assert (answer.code, done.code) == ("AMI-005", "AMI-006")
    assert answer.keys == done.keys == [("msgid", "1234567890-0")]
    warning = _cls(
        cat, "AAMI", "[ERROR] - Validation error is detected during validation process of error configuration"
    )
    assert warning.code == "AMI-008" and warning.cls == "startup"


def test_smr_flow_and_success_marker(cat: Catalog) -> None:
    flow = [
        ('[CreatedWorks] - Processing message with messageId: "EXAMPLE_1" started.', "SMR-001"),
        ("[CreatedWorks] - Getting Nms data for the following devices: CS_0001.", "SMR-002"),
        ("[CreatedWorks] - Getting Nms data for the following devices: CS_0001 succeeded.", "SMR-002"),
        ("[CreatedWorks] - Getting Wms data for the following works: 1001.", "SMR-003"),
        (
            "[CreatedWorks] - Work (name, customId): (Example work, WR 1001) successfully added to Adms.",
            "SMR-004",
        ),
        ('[CreatedWorks] - Processing message with messageId: "EXAMPLE_1" successfully finished.', "SMR-005"),
        (
            "[CreatedWorks] -  Unable to create work request. WOM service returned some errors. For more details analyze work management service log.",
            "SMR-006",
        ),
    ]
    for msg, code in flow:
        assert _cls(cat, "ASMR", msg).code == code, msg
    work = _cls(
        cat,
        "ASMR",
        "[CreatedWorks] - Work (name, customId): (Example work, WR 1001) successfully added to Adms.",
    )
    assert work.fields["custom_id"] == "WR 1001" and work.keys == [("custom_id", "WR 1001")]
    empty = _cls(cat, "ASMR", "[CreatedWorks] - Getting Nms data for the following devices:.")
    assert empty.code == "SMR-002"
    two = _cls(cat, "ASMR", "[CreatedWorks] - Getting Wms data for the following works: 1,2.")
    assert two.fields["works"] == ["1", "2"]


def test_claimcheck_severity_follows_the_validation_prefix(cat: Catalog) -> None:
    sev = {
        "Info": 4,
        "Warning": 3,
        "Error": 2,
        "Critical": 2,
    }
    for word, expected in sev.items():
        got = _cls(cat, "ACLC", f"\t\t[Validation {word}]: ExampleCode")
        assert got.code == "CLC-007" and got.sev_hint == expected, word
        assert got.keys == [("errcode", "ExampleCode")]
        detail = _cls(cat, "ACLC", f"\t\t[Validation {word}]: Example detail.", sensitive=True)
        assert detail.code == "CLC-S07" and detail.sev_hint == expected
    # The normal stream never takes the free-text detail, the sensitive stream never the bare code form.
    assert _cls(cat, "ACLC", "[Validation Info]: Example detail.") is None


def test_claimcheck_flow_and_exception_chain(cat: Catalog) -> None:
    flow = [
        ('"CreatedFileNotificationEvent" message processing started...', "CLC-001"),
        ("[ClaimCheckAdapter] - RequestProcessor - Executing process line: Multimedia...", "CLC-002"),
        ("Number of retries left: 3", "CLC-005"),
        (
            "File is successfully copied from remote file share to local file share. For more details check sensitive log.",
            "CLC-003",
        ),
        ("Obtaining Config from Multimedia Service for attachment validation.", "CLC-004"),
        ("Processing attachment on ADMS gid '288230376151711744' started.", "CLC-006"),
        ("Processing attachment on ADMS gid '288230376151711744' finished.", "CLC-006"),
        ('"CreatedFileNotificationEvent" message processing finished successfully.', "CLC-009"),
        ('"CreatedFileNotificationEvent" message processing failed. Reason:', "CLC-010"),
        ("Uploading attachment failed due to AnotherOperationRunning error.", "CLC-011"),
    ]
    for msg, code in flow:
        assert _cls(cat, "ACLC", msg).code == code, msg
    exc = _cls(
        cat,
        "ACLC",
        "Non Fatal Exception System.Net.Sockets.SocketException: An existing connection was forcibly closed by the remote host",
        "\tManaged Code Stack Trace:\n   at Renci.SshNet.Abstractions.SocketAbstraction.Read() in F:\\PD\\7654321\\1\\Example\\Socket.cs:line 9\n",
    )
    assert (
        exc.code == "GEN-EXC-01" and exc.fields["frame"] == "Renci.SshNet.Abstractions.SocketAbstraction.Read"
    )
    copied = _cls(
        cat,
        "ACLC",
        "File: /PROD/example/file_1.jpg - is copied to location: \\\\share.invalid\\example\\file_1.jpg.",
        sensitive=True,
    )
    assert copied.code == "CLC-S01"


def test_fileupload_flow_and_sensitive_pair(cat: Catalog) -> None:
    flow = [
        ("FileUploadService FileUpload message processing started.", "FUP-001"),
        (
            'FileUploadService Received file - Name: "SP 1_Example_20260101.csv" Type: "OutageReport".',
            "FUP-002",
        ),
        ("FileUploadService File content can be observed in sensitive log on verbose log level.", "FUP-003"),
        (
            "Writing file to computer on a local path started... For computer name and local path details check sensitive log.",
            "FUP-004",
        ),
        (
            "Writing file to computer on a local path succeeded... For computer name and local path details check sensitive log.",
            "FUP-004",
        ),
        (
            "Uploading file to remote location. File details and remote location details written in sensitive log.",
            "FUP-005",
        ),
        ("Uploading file to remote location succeeded. Details written in sensitive log.", "FUP-005"),
        ("FileUploadService FileUpload message processing: File is uploaded to SFTP.", "FUP-006"),
        ("File type: OutageReport: Sending Claim Check Message is DISABLED", "FUP-007"),
        (
            "[SystemEventSenderWithQueuing] System event message: [AdapterFileUpload] Uploading file to remote location succeeded. Details written in sensitive log.",
            "GEN-EVT-01",
        ),
        ("FileUploadService FileUpload message processing finished.", "FUP-008"),
    ]
    for msg, code in flow:
        assert _cls(cat, "AFUP", msg).code == code, msg
    assert _cls(cat, "AFUP", flow[3][0]).refs_sensitive
    writing = _cls(
        cat,
        "AFUP",
        "Writing file to computer HOST01 on local path: \\\\share.invalid\\Out\\SP 1_Example.csv succeeded.",
        sensitive=True,
    )
    uploading = _cls(
        cat,
        "AFUP",
        "Uploading file: SP 1_Example.csv to remote location /PROD/Out/ succeeded.",
        sensitive=True,
    )
    assert (writing.code, uploading.code) == ("FUP-S01", "FUP-S02")
    assert writing.fields["phase"] == "succeeded." and uploading.fields["file"] == "SP 1_Example.csv"


def test_filemonitor_credential_exception_repeats_hourly(cat: Catalog) -> None:
    got = _cls(
        cat,
        "AFMO",
        "Non Fatal Exception Example.CredentialNotFoundException: Credential with with source application 'AdapterExample' and destination application 'SFTP' not found. Reason: The ADAMEntry 'x' doesn't exist.",
        "\tManaged Code Stack Trace:\n   at Example.CredentialProvider.GetCredential() in F:\\PD\\7654321\\1\\Example\\Cred.cs:line 10\n\tInner Exception Example.ADAMEntryDoesntExistException: x\n",
    )
    assert got.code == "FMO-001" and got.sev_hint == 2
    assert got.fields["src_app"] == "AdapterExample" and got.fields["dst_app"] == "SFTP"
    assert got.fields["inner_type"] == "Example.ADAMEntryDoesntExistException"
    assert got.keys == [("exc_type", "Example.CredentialNotFoundException")]


def test_wdi_has_only_the_weather_specific_startup_line(cat: Catalog) -> None:
    assert (
        _cls(
            cat,
            "AWDI",
            "The adapter component (Name: ExampleComponent; InputType: ExampleInput) is successfully registered in Example adapter.",
        ).code
        == "GEN-STU-11"
    )
    assert (
        _cls(
            cat,
            "AWDI",
            "[ReceiveExampleData] Starting web service on : https://localhost:9999/Example",
            sensitive=True,
        ).code
        == "WDI-001"
    )
    assert (
        _cls(
            cat,
            "AWDI",
            "Endpoint ReceiveExampleData on https://localhost:9999/Example was not invoked more than 12:00:00",
            sensitive=True,
        ).code
        == "GEN-AUD-01"
    )


def test_blthost_startup_block_and_mode_switch(cat: Catalog) -> None:
    suffix = " {E:\\Builds\\1\\Example\\Main.cs:373}"
    for msg in (
        "Initializing..." + suffix,
        "Managed BLT: bobjExample.dll" + suffix,
        "=============================== " + suffix,
        "BLTHost started on address 'net.tcp://localhost:9000/BLTService/*' ServiceEndpoint 'TCP'." + suffix,
    ):
        got = _cls(cat, "BLTHOST", msg)
        assert got.code == "BLT-001" and got.cls == "startup", msg
        assert got.fields["line"] == 373
    assert (
        _cls(
            cat,
            "BLTHOST",
            "The codified settings for BLTHost are:",
            "\tInstanceContextMode = PerSession\n {E:\\Builds\\1\\Example\\Main.cs:373}",
        ).code
        == "BLT-001"
    )
    bad = _cls(
        cat,
        "BLTHOST",
        "Failed to create the Service (The specified service has been marked for deletion. [1072])  {E:\\Builds\\1\\Example\\Main.cs:50}",
    )
    assert bad.code == "BLT-005" and bad.sev_hint == 2
    started = _cls(
        cat,
        "BLTHOST",
        "[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution started with input parameters: Originator system: 'sysa', Originator service: 'svc', Skip unreachable nodes: 'False'. Local system: 'sysb', service: 'svc'",
    )
    assert started.code == "BLT-010" and started.cls == "start"
    ready = "[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution completed. Result: LocalAndDownstreamNodesReady status: '%s' "
    assert _cls(cat, "BLTHOST", ready % "True").sev_hint == 5
    assert _cls(cat, "BLTHOST", ready % "False").sev_hint == 3
    assert _cls(cat, "BLTHOST", ready % "False", sensitive=True).code == "BLT-011"
    activated = _cls(
        cat,
        "BLTHOST",
        "[RemoteModeActivation] ModeGroup/Mode: 'DMSGroup'/'MainMode' activated on SystemName: sysa, ServiceName: svc.",
    )
    assert activated.code == "BLT-021" and activated.fields["mode"] == "MainMode"
    topology = _cls(
        cat, "BLTHOST", "[RTDBConfigAccessor] doas_system record read: name=sysa, zone=PROD, site=MainSite"
    )
    assert topology.code == "BLT-002" and topology.fields["name"] == "sysa" and topology.sev_hint == 5


def test_oaserr_two_record_exception(cat: Catalog) -> None:
    head = _cls(
        cat,
        "OASERR",
        "[Communication2] - Service operation ExampleContractResults threw unexpected exception.",
    )
    assert head.code == "OAS-001" and head.keys == [("op", "ExampleContractResults")]
    exc = _cls(
        cat,
        "OASERR",
        "[Communication2] - Example.Platform.ServiceModel.ServiceOperationException:",
        "   at Example.Service.Call() in F:\\PD\\7654321\\1\\Example\\Service.cs:line 3\n",
    )
    assert (
        exc.code == "OAS-002"
        and exc.fields["exc_type"] == "Example.Platform.ServiceModel.ServiceOperationException"
    )
    assert exc.keys == [("exc_type", "Example.Platform.ServiceModel.ServiceOperationException")]


def test_service_logs_state_changes_and_process_deaths(cat: Catalog) -> None:
    suffix = " {E:\\Builds\\1\\Example\\Arb.cpp:100}"
    hot = _cls(cat, "SERVICE", "The EXAMPLE_SVC service on HOST01 changed from STANDBY to HOT." + suffix)
    assert hot.code == "SVC-001" and hot.sev_hint == 4 and hot.fields["to_state"] == "HOT"
    fail = _cls(cat, "SERVICE", "The EXAMPLE_SVC service on HOST01 changed from HOT to FAIL." + suffix)
    assert fail.sev_hint == 3
    assert (
        _cls(
            cat,
            "SERVICE",
            "The EXAMPLE_SVC service on HOST01 is not communicating with the partner service on HOST02.  Setting the state of the EXAMPLE_SVC service on HOST01 to FAIL."
            + suffix,
        ).code
        == "SVC-002"
    )
    dead = _cls(
        cat,
        "SERVICE",
        "Non-Critical process ExampleAdapter (1234) belonging to EXAMPLE_SVC service is dead." + suffix,
    )
    assert dead.code == "SVC-005" and dead.fields["proc_pid"] == 1234
    assert ("process", "ExampleAdapter") in dead.keys and ("pid", "1234") in dead.keys
    restart = _cls(
        cat,
        "SERVICE",
        "RestartProcess: Process ExampleAdapter (pid 1234) belonging to the EXAMPLE_SVC service is being restarted using command: 'ExampleAdapter.exe -serviceName EXAMPLE_SVC'"
        + suffix,
    )
    assert restart.code == "SVC-006"
    assert [
        _cls(cat, "SERVICE", m).code
        for m in (
            "Shutting down service EXAMPLE_SVC." + suffix,
            "Starting service EXAMPLE_SVC" + suffix,
            "Service EXAMPLE_SVC startup complete." + suffix,
        )
    ] == ["SVC-012", "SVC-010", "SVC-011"]
    warm = _cls(
        cat,
        "SERVICE",
        "warm_start  failed to execute for the EXAMPLE_SVC service on computer HOST01.  Process took more than the timeout of 600 seconds to complete."
        + suffix,
    )
    assert warm.code == "SVC-018" and warm.sev_hint == 2
