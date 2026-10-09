from __future__ import annotations

from pathlib import Path

import pytest

from dmslog.knowledge.registry import (
    ConfigParam,
    RegistryError,
    RegistryLayer,
    parse_registry_filename,
    parse_registry_xml,
    resolve_registry,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "registry"
REG_DIR = FIXTURES / "install" / "SchneiderElectricData" / "OASyS" / "Servers" / "config" / "Registry"


def _layer(file: str, product: str, layer: int, label: str, **values: str) -> RegistryLayer:
    return RegistryLayer(product, file, layer, label, {("", k): v for k, v in values.items()})


def _rows(rows: list[ConfigParam], name: str, key_path: str = "") -> dict[int, ConfigParam]:
    return {r.layer: r for r in rows if r.name == name and r.key_path == key_path}


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("AdapterOnt.xml", ("AdapterOnt", -1, "")),
        ("AdapterOnt_2.xml", ("AdapterOnt", 2, "")),
        ("AdapterOnt_1_Custom.xml", ("AdapterOnt", 1, "Custom")),
        ("AdapterSiteSwitch_0_Product.xml", ("AdapterSiteSwitch", 0, "Product")),
        ("C:\\x\\AdapterOnt_3.xml", ("AdapterOnt", 3, "")),
    ],
)
def test_filename_parsing(filename: str, expected: tuple[str, int, str]) -> None:
    assert parse_registry_filename(filename) == expected


def test_parse_fixture_layer() -> None:
    layer = parse_registry_xml(REG_DIR / "AdapterExample_1_Example_Project.xml")
    assert (layer.product, layer.layer, layer.label) == ("AdapterExample", 1, "Example_Project")
    assert layer.values[("", "LogLevel")] == "Debug"
    assert layer.values[("ContactTypeIds", "")] == ("20",)


def test_product_grouped_by_attribute_not_filename() -> None:
    layer = parse_registry_xml(REG_DIR / "ExampleOther_1.xml")
    assert layer.product == "AdapterOther"


def test_single_layer_is_only() -> None:
    rows = resolve_registry([_layer("AdapterX.xml", "AdapterX", -1, "", A="1")])
    assert len(rows) == 1
    assert (rows[0].status, rows[0].effective) == ("only", True)


def test_overlap_across_four_layers() -> None:
    layers = [parse_registry_xml(p) for p in sorted(REG_DIR.glob("AdapterExample*.xml"))]
    rows = resolve_registry(layers)
    log_level = _rows(rows, "LogLevel")
    assert sorted(log_level) == [-1, 0, 1, 2]
    assert {n: r.status for n, r in log_level.items()} == {
        -1: "overridden",
        0: "overridden",
        1: "overridden",
        2: "wins",
    }
    assert [r.effective for _, r in sorted(log_level.items())] == [False, False, False, True]
    assert log_level[2].value == "Error"
    assert log_level[1].label == "Example_Project"
    # key present in a single layer stays 'only', even when the product has other layers
    assert _rows(rows, "ProjectOnly")[1].status == "only"
    assert _rows(rows, "FileUploadInterval")[2].value == "00:01:00"


def test_key_list_compared_as_whole_list() -> None:
    layers = [parse_registry_xml(p) for p in sorted(REG_DIR.glob("AdapterExample*.xml"))]
    rows = resolve_registry(layers)
    lists = _rows(rows, "", key_path="ContactTypeIds")
    assert lists[1].status == "wins" and lists[1].value == ("20",)
    assert lists[-1].status == "overridden" and lists[-1].value == ("10", "11")


def test_tie_same_layer_is_ambiguous_without_effective_value() -> None:
    layers = [parse_registry_xml(p) for p in sorted(REG_DIR.glob("AdapterTie*.xml"))]
    rows = resolve_registry(layers)
    mode = [r for r in rows if r.name == "Mode"]
    by_file = {r.source_file: r for r in mode}
    assert by_file["AdapterTie_1_ProjectA.xml"].status == "ambiguous"
    assert by_file["AdapterTie_1_ProjectB.xml"].status == "ambiguous"
    assert by_file["AdapterTie_0_Product.xml"].status == "overridden"
    assert not any(r.effective for r in mode)
    solo = [r for r in rows if r.name == "Solo"]
    assert [(r.status, r.effective) for r in solo] == [("only", True)]


def test_products_do_not_mix() -> None:
    rows = resolve_registry(
        [
            _layer("A.xml", "AdapterA", -1, "", K="a"),
            _layer("B.xml", "AdapterB", -1, "", K="b"),
        ]
    )
    assert [r.status for r in rows] == ["only", "only"]


def test_resolution_is_deterministic() -> None:
    layers = [parse_registry_xml(p) for p in sorted(REG_DIR.glob("*.xml")) if p.name != "Broken.xml"]
    assert resolve_registry(layers) == resolve_registry(list(reversed(layers)))


def test_bytes_input_uses_filename() -> None:
    data = (
        b'<OASYSDNARegistry><Product Name="AdapterB"><Value Name="K">v</Value></Product></OASYSDNARegistry>'
    )
    layer = parse_registry_xml(data, filename="AdapterB_2_Lbl.xml")
    assert (layer.layer, layer.label, layer.values[("", "K")]) == (2, "Lbl", "v")


@pytest.mark.parametrize("name", ["AdapterMalformed.xml", "AdapterWrongRoot.xml"])
def test_malformed_xml_raises_registry_error(name: str) -> None:
    with pytest.raises(RegistryError):
        parse_registry_xml(FIXTURES / "bad" / name)


def test_truncated_fixture_raises_registry_error() -> None:
    with pytest.raises(RegistryError):
        parse_registry_xml(REG_DIR / "Broken.xml")


def test_xxe_entity_is_rejected() -> None:
    with pytest.raises(RegistryError, match="DTD"):
        parse_registry_xml(FIXTURES / "bad" / "AdapterXxe.xml")


def test_oversized_file_rejected(tmp_path: Path) -> None:
    big = tmp_path / "AdapterBig.xml"
    big.write_bytes(b"<OASYSDNARegistry>" + b" " * 2048 + b"</OASYSDNARegistry>")
    with pytest.raises(RegistryError, match="limit"):
        parse_registry_xml(big, max_bytes=1024)
    with pytest.raises(RegistryError, match="limit"):
        parse_registry_xml(big.read_bytes(), filename="AdapterBig.xml", max_bytes=1024)


def test_missing_file_raises_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        parse_registry_xml(tmp_path / "AdapterNope.xml")


_ENTITY_DOC = (
    '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "expanded">]>'
    '<OASYSDNARegistry><Product Name="ExampleAdapter"><Value Name="A">&x;</Value></Product>'
    "</OASYSDNARegistry>"
)


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-16", "utf-32"])
@pytest.mark.parametrize("bom", [True, False])
def test_entity_declaration_rejected_in_any_encoding(codec: str, bom: bool) -> None:
    data = _ENTITY_DOC.encode(codec)
    if codec == "utf-16" and not bom:
        data = data[2:]  # 'utf-16' writes a BOM; strip it for the no-BOM case
    elif codec != "utf-16" and bom:
        data = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff", "utf-32": b"\xff\xfe\x00\x00"}[
            codec
        ] + data
    with pytest.raises(RegistryError):
        parse_registry_xml(data, filename="ExampleAdapter_2.xml")


def test_deeply_nested_keys_become_registry_error() -> None:
    depth = 1100
    doc = (
        '<OASYSDNARegistry><Product Name="ExampleAdapter">'
        + '<Key Name="k">' * depth
        + "</Key>" * depth
        + "</Product></OASYSDNARegistry>"
    )
    with pytest.raises(RegistryError):
        parse_registry_xml(doc.encode(), filename="ExampleAdapter_2.xml")
