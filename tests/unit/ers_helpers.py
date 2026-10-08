"""Helpers that build tiny synthetic ERS zips and folders (no real log content)."""

from __future__ import annotations

import zipfile
from pathlib import Path

GUID = "00000000-0000-4000-8000-000000000001"
OTHER_GUID = "AAAAAAAA-1111-2222-3333-BBBBBBBBBBBB"
LOG = "SchneiderElectricData/OASyS/Servers/Log"
APP = f"{LOG}/dnaApp_zone1/DMS_INTEGRATION"

ENTERPRISE_MEMBERS = [
    f"{APP}/WfmAdapter_100.log",
    f"{APP}/WfmAdapter_100.log.0",
    f"{APP}/OntAdapter_200.log",
    f"{APP}/CustomAdapterChangeIncidentDevice_300.log",
    f"{APP}/NDSProxy_400.log",
    f"{LOG}/oasErrLog.log",
    f"{LOG}/oasErrLog.log.0",
    f"{LOG}/BLTHost.DMS_INTEGRATION.log",
    f"{LOG}/dnaService.DMS_INTEGRATION.log",
    f"{LOG}/dnaService.RCS.log",
    f"{LOG}/ProcessMonitor.DMS_INTEGRATION.log",
    f"{LOG}/arbRx.DMS_INTEGRATION.log",
    f"{LOG}/NMC.log",
    f"{LOG}/NMC.log.3",
    f"{LOG}/PSengine.log",
    f"{LOG}/arbIP_500.log",
    f"{LOG}/ServiceShutdown_600.log",
    f"{LOG}/ConfigTool_700.log",
    "Deployment/Logs/ConfigTool_800.log",
    "SchneiderElectricData/OASyS/Servers/config/Registry/AdapterWfm.xml",
    "SystemEvents/System.evtx",
    "users/u1/appdata/local/temp/ERS-1/computer_info/Environment.txt",
    "SchneiderElectricData/ADMS/tools/odd.bin",
]

SENSITIVE_MEMBERS = [
    f"{APP}/WfmAdapter_100.sensitive.log",
    f"{APP}/OntAdapter_200.sensitive.log",
    f"{LOG}/BLTHost.DMS_INTEGRATION.sensitive.log",
]


def ers_name(
    guid: str = GUID, ts: str = "20261008124051", sensitive: bool = False, desc: str = "case1"
) -> str:
    middle = "Sensitive_Enterprise" if sensitive else "Enterprise"
    return f"ERS_CL_{guid}_{ts}_{middle}_{desc}.zip"


def make_zip(path: Path, members: list[str], payload: bytes = b"line\r\n") -> Path:
    """Write a zip whose members all hold `payload` (listing never reads it back)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in members:
            archive.writestr(name, payload)
    return path


def make_pair(directory: Path, guid: str = GUID, ts: str = "20261008124051") -> tuple[Path, Path]:
    enterprise = make_zip(directory / ers_name(guid, ts), ENTERPRISE_MEMBERS)
    sensitive = make_zip(directory / ers_name(guid, ts, sensitive=True), SENSITIVE_MEMBERS)
    return enterprise, sensitive


def make_folder(root: Path, members: list[str]) -> Path:
    for name in members:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x\n")
    return root
