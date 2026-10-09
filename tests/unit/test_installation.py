from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from dmslog.knowledge.installation import normalize_install_path, read_installation
from dmslog.knowledge.registry import RegistryError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "registry"
INSTALL = FIXTURES / "install"


@pytest.mark.parametrize(
    "raw",
    [
        r"%OASINSTALLDATADIR%\ADMS\configuration\X\f.xml",
        r"%OASINSTALLDATADIR%ADMS\configuration\X\f.xml",
        r"%oasinstalldatadir%/ADMS/configuration/X/f.xml",
        r"C:\SchneiderElectricData\\ADMS\configuration\X\f.xml",
        r"C:\SchneiderElectricData\ADMS\\configuration\\X\f.xml",
        "/opt/SchneiderElectricData/ADMS/configuration/X/f.xml",
    ],
)
def test_normalize_install_path(raw: str) -> None:
    assert normalize_install_path(raw) == "ADMS/configuration/X/f.xml"


def test_normalize_unmappable_path() -> None:
    assert normalize_install_path(r"D:\Elsewhere\f.xml") is None
    assert normalize_install_path("%OASINSTALLDATADIR%") is None


def _assert_example(config) -> None:
    example = config.product("AdapterExample")
    assert example is not None
    assert example.effective("LogLevel").value == "Error"
    assert example.effective("WebServiceConfiguration").layer == 2
    # effective WebServiceConfiguration is the layer-2 (PROD) file
    assert "ExampleIntegrationPROD" in (example.web_service_path or "")
    ops = {op.operation: op for op in example.operations}
    created = ops["CreatedThing"]
    assert (created.message_log_level, created.auditing_period) == ("Debug", "30")
    assert (created.num_of_retries, created.retry_timeout) == ("3", "00:00:10")
    assert ops["ChangedThing"].auditing_period is None
    # both %OASINSTALLDATADIR% forms and the C:\SchneiderElectricData\\ form resolve
    errors = {e.param_name: e for e in example.error_configurations}
    assert set(errors) == {"Created_ErrorConfigurationPath", "Changed_ErrorConfigurationPath"}
    created_err = errors["Created_ErrorConfigurationPath"]
    assert created_err.interface == "Created"
    assert created_err.severity_eventing_threshold == "Error"
    entry = created_err.entries[0]
    assert (entry.adms_id, entry.adms_severity, entry.code) == ("ADMS-1", "Error", "E100")
    assert (entry.severity, entry.reason, entry.details) == ("Error", "Example reason", "Example details")
    # ambiguous product: no effective value, no crash
    tie = config.product("AdapterTie")
    assert tie.effective("Mode") is None
    # malformed registry file is reported, not raised
    assert any("Broken.xml" in p for p in config.problems)


def test_read_folder() -> None:
    _assert_example(read_installation(INSTALL))


def test_read_zip_without_extracting(tmp_path: Path) -> None:
    archive = tmp_path / "ers.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in INSTALL.rglob("*"):
            if item.is_file():
                zf.write(item, "Enterprise/" + item.relative_to(INSTALL).as_posix())
    _assert_example(read_installation(archive))
    assert [p.name for p in tmp_path.iterdir()] == ["ers.zip"]


def test_case_insensitive_lookup(tmp_path: Path) -> None:
    archive = tmp_path / "ers.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in INSTALL.rglob("*"):
            if item.is_file():
                name = (
                    item.relative_to(INSTALL).as_posix().replace("ADMS/configuration", "adms/CONFIGURATION")
                )
                zf.write(item, name)
    config = read_installation(archive)
    example = config.product("AdapterExample")
    assert len(example.error_configurations) == 2


def test_missing_target_is_a_problem(tmp_path: Path) -> None:
    reg = tmp_path / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"
    reg.mkdir(parents=True)
    (reg / "AdapterGone.xml").write_text(
        '<OASYSDNARegistry><Product Name="AdapterGone">'
        r'<Value Name="WebServiceConfiguration">%OASINSTALLDATADIR%\ADMS\nope.xml</Value>'
        "</Product></OASYSDNARegistry>",
        encoding="utf-8",
    )
    config = read_installation(tmp_path)
    assert config.product("AdapterGone").web_service_path is None
    assert any("not found" in p for p in config.problems)


def test_oversized_registry_file_reported(tmp_path: Path) -> None:
    reg = tmp_path / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"
    reg.mkdir(parents=True)
    (reg / "AdapterBig.xml").write_bytes(b"<OASYSDNARegistry>" + b" " * 4096 + b"</OASYSDNARegistry>")
    config = read_installation(tmp_path, max_xml_bytes=1024)
    assert config.products == ()
    assert any("limit" in p for p in config.problems)


def test_unusable_path_raises(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        read_installation(tmp_path / "missing.zip")
