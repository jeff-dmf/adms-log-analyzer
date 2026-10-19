"""Exemplar learning: shape (names, order, namespaces) from logged payloads, never values."""

from __future__ import annotations

from dmslog.msgqueue import exemplar as ex
from dmslog.payload.pretty import tokenize

MSG = "http://iec.ch/TC57/2011/WorksMessage"
HDR = "http://iec.ch/TC57/2011/schema/message"
BIZ = "http://example.test/biz"

SAMPLE = (
    f'<ChangedWorks xmlns="{MSG}"><Header xmlns="{HDR}"><Verb>changed</Verb><Noun>Works</Noun>'
    "<Property><Name>k1</Name><Value>v1</Value></Property>"
    "<Property><Name>k2</Name><Value>v2</Value></Property>"
    f'</Header><Payload><Works xmlns="{BIZ}"><Work id="w-1"><mRID>m-1</mRID><Crew>c-1</Crew>'
    "<Day>2020-01-02</Day><Steps><Step>s1</Step><Step>s2</Step></Steps></Work></Works></Payload></ChangedWorks>"
)


def test_tokenize_is_the_public_wrapper():
    tokens, status, pos = tokenize("<a><b/></a>")
    assert status == "ok" and pos == 11
    assert [t[0] for t in tokens] == ["o", "s", "c"]


def test_learn_shape_names_order_namespaces():
    shape = ex.learn_shape([SAMPLE])
    assert shape is not None and shape.count == 1
    assert shape.get(()).children == ("Header", "Payload")
    assert shape.get(()).namespace == MSG
    assert shape.get(("Header",)).namespace == HDR
    assert shape.get(("Header",)).children == ("Verb", "Noun", "Property")
    assert shape.get(("Payload", "Works")).namespace == BIZ
    assert shape.get(("Payload", "Works", "Work")).children == ("mRID", "Crew", "Day", "Steps")


def test_learn_shape_arrays_attrs_dates():
    shape = ex.learn_shape([SAMPLE])
    assert shape.get(("Header",)).flat == frozenset({"Property"})
    work = ("Payload", "Works", "Work")
    assert shape.get((*work, "Steps")).item == "Step"
    assert shape.get((*work, "Steps")).flat == frozenset({"Step"})
    assert shape.get(work).attrs == ("id",)
    assert shape.get((*work, "Day")).date_only is True
    assert shape.get((*work, "Crew")).date_only is False


def test_shape_holds_no_values():
    blob = repr(ex.learn_shape([SAMPLE]))
    for value in ("m-1", "c-1", "k1", "v2", "w-1", "2020-01-02", "s1"):
        assert value not in blob


def test_prefixed_names_resolve_namespaces():
    text = f'<p:Root xmlns:p="{MSG}" xmlns:q="{BIZ}"><q:Item q:flag="1"/></p:Root>'
    shape = ex.learn_shape([text])
    assert shape.get(()).namespace == MSG
    assert shape.get(("Item",)).namespace == BIZ
    assert shape.get(("Item",)).attrs == ("flag",)


def test_merge_keeps_stable_order_across_exemplars():
    shape = ex.learn_shape(["<R><a/><c/></R>", "<R><a/><b/><c/><d/></R>"])
    assert shape.count == 2
    assert shape.get(()).children == ("a", "b", "c", "d")


def test_merge_order_helper_inserts_after_its_predecessor():
    base = ["x", "y"]
    ex._merge_order(base, ["a", "x", "b", "y"])
    assert base == ["a", "x", "b", "y"]


def test_date_only_requires_every_observation():
    shape = ex.learn_shape(["<R><d>2020-01-02</d></R>", "<R><d>2020-01-02T01:02:03</d></R>"])
    assert shape.get(("d",)).date_only is False


def test_truncated_and_hostile_text_never_raises():
    shape = ex.learn_shape(["<R><a><b>x", "not xml at all", "<R><a></zzz>", ""])
    assert shape is not None and shape.count >= 1
    assert shape.get(("a",)) is not None
    assert ex.learn_shape(["", "plain text"]) is None


def test_only_the_first_root_of_a_sequence_counts():
    shape = ex.learn_shape(["<R><a/></R><S><b/></S>"])
    assert shape.get(("b",)) is None and shape.get(("a",)) is not None


def test_entities_stay_literal():
    shape = ex.learn_shape(['<!DOCTYPE r [<!ENTITY e "x">]><R xmlns="http://u/&e;"><a>&e;</a></R>'])
    assert shape.get(()).namespace == "http://u/&e;"


def _meta(id_, root, well_formed=True):
    return {"id": id_, "root": root, "well_formed": well_formed}


def test_select_exemplar_ids_prefers_exact_root_and_newest(monkeypatch):
    rows = [_meta(i, "ChangedWorks") for i in range(1, 9)]
    rows += [_meta(20, "CreatedWorks"), _meta(21, "ChangedWorks", False)]
    seen = {}

    def fake_list(conn, flt, limit, after):
        seen["flt"] = flt
        return rows, None

    monkeypatch.setattr(ex, "list_payloads", fake_list)
    assert ex.select_exemplar_ids(None, "ChangedWorks", "Works") == [8, 7, 6, 5, 4]
    flt = seen["flt"]
    assert (flt.roles, flt.directions, flt.formats) == (("request",), ("out",), ("xml",))


def test_select_exemplar_ids_falls_back_to_the_noun(monkeypatch):
    rows = [_meta(1, "CreatedWorks"), _meta(2, "ns:DeletedWorks"), _meta(3, "Other")]
    monkeypatch.setattr(ex, "list_payloads", lambda *a: (rows, None))
    assert ex.select_exemplar_ids(None, "ChangedWorks", "Works") == [2, 1]
    assert ex.select_exemplar_ids(None, "ChangedWorks", None) == []


def test_select_pages_through_the_cursor(monkeypatch):
    pages = {None: ([_meta(1, "R")], "5:1"), "5:1": ([_meta(2, "R")], None)}

    def fake_list(conn, flt, limit, after):
        return pages[None if after is None else str(after)]

    monkeypatch.setattr(ex, "list_payloads", fake_list)
    assert ex.select_exemplar_ids(None, "R") == [2, 1]


def test_select_exemplar_texts_skips_missing(monkeypatch):
    monkeypatch.setattr(ex, "select_exemplar_ids", lambda *a: [1, 2, 3])
    got = {1: {"text": "<a/>"}, 2: None, 3: {"text": ""}}
    monkeypatch.setattr(ex, "get_payload", lambda conn, pid, cap: got[pid])
    assert ex.select_exemplar_texts(None, "R") == ["<a/>"]


def test_cache_hits_and_lru_eviction():
    cache = ex.ShapeCache(2)
    calls = []

    def loader(tag):
        def _load():
            calls.append(tag)

        return _load

    cache.get_or_load(("c", "R", 1), loader("a"))
    cache.get_or_load(("c", "R", 1), loader("a2"))  # hit, even for a cached None
    cache.get_or_load(("c", "R", 2), loader("b"))
    cache.get_or_load(("c", "R", 1), loader("a3"))  # refreshes 1
    cache.get_or_load(("c", "R", 3), loader("c"))  # evicts 2
    cache.get_or_load(("c", "R", 2), loader("b2"))
    assert calls == ["a", "b", "c", "b2"]
    assert len(cache) == 2


def test_load_shape_uses_the_generation_in_the_key(monkeypatch):
    monkeypatch.setattr(ex, "SHAPE_CACHE", ex.ShapeCache())
    monkeypatch.setattr(ex, "select_exemplar_texts", lambda *a: ["<R><a/></R>"])
    s1 = ex.load_shape(None, "case", "R", 1)
    assert ex.load_shape(None, "case", "R", 1) is s1
    assert ex.load_shape(None, "case", "R", 2) is not s1


def test_deep_nesting_is_bounded_and_the_rest_still_learned():
    depth = 5000
    text = "<R><ok/>" + "<a>" * depth + "</a>" * depth + "<after/></R>"
    shape = ex.learn_shape([text])
    assert shape.get(()).children == ("ok", "a", "after")
    assert max(len(p) for p in shape.elements) <= ex.MAX_DEPTH
    assert len(shape.elements) <= ex.MAX_PATHS


def test_paths_and_children_are_capped():
    wide = "<R>" + "".join(f"<c{i}/>" for i in range(ex.MAX_CHILDREN + 50)) + "</R>"
    shape = ex.learn_shape([wide])
    assert len(shape.get(()).children) == ex.MAX_CHILDREN
    many = "<R>" + "".join(f"<p{i}><x/></p{i}>" for i in range(ex.MAX_PATHS)) + "</R>"
    assert len(ex.learn_shape([many]).elements) <= ex.MAX_PATHS
