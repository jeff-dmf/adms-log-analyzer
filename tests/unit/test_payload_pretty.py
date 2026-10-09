"""Tests for the tolerant payload formatter (all inputs are synthetic)."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from hypothesis import given, settings
from hypothesis import strategies as st

from dmslog.payload.pretty import MAX_DEPTH, format_payload


def fmt(text: str, kind: str = "xml", **kw):
    return format_payload(text, kind, **kw)


def test_wfm_style_flat_input_is_indented():
    src = (
        '<ChangedCrews xmlns="urn:x"><Crew id="1"><Name>Crew A</Name>'
        "<Members><M>a</M><M>b</M></Members></Crew></ChangedCrews>"
    )
    r = fmt(src)
    assert r.ok and r.kind == "xml" and r.reason is None and not r.truncated_display
    assert r.text.splitlines() == [
        '<ChangedCrews xmlns="urn:x">',
        '  <Crew id="1">',
        "    <Name>Crew A</Name>",
        "    <Members>",
        "      <M>a</M>",
        "      <M>b</M>",
        "    </Members>",
        "  </Crew>",
        "</ChangedCrews>",
    ]


def test_prefixes_namespaces_and_both_quote_kinds():
    src = "<ns0:Msg xmlns:ns0='urn:a' xmlns:ns1=\"urn:b\"><ns1:Item a='x\"y' b=\"p'q\"/></ns0:Msg>"
    r = fmt(src)
    assert r.ok
    assert r.text.splitlines() == [
        "<ns0:Msg xmlns:ns0='urn:a' xmlns:ns1=\"urn:b\">",
        "  <ns1:Item a='x\"y' b=\"p'q\"/>",
        "</ns0:Msg>",
    ]


def test_already_indented_input_is_stable_and_crlf_normalized():
    src = "<a>\r\n  <b>1</b>\r\n  <c/>\r\n</a>"
    r = fmt(src)
    assert r.text == "<a>\n  <b>1</b>\n  <c/>\n</a>"
    assert fmt(r.text).text == r.text


def test_xml_seq_without_root():
    r = fmt('<R id="1"><x/></R><R id="2"/>\n<R id="3">t</R>')
    assert r.ok and r.kind == "xml_seq" and r.reason is None
    assert r.text.splitlines() == [
        '<R id="1">',
        "  <x/>",
        "</R>",
        '<R id="2"/>',
        '<R id="3">t</R>',
    ]


def test_multiline_element_text_preserved():
    src = "<I><comment>line one\nline two\n\nline four</comment><n>1</n></I>"
    r = fmt(src)
    assert "<comment>line one\nline two\n\nline four</comment>" in r.text
    assert r.ok


def test_entities_kept_literal():
    src = '<a v="x&#xD;&#xA;y">Tom &amp; Jerry &#233; &lt;b&gt;</a>'
    r = fmt(src)
    assert r.ok
    assert r.text == src


def test_comment_cdata_pi_kept():
    src = '<?xml version="1.0"?><a><!-- c --><![CDATA[ <raw> & ]]><b/></a>'
    r = fmt(src)
    assert r.ok
    assert r.text.splitlines() == [
        '<?xml version="1.0"?>',
        "<a>",
        "  <!-- c -->",
        "  <![CDATA[ <raw> & ]]>",
        "  <b/>",
        "</a>",
    ]


def test_truncated_closes_open_elements_for_display():
    r = fmt("<a><b><c>text</c><d>par")
    assert r.ok and r.reason == "truncated"
    assert r.text.splitlines() == [
        "<a>",
        "  <b>",
        "    <c>text</c>",
        "    <d>",
        "      par",
        "    </d>",
        "  </b>",
        "</a>",
    ]


def test_truncated_inside_tag_and_quote():
    for src in ('<a><b x="1', "<a><b x", "<a><!-- never", "<a><b"):
        r = fmt(src)
        assert r.reason == "truncated", src
        assert r.text.splitlines()[-1] == "</a>"


def test_malformed_stray_lt_keeps_prefix_then_raw():
    r = fmt("<a><b>1</b> x < y <c/></a>")
    assert not r.ok and r.reason == "malformed"
    assert r.text.startswith("<a>\n  <b>1</b>\n")
    assert r.text.endswith("< y <c/></a>")


def test_malformed_unclosed_attribute_quote():
    r = fmt('<a><b x="1><c/></b></a>')
    assert not r.ok and r.reason == "malformed"
    assert r.text.endswith('<b x="1><c/></b></a>')
    assert r.text.startswith("<a>")


def test_malformed_mismatched_close_and_plain_text():
    r = fmt("<a><b></a></b>")
    assert not r.ok and r.reason == "malformed"
    assert r.text.endswith("</a></b>")
    r = fmt("just some words")
    assert not r.ok and r.reason == "malformed" and r.text == "just some words"


def test_empty_input():
    r = fmt("  \n")
    assert not r.ok and r.reason == "empty"


def test_doctype_is_literal_and_never_expanded():
    src = (
        '<!DOCTYPE r [<!ENTITY x SYSTEM "http://127.0.0.1:9/evil">'
        '<!ENTITY y "boom>boom">]><r a="&x;">&x;&y;</r>'
    )
    r = fmt(src)
    assert r.ok
    assert r.text.splitlines()[0].startswith("<!DOCTYPE r [")
    assert r.text.splitlines()[0].endswith("]>")
    assert '<r a="&x;">&x;&y;</r>' in r.text
    assert "boom>boom" in r.text  # still literal


def test_script_is_literal_in_html_and_xml():
    html = "<html><script>alert(1)</script></html>"
    r = fmt(html, "html")
    assert r.text == html and not r.ok and r.kind == "html" and r.reason == "html_raw"
    r = fmt("<a><script>if (a < b) {}</script></a>")
    assert "<script>" not in r.text or r.reason == "malformed"


def test_json_pretty_and_malformed():
    r = fmt('{"b":[1,2,{"z":null}],"a":"\u00e9"}', "json")
    assert r.ok and r.kind == "json"
    assert r.text == '{\n  "b": [\n    1,\n    2,\n    {\n      "z": null\n    }\n  ],\n  "a": "\u00e9"\n}'
    r = fmt('{"a": ', "json")
    assert not r.ok and r.reason == "malformed" and r.text == '{"a": '
    r = fmt("[" * 100_000, "json")
    assert not r.ok  # no RecursionError


def test_text_and_unknown_unchanged():
    assert fmt("a\r\nb", "text").text == "a\r\nb"
    r = fmt("<a/>", "weird")
    assert r.text == "<a/>" and not r.ok and r.kind == "text"


def test_too_large_returns_head():
    r = fmt("<a>" + "x" * 100 + "</a>", max_chars=50)
    assert not r.ok and r.reason == "too_large" and r.truncated_display
    assert len(r.text) == 50
    r = fmt("x" * 100, "text", max_chars=10)
    assert r.truncated_display and len(r.text) == 10


def test_output_budget_for_deep_wide_input():
    s = "<a>" * 500 + "<b/>" * 20_000
    r = fmt(s, max_chars=len(s))
    # indentation would multiply the size far beyond the budget
    assert r.reason == "too_large" and r.truncated_display


def test_one_megabyte_time_bound():
    unit = "<Item id='1'><Name>some text &amp; more</Name><V a=\"2\"/></Item>"
    big = "<Root>" + unit * (990_000 // len(unit)) + "</Root>"
    small = "<Root>" + unit * (100_000 // len(unit)) + "</Root>"
    t0 = time.perf_counter()
    fmt(small)
    t_small = time.perf_counter() - t0
    t0 = time.perf_counter()
    r = fmt(big)
    t_big = time.perf_counter() - t0
    assert r.ok
    # linear: 10x the input must not cost anywhere near 100x
    assert t_big < max(t_small, 0.005) * 40
    assert t_big < 10


def test_hostile_inputs_are_linear():
    for s in ("<a " * 100_000, "<" * 200_000, '<a x="' * 50_000, "<!--" * 50_000, "<a>" * 200_000):
        t0 = time.perf_counter()
        fmt(s)
        assert time.perf_counter() - t0 < 5


def test_100k_deep_nesting_no_recursion_error():
    r = fmt("<a>" * 100_000 + "</a>" * 100_000)
    assert not r.ok and r.reason == "too_deep"


def test_depth_cap_boundary():
    ok = "<a>" * MAX_DEPTH + "</a>" * MAX_DEPTH
    assert fmt(ok).ok
    too = "<a>" * (MAX_DEPTH + 1) + "</a>" * (MAX_DEPTH + 1)
    assert fmt(too).reason == "too_deep"


# --- properties -------------------------------------------------------------

_NAME = st.sampled_from(["a", "b", "ns:c", "Item", "x_1"])
_TEXT = st.text(alphabet="ab c\n\t&;#xDA1\u00e9.-", max_size=12)


def _lit(t: str) -> str:
    # keep '&' only as valid literal entities so the document stays well-formed
    return t.replace("&", "&amp;")


@st.composite
def _docs(draw, depth=0):
    name = draw(_NAME)
    attr = draw(st.sampled_from(["", ' k="v"', " k='v w'", ' k="a&amp;b"']))
    if depth >= 3 or draw(st.booleans()):
        txt = _lit(draw(_TEXT))
        if not txt and draw(st.booleans()):
            return f"<{name}{attr}/>"
        return f"<{name}{attr}>{txt}</{name}>"
    parts = []
    for _ in range(draw(st.integers(1, 3))):
        if draw(st.booleans()):
            parts.append(_lit(draw(_TEXT)))
        parts.append(draw(_docs(depth + 1)))
    return f"<{name}{attr}>{''.join(parts)}</{name}>"


def _content(xml: str) -> str:
    root = ET.fromstring(xml.replace("ns:c", "c"))
    return "".join("".join(root.itertext()).split())


@settings(max_examples=200, deadline=None)
@given(_docs())
def test_formatting_only_changes_whitespace_between_tags(doc):
    r = fmt(doc)
    assert r.ok and r.reason is None
    assert _content(r.text) == _content(doc)
    # tags and non-whitespace content survive exactly
    assert "".join(r.text.split()) == "".join(doc.split())
    assert fmt(r.text).text == r.text  # idempotent


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300), st.sampled_from(["xml", "xml_seq", "html", "json", "text", "?"]))
def test_never_raises_on_arbitrary_text(text, kind):
    r = fmt(text, kind)
    assert isinstance(r.text, str) and isinstance(r.ok, bool)


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet="<>/!?-[]\"'= &;abCDATA\n", max_size=200))
def test_never_raises_on_markup_soup(text):
    r = fmt(text)
    assert isinstance(r.text, str)


def _timed(s: str) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        fmt(s, max_chars=10_000_000)
        best = min(best, time.perf_counter() - t0)
    return best


def test_hostile_inputs_grow_linearly():
    shapes = {
        "long_name": lambda n: "<" + "a" * n,
        "many_attrs": lambda n: "<a " + "k='v' " * (n // 6),
        "bare_lt": lambda n: "<" * n,
        "comment": lambda n: "<!--" + "x" * n,
        "cdata": lambda n: "<a><![CDATA[" + "x" * n,
        "pi": lambda n: "<?" + "x" * n,
        "long_name_nested": lambda n: "<a>" + "<" + "b" * n,
    }
    for name, make in shapes.items():
        small, big = _timed(make(20_000)), _timed(make(160_000))
        assert big <= max(small, 0.002) * 20, name


def test_budget_counts_synthetic_closers_and_tail():
    # Deep nesting + truncation: closers and the raw tail must count too.
    src = "<abcdefghij>" * 500 + "<tail x='" + "y" * 100
    r = fmt(src)
    assert len(r.text) <= 5 * 1_000_000
    for max_chars in (1_000, 5_000, 20_000):
        r = fmt(src, max_chars=max_chars)
        assert len(r.text) <= max(5 * max_chars, max_chars)
        if r.reason == "too_large":
            assert r.truncated_display and not r.ok and len(r.text) <= max_chars
    # Reproduces the reported shape: small input, huge indented output.
    src = "<a>" * 512 + "<b/>" * 1_200 + "<c"
    r = fmt(src, max_chars=len(src))
    assert len(r.text) <= 5 * len(src) or (r.reason == "too_large" and r.truncated_display)
