"""Ad hoc extraction over records (ROADMAP 9.8): scan order, values, summary, leaf, ids, promotion."""

from __future__ import annotations

import tomllib

import pytest
from extract_helpers import build, locator

from dmslog.parse.templates import load_texts
from dmslog.store import query as Q
from dmslog.store.distinct import distinct, parse_field
from dmslog.store.extract import (
    ExtractError,
    _parts_sql,
    as_json,
    extract,
    extract_values,
    extractor,
    leaf_sql,
    parse_request,
    promote,
)
from dmslog.store.filters import FilterError, compile_bucket_filter, compile_filter

RECORDS = [
    (2, 3000, "Callback received: GID: 17, Errors count: 0.", None),
    (2, 1000, "Callback received: GID: 5, Errors count: 2.", None),
    (2, 2000, "  Callback received: GID: 5, Errors count: 1.\t", None),
    (1, 2000, "Callback received: GID: 9, Errors count: 0.", None),  # X.log.0: before X.log at the same ts
    (0, 2000, "Callback received: GID: 11, Errors count: 0.", None),  # X.log.1: before X.log.0
    (3, 2000, "Callback received: GID: 12, Errors count: 0.", None),  # sensitive half: after enterprise
    (4, None, "plain line GID: 99", "more\nGID: 100"),  # no time: first of all
    (4, 4000, 'Sending works. Request with ID "AbC-1".', "<Body>\n  <Code>E42</Code>\n</Body>"),
    (4, 5000, "unrelated", None),
]
GID = r"GID: (?P<gid>\d+)"


@pytest.fixture
def conn(tmp_path):
    c = build(tmp_path / "case.sqlite", RECORDS)
    yield c
    c.close()


def _order(conn, result):
    return [locator(conn, r["id"]) for r in result.rows]


def test_rows_follow_the_stable_order_and_carry_their_values(conn):
    result = extract(conn, extractor(GID), None)
    assert result.scanned == 9 and result.matched == 7 and result.cut == 0 and not result.truncated
    assert [r["values"] for r in result.rows] == [["99"], ["5"], ["11"], ["9"], ["5"], ["12"], ["17"]]
    assert _order(conn, result) == [
        ("D:/logs", "Y.log", 1),
        ("C:/ers/A.zip", "Log/X.log", 2),
        ("C:/ers/A.zip", "Log/X.log.1", 1),
        ("C:/ers/A.zip", "Log/X.log.0", 1),
        ("C:/ers/A.zip", "Log/X.log", 3),
        ("C:/ers/A_s.zip", "Log/X.log", 1),
        ("C:/ers/A.zip", "Log/X.log", 1),
    ]


def test_the_result_does_not_depend_on_the_ids(tmp_path, conn):
    """Same records, other insertion orders of collections and sources (other ids, other interleaving)."""
    other = build(
        tmp_path / "other.sqlite", RECORDS, source_order=[4, 3, 2, 1, 0], collection_order=[2, 1, 0]
    )
    for limit in (1, 3, 5, 9):
        a = extract(conn, extractor(GID, types={"gid": "int"}), None, limit=limit)
        b = extract(other, extractor(GID, types={"gid": "int"}), None, limit=limit)
        assert _order(conn, a) == _order(other, b)
        assert [r["values"] for r in a.rows] == [r["values"] for r in b.rows]
        ja, jb = as_json(a), as_json(b)
        for j in (ja, jb):
            j.pop("rows")
        assert ja == jb
    other.close()


def test_limit_and_truncation(conn):
    result = extract(conn, extractor(GID), None, limit=3)
    assert result.scanned == 3 and result.truncated
    assert [r["values"] for r in result.rows] == [["99"], ["5"], ["11"]]
    assert not extract(conn, extractor(GID), None, limit=9).truncated


def test_filter_and_trimmed_first_line(conn):
    ex = extractor(r"^Callback received: GID: (?P<gid>\d+)")
    result = extract(conn, ex, {"source": [3]})
    assert [r["values"] for r in result.rows] == [["5"], ["5"], ["17"]]  # the third has spaces around it


def test_target_text_reads_the_continuation(conn):
    ex = extractor(r"<Code>(?P<code>[^<]+)<", "text")
    result = extract(conn, ex, None)
    assert [r["values"] for r in result.rows] == [["E42"]]
    ex = extractor(r"\nGID: (?P<gid>\d+)", "text")
    assert [r["values"] for r in extract(conn, ex, None).rows] == [["100"]]


def test_columns_summary_types_and_statistics(conn):
    result = extract(conn, extractor(GID, types={"gid": "int"}), None)
    (col,) = result.columns
    assert col["name"] == "gid" and col["type"] == "int" and col["suggested_type"] == "int"
    assert col["present"] == 7 and col["distinct"] == 7  # 6 values and "no value"
    assert col["values"][0] == {
        "value": None,
        "count": 2,
        "pct": 22.22,
        "leaf": {"extract": {"regex": GID, "target": "msg", "group": "gid", "type": "int", "eq": None}},
    }
    assert [(v["value"], v["count"]) for v in col["values"][1:3]] == [(5, 2), (9, 1)]
    assert col["numeric"] == {
        "type": "int", "count": 7, "min": 5, "max": 99, "sum": 158, "avg": 158 / 7, "p50": 11, "p90": 99,
        "p99": 99,
    }  # fmt: skip
    text_col = extract(conn, extractor(GID), None, top=2).columns[0]
    assert text_col["type"] == "str" and text_col["numeric"]["type"] == "int"  # suggested
    assert len(text_col["values"]) == 2 and text_col["others"] == {"count": 5, "groups": 5}
    words = extract(conn, extractor(r"(?P<w>[A-Z][a-z]+)"), None).columns[0]
    assert words["suggested_type"] == "str" and words["numeric"] is None


def test_every_value_leaf_selects_its_records(conn):
    for ex in (
        extractor(GID),
        extractor(GID, types={"gid": "int"}),
        extractor(r"<Code>(?P<c>[^<]+)<", "text"),
    ):
        result = extract(conn, ex, None)
        for v in result.columns[0]["values"]:
            n = Q.count(conn, v["leaf"]).n
            assert n == v["count"], v


def test_the_leaf_in_page_count_and_distinct(conn):
    leaf = {"extract": {"regex": GID, "group": "gid", "eq": "5"}}
    cf = compile_filter(leaf)
    assert cf.uses_regex and compile_bucket_filter(leaf) is None
    assert Q.count(conn, leaf).n == 2
    page = Q.page(conn, leaf, None)
    assert len(page.rows) == 2
    res = distinct(conn, {"and": [leaf, {"source": [3]}]}, parse_field("source"))
    assert [(v["value"], v["count"]) for v in res.values] == [(3, 2)]
    assert Q.count(conn, {"not": leaf}).n == 7  # two-valued: the complement


def test_leaf_validation(conn):
    bad = [
        ({"regex": GID, "group": "gid"}, "bad_type", "$.extract"),
        ({"regex": GID, "group": "gid", "eq": "5", "x": 1}, "bad_type", "$.extract"),
        ({"regex": "(?P<a>.*)x", "group": "a", "eq": None}, "bad_regex", "$.extract.regex"),
        ({"regex": GID, "group": "nope", "eq": None}, "bad_value", "$.extract.group"),
        ({"regex": GID, "group": "gid", "eq": None, "type": "list"}, "bad_value", "$.extract.type"),
        ({"regex": GID, "group": "gid", "eq": None, "target": "body"}, "bad_value", "$.extract.target"),
        ({"regex": GID, "group": "gid", "eq": True}, "bad_type", "$.extract.eq"),
        ({"regex": GID, "group": "gid", "eq": [1]}, "bad_type", "$.extract.eq"),
        ({"regex": GID, "group": "gid", "eq": 2**70}, "bad_value", "$.extract.eq"),
        ({"regex": GID, "group": "gid", "eq": "x" * 2000}, "string_too_long", "$.extract.eq"),
        ({"regex": 5, "group": "gid", "eq": None}, "bad_regex", "$.extract.regex"),
    ]
    for arg, code, path in bad:
        with pytest.raises(FilterError) as info:
            compile_filter({"extract": arg})
        assert (info.value.code, info.value.path) == (code, path), arg
    with pytest.raises(FilterError) as info:
        leaf_sql({"regex": GID, "group": "gid", "eq": float("nan")}, "$")
    assert info.value.code == "bad_value"
    sql, params = leaf_sql({"regex": GID, "group": "gid", "eq": "5"}, "$")
    assert "GID" not in sql and params[-1] == "5"  # the regex is a parameter


def test_values_by_id(conn):
    ex = extractor(GID, types={"gid": "int"})
    values = extract_values(conn, ex, list(range(1, 12)))
    got = {locator(conn, k): v["values"] for k, v in values.items()}
    assert got == {
        ("C:/ers/A.zip", "Log/X.log.1", 1): [11], ("C:/ers/A.zip", "Log/X.log.0", 1): [9],
        ("C:/ers/A.zip", "Log/X.log", 1): [17], ("C:/ers/A.zip", "Log/X.log", 2): [5],
        ("C:/ers/A.zip", "Log/X.log", 3): [5], ("C:/ers/A_s.zip", "Log/X.log", 1): [12],
        ("D:/logs", "Y.log", 1): [99],
    }  # fmt: skip
    assert len(values) == 7 and all(v["cut"] is False for v in values.values())
    assert extract_values(conn, ex, []) == {}
    with pytest.raises(ValueError):
        extract_values(conn, ex, list(range(1001)))


def test_cut_records(tmp_path):
    long_msg = "x" * 5000 + " GID: 1"
    c = build(tmp_path / "c.sqlite", [(0, 1, "GID: 2 " + "y" * 5000, None), (0, 2, long_msg, None)])
    result = extract(c, extractor(GID), None)
    assert result.cut == 2 and [r["values"] for r in result.rows] == [["2"]] and result.rows[0]["cut"]
    c.close()


def test_sql_parts_equal_the_reference_parts(tmp_path):
    msgs = ["  a\tb \t ", "\t\t", "", "\u00e9  ", "x" * 5000, " " * 3000 + "y" + " " * 3000, "a\u00a0 "]
    c = build(tmp_path / "p.sqlite", [(0, k, m, "c" * 70000 if k % 2 else None) for k, m in enumerate(msgs)])
    for target in ("msg", "text"):
        for source in (r"(?P<a>[ab])", r"^(?P<a>.+)"):
            ex = extractor(source, target)
            parts, params = _parts_sql(ex)
            sql = f"SELECT e.msg, b.cont, {parts} FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id"
            for msg, cont, part_msg, part_cont in c.execute(sql, params):
                assert (part_msg, part_cont) == ex.parts(msg, cont)
    c.close()


def test_timeouts_raise_and_never_give_a_partial_answer(conn):
    with pytest.raises(Q.QueryTimeout):
        extract(conn, extractor(GID), None, timeout_s=0)


def test_request_validation():
    ok = {"case": "c", "regex": GID}
    assert parse_request(ok)[2:] == (1000, 20)
    bad = [
        ({"regex": ""}, "bad_regex", "$.regex"),
        ({"regex": "(?P<a>x"}, "bad_regex", "$.regex"),
        ({"regex": GID, "target": "x"}, "bad_target", "$.target"),
        ({"regex": GID, "types": {"gid": "list"}}, "bad_types", "$.types"),
        ({"regex": GID, "types": {"zz": "int"}}, "bad_types", "$.types"),
        ({"regex": GID, "types": []}, "bad_types", "$.types"),
        ({"regex": GID, "limit": 0}, "bad_limit", "$.limit"),
        ({"regex": GID, "limit": 10001}, "bad_limit", "$.limit"),
        ({"regex": GID, "limit": True}, "bad_limit", "$.limit"),
        ({"regex": GID, "top": 101}, "bad_top", "$.top"),
        ({"regex": GID, "filter": []}, "bad_type", "$.filter"),
        ({"regex": GID, "nope": 1}, "unknown_member", "$.nope"),
        ({"regex": "(?P<a>a+)" + "".join(chr(0x100 + i) + "?" for i in range(200)) + "z"}, "regex_too_costly",
         "$.regex"),
    ]  # fmt: skip
    for body, code, path in bad:
        with pytest.raises(ExtractError) as info:
            parse_request(body)
        assert (info.value.code, info.value.path) == (code, path), body
    with pytest.raises(ExtractError) as info:
        parse_request({"regex": "ab(?P<a>.*)x"})
    assert "offset 8" in str(info.value)


# --------------------------------------------------------------------------- promotion


def _ids(conn, n):
    return [r[0] for r in conn.execute("SELECT id FROM entry ORDER BY id LIMIT ?", (n,))]


def test_promote_builds_a_template_the_loader_accepts(conn):
    ids = _ids(conn, 9)
    out = promote(conn, {"ids": ids, "regex": r"GID: (?P<gid>\d+), Errors count: (?P<errors>\d+)"})
    assert out["check"] == {"loads": True, "error": None}
    tpl = out["template"]
    assert tpl["family"] == "AWFM" and tpl["code"].startswith("WFM-9")
    assert tpl["regex"] == r"^Callback received: GID: (?P<gid>[0-9]+), Errors count: (?P<errors>[0-9]+)"
    assert tpl["fields"] == {"gid": "int", "errors": "int"}
    assert len(tpl["examples"]) == 3 and tpl["examples"][0]["fields"] == {"gid": 5, "errors": 2}
    assert out["file"] == "src/dmslog/adapters/awfm.toml"
    doc = tomllib.loads(out["toml"])
    assert doc["template"][0]["regex"] == tpl["regex"]
    # the text drops into a family file as is
    catalog = load_texts({"x.toml": 'schema = 1\nfamily = "AWFM"\n\n' + out["toml"]}, check_examples=True)
    assert catalog.template(tpl["code"]).field_types == {"gid": "int", "errors": "int"}
    assert out["warnings"] == []
    assert [r["matched"] for r in out["records"]] == [False, True, True, True, True, True, True, False, False]


def test_promote_with_a_varying_lead_and_explicit_family_and_code(conn):
    ids = _ids(conn, 9)
    out = promote(conn, {"ids": ids, "regex": GID, "family": "AONT", "code": "ONT-990"})
    assert out["template"]["regex"].startswith("^.*?GID")
    assert [w["code"] for w in out["warnings"]] == ["lead_varies"]
    assert out["template"]["code"] == "ONT-990" and out["template"]["family"] == "AONT"
    assert isinstance(out["check"]["loads"], bool)


def test_promote_errors(conn, tmp_path):
    ids = _ids(conn, 9)
    for body, code in (
        ({"ids": ids, "regex": "(?P<z>zzz)"}, "promote_no_match"),
        ({"ids": ids, "regex": GID}, "mixed_families"),
        ({"ids": [], "regex": GID}, "bad_ids"),
        ({"ids": list(range(21)), "regex": GID}, "bad_ids"),
        ({"ids": ids, "regex": GID, "family": "a b"}, "bad_family"),
        ({"ids": ids, "regex": GID, "code": "lower-1"}, "bad_code"),
        ({"ids": ids, "regex": GID, "target": "text"}, "promote_needs_msg"),
        ({"ids": ids, "regex": GID, "limit": 5}, "unknown_member"),
    ):
        with pytest.raises(ExtractError) as info:
            promote(conn, body)
        assert info.value.code == code, body


def test_promote_warns_about_classified_records_and_existing_codes(tmp_path):
    c = build(
        tmp_path / "t.sqlite",
        [(0, 1, "Callback received: GID: 1, Errors count: 0.", None, 7)],
        tpl={7: "WFM-030"},
    )
    out = promote(c, {"ids": [1], "regex": r"^Callback received: GID: (?P<gid>\d+)", "code": "WFM-030"})
    assert {w["code"] for w in out["warnings"]} == {"already_classified", "code_exists"}
    assert out["records"] == [{"id": 1, "matched": True, "current": "WFM-030"}]
    c.close()


def test_the_captured_characters_are_bounded(conn, monkeypatch):
    from dmslog.store import extract as X

    monkeypatch.setattr(X, "MAX_RESULT_CHARS", 100)
    ex = extractor(r"^(?P<all>.+)")
    with pytest.raises(ExtractError) as info:
        extract(conn, ex, None)
    assert info.value.code == "result_too_large"
    with pytest.raises(ExtractError):
        extract_values(conn, ex, list(range(1, 10)))
    assert extract(conn, ex, None, limit=1).matched == 1
