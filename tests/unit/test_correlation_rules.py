"""Correlation rules ([[sensitive_pair]], [[link_rule]]) of the real catalog: references, keys, coverage."""

import pytest

from dmslog.parse.templates import Catalog, TemplateDef, load_catalog

PAIR_KEYS = {"normal", "sensitive", "match", "max_dt_ms", "key", "order"}
LINK_KEYS = {"kind", "a", "b", "key", "scope", "max_dt_ms", "window_only"}
LINK_KINDS = {"callback", "retry", "response"}

# (family, normal, sensitive): verified pairs from docs/formato (sections "normal <-> sensitive").
EXPECTED_PAIRS = {
    ("AAVL", "AVL-002", "AVL-S002"),
    ("AAVL", "AVL-007", "AVL-S003"),
    ("AOSR", "OSR-005", "OSR-S005"),
    ("ASMN", "SMN-016", "SMN-S016"),
    ("ASMN", "SMN-018", "SMN-017"),
    ("ASMN", "SMN-019", "SMN-S019"),
    ("ACRM", "CRM-010", "CRM-S01"),
    ("ACRM", "GEN-ERR-01", "GEN-ERR-03"),
    ("ACLC", "CLC-003", "CLC-S01"),
    ("ACLC", "CLC-007", "CLC-S07"),
    ("ASMR", "GEN-ERR-01", "GEN-ERR-03"),
    ("AFUP", "FUP-004", "FUP-S01"),
    ("AFUP", "FUP-005", "FUP-S02"),
    ("AAMI", "AMI-005", "AMI-S02"),
    ("AONT", "ONT-022", "ONT-022"),
    ("AWFM", "WFM-030", "WFM-S030"),
    ("AWFM", "WFM-010", "WFM-S010"),
}
# (family, kind, a code, b code)
EXPECTED_LINKS = {
    ("ACRM", "callback", "CRM-011", "CRM-012"),
    ("AWFM", "callback", "WFM-124A", "WFM-126"),
    ("AWFM", "callback", "WFM-126", "WFM-030"),
    ("AWFM", "callback", "WFM-010", "WFM-030"),
    ("ASMN", "retry", "SMN-007", "SMN-007"),
    ("ASMN", "response", "SMN-007", "SMN-008"),
    ("AONT", "retry", "ONT-011", "ONT-011"),
    ("AONT", "retry", "ONT-016A", "ONT-016A"),
    ("AONT", "response", "ONT-011", "ONT-012"),
    ("AONT", "response", "ONT-016A", "ONT-016B"),
}


@pytest.fixture(scope="module")
def cat() -> Catalog:
    return load_catalog()


def _families(cat: Catalog) -> list[str]:
    return [f for f, d in cat.families.items() if not d.is_library]


def _codes(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _all_pairs(cat: Catalog):
    for fam in _families(cat):
        for p in cat.for_family(fam, sensitive=False).sensitive_pairs():
            yield fam, p


def _all_links(cat: Catalog):
    for fam in _families(cat):
        for r in cat.for_family(fam, sensitive=False).link_rules():
            yield fam, r


def _has_key(t: TemplateDef, key: str) -> bool:
    return key in t.field_names or any(k.type == key for k in t.keys) or any(k.field == key for k in t.keys)


def test_pair_shape_and_streams(cat: Catalog) -> None:
    n = 0
    for fam, p in _all_pairs(cat):
        n += 1
        assert set(p) <= PAIR_KEYS, (fam, p)
        normal, sens = cat.template(p["normal"]), cat.template(p["sensitive"])
        assert normal.stream in ("normal", "any"), (fam, p["normal"])
        assert sens.stream in ("sensitive", "any"), (fam, p["sensitive"])
        assert isinstance(p.get("max_dt_ms", 1000), int) and p.get("max_dt_ms", 1000) > 0
        assert set(p.get("match", ["pid", "tid"])) <= {"pid", "tid"}
        key = p.get("key")
        if key:
            assert _has_key(normal, key), (fam, p, "key missing on the normal template")
            assert _has_key(sens, key), (fam, p, "key missing on the sensitive template")
    assert n >= len(EXPECTED_PAIRS)


def test_pair_without_tid_needs_a_key(cat: Catalog) -> None:
    for fam, p in _all_pairs(cat):
        if "tid" not in p.get("match", ["pid", "tid"]):
            assert p.get("key"), (fam, p)


def test_normal_side_points_to_the_sensitive_log(cat: Catalog) -> None:
    # Where the normal log announces the sensitive one with a fixed phrase, the template says so.
    announcing = {
        "AVL-002",
        "CRM-010",
        "CLC-003",
        "FUP-004",
        "FUP-005",
        "AMI-005",
        "SMN-016",
        "SMN-018",
        "SMN-019",
        "WFM-010",
        "WFM-030",
    }
    paired = {p["normal"] for _, p in _all_pairs(cat)}
    for code in announcing:
        assert code in paired, code
        assert cat.template(code).refs_sensitive, code


def test_no_duplicate_pairs_or_links(cat: Catalog) -> None:
    for fam in _families(cat):
        ts = cat.for_family(fam, sensitive=False)
        pairs = [(p["normal"], p["sensitive"]) for p in ts.sensitive_pairs()]
        assert len(pairs) == len(set(pairs)), (fam, pairs)
        links = [(r["kind"], tuple(_codes(r["a"])), tuple(_codes(r["b"]))) for r in ts.link_rules()]
        assert len(links) == len(set(links)), (fam, links)


def test_link_rules_shape(cat: Catalog) -> None:
    n = 0
    for fam, r in _all_links(cat):
        n += 1
        assert set(r) <= LINK_KEYS, (fam, r)
        assert r["kind"] in LINK_KINDS, (fam, r)
        assert r.get("scope", "stream") in ("stream", "group"), (fam, r)
        assert isinstance(r.get("max_dt_ms", 60000), int) and r.get("max_dt_ms", 60000) > 0
        a_codes, b_codes = _codes(r["a"]), _codes(r["b"])
        window_only = bool(r.get("window_only", False))
        key = r.get("key")
        if window_only:
            assert not key, (fam, r)
            continue
        assert key, (fam, r, "key required unless window_only")
        for code in a_codes + b_codes:
            t = cat.template(code)
            assert _has_key(t, key), (fam, r, code)
            # Rules of one family never reach into another family (the common library is allowed).
            assert t.family in (fam, "_common"), (fam, code)
    assert n >= len(EXPECTED_LINKS) // 2


def test_documented_pairs_have_a_rule(cat: Catalog) -> None:
    have = {(fam, p["normal"], p["sensitive"]) for fam, p in _all_pairs(cat)}
    assert EXPECTED_PAIRS <= have, sorted(EXPECTED_PAIRS - have)


def test_documented_links_have_a_rule(cat: Catalog) -> None:
    have = set()
    for fam, r in _all_links(cat):
        for a in _codes(r["a"]):
            for b in _codes(r["b"]):
                have.add((fam, r["kind"], a, b))
    assert EXPECTED_LINKS <= have, sorted(EXPECTED_LINKS - have)


def test_payload_pairing_is_not_declared_here(cat: Catalog) -> None:
    # GEN-MSG-01 <-> GEN-PLD-01 belongs to the payload step (M6), not to the family rules.
    for _, p in _all_pairs(cat):
        assert p["sensitive"] != "GEN-PLD-01"
