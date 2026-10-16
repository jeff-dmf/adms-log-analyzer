"""Property tests of the preferences package (store/preferences.py, ROADMAP 9.10).

The oracles are small and independent of the code under test: a reference name normalizer, a
type-exact structural equality (``True`` is not ``1`` and ``1`` is not ``1.0``), and a toy migration
chain checked against hand-folded steps.
"""

import copy
import tempfile
import unicodedata
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.report import redact  # noqa: E402
from dmslog.store import alerts, preferences, presets  # noqa: E402

SETTINGS = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
FUZZ = settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# -- oracles ----------------------------------------------------------------------------------------


def ref_name(raw: str) -> str | None:
    """Reference for the name rules (NFC, no control/format chars, blanks collapsed, 1..80 chars)."""
    text = unicodedata.normalize("NFC", raw)
    text = "".join(
        " " if ch.isspace() else ch for ch in text if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    text = " ".join(text.split())
    return text if 0 < len(text) <= 80 else None


def exact(a, b) -> bool:
    """Structural equality that keeps bool, int and float apart and ignores key order."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(exact(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b, strict=True))
    return a == b


# -- strategies -------------------------------------------------------------------------------------

weird_text = st.text(max_size=30)  # includes quotes, backslashes, controls, U+2028, astral characters
safe_text = st.sampled_from(
    ["timeout", "Frame", "it's", 'a"b', "back\\slash", "a\u00e7\u00e3o", "\u65e5\u672c", "x y", "\u2028"]
)
field_value = st.one_of(
    st.sampled_from([0, 3, -7, 2**40, 3.0, 0.5, -0.0, "3", "timeout", True, False]), weird_text
)
leaf = st.one_of(
    st.builds(lambda v: {"sev": {"le": v}}, st.integers(1, 5)),
    st.builds(
        lambda v: {"tpl": v}, st.lists(st.sampled_from(["TST-001", "GEN-EXC-01"]), min_size=1, max_size=3)
    ),
    st.builds(lambda v: {"text": {"substr": v}}, st.one_of(safe_text, weird_text.filter(bool))),
    st.builds(lambda t: {"key": {"type": t}}, st.sampled_from(["msgid", "gid"])),
    st.builds(lambda n, v: {"field": {"name": n, "eq": v}}, st.sampled_from(["op", "n"]), field_value),
    st.builds(lambda v: {"sensitive": v}, st.booleans()),
    st.builds(lambda v: {"flags": v}, st.just(["gap"])),
)
trees = st.recursive(
    leaf,
    lambda c: st.one_of(
        st.builds(lambda x: {"and": x}, st.lists(c, min_size=1, max_size=3)),
        st.builds(lambda x: {"or": x}, st.lists(c, min_size=1, max_size=3)),
        st.builds(lambda x: {"not": x}, c),
    ),
    max_leaves=6,
)
names = st.one_of(
    st.sampled_from(["Errors", "errors", "ERRORS", "Wfm", "Caf\u00e9", "Cafe\u0301", "a  b", "a b"]),
    weird_text,
)
preset_items = st.builds(
    lambda n, d, f, t: {"name": n, "description": d, "family": f, "filter": t},
    names,
    st.text(max_size=40),
    st.sampled_from([None, "WFM", "wfm", "AWFM"]),
    trees,
)
rule_items = st.one_of(
    st.builds(
        lambda n, s, e, c, t: {
            "name": n,
            "kind": "match",
            "severity": s,
            "enabled": e,
            "min_count": c,
            "filter": t,
        },
        names,
        st.sampled_from(["info", "warning", "critical"]),
        st.booleans(),
        st.integers(1, 99),
        trees,
    ),  # fmt: skip
    st.builds(
        lambda n, s: {"name": n, "kind": "open_operation", "timeout_s": s},
        names,
        st.integers(1, 100_000),
    ),  # fmt: skip
)
profiles = st.builds(
    lambda n, ip, w, lit, ex, mode: {
        "name": n, "categories": {"ip": ip}, "terms": {"word": w, "literal": lit},
        "exceptions": ex, "mode": mode,
    },
    st.sampled_from(["work", "a b", "x.y-z"]),
    st.booleans(),
    st.lists(st.sampled_from(["Acme", "Bob", "\u65e5\u672c"]), max_size=3),
    st.lists(st.sampled_from(["secret", "x'y", 'q"q']), max_size=3),
    st.lists(st.sampled_from(["keep", "also"]), max_size=3),
    st.sampled_from(["strict", "standard"]),
)  # fmt: skip
packages = st.builds(
    lambda p, a, r: {
        "schema": 1, "kind": preferences.KIND, **({"presets": p} if p is not None else {}),
        **({"alert_rules": a} if a is not None else {}), **({"redaction": r} if r is not None else {}),
    },
    st.one_of(st.none(), st.lists(preset_items, max_size=6)),
    st.one_of(st.none(), st.lists(rule_items, max_size=6)),
    st.one_of(st.none(), profiles),
)  # fmt: skip


def snap(folder: Path):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())} if folder.exists() else {}


def fresh():
    return tempfile.TemporaryDirectory(prefix="dmslog-prefs-")


# -- properties -------------------------------------------------------------------------------------


@SETTINGS
@given(packages)
def test_import_export_roundtrip_and_idempotence(pkg):
    with fresh() as a_name, fresh() as b_name:
        a, b = Path(a_name), Path(b_name)
        rep = preferences.import_package(a, pkg, on_item_error="skip_item")
        # accepted items are stored exactly as sent, except for the name/description normalization
        for section, store in (
            ("presets", presets.load_user(a).presets),
            ("alert_rules", alerts.load(a).rules),
        ):
            if section not in pkg:
                continue
            by_name = {i["name"].casefold(): i for i in store}
            for row, sent in zip(rep["sections"][section]["items"], pkg[section], strict=True):
                if row["outcome"] in ("added", "updated", "unchanged"):
                    assert row["name"] == ref_name(sent["name"])
                    stored = next(
                        i
                        for i in store
                        if i["name"] == row["name"]
                        and (
                            section != "presets"
                            or i["family"] == (sent["family"].upper() if sent["family"] else None)
                        )
                    )
                    assert "filter" not in sent or exact(stored["filter"], sent["filter"])
                    assert row["name"].casefold() in by_name
                else:
                    assert row["outcome"] == "rejected" and row["code"]
        if "redaction" in pkg and rep["sections"]["redaction"]["status"] != "rejected":
            terms = {
                k: list(dict.fromkeys(v)) for k, v in pkg["redaction"]["terms"].items()
            }  # duplicates collapse
            assert exact(redact.load_profile(a).to_dict()["terms"], terms)
        text = preferences.export_package(a)["text"]
        # export -> import elsewhere -> export is the identity, and importing it again changes nothing
        again = preferences.import_package(b, text)
        assert again["outcome"] in ("applied", "unchanged") and again["summary"]["rejected"] == 0
        assert preferences.export_package(b)["text"] == text
        before = snap(b)
        second = preferences.import_package(b, text)
        assert second["outcome"] == "unchanged" and second["applied"] is False and snap(b) == before
        # and importing the same input twice into the first folder changes nothing
        before_a = snap(a)
        repeat = preferences.import_package(a, pkg, on_item_error="skip_item")
        assert repeat["applied"] is False and snap(a) == before_a
        assert repeat["summary"]["added"] == repeat["summary"]["updated"] == 0


@SETTINGS
@given(packages, st.sampled_from(["merge", "replace"]), st.sampled_from(["reject_section", "skip_item"]))
def test_dry_run_matches_the_real_import_and_writes_nothing(pkg, mode, policy):
    with fresh() as a_name, fresh() as b_name:
        a, b = Path(a_name) / "d", Path(b_name) / "d"
        dry = preferences.import_package(a, pkg, dry_run=True, mode=mode, on_item_error=policy)
        assert not a.exists()
        real = preferences.import_package(b, pkg, mode=mode, on_item_error=policy)
        strip = lambda r: {k: v for k, v in r.items() if k not in ("applied", "dry_run")}  # noqa: E731
        assert exact(strip(dry), strip(real))


@SETTINGS
@given(st.lists(preset_items, max_size=5), st.lists(preset_items, max_size=5))
def test_merge_then_replace_equals_replace(first, second):
    with fresh() as a_name, fresh() as b_name:
        a, b = Path(a_name), Path(b_name)
        preferences.import_package(
            a, {"schema": 1, "kind": preferences.KIND, "presets": first}, on_item_error="skip_item"
        )
        r1 = preferences.import_package(
            a, {"schema": 1, "kind": preferences.KIND, "presets": second}, mode="replace"
        )
        r2 = preferences.import_package(
            b, {"schema": 1, "kind": preferences.KIND, "presets": second}, mode="replace"
        )
        if r1["sections"]["presets"]["status"] != "rejected":
            assert exact(presets.load_user(a).presets, presets.load_user(b).presets)
        else:
            assert r2["sections"]["presets"]["status"] == "rejected"


@SETTINGS
@given(st.lists(preset_items, min_size=1, max_size=5), st.randoms(use_true_random=False))
def test_export_does_not_depend_on_the_insertion_order(items, rnd):
    with fresh() as a_name, fresh() as b_name:
        a, b = Path(a_name), Path(b_name)
        preferences.import_package(
            a, {"schema": 1, "kind": preferences.KIND, "presets": items}, on_item_error="skip_item"
        )
        shuffled = list(items)
        rnd.shuffle(shuffled)
        preferences.import_package(
            b, {"schema": 1, "kind": preferences.KIND, "presets": shuffled}, on_item_error="skip_item"
        )
        # when two inputs share an identity the last one wins, so only compare when identities are unique
        ids = [(ref_name(i["name"]) or "").casefold() + "|" + str(i["family"] or "").upper() for i in items]
        if len(set(ids)) == len(ids):
            assert preferences.export_package(a)["text"] == preferences.export_package(b)["text"]


json_leaf = st.one_of(
    st.none(), st.booleans(), st.integers(-(2**70), 2**70), st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
)  # fmt: skip
json_values = st.recursive(
    json_leaf,
    lambda c: st.one_of(st.lists(c, max_size=4), st.dictionaries(st.text(max_size=8), c, max_size=4)),
    max_leaves=25,
)


@FUZZ
@given(
    st.one_of(
        json_values,
        st.builds(
            lambda s, k, p, a, r: {"schema": s, "kind": k, "presets": p, "alert_rules": a, "redaction": r},
            st.one_of(st.just(1), json_leaf),
            st.one_of(st.just(preferences.KIND), json_leaf),
            json_values,
            json_values,
            json_values,
        ),  # fmt: skip
    )
)
def test_arbitrary_data_never_raises_and_never_corrupts(raw):
    with fresh() as name:
        folder = Path(name)
        presets.create(folder, {"name": "keep", "filter": {"sev": {"le": 2}}})
        before = snap(folder)
        frozen = copy.deepcopy(raw)
        rep = preferences.import_package(folder, raw, on_item_error="skip_item")
        assert repr(raw) == repr(frozen)  # the input is never modified (repr: nan != nan)
        assert rep["outcome"] in {"rejected", "partial", "applied", "unchanged", "failed"}
        if rep["outcome"] == "rejected" and rep["errors"]:
            assert snap(folder) == before and rep["applied"] is False
        # whatever was written reads back as valid files
        assert presets.load_user(folder).invalid is False
        assert alerts.load(folder).invalid is False
        redact.load_profile(folder)


# -- migration --------------------------------------------------------------------------------------


def step(n):
    """Toy step n -> n+1: renames key ``k{n}`` to ``k{n+1}`` and doubles the list under ``nums``."""

    def run(data):
        out = {k: v for k, v in data.items() if k != f"k{n}"}
        if f"k{n}" in data:
            out[f"k{n + 1}"] = data[f"k{n}"]
        out["nums"] = [x * 2 for x in data.get("nums", [])]
        return out

    return run


TOP = 5
TOY = {n: step(n) for n in range(1, TOP)}
toy_data = st.builds(
    lambda v, nums, extra: {"schema": 1, "kind": "k", "k1": v, "nums": nums, "other": extra},
    st.text(max_size=5),
    st.lists(st.integers(-5, 5), max_size=4),
    st.lists(st.integers(-100, 100), max_size=3),
)  # fmt: skip


@SETTINGS
@given(toy_data, st.integers(1, TOP), st.integers(1, TOP))
def test_chained_migration_equals_stepwise_and_split_anywhere(data, mid, end):
    mid, end = min(mid, end), max(mid, end)
    frozen = copy.deepcopy(data)
    whole, steps = preferences.migrate(data, end, TOY)
    assert data == frozen and steps == list(range(1, end))
    # oracle: closed form of the toy chain
    assert whole["schema"] == end and whole["nums"] == [x * 2 ** (end - 1) for x in data["nums"]]
    assert whole[f"k{end}"] == data["k1"] and all(f"k{n}" not in whole for n in range(1, end))
    # split: migrate to `mid`, then continue from there
    part, _ = preferences.migrate(data, mid, TOY)
    rest, rest_steps = preferences.migrate(part, end, TOY)
    assert rest == whole and rest_steps == list(range(mid, end))
    # idempotent at the target
    assert preferences.migrate(whole, end, TOY) == (whole, [])
