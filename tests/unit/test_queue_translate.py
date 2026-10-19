"""Translation of a decoded message body to the wire XML (hand-built trees, synthetic values only)."""

from __future__ import annotations

import pytest

from dmslog.msgqueue import translate as tr
from dmslog.msgqueue.exemplar import learn_shape
from dmslog.msgqueue.nbfx import Node, Text

DC = "http://schemas.datacontract.org/2004/07/Test.Contracts"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ARR = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
MSG = "http://iec.ch/TC57/2011/WorksMessage"
HDR = "http://iec.ch/TC57/2011/schema/message"
BIZ = "http://example.test/biz"

CATALOG = tr.load_catalog()


def t(value, kind="chars"):
    return Text(value, kind)


def el(name, *children, xmlns=(), attrs=(), prefix=""):
    kids = tuple(Text(c, "chars") if isinstance(c, str) else c for c in children)
    return Node(name, prefix, tuple(xmlns), tuple(attrs), kids)


def nil(name):
    return el(name, attrs=(("i", "nil", "true"),))


def leaf(name, value, kind="chars"):
    return Node(name, "", (), (), (Text(value, kind),))


def message(*parts, verb="changed", noun="Works", clr="WorksEventMessageType", header=()):
    head = el("headerField", leaf("nounField", noun), leaf("verbField", verb), *header)
    return el(clr, head, *parts, xmlns=(("", DC), ("i", XSI)))


def run(decoded, shape=None):
    return tr.reconstruct(decoded, CATALOG, shape)


def names(node):
    return [c.name for c in node.children if isinstance(c, Node)]


def child(node, name):
    return next(c for c in node.children if isinstance(c, Node) and c.name == name)


def text(node):
    return "".join(c.value for c in node.children if isinstance(c, Text))


def work(*members):
    return message(el("payloadField", el("workField", *members)))


# -- root ---------------------------------------------------------------------------------------------


def test_root_is_capitalized_verb_plus_noun():
    rec = run(message())
    assert rec.root.name == "ChangedWorks"
    assert "root_guessed" not in rec.notes
    assert rec.basis == "catalog" and rec.exemplars == 0


def test_root_without_verb_or_noun_is_the_type_without_suffix():
    rec = run(el("WorksEventMessageType", el("payloadField")))
    assert rec.root.name == "WorksEventMessage"
    assert "root_guessed" in rec.notes


def test_unknown_type_uses_the_noun_namespace_and_says_so():
    rec = run(message(clr="OtherEventMessageType", noun="Others"))
    assert rec.basis == "none"
    assert rec.root.xmlns == (("", "http://iec.ch/TC57/2011/OthersMessage"),)
    assert "catalog_missing" in rec.notes


def test_catalog_namespace_and_no_datacontract_declarations():
    rec = run(message())
    assert rec.root.xmlns == (("", MSG),)
    xml = tr.serialize(rec.root)
    assert DC not in xml and XSI not in xml and "nil" not in xml


# -- members -----------------------------------------------------------------------------------------


def test_field_suffix_is_removed_and_items_keep_their_name():
    rec = run(work(el("stepsField", el("SwitchingStep", leaf("nameField", "n-1")))))
    steps = child(child(child(rec.root, "Payload"), "Work"), "steps")
    assert names(steps) == ["SwitchingStep"]
    assert names(steps.children[0]) == ["name"]


def test_specified_pairs_emit_only_when_true():
    rec = run(
        work(
            leaf("aField", "1"),
            leaf("aFieldSpecified", "true", "bool"),
            leaf("bField", "2"),
            leaf("bFieldSpecified", "false", "bool"),
            leaf("cField", "3"),
        )
    )
    w = child(child(rec.root, "Payload"), "Work")
    assert sorted(names(w)) == ["a", "c"]
    assert "Specified" not in tr.serialize(rec.root)


def test_nil_elements_are_omitted():
    rec = run(work(nil("aField"), leaf("bField", "x")))
    assert names(child(child(rec.root, "Payload"), "Work")) == ["b"]


def test_any_nil_is_dropped_and_non_nil_is_emitted_in_place():
    dropped = run(work(nil("anyField"), nil("anyAttrField"), leaf("bField", "x")))
    assert names(child(child(dropped.root, "Payload"), "Work")) == ["b"]
    assert "any_content" not in dropped.notes
    kept = run(work(el("anyField", leaf("Extra", "e-1")), leaf("bField", "x")))
    assert "any_content" in kept.notes
    assert "Extra" in names(child(child(kept.root, "Payload"), "Work"))


def test_empty_member_is_an_empty_element():
    rec = run(work(el("emptyField")))
    assert child(child(child(rec.root, "Payload"), "Work"), "empty").children == ()


# -- names (letter case) ------------------------------------------------------------------------------


def test_case_from_the_exemplar_wins():
    shape = learn_shape(
        ["<ChangedWorks><Payload><Work><Crew>c</Crew><mRID>m</mRID></Work></Payload></ChangedWorks>"]
    )
    rec = run(work(leaf("crewField", "c-1"), leaf("mRIDField", "m-1")), shape)
    w = child(child(rec.root, "Payload"), "Work")
    assert sorted(names(w)) == ["Crew", "mRID"]
    assert "names_guessed" not in rec.notes


def test_case_from_the_catalog_header_names():
    rec = run(message(header=(leaf("messageIDField", "id-1"),)))
    header = child(rec.root, "Header")
    assert "MessageID" in names(header)
    assert "names_guessed" not in rec.notes


def test_case_heuristic_capitalizes_known_cim_names_and_flags_the_rest():
    rec = run(work(leaf("crewField", "c"), leaf("zzzField", "z")))
    payload = child(rec.root, "Payload")
    assert names(payload) == ["Work"] or "work" in names(payload)
    assert "names_guessed" in rec.notes
    inner = payload.children[0]
    assert sorted(names(inner)) == ["Crew", "zzz"]


def test_known_name_with_matching_case_is_not_a_guess():
    rec = run(message(el("payloadField", el("WorkField", leaf("mRIDField", "m")))))
    assert "names_guessed" not in rec.notes


# -- arrays ------------------------------------------------------------------------------------------


def _steps():
    return el(
        "stepsField", el("SwitchingStep", leaf("nameField", "a")), el("SwitchingStep", leaf("nameField", "b"))
    )


def test_array_defaults_to_wrapped_and_says_so():
    rec = run(work(_steps()))
    steps = child(child(child(rec.root, "Payload"), "Work"), "steps")
    assert names(steps) == ["SwitchingStep", "SwitchingStep"]
    assert "array_form_guessed" in rec.notes


def test_array_form_from_exemplar_flat_and_wrapped():
    flat = learn_shape(["<R><Payload><Work><steps/><steps/></Work></Payload></R>"])
    rec = run(work(_steps()), flat)
    assert names(child(child(rec.root, "Payload"), "Work")) == ["steps", "steps"]
    wrapped = learn_shape(["<R><Payload><Work><steps><Step><name/></Step></steps></Work></Payload></R>"])
    rec = run(work(_steps()), wrapped)
    w = child(child(rec.root, "Payload"), "Work")
    assert names(w) == ["steps"] and names(w.children[0]) == ["Step", "Step"]
    assert "array_form_guessed" not in rec.notes


def test_header_property_is_flat_by_the_catalog():
    prop = el(
        "propertyField", el("Property", leaf("nameField", "k1")), el("Property", leaf("nameField", "k2"))
    )
    rec = run(message(header=(prop,)))
    header = child(rec.root, "Header")
    assert names(header).count("Property") == 2
    assert "array_form_guessed" not in rec.notes


def test_primitive_array_items_take_the_member_name_flat():
    arr = el(
        "tagsField",
        Node("string", "a", (), (), (Text("x", "chars"),)),
        Node("string", "a", (), (), (Text("y", "chars"),)),
        xmlns=(("a", ARR),),
    )
    rec = run(work(arr))
    w = child(child(rec.root, "Payload"), "Work")
    assert names(w) == ["tags", "tags"]
    assert [text(n) for n in w.children] == ["x", "y"]


# -- order -------------------------------------------------------------------------------------------


def test_order_from_the_exemplar_and_unknown_members_go_last():
    shape = learn_shape(["<R><Payload><Work><b/><a/></Work></Payload></R>"])
    rec = run(work(leaf("aField", "1"), leaf("bField", "2"), leaf("cField", "3")), shape)
    assert names(child(child(rec.root, "Payload"), "Work")) == ["b", "a", "c"]
    assert "order_partial" in rec.notes


def test_header_order_from_the_catalog():
    rec = run(
        message(header=(leaf("userField", "u"), leaf("timestampField", "2020-01-01T00:00:00Z", "datetime")))
    )
    assert names(child(rec.root, "Header")) == ["Verb", "Noun", "Timestamp", "User"]
    assert "order_alphabetical" not in rec.notes


def test_envelope_and_payload_content_order_from_the_catalog():
    payload = el(
        "payloadField",
        leaf("formatField", "f"),
        el("workField", leaf("aField", "1")),
        leaf("compressedField", "false", "bool"),
    )
    rec = run(message(payload))
    assert names(rec.root) == ["Header", "Payload"]
    assert names(child(rec.root, "Payload")) == ["Work", "Compressed", "Format"]


def test_without_any_order_information_it_is_alphabetical():
    rec = run(work(leaf("zField", "1"), leaf("aField", "2")))
    assert names(child(child(rec.root, "Payload"), "Work")) == ["a", "z"]
    assert "order_alphabetical" in rec.notes


# -- namespaces --------------------------------------------------------------------------------------


def test_namespaces_message_schema_and_unknown_payload():
    rec = run(work(leaf("aField", "1")))
    assert rec.root.xmlns == (("", MSG),)
    header = child(rec.root, "Header")
    assert header.xmlns == ()  # direct child: the message namespace
    assert child(header, "Verb").xmlns == (("", HDR),)
    payload = child(rec.root, "Payload")
    assert payload.xmlns == ()
    content = payload.children[0]
    assert content.xmlns == (("", ""),)
    assert "payload_namespace_unknown" in rec.notes


def test_namespaces_from_the_exemplar():
    shape = learn_shape([f'<R xmlns="{MSG}"><Payload><Work xmlns="{BIZ}"><a>x</a></Work></Payload></R>'])
    rec = run(work(leaf("aField", "1")), shape)
    content = child(child(rec.root, "Payload"), "Work")
    assert content.xmlns == (("", BIZ),)
    assert content.children[0].xmlns == ()
    assert "payload_namespace_unknown" not in rec.notes
    assert rec.basis == "exemplar" and rec.exemplars == 1


def test_payload_namespace_from_a_catalog_entry():
    entry = tr.MessageEntry("WorksEventMessageType", "Works", MSG, BIZ)
    catalog = tr.Catalog(**{**CATALOG.__dict__, "messages": (entry,)})
    rec = tr.reconstruct(work(leaf("aField", "1")), catalog, None)
    assert child(rec.root, "Payload").children[0].xmlns == (("", BIZ),)
    assert "payload_namespace_unknown" not in rec.notes


# -- values ------------------------------------------------------------------------------------------


def test_values_are_kept_and_datetime_local_is_flagged():
    rec = run(
        work(
            leaf("whenField", "2020-01-02T03:04:05", "datetime"),
            leaf("utcField", "2020-01-02T03:04:05Z", "datetime"),
        )
    )
    w = child(child(rec.root, "Payload"), "Work")
    assert text(child(w, "when")) == "2020-01-02T03:04:05"
    assert "datetime_local" in rec.notes
    only_utc = run(work(leaf("utcField", "2020-01-02T03:04:05Z", "datetime")))
    assert "datetime_local" not in only_utc.notes


def test_date_only_field_in_the_exemplar_cuts_the_time():
    shape = learn_shape(["<R><Payload><Work><day>2020-05-06</day></Work></Payload></R>"])
    rec = run(work(leaf("dayField", "2020-01-02T00:00:00", "datetime")), shape)
    assert text(child(child(child(rec.root, "Payload"), "Work"), "day")) == "2020-01-02"
    assert "datetime_format" in rec.notes


def test_generated_enum_values_are_flagged():
    rec = run(work(leaf("kindField", "Item3")))
    assert "enum_as_stored" in rec.notes
    assert "enum_as_stored" not in run(work(leaf("kindField", "plain"))).notes


def test_cataloged_enum_field_is_flagged():
    catalog = tr.Catalog(**{**CATALOG.__dict__, "enum_fields": frozenset({"kind"})})
    assert "enum_as_stored" in tr.reconstruct(work(leaf("kindField", "plain")), catalog, None).notes


def test_exemplar_attributes_become_attributes():
    shape = learn_shape(['<R><Payload><Work id="x"><name>n</name></Work></Payload></R>'])
    rec = run(work(leaf("idField", "w-9"), leaf("nameField", "n-9")), shape)
    w = child(child(rec.root, "Payload"), "Work")
    assert w.attrs == (("", "id", "w-9"),)
    assert names(w) == ["name"]


def test_catalog_attributes_without_exemplar_are_flagged():
    entry = tr.MessageEntry("WorksEventMessageType", "Works", MSG, None, ("id",))
    catalog = tr.Catalog(**{**CATALOG.__dict__, "messages": (entry,)})
    assert "attributes_unknown" in tr.reconstruct(work(leaf("aField", "1")), catalog, None).notes


def test_invalid_xml_characters_are_replaced():
    rec = run(work(leaf("aField", "x\x01y")))
    assert "invalid_chars" in rec.notes
    assert "\x01" not in tr.serialize(rec.root)


def test_too_deep_input_is_cut():
    node = leaf("xField", "v")
    for _ in range(tr.MAX_DEPTH + 5):
        node = el("nField", node)
    rec = run(work(node))
    assert "too_deep" in rec.notes


# -- output ------------------------------------------------------------------------------------------


def test_serialize_escapes_and_format_payload_accepts_it():
    rec = run(work(leaf("aField", 'a<b>&"c'), leaf("bField", "x")))
    xml = tr.serialize(rec.root)
    assert 'a&lt;b&gt;&amp;"c' in xml
    result = tr.format_reconstruction(rec)
    assert result.ok and result.kind == "xml" and result.reason is None
    assert "\n" in result.text


def test_full_message_goes_through_format_payload_ok():
    shape = learn_shape(
        [
            f'<ChangedWorks xmlns="{MSG}"><Header xmlns="{HDR}"><Verb>v</Verb><Noun>n</Noun></Header>'
            f'<Payload><Work xmlns="{BIZ}" id="1"><name>x</name></Work></Payload></ChangedWorks>'
        ]
    )
    rec = run(work(leaf("idField", "w-1"), leaf("nameField", "n-1")), shape)
    result = tr.format_reconstruction(rec)
    assert result.ok
    assert 'id="w-1"' in result.text and "<name>n-1</name>" in result.text
    assert rec.notes == tuple(sorted(rec.notes))
    assert set(rec.notes) <= set(tr.NOTE_CODES)


def test_serialize_prefixes_attributes_and_empty_elements():
    node = Node(
        "a",
        "p",
        (("p", "http://x/"),),
        (("q", "k", 'v"1'),),
        (Node("b", "", (), (), ()), Text("t&", "chars")),
    )
    assert tr.serialize(node) == '<p:a xmlns:p="http://x/" q:k="v&quot;1"><b/>t&amp;</p:a>'


def test_load_catalog_rejects_other_schemas(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text("schema = 2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tr.load_catalog(bad)


def test_bundled_catalog_has_the_initial_entries_and_only_names():
    types = {m.type for m in CATALOG.messages}
    assert {
        "WorksEventMessageType",
        "SwitchingStepsEventMessageType",
        "SwitchingPlansEventMessageType",
        "IncidentsEventMessageType",
        "IncidentTroubleTicketsEventMessageType",
    } <= types
    assert CATALOG.header_order[0] == "Verb" and "Property" in CATALOG.flat_arrays
    assert all(m.namespace.startswith("http://iec.ch/TC57/2011/") for m in CATALOG.messages)
