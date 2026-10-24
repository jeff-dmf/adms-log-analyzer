import dataclasses
import importlib.util
from pathlib import Path

import pytest

from dmslog.knowledge.installation import (
    ErrorConfiguration,
    ErrorEntry,
    InstallationConfig,
    ProductConfig,
)
from dmslog.knowledge.pack import EMPTY_PACK, RtReference, RtService, load_pack_from
from dmslog.knowledge.registry import ConfigParam
from dmslog.knowledge.resolve import Knowledge, _curated_rt_services, resolve_rt_service, severity_number

ROOT = Path(__file__).resolve().parents[2]
MINI_PACK = ROOT / "tests" / "fixtures" / "knowledge" / "mini_pack"
MINI_VAULT = ROOT / "tests" / "fixtures" / "mini_vault"

_spec = importlib.util.spec_from_file_location("kb_build", ROOT / "tools" / "kb_build.py")
kb_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_build)


@pytest.fixture
def pack(tmp_path):
    out = tmp_path / "pack"
    assert (
        kb_build.main(["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--no-rt"])
        == 0
    )
    return load_pack_from(out)


def installation(*entries, interface="CreatedExampleItems", product_name="ExampleAdapter"):
    conf = ErrorConfiguration(
        param_name=f"{interface}_ErrorConfigurationPath",
        path="ADMS/x/ErrorConfiguration.xml",
        interface=interface,
        severity_eventing_threshold=None,
        entries=tuple(entries),
    )
    param = ConfigParam(product_name, "", "LogLevel", "Error", f"{product_name}_2.xml", 2, "", True, "wins")
    product = ProductConfig(product_name, (param,), error_configurations=(conf,))
    return InstallationConfig((product,))


def test_severity_number():
    assert [severity_number(s) for s in ("FATAL", "Error", "WARNING", "Info", "weird", None)] == [
        1,
        2,
        3,
        4,
        None,
        None,
    ]


def test_adapter_for_process_and_vault_note(pack):
    k = Knowledge(pack)
    assert k.adapter_for_process("ExampleAdapter").acronym == "AAAA"
    assert k.adapter_for_process("exampleadapter").acronym == "AAAA"
    assert k.adapter_for_process("Nope") is None
    assert k.vault_note("AAAA").endswith("AAAA Index.md")
    assert k.vault_note("ZZZZ") is None


def test_error_from_vault(pack):
    info = Knowledge(pack).error("AAAA", "1.8")
    assert (info.source, info.out_of_vault, info.severity, info.adms_severity) == ("vault", False, 1, "FATAL")
    assert info.reason == "InvalidMessage"
    assert Knowledge(pack).error("AAAA", "9.9") is None
    assert Knowledge(pack).error("ABBB", "InvalidMessage").severity == 2


def test_ers_takes_precedence_over_vault(pack):
    inst = installation(ErrorEntry(None, "Warning", "1.8", None, "Overridden", "From the ERS."))
    info = Knowledge(pack, inst).error("AAAA", "1.8", "CreatedExampleItems")
    assert (info.source, info.severity, info.out_of_vault) == ("ers", 3, False)
    assert (info.reason, info.details) == ("Overridden", "From the ERS.")
    assert info.config_path == "ADMS/x/ErrorConfiguration.xml"


def test_ers_falls_back_to_vault_text(pack):
    inst = installation(ErrorEntry(None, "Error", "1.8", None, None, None))
    info = Knowledge(pack, inst).error("AAAA", "1.8")
    assert info.severity == 2
    assert info.reason == "InvalidMessage"


def test_code_only_in_ers_is_out_of_vault(pack):
    inst = installation(ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None))
    info = Knowledge(pack, inst).error("AAAA", "ExampleCodeOne")
    assert info is not None
    assert (info.out_of_vault, info.source, info.severity) == (True, "ers", 1)


def test_ers_operation_filter(pack):
    inst = installation(ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None))
    k = Knowledge(pack, inst)
    assert k.error("AAAA", "ExampleCodeOne", "OtherOperation") is None
    assert k.error("AAAA", "ExampleCodeOne", "CreatedExampleItems") is not None


def test_param_registry_and_vault(pack):
    k = Knowledge(pack, {3: installation()})
    value = k.param(3, "ExampleAdapter", "LogLevel")
    assert (value.value, value.source, value.layer) == ("Error", "registry", 2)
    assert k.param(0, "ExampleAdapter", "LogLevel") is None
    assert k.param(3, "ExampleAdapter", "Missing") is None
    vault = k.param(3, "AAAA", "LogLevel", scope="vault")
    assert (vault.source, vault.default, vault.log_relevant) == ("vault", "DebugLog", True)
    with pytest.raises(ValueError):
        k.param(3, "AAAA", "LogLevel", scope="x")


def test_hint_absent_file_and_present(pack, tmp_path):
    assert Knowledge(pack, hints_path=tmp_path / "missing.toml").hint("EX-001") is None
    path = tmp_path / "hints.toml"
    path.write_text(
        '[[hint]]\ncode = "EX-001"\ntext = "Short."\nparams = ["SendTimeout"]\nnote = "n.md"\n\n'
        '[[hint]]\ncode = "EX-002"\ntext = "Only text."\n',
        encoding="utf-8",
    )
    k = Knowledge(pack, hints_path=path)
    hint = k.hint("EX-001")
    assert (hint.text, hint.params, hint.note) == ("Short.", ("SendTimeout",), "n.md")
    assert k.hint("EX-002").params == ()
    assert k.hint("EX-003") is None


def test_default_hints_file_is_optional(pack):
    assert Knowledge(pack).hint("NO-SUCH-TEMPLATE") is None


def test_error_of_another_family_product_is_not_returned(pack):
    entry = ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None)
    k = Knowledge(pack, installation(entry))
    assert k.error("ABBB", "ExampleCodeOne") is None  # defined only in the product of family AAAA
    assert k.error("NOFAMILY", "ExampleCodeOne") is None
    assert k.error("AAAA", "ExampleCodeOne") is not None
    other = Knowledge(pack, installation(entry, product_name="AdapterOther"))
    assert other.error("AAAA", "ExampleCodeOne") is None  # product does not match the family


def test_unknown_family_gets_no_ers_products(pack):
    inst = installation(ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None))
    k = Knowledge(pack, inst)
    assert k.error("Adapter", "ExampleCodeOne") is None  # not a family: no loose substring match
    assert k.error("AAAA", "ExampleCodeOne") is not None


def test_error_of_adapter_wfm_is_not_returned_for_another_family(pack):
    entry = ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None)
    k = Knowledge(pack, installation(entry, product_name="AdapterWfm"))
    assert k.error("AAAA", "ExampleCodeOne") is None
    assert k.error("ABBB", "ExampleCodeOne") is None


def test_family_codes_translate_to_vault_acronyms(use_pack, extended_pack):
    knowledge = Knowledge()  # the translation is curated: it works without a pack
    assert knowledge.acronym_for_family("AEDP") == "ACID"
    assert knowledge.acronym_for_family("AWDI") == "AWMI"
    assert knowledge.acronym_for_family("AWFM") == "AWFM"  # no translation needed
    assert knowledge.vault_note("AEDP") is None
    adapter = {
        "acronym": "ACID", "title": "Example Synthetic Interface", "adapter": "Example Synthetic Adapter",
        "type": "soap", "type_label": "SOAP/WSDL", "operations": [], "config_note": None,
        "document_title": None, "document_version": None, "aliases": [],
        "index_note": "Example/Synthetic Index.md", "process_names": [],
    }  # fmt: skip
    use_pack(extended_pack(adapters=[adapter]))
    knowledge = Knowledge()
    assert knowledge.vault_note("AEDP") == "Example/Synthetic Index.md"
    assert knowledge.adapter_for_process("CustomAdapterChangeIncidentDevice").acronym == "ACID"


def test_without_pack_vault_lookups_are_empty_and_ers_still_answers():
    entry = ErrorEntry("ExampleCodeOne", "FATAL", None, None, None, None)
    knowledge = Knowledge(installations={3: installation(entry, product_name="AdapterWfm")})
    assert not knowledge.has_pack and knowledge.pack_status.state == "not_installed"
    assert knowledge.adapter_for_process("ExampleAdapter") is None
    assert knowledge.vault_note("AAAA") is None
    assert knowledge.error("AAAA", "1.8") is None
    assert knowledge.param(0, "AAAA", "LogLevel", scope="vault") is None
    value = knowledge.param(3, "AdapterWfm", "LogLevel")  # ERS value, no vault description
    assert (value.value, value.source, value.description) == ("Error", "registry", None)
    # the curated process map binds AdapterWfm to AWFM; the vault was not consulted: not "out of vault"
    info = knowledge.error("AWFM", "ExampleCodeOne")
    assert (info.source, info.severity, info.out_of_vault) == ("ers", 1, False)


def test_a_given_pack_counts_as_installed(pack):
    status = Knowledge(pack).pack_status
    assert (status.state, status.source) == ("installed", "given")


# -- RT services (R5e) ---------------------------------------------------------------------------


def _pack_with(*services):
    return dataclasses.replace(EMPTY_PACK, rt_services=tuple(services))


def _vault_row(process, family, **kw):
    base = {
        "title": "Example title",
        "summary": "Synthetic sentence.",
        "note": "01 Example/Example Note.md",
        "section": "1.2.3",
        "install_dir": "Example\\bin",
        "monitored": ("cpu",),
        "counters": ("Example Counter",),
        "usage_events": 2,
        "references": (RtReference("EXMP", "02 Example/EXMP 1.md", "1.1", 3),),
    }
    base.update(kw)
    return RtService(process, family, **base)


def test_rt_service_exact_name_without_pack():
    info = resolve_rt_service("DMS_JobScheduler")
    assert info is not None
    assert (info.process, info.family, info.group) == ("DMS_JobScheduler", "DMSJOB", "calculation")
    assert info.keys == ("job_id",)
    assert info.title is None and info.note is None and info.summary is None
    assert info.monitored == () and info.counters == () and info.references == ()
    assert info.usage_events == 0
    assert info.in_vault is False


def test_rt_service_none_pack_and_empty_pack_equal():
    assert resolve_rt_service("DMS_JobScheduler", EMPTY_PACK) == resolve_rt_service("DMS_JobScheduler")


def test_rt_service_joins_pack_record():
    pack = _pack_with(_vault_row("DMS_JobScheduler", "DMSJOB"))
    info = Knowledge(pack).rt_service("DMS_JobScheduler")
    assert info.in_vault is True
    assert info.title == "Example title"
    assert info.section == "1.2.3"
    assert info.counters == ("Example Counter",)
    assert info.references[0].doc == "EXMP"
    assert info.family == "DMSJOB"  # curated, not the pack


def test_rt_service_pack_record_without_note_is_not_in_vault():
    pack = _pack_with(_vault_row("DMS_JobScheduler", "DMSJOB", note=None, title=None, section=None))
    assert resolve_rt_service("DMS_JobScheduler", pack).in_vault is False


def test_rt_service_pool_instance_resolves_to_pool():
    pack = _pack_with(_vault_row("DMS_FunctionHost", "DMSFH"))
    info = resolve_rt_service("DMS_FunctionHost_7", pack, "DMSFH")
    assert info.process == "DMS_FunctionHost"
    assert info.in_vault is True
    assert resolve_rt_service("DMS_FunctionHost_7").process == "DMS_FunctionHost"
    assert resolve_rt_service("DMS_FunctionHost").process == "DMS_FunctionHost"


@pytest.mark.parametrize(
    "name", ["DMS_FunctionHost_0", "DMS_FunctionHost_", "DMS_FunctionHost_12345", "DMS_FunctionHost_x"]
)
def test_rt_service_pool_instance_shape_is_strict(name):
    assert resolve_rt_service(name) is None


def test_rt_service_only_pools_take_instances():
    assert resolve_rt_service("DMS_JobScheduler_2") is None


def test_rt_service_pool_instance_with_other_family_is_refused():
    assert resolve_rt_service("DMS_FunctionHost_7", None, "DMSJOB") is None


def test_rt_service_multi_service_families_resolve_by_process():
    sched = resolve_rt_service("DMS_JobScheduler")
    launcher = resolve_rt_service("DMS_JobLauncher")
    manager = resolve_rt_service("DMS_JobManagerService")
    assert {sched.family, launcher.family, manager.family} == {"DMSJOB"}
    assert len({sched.process, launcher.process, manager.process}) == 3
    assert launcher.keys == ("job_id", "fh_pid", "new_pid")
    assert manager.keys == ("job_id", "jms_job")


def test_rt_service_related_carries_the_key():
    launcher = resolve_rt_service("DMS_JobLauncher")
    pairs = {(r.process, r.via) for r in launcher.related}
    assert ("DMS_JobScheduler", "job_id") in pairs
    assert ("DMS_FunctionHost", "handoff") in pairs
    assert resolve_rt_service("DMS_JobScheduler").as_dict()["related"][0] == {
        "process": "DMS_JobLauncher",
        "via": "job_id",
    }


@pytest.mark.parametrize(
    "name", [None, "", "DMS_Unknown", "Example_Adapter", "ADMS_Process", "dms_jobscheduler"]
)
def test_rt_service_unknown_or_non_rt_is_none(name):
    assert resolve_rt_service(name) is None
    assert Knowledge(EMPTY_PACK).rt_service(name) is None


def test_rt_service_with_synthetic_mini_pack_keeps_curated_only_processes():
    # The mini pack only knows synthetic processes: real ones still resolve (curated) with empty vault fields.
    pack = load_pack_from(MINI_PACK)
    assert pack.rt_services
    info = resolve_rt_service("DMS_JobScheduler", pack)
    assert info is not None and info.in_vault is False
    # a pack-only process that the curated TOML does not know is not an RT service
    assert resolve_rt_service("DMS_ExampleScheduler", pack) is None


def test_rt_service_every_curated_service_resolves():
    curated = _curated_rt_services()
    assert len(curated) == 33
    for name, row in curated.items():
        info = resolve_rt_service(name)
        assert info is not None and info.process == name and info.family == row.family


def test_rt_service_results_are_immutable():
    info = resolve_rt_service("DMS_JobLauncher", _pack_with(_vault_row("DMS_JobLauncher", "DMSJOB")))
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.title = "x"
    assert isinstance(info.keys, tuple) and isinstance(info.related, tuple)
    assert isinstance(info.monitored, tuple) and isinstance(info.references, tuple)
    d = info.as_dict()
    d["keys"].append("bogus")  # a copy: the cached curated data is untouched
    d["related"].clear()
    again = resolve_rt_service("DMS_JobLauncher")
    assert "bogus" not in again.keys and again.related
