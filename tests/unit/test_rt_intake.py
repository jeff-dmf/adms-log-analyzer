"""Real-time (RT) server captures: the ERS name, the pair of servers of a cluster, the service folders and the
default scope of the DMS_* and OMS_* services (issue: RT intake). Everything is synthetic."""

from __future__ import annotations

import pytest
from ers_helpers import GUID, RT_MEMBERS, ers_name, make_zip, rt_name

from dmslog.ingest.ers import (
    ADD_NEW,
    ADD_PAIR,
    HeldCollection,
    classify_add,
    ers_group_for,
    held_identity,
    parse_ers_name,
    plan_collection,
)


def test_a_real_time_server_name_is_an_ers_name():
    ident = parse_ers_name(rt_name("Primary", desc="host1"))
    assert ident is not None
    assert (ident.guid, ident.timestamp, ident.variant) == (GUID, "20261008124051", "enterprise")
    assert ident.prefix == "site_RT_Primary" and ident.server == ("site", "RT", "Primary")
    assert ident.description == "host1" and ident.display_label == "RT Primary host1"
    sensitive = parse_ers_name(rt_name("Secondary", sensitive=True, desc="host2"))
    assert sensitive.variant == "sensitive" and sensitive.display_label == "RT Secondary host2"


def test_the_integration_name_is_unchanged():
    ident = parse_ers_name(ers_name(desc="case1"))
    assert ident.prefix == "CL" and ident.server is None
    assert ident.display_label == "case1" and ident.group_key == (GUID, "20261008124051")
    assert ers_group_for(ident) == f"{GUID}_20261008124051"


def test_two_servers_of_one_cluster_are_two_captures_even_in_the_same_second():
    primary = parse_ers_name(rt_name("Primary"))
    secondary = parse_ers_name(rt_name("Secondary"))  # same GUID and same second
    assert primary.group_key != secondary.group_key
    assert ers_group_for(primary) != ers_group_for(secondary)
    assert primary.group_key == parse_ers_name(rt_name("Primary", sensitive=True)).group_key  # the two halves


def held(cid, name, group):
    return HeldCollection(
        cid,
        "ers_zip",
        f"/data/{name}",
        held_identity("ers_zip", GUID, "20261008124051", None, "x", f"/data/{name}"),
        group,
    )


def test_a_secondary_does_not_join_the_primary_but_its_own_sensitive_half_does():
    primary = held(1, rt_name("Primary"), 1)
    new = parse_ers_name(rt_name("Secondary"))
    assert classify_add([primary], "ers_zip", f"/data/{new.file_name}", new).action == ADD_NEW
    other_half = parse_ers_name(rt_name("Primary", sensitive=True))
    decision = classify_add([primary], "ers_zip", f"/data/{other_half.file_name}", other_half)
    assert (decision.action, decision.group) == (ADD_PAIR, 1)
    secondary = held(2, rt_name("Secondary"), 2)
    half = parse_ers_name(rt_name("Secondary", sensitive=True))
    assert classify_add([primary, secondary], "ers_zip", f"/data/{half.file_name}", half).group == 2


def test_the_plan_labels_the_collection_with_its_function_and_role(tmp_path):
    plan = plan_collection(make_zip(tmp_path / rt_name("Secondary", desc="host2"), RT_MEMBERS))
    assert plan.label == "RT Secondary host2" and plan.identity.prefix == "site_RT_Secondary"
    integration = plan_collection(make_zip(tmp_path / ers_name(desc="case9"), RT_MEMBERS))
    assert integration.label == "case9"


def by_member(plan):
    return {s.member.rsplit("/", 1)[-1]: s for s in plan.sources}


def test_the_dms_and_oms_services_of_a_real_time_folder_are_in_the_default_scope(tmp_path):
    sources = by_member(plan_collection(make_zip(tmp_path / rt_name(), RT_MEMBERS)))
    for name, family in (
        ("DMS_JobLauncher_1100.log.0", "DMSJOB"),
        ("DMS_JobScheduler_1200.log", "DMSJOB"),
        ("DMS_CoreFunctionsService_1300.log.0", "DMSCORE"),
        ("OMS_IncidentManagementService_1400.log", "DMSOMS"),
    ):
        assert (sources[name].family, sources[name].default_scope) == (family, True), name
    launcher = sources["DMS_JobLauncher_1100.log.0"]
    assert (launcher.process, launcher.pid, launcher.service, launcher.rotation) == (
        "DMS_JobLauncher",
        1100,
        "DMS_REALTIME",
        0,
    )


def test_a_service_inside_a_context_subfolder_keeps_its_service_and_pid(tmp_path):
    sources = by_member(plan_collection(make_zip(tmp_path / rt_name(), RT_MEMBERS)))
    core = sources["DMS_CoreFunctionsService_1300.log.0"]
    assert (core.service, core.process, core.pid, core.account) == (
        "DMS_REALTIME",
        "DMS_CoreFunctionsService",
        1300,
        "dnaApp_zone1",
    )
    sensitive = sources["DMS_WorkManagementService_1500.sensitive.log"]
    assert sensitive.sensitive is True and sensitive.family == "DMSOMS"


def test_a_function_host_number_is_an_instance_not_a_process_id(tmp_path):
    sources = by_member(plan_collection(make_zip(tmp_path / rt_name(), RT_MEMBERS)))
    for name, instance in (("DMS_FunctionHost_7.log", "7"), ("DMS_FunctionHost_13.log.0", "13")):
        host = sources[name]
        assert (host.family, host.default_scope, host.pid) == ("DMSFH", True, None)
        assert host.process == f"DMS_FunctionHost_{instance}"


def test_the_integration_service_folder_keeps_its_processes_optional(tmp_path):
    sources = by_member(plan_collection(make_zip(tmp_path / ers_name(), RT_MEMBERS)))
    replication = sources["DMS_ReplicationService_1600.log"]
    assert (replication.family, replication.default_scope) == ("PROCESS", False)
    tool = sources[
        "ToolService_1700.log"
    ]  # a subfolder of an integration service: the service is still known
    assert (tool.family, tool.service, tool.pid) == ("PROCESS", "DMS_INTEGRATION", 1700)


@pytest.mark.parametrize(
    "name", ["not_ers.zip", "ERS_site_RT_Primary_nonsense_20261008124051_Enterprise_x.zip"]
)
def test_other_names_are_still_not_ers_names(name):
    assert parse_ers_name(name) is None


def test_a_windows_path_recorded_in_the_case_keeps_the_server_prefix_on_any_system():
    name = rt_name("Primary")
    win = "C:\\data\\ers\\" + name
    ident = held_identity("ers_zip", GUID, "20261008124051", "enterprise", "x", win)
    assert ident.prefix == "site_RT_Primary" and ident.file_name == name
    primary = HeldCollection(1, "ers_zip", win, ident, 1)
    half = parse_ers_name(rt_name("Primary", sensitive=True))
    assert classify_add([primary], "ers_zip", f"/data/{half.file_name}", half).action == ADD_PAIR
    copy = parse_ers_name(name)
    assert classify_add([primary], "ers_zip", f"/other/{name}", copy).action == "same"


def test_the_rt_services_are_split_by_what_they_do(tmp_path):
    from ers_helpers import RT_FAMILY_MEMBERS

    sources = by_member(plan_collection(make_zip(tmp_path / rt_name(), RT_FAMILY_MEMBERS)))
    families = {name: s.family for name, s in sources.items()}
    assert families["DMS_JobScheduler_10.log"] == "DMSJOB"
    assert families["DMS_JobLauncher_11.log"] == "DMSJOB"
    assert families["DMS_JobManagerService_12.log"] == "DMSJOB"
    assert families["DMS_CoreFunctionsService_13.log"] == "DMSCORE"
    assert families["DMS_ModelTopologyService_14.log"] == "DMSCORE"
    assert families["DMS_NetworkModelService_15.log"] == "DMSMODEL"
    assert families["DMS_ReplicationService_16.sensitive.log"] == "DMSMODEL"
    assert families["OMS_IncidentManagementService_17.log"] == "DMSOMS"
    assert families["DMS_WorkManagementService_18.log"] == "DMSOMS"
    assert families["DMS_SomethingNewService_19.log"] == "DMSRT"  # any other DMS_* service
    assert all(s.default_scope for s in sources.values())
