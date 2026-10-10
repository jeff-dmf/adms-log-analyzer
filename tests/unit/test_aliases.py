"""Local aliases (real name -> public token): schema, loading, normalization and ingest boundaries.

Only synthetic names are used here (``AcmeWidgetAdapter``), never a real one.
"""

import shutil
import zipfile
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


# ----------------------------------------------------------------------------- review follow-ups


@pytest.mark.parametrize(
    "text",
    [
        "version = 1\n[AcmeWidget]\n[AcmeWidget]\n",  # duplicated table: the parser names it
        "version = 1\nAcmeWidget = 1\n",  # unknown top-level key
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "x"\nAcmeWidget = "y"\n',  # unknown key in entry
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget\n',  # broken string
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\npublic = "Other"\n',
    ],
)
def test_diagnostics_never_contain_file_content(tmp_path, text):
    (tmp_path / "aliases.toml").write_text(text, encoding="utf-8")
    loaded = al.load(tmp_path)
    assert loaded.error and "Acme" not in loaded.error
    assert "Acme" not in al.describe(loaded)


def test_apply_is_a_single_pass_over_the_original_text():
    a = al.Aliases.from_pairs([("Acme", "Short"), ("AcmeWidget", "Long")])
    assert a.apply("AcmeWidget") == "Long"
    assert a.apply_name("acmewidget") == "Long"
    assert a.apply_name("AcmeWIDGET") == "Long"  # whole configured string, never "ShortWIDGET"
    chained = al.Aliases.from_pairs([("AcmeWidget", "Custom"), ("Custom", "Other")])
    assert chained.apply("AcmeWidget_1") == "Custom_1"
    assert chained.apply_name("ACMEWIDGET_1") == "Custom_1"
    assert chained.sanitize("AcmeWidget and Custom") == "Custom and Other"


@pytest.mark.parametrize(
    "body",
    [
        'version = 1.0\n[[alias]]\npublic = "Custom"\nreal = "a"\n',
        'version = true\n[[alias]]\npublic = "Custom"\nreal = "a"\n',
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "a\\u0085b"\n',  # C1 control
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "a\\u200bb"\n',  # format char
        'version = 1\n[[alias]]\npublic = "Cust\\u0085om"\nreal = "a"\n',
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Other"\nreal = "ACMEWIDGET"\n',  # equal ignoring case, different public
    ],
)
def test_stricter_validation(tmp_path, body):
    (tmp_path / "aliases.toml").write_text(body, encoding="utf-8")
    loaded = al.load(tmp_path)
    assert len(loaded) == 0 and loaded.error


def test_identical_duplicates_are_fine():
    parsed = al.parse(
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Custom"\nreal = "ACMEWIDGET"\n'
    )
    assert parsed.error is None and len(parsed) == 2


def test_registry_diagnostics_use_the_public_name(tmp_path):
    from dmslog.knowledge.registry import RegistryError, parse_registry_xml

    a = al.Aliases.from_pairs([("AcmeWidget", "Custom")])
    cases = [
        b"<OASYSDNARegistry><Product",  # malformed
        b"<Other/>",  # wrong root
        b"<OASYSDNARegistry/>",  # no product
    ]
    for data in cases:
        with pytest.raises(RegistryError) as info:
            parse_registry_xml(data, filename="AdapterOnt_1_AcmeWidget.xml", aliases=a)
        assert "Acme" not in str(info.value) and "Custom" in str(info.value)


def test_failed_source_error_and_warnings_use_the_public_name(tmp_path):
    payload = b"2026-01-01 00:00:00,000 INFO x\r\n" * 50
    zpath = tmp_path / "ERS.zip"
    member = "Log/dnaApp_zone1/SVC/AcmeWidgetAdapterChangeIncidentDevice_9.log"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        z.writestr(member, payload)
    raw = bytearray(zpath.read_bytes())
    at = raw.index(payload[:20])
    raw[at + 5] ^= 0xFF  # corrupt the stored data: CRC mismatch while reading
    zpath.write_bytes(bytes(raw))
    a = al.Aliases.from_pairs([("AcmeWidget", "Custom")])
    events = []
    conn = create_case_db(tmp_path / "case.sqlite")
    result = run_ingest(conn, zpath, IngestOptions(scope="all", aliases=a), events.append)
    assert result.sources_failed == 1
    stored = conn.execute("SELECT error FROM source WHERE status = 'error'").fetchone()[0]
    assert stored and "Acme" not in stored and stored.startswith("BadZipFile")
    assert all("Acme" not in (d.member + (d.error or "")) for d in result.details)
    assert all("Acme" not in (e.member or "") for e in events)
    assert not any("Acme" in w for w in result.warnings)
    # the DB keeps the real member: it is how the file is reopened
    assert conn.execute("SELECT member FROM source").fetchone()[0] == member
    conn.close()


# ----------------------------------------------------------------------------- review round 2

CHAIN = [("AcmeWidget", "Custom"), ("Custom", "Other")]


def test_matched_alternative_decides_the_token_for_unicode_equivalents():
    import re

    a = al.Aliases.from_pairs([("Acmeİ", "Custom")])  # dotted capital I
    for text in ("ACMEI", "acmei", "Acmeİ", "acmeİ"):
        matched = re.fullmatch(re.escape("Acmeİ"), text, re.IGNORECASE) is not None
        assert (a.sanitize(text) == "Custom") == matched
    kelvin = al.Aliases.from_pairs([("Kelvin", "Custom")])
    assert kelvin.sanitize("KELVIN_1") == "Custom_1"  # Kelvin sign: equal under the matcher


@pytest.mark.parametrize(
    "first,second", [("Kelvin", "Kelvin"), ("Acmeİ", "ACMEI"), ("AcmeWidget", "ACMEWIDGET")]
)
def test_conflicts_follow_the_matcher_equivalence(first, second):
    body = (
        f'version = 1\n[[alias]]\npublic = "A"\nreal = "{first}"\n'
        f'[[alias]]\npublic = "B"\nreal = "{second}"\n'
    )
    parsed = al.parse(body)
    assert (parsed.error is not None) == al.same_spelling(first, second)
    if first == "Kelvin":
        assert al.same_spelling(first, second) and parsed.error


@pytest.mark.parametrize("ch", [" ", " ", "​", "\u0085", "\x07", ""])
def test_non_printable_characters_are_rejected(ch):
    body = f'version = 1\n[[alias]]\npublic = "Custom"\nreal = "a{ch}b"\n'
    assert al.parse(body).error


def test_space_in_real_is_allowed():
    assert al.parse('version = 1\n[[alias]]\npublic = "Custom"\nreal = "Acme Widget"\n').error is None


def test_registry_diagnostic_is_normalized_once_with_chained_aliases(tmp_path):
    from dmslog.knowledge.registry import RegistryError, parse_registry_xml

    a = al.Aliases.from_pairs(CHAIN)
    with pytest.raises(RegistryError) as info:
        parse_registry_xml(b"<Other/>", filename="AdapterAcmeWidget.xml", aliases=a)
    assert "AdapterCustom.xml" in str(info.value) and "Other" not in str(info.value).split(":")[0]

    registry = tmp_path / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"
    registry.mkdir(parents=True)
    (registry / "AdapterAcmeWidget.xml").write_text("<OASYSDNARegistry><Product", encoding="utf-8")
    (registry / "AdapterGood.xml").write_text(
        '<OASYSDNARegistry><Product Name="AdapterGood">'
        '<Value Name="WebServiceConfiguration">%OASINSTALLDATADIR%\\ADMS\\ws_AcmeWidget.xml</Value>'
        "</Product></OASYSDNARegistry>",
        encoding="utf-8",
    )
    ws = tmp_path / "SchneiderElectricData" / "ADMS" / "ws_AcmeWidget.xml"
    ws.parent.mkdir(parents=True)
    ws.write_text("<Config", encoding="utf-8")
    problems = read_installation(tmp_path, aliases=a).problems
    text = "\n".join(problems)
    assert "AdapterCustom.xml" in text and "ws_Custom.xml" in text
    assert "Other" not in text and "Acme" not in text

    big = read_installation(tmp_path, max_xml_bytes=10, aliases=a).problems  # collection.read errors
    assert big and all("Other" not in p and "Acme" not in p for p in big)


def test_ingest_text_is_normalized_once_with_chained_aliases(tmp_path):
    payload = b"2026-01-01 00:00:00,000 INFO x\r\n" * 50
    zpath = tmp_path / "ERS.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Log/dnaApp_zone1/SVC/AcmeWidgetAdapterChangeIncidentDevice_9.log", payload)
    raw = bytearray(zpath.read_bytes())
    raw[raw.index(payload[:20]) + 5] ^= 0xFF
    zpath.write_bytes(bytes(raw))
    events = []
    conn = create_case_db(tmp_path / "case.sqlite")
    a = al.Aliases.from_pairs(CHAIN)
    result = run_ingest(conn, zpath, IngestOptions(scope="all", aliases=a), events.append)
    stored = conn.execute("SELECT error FROM source WHERE status = 'error'").fetchone()[0]
    shown = [stored, *(d.member + (d.error or "") for d in result.details), *(e.member or "" for e in events)]
    assert any("Custom" in t for t in shown)
    assert all("Other" not in t and "Acme" not in t for t in shown)
    conn.close()


def test_cli_and_api_messages_are_normalized_once_with_chained_aliases(tmp_path, capsys):
    from dmslog.cli import main

    data = tmp_path / "data"
    data.mkdir()
    (data / "aliases.toml").write_text(
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Other"\nreal = "Custom"\n',
        encoding="utf-8",
    )
    missing = tmp_path / "AcmeWidget_missing.zip"
    assert main(["ingest", str(missing), "--data-dir", str(data)]) != 0
    err = capsys.readouterr().err
    assert "Custom_missing" in err and "Other_missing" not in err and "AcmeWidget" not in err


def test_api_error_message_is_normalized_once_with_chained_aliases(tmp_path):
    import threading

    from test_api import Client

    from dmslog.server.app import DmslogServer

    data = tmp_path / "data"
    data.mkdir()
    (data / "aliases.toml").write_text(
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Other"\nreal = "Custom"\n',
        encoding="utf-8",
    )
    srv = DmslogServer(0, data_dir=data)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        client = Client(srv)
        assert client.post("/api/cases", {"name": "c1"})[0] == 201
        missing = str(tmp_path / "AcmeWidget_missing.zip")
        status, body, _ = client.post("/api/cases/c1/collections", {"path": missing, "scope": "default"})
        assert status == 400
        assert "Custom_missing" in body["message"] and "Other" not in body["message"]
        assert "Acme" not in body["message"]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# ----------------------------------------------------------------------------- review round 3

_REG = "SchneiderElectricData/OASyS/Servers/config/Registry"
_ADMS = "SchneiderElectricData/ADMS"


def _product_xml(*values: tuple[str, str], name: str = "AdapterAcmeWidget") -> str:
    body = "".join(f'<Value Name="{k}">{v}</Value>' for k, v in values)
    return f'<OASYSDNARegistry><Product Name="{name}">{body}</Product></OASYSDNARegistry>'


# (case id, files {member: text}, read_installation kwargs, expected public fragment)
_DIAGNOSTIC_CASES = [
    ("malformed", {f"{_REG}/AdapterAcmeWidget.xml": "<OASYSDNARegistry><Product"}, {}, "AdapterCustom.xml"),
    ("wrong-root", {f"{_REG}/AdapterAcmeWidget.xml": "<AcmeWidget/>"}, {}, "root element is 'Custom'"),
    ("no-product", {f"{_REG}/AdapterAcmeWidget.xml": "<OASYSDNARegistry/>"}, {}, "AdapterCustom.xml"),
    (
        "dtd",
        {f"{_REG}/AdapterAcmeWidget.xml": '<!DOCTYPE AcmeWidget [<!ENTITY a "b">]><OASYSDNARegistry/>'},
        {},
        "AdapterCustom.xml",
    ),
    (
        "oversize",
        {f"{_REG}/AdapterAcmeWidget.xml": _product_xml(("LogLevel", "Info"))},
        {"max_xml_bytes": 20},
        "AdapterCustom.xml",
    ),
    (
        "missing-target",
        {
            f"{_REG}/AdapterAcmeWidget.xml": _product_xml(
                ("AcmeWidget_ErrorConfigurationPath", "%OASINSTALLDATADIR%\\ADMS\\gone_AcmeWidget.xml")
            )
        },
        {},
        "AdapterCustom.Custom_ErrorConfigurationPath",
    ),
    (
        "unknown-param-target",
        {
            f"{_REG}/AdapterAcmeWidget.xml": _product_xml(
                ("WebServiceConfiguration", "%OASINSTALLDATADIR%\\ADMS\\nowhere_AcmeWidget.xml")
            )
        },
        {},
        "AdapterCustom.WebServiceConfiguration",
    ),
    (
        "web-service-malformed",
        {
            f"{_REG}/AdapterAcmeWidget.xml": _product_xml(
                ("WebServiceConfiguration", "%OASINSTALLDATADIR%\\ADMS\\ws_AcmeWidget.xml")
            ),
            f"{_ADMS}/ws_AcmeWidget.xml": "<Config",
        },
        {},
        "ws_Custom.xml",
    ),
    (
        "error-config-malformed",
        {
            f"{_REG}/AdapterAcmeWidget.xml": _product_xml(
                ("Op_ErrorConfigurationPath", "%OASINSTALLDATADIR%\\ADMS\\err_AcmeWidget.xml")
            ),
            f"{_ADMS}/err_AcmeWidget.xml": "<AcmeWidget><Error>",
        },
        {},
        "err_Custom.xml",
    ),
]


@pytest.mark.parametrize(
    "files,kwargs,expected", [c[1:] for c in _DIAGNOSTIC_CASES], ids=[c[0] for c in _DIAGNOSTIC_CASES]
)
def test_every_diagnostic_is_built_from_components_normalized_once(tmp_path, files, kwargs, expected):
    for member, text in files.items():
        target = tmp_path / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    a = al.Aliases.from_pairs(CHAIN)
    problems = "\n".join(read_installation(tmp_path, aliases=a, **kwargs).problems)
    assert expected in problems, problems
    assert "Acme" not in problems and "Other" not in problems, problems


def test_zip_read_error_and_unusable_path_diagnostics(tmp_path):
    from dmslog.knowledge.registry import RegistryError

    a = al.Aliases.from_pairs(CHAIN)
    zpath = tmp_path / "ERS_AcmeWidget.zip"
    member = f"{_REG}/AdapterAcmeWidget.xml"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        z.writestr(member, _product_xml(("LogLevel", "Info")))
    raw = bytearray(zpath.read_bytes())
    raw[raw.index(b"LogLevel")] ^= 0xFF  # corrupt the stored data: CRC error while reading
    zpath.write_bytes(bytes(raw))
    problems = "\n".join(read_installation(zpath, aliases=a).problems)
    assert "Custom" in problems and "Acme" not in problems and "Other" not in problems, problems

    with pytest.raises(RegistryError) as info:
        read_installation(tmp_path / "AcmeWidget_missing.bin", aliases=a)
    assert "Custom_missing" in str(info.value) and "Acme" not in str(info.value)
    assert "Other" not in str(info.value)


# ----------------------------------------------------------------------------- review round 4

NASTY = [
    ("Acme\\Widget", "Custom"),  # backslash (Windows style)
    ('Acme"W(id)get+[x]*', "Custom"),  # quotes and regex metacharacters
    ("AcmeWidget", "Custom"),
    ("Custom", "Other"),
]


def test_backslash_reals_match_in_slash_paths_and_regex_metacharacters_are_literal():
    a = al.Aliases.from_pairs(NASTY)
    assert a.apply_path("Log/Acme/Widget/x.log") == "Log/Custom/x.log"
    assert a.apply_name("Acme\\Widget.log") == "Custom.log"
    assert a.sanitize('Acme"W(id)get+[x]*_1') == "Custom_1"
    assert a.sanitize("AcmeXWXidXgetXX") == "AcmeXWXidXgetXX"
    assert len(a) == 4


def test_describe_error_never_uses_exception_text():
    import zlib

    from dmslog.diagnostics import describe_error

    nasty = "C:\\Acme\\Widget\\x.xml"
    for exc in (
        PermissionError(13, "denied " + nasty, nasty),
        FileNotFoundError(2, nasty, nasty),
        OSError(5, nasty),
        zipfile.BadZipFile("Bad CRC-32 for file " + repr(nasty)),
        zlib.error("Error -3 " + nasty),
        LookupError("unknown encoding: " + nasty),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, nasty),
        ValueError(nasty),
        KeyError(nasty),
        RuntimeError(nasty),
    ):
        text = describe_error(exc, al.Aliases.from_pairs(NASTY), with_filename=False)
        assert "Acme" not in text and "Widget" not in text and "Other" not in text, text
    assert describe_error(FileNotFoundError(2, "x", nasty), al.Aliases.from_pairs(NASTY)) == (
        "file not found: C:\\Custom\\x.xml"
    ) or "Acme" not in describe_error(FileNotFoundError(2, "x", nasty), al.Aliases.from_pairs(NASTY))


def _folder_with_registry(tmp_path: Path, text: str, name: str = "AdapterAcmeWidget.xml") -> Path:
    registry = tmp_path / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"
    registry.mkdir(parents=True)
    (registry / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_unknown_xml_encoding_is_a_diagnostic_not_an_escape(tmp_path):
    a = al.Aliases.from_pairs(NASTY)
    root = _folder_with_registry(
        tmp_path,
        '<?xml version="1.0" encoding="AcmeWidget"?><OASYSDNARegistry><Product Name="x"/></OASYSDNARegistry>',
    )
    problems = "\n".join(read_installation(root, aliases=a).problems)
    assert "AdapterCustom.xml" in problems and "malformed XML" in problems
    assert "Acme" not in problems and "Other" not in problems


def test_permission_error_on_open_is_normalized(tmp_path, monkeypatch):
    a = al.Aliases.from_pairs(NASTY)
    root = _folder_with_registry(tmp_path, "<OASYSDNARegistry/>")
    real_open = Path.open

    def deny(self, *args, **kwargs):
        if self.suffix == ".xml":
            raise PermissionError(13, "Permission denied: C:\\Acme\\Widget", "C:\\Acme\\Widget")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    problems = "\n".join(read_installation(root, aliases=a).problems)
    assert "permission denied" in problems
    assert "Acme" not in problems and "Widget" not in problems and "Other" not in problems


def test_source_error_from_an_oserror_with_an_escaped_name_is_fixed_text(tmp_path, monkeypatch):
    from dmslog.ingest import pipeline

    folder = _folder(tmp_path)
    a = al.Aliases.from_pairs(NASTY)

    def boom(self, *args, **kwargs):
        raise OSError(5, "read failed for C:\\Acme\\Widget", "C:\\Acme\\Widget")

    monkeypatch.setattr(pipeline._Run, "_index", boom)
    events = []
    conn = create_case_db(tmp_path / "case.sqlite")
    result = run_ingest(conn, folder, IngestOptions(scope="all", aliases=a), events.append)
    assert result.sources_failed == 1
    stored = conn.execute("SELECT error FROM source WHERE status = 'error'").fetchone()[0]
    shown = [stored, *(d.member + (d.error or "") for d in result.details), *result.warnings]
    shown += [e.member or "" for e in events]
    assert "I/O error (errno 5)" in stored
    assert all("Acme" not in t and "Widget" not in t and "Other" not in t for t in shown), shown
    conn.close()


def test_cli_output_for_invalid_zip_and_failed_source_is_normalized(tmp_path, capsys):
    from dmslog.cli import main

    data = tmp_path / "data"
    data.mkdir()
    (data / "aliases.toml").write_text(
        'version = 1\n[[alias]]\npublic = "Custom"\nreal = "AcmeWidget"\n'
        '[[alias]]\npublic = "Other"\nreal = "Custom"\n',
        encoding="utf-8",
    )
    bad = tmp_path / "AcmeWidget_bad.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    main(["ingest", str(bad), "--data-dir", str(data)])
    out = capsys.readouterr()
    text = out.out + out.err
    assert "Acme" not in text and "Other" not in text
