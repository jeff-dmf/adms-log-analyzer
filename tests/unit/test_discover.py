import zipfile
from pathlib import Path

import pytest
from ers_helpers import APP, ENTERPRISE_MEMBERS, LOG, SENSITIVE_MEMBERS, make_folder, make_pair, make_zip

import dmslog.ingest.discover as discover_module
from dmslog.ingest.discover import (
    FamiliesConfigError,
    RuleSet,
    SourceInfo,
    SourceNotFoundError,
    UnsafeArchiveError,
    discover,
    load_rules,
    normalize_member_name,
)

ADAPTERS = {
    "WfmAdapter": "AWFM",
    "OntAdapter": "AONT",
    "AVLAdapter": "AAVL",
    "OsrAdapter": "AOSR",
    "SmnAdapter": "ASMN",
    "SiteSwitchAdapter": "ASSS",
    "CrmAdapter": "ACRM",
    "AmiAdapter": "AAMI",
    "SmrAdapter": "ASMR",
    "WDIAdapter": "AWDI",
    "ClaimCheckAdapter": "ACLC",
    "FileUploadAdapter": "AFUP",
    "FileMonitorAdapter": "AFMO",
    "CustomAdapterChangeIncidentDevice": "AEDP",
}


def by_member(sources):
    return {s.member: s for s in sources}


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(("process", "family"), sorted(ADAPTERS.items()))
def test_every_scope_adapter_has_its_family_and_default_scope(process, family):
    cls = load_rules().classify(f"{APP}/{process}_1234.log")
    assert (cls.family, cls.scope, cls.envelope, cls.process, cls.pid) == (
        family,
        "default",
        "adms",
        process,
        1234,
    )
    assert cls.account == "dnaApp_zone1" and cls.service == "DMS_INTEGRATION"
    assert not cls.sensitive and cls.rotation is None


def test_adapter_rotation_and_sensitive_flags():
    rules = load_rules()
    rotated = rules.classify(f"{APP}/AVLAdapter_31136.log.0")
    assert (rotated.family, rotated.rotation, rotated.sensitive) == ("AAVL", 0, False)
    sensitive = rules.classify(f"{APP}/AVLAdapter_31136.sensitive.log")
    assert (sensitive.family, sensitive.sensitive, sensitive.rotation) == ("AAVL", True, None)
    both = rules.classify(f"{APP}/AVLAdapter_31136.sensitive.log.2")
    assert (both.sensitive, both.rotation) == (True, 2)


def test_classification_is_case_insensitive_and_accepts_backslash_free_bare_names():
    rules = load_rules()
    assert rules.classify("WFMADAPTER_7.LOG").family == "AWFM"
    bare = rules.classify("WfmAdapter_7.log")
    assert bare.family == "AWFM" and bare.account is None and bare.service is None


@pytest.mark.parametrize(
    ("path", "family", "scope"),
    [
        (f"{LOG}/oasErrLog.log", "OASERR", "default"),
        (f"{LOG}/oasErrLog.log.4", "OASERR", "default"),
        (f"{LOG}/BLTHost.DMS_INTEGRATION.log", "BLTHOST", "default"),
        (f"{LOG}/BLTHost.DMS_INTEGRATION.sensitive.log", "BLTHOST", "default"),
        (f"{LOG}/BLTHost.Common.log.3", "BLTHOST", "default"),
        (f"{LOG}/dnaService.DMS_INTEGRATION.log", "SERVICE", "service"),
        (f"{LOG}/ProcessMonitor.RCS.log", "SERVICE", "service"),
        (f"{LOG}/dnaHealthMonitor.DMS_INTEGRATION.log", "SERVICE", "service"),
        (f"{LOG}/arbRx.COMMON.log", "SERVICE", "service"),
        (f"{LOG}/arbMonitor.exe.RCS.log", "SERVICE", "service"),
        (f"{LOG}/dnaServiceStartup.log", "SERVICE", "optional"),
        (f"{LOG}/arbinfo.log", "SERVICE", "optional"),
        (f"{LOG}/NMC.log", "NMC", "optional"),
        (f"{LOG}/NMC.log.6", "NMC", "optional"),
        (f"{LOG}/PSengine.log", "PSENGINE", "optional"),
        (f"{LOG}/arbIP_10068.log", "ARBIP", "optional"),
        (f"{LOG}/ServiceShutdown_41.log", "SHUTDOWN", "optional"),
        (f"{LOG}/RCS_LastShutdownReason.log.1", "SHUTDOWN", "optional"),
        (f"{LOG}/ERS.log.2", "ERS", "optional"),
        (f"{LOG}/ConfigTool_9.log", "PROCESS", "optional"),
        (f"{LOG}/cscscript_9.log", "PROCESS", "optional"),
        (f"{APP}/DMS_ReplicationService_16496.log", "PROCESS", "optional"),
        (f"{APP}/NDSProxy_39076.log", "PROCESS", "optional"),
        (f"{LOG}/AdminPager.log", "SERVERLOG", "optional"),
        ("Deployment/Logs/Install-CU-Enterprise-1_2_3_4.log", "INSTALL", "optional"),
        ("Deployment/Logs/ConfigTool_5.log", "INSTALL", "optional"),
        (
            "SchneiderElectricData/ADMS/scripts/InstallationLogs/TelventDMS.SnapShot.1.txt",
            "INSTALL",
            "optional",
        ),
        ("SchneiderElectricData/OASyS/Servers/config/Registry/AdapterWfm.xml", "NONLOG", "optional"),
        ("SystemEvents/System.evtx", "NONLOG", "optional"),
        ("SchneiderElectricData/ADMS/data/LocalPersistenceStore/x.db3-wal", "NONLOG", "optional"),
        ("users/u/appdata/local/temp/ERS-1/computer_info/Environment.txt", "REPORT", "optional"),
        ("SchneiderElectricData/ADMS/tools/odd.bin", "UNKNOWN", "optional"),
    ],
)
def test_generic_families_and_scopes(path, family, scope):
    cls = load_rules().classify(path)
    assert (cls.family, cls.scope) == (family, scope)


def test_service_logs_capture_the_service_name():
    cls = load_rules().classify(f"{LOG}/dnaService.DMS_INTEGRATION.log")
    assert cls.service == "DMS_INTEGRATION" and cls.process == "dnaService"


def test_non_log_files_are_not_indexable_and_unknown_is_the_fallback():
    rules = load_rules()
    assert rules.classify("a/b/c.xml").indexable is False
    fallback = rules.classify("totally/odd.bin")
    assert fallback.family == "UNKNOWN" and fallback.rule_index is None and fallback.indexable


def test_every_expected_family_exists():
    families = set(load_rules().families)
    assert set(ADAPTERS.values()) | {"BLTHOST", "OASERR", "SERVICE", "NMC", "PSENGINE", "UNKNOWN"} <= families


def test_blthost_is_not_captured_by_a_generic_rule():
    # BLTHost.<svc>.log would also match SERVERLOG; the specific rule must come first.
    assert load_rules().classify(f"{LOG}/BLTHost.X.log").family == "BLTHOST"


# ------------------------------------------------------------------- precedence


def _index_of(rules, family, sample):
    cls = rules.classify(sample)
    assert cls.family == family, (sample, cls.family)
    return cls.rule_index


def test_specific_rules_come_before_generic_ones_in_the_packaged_file():
    rules = load_rules()
    first_generic = min(
        i for i, r in enumerate(rules.rules) if r.family in {"PROCESS", "SERVERLOG", "NONLOG"}
    )
    for family in [*ADAPTERS.values(), "BLTHOST", "OASERR", "SERVICE", "NMC", "PSENGINE"]:
        indexes = [i for i, r in enumerate(rules.rules) if r.family == family]
        assert indexes and max(indexes) < first_generic, family
    # INSTALL must precede PROCESS so Deployment/Logs/ConfigTool_N.log is an installer log.
    install = [i for i, r in enumerate(rules.rules) if r.family == "INSTALL"]
    process = [i for i, r in enumerate(rules.rules) if r.family == "PROCESS"]
    assert max(install) < min(process)


def test_first_matching_rule_wins_over_a_later_overlapping_one():
    text = """
[layouts]
x = '(?:^|/)(?P<process>{process})\\.log$'
[[rule]]
family = "FIRST"
layout = "x"
process = "app"
[[rule]]
family = "SECOND"
pattern = '\\.log$'
[fallback]
family = "UNKNOWN"
"""
    rules = RuleSet.from_text(text)
    assert rules.classify("dir/app.log").family == "FIRST"
    assert rules.classify("dir/other.log").family == "SECOND"
    assert rules.classify("dir/other.txt").family == "UNKNOWN"
    swapped = RuleSet.from_text(
        text.replace("FIRST", "TMP").replace("SECOND", "FIRST").replace("TMP", "SECOND")
    )
    assert swapped.classify("dir/app.log").family == "SECOND"  # file order, not family name, decides


@pytest.mark.parametrize(
    "bad",
    [
        "[[rule]]\nfamily='A'\npattern='('\n[fallback]\nfamily='U'\n",  # invalid regex
        "[[rule]]\nfamily='A'\n[fallback]\nfamily='U'\n",  # no layout or pattern
        "[[rule]]\nfamily='A'\npattern='x'\nenvelope='nope'\n[fallback]\nfamily='U'\n",
        "[[rule]]\nfamily='A'\npattern='x'\nscope='nope'\n[fallback]\nfamily='U'\n",
        "[[rule]]\nfamily='A'\nlayout='missing'\n[fallback]\nfamily='U'\n",
        "[[rule]]\nfamily='A'\npattern='x'\nbogus=1\n[fallback]\nfamily='U'\n",
        "[[rule]]\nfamily='A'\npattern='x'\n",  # no fallback
        "[fallback]\nfamily='U'\n",  # no rules
        "not toml [",
    ],
)
def test_bad_families_config_is_rejected(bad):
    with pytest.raises(FamiliesConfigError):
        RuleSet.from_text(bad)


def test_packaged_families_file_loads_and_every_rule_has_a_valid_envelope():
    rules = load_rules()
    assert rules.rules and all(r.envelope in {"adms", "oaserr", "plain"} for r in rules.rules)


# ------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "name",
    [
        "../evil.log",
        "a/../../evil.log",
        "/etc/passwd",
        "C:/x.log",
        "c:\\x.log",
        "a\\..\\..\\x.log",
        "a\x00b.log",
        "",
        "./",
    ],
)
def test_unsafe_member_names_are_rejected(name):
    with pytest.raises(UnsafeArchiveError):
        normalize_member_name(name)


def test_member_names_are_normalized_to_posix():
    assert normalize_member_name("a\\b//c/./d.log") == "a/b/c/d.log"


# ------------------------------------------------------------------- zip


def test_zip_listing_never_reads_member_content(tmp_path, monkeypatch):
    enterprise, _ = make_pair(tmp_path)

    def boom(*_args, **_kwargs):
        raise AssertionError("content was read while listing")

    monkeypatch.setattr(zipfile.ZipFile, "open", boom)
    monkeypatch.setattr(zipfile.ZipFile, "read", boom)
    monkeypatch.setattr(zipfile.ZipFile, "extract", boom)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    sources = list(discover(enterprise))
    assert len(sources) == len(ENTERPRISE_MEMBERS)


def test_zip_sources_carry_size_crc_and_logical_path(tmp_path):
    enterprise, _ = make_pair(tmp_path)
    sources = by_member(discover(enterprise))
    wfm = sources[f"{APP}/WfmAdapter_100.log"]
    assert isinstance(wfm, SourceInfo)
    assert wfm.container == enterprise and wfm.container_kind == "zip"
    assert wfm.file_name == "WfmAdapter_100.log" and wfm.size == len(b"line\r\n")
    with zipfile.ZipFile(enterprise) as z:
        assert wfm.zip_crc == z.getinfo(wfm.member).CRC
    assert wfm.compressed_size is not None and wfm.mtime is not None
    assert (
        wfm.zone == "zone1"
        and wfm.account == "dnaApp_zone1"
        and wfm.stream_key == ("AWFM", "WfmAdapter", 100, False)
    )


def test_rotation_shares_the_stream_of_the_current_log(tmp_path):
    enterprise, _ = make_pair(tmp_path)
    sources = by_member(discover(enterprise))
    current = sources[f"{APP}/WfmAdapter_100.log"]
    rotated = sources[f"{APP}/WfmAdapter_100.log.0"]
    assert (current.rotation, rotated.rotation) == (None, 0)
    assert current.stream_key == rotated.stream_key


def test_default_scope_of_a_synthetic_ers(tmp_path):
    enterprise, sensitive = make_pair(tmp_path)
    sources = by_member(discover(enterprise))
    in_scope = {m for m, s in sources.items() if s.default_scope}
    assert in_scope == {
        f"{APP}/WfmAdapter_100.log",
        f"{APP}/WfmAdapter_100.log.0",
        f"{APP}/OntAdapter_200.log",
        f"{APP}/CustomAdapterChangeIncidentDevice_300.log",
        f"{LOG}/oasErrLog.log",
        f"{LOG}/oasErrLog.log.0",
        f"{LOG}/BLTHost.DMS_INTEGRATION.log",
        f"{LOG}/dnaService.DMS_INTEGRATION.log",
        f"{LOG}/ProcessMonitor.DMS_INTEGRATION.log",
        f"{LOG}/arbRx.DMS_INTEGRATION.log",
    }
    assert not sources[f"{LOG}/dnaService.RCS.log"].default_scope  # service without adapters
    assert not sources[f"{LOG}/NMC.log"].default_scope
    assert not sources["SystemEvents/System.evtx"].default_scope  # not indexable
    assert all(s.default_scope for s in discover(sensitive))
    assert {s.sensitive for s in discover(sensitive)} == {True}


def test_service_logs_are_default_when_the_source_has_no_adapter(tmp_path):
    zpath = make_zip(tmp_path / "x.zip", [f"{LOG}/dnaService.RCS.log", f"{LOG}/NMC.log"])
    sources = by_member(discover(zpath))
    assert sources[f"{LOG}/dnaService.RCS.log"].default_scope
    assert not sources[f"{LOG}/NMC.log"].default_scope


@pytest.mark.parametrize("member", ["../x.log", "a/../../x.log", "/abs/x.log", "C:/x.log", "a\\..\\x.log"])
def test_zip_slip_member_aborts_the_listing(tmp_path, member):
    zpath = make_zip(tmp_path / "bad.zip", ["ok/WfmAdapter_1.log", member])
    with pytest.raises(UnsafeArchiveError):
        list(discover(zpath))


def test_duplicate_member_names_are_rejected(tmp_path):
    zpath = tmp_path / "dup.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.writestr("a/WfmAdapter_1.log", b"1")
        archive.writestr("A/wfmadapter_1.LOG", b"2")
    with pytest.raises(UnsafeArchiveError):
        list(discover(zpath))


def test_member_count_and_total_size_limits_abort(tmp_path):
    zpath = make_zip(tmp_path / "many.zip", [f"d/f{i}.log" for i in range(5)])
    with pytest.raises(UnsafeArchiveError):
        list(discover(zpath, max_members=4))
    with pytest.raises(UnsafeArchiveError):
        list(discover(zpath, max_total_bytes=10))


def test_member_size_and_ratio_limits_mark_the_member_not_the_listing(tmp_path):
    zpath = tmp_path / "bomb.zip"
    big = b"a" * (3 * 1024 * 1024)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{APP}/WfmAdapter_1.log", big)  # compresses far beyond 200x
        archive.writestr(f"{APP}/OntAdapter_2.log", b"small\n")
    sources = by_member(discover(zpath))
    bomb = sources[f"{APP}/WfmAdapter_1.log"]
    assert bomb.limit_violation == "ratio" and not bomb.selectable and not bomb.default_scope
    assert sources[f"{APP}/OntAdapter_2.log"].limit_violation is None
    too_large = by_member(discover(zpath, max_member_bytes=1000))
    assert too_large[f"{APP}/OntAdapter_2.log"].limit_violation is None
    assert too_large[f"{APP}/WfmAdapter_1.log"].limit_violation == "too_large"
    relaxed = by_member(discover(zpath, max_ratio=1e9))
    assert relaxed[f"{APP}/WfmAdapter_1.log"].limit_violation is None


def test_small_highly_compressible_members_are_not_flagged(tmp_path):
    zpath = make_zip(tmp_path / "s.zip", [f"{APP}/WfmAdapter_1.log"], payload=b"a" * 100_000)
    assert next(iter(discover(zpath))).limit_violation is None


def test_corrupt_zip_is_reported_as_a_typed_error(tmp_path):
    bad = tmp_path / "bad.zip"
    good = make_zip(tmp_path / "good.zip", ["a.log"])
    bad.write_bytes(good.read_bytes()[:20] + b"\x00" * 50)
    # Not a zip any more: it is treated as a single file, never raising a raw BadZipFile.
    assert [s.container_kind for s in discover(bad)] == ["file"]


def test_zip_directory_entries_are_skipped(tmp_path):
    zpath = tmp_path / "d.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.writestr("dir/", b"")
        archive.writestr("dir/WfmAdapter_1.log", b"x")
    assert [s.member for s in discover(zpath)] == ["dir/WfmAdapter_1.log"]


# ------------------------------------------------------------------- folder and file


def test_folder_discovery_uses_relative_posix_paths_and_stat_only(tmp_path):
    root = make_folder(
        tmp_path / "Log", ["dnaApp_z/SVC/WfmAdapter_1.log", "oasErrLog.log", "NMC.log", "dnaService.SVC.log"]
    )
    sources = by_member(discover(root))
    assert set(sources) == {"dnaApp_z/SVC/WfmAdapter_1.log", "oasErrLog.log", "NMC.log", "dnaService.SVC.log"}
    wfm = sources["dnaApp_z/SVC/WfmAdapter_1.log"]
    assert wfm.container == root and wfm.container_kind == "folder" and wfm.zip_crc is None
    assert wfm.size == 2 and wfm.mtime is not None
    assert {m for m, s in sources.items() if s.default_scope} == {
        "dnaApp_z/SVC/WfmAdapter_1.log",
        "oasErrLog.log",
        "dnaService.SVC.log",
    }


def test_folder_discovery_skips_symlinks(tmp_path):
    root = make_folder(tmp_path / "Log", ["oasErrLog.log"])
    try:
        (root / "link.log").symlink_to(root / "oasErrLog.log")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert [s.member for s in discover(root)] == ["oasErrLog.log"]


def test_single_file_is_always_in_default_scope(tmp_path):
    path = tmp_path / "NMC.log"
    path.write_bytes(b"abc")
    (source,) = discover(path)
    assert (source.family, source.container_kind, source.default_scope, source.size) == (
        "NMC",
        "file",
        True,
        3,
    )
    xml = tmp_path / "a.xml"
    xml.write_bytes(b"<a/>")
    (xml_source,) = discover(xml)
    assert not xml_source.default_scope  # not a text log, even when named explicitly


def test_missing_path_raises(tmp_path):
    with pytest.raises(SourceNotFoundError):
        list(discover(tmp_path / "nope.zip"))


def test_sensitive_members_constant_matches_expectation():
    assert len(SENSITIVE_MEMBERS) == 3 and discover_module.FALLBACK_FAMILY == "UNKNOWN"


def test_discover_returns_the_same_list_for_a_zip_and_its_extracted_folder(tmp_path):
    enterprise, _ = make_pair(tmp_path)
    folder = tmp_path / "extracted"
    with zipfile.ZipFile(enterprise) as z:
        z.extractall(folder)
    from_zip = {s.member: (s.family, s.default_scope, s.pid, s.rotation) for s in discover(enterprise)}
    from_folder = {s.member: (s.family, s.default_scope, s.pid, s.rotation) for s in discover(folder)}
    assert from_zip == from_folder


def test_packaged_resource_is_a_plain_data_file():
    path = Path(discover_module.__file__).parents[1] / "families.toml"
    assert path.is_file()


_POOL_BASE = "[[rule]]\nfamily='FH'\npattern='x'\n[fallback]\nfamily='U'\n"
_POOL_OK = "[[pool]]\nname='Pool_X'\nfamily='FH'\nprocess='Svc_(?P<instance>[1-9]\\d{0,3})'\n"


def test_pool_of_matches_whole_process_names_only():
    rules = RuleSet.from_text(_POOL_BASE + _POOL_OK)
    assert [p.name for p in rules.pools] == ["Pool_X"]
    assert rules.pool_of("FH", "Svc_1") == ("Pool_X", 1)
    assert rules.pool_of("FH", "Svc_28") == ("Pool_X", 28)
    assert rules.pool_of("FH", "Svc_0") is None
    assert rules.pool_of("FH", "Svc_7_extra") is None
    assert rules.pool_of("FH", "xSvc_7") is None
    assert rules.pool_of("U", "Svc_7") is None
    assert rules.pool_of(None, "Svc_7") is None
    assert rules.pool_of("FH", None) is None
    assert rules.pool_of("FH", "Svc_7") == ("Pool_X", 7)
    assert rules.pool_of("FH", "Svc_7") == ("Pool_X", 7)  # cached


def test_no_pool_table_means_no_pools():
    assert RuleSet.from_text(_POOL_BASE).pools == []
    assert RuleSet.from_text(_POOL_BASE).pool_of("FH", "Svc_1") is None


@pytest.mark.parametrize(
    "bad",
    [
        "[[pool]]\nname='1bad'\nfamily='FH'\nprocess='S(?P<instance>\\d)'\n",
        "[[pool]]\nname='P'\nfamily='NOPE'\nprocess='S(?P<instance>\\d)'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(\\d'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S\\d'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(?P<n>\\d)'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='(S)(?P<instance>\\d)'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='(?P<instance>(\\d+)+)'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(?P<instance>\\d)'\nbogus=1\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='(?P<instance>(?:\\d+))+'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(?P<instance>x)'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(?P<instance>\\d+)?'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='S(?P<instance>\\d+)+'\n",
        "[[pool]]\nname='P'\nfamily='FH'\nprocess='(?:A|B)(?P<instance>\\d+)'\n",
        _POOL_OK + _POOL_OK,
    ],
)
def test_bad_pool_is_rejected(bad):
    with pytest.raises(FamiliesConfigError):
        RuleSet.from_text(_POOL_BASE + bad)


def test_packaged_function_host_pool():
    rules = load_rules()
    assert rules.pool_of("DMSFH", "DMS_FunctionHost_1") == ("DMS_FunctionHost", 1)
    assert rules.pool_of("DMSFH", "DMS_FunctionHost_28") == ("DMS_FunctionHost", 28)
    assert rules.pool_of("DMSFH", "DMS_FunctionHost_0") is None
    assert rules.pool_of("DMSJOB", "DMS_FunctionHost_7") is None
    assert rules.pool_of("DMSFH", "OtherName") is None


def test_pool_of_never_raises():
    from hypothesis import given
    from hypothesis import strategies as st

    rules = RuleSet.from_text(
        _POOL_BASE + _POOL_OK + "[[pool]]\nname='Q'\nfamily='FH'\nprocess='Q(?P<instance>\\d+)'\n"
    )

    @given(st.text(max_size=50) | st.from_regex(r"(Svc_|Q)[0-9\u0660-\u0669]{0,40}!?", fullmatch=True))
    def check(name):
        found = rules.pool_of("FH", name)
        assert found is None or (isinstance(found[1], int) and found[1] >= 0)

    check()
    assert rules.pool_of("FH", "Q" + "9" * 5000) is None
