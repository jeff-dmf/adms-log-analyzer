"""config_observed at ingestion, config_param from the Registry, and store.config queries."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from dmslog.ingest.pipeline import run_ingest
from dmslog.store import create_case_db
from dmslog.store.config import (
    config_for,
    config_products,
    logged_name,
    normalize_name,
    payload_expected,
    product_for_process,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "config" / "SchneiderElectricData"
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("make_fixture", mf)
_spec.loader.exec_module(mf)

LOG_REL = "SchneiderElectricData/OASyS/Servers/Log/dnaApp_zone1/SVC/WfmAdapter_1111.log"
WS_PATH = (
    r"C:\SchneiderElectricData\\ADMS\configuration\ExampleIntegrationPROD"
    r"\ExampleAdapter_WebServiceConfiguration.xml"
)


def startup_lines(log_level: str = "Info") -> list[str]:
    cfg = "[Configuration] "
    return [
        mf.header(0, "ALWAYS", f"{cfg}[LogLevel]: [{log_level}]. Type: [ConfigurationRegistry]"),
        mf.header(
            1, "ALWAYS", f"{cfg}[PathToWebServiceConfiguration]: [{WS_PATH}]. Type: [ConfigurationRegistry]"
        ),
        mf.header(2, "ALWAYS", f"{cfg}[AddressingModeString]: [Local]. Type: [ConfigurationRegistry]"),
        mf.header(3, "ALWAYS", f"{cfg}[Default] [SendTimeout]: [00:01:00]."),
        mf.header(4, "INFO", "Something else happened."),
    ]


def build_folder(root: Path, lines: list[str] | None = None, registry: bool = True) -> Path:
    if registry:
        shutil.copytree(FIXTURE, root / "SchneiderElectricData")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(mf.render(lines if lines is not None else startup_lines()))
    return root


@pytest.fixture
def case(tmp_path):
    folder = build_folder(tmp_path / "coll")
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, folder)
    yield conn, folder
    conn.close()


def observed(conn):
    return {
        r[0]: tuple(r[1:])
        for r in conn.execute("SELECT name, value, is_default, entry_id FROM config_observed")
    }


def test_observed_lines_are_stored(case):
    conn, _ = case
    rows = observed(conn)
    assert set(rows) == {"LogLevel", "PathToWebServiceConfiguration", "AddressingModeString", "SendTimeout"}
    assert rows["LogLevel"][:2] == ("Info", 0)
    assert rows["SendTimeout"][:2] == ("00:01:00", 1)
    assert rows["PathToWebServiceConfiguration"][0] == WS_PATH
    ids = {r[0] for r in conn.execute("SELECT id FROM entry")}
    assert {v[2] for v in rows.values()} <= ids


def test_reingest_is_idempotent(case):
    conn, folder = case
    before = (observed(conn), conn.execute("SELECT * FROM config_param ORDER BY 1,2,3,4,5,6").fetchall())
    run_ingest(conn, folder)
    after = (observed(conn), conn.execute("SELECT * FROM config_param ORDER BY 1,2,3,4,5,6").fetchall())
    assert before == after and before[0] and before[1]


def test_growth_and_reindex_do_not_leave_stale_rows(case):
    conn, folder = case
    log = folder / LOG_REL
    log.write_bytes(mf.render(startup_lines("Verbose")))  # rewritten file: same size class, new content
    run_ingest(conn, folder)
    rows = conn.execute("SELECT name, value FROM config_observed WHERE name = 'LogLevel'").fetchall()
    assert rows == [("LogLevel", "Verbose")]
    # entries of the removed rows are gone: every config_observed row points at a live entry
    dangling = conn.execute(
        "SELECT count(*) FROM config_observed WHERE entry_id NOT IN (SELECT id FROM entry)"
    ).fetchone()[0]
    assert dangling == 0


def test_config_param_layers_and_scope(case):
    conn, _ = case
    assert [p["product"] for p in config_products(conn)] == ["AdapterWfm"]  # CoreSDK is not stored
    rows = conn.execute(
        "SELECT layer, layer_label, effective, status, value FROM config_param"
        " WHERE scope='registry' AND name='LogLevel' ORDER BY layer"
    ).fetchall()
    assert rows == [(-1, "", 0, "overridden", "Info"), (2, "", 1, "wins", "DebugLog")]
    ws = conn.execute(
        "SELECT scope, name, value, layer, effective FROM config_param WHERE scope LIKE 'ws:%'"
        " AND scope = 'ws:CreatedThing' AND name='NumOfRetries'"
    ).fetchall()
    assert ws == [("ws:CreatedThing", "NumOfRetries", "3", 2, 1)]


def test_config_for_layers_effective_and_divergence(case):
    conn, _ = case
    result = config_for(conn, "AdapterWfm")
    assert result["product"] == "AdapterWfm"
    keys = {k["name"]: k for k in result["keys"]}
    level = keys["LogLevel"]
    assert [x["layer"] for x in level["layers"]] == [-1, 2]
    assert level["effective"] == "DebugLog" and level["effective_layer"] == 2 and level["status"] == "wins"
    assert (
        level["observed"]["value"] == "Info" and level["diverges"] is True
    )  # log says Info, Registry DebugLog
    # alias: WebServiceConfiguration is logged as PathToWebServiceConfiguration; path forms differ but agree
    ws = keys["WebServiceConfiguration"]
    assert ws["observed"]["name"] == "PathToWebServiceConfiguration" and ws["diverges"] is False
    # alias: AddressingMode -> AddressingModeString
    assert (
        keys["AddressingMode"]["observed"]["value"] == "Local" and keys["AddressingMode"]["diverges"] is False
    )
    # observed as default, different from the Registry
    send = keys["SendTimeout"]
    assert send["observed"]["is_default"] is True and send["diverges"] is False  # 00:01:00 == 00:01:00
    # not logged at all
    assert keys["Created_ErrorConfigurationPath"]["observed"] is None
    assert keys["Created_ErrorConfigurationPath"]["diverges"] is False


def test_payload_expected_uses_observed_level(case):
    conn, _ = case
    result = config_for(conn, "AdapterWfm")
    assert result["log_level"] == {"value": "Info", "source": "observed"}
    ops = {o["operation"]: o for o in result["operations"]}
    assert set(ops) == {
        "CreatedThing",
        "ChangedThing",
        "QuietThing",
        "OddThing",
    }  # the PROD file, not the old one
    assert ops["CreatedThing"]["payload_expected"] is True  # Info >= Info
    assert ops["ChangedThing"]["payload_expected"] is False  # Info < Verbose
    assert ops["QuietThing"]["payload_expected"] is False  # None = never
    assert ops["OddThing"]["payload_expected"] is None  # unknown level
    assert ops["CreatedThing"]["settings"]["RetryTimeout"] == "00:00:10"


def test_registry_level_is_used_without_observation(tmp_path):
    folder = build_folder(tmp_path / "coll", lines=[mf.header(0, "INFO", "Nothing configured here.")])
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, folder)
    result = config_for(conn, "AdapterWfm")
    assert result["log_level"] == {"value": "DebugLog", "source": "registry"}
    ops = {o["operation"]: o for o in result["operations"]}
    assert ops["CreatedThing"]["payload_expected"] is False  # DebugLog (3) < Info (5)
    assert all(k["observed"] is None and not k["diverges"] for k in result["keys"])
    conn.close()


def test_collection_without_registry_stores_no_config(tmp_path):
    folder = build_folder(tmp_path / "coll", registry=False)
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, folder)
    assert config_products(conn) == [] and config_for(conn, "AdapterWfm") is None
    assert len(observed(conn)) == 4  # observed values do not depend on the Registry
    conn.close()


def test_zip_collection(tmp_path):
    folder = build_folder(tmp_path / "coll")
    archive = tmp_path / "ERS_CL_00000000-0000-4000-8000-000000000001_20261008124051_Enterprise_demo.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(folder).as_posix())
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, archive)
    assert [p["product"] for p in config_products(conn)] == ["AdapterWfm"]
    assert config_for(conn, "AdapterWfm")["operations"]
    conn.close()


def test_reingest_after_registry_change_replaces_rows(case):
    conn, folder = case
    reg = folder / "SchneiderElectricData/OASyS/Servers/config/Registry/AdapterWfm_2.xml"
    reg.write_text(reg.read_text(encoding="utf-8").replace("DebugLog", "Warning"), encoding="utf-8")
    run_ingest(conn, folder)
    level = {k["name"]: k for k in config_for(conn, "AdapterWfm")["keys"]}["LogLevel"]
    assert level["effective"] == "Warning" and len(level["layers"]) == 2


def test_unknown_product_and_group(case):
    conn, _ = case
    assert config_for(conn, "AdapterNope") is None
    assert config_for(conn, "AdapterWfm", group=999) is None


@pytest.mark.parametrize(
    ("log_level", "message", "expected"),
    [
        ("DebugLog", "Info", False),
        ("Warning", "Info", False),
        ("Info", "Info", True),
        ("Verbose", "Info", True),
        ("Verbose", "Verbose", True),
        ("Debug", "DebugLog", True),
        ("Information", "info", True),
        ("Verbose", "None", False),
        ("Error", "Info", None),
        (None, "Info", None),
        ("Info", None, None),
        ("Info", "Sideways", None),
    ],
)
def test_payload_expected_rule(log_level, message, expected):
    assert payload_expected(log_level, message) is expected


def test_timespans_compare_by_value():
    from dmslog.store.config import _same_value

    assert _same_value("0:00:30", "00:00:30")
    assert _same_value("1.00:00:00", "24:00:00")
    assert not _same_value("00:00:30", "00:01:00")
    assert _same_value("True", "true") and not _same_value("1", "2")


def test_name_helpers():
    assert normalize_name("Created_ErrorConfigurationPath") == "createderrorconfigurationpath"
    assert logged_name("Created_ErrorConfigurationPath") == normalize_name("CreatedErrorConfigurationPath")
    assert logged_name("WebServiceConfiguration") == "pathtowebserviceconfiguration"
    assert logged_name("RemoteHouseKeepingType") == "remotehousekeepingtypestring"


def test_product_for_process():
    products = {"AdapterWfm", "AdapterAVL", "CustomAdapterChangeIncidentDevice", "CoreSDK"}
    assert product_for_process("WfmAdapter", products) == "AdapterWfm"
    assert product_for_process("AVLAdapter", products) == "AdapterAVL"
    assert (
        product_for_process("CustomAdapterChangeIncidentDevice", products)
        == "CustomAdapterChangeIncidentDevice"
    )
    assert product_for_process("OntAdapter", products) is None
    assert product_for_process("CoreSDK", products) == "CoreSDK"
    assert product_for_process(None, products) is None


def test_later_startup_default_beats_earlier_registry_value(tmp_path):
    cfg = "[Configuration] "
    lines = [
        mf.header(0, "ALWAYS", f"{cfg}[SendTimeout]: [00:01:00]. Type: [ConfigurationRegistry]"),
        mf.header(
            1000, "ALWAYS", f"{cfg}[Default] [SendTimeout]: [00:01:00]."
        ),  # same startup: Registry wins
        mf.header(3_600_000, "ALWAYS", f"{cfg}[Default] [SendTimeout]: [00:00:30]."),  # a later startup
    ]
    folder = build_folder(tmp_path / "coll", lines=lines)
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, folder)
    keys = {k["name"]: k for k in config_for(conn, "AdapterWfm")["keys"]}
    observed_send = keys["SendTimeout"]["observed"]
    assert (observed_send["value"], observed_send["is_default"]) == ("00:00:30", True)
    conn.close()


def test_fast_restart_default_is_not_dropped(tmp_path):
    cfg = "[Configuration] "
    lines = [
        mf.header(0, "ALWAYS", f"{cfg}[SendTimeout]: [00:01:00]. Type: [ConfigurationRegistry]"),
        mf.header(5000, "ALWAYS", f"{cfg}[Default] [SendTimeout]: [00:00:30]."),  # restart seconds later
    ]
    folder = build_folder(tmp_path / "coll", lines=lines)
    conn = create_case_db(tmp_path / "case.sqlite")
    run_ingest(conn, folder)
    keys = {k["name"]: k for k in config_for(conn, "AdapterWfm")["keys"]}
    assert keys["SendTimeout"]["observed"]["value"] == "00:00:30"
    conn.close()
