"""NBFX decoder: records, value texts, arrays, limits, every error code and truncation (issue #74)."""

from __future__ import annotations

import decimal
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import nbfx_writer as w  # noqa: E402

from dmslog.msgqueue import nbfx  # noqa: E402
from dmslog.msgqueue.nbfx import Limits, NbfxError, Node, Text, decode, to_xml  # noqa: E402

START_A = b"\x40" + w.string("a")  # <a>
END = b"\x01"


def body(*records: bytes) -> bytes:
    """A document whose root is <a> and whose content is the given records, then </a>."""
    return w.doc(START_A, *records, END)


def error_of(blob: bytes, limits: Limits | None = None) -> NbfxError:
    with pytest.raises(NbfxError) as info:
        decode(blob, limits or Limits())
    return info.value


def only_text(blob: bytes) -> Text:
    root = decode(blob)
    assert len(root.children) == 1 and isinstance(root.children[0], Text)
    return root.children[0]


def value_of(val: w.Val) -> Text:
    return only_text(body(w.text_record(val)))


# --- structure ------------------------------------------------------------------------------------------


def test_message_shape_round_trips():
    for n in (1, 2, 3):
        message = w.make_message(n, nil_payload=n == 2)
        assert decode(w.encode(message)) == w.expected_tree(message)


def test_message_shape_to_xml():
    root = decode(w.encode(w.make_message(7)))
    xml = to_xml(root)
    assert xml.startswith(f'<WorksEventMessageType xmlns="{w.DATACONTRACT_NS}" xmlns:i="{w.XSI_NS}">')
    assert "<verbField>verb-7</verbField>" in xml
    assert f"<messageId>{w.guid(7)}</messageId>" in xml
    assert "<value>7</value><value>8</value><value>9</value>" in xml
    assert xml.endswith("</WorksEventMessageType>")
    assert "<?xml" not in xml


def test_nil_payload_attribute_and_empty_element():
    xml = to_xml(decode(w.encode(w.make_message(2, nil_payload=True))))
    assert '<payloadField i:nil="true"/>' in xml


def test_header_tail_is_ignored():
    blob = w.encode(w.E("a"), header=w.HEADER_SIGNATURE + b"\xde\xad\xbe\xef")
    assert decode(blob) == Node("a", "", (), (), ())


def test_element_prefix_forms():
    root = w.E(
        "r",
        children=(
            w.E("x", prefix="p"),  # PrefixElementP
            w.E("y", prefix="q", long_form=True),  # Element with a one-letter prefix
            w.E("z", prefix="longprefix"),  # Element
            w.E("e", children=(w.Val("chars", "t"),), fuse=False),  # text then a separate EndElement
        ),
    )
    tree = decode(w.encode(root))
    assert tree == w.expected_tree(root)
    assert [c.prefix for c in tree.children] == ["p", "q", "longprefix", ""]


def test_attribute_forms_and_xmlns():
    root = w.E(
        "r",
        xmlns=(("", "urn:default"), ("p", "urn:p")),
        attrs=(
            ("", "short", w.Val("chars", "1")),
            ("k", "letter", w.Val("int32", 5)),
            ("ns", "long", w.Val("true")),
        ),
    )
    tree = decode(w.encode(root))
    assert tree.xmlns == (("", "urn:default"), ("p", "urn:p"))
    assert tree.attrs == (("", "short", "1"), ("k", "letter", "5"), ("ns", "long", "true"))
    assert to_xml(tree) == '<r xmlns="urn:default" xmlns:p="urn:p" short="1" k:letter="5" ns:long="true"/>'


def test_comments_are_dropped_and_do_not_end_the_start_tag():
    blob = w.doc(START_A, b"\x02" + w.string("note"), b"\x04" + w.string("x") + b"\x82", b"\x86", END)
    tree = decode(blob)
    assert tree.attrs == (("", "x", "1"),)
    assert tree.children == (Text("true", "bool"),)


def test_fused_text_closes_the_element():
    blob = w.doc(START_A, b"\x40" + w.string("b") + b"\x99" + bytes([1]) + b"x", END)
    tree = decode(blob)
    assert tree.children[0] == Node("b", "", (), (), (Text("x", "chars"),))


def test_text_directly_in_root_with_end():
    assert decode(w.doc(START_A, w.text_record(w.Val("chars", "v"), end=True))).children == (
        Text("v", "chars"),
    )


# --- value texts ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("val", "text", "kind"),
    [
        (w.Val("zero"), "0", "int"),
        (w.Val("one"), "1", "int"),
        (w.Val("false"), "false", "bool"),
        (w.Val("true"), "true", "bool"),
        (w.Val("empty"), "", "empty"),
        (w.Val("int8", -5), "-5", "int"),
        (w.Val("int16", -300), "-300", "int"),
        (w.Val("int32", 2**31 - 1), "2147483647", "int"),
        (w.Val("int64", -(2**63)), "-9223372036854775808", "int"),
        (w.Val("uint64", 2**64 - 1), "18446744073709551615", "int"),
        (w.Val("bool", True), "true", "bool"),
        (w.Val("bool", False), "false", "bool"),
        (w.Val("float", 0.5), "0.5", "float"),
        (w.Val("float", 0.1), "0.1", "float"),
        (w.Val("float", float("inf")), "INF", "float"),
        (w.Val("float", float("-inf")), "-INF", "float"),
        (w.Val("float", float("nan")), "NaN", "float"),
        (w.Val("double", 0.1), "0.1", "double"),
        (w.Val("double", -2.5e-10), "-2.5e-10", "double"),
        (w.Val("double", float("inf")), "INF", "double"),
        (w.Val("double", float("-inf")), "-INF", "double"),
        (w.Val("double", float("nan")), "NaN", "double"),
        (w.Val("decimal", decimal.Decimal("12.340")), "12.340", "decimal"),
        (w.Val("decimal", decimal.Decimal("-0.05")), "-0.05", "decimal"),
        (w.Val("decimal", decimal.Decimal("0")), "0", "decimal"),
        (w.Val("decimal", decimal.Decimal(2**96 - 1)), str(2**96 - 1), "decimal"),
        (
            w.Val("decimal", decimal.Decimal("0.0000000000000000000000000001")),
            "0.0000000000000000000000000001",
            "decimal",
        ),
        (w.Val("datetime", (0, 0)), "0001-01-01T00:00:00", "datetime"),
        (w.Val("datetime", (0, 1)), "0001-01-01T00:00:00Z", "datetime"),
        (
            w.Val("datetime", (637_134_336_000_000_000 + 1_234_567, 1)),
            "2020-01-01T00:00:00.1234567Z",
            "datetime",
        ),
        (w.Val("datetime", (637_134_336_000_000_000 + 5_000_000, 2)), "2020-01-01T00:00:00.5", "datetime"),
        (w.Val("datetime", (637_134_336_000_000_000 + 10_000_000, 3)), "2020-01-01T00:00:01", "datetime"),
        (w.Val("datetime", (3_155_378_975_999_999_999, 1)), "9999-12-31T23:59:59.9999999Z", "datetime"),
        (w.Val("timespan", 0), "PT0S", "timespan"),
        (w.Val("timespan", 90_061 * 10_000_000), "P1DT1H1M1S", "timespan"),
        (w.Val("timespan", -15_000_000), "-PT1.5S", "timespan"),
        (w.Val("timespan", 2 * 864_000_000_000), "P2D", "timespan"),
        (w.Val("timespan", 600_000_000), "PT1M", "timespan"),
        (w.Val("timespan", 36_000_000_000 + 1), "PT1H0.0000001S", "timespan"),
        (w.Val("timespan", 864_000_000_000 + 10_000_000), "P1DT1S", "timespan"),
        (w.Val("guid", w.guid(5)), "00000000-0000-0000-0000-000000000005", "guid"),
        (w.Val("uniqueid", w.guid(0xABCDEF)), "00000000-0000-0000-0000-000000abcdef", "uniqueid"),
        (w.Val("chars", "text-1"), "text-1", "chars"),
        (w.Val("chars", "", width=16), "", "chars"),
        (w.Val("chars", "caf\u00e9", width=32), "caf\u00e9", "chars"),
        (w.Val("chars", "x" * 300), "x" * 300, "chars"),  # picks the 16-bit length
        (w.Val("unicode", "na\u00efve \U0001f600"), "na\u00efve \U0001f600", "chars"),
        (w.Val("unicode", "u16", width=16), "u16", "chars"),
        (w.Val("unicode", "u32", width=32), "u32", "chars"),
        (w.Val("bytes", b"\x00\x01\xfe"), "AAH+", "bytes"),
        (w.Val("bytes", b"", width=16), "", "bytes"),
        (w.Val("bytes", b"abcd", width=32), "YWJjZA==", "bytes"),
        (w.Val("list", [w.Val("int32", 1), w.Val("chars", "ab"), w.Val("true")]), "1 ab true", "chars"),
        (w.Val("list", []), "", "chars"),
    ],
)
def test_text_values(val, text, kind):
    result = value_of(val)
    assert result == Text(text, kind)
    assert w.expected_text(val) == result  # the independent oracle agrees


def test_list_followed_by_end_element_record():
    blob = w.doc(START_A, w.text_record(w.Val("list", [w.Val("one")]), end=True))
    assert decode(blob).children == (Text("1", "chars"),)


def test_float32_uses_shortest_text():
    assert value_of(w.Val("float", 3.4028234663852886e38)).value == "3.4028235e+38"
    assert value_of(w.Val("float", 1.0)).value == "1.0"


def test_utf8_text_keeps_other_scripts_and_controls():
    assert value_of(w.Val("chars", "\u3042\x01")).value == "\u3042\x01"


# --- arrays ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "values"),
    [
        ("int16", [-1, 0, 7]),
        ("int32", [1, 2, 3]),
        ("int64", [2**40, -(2**40)]),
        ("float", [0.5, 1.5]),
        ("double", [0.1, -3.0]),
        ("decimal", [decimal.Decimal("1.5"), decimal.Decimal("-2")]),
        ("datetime", [(637_134_336_000_000_000, 1), (0, 0)]),
        ("timespan", [0, 10_000_000]),
        ("guid", [w.guid(1), w.guid(2)]),
        ("bool", [True, False, True]),
        ("int32", []),
    ],
)
def test_arrays(kind, values):
    element = w.E("r", children=(w.ArrayOf("v", kind, tuple(values)),))
    tree = decode(w.encode(element))
    assert tree == w.expected_tree(element)
    assert [c.name for c in tree.children] == ["v"] * len(values)


def test_array_with_prefix_and_attributes():
    element = w.E(
        "r",
        children=(
            w.ArrayOf(
                "v",
                "int16",
                (1, 2),
                prefix="p",
                xmlns=(("p", "urn:p"),),
                attrs=(("", "unit", w.Val("chars", "mm")),),
            ),
        ),
    )
    tree = decode(w.encode(element))
    assert tree == w.expected_tree(element)
    assert to_xml(tree) == (
        '<r><p:v xmlns:p="urn:p" unit="mm">1</p:v><p:v xmlns:p="urn:p" unit="mm">2</p:v></r>'
    )


def test_array_after_text_keeps_order():
    blob = w.doc(START_A, b"\x82", w._array_bytes(w.ArrayOf("v", "bool", (True,))), END)
    kinds = [type(c).__name__ for c in decode(blob).children]
    assert kinds == ["Text", "Node"]


# --- error codes ----------------------------------------------------------------------------------------


def test_bad_header():
    assert error_of(b"\x01\x02\x03").code == "bad_header"
    assert error_of(b"\x00\x05" + b"\x00" * 20).code == "bad_header"
    assert error_of(w.HEADER_SIGNATURE + b"\x00" * 4 + b"\x82").code == "bad_header"  # not an element
    err = error_of(w.HEADER_SIGNATURE + b"\x00" * 4 + b"\x82")
    assert err.offset == 12


def test_truncated_header():
    assert error_of(b"").code == "truncated"
    assert error_of(w.HEADER_SIGNATURE[:3]).code == "truncated"
    assert error_of(w.HEADER[:11]).code == "truncated"
    assert error_of(w.HEADER).code == "truncated"  # no root record at all


@pytest.mark.parametrize("rec", [0x00, 0xBE, 0xFF, 0xC0])
def test_unknown_record(rec):
    err = error_of(body(bytes([rec])))
    assert (err.code, err.offset) == ("unknown_record", 12 + len(START_A))


def test_unknown_record_cases():
    # attribute after content
    assert error_of(body(b"\x82", b"\x04" + w.string("x") + b"\x82")).code == "unknown_record"
    # attribute value that is not a text record / is an end-element variant
    assert error_of(w.doc(START_A, b"\x04" + w.string("x") + b"\x40")).code == "unknown_record"
    assert error_of(w.doc(START_A, b"\x04" + w.string("x") + b"\x83")).code == "unknown_record"
    # EndListText alone, and a nested/odd item in a list
    assert error_of(body(b"\xa6")).code == "unknown_record"
    assert error_of(body(b"\xa4\xa4")).code == "unknown_record"
    assert error_of(body(b"\xa4\x83")).code == "unknown_record"
    assert error_of(body(b"\xa4\x40")).code == "unknown_record"
    # array: not an element, bad type, attribute-less junk between element and EndElement
    assert error_of(body(b"\x03\x82")).code == "unknown_record"
    assert error_of(body(b"\x03" + b"\x40" + w.string("v") + b"\x01\x98")).code == "unknown_record"
    assert error_of(body(b"\x03" + b"\x40" + w.string("v") + b"\x82")).code == "unknown_record"


DICTIONARY = [0x06, 0x07, 0x0A, 0x0B, 0x0C, 0x25, 0x42, 0x43, 0x44, 0x5D, 0xAA, 0xAB, 0xBC, 0xBD]


@pytest.mark.parametrize("rec", DICTIONARY)
def test_dictionary_record_in_content_and_root(rec):
    err = error_of(body(bytes([rec]) + b"\x00\x00"))
    assert (err.code, err.offset) == ("dictionary_record", 12 + len(START_A))
    assert error_of(w.HEADER + bytes([rec]) + b"\x00").code == "dictionary_record"


def test_dictionary_record_in_attribute_array_and_list():
    assert error_of(w.doc(START_A, b"\x04" + w.string("x") + b"\xaa\x00")).code == "dictionary_record"
    assert error_of(body(b"\x03\x42\x00")).code == "dictionary_record"
    assert error_of(body(b"\x03" + b"\x40" + w.string("v") + b"\x06\x00")).code == "dictionary_record"
    assert error_of(body(b"\x03" + b"\x40" + w.string("v") + b"\x01\xaa")).code == "dictionary_record"
    assert error_of(body(b"\xa4\xaa\x00")).code == "dictionary_record"
    assert error_of(body(b"\xaa\x00")).code == "dictionary_record"


def test_bad_length():
    too_long = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x7F])  # five bytes, value above 2^31 - 1
    assert error_of(w.HEADER + b"\x40" + too_long).code == "bad_length"
    six = bytes([0x80, 0x80, 0x80, 0x80, 0x80, 0x01])  # continuation on the fifth byte
    err = error_of(w.HEADER + b"\x40" + six)
    assert (err.code, err.offset) == ("bad_length", 13)
    assert error_of(body(b"\x9c" + struct.pack("<i", -1))).code == "bad_length"  # negative Chars32
    assert error_of(body(b"\xb6\x03abc")).code == "bad_length"  # odd UTF-16 byte count
    scale29 = struct.pack("<HBBIQ", 0, 29, 0, 0, 1)
    assert error_of(body(b"\x94" + scale29)).code == "bad_length"
    assert error_of(body(b"\x96" + struct.pack("<Q", 3_155_378_975_999_999_999 + 1))).code == "bad_length"
    arr = b"\x03" + b"\x40" + w.string("v") + b"\x01\x97" + w.mbi31(1) + struct.pack("<Q", 2**62 - 1)
    assert error_of(body(arr)).code == "bad_length"
    arr_dec = b"\x03" + b"\x40" + w.string("v") + b"\x01\x95" + w.mbi31(1) + scale29
    assert error_of(body(arr_dec)).code == "bad_length"


@pytest.mark.parametrize("name", ["1abc", "", "a b", "a<b", "-x", "a:b"])
def test_bad_name(name):
    err = error_of(w.HEADER + b"\x40" + w.string(name))
    assert (err.code, err.offset) == ("bad_name", 13)


def test_bad_name_in_prefix_and_attribute():
    assert error_of(w.HEADER + b"\x41" + w.string("1p") + w.string("a")).code == "bad_name"
    assert error_of(w.doc(START_A, b"\x04" + w.string("9x") + b"\x82")).code == "bad_name"
    assert error_of(w.doc(START_A, b"\x05" + w.string("p q") + w.string("x") + b"\x82")).code == "bad_name"


def test_bad_utf8():
    assert error_of(w.HEADER + b"\x40\x02\xc3\x28").code == "bad_utf8"  # invalid name bytes
    assert error_of(body(b"\x98\x02\xff\xfe")).code == "bad_utf8"  # Chars8
    assert error_of(body(b"\xb6\x02\x00\xd8")).code == "bad_utf8"  # lone UTF-16 surrogate
    assert error_of(w.doc(START_A, b"\x09" + w.string("p") + b"\x02\xff\xfe")).code == "bad_utf8"  # xmlns URI


def test_trailing_data_and_unbalanced():
    complete = w.encode(w.E("a"))
    err = error_of(complete + b"\x82")
    assert (err.code, err.offset) == ("trailing_data", len(complete))
    assert error_of(complete + b"\x00\x00").code == "trailing_data"
    err = error_of(complete + b"\x01")
    assert (err.code, err.offset) == ("unbalanced", len(complete))


def test_error_message_never_carries_blob_content():
    marker = "SECRETMARKER"
    blobs = [
        w.HEADER + b"\x40" + w.string("1" + marker),  # bad_name
        w.doc(START_A, b"\x98" + bytes([len(marker) + 2]) + marker.encode() + b"\xff\xfe", END),  # bad_utf8
        w.doc(START_A, b"\x98" + bytes([len(marker)]) + marker.encode()),  # truncated (no EndElement)
        w.encode(w.E(marker)) + marker.encode(),  # trailing_data
    ]
    for blob in blobs:
        err = error_of(blob)
        assert marker not in str(err) and marker not in repr(err.args)
        assert err.code in nbfx.ERROR_CODES
        assert isinstance(err.offset, int)


def test_error_codes_are_documented():
    assert set(nbfx.ERROR_CODES) == {
        "bad_header", "truncated", "unknown_record", "dictionary_record", "bad_length", "bad_name",
        "bad_utf8", "too_large", "too_deep", "too_many_nodes", "text_too_large", "unbalanced",
        "trailing_data",
    }  # fmt: skip
    assert issubclass(NbfxError, ValueError)


# --- truncation at every point --------------------------------------------------------------------------


def _rich_element() -> w.E:
    return w.E(
        "Root",
        prefix="ns",
        xmlns=(("", "urn:x"), ("i", "urn:i"), ("ns", "urn:ns")),
        attrs=(("i", "nil", w.Val("true")), ("", "id", w.Val("chars", "text-1"))),
        children=(
            w.Comment("note"),
            w.E("l", children=(w.Val("list", [w.Val("int32", 1), w.Val("chars", "ab")]),)),
            w.ArrayOf("arr", "decimal", (decimal.Decimal("1.50"), decimal.Decimal("-3"))),
            w.E("u", children=(w.Val("unicode", "u\u00e9"),)),
            w.E("b", children=(w.Val("bytes", b"\x01\x02"), w.Val("guid", w.guid(9)))),
            w.E("t", children=(w.Val("datetime", (637_134_336_000_000_000, 1)),)),
            w.Val("chars", "tail", width=32),
        ),
    )


def test_every_truncation_is_reported_as_truncated():
    for element in (_rich_element(), w.make_message(3)):
        blob = w.encode(element)
        assert decode(blob) == w.expected_tree(element)
        for cut in range(len(blob)):
            err = error_of(blob[:cut])
            assert err.code == "truncated", (cut, err.code)
            assert 0 <= err.offset <= cut


# --- limits ---------------------------------------------------------------------------------------------


def _nested(depth: int) -> bytes:
    return w.doc(*([START_A] * depth), *([END] * depth))


def test_too_large():
    blob = w.encode(w.E("a", children=(w.Val("chars", "x" * 100),)))
    assert error_of(blob, Limits(max_bytes=len(blob) - 1)).code == "too_large"
    assert decode(blob, Limits(max_bytes=len(blob))).name == "a"


def test_too_deep_and_deep_documents_do_not_recurse():
    assert decode(_nested(10), Limits(max_depth=10)).name == "a"
    assert error_of(_nested(11), Limits(max_depth=10)).code == "too_deep"
    deep = decode(_nested(256))  # default limit; the decoder is iterative
    depth = 1
    while deep.children:
        deep = deep.children[0]
        depth += 1
    assert depth == 256
    assert error_of(_nested(257)).code == "too_deep"


def test_array_depth_counts():
    arr = w._array_bytes(w.ArrayOf("v", "bool", (True,)))
    assert decode(w.doc(START_A, arr, END), Limits(max_depth=2)).name == "a"
    assert error_of(w.doc(START_A, arr, END), Limits(max_depth=1)).code == "too_deep"


def test_too_many_nodes():
    blob = w.encode(w.E("a", children=tuple(w.E("b") for _ in range(10))))
    assert decode(blob, Limits(max_nodes=11)).name == "a"
    assert error_of(blob, Limits(max_nodes=10)).code == "too_many_nodes"
    texts = w.encode(w.E("a", children=tuple(w.Val("one") for _ in range(10))))
    assert error_of(texts, Limits(max_nodes=5)).code == "too_many_nodes"
    items = w.encode(w.E("a", children=(w.Val("list", [w.Val("one")] * 20),)))
    assert error_of(items, Limits(max_nodes=8)).code == "too_many_nodes"


def test_attribute_count_limits():
    element = w.E("a", attrs=tuple(("", f"n{i}", w.Val("one")) for i in range(5)))
    assert decode(w.encode(element), Limits(max_attrs=5)).name == "a"
    assert error_of(w.encode(element), Limits(max_attrs=4)).code == "too_many_nodes"
    declared = w.E("a", xmlns=tuple((f"p{i}", "urn:x") for i in range(5)))
    assert error_of(w.encode(declared), Limits(max_attrs=4)).code == "too_many_nodes"


def test_array_count_is_checked_before_allocation():
    huge = b"\x03\x40" + w.string("v") + b"\x01\x8b" + w.mbi31(2**31 - 1)
    err = error_of(body(huge))
    assert err.code == "truncated"  # more values announced than bytes available
    many = w.encode(w.E("a", children=(w.ArrayOf("v", "bool", (True,) * 50),)))
    assert error_of(many, Limits(max_nodes=60)).code == "too_many_nodes"
    assert decode(many, Limits(max_nodes=101)).name == "a"
    with_attr = w.encode(
        w.E("a", children=(w.ArrayOf("v", "bool", (True,) * 5, attrs=(("", "x", w.Val("one")),)),))
    )
    assert error_of(with_attr, Limits(max_nodes=10)).code == "too_many_nodes"
    assert decode(with_attr, Limits(max_nodes=16)).name == "a"


def test_text_too_large():
    text = w.encode(w.E("a", children=(w.Val("chars", "x" * 11),)))
    assert error_of(text, Limits(max_text=10)).code == "text_too_large"
    assert decode(text, Limits(max_text=11)).name == "a"
    assert error_of(w.HEADER + b"\x40" + w.string("n" * 11), Limits(max_text=10)).code == "text_too_large"
    assert error_of(body(b"\x02" + w.string("c" * 11)), Limits(max_text=10)).code == "text_too_large"


def test_declared_huge_length_fails_without_reading():
    err = error_of(body(b"\x9c" + struct.pack("<I", 2**31 - 1)))
    assert err.code == "text_too_large"
    err = error_of(w.HEADER + b"\x40" + w.mbi31(2**31 - 1))
    assert err.code == "text_too_large"


def test_total_text_limit():
    blob = w.encode(w.E("a", children=tuple(w.E("b", children=(w.Val("chars", "xxxx"),)) for _ in range(5))))
    assert decode(blob, Limits(max_total_text=26)).name == "a"
    assert error_of(blob, Limits(max_total_text=15)).code == "text_too_large"
    arr = w.encode(w.E("a", children=(w.ArrayOf("v", "int32", (1000,) * 10),)))
    assert error_of(arr, Limits(max_total_text=10)).code == "text_too_large"


# --- to_xml ---------------------------------------------------------------------------------------------


def test_to_xml_escapes_text_and_attributes():
    node = Node(
        "r",
        "",
        (("", 'u"<&>'),),
        (("", "a", 'q"<&>\t\n\r'),),
        (Text('a&b<c>d"e\r', "chars"), Node("e", "p", (), (), ())),
    )
    assert to_xml(node) == (
        '<r xmlns="u&quot;&lt;&amp;&gt;" a="q&quot;&lt;&amp;&gt;&#9;&#10;&#13;">'
        "a&amp;b&lt;c&gt;d&quot;e&#13;<p:e/></r>"
    )


def test_to_xml_replaces_forbidden_characters():
    node = Node("r", "", (), (("", "a", "x\x00y"),), (Text("a\x01b\ufffe\x0bc\ud800", "chars"),))
    assert to_xml(node) == '<r a="x\ufffdy">a\ufffdb\ufffd\ufffdc\ufffd</r>'


def test_to_xml_output_is_well_formed():
    from xml.etree import ElementTree

    xml = to_xml(decode(w.encode(_rich_element())))
    ElementTree.fromstring(xml)


def test_to_xml_limit_and_deep_tree():
    node = Node("a", "", (), (), (Text("x" * 100, "chars"),))
    assert len(to_xml(node, max_chars=len("<a>" + "x" * 100 + "</a>"))) > 100
    err = pytest.raises(NbfxError, to_xml, node, max_chars=50).value
    assert err.code == "too_large"
    deep = Node("z", "", (), (), ())
    for _ in range(5000):  # far beyond the recursion limit
        deep = Node("n", "", (), (), (deep,))
    xml = to_xml(deep)
    assert xml.count("<n>") == 5000 and xml.endswith("<z/>" + "</n>" * 5000)


def test_text_kinds_are_declared():
    for val in (w.Val("float", 1.0), w.Val("uniqueid", w.guid(1)), w.Val("empty")):
        assert value_of(val).kind in nbfx.TEXT_KINDS


def test_array_hand_written_vector():
    # <a><v>1</v><v>-2</v></a>: Array, ShortElement "v", EndElement, Int16TextWithEndElement (0x8B),
    # count 2, then two little-endian int16 values.
    blob = bytes.fromhex("000400000106000100000000") + bytes.fromhex(
        "40 01 61 03 40 01 76 01 8b 02 0100 feff 01".replace(" ", "")
    )
    tree = decode(blob)
    assert tree.children == (
        Node("v", "", (), (), (Text("1", "int"),)),
        Node("v", "", (), (), (Text("-2", "int"),)),
    )


def test_array_even_value_type_is_rejected():
    blob = w.doc(START_A, b"\x03\x40" + w.string("v") + b"\x01\x8a" + w.mbi31(0), END)
    assert error_of(blob).code == "unknown_record"


def test_list_and_base64_respect_max_text_on_the_resulting_text():
    limits = Limits(max_text=8)
    items = [w.Val("chars", "abcd")] * 3  # each item fits, the joined text is 14 characters
    assert error_of(body(w.text_record(w.Val("list", items))), limits).code == "text_too_large"
    ok = [w.Val("chars", "abcd")] * 2  # 9 characters with the separator: still above 8
    assert error_of(body(w.text_record(w.Val("list", ok))), limits).code == "text_too_large"
    assert decode(body(w.text_record(w.Val("list", [w.Val("chars", "abcd")]))), limits).name == "a"
    raw = w.Val("bytes", b"123456")  # 6 bytes -> 8 characters of base64: fits
    assert decode(body(w.text_record(raw)), limits).children == (Text("MTIzNDU2", "bytes"),)
    assert error_of(body(w.text_record(w.Val("bytes", b"1234567"))), limits).code == "text_too_large"


def test_a_long_valid_list_is_not_counted_twice():
    # 5,000 one-digit integers: 9,999 characters joined, far below the default limits; each counts once.
    items = [w.Val("int8", 7)] * 5000
    tree = decode(body(w.text_record(w.Val("list", items))))
    assert len(tree.children[0].value) == 9999
    # The running total grows by the text plus one separator per item, not by the joined length every time.
    limits = Limits(max_total_text=10_000)
    assert decode(body(w.text_record(w.Val("list", items))), limits).children[0].value.count(" ") == 4999
    assert (
        error_of(body(w.text_record(w.Val("list", items))), Limits(max_total_text=9_998)).code
        == "text_too_large"
    )


def test_constant_texts_count_in_the_total_and_in_lists():
    # A list of "false" constants: each one is text and counts once, like any other item.
    items = [w.Val("false")] * 3
    assert decode(body(w.text_record(w.Val("list", items)))).children[0].value == "false false false"
    assert (
        error_of(body(w.text_record(w.Val("list", items))), Limits(max_total_text=1)).code == "text_too_large"
    )
    assert error_of(body(w.text_record(w.Val("true"))), Limits(max_total_text=3)).code == "text_too_large"
