import zipfile
from pathlib import Path

import pytest
from ers_helpers import GUID, OTHER_GUID, ers_name, make_folder, make_pair, make_zip

from dmslog import aliases as aliases_mod
from dmslog.ingest.discover import discover
from dmslog.ingest.ers import (
    ADD_NEW,
    ADD_PAIR,
    ADD_SAME,
    CollectionConflictError,
    HeldCollection,
    classify_add,
    ers_group_for,
    parse_ers_name,
    plan_collection,
    refuse_duplicate,
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


def held(cid, name, group=None, kind="ers_zip", path=None):
    """A collection already in the case, named like an ERS zip (or a folder/file)."""
    ident = parse_ers_name(name) if kind == "ers_zip" else None
    return HeldCollection(cid, kind, path or f"/cases/in/{name}", ident, group or cid)


def add(holds, name, kind="ers_zip", path=None):
    ident = parse_ers_name(name) if kind == "ers_zip" else None
    return classify_add(holds, kind, path or f"/new/{name}", ident)


def test_empty_case_takes_anything_as_a_new_group():
    assert add([], ers_name()).action == ADD_NEW
    assert add([], "Log", kind="folder").action == ADD_NEW


def test_the_other_half_joins_the_group_in_any_order():
    for first, second in ((False, True), (True, False)):
        holds = [held(7, ers_name(sensitive=first))]
        decision = add(holds, ers_name(sensitive=second))
        assert (decision.action, decision.group, decision.collection_id) == (ADD_PAIR, 7, None)


def test_same_half_is_the_same_collection_by_path_or_identity():
    holds = [held(3, ers_name()), held(4, ers_name(sensitive=True), group=3)]
    same_path = classify_add(holds, "ers_zip", holds[1].path, holds[1].identity)
    assert (same_path.action, same_path.collection_id, same_path.group) == (ADD_SAME, 4, 3)
    copy = add(holds, ers_name())  # the same half under another path
    assert (copy.action, copy.collection_id) == (ADD_SAME, 3)


def test_other_servers_captures_folders_and_files_are_new_groups():
    holds = [held(1, ers_name()), held(2, ers_name(sensitive=True), group=1)]
    assert add(holds, ers_name(OTHER_GUID)).action == ADD_NEW  # another server
    assert add(holds, ers_name(ts="20261008145350")).action == ADD_NEW  # another capture, same GUID
    assert add(holds, ers_name(OTHER_GUID, sensitive=True)).action == ADD_NEW  # a lone sensitive half
    assert add(holds, "Log", kind="folder").action == ADD_NEW
    assert add(holds, "report.zip").action == ADD_NEW  # a zip that is not named like an ERS
    folder = [held(5, "Log", kind="folder", path="/srv/Log")]
    assert classify_add(folder, "folder", "/srv/Log", None).action == ADD_SAME
    assert classify_add(folder, "folder", "/srv/Other", None).action == ADD_NEW


def test_a_second_copy_of_a_held_half_is_a_typed_conflict(tmp_path):
    (tmp_path / "a").mkdir()
    original = make_zip(tmp_path / "a" / ers_name(), ["x"])
    holds = [HeldCollection(1, "ers_zip", str(original), parse_ers_name(original.name), 1)]
    copy = str(tmp_path / "b" / original.name)
    decision = classify_add(holds, "ers_zip", copy, parse_ers_name(original.name))
    with pytest.raises(CollectionConflictError) as caught:
        refuse_duplicate(holds, decision, copy)
    assert caught.value.reason == "duplicate_collection" and GUID in str(caught.value)
    assert isinstance(caught.value, ValueError)
    refuse_duplicate(holds, decision, str(original))  # the same file: a re-ingest
    original.unlink()
    refuse_duplicate(holds, decision, copy)  # the recorded file is gone: the zip was moved
    refuse_duplicate(holds, add(holds, ers_name(OTHER_GUID)), "/x")  # new groups are never refused


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


def test_two_different_zips_become_two_groups_through_the_plan_identities(tmp_path):
    first = plan_collection(make_zip(tmp_path / ers_name(), ["a/WfmAdapter_1.log"]))
    second = plan_collection(make_zip(tmp_path / ers_name(OTHER_GUID), ["a/WfmAdapter_1.log"]))
    holds = [HeldCollection(1, first.kind, str(first.path), first.identity, 1)]
    assert classify_add(holds, second.kind, str(second.path), second.identity).action == ADD_NEW


# ---------------------------------------------------------------- real zips (names only)


def _real_zips():
    def big_enough(path):
        try:
            return path.stat().st_size >= 1024  # skip Git LFS pointers
        except OSError:  # vanished or unreadable: skipped without printing the exception
            return False

    try:
        found = sorted(REAL_DIR.glob("ERS_CL_*.zip"))
    except OSError:
        return []
    return [p for p in found if big_enough(p)]


REAL = _real_zips()


def _check(ok: object, label: str) -> None:
    """Assert with a FIXED message: pytest must never echo real names, GUIDs or counts from samples."""
    assert ok, f"real-sample check failed: {label}"


REAL_ALIASES = aliases_mod.load(REAL_DIR)  # optional local-samples/aliases.toml; empty when absent


@pytest.mark.skipif(len(REAL) < 6, reason="real ERS zips not available (missing or LFS pointers)")
def test_real_ers_zips_every_member_gets_a_family_by_name_only():
    total = 0
    unknown = 0
    for path in REAL:
        with zipfile.ZipFile(path) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
        # names, sizes and CRC only; nothing is extracted
        sources = list(discover(path, aliases=REAL_ALIASES))
        _check([s.member for s in sources] == names, "members match the zip listing")
        _check(all(s.family for s in sources), "every member has a family")
        total += len(sources)
        unknown += sum(1 for s in sources if s.family == "UNKNOWN")
    _check(
        total == sum(members for members, _default, _indexable in REAL_COUNTS.values()),
        "total member count",
    )
    _check(unknown == 0, "no member with an UNKNOWN family")


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
        _check(ident is not None and expected is not None, "identity parsed")
        same_identity = (ident.guid, ident.timestamp, ident.variant) == (
            expected.guid,
            expected.timestamp,
            expected.variant,
        )
        _check(same_identity, "identity matches the zip name")
        counts = REAL_COUNTS.get((ident.timestamp, ident.variant))
        _check(counts is not None, "known capture in REAL_COUNTS")
        got = (len(plan.sources), len(plan.default_sources), len(plan.indexable_sources))
        _check(got == counts, "member/default/indexable counts")
        _check(all(s.limit_violation is None for s in plan.default_sources), "no limit violation")
        families = {}
        for s in plan.default_sources:
            families[s.family] = families.get(s.family, 0) + 1
        _check(set(families) <= {*ADAPTER_FAMILIES, "BLTHOST", "OASERR", "SERVICE"}, "family set")
        if ident.variant == "sensitive":
            _check(all(s.sensitive for s in plan.sources), "all sources sensitive")
            _check(
                families
                == dict.fromkeys(
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
                ),
                "sensitive family set",
            )
        else:
            # oasErrLog.log plus .log.0 to .log.4
            _check(families.get("OASERR") == 6, "OASERR count")
            # dnaService, ProcessMonitor, dnaHealthMonitor, arbRx, arbTx, arbMonitor.exe
            _check(families.get("SERVICE") == 6, "SERVICE count")
            _check(
                not any(
                    "RCS" in s.member or "COMMON" in s.member
                    for s in plan.default_sources
                    if s.family == "SERVICE"
                ),
                "no RCS/COMMON service in default scope",
            )
            _check(all(families.get(f, 0) >= 1 for f in ADAPTER_FAMILIES), "every adapter family present")
