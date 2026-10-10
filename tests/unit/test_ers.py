import zipfile
from pathlib import Path

import pytest
from ers_helpers import GUID, OTHER_GUID, ers_name, make_folder, make_pair, make_zip

from dmslog import aliases as aliases_mod
from dmslog.ingest.discover import discover
from dmslog.ingest.ers import (
    CollectionConflictError,
    check_can_add,
    ers_group_for,
    parse_ers_name,
    plan_collection,
    same_capture,
    select_sources,
)

REAL_DIR = Path(__file__).resolve().parents[2] / "local-samples"


def test_parse_enterprise_name():
    ident = parse_ers_name(ers_name(desc="loganalyzer"))
    assert ident is not None
    assert ident.guid == GUID and ident.timestamp == "20261008124051"
    assert ident.variant == "enterprise" and ident.product == "Enterprise"
    assert ident.description == "loganalyzer" and ident.captured_at.hour == 12


def test_parse_sensitive_name_and_description_with_underscores():
    ident = parse_ers_name(f"/some/dir/{ers_name(sensitive=True, desc='my_case_2')}")
    assert ident.variant == "sensitive" and ident.description == "my_case_2"
    assert ident.file_name == ers_name(sensitive=True, desc="my_case_2")


def test_parse_normalizes_guid_case_and_ignores_extension_case():
    ident = parse_ers_name(ers_name(GUID.lower()).replace(".zip", ".ZIP"))
    assert ident.guid == GUID


@pytest.mark.parametrize(
    "name",
    [
        "report.zip",
        "ERS_CL_notaguid_20261008124051_Enterprise_x.zip",
        f"ERS_CL_{GUID}_20261308124051_Enterprise_x.zip",  # month 13
        f"ERS_CL_{GUID}_2026100812_Enterprise_x.zip",
        f"ERS_CL_{GUID}_20261008124051_Enterprise_x.tar",
    ],
)
def test_names_that_are_not_ers_return_none(name):
    assert parse_ers_name(name) is None


def test_two_halves_share_the_group_key():
    enterprise = parse_ers_name(ers_name())
    sensitive = parse_ers_name(ers_name(sensitive=True))
    assert enterprise != sensitive and same_capture(enterprise, sensitive)
    assert ers_group_for(enterprise) == ers_group_for(sensitive)
    later = parse_ers_name(ers_name(ts="20261008145350"))
    assert not same_capture(enterprise, later)


def test_one_ers_per_case_accepts_the_other_half_in_any_order():
    enterprise = parse_ers_name(ers_name())
    sensitive = parse_ers_name(ers_name(sensitive=True))
    check_can_add([], enterprise)
    check_can_add([enterprise], sensitive)
    check_can_add([sensitive], enterprise)
    check_can_add([enterprise, sensitive], enterprise)  # re-ingesting the same zip


def test_second_collection_with_another_guid_is_a_typed_conflict():
    enterprise = parse_ers_name(ers_name())
    other = parse_ers_name(ers_name(OTHER_GUID))
    with pytest.raises(CollectionConflictError) as caught:
        check_can_add([enterprise], other)
    assert caught.value.reason == "different_guid" and OTHER_GUID in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_new_capture_of_the_same_server_is_also_a_conflict():
    first = parse_ers_name(ers_name())
    second = parse_ers_name(ers_name(ts="20261008145350"))
    with pytest.raises(CollectionConflictError) as caught:
        check_can_add([first], second)
    assert caught.value.reason == "different_capture"


def test_folders_and_files_do_not_mix_with_another_collection():
    ers = parse_ers_name(ers_name())
    with pytest.raises(CollectionConflictError) as first:
        check_can_add([ers], None)
    assert first.value.reason == "not_an_ers"
    with pytest.raises(CollectionConflictError) as second:
        check_can_add([None], ers)
    assert second.value.reason == "case_has_folder"
    check_can_add([], None)


def test_plan_collection_for_a_zip_pair(tmp_path):
    enterprise, sensitive = make_pair(tmp_path)
    plan = plan_collection(enterprise)
    assert plan.kind == "ers_zip" and plan.path == enterprise
    assert plan.identity.variant == "enterprise" and plan.system == "zone1" and plan.label == "case1"
    assert plan.machine is None
    default_members = {s.member for s in plan.default_sources}
    assert any(m.endswith("WfmAdapter_100.log") for m in default_members)
    assert not any("NMC" in m for m in default_members)
    other = plan_collection(sensitive)
    assert other.identity.variant == "sensitive" and other.identity.group_key == plan.identity.group_key
    assert {s.family for s in other.default_sources} == {"AWFM", "AONT", "BLTHOST"}


def test_plan_collection_for_folder_and_file(tmp_path):
    root = make_folder(tmp_path / "Log", ["dnaApp_z9/SVC/WfmAdapter_1.log", "NMC.log"])
    folder_plan = plan_collection(root)
    assert folder_plan.kind == "folder" and folder_plan.identity is None and folder_plan.system == "z9"
    assert folder_plan.label == "Log"
    single = tmp_path / "NMC.log"
    single.write_bytes(b"x")
    file_plan = plan_collection(single)
    assert file_plan.kind == "file" and file_plan.system is None and len(file_plan.default_sources) == 1


def test_plan_collection_of_an_empty_zip_is_still_a_zip(tmp_path):
    path = tmp_path / ers_name()
    with zipfile.ZipFile(path, "w"):
        pass
    plan = plan_collection(path)
    assert plan.kind == "ers_zip" and plan.sources == ()


def test_select_sources_scope_include_and_exclude(tmp_path):
    enterprise, _ = make_pair(tmp_path)
    sources = list(discover(enterprise))
    default = {s.member for s in select_sources(sources)}
    everything = {s.member for s in select_sources(sources, "all")}
    assert default < everything
    assert all(s.indexable for s in select_sources(sources, "all"))
    nmc = next(s.member for s in sources if s.member.endswith("NMC.log"))
    assert nmc in {s.member for s in select_sources(sources, include=[nmc])}
    wfm = next(s.member for s in sources if s.member.endswith("WfmAdapter_100.log"))
    assert wfm not in {s.member for s in select_sources(sources, exclude=[wfm])}
    xml = next(s.member for s in sources if s.member.endswith(".xml"))
    assert xml not in {s.member for s in select_sources(sources, "all", include=[xml])}
    with pytest.raises(ValueError):
        select_sources(sources, "everything")


def test_two_different_zips_in_one_case_raise_through_the_plan_identities(tmp_path):
    first = plan_collection(make_zip(tmp_path / ers_name(), ["a/WfmAdapter_1.log"]))
    second = plan_collection(make_zip(tmp_path / ers_name(OTHER_GUID), ["a/WfmAdapter_1.log"]))
    with pytest.raises(CollectionConflictError):
        check_can_add([first.identity], second.identity)


# ---------------------------------------------------------------- real zips (names only)


def _real_zips():
    found = sorted(REAL_DIR.glob("ERS_CL_*.zip"))
    return [p for p in found if p.stat().st_size >= 1024]  # skip Git LFS pointers


REAL = _real_zips()
REAL_ALIASES = aliases_mod.load(REAL_DIR)  # optional local-samples/aliases.toml; empty when absent


@pytest.mark.skipif(len(REAL) < 6, reason="real ERS zips not available (missing or LFS pointers)")
def test_real_ers_zips_every_member_gets_a_family_by_name_only():
    total = 0
    unknown = []
    for path in REAL:
        with zipfile.ZipFile(path) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
        # names, sizes and CRC only; nothing is extracted
        sources = list(discover(path, aliases=REAL_ALIASES))
        assert [s.member for s in sources] == names
        assert all(s.family for s in sources)
        total += len(sources)
        unknown += [s.member for s in sources if s.family == "UNKNOWN"]
    assert total == sum(members for members, _default, _indexable in REAL_COUNTS.values())
    assert unknown == []


ADAPTER_FAMILIES = (
    "AWFM",
    "AONT",
    "AAVL",
    "AOSR",
    "ASMN",
    "ASSS",
    "ACRM",
    "AAMI",
    "ASMR",
    "AWDI",
    "ACLC",
    "AFUP",
    "AFMO",
    "AEDP",
)

# (members, default scope, indexable) per real zip, by capture timestamp and variant.
REAL_COUNTS = {
    ("20261008124051", "enterprise"): (3095, 45, 2364),
    ("20261008124051", "sensitive"): (12, 12, 12),
    ("20261008145350", "enterprise"): (3099, 48, 2368),
    ("20261008145350", "sensitive"): (12, 12, 12),
    ("20261009145038", "enterprise"): (3095, 44, 2364),
    ("20261009145038", "sensitive"): (13, 13, 13),
}


@pytest.mark.skipif(len(REAL) < 6, reason="real ERS zips not available (missing or LFS pointers)")
def test_real_ers_default_scope_counts():
    for path in REAL:
        plan = plan_collection(path, REAL_ALIASES)
        ident = plan.identity
        expected = parse_ers_name(path.name)  # GUID and timestamp come from the file name
        assert ident is not None and expected is not None
        assert (ident.guid, ident.timestamp, ident.variant) == (
            expected.guid,
            expected.timestamp,
            expected.variant,
        )
        members, default, indexable = REAL_COUNTS[(ident.timestamp, ident.variant)]
        assert (len(plan.sources), len(plan.default_sources), len(plan.indexable_sources)) == (
            members,
            default,
            indexable,
        )
        assert all(s.limit_violation is None for s in plan.default_sources)
        families = {}
        for s in plan.default_sources:
            families[s.family] = families.get(s.family, 0) + 1
        assert set(families) <= {*ADAPTER_FAMILIES, "BLTHOST", "OASERR", "SERVICE"}
        if ident.variant == "sensitive":
            assert all(s.sensitive for s in plan.sources)
            assert families == dict.fromkeys(
                (
                    "AWFM",
                    "AONT",
                    "AAVL",
                    "AOSR",
                    "ASMN",
                    "ACRM",
                    "AAMI",
                    "ASMR",
                    "AWDI",
                    "ACLC",
                    "AFUP",
                    "BLTHOST",
                    *(("AEDP",) if ident.timestamp == "20261009145038" else ()),
                ),
                1,
            )
        else:
            assert families["OASERR"] == 6  # oasErrLog.log plus .log.0 to .log.4
            assert (
                families["SERVICE"] == 6
            )  # dnaService, ProcessMonitor, dnaHealthMonitor, arbRx, arbTx, arbMonitor.exe
            assert not any(
                "RCS" in s.member or "COMMON" in s.member
                for s in plan.default_sources
                if s.family == "SERVICE"
            )
            assert all(families.get(f, 0) >= 1 for f in ADAPTER_FAMILIES)
