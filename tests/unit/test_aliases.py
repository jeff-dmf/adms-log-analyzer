"""Local aliases (real name -> public token): schema, loading, normalization and ingest boundaries.

Only synthetic names are used here (``AcmeWidgetAdapter``), never a real one.
"""

import shutil
from pathlib import Path

import pytest

from dmslog import aliases as al
from dmslog.ingest.discover import discover
from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.knowledge.installation import read_installation
from dmslog.store import create_case_db

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LOG = ROOT / "tests" / "fixtures" / "families" / "dnaApp_zone1" / "SVC"
GOOD = """\
version = 1

[[alias]]
public = "Custom"
real = "AcmeWidgetAdapter"

[[alias]]
public = "Custom"
real = "Acme_Widget"
"""


def synthetic() -> al.Aliases:
    return al.Aliases.from_pairs([("AcmeWidgetAdapter", "Custom"), ("Acme_Widget", "Custom")])


# ----------------------------------------------------------------------------- schema and loading


def test_missing_file_is_no_aliases(tmp_path):
    loaded = al.load(tmp_path)
    assert len(loaded) == 0 and loaded.error is None
    assert loaded.apply("AcmeWidgetAdapter") == "AcmeWidgetAdapter"


def test_valid_file_loads_with_bom(tmp_path):
    (tmp_path / "aliases.toml").write_bytes(b"\xef\xbb\xbf" + GOOD.encode())
    loaded = al.load(tmp_path)
    assert loaded.error is None and len(loaded) == 2
    assert loaded.apply("x_Acme_Widget_y") == "x_Custom_y"
    assert al.describe(loaded) == "2 loaded"


@pytest.mark.parametrize(
    "text",
    [
        "version = [",  # not TOML
        'version = 2\n[[alias]]\npublic = "Custom"\nreal = "a"\n',
        '[[alias]]\npublic = "Custom"\nreal = "a"\n',  # no version
        "version = 1\nextra = 1\n",
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "a"\nnote = "x"\n',
        'version = 1\n[[alias]]\npublic = "bad name"\nreal = "a"\n',
        'version = 1\n[[alias]]\npublic = ""\nreal = "a"\n',
        'version = 1\n[[alias]]\npublic = "' + "p" * 65 + '"\nreal = "a"\n',
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = ""\n',
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "' + "r" * 129 + '"\n',
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "a\\u0007b"\n',
        'version = 1\n[[alias]]\npublic = "Custom"\n',
        'version = 1\n[[alias]]\npublic = "A"\nreal = "x"\n[[alias]]\npublic = "B"\nreal = "x"\n',
        "version = 1\nalias = 3\n",
    ],
)
def test_invalid_file_is_ignored_with_a_clear_message(tmp_path, text):
    (tmp_path / "aliases.toml").write_text(text, encoding="utf-8")
    loaded = al.load(tmp_path)
    assert len(loaded) == 0 and loaded.error and loaded.error.startswith("aliases.toml")
    assert "ignored" in al.describe(loaded)


def test_error_text_never_contains_the_values(tmp_path):
    (tmp_path / "aliases.toml").write_text(
        'version = 1\n[[alias]]\npublic = "bad name"\nreal = "SecretReal"\n', encoding="utf-8"
    )
    assert "SecretReal" not in (al.load(tmp_path).error or "")


def test_get_is_cached_and_refreshes_on_change(tmp_path):
    assert len(al.get(tmp_path)) == 0
    (tmp_path / "aliases.toml").write_text(GOOD, encoding="utf-8")
    first = al.get(tmp_path)
    assert len(first) == 2 and al.get(tmp_path) is first


# ----------------------------------------------------------------------------- normalization


def test_apply_is_longest_first():
    a = al.Aliases.from_pairs([("Acme", "Short"), ("AcmeWidget", "Long")])
    assert a.apply("AcmeWidget_1") == "Long_1"


def test_apply_is_case_sensitive_but_apply_name_is_not():
    a = synthetic()
    assert a.apply("acmewidgetadapter_1.log") == "acmewidgetadapter_1.log"
    assert a.apply_name("ACMEWIDGETADAPTER_1.log") == "Custom_1.log"
    assert a.apply_name("Other_1.log") == "Other_1.log"


# ----------------------------------------------------------------------------- ingest boundaries


def _folder(tmp_path: Path) -> Path:
    root = tmp_path / "Log" / "dnaApp_zone1" / "SVC"
    root.mkdir(parents=True)
    shutil.copyfile(
        FIXTURE_LOG / "CustomAdapterChangeIncidentDevice_1187.log",
        root / "AcmeWidgetAdapterChangeIncidentDevice_9.log",
    )
    return tmp_path / "Log"


def test_discover_resolves_the_family_only_with_aliases(tmp_path):
    folder = _folder(tmp_path)
    plain = next(discover(folder))
    assert plain.family != "AEDP"
    aliased = next(discover(folder, aliases=al.Aliases.from_pairs([("AcmeWidget", "Custom")])))
    assert aliased.family == "AEDP" and aliased.process == "CustomAdapterChangeIncidentDevice"
    assert aliased.member == plain.member  # the member is still the real path: it is read back through it


def test_end_to_end_ingest_stores_the_public_name(tmp_path):
    folder = _folder(tmp_path)
    a = al.Aliases.from_pairs([("AcmeWidget", "Custom")])

    conn = create_case_db(tmp_path / "with.sqlite")
    run_ingest(conn, folder, IngestOptions(scope="all", aliases=a))
    rows = conn.execute("SELECT family, process FROM stream").fetchall()
    assert rows == [("AEDP", "CustomAdapterChangeIncidentDevice")]
    assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] > 0
    conn.close()

    conn = create_case_db(tmp_path / "without.sqlite")
    run_ingest(conn, folder, IngestOptions(scope="all"))
    assert [r[0] for r in conn.execute("SELECT family FROM stream")] != ["AEDP"]
    conn.close()


def test_registry_layer_label_and_names_use_the_public_token(tmp_path):
    registry = tmp_path / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"
    registry.mkdir(parents=True)
    (registry / "AdapterOnt_1_AcmeWidget.xml").write_text(
        '<OASYSDNARegistry><Product Name="AdapterOnt"><Value Name="LogLevel">Info</Value></Product>'
        "</OASYSDNARegistry>",
        encoding="utf-8",
    )
    a = al.Aliases.from_pairs([("AcmeWidget", "Custom")])
    labels = {p.label for p in read_installation(tmp_path, aliases=a).products[0].params}
    assert labels == {"Custom"}
    labels = {p.label for p in read_installation(tmp_path).products[0].params}
    assert labels == {"AcmeWidget"}


def test_registry_names_in_a_zip_and_web_service_operations(tmp_path):
    base = "SchneiderElectricData"
    registry = tmp_path / base / "OASyS" / "Servers" / "config" / "Registry"
    registry.mkdir(parents=True)
    (registry / "AdapterAcmeWidget.xml").write_text(
        '<OASYSDNARegistry><Product Name="AdapterAcmeWidget">'
        '<Value Name="WebServiceConfiguration">%OASINSTALLDATADIR%\\ADMS\\ws.xml</Value>'
        "</Product></OASYSDNARegistry>",
        encoding="utf-8",
    )
    ws = tmp_path / base / "ADMS" / "ws.xml"
    ws.parent.mkdir(parents=True)
    ws.write_text(
        '<Config><WebServiceConfiguration Name="AcmeWidgetOp"><MessageLogLevel>Info</MessageLogLevel>'
        "</WebServiceConfiguration></Config>",
        encoding="utf-8",
    )
    a = al.Aliases.from_pairs([("AcmeWidget", "Custom")])
    product = read_installation(tmp_path, aliases=a).products[0]
    assert product.product == "AdapterCustom"
    assert [op.operation for op in product.operations] == ["CustomOp"]
